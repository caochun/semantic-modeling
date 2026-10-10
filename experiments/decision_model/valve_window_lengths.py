"""Measure full candidate records with the pinned tokenizer/official encoder.

CPU only. Requires transformers and an installed pinned Unsloth source tree;
does not import Unsloth, load weights, truncate windows, or start training.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil

from .contract_check import LOCK, upstream_data_api
from .data import decode_row, digest, read_jsonl, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=Path, required=True)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    source_root = Path(importlib.util.find_spec('unsloth').origin).parent.parent
    lock = json.loads(LOCK.read_text())
    for spec in lock['sources']:
        if spec['path'] in {'unsloth/models/decision.py', 'unsloth/models/_decision_common.py',
                            'unsloth/_vendor/clef/joint_schema_model.py'}:
            shutil.copyfile(source_root / spec['path'], args.cache / spec['path'].replace('/', '_'))
    _, _, common, clef = upstream_data_api(args.cache)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model_dir, local_files_only=True)
    lengths = []
    for raw in read_jsonl(args.rows):
        row = decode_row(raw)
        record = {'state': common._parsed(row['state']), 'questions': row['questions']}
        encoded = clef.encode_record(tokenizer, record, max_length=1_000_000)
        n = len(encoded.input_ids)
        lengths.append({'id': row['id'], 'tokens': n, 'fits_training_4096': n <= 4096,
                        'fits_serving_16384': n <= 16384, 'truncated': False})
    report = {'tokenizer': lock['pilot_model'], 'unsloth_revision': lock['revision'],
              'rows_sha256': digest(args.rows),
              'encoding': 'original encode_record and _parsed on complete serialized state',
              'model_weights_loaded': False, 'records': lengths,
              'fits_4096': sum(r['fits_training_4096'] for r in lengths),
              'fits_16384': sum(r['fits_serving_16384'] for r in lengths),
              'min_tokens': min(r['tokens'] for r in lengths),
              'max_tokens': max(r['tokens'] for r in lengths)}
    write_json(args.output, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'records'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
