"""Source-linked event-day splits and token-bounded windows for an evidence pilot.

This is weak supervision of explicit signal evidence, not a health classifier.
The original v1 snapshot is never changed. Only same-day past observations may
enter a carry ledger; missing initial state remains unknown.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil

from .contract_check import upstream_data_api
from .data import ROOT, decode_row, digest, dumps, read_jsonl, validate_row, write_json, write_jsonl
from .valve_windows import (CATALOG, DEFAULT_OUTPUT as V1, FAMILIES, HOSTS,
    INSTRUCTIONS, LOG_ROOT, OPTIONS, compact_events, expand_events, load_events, signal, time_of)

DEFAULT_OUTPUT = ROOT / 'data/decision/gusu-valve-windows-v2-evidence'
PILOT_OPTIONS = {q: {k: v for k, v in choices.items() if k != 'verified_expected'}
                 for q, choices in OPTIONS.items()}
MASK = '泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽'


def kind(event):
    match = re.fullmatch(r'(.*?)(出现|消失)', event['content'])
    if not match:
        return None
    name, edge = match.groups()
    category = ('alarm' if signal(event) else 'mask' if name == MASK else
                'fan' if re.fullmatch(r'E\d+\.M\d+风机运行', name) else
                'mode' if '模式' in name else None)
    if not category:
        return None
    return (event['host'], event['channel'], event['device'], name), category, edge


def ledger(events):
    """Keep latest edge(s), plus first assertion of an open masking interval.

    Contradictory simultaneous edges are unresolved regardless of input order.
    A later unambiguous observation supersedes that uncertainty.
    """
    states = {}
    for event in sorted(events, key=lambda e: (e['event_time'], e['id'])):
        item = kind(event)
        if not item or event['timestamp_marker']:
            continue
        key, category, edge = item
        old = states.get(key)
        simultaneous = old and old['last_time'] == event['event_time']
        if simultaneous:
            old['last_ids'].append(event['id'])
            if old['edge'] != edge:
                old['edge'] = '冲突'
                old['onset_id'] = None
            continue
        onset = (old['onset_id'] if old and old['edge'] == '出现' and edge == '出现'
                 else event['id'] if edge == '出现' else None)
        states[key] = {'category': category, 'edge': edge, 'last_ids': [event['id']],
                       'last_time': event['event_time'], 'onset_id': onset}
    return states


def carry_ids(history):
    keep = set()
    for state in ledger(history).values():
        if state['category'] == 'alarm' and state['edge'] == '消失':
            continue
        keep.update(state['last_ids'])
        if state['category'] == 'mask' and state['onset_id']:
            keep.add(state['onset_id'])
    return [e['id'] for e in history if e['id'] in keep]


def annotate(core, carry):
    initial = ledger(carry)
    tracked = {k for k, s in initial.items() if s['category'] == 'alarm' and s['edge'] != '消失'}
    assertions = []
    for e in core:
        item = kind(e)
        if item and item[1] == 'alarm' and item[2] == '出现' and not e['timestamp_marker']:
            tracked.add(item[0])
            assertions.append(e['id'])
    end = ledger(carry + core)
    open_keys = [k for k in tracked if end[k]['edge'] == '出现']
    uncertain = [k for k in tracked if end[k]['edge'] == '冲突']
    recovery = ('indeterminate' if uncertain else 'not_observed' if not tracked else
                'all_observed_cleared' if not open_keys else
                'all_unresolved' if len(open_keys) == len(tracked) else 'some_unresolved')
    initial_asserted = [k for k in sorted(tracked) if k in initial and initial[k]['edge'] == '出现']
    return {'candidate_labels': {
                'during_window': 'abnormal_evidence' if assertions or initial_asserted else 'insufficient_evidence',
                'at_window_end': 'abnormal_evidence' if open_keys else 'insufficient_evidence',
                'observed_recovery': recovery},
            'assertion_ids': assertions,
            'initial_open_ids': [i for k in initial_asserted for i in initial[k]['last_ids']],
            'tracked': [{'key': list(k), **end[k]} for k in sorted(tracked)],
            'expert_approved': False, 'physical_fault_confirmed': False}


def row_for(case, registry, rules, rotation=0):
    start = datetime.fromisoformat(case['start'])
    sequence = [registry[k] for k in case['carry_ids'] + case['event_ids']]
    state = {
        'scope': f"姑苏极2低端阀1阀冷{case['host']}，A/B独立；仅评估下列目标信号的记录证据。",
        'window': {'start_inclusive': case['start'], 'end_exclusive': case['end']},
        'context': '调试归档；模式信号不能证明某故障是试验注入。无全量初始/末端状态，无采集完整性证明。',
        'target_signals': '风机故障、冷却冗余丢失、膨胀罐低液位、泄漏报警、冷却水低流量、进阀温度/压力异常及对应跳闸、仪表回路异常/故障、传感器冗余超差、电导率高。其他信号保留但不在本次目标词表内。',
        'interpretation': '运行/屏蔽/维护/模式变化本身不等于故障；事件等级正常不代表健康。不使用数字等级或600秒屏蔽规则。',
        'encoding': 'devices=[host,channel,device]；records=[相对起点毫秒,设备索引,原文消息索引,原等级,时间标记]，索引从0开始。非负时间为窗口内全部事件。负时间为同日此前未见复归的目标异常、最近风机/模式/屏蔽状态及尚未结束的屏蔽起点；不是完整历史。未见事件不补作正常。',
        'carry_policy': '仅同日、同主机、严格早于窗口起点的记录。异常已在起点前明确复归的闭环不计入本窗口恢复任务；历史状态可能陈旧，同刻矛盾保留为未知。',
        **compact_events(sequence, start),
        'references': [{'locator': r['locator'], 'quotes': r['quotes']} for r in rules if r['id'] != 'reg1-3.5.5'],
        'applicability': '规程作为信号语义参考，不用现版数值定值反推2022年现场；精确点表和历史软件适用性待确认。',
    }
    questions, gold, mappings = {}, {}, {}
    for qid, options in PILOT_OPTIONS.items():
        labels = list(options)
        n = rotation % len(labels)
        labels = labels[n:] + labels[:n]
        mapping = {f'c{i}': label for i, label in enumerate(labels)}
        questions[qid] = {'type': 'choice', 'instructions': INSTRUCTIONS[qid],
                          'criteria': {k: options[v] for k, v in mapping.items()}}
        target = case['annotation']['candidate_labels'][qid]
        gold[qid] = {'label': next(k for k, v in mapping.items() if v == target)}
        mappings[qid] = mapping
    row = {'id': case['id'] + f'/rotation-{rotation}', 'state': dumps(state),
           'questions': dumps(questions), 'gold': dumps(gold)}
    validate_row(row)
    return row, mappings


def related_context(events):
    """Bounded lexical review of all exports, with explicit scope rejection."""
    direct = [e for e in events if re.search(r'模式|命令', e['content'])]
    related, failures = [], []
    pattern = re.compile(r'(?:极(?:2|Ⅱ|II|二)低端(?:阀1|VSC1)|P221VCC[12])', re.I)
    intent = re.compile(r'命令|操作|切换|试验|测试|调试|手动|自动')
    scanned = 0
    for path in sorted(LOG_ROOT.glob('*.txt')):
        source = str(path.relative_to(ROOT))
        for number, raw in enumerate(path.read_text(encoding='gb18030').splitlines(), 1):
            scanned += 1
            if not pattern.search(raw) or not intent.search(raw) or re.search(r'\bP221VCC[12]\b', raw):
                continue
            parts = re.split(r'\s{2,}', raw.strip(), maxsplit=5)
            if len(parts) != 6:
                failures.append({'source': source, 'line': number, 'raw': raw})
                continue
            stamp, host, channel, level, device, content = parts
            related.append({'event_time': stamp, 'host': host, 'channel': channel,
                            'device': device, 'content': content, 'source': source, 'line': number,
                            'raw': raw, 'use': 'context_candidate_not_proof_of_executed_operation'})
    return {'scanned_lines': scanned, 'direct_context_event_ids': [e['id'] for e in direct],
            'related_host_candidates': related, 'matching_unparsed_lines': failures,
            'search': {'equipment': pattern.pattern, 'intent': intent.pattern},
            'conclusion': '存在目标主机调试/在线测试模式及主泵切换命令异常记录；不等于操作成功或窗口符合预期。',
            'document_review': [
                {'source_id': 'D176', 'locator': '正文块7146–7164', 'finding': '极II低端VSC1两套保护投退和无异常检查的典型操作票模板；不是已执行票'},
                {'source_id': 'D157/D158/D166', 'finding': '相关低端试验/温度记录描述建昌站，排除作为姑苏样本依据'},
                {'source_id': 'D159/D165', 'finding': '姑苏相关试验为高端设备，不能标注本批低端过程正常'}],
            'limitations': '可提取文本和上述词法范围；非OCR或全部语义表达穷举。未找到目标设备正常操作完整闭环。'}


def build(output, model_dir, cache, max_length=4096):
    output = Path(output)
    if output.exists():
        raise ValueError('Snapshot exists; choose a new directory')
    from transformers import AutoTokenizer
    import transformers
    lock, api, common, clef = upstream_data_api(cache)
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    def length(row):
        row = decode_row(row)
        encoded = clef.encode_record(tokenizer, {'state': common._parsed(row['state']), 'questions': row['questions']}, max_length=1_000_000)
        return len(encoded.input_ids)
    events, sources, _ = load_events()
    registry = {e['id']: e for e in events}
    rules = json.loads((V1 / 'rules.json').read_text())
    catalog = json.loads(CATALOG.read_text())
    cases = []
    for parent in catalog['windows']:
        for host in sorted(HOSTS):
            day = [e for e in events if e['host'] == host and e['event_time'][:10] == parent['start'][:10]]
            core = [e for e in day if parent['start'] <= e['event_time'] < parent['end']]
            if not core:
                continue
            groups = []
            for e in core:
                if not groups or groups[-1][0]['event_time'] != e['event_time']:
                    groups.append([])
                groups[-1].append(e)
            offset, index, start = 0, 0, parent['start']
            while offset < len(groups):
                history = [e for e in day if e['event_time'] < start]
                carried = carry_ids(history)
                def proposal(stop):
                    current = [e for g in groups[offset:stop] for e in g]
                    end = groups[stop][0]['event_time'] if stop < len(groups) else parent['end']
                    case = {'id': f"{parent['id']}/{host}/{index:03d}", 'parent_id': parent['id'],
                            'host': host, 'start': start, 'end': end,
                            'split': parent['proposed_split'], 'group_id': parent['group_id'],
                            'carry_ids': carried, 'event_ids': [e['id'] for e in current],
                            'annotation': annotate(current, [registry[k] for k in carried])}
                    return case
                # All cyclic placements have the same vocabulary. Measure every
                # chosen output placement; binary search uses rotation zero.
                low, high, best = offset+1, len(groups), None
                while low <= high:
                    mid = (low+high)//2
                    candidate = proposal(mid)
                    if length(row_for(candidate, registry, rules)[0]) <= max_length-16:
                        best, low = mid, mid+1
                    else:
                        high = mid-1
                if best is None:
                    raise ValueError(f'Carry plus one timestamp exceeds budget: {parent["id"]}/{host}/{start}')
                # Prefer a pause near the capacity boundary without dropping any
                # intervening record. Otherwise split at a timestamp boundary.
                pauses = [j for j in range(offset+1, best+1) if j < len(groups)
                          and j >= offset + (best-offset)*0.65
                          and (time_of(groups[j][0])-time_of(groups[j-1][-1])).total_seconds() >= 30]
                stop = pauses[-1] if pauses and best < len(groups) else best
                case = proposal(stop)
                case['boundary_reason'] = 'parent_end' if stop == len(groups) else 'pause_ge_30s' if stop in pauses else 'token_budget_timestamp_boundary'
                case['lengths'] = [length(row_for(case, registry, rules, r)[0]) for r in range(5)]
                if max(case['lengths']) > max_length:
                    raise ValueError('Option rotation exceeds budget; no truncation permitted')
                cases.append(case)
                offset, index, start = stop, index+1, case['end']
    output.mkdir(parents=True)
    write_jsonl(output / 'events.jsonl', events)
    write_jsonl(output / 'windows.jsonl', cases)
    write_json(output / 'context_search.json', related_context(events))
    for name in ('rules.json', 'documents.json'):
        shutil.copyfile(V1 / name, output / name)
    write_json(output / 'schema.json', {'options': PILOT_OPTIONS, 'signal_families': FAMILIES,
        'scope': '目标词表内异常证据与复归；无正常分类，无现场金标', 'history_horizon': 'same_event_day_only'})
    rows = defaultdict(list)
    audit, probes = [], []
    for case in cases:
        rotation = int(hashlib.sha256(case['id'].encode()).hexdigest()[:8], 16) % 5
        row, mapping = row_for(case, registry, rules, rotation)
        rows[case['split']].append(row)
        audit.append({'id': row['id'], 'window_id': case['id'], 'parent_id': case['parent_id'],
                      'split': case['split'], 'group_id': case['group_id'], 'mapping': mapping,
                      'labels': case['annotation']['candidate_labels'], 'tokens': case['lengths'][rotation],
                      'expert_approved': False})
        if case['split'] == 'test':
            for r in range(5):
                row, mapping = row_for(case, registry, rules, r)
                row.pop('gold')
                probes.append({**row, 'window_id': case['id'], 'mapping': mapping})
    for split in ('train', 'calibration', 'test'):
        write_jsonl(output / f'{split}.jsonl', rows[split])
    write_jsonl(output / 'audit.jsonl', audit)
    write_jsonl(output / 'robustness_requests.jsonl', probes)
    summary = {'windows': len(cases), 'parents': len(catalog['windows']),
               'core_events': sum(len(c['event_ids']) for c in cases),
               'max_tokens': max(max(c['lengths']) for c in cases),
               'min_tokens': min(min(c['lengths']) for c in cases),
               'splits': {s: {'windows': len(rows[s]),
                    'days': sorted({c['start'][:10] for c in cases if c['split'] == s}),
                    'labels': {q: dict(Counter(c['annotation']['candidate_labels'][q] for c in cases if c['split'] == s)) for q in PILOT_OPTIONS}}
                    for s in rows}, 'normal_examples': 0}
    write_json(output / 'summary.json', summary)
    text = ['# 姑苏阀冷 v2：证据识别与复归追踪', '',
            '冻结的弱监督试验集。无业务专家金标；不用于宣称正常/异常现场准确率。', '',
            '按同日历史携带未复归异常、屏蔽起点和最近风机/模式状态。原始窗口内全部事件恰好保留一次；A/B不相互抵消。跨日状态未知。', '',
            'train/calibration/test 沿用 v1 事件日隔离；calibration 保留作验证，不拟合置信度。测试选项轮换不增加独立样本数。', '',
            'events.jsonl 保留所有来源行；windows.jsonl 以 carry_ids/event_ids 指向证据。context_search.json 记录操作背景检索。', '',
            '| 子窗口 | 分组 | 核心事件 | 历史记录 | 最大token | 窗口内 / 末端 / 复归候选 |',
            '| --- | --- | --- | --- | --- | --- |']
    for c in cases:
        text.append(f"| {c['id']} | {c['split']} | {len(c['event_ids'])} | {len(c['carry_ids'])} | {max(c['lengths'])} | {' / '.join(c['annotation']['candidate_labels'].values())} |")
    (output / 'README.md').write_text('\n'.join(text)+'\n')
    manifest = {'dataset': output.name, 'task': 'rule_supervised_evidence_tracking_pilot',
                'expert_approved': False, 'full_health_classifier_ready': False,
                'evidence_pilot_ready': True, 'max_length': max_length,
                'v1_manifest_sha256': digest(V1 / 'manifest.json'),
                'parent_catalog': catalog, 'sources': sources,
                'tokenizer': lock['pilot_model'], 'transformers_version': transformers.__version__,
                'tokenizer_files': {p.name: digest(p) for p in Path(model_dir).glob('*.json')},
                'unsloth_revision': lock['revision'], 'builder_sha256': digest(__file__),
                'summary': summary, 'files': {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob('*')) if p.is_file()}}
    write_json(output / 'manifest.json', manifest)
    return verify(output, check_sources=True)


def verify(folder=DEFAULT_OUTPUT, check_sources=False):
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        if digest(folder / name) != expected:
            raise ValueError(f'Frozen file changed: {name}')
    events = read_jsonl(folder / 'events.jsonl')
    registry = {e['id']: e for e in events}
    if len(registry) != len(events) or any(e['event_time'][:10] == '2026-09-11' for e in events):
        raise ValueError('Duplicate or reserved-case event')
    if check_sources:
        lines = {}
        for source in manifest['sources']:
            path = ROOT / source['path']
            if digest(path) != source['sha256']:
                raise ValueError('Source changed')
            lines[source['path']] = path.read_text(encoding='gb18030').splitlines()
        for e in events:
            for p in e['provenance']:
                if lines[p['source']][p['line']-1] != p['raw']:
                    raise ValueError('Source line mismatch')
    cases = read_jsonl(folder / 'windows.jsonl')
    rules = json.loads((folder / 'rules.json').read_text())
    splits = defaultdict(set)
    covered = defaultdict(list)
    for c in cases:
        splits[c['start'][:10]].add(c['split'])
        core = [registry[k] for k in c['event_ids']]
        history = [e for e in events if e['host'] == c['host'] and e['event_time'][:10] == c['start'][:10] and e['event_time'] < c['start']]
        expected_core = [e['id'] for e in events if e['host'] == c['host'] and c['start'] <= e['event_time'] < c['end']]
        if expected_core != c['event_ids'] or carry_ids(history) != c['carry_ids']:
            raise ValueError('Core coverage or causal carry mismatch')
        carry = [registry[k] for k in c['carry_ids']]
        complete_state, carried_state = ledger(history + core), ledger(carry + core)
        for key, state in complete_state.items():
            if state['category'] == 'alarm' and state['edge'] == '消失' and key not in carried_state:
                continue
            for field in ('edge', 'last_ids', 'last_time'):
                if carried_state[key][field] != state[field]:
                    raise ValueError('Carry changes last-known state')
            if state['category'] == 'mask' and state['onset_id'] != carried_state[key]['onset_id']:
                raise ValueError('Carry loses masking onset')
        if annotate(core, carry) != c['annotation']:
            raise ValueError('Candidate label mismatch')
        if max(c['lengths']) > manifest['max_length']:
            raise ValueError('Overlength record')
        covered[c['parent_id']].extend(c['event_ids'])
    for p in manifest['parent_catalog']['windows']:
        expected = [e['id'] for e in events if p['start'] <= e['event_time'] < p['end']]
        if Counter(expected) != Counter(covered[p['id']]):
            raise ValueError('Parent events not covered exactly once')
    if any(len(s) != 1 for s in splits.values()):
        raise ValueError('Event day leaks across splits')
    by_id = {c['id']: c for c in cases}
    for split in ('train', 'calibration', 'test'):
        for row in read_jsonl(folder / f'{split}.jsonl'):
            cid, r = row['id'].rsplit('/rotation-', 1)
            c = by_id[cid]
            if c['split'] != split or row != row_for(c, registry, rules, int(r))[0]:
                raise ValueError('Row/evidence/mapping mismatch')
    probes = read_jsonl(folder / 'robustness_requests.jsonl')
    for c in [c for c in cases if c['split'] == 'test']:
        selected = [p for p in probes if p['window_id'] == c['id']]
        if len(selected) != 5:
            raise ValueError('Missing option rotations')
        for qid, opts in PILOT_OPTIONS.items():
            for label in opts:
                if {k for p in selected for k, v in p['mapping'][qid].items() if v == label} != {f'c{i}' for i in range(len(opts))}:
                    raise ValueError('Incomplete choice-position coverage')
        for p in selected:
            r = int(p['id'].rsplit('-', 1)[1])
            row, mapping = row_for(c, registry, rules, r)
            row.pop('gold')
            if p != {**row, 'window_id': c['id'], 'mapping': mapping}:
                raise ValueError('Probe differs from evidence row')
    return {**manifest['summary'], 'verified': True, 'expert_approved': False,
            'evidence_pilot_ready': True, 'full_health_classifier_ready': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'verify'])
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--model-dir', type=Path, default=ROOT / '.semantic/decision-tokenizer')
    parser.add_argument('--cache', type=Path, default=ROOT / '.semantic/decision-model-reference/contract')
    parser.add_argument('--max-length', type=int, default=4096)
    parser.add_argument('--check-sources', action='store_true')
    args = parser.parse_args()
    result = build(args.output, args.model_dir, args.cache, args.max_length) if args.action == 'build' else verify(args.output, args.check_sources)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
