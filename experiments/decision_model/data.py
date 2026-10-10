"""Build/validate an auditable pilot in FastDecisionModel's native row format."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import re
import shutil
import subprocess
from pathlib import Path

from .catalog import BLOCK_OPTIONS, SOURCES, rules

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = ROOT / 'data/decision/gusu-alarm-pilot-v1'
SPLITS = ('train', 'calibration', 'test')

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]

def write_jsonl(path, rows):
    path.write_text(''.join(dumps(row) + '\n' for row in rows), encoding='utf-8')

def decode_row(row):
    row = dict(row)
    for key in ('questions', 'gold'):
        if isinstance(row.get(key), str):
            row[key] = json.loads(row[key])
    return row

def validate_row(raw):
    row = decode_row(raw)
    if set(row) != {'id', 'state', 'questions', 'gold'}:
        raise ValueError('Model row must contain only id/state/questions/gold; audit is separate')
    if not isinstance(row['state'], str) or not row['state'].strip():
        raise ValueError('state must be nonempty text (JSON strings are also supported)')
    if not isinstance(row['questions'], dict) or not row['questions']:
        raise ValueError('questions must be a nonempty mapping')
    if set(row['questions']) != set(row['gold']):
        raise ValueError('Every question must have exactly one gold')
    for name, question in row['questions'].items():
        if question.get('type') != 'choice' or not question.get('instructions'):
            raise ValueError('Pilot supports only choice questions with instructions')
        options = question.get('criteria')
        if not isinstance(options, dict) or not 2 <= len(options) <= 8 or not all(isinstance(v, str) and v for v in options.values()):
            raise ValueError('criteria must name 2..8 described choices')
        gold = row['gold'][name]
        if not isinstance(gold, dict) or set(gold) != {'label'} or gold['label'] not in options:
            raise ValueError('gold label is not in the question criteria')
    return row

def pdf_pages(path, wanted):
    if shutil.which('pdftotext'):
        # No OCR. Layout is retained in source excerpts; curated rule text is separate.
        return {page: subprocess.run(['pdftotext', '-layout', '-f', str(page), '-l', str(page), str(path), '-'],
                    check=True, capture_output=True, text=True).stdout.rstrip('\f') for page in wanted}
    from pypdf import PdfReader
    pdf = PdfReader(path)
    return {page: pdf.pages[page - 1].extract_text(extraction_mode='layout') for page in wanted}

def build(destination=DEFAULT_DATA):
    destination = Path(destination)
    if (destination / 'manifest.json').exists():
        raise ValueError('Existing snapshot is immutable; choose a new --output directory')
    source_pages = {}
    specs = rules()
    for key, source in SOURCES.items():
        path = ROOT / source['path']
        if digest(path) != source['sha256']:
            raise ValueError(f'Source changed; re-review required: {path.name}')
        wanted = sorted({p for rule in specs if rule['source'] == key for p in rule['pages']})
        source_pages[key] = {str(p): text for p, text in pdf_pages(path, wanted).items()}
    for rule in specs:
        for page, anchors in rule['anchors'].items():
            text = re.sub(r'\s+', '', source_pages[rule['source']][str(page)])
            for anchor in anchors:
                if re.sub(r'\s+', '', anchor) not in text:
                    raise ValueError(f"Source anchor absent: {rule['id']} / {page} / {anchor}")

    rows_by_split = {split: [] for split in SPLITS}
    audit = []
    for rule in specs:
        for case in rule['cases']:
            for variant in ('narrative', 'structured'):
                row_id = f"{rule['id']}/{case['name']}/{variant}"
                # Clef sorts choice keys. Permute the meaning-to-opaque-key mapping,
                # not just dict insertion order, so a fixed option position cannot win.
                actions = list(rule['options'])
                random.Random(row_id).shuffle(actions)
                mapping = {action: f'c{i}' for i, action in enumerate(actions)}
                options = {mapping[action]: rule['options'][action] for action in actions}
                questions = {}
                if rule['block_question']:
                    questions['blocking'] = {'type': 'choice', 'instructions': '依据适用规则，仅判断本条自检信号是否闭锁装置。未知型号不得套用；不据此判断全系统健康。', 'criteria': BLOCK_OPTIONS}
                questions['next_check'] = {'type': 'choice', 'instructions': '结合已知状态和已完成检查，选择当前下一项核查或处置建议。不得补造缺失观测；这里不授权执行任何现场操作。', 'criteria': options}
                if variant == 'narrative':
                    state = f"适用规则（证据摘要）：\n{rule['rule_context']}\n\n本次观测：\n{case['observations']}"
                else:
                    state = dumps({'context': {'applicable_rule': rule['rule_context']}, 'observed_state': case['observations']})
                gold = {key: {'label': mapping[value] if key == 'next_check' else value} for key, value in case['gold'].items()}
                row = {'id': row_id, 'state': state, 'questions': dumps(questions), 'gold': dumps(gold)}
                validate_row(row)
                rows_by_split[rule['split']].append(row)
                audit.append({'id': row_id, 'split': rule['split'], 'family_id': rule['id'],
                              'scenario_id': f"{rule['id']}/{case['name']}", 'variant': variant,
                              'source_id': rule['source'], 'source_path': SOURCES[rule['source']]['path'],
                              'source_sha256': SOURCES[rule['source']]['sha256'],
                              'physical_pages': rule['pages'], 'locator': rule['locator'],
                              'source_type': 'rule_derived_synthetic', 'gold_origin': 'manually_specified_candidate',
                              'review_status': 'source_checked_not_domain_expert_approved',
                              'rationale': case['reason'], 'expected_semantic_answers': case['gold'],
                              'action_key_mapping': mapping})
    destination.mkdir(parents=True, exist_ok=True)
    for split, rows in rows_by_split.items():
        write_jsonl(destination / f'{split}.jsonl', rows)
    write_jsonl(destination / 'audit.jsonl', audit)
    write_json(destination / 'rules.json', specs)
    write_json(destination / 'source_pages.json', {'sources': SOURCES, 'pages': source_pages})
    manifest = {'dataset': 'gusu-alarm-pilot-v1', 'created_date': '2026-10-09',
                'purpose': 'evidence_conditioned_decision_protocol_pilot', 'source_scope': 'rawdoc/姑苏站',
                'review_status': 'source_checked_candidate_not_expert_approved',
                'split_unit': 'rule_family; all scenarios and both renderings stay together',
                'test_interpretation': 'held-out rule-family application with evidence supplied; not closed-book knowledge retention or real-event generalization',
                'independent_events': 0, 'sources': SOURCES,
                'files': {p.name: digest(p) for p in sorted(destination.iterdir()) if p.is_file()},
                'build_code': {f: digest(Path(__file__).parent / f) for f in ('catalog.py', 'data.py')}}
    write_json(destination / 'manifest.json', manifest)
    result = verify(destination, check_sources=True)
    make_review(destination, rows_by_split, audit, specs)
    write_json(destination / 'validation.json', result)
    return result

def verify(destination=DEFAULT_DATA, check_sources=False):
    destination = Path(destination)
    manifest = json.loads((destination / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        if digest(destination / name) != expected:
            raise ValueError(f'Snapshot hash mismatch: {name}')
    if check_sources:
        for source in manifest['sources'].values():
            if digest(ROOT / source['path']) != source['sha256']:
                raise ValueError(f"Source hash mismatch: {source['path']}")
    audit_rows = read_jsonl(destination / 'audit.jsonl')
    audit = {r['id']: r for r in audit_rows}
    if len(audit) != len(audit_rows):
        raise ValueError('Duplicate audit IDs')
    families, scenarios, states, ids = {}, {}, {}, set()
    counts = {}
    distributions = {}
    for split in SPLITS:
        rows = read_jsonl(destination / f'{split}.jsonl')
        if not rows:
            raise ValueError(f'Empty split: {split}')
        decisions = 0
        labels = collections.Counter()
        for raw in rows:
            row = validate_row(raw)
            if row['id'] in ids:
                raise ValueError('Duplicate row ID')
            ids.add(row['id'])
            meta = audit[row['id']]
            if meta['split'] != split:
                raise ValueError('Audit split mismatch')
            for registry, key in [(families, meta['family_id']), (scenarios, meta['scenario_id']), (states, row['state'])]:
                if key in registry and registry[key] != split:
                    raise ValueError('Cross-split leakage')
                registry[key] = split
            for question, gold in row['gold'].items():
                semantic = meta['expected_semantic_answers'][question]
                expected = meta['action_key_mapping'][semantic] if question == 'next_check' else semantic
                if gold['label'] != expected:
                    raise ValueError('Candidate option mapping/gold mismatch')
                labels[f'{question}:{semantic}'] += 1
                decisions += 1
        counts[split] = {'rows': len(rows), 'decisions': decisions,
                         'families': sum(s == split for s in families.values()),
                         'scenarios': sum(s == split for s in scenarios.values())}
        distributions[split] = dict(sorted(labels.items()))
    if ids != set(audit):
        raise ValueError('Audit/model rows do not cover each other exactly')
    return {'status': 'passed', 'counts': counts, 'total_rows': len(ids),
            'canonical_scenarios': len(scenarios), 'rule_families': len(families),
            'label_counts': distributions, 'source_hashes_checked': check_sources,
            'upstream_tokenization_checked': False,
            'limitations': ['Synthetic pilot, no independent field events or expert approval.',
                            'Two renderings are not independent cases; metrics must also count canonical scenarios.',
                            'Rule-family isolation does not imply station/document independence.']}

def make_review(destination, rows_by_split, audit, specs):
    index = {r['id']: r for r in audit}
    lines = ['# 姑苏站告警决策试点：逐场景核读', '',
             '规则派生模拟场景；非实际故障。每个场景有自然语言/结构化两种输入，均属于同一集合。此处只展示自然语言版。', '',
             '输入明确提供适用规则，评测规则应用；来源现行性及专家审核未认证。', '']
    for split, rows in rows_by_split.items():
        lines += [f'## {split}', '']
        for raw in rows:
            if not raw['id'].endswith('/narrative'):
                continue
            row = decode_row(raw)
            meta = index[row['id']]
            lines += [f"### {row['id']}", '', row['state'], '',
                      f"来源：`{meta['source_path']}`，PDF物理页{meta['physical_pages']}，{meta['locator']}。", '']
            for name, question in row['questions'].items():
                lines += [question['instructions'], '']
                for key, description in question['criteria'].items():
                    marker = ' **← 候选标签**' if row['gold'][name]['label'] == key else ''
                    lines.append(f'- `{key}`：{description}{marker}')
                lines.append('')
            lines += ['标注依据：' + meta['rationale'], '']
    (destination / 'review.md').write_text('\n'.join(lines), encoding='utf-8')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['build', 'verify'])
    parser.add_argument('--output', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--check-sources', action='store_true')
    args = parser.parse_args()
    result = build(args.output) if args.command == 'build' else verify(args.output, args.check_sources)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
