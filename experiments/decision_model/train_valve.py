"""Fixed weak-supervision pilot; no normal classification or calibration fit."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import importlib.metadata
import json
import math
from pathlib import Path

from .contract_check import LOCK, assert_no_truncation
from .data import decode_row, digest, read_jsonl, write_json, write_jsonl
from .download import verify_model
from .runtime_check import update_probe
from .train import runtime_source_hashes
from .valve_windows import signal
from .valve_windows_v2 import PILOT_OPTIONS, annotate, row_for, verify


def expanded_training(folder):
    registry = {e['id']: e for e in read_jsonl(folder / 'events.jsonl')}
    rules = json.loads((folder / 'rules.json').read_text())
    cases = [c for c in read_jsonl(folder / 'windows.jsonl') if c['split'] == 'train']
    return [row_for(c, registry, rules, r)[0] for c in cases for r in range(5)]


def evaluation_plan(folder):
    audit = read_jsonl(folder / 'audit.jsonl')
    expected = {a['window_id']: a['labels'] for a in audit}
    cases = read_jsonl(folder / 'windows.jsonl')
    registry = {e['id']: e for e in read_jsonl(folder / 'events.jsonl')}
    rules = json.loads((folder / 'rules.json').read_text())
    probes = read_jsonl(folder / 'robustness_requests.jsonl')
    for p in probes:
        p['expected'] = expected[p['window_id']]
        p['kind'] = 'test_rotation'
    # Predeclare at most two observation-removal cases per test day. These are
    # diagnostic altered views, not additional real windows or training rows.
    chosen = Counter()
    for c in cases:
        day = c['start'][:10]
        if c['split'] != 'test' or chosen[day] >= 2 or c['annotation']['candidate_labels']['during_window'] != 'abnormal_evidence':
            continue
        altered = dict(c)
        removed = [k for k in c['event_ids'] + c['carry_ids'] if signal(registry[k])]
        altered['event_ids'] = [k for k in c['event_ids'] if k not in removed]
        altered['carry_ids'] = [k for k in c['carry_ids'] if k not in removed]
        altered['annotation'] = annotate([registry[k] for k in altered['event_ids']], [registry[k] for k in altered['carry_ids']])
        altered['id'] = c['id'] + '/target-observations-removed'
        row, mapping = row_for(altered, registry, rules)
        row.pop('gold')
        probes.append({**row, 'window_id': c['id'], 'mapping': mapping,
                       'expected': altered['annotation']['candidate_labels'],
                       'kind': 'counterfactual_missing_observations', 'removed_event_ids': removed})
        chosen[day] += 1
    for a in audit:
        if a['split'] != 'calibration':
            continue
        row = next(r for r in read_jsonl(folder / 'calibration.jsonl') if r['id'] == a['id'])
        row.pop('gold')
        probes.append({**row, 'window_id': a['window_id'], 'mapping': a['mapping'],
                       'expected': a['labels'], 'kind': 'validation_no_calibration_fit'})
    return probes


def metrics(predictions, audit):
    test = [p for p in predictions if p['kind'] == 'test_rotation']
    canonical_ids = {a['id'] for a in audit if a['split'] == 'test'}
    canonical = [p for p in test if p['id'] in canonical_ids]
    windows = sorted({p['window_id'] for p in test})
    result = {'interpretation': 'agreement_with_rule_candidates_not_field_accuracy',
              'test_windows': len(windows), 'test_event_days': 2,
              'test_parent_windows': len({w.rsplit('/', 2)[0] for w in windows}),
              'rotations_per_window': 5, 'tasks': {}}
    for qid in PILOT_OPTIONS:
        majority = Counter(a['labels'][qid] for a in audit if a['split'] == 'train').most_common(1)[0][0]
        confusion = Counter((p['expected'][qid], p['semantic_choices'][qid]) for p in canonical)
        recalls = {label: sum(p['semantic_choices'][qid] == label for p in canonical if p['expected'][qid] == label) / sum(p['expected'][qid] == label for p in canonical)
                   for label in PILOT_OPTIONS[qid] if any(p['expected'][qid] == label for p in canonical)}
        stable = sum(len({p['semantic_choices'][qid] for p in test if p['window_id'] == w}) == 1 for w in windows)
        strict = sum(all(p['semantic_choices'][qid] == p['expected'][qid] for p in test if p['window_id'] == w) for w in windows)
        days = sorted({p['window_id'].split('/')[0].split('-')[2] for p in canonical})
        result['tasks'][qid] = {
            'canonical_correct': sum(p['semantic_choices'][qid] == p['expected'][qid] for p in canonical),
            'canonical_total': len(canonical), 'macro_recall_present_classes': sum(recalls.values())/len(recalls),
            'recall_by_present_label': recalls,
            'confusion': [{'expected': a, 'predicted': b, 'count': n} for (a, b), n in sorted(confusion.items())],
            'stable_across_all_positions': stable, 'correct_across_all_positions': strict,
            'train_majority_label': majority,
            'train_majority_baseline_correct': sum(p['expected'][qid] == majority for p in canonical),
            'by_event_day': {d: {'correct': sum(p['semantic_choices'][qid] == p['expected'][qid] for p in canonical if d in p['window_id']),
                                  'total': sum(d in p['window_id'] for p in canonical)} for d in days}}
    for category in ('counterfactual_missing_observations', 'validation_no_calibration_fit'):
        selected = [p for p in predictions if p['kind'] == category]
        result[category] = {'windows': len(selected), 'correct_decisions': sum(p['semantic_choices'][q] == p['expected'][q] for p in selected for q in PILOT_OPTIONS),
                            'decisions': len(selected)*len(PILOT_OPTIONS)}
    return result


def evaluate(model, tokenizer, plan, output, audit):
    from unsloth import FastDecisionModel
    FastDecisionModel.for_inference(model)
    predictions = []
    for index, raw in enumerate(plan):
        p = decode_row(raw)
        answers = FastDecisionModel.predict(model, tokenizer, p['state'], p['questions'])
        predictions.append({k: p[k] for k in ('id', 'window_id', 'kind', 'expected')} | {
            'predictions': answers,
            'semantic_choices': {q: p['mapping'][q][answer['choice']] for q, answer in answers.items()}})
        if (index+1) % 20 == 0:
            write_jsonl(output / 'predictions.jsonl', predictions)
            print(f'evaluation {index+1}/{len(plan)}', flush=True)
    write_jsonl(output / 'predictions.jsonl', predictions)
    report = metrics(predictions, audit)
    write_json(output / 'metrics.json', report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--model-dir', type=Path, required=True)
    p.add_argument('--evaluate-adapter', type=Path)
    args = p.parse_args()
    validation = verify(args.data)
    if args.output.exists():
        p.error('Use a new run directory')
    args.output.mkdir(parents=True)
    plan = evaluation_plan(args.data)
    write_jsonl(args.output / 'evaluation_plan.jsonl', plan)
    audit = read_jsonl(args.data / 'audit.jsonl')

    import unsloth
    from unsloth import FastDecisionModel, DecisionTrainer, is_bfloat16_supported
    from unsloth.models.clef import encode_record
    from unsloth.models._decision_common import _parsed
    import torch
    from transformers import TrainingArguments
    lock = json.loads(LOCK.read_text())
    source_hashes = runtime_source_hashes(Path(unsloth.__file__).resolve().parent, lock)
    verify_model(args.model_dir, lock['pilot_model'])
    config = {'model_dir': str(args.model_dir), 'max_length': 4096, 'smoke_test': False,
              'seed': 3407, 'epochs': 2, 'calibration_fit': False,
              'dataset_manifest_sha256': digest(args.data / 'manifest.json'),
              'runner_sha256': digest(__file__), 'installed_source_hashes': source_hashes,
              'versions': {n: importlib.metadata.version(n) for n in ('unsloth', 'transformers', 'torch')},
              'gpu': torch.cuda.get_device_name(0),
              'label_status': 'rule_proposed_not_expert_gold',
              'training_augmentation': 'five cyclic placements per training window; not independent examples',
              'recipe_selection': 'fixed before test; no validation/test-based checkpoint selection',
              'scope': 'explicit target evidence and recovery; normal/expected class excluded',
              'model_initialization': str(args.evaluate_adapter) if args.evaluate_adapter else 'pinned base + fresh Clef head; r16 LoRA'}
    write_json(args.output / 'run_config.json', config)
    write_json(args.output / 'status.json', {'stage': 'loading', 'training_completed': False})
    try:
        model, tokenizer = FastDecisionModel.from_pretrained(
            model_name=str(args.evaluate_adapter or args.model_dir), max_seq_length=4096,
            decision_head='clef', load_in_4bit=True, local_files_only=True, random_state=3407)
        plain = getattr(tokenizer, 'tokenizer', tokenizer)
        # Inference's default ceiling is longer; still require every planned
        # request to meet the same 4096-token training budget without truncation.
        assert_no_truncation(plan, plain, encode_record, _parsed, 4096)
        if args.evaluate_adapter:
            result = evaluate(model, tokenizer, plan, args.output, audit)
            write_json(args.output / 'status.json', {'stage': 'evaluation_complete', 'training_started': False})
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return
        model = FastDecisionModel.get_peft_model(model, r=16, lora_alpha=16, lora_dropout=0,
                                                use_gradient_checkpointing='unsloth', random_state=3407)
        rows = expanded_training(args.data)
        lengths = assert_no_truncation(rows, plain, encode_record, _parsed, 4096)
        items, report = FastDecisionModel.build_dataset(rows, tokenizer, model)
        if report['skipped'] or report['truncated'] or len(items) != len(rows):
            raise ValueError(f'Official builder lost rows: {report}')
        write_json(args.output / 'build_report.json', {'report': report, 'max_tokens': max(lengths),
                    'independent_train_windows': validation['splits']['train']['windows'], 'augmented_rows': len(rows)})
        probe = update_probe(model)
        steps = math.ceil(len(items)/4)*2
        trainer = DecisionTrainer(model=model, processing_class=tokenizer, train_dataset=items,
            head_learning_rate=1e-4, callbacks=[probe], args=TrainingArguments(
                output_dir=str(args.output / 'checkpoints'), per_device_train_batch_size=1,
                gradient_accumulation_steps=4, num_train_epochs=2, learning_rate=2e-4,
                lr_scheduler_type='cosine', warmup_steps=math.ceil(steps*0.1), weight_decay=0.01,
                bf16=is_bfloat16_supported(), fp16=not is_bfloat16_supported(), eval_strategy='no',
                save_strategy='no', logging_steps=1, report_to='none', seed=3407, data_seed=3407))
        write_json(args.output / 'status.json', {'stage': 'training', 'training_completed': False})
        trained = trainer.train()
        update = probe.report(trained.training_loss)
        write_json(args.output / 'update_verification.json', update)
        write_json(args.output / 'training_metrics.json', trained.metrics)
        if not update['passed']:
            raise ValueError('Finite parameter update check failed')
        trainer.save_model(str(args.output / 'adapter'))
        FastDecisionModel.for_inference(model)
        fixed = [decode_row(r) for r in rows[::5][:6]]
        write_json(args.output / 'reload_reference.json', {'absolute_tolerance': 0.02,
            'probes': [{'id': r['id'], 'state': r['state'], 'questions': r['questions']} for r in fixed],
            'predictions': [FastDecisionModel.predict(model, tokenizer, r['state'], r['questions']) for r in fixed]})
        write_json(args.output / 'status.json', {'stage': 'evaluating', 'training_completed': True})
        result = evaluate(model, tokenizer, plan, args.output, audit)
        write_json(args.output / 'status.json', {'stage': 'awaiting_reload_verification', 'training_completed': True})
        print(json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as exc:
        write_json(args.output / 'status.json', {'stage': 'failed', 'adapter_saved': (args.output / 'adapter').exists(),
                                               'error_type': type(exc).__name__, 'error': str(exc)})
        raise


if __name__ == '__main__':
    main()
