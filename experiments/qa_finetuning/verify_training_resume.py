"""Exercise the actual training loop's interrupted checkpoint resume on a tiny GPU model."""
import argparse
import json
from pathlib import Path
import tempfile

import torch
from peft import LoraConfig, PeftModel, get_peft_model
from transformers import Qwen3Config, Qwen3ForCausalLM

import train_pilot as pilot


class SimulatedInterruption(Exception):
    pass


def tiny_load(model_path, training=False, adapter=None):
    assert training
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(pilot.SEED)
        base = Qwen3ForCausalLM(Qwen3Config(vocab_size=97, hidden_size=32,
            intermediate_size=64, num_hidden_layers=1, num_attention_heads=4,
            num_key_value_heads=2, head_dim=8, attention_dropout=0.0)).to('cuda')
    if adapter is None:
        model = get_peft_model(base, LoraConfig(r=4, lora_alpha=8, lora_dropout=0.05,
            target_modules=pilot.TARGETS, bias='none', task_type='CAUSAL_LM'))
    else:
        model = PeftModel.from_pretrained(base, adapter, is_trainable=True)
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    model.enable_input_require_grads()
    model.train()
    return None, model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    generator = torch.Generator().manual_seed(123)
    data = [{'id': f'tiny-{i}', 'tokens': torch.randint(0, 97, (13,), generator=generator).tolist(),
             'prompt_length': 5} for i in range(16)]
    pilot.load = tiny_load
    pilot.tokenize_training = lambda tokenizer, run: data
    pilot.SAVE_EVERY = 1
    original_checkpoint = pilot.checkpoint
    with tempfile.TemporaryDirectory(prefix='qa-resume-verification-') as temporary:
        root = Path(temporary)
        reference, interrupted = root / 'reference', root / 'interrupted'
        for path in [reference, interrupted]:
            path.mkdir()
            pilot.save(path / 'config.json', {'test': 'actual_train_loop_tiny_model'})
        pilot.train(reference, Path('unused'))

        def checkpoint_then_interrupt(*arguments):
            original_checkpoint(*arguments)
            raise SimulatedInterruption('After committed first optimizer checkpoint')

        pilot.checkpoint = checkpoint_then_interrupt
        try:
            pilot.train(interrupted, Path('unused'))
        except SimulatedInterruption:
            pass
        else:
            raise AssertionError('Test did not interrupt')
        first = json.loads((interrupted / 'train/latest.json').read_text())
        assert first['optimizer_step'] == 1 and first['next_start'] == 8
        pilot.checkpoint = original_checkpoint
        pilot.train(interrupted, Path('unused'))
        final_reference = json.loads((reference / 'train/latest.json').read_text())
        final_resumed = json.loads((interrupted / 'train/latest.json').read_text())
        assert final_reference['optimizer_step'] == final_resumed['optimizer_step'] == 2
        assert final_reference['sample_count'] == final_resumed['sample_count'] == 16
        from safetensors.torch import load_file
        expected = load_file(str(Path(final_reference['checkpoint']) / 'adapter_model.safetensors'))
        actual = load_file(str(Path(final_resumed['checkpoint']) / 'adapter_model.safetensors'))
        assert set(actual) == set(expected)
        for name in expected:
            torch.testing.assert_close(actual[name], expected[name], atol=1e-7, rtol=1e-5)
        expected_state = torch.load(Path(final_reference['checkpoint']) / 'training_state.pt', weights_only=False)
        actual_state = torch.load(Path(final_resumed['checkpoint']) / 'training_state.pt', weights_only=False)
        assert expected_state['scheduler'] == actual_state['scheduler']
        for index, values in expected_state['optimizer']['state'].items():
            for key, value in values.items():
                torch.testing.assert_close(actual_state['optimizer']['state'][index][key], value,
                                           atol=1e-7, rtol=1e-5)
        torch.testing.assert_close(actual_state['torch_rng'], expected_state['torch_rng'])
        for actual_rng, expected_rng in zip(actual_state['cuda_rng'], expected_state['cuda_rng']):
            torch.testing.assert_close(actual_rng, expected_rng)
        pilot.save(args.output, {'status': 'passed', 'test': 'actual_train_loop_checkpoint_resume',
            'simulated_interruption_after_step': 1, 'final_steps': 2,
            'final_samples': 16, 'dropout_enabled': True,
            'adapter_optimizer_scheduler_and_torch_cuda_rng_parity': True,
            'formal_training_data_used': False})
    print(args.output.read_text(), flush=True)


if __name__ == '__main__':
    main()
