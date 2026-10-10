"""GPU training entry point using upstream FastDecisionModel/DecisionTrainer.

Data validation is standard-library-only. Heavy imports happen only after it
passes. No publication or network inference service is started by this script.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
from pathlib import Path

from .contract_check import LOCK, assert_no_truncation
from .data import DEFAULT_DATA, ROOT, SPLITS, decode_row, digest, read_jsonl, verify, write_json, write_jsonl
from .download import verify_model
from .runtime_check import update_probe

def runtime_source_hashes(package_root, lock):
    """Refuse to silently run a different decision implementation than audited."""
    hashes = {}
    for source in lock['sources']:
        if source['path'] in {'unsloth/models/decision.py', 'unsloth/models/_decision_common.py',
                              'unsloth/models/decision_from_lm.py', 'unsloth/models/clef.py',
                              'unsloth/_vendor/clef/joint_schema_model.py'}:
            relative = Path(source['path']).relative_to('unsloth')
            hashes[source['path']] = digest(package_root / relative)
            if hashes[source['path']] != source['sha256']:
                raise RuntimeError(f"Installed Unsloth source differs from audited revision: {relative}")
    return hashes

def canonical_rows(rows):
    return [row for row in rows if row['id'].endswith('/narrative')]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--epochs', type=float, default=2)
    parser.add_argument('--max-steps', type=int, default=-1)
    parser.add_argument('--max-length', type=int, default=4096)
    parser.add_argument('--model-dir', type=Path, help='Local model downloaded by decision_model.download')
    parser.add_argument('--smoke-test', action='store_true', help='One nonzero-LR update, using training data only')
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    if args.smoke_test:
        args.max_steps = 1
    if args.epochs <= 0 or args.max_steps == 0 or args.max_steps < -1:
        parser.error('epochs must be positive; max-steps must be -1 or positive')
    validation = verify(args.data)
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('output must be a new, empty run directory')
    if args.preflight_only:
        print(json.dumps({'data': validation, 'runtime_training_tested': False}, ensure_ascii=False, indent=2))
        return

    # Follow Unsloth import ordering. This entry point targets its CUDA path.
    import unsloth
    from unsloth import FastDecisionModel, DecisionTrainer, is_bfloat16_supported
    import torch
    from transformers import TrainingArguments
    from unsloth.models.clef import encode_record
    from unsloth.models._decision_common import _parsed
    if not torch.cuda.is_available():
        raise RuntimeError('This runner targets CUDA. The data/contract checks run on CPU; upstream also has a separate MLX path.')
    lock = json.loads(LOCK.read_text())
    model_manifest = verify_model(args.model_dir, lock['pilot_model']) if args.model_dir else None
    source_hashes = runtime_source_hashes(Path(unsloth.__file__).resolve().parent, lock)
    args.output.mkdir(parents=True, exist_ok=True)
    run_config = {**vars(args), 'data': str(args.data), 'output': str(args.output),
                  'model_dir': str(args.model_dir.resolve()) if args.model_dir else None,
                  'model': lock['pilot_model'], 'audited_unsloth_revision': lock['revision'],
                  'local_model_manifest': model_manifest,
                  'installed_decision_source_sha256': source_hashes,
                  'runner_source_sha256': {p.name: digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
                  'dataset_manifest_sha256': digest(args.data / 'manifest.json'),
                  'versions': {name: importlib.metadata.version(name) for name in ('unsloth', 'torch', 'transformers', 'peft')},
                  'cuda': {'torch_cuda_version': torch.version.cuda,
                           'gpu': torch.cuda.get_device_name(0),
                           'memory_bytes': torch.cuda.get_device_properties(0).total_memory,
                           'bf16_supported': is_bfloat16_supported()},
                  'review_status': 'source_checked_synthetic_pilot_not_expert_approved',
                  'baseline_interpretation': 'plain LM plus newly initialized Clef head; not original LM QA capability',
                  'export_format': 'official_trainer_adapter_with_float32_head',
                  'evaluation_unit': 'one narrative rendering per canonical scenario; no duplicate paraphrases in calibration/test'}
    write_json(args.output / 'run_config.json', run_config)
    write_json(args.output / 'status.json', {'stage': 'loading_model', 'training_completed': False})
    try:
        model, tokenizer = FastDecisionModel.from_pretrained(
            model_name=str(args.model_dir.resolve()) if args.model_dir else lock['pilot_model']['name'],
            revision=lock['pilot_model']['revision'], local_files_only=bool(args.model_dir),
            decision_head='clef', max_seq_length=args.max_length, load_in_4bit=True, random_state=3407)
        model = FastDecisionModel.get_peft_model(model, r=16, lora_alpha=16, lora_dropout=0,
                                                use_gradient_checkpointing='unsloth', random_state=3407)
        rows = {split: read_jsonl(args.data / f'{split}.jsonl') for split in SPLITS}
        # Rendering variants augment training, not the number of independent test/calibration cases.
        rows['calibration'] = canonical_rows(rows['calibration'])
        rows['test'] = canonical_rows(rows['test'])
        items, reports = {}, {}
        plain_tokenizer = getattr(tokenizer, 'tokenizer', tokenizer)
        for split in SPLITS:
            assert_no_truncation(rows[split], plain_tokenizer, encode_record, _parsed, args.max_length)
            items[split], reports[split] = FastDecisionModel.build_dataset(rows[split], tokenizer, model)
            if reports[split]['skipped'] or reports[split]['truncated'] or len(items[split]) != len(rows[split]):
                raise ValueError(f'Upstream data loss: {split} {reports[split]}')
        write_json(args.output / 'build_reports.json', reports)
        if not args.smoke_test:
            before = FastDecisionModel.evaluate(model, tokenizer, items['test'], batch_size=1)
            write_json(args.output / 'baseline.json', before)
        write_json(args.output / 'status.json', {'stage': 'training', 'training_completed': False})
        probe = update_probe(model)
        total_steps = args.max_steps if args.max_steps > 0 else math.ceil(math.ceil(len(items['train']) / 4) * args.epochs)
        warmup_steps = 0 if args.smoke_test else math.ceil(total_steps * 0.1)
        run_config['scheduler'] = {'planned_optimizer_steps': total_steps, 'warmup_steps': warmup_steps}
        write_json(args.output / 'run_config.json', run_config)
        trainer = DecisionTrainer(
            model=model, processing_class=tokenizer, train_dataset=items['train'], head_learning_rate=1e-4,
            # Fixed recipe: no test-driven checkpoint selection, no fitting on calibration labels.
            args=TrainingArguments(output_dir=str(args.output / 'checkpoints'),
                per_device_train_batch_size=1, gradient_accumulation_steps=4,
                num_train_epochs=args.epochs, max_steps=args.max_steps, learning_rate=2e-4,
                lr_scheduler_type='cosine', warmup_steps=warmup_steps, weight_decay=0.01,
                bf16=is_bfloat16_supported(), fp16=not is_bfloat16_supported(),
                eval_strategy='no', save_strategy='no', logging_steps=1,
                report_to='none', seed=3407, data_seed=3407),
            callbacks=[probe],
        )
        trained = trainer.train()
        update_report = probe.report(trained.training_loss)
        write_json(args.output / 'update_verification.json', update_report)
        if not update_report['passed']:
            raise RuntimeError('Finite loss/gradients and head/LoRA parameter updates were not all verified')
        if not args.smoke_test:
            uncalibrated = FastDecisionModel.evaluate(model, tokenizer, items['test'], batch_size=1)
            calibration = FastDecisionModel.calibrate(model, tokenizer, items['calibration'], batch_size=1)
            after = FastDecisionModel.evaluate(model, tokenizer, items['test'], batch_size=1)
        # The public model export rounds the head to BF16, which can flip close
        # decisions. Official Trainer saving preserves its exact FP32 head instead.
        trainer.save_model(str(args.output / 'adapter'))  # head + adapters + calibration
        FastDecisionModel.for_inference(model)
        # These fixed training probes test serialization, not generalization. Keep the
        # original predeclared tolerance, and require every discrete decision to match.
        probes = [decode_row(row) for row in canonical_rows(rows['train'])[:6]]
        write_json(args.output / 'reload_reference.json', {
            'absolute_tolerance': 0.02,
            'probes': [{'id': row['id'], 'state': row['state'], 'questions': row['questions']} for row in probes],
            'predictions': [FastDecisionModel.predict(model, tokenizer, row['state'], row['questions']) for row in probes],
        })
        predictions = []
        for raw in [] if args.smoke_test else rows['test']:
            row = decode_row(raw)
            answers = FastDecisionModel.predict(model, tokenizer, row['state'], row['questions'])
            predictions.append({'id': row['id'], 'answers': answers, 'gold': row['gold']})
        if not args.smoke_test:
            write_jsonl(args.output / 'test_predictions.jsonl', predictions)
            write_json(args.output / 'metrics.json', {'baseline_fresh_head': before,
                    'after_uncalibrated': uncalibrated, 'calibration': calibration,
                    'after_calibrated': after, 'training': trained.metrics,
                        'test_cases': len(rows['test']), 'field_validity_claimed': False})
        write_json(args.output / 'status.json', {'stage': 'awaiting_reload_verification',
                                                'training_completed': True, 'reload_verified': False})
    except Exception as exc:
        write_json(args.output / 'status.json', {'stage': 'failed', 'training_completed': False,
                                               'error_type': type(exc).__name__, 'error': str(exc)})
        raise

if __name__ == '__main__':
    main()
