"""Checks around the official trainer, without replacing its model or loss."""
from __future__ import annotations

import math


def update_probe(model):
    # Imported after Unsloth by the entry point.
    import torch
    from transformers import TrainerCallback

    class Probe(TrainerCallback):
        def __init__(self):
            self.parameters = {'head': [], 'lora': []}
            for name, parameter in model.named_parameters():
                if not parameter.requires_grad:
                    continue
                group = 'head' if name.startswith('head.') else 'lora' if 'lora_' in name else None
                if group is None:
                    raise ValueError(f'Unexpected trainable parameter: {name}')
                self.parameters[group].append((name, parameter))
            self.initial = {name: p.detach().float().cpu().clone()
                            for values in self.parameters.values() for name, p in values}
            self.gradients = {group: {'finite': True, 'nonzero': False, 'max_abs': 0.0}
                              for group in self.parameters}
            self.optimizer_steps = 0
            self.learning_rates = []

        def on_pre_optimizer_step(self, args, state, control, optimizer=None, **kwargs):
            self.learning_rates.append([float(g['lr']) for g in optimizer.param_groups])
            for group, values in self.parameters.items():
                for _, p in values:
                    if p.grad is None:
                        continue
                    gradient = p.grad.detach()
                    finite = bool(torch.isfinite(gradient).all())
                    maximum = float(gradient.abs().max())
                    self.gradients[group]['finite'] &= finite
                    self.gradients[group]['nonzero'] |= finite and maximum > 0
                    self.gradients[group]['max_abs'] = max(self.gradients[group]['max_abs'], maximum)

        def on_optimizer_step(self, args, state, control, **kwargs):
            self.optimizer_steps += 1

        def report(self, loss):
            groups = {}
            for group, values in self.parameters.items():
                changed, max_delta, finite = 0, 0.0, True
                for name, p in values:
                    current = p.detach().float().cpu()
                    finite &= bool(torch.isfinite(current).all())
                    delta = float((current - self.initial[name]).abs().max())
                    changed += int(delta > 0)
                    max_delta = max(max_delta, delta)
                groups[group] = {**self.gradients[group], 'parameters_finite': finite,
                                 'tensors': len(values), 'changed_tensors': changed,
                                 'max_parameter_delta': max_delta}
            passed = (self.optimizer_steps > 0 and math.isfinite(loss)
                      and all(g['finite'] and g['nonzero'] and g['parameters_finite']
                              and g['changed_tensors'] > 0 for g in groups.values()))
            return {'passed': passed, 'loss': loss, 'optimizer_steps': self.optimizer_steps,
                    'learning_rates': self.learning_rates, 'groups': groups}

    return Probe()


def compare_predictions(reference, actual, tolerance):
    """Compare the official prediction payload (labels and all probabilities)."""
    maximum, comparisons, labels = 0.0, 0, 0

    def visit(left, right, path):
        nonlocal maximum, comparisons, labels
        if isinstance(left, dict):
            if not isinstance(right, dict) or left.keys() != right.keys():
                raise ValueError(f'Reload payload keys differ: {path}')
            for key in left:
                visit(left[key], right[key], f'{path}.{key}')
        elif isinstance(left, list):
            if not isinstance(right, list) or len(left) != len(right):
                raise ValueError(f'Reload payload lengths differ: {path}')
            for i, value in enumerate(left):
                visit(value, right[i], f'{path}[{i}]')
        elif isinstance(left, float):
            if not isinstance(right, (int, float)) or not math.isfinite(left) or not math.isfinite(right):
                raise ValueError(f'Nonfinite reload probability: {path}')
            maximum = max(maximum, abs(left - right))
            comparisons += 1
        elif left != right:
            raise ValueError(f'Reload prediction differs: {path}: {left!r} != {right!r}')
        elif path.endswith('.label') or path.endswith('.answer'):
            labels += 1

    visit(reference, actual, 'predictions')
    return {'passed': comparisons > 0 and maximum <= tolerance,
            'all_discrete_values_equal': True, 'probability_values_compared': comparisons,
            'max_absolute_probability_delta': maximum, 'absolute_tolerance': tolerance}
