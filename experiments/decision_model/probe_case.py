"""Read-only inference probes of a saved Decision Model on an external case.

This measures choices, not generated explanations or expert-labelled accuracy.
Run from the same pinned Unsloth environment used for the GPU pilot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter', type=Path, required=True)
    parser.add_argument('--requests', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    requests_bytes = args.requests.read_bytes()
    requests = json.loads(requests_bytes)
    import unsloth  # Must precede torch/transformers imports.
    from unsloth import FastDecisionModel
    from unsloth.models.decision import _clef_question
    from unsloth.models._decision_common import _parsed
    from unsloth._vendor.clef.joint_schema_model import encode_record
    from .contract_check import LOCK
    from .train import runtime_source_hashes
    import torch

    lock = json.loads(LOCK.read_text())
    runtime_source_hashes(Path(unsloth.__file__).resolve().parent, lock)
    torch.manual_seed(42)
    model, tokenizer = FastDecisionModel.from_pretrained(
        model_name=str(args.adapter), max_seq_length=4096,
        load_in_4bit=True, local_files_only=True)
    FastDecisionModel.for_inference(model)
    model.eval()
    text_tokenizer = getattr(tokenizer, 'tokenizer', tokenizer)
    result = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'adapter': str(args.adapter),
        'adapter_files_sha256': {
            str(p.relative_to(args.adapter)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(args.adapter.rglob('*'))
            if p.is_file() and p.suffix in {'.json', '.safetensors', '.bin'}
        },
        'requests_sha256': hashlib.sha256(requests_bytes).hexdigest(),
        'unsloth_revision': lock['revision'],
        'device': torch.cuda.get_device_name(),
        'decision_config': dict(model.decision_config),
        'expert_gold_available': False,
        'scope': 'External, unseen task schema; choices only; no accuracy claim.',
        'probes': [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for request in requests:
        schema = {str(k): _clef_question(v) for k, v in request['questions'].items()}
        record = {'state': _parsed(request['state']), 'questions': schema}
        full = encode_record(text_tokenizer, record, max_length=1_000_000)
        # Official predict serves at least 16384 tokens. Check preservation explicitly.
        served = encode_record(text_tokenizer, record, max_length=16384)
        if full.input_ids != served.input_ids:
            raise ValueError(f"{request['id']}: would truncate {len(full.input_ids)} tokens")
        with torch.inference_mode():
            predictions = FastDecisionModel.predict(
                model, tokenizer, request['state'], request['questions'])
        labels = {
            key: request['label_map'][key][value['choice']]
            for key, value in predictions.items()
        }
        result['probes'].append({
            'id': request['id'], 'tokens': len(full.input_ids),
            'exceeds_training_max_length': len(full.input_ids) > 4096,
            'truncated': False, 'predictions': predictions, 'semantic_choices': labels,
        })
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result['probes'][-1], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
