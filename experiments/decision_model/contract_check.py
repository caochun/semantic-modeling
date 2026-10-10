"""Run pinned upstream data/encoding functions without importing the GPU stack.

Only upstream's pure data functions and two dataclasses are loaded. This checks
real label conversion, build_dataset and Clef tokenization with a real tokenizer;
it does NOT exercise model forward/backward, Trainer, saving or inference.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import sys
import types
import urllib.request
from pathlib import Path

from .data import DEFAULT_DATA, ROOT, SPLITS, decode_row, read_jsonl, verify, write_json

LOCK = Path(__file__).with_name('upstream.lock.json')

def download_sources(cache):
    lock = json.loads(LOCK.read_text())
    cache.mkdir(parents=True, exist_ok=True)
    paths = {}
    for source in lock['sources']:
        if source['path'] not in {'unsloth/models/decision.py', 'unsloth/models/_decision_common.py',
                                  'unsloth/_vendor/clef/joint_schema_model.py'}:
            continue
        path = cache / source['path'].replace('/', '_')
        if not path.exists():
            with urllib.request.urlopen(source['url'], timeout=45) as response:
                content = response.read()
            if hashlib.sha256(content).hexdigest() != source['sha256']:
                raise ValueError('Downloaded upstream file hash does not match lock')
            path.write_bytes(content)
        if hashlib.sha256(path.read_bytes()).hexdigest() != source['sha256']:
            raise ValueError(f'Cached upstream file changed: {path}')
        paths[source['path']] = path
    return lock, paths

def load_module(name, path, selected=None):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = name.rpartition('.')[0]
    sys.modules[name] = module
    tree = ast.parse(path.read_text(), filename=str(path))
    if selected is not None:
        tree.body = [node for node in tree.body if selected(node)]
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module

def upstream_data_api(cache):
    lock, paths = download_sources(cache)
    package = types.ModuleType('_gusu_upstream_contract')
    package.__path__ = []
    sys.modules[package.__name__] = package
    common = load_module(package.__name__ + '.common', paths['unsloth/models/_decision_common.py'])
    names = {'render', 'question_options', 'EncodedQuestion', 'EncodedRecord', '_tokens', '_encode_media', 'encode_record'}
    def selected(node):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            return node.name in names
        if isinstance(node, ast.Import):
            return all(alias.name in {'json', 'math'} for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            return node.module in {'__future__', 'dataclasses', 'typing', 'pathlib'}
        return isinstance(node, ast.Assign)
    clef = load_module(package.__name__ + '.clef', paths['unsloth/_vendor/clef/joint_schema_model.py'], selected)
    decision = types.ModuleType(package.__name__ + '.decision')
    decision.__package__ = package.__name__
    decision.__dict__.update({name: getattr(common, name) for name in common.__all__})
    decision.__dict__.update({'Optional': __import__('typing').Optional, 'Callable': __import__('typing').Callable})
    tree = ast.parse(paths['unsloth/models/decision.py'].read_text())
    kept = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == '_clef_items':
            kept.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == 'FastDecisionModel':
            node.body = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == 'build_dataset']
            kept.append(node)
    tree.body = kept
    exec(compile(tree, str(paths['unsloth/models/decision.py']), 'exec'), decision.__dict__)
    return lock, decision.FastDecisionModel, common, clef

def assert_no_truncation(rows, tokenizer, encode_record, parsed, max_length):
    lengths = []
    for raw in rows:
        row = decode_row(raw)
        request = {'id': row['id'], 'state': parsed(row['state']), 'questions': row['questions']}
        complete = encode_record(tokenizer, request, max_length=1_000_000)
        if len(complete.input_ids) > max_length:
            raise ValueError(f"{row['id']}: {len(complete.input_ids)} tokens exceed {max_length}; no state truncation allowed")
        limited = encode_record(tokenizer, request, max_length=max_length)
        if complete.input_ids != limited.input_ids:
            raise ValueError('Unexpected tokenizer truncation')
        lengths.append(len(complete.input_ids))
    return lengths

def check(data=DEFAULT_DATA, cache=None, max_length=4096):
    verify(data)
    cache = cache or ROOT / '.semantic/decision-model-reference/contract'
    lock, api, common, clef = upstream_data_api(cache)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(lock['pilot_model']['name'], revision=lock['pilot_model']['revision'])
    # build_dataset only reads these two config attributes. No model is claimed loaded.
    config_only = types.SimpleNamespace(is_clef=True, decision_config={'max_len': max_length})
    reports = {}
    for split in SPLITS:
        rows = read_jsonl(data / f'{split}.jsonl')
        lengths = assert_no_truncation(rows, tokenizer, clef.encode_record, common._parsed, max_length)
        items, report = api.build_dataset(rows, tokenizer, config_only)
        expected = sum(len(decode_row(r)['questions']) for r in rows)
        if report['skipped'] or report['truncated'] or report['total'] != expected or len(items) != len(rows):
            raise ValueError(f'Upstream builder rejected or changed records: {split} {report}')
        for row, item in zip(rows, items):
            row = decode_row(row)
            for question, target in zip(item['record'].questions, item['targets']):
                expected_key = row['gold'][question.question_id]['label']
                if question.option_ids[target.index(max(target))] != expected_key:
                    raise ValueError('Upstream option sorting changed the gold mapping')
        reports[split] = {**report, 'rows': len(items), 'max_tokens': max(lengths),
                          'min_tokens': min(lengths), 'mean_tokens': round(sum(lengths) / len(lengths), 1)}
    return {'status': 'passed', 'unsloth_revision': lock['revision'], 'tokenizer': lock['pilot_model'],
            'checks': ['original upstream build_dataset/_clef_items', 'original upstream gold conversion',
                       'original Clef encode_record with real tokenizer', 'complete state preserved', 'option ID/label alignment'],
            'splits': reports, 'model_training_tested': False,
            'scope': 'CPU data contract check; no model weights loaded, no forward/backward or Trainer execution'}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--max-length', type=int, default=4096)
    parser.add_argument('--report', type=Path, default=DEFAULT_DATA / 'upstream_validation.json')
    args = parser.parse_args()
    result = check(args.data, max_length=args.max_length)
    write_json(args.report, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
