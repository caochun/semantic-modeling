"""Build an auditable, NOT expert-labelled batch of Gusu valve-cooling windows.

The source event stream is preserved within a declared host/time scope. Proposed
labels describe evidence, never a claim that commissioning alarms prove damage.
No training, source edits or use of the user's held-out case occurs here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re

from .data import ROOT, digest, dumps, read_jsonl, validate_row, write_json, write_jsonl

HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / 'data/decision/gusu-valve-windows-v1-candidates'
LOG_ROOT = ROOT / 'rawdoc/姑苏站/调试日报/事件汇总（第一阶段）'
CATALOG = HERE / 'valve_window_catalog.json'
SOURCES = HERE / 'valve_window_sources.json'
HOSTS = {'P221VCC1', 'P221VCC2'}
ROW_RE = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)(\*)?\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+)$')
OPTIONS = {
    'during_window': {
        'abnormal_evidence': '窗口内有明确异常信号或异常现象证据；可能来自试验，不等于设备实际损坏。',
        'verified_expected': '有完整且适用的运行/操作依据，足以确认所评估过程符合预期。',
        'insufficient_evidence': '现有记录不足以确认异常或符合预期，需补充状态或操作背景。',
    },
    'at_window_end': {
        'abnormal_evidence': '末端仍有未见复归的明确异常证据；这是记录中的最后已知状态，现场持续性待核实。',
        'verified_expected': '有充分末端状态/操作核对证据，确认本次评估范围符合预期。',
        'insufficient_evidence': '末端状态证据不足；个别信号复归或没有新报警，不能证明整个评估范围正常。',
    },
    'observed_recovery': {
        'all_observed_cleared': '本窗口及前置上下文中已观察到出现的目标异常信号，在窗口末均有后续复归记录。',
        'some_unresolved': '部分已观察到出现的目标异常信号已复归，另有部分未见后续复归。',
        'all_unresolved': '已观察到出现的目标异常信号均未见后续复归记录。',
        'not_observed': '未观察到目标异常信号出现，不能据此判断系统正常或确认其恢复。',
        'indeterminate': '同一信号同一时刻有矛盾边沿，无法确定其复归状态。',
    },
}
INSTRUCTIONS = {
    'during_window': '判断指定窗口内是否存在阀冷异常证据。区分信号证据、试验注入与设备真实损坏；不能用事件等级“正常”推断系统健康。',
    'at_window_end': '判断截至窗口结束时阀冷异常证据状态。只使用截止时间之前的信息；无复归是记录状态，不保证现场持续异常；全部已见信号复归也不等于全系统正常。',
    'observed_recovery': '仅检查本窗口或前置上下文中曾明确出现的目标异常信号是否随后复归。按主机、A/B通道和信号分别追踪；未观察到出现的单独消失不能算恢复闭环。',
}
LABEL_NAMES = {'abnormal_evidence': '有异常证据', 'verified_expected': '已核实符合预期',
               'insufficient_evidence': '信息不足', 'all_observed_cleared': '已见目标信号均复归',
               'some_unresolved': '部分未见复归', 'all_unresolved': '均未见复归',
               'not_observed': '未观察到目标信号出现', 'indeterminate': '边沿冲突，无法确定'}
# Explicit signal families, not an alarm-level classifier. Switch openings,
# protection masks, setpoint changes and maintenance states are not faults here.
FAMILIES = [
    ('fan_fault', r'E\d+\.M\d+风机(?:变频器|变频回路|工频回路)?故障', 'reg5-11.1'),
    ('cooling_redundancy_lost', r'冷却系统失去冗余冷却能力', 'reg5-11.1'),
    ('low_tank_level', r'(?:膨胀罐|高位水箱)(?:水位|液位)(?:低|超低)', 'reg5-10.3'),
    ('leak_alarm', r'(?:24小时泄漏报警|渗漏报警|泄漏报警)', 'reg5-10.3'),
    ('cooling_flow', r'冷却水流量(?:低|超低)', 'reg1-3.5.5'),
    ('inlet_temperature', r'进阀温度(?:高|超高)', 'reg1-3.5.5'),
    ('inlet_pressure', r'进阀压力(?:低|超低|高|超高)', 'reg1-3.5.5'),
    ('sensor_circuit', r'.+仪表回路(?:异常|故障)', 'reg5-10.14'),
    ('sensor_disagreement', r'.+冗余超差', 'reg5-10.14'),
    ('conductivity', r'(?:冷却水|去离子水)电导率(?:高|超高)', 'reg5-10.7'),
    ('cooling_trip_request', r'(?:进阀温度超高|膨胀罐液位超低|进阀压力超低且冷却水流量低|冷却水流量超低且进阀压力低)跳闸', 'reg1-3.5.5'),
]


def time_of(event):
    return datetime.fromisoformat(event['event_time'])


def parse_line(line, source, number):
    match = ROW_RE.fullmatch(line.strip())
    if not match:
        raise ValueError(f'Unparsed target-host record: {source}:{number}')
    stamp, marker, host, channel, level, device, content = match.groups()
    event = {'event_time': datetime.fromisoformat(stamp).isoformat(timespec='milliseconds'),
             'station': '姑苏', 'host': host, 'channel': channel,
             'event_level_text': level, 'device': device, 'content': content,
             'timestamp_marker': marker or ''}
    event['id'] = 'e-' + hashlib.sha256(dumps(event).encode()).hexdigest()[:20]
    event['provenance'] = [{'source': source, 'line': number, 'raw': line}]
    return event


def load_events():
    sources, unique = [], {}
    duplicates = 0
    for path in sorted(LOG_ROOT.glob('*.txt')):
        rel = str(path.relative_to(ROOT))
        lines = path.read_text(encoding='gb18030').splitlines()
        dates, records = set(), 0
        for number, line in enumerate(lines, 1):
            if not re.search(r'\bP221VCC[12]\b', line):
                continue
            event = parse_line(line, rel, number)
            dates.add(event['event_time'][:10])
            records += 1
            if event['id'] in unique:
                unique[event['id']]['provenance'].extend(event['provenance'])
                duplicates += 1
            else:
                unique[event['id']] = event
        hint = re.search(r'saveevent(\d{4})', path.name)
        sources.append({'path': rel, 'sha256': digest(path), 'encoding': 'gb18030',
                        'target_records': records, 'event_dates': sorted(dates),
                        'filename_date_hint_differs': bool(hint and any(d[5:].replace('-', '') != hint[1] for d in dates))})
    return sorted(unique.values(), key=lambda e: (e['event_time'], e['id'])), sources, duplicates


def signal(event):
    content = event['content']
    match = re.fullmatch(r'(.*?)(出现|消失)', content)
    if not match:
        return None
    name, edge = match.groups()
    if not event['device'].startswith('极2低端阀1许继阀冷'):
        return None
    for family, pattern, rule in FAMILIES:
        if re.fullmatch(pattern, name):
            return {'key': (event['host'], event['channel'], name),
                    'name': name, 'edge': edge, 'family': family, 'rule_id': rule}
    return None


def annotate(events, preceding):
    states, tracked, during, excluded = {}, set(), [], []
    def update(item, event):
        old = states.get(item['key'])
        if old and old[1]['event_time'] == event['event_time'] and old[0]['edge'] != item['edge']:
            item = {**item, 'edge': '冲突'}
        states[item['key']] = (item, event)
    for event in preceding:
        item = signal(event)
        if item and not event['timestamp_marker']:
            update(item, event)
    # Only open states at the window start carry forward. Earlier, already
    # cleared faults do not count as occurrences inside the assessed window.
    initial = [event['id'] for item, event in states.values() if item['edge'] == '出现']
    tracked.update(k for k, (item, _) in states.items() if item['edge'] == '出现')
    for event in events:
        item = signal(event)
        if not item:
            continue
        if event['timestamp_marker']:
            excluded.append(event['id'])
            continue
        update(item, event)
        if item['edge'] == '出现':
            tracked.add(item['key'])
            during.append(event['id'])
    details = []
    for key in sorted(tracked):
        item, last = states[key]
        details.append({'host': key[0], 'channel': key[1], 'signal': key[2],
                        'family': item['family'], 'rule_id': item['rule_id'],
                        'last_edge': item['edge'], 'last_event_id': last['id'],
                        'last_event_time': last['event_time']})
    open_signals = [x for x in details if x['last_edge'] == '出现']
    if any(x['last_edge'] == '冲突' for x in details):
        recovery = 'indeterminate'
    elif not details:
        recovery = 'not_observed'
    elif not open_signals:
        recovery = 'all_observed_cleared'
    elif len(open_signals) == len(details):
        recovery = 'all_unresolved'
    else:
        recovery = 'some_unresolved'
    labels = {'during_window': 'abnormal_evidence' if during or initial else 'insufficient_evidence',
              'at_window_end': 'abnormal_evidence' if open_signals else 'insufficient_evidence',
              'observed_recovery': recovery}
    # A last assertion alone supports an unresolved-report label, not a claim
    # of continuous physical failure. Capture completeness is never assumed.
    return {'candidate_labels': labels, 'initial_open_evidence': initial,
            'assertion_evidence': during, 'tracked_signals': details,
            'timestamp_marked_evidence_excluded': excluded,
            'unclassified_non_normal_events': [e['id'] for e in events
                if e['event_level_text'] != '正常' and not signal(e)],
            'rationale': {
                'during_window': '所列出现记录/窗口起点前最后已知出现状态支持存在异常证据；若无，不反推正常。',
                'at_window_end': '仅追踪已观察到出现的目标信号；未见复归为最后已知记录状态，全部复归仍缺少全面末端状态。',
                'observed_recovery': '按主机、通道、信号分别匹配出现和后续消失；单独消失不补造此前出现。'},
            'physical_fault_confirmed': False,
            'operation_context': 'commissioning_archive; exact low-end trial not established',
            'review_status': 'rule_proposed_source_linked_not_expert_approved'}


def mask_intervals(events):
    opened, completed, left = {}, [], []
    for event in events:
        content = event['content']
        key = (event['host'], event['channel'])
        if content == '泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽出现':
            opened.setdefault(key, event)
        elif content == '泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽消失':
            start = opened.pop(key, None)
            if start is None:
                left.append(event['id'])
                continue
            fans = [e for e in events if (e['host'], e['channel']) == key
                    and start['event_time'] <= e['event_time'] <= event['event_time']
                    and re.fullmatch(r'E\d+\.M\d+风机运行(?:出现|消失)', e['content'])]
            last = fans[-1] if fans else None
            completed.append({'start_event_id': start['id'], 'end_event_id': event['id'],
                              'duration_s': round((time_of(event)-time_of(start)).total_seconds(), 3),
                              'last_fan_event_id': last['id'] if last else None,
                              'last_fan_to_clear_s': round((time_of(event)-time_of(last)).total_seconds(), 3) if last else None})
    return {'complete_intervals': completed, 'left_censored_clear_events': left,
            'right_censored_assert_events': [e['id'] for e in opened.values()],
            'interpretation': 'Timing observations only; no 600-second threshold is assumed.'}


def rule_bundle(documents):
    reg3 = next(d for d in documents if d['id'] == 'D172')
    reg1 = next(d for d in documents if d['id'] == 'D171')
    reg5 = next(d for d in documents if d['id'] == 'D174')
    return [
        {'id': 'reg3-fan-groups', 'source': reg3['source'],
         'locator': '第10章，变频风机运行规定；Word CP 134765–134979',
         'quotes': [s['text'] for s in reg3['sections'] if '134765' in s['locator'] or '134892' in s['locator'] or '134923' in s['locator']]},
        {'id': 'reg5-11.1', 'source': reg5['source'], 'locator': '11.1.1.1，PDF第68页，印刷页58',
         'quotes': ['事件记录发外水冷冷却塔风扇故障报警和冗余冷却能力丢失报警信号；']},
        {'id': 'reg5-10.3', 'source': reg5['source'], 'locator': '10.3.1.1、10.3.1.2，PDF第63页，印刷页53',
         'quotes': ['事件记录发24小时泄漏报警或高位水箱(膨胀罐)水位低报警；', '巡视或监视过程中，发现内水冷有泄漏或水位异常降低。']},
        {'id': 'reg1-3.5.5', 'source': reg1['source'], 'locator': '表3.5-5 河南晶锐阀冷保护功能说明',
         'quotes': ['进阀温度高45℃；进阀温度超高48℃。', '进阀流量低102L/s，进阀流量超低97L/s；',
                    '进阀压力高1.1Mpa，进阀压力超高1.13Mpa，进阀压力低0.58Mpa，进阀压力超低0.55Mpa。',
                    '膨胀罐液位超低跳闸', '阀冷系统微分泄漏跳闸']},
        {'id': 'reg5-10.14', 'source': reg5['source'], 'locator': '10.14.1.1、10.14.2.1，PDF第67页，印刷页57',
         'quotes': ['运行人员工作站报“内水冷传感器故障相应报警”信号。',
                    '在水冷控制面板上检查各传感器读数是否相近，若发现报警传感器测量值异常，做好隔离措施及时检查处理。']},
        {'id': 'reg5-10.7', 'source': reg5['source'], 'locator': '10.7.1.1，PDF第65页，印刷页55',
         'quotes': ['事件记录发冷却水电导率高报警信号。']},
    ]


def compact_events(events, start):
    """Lossless dictionary representation, no semantic filtering/summarization."""
    devices, messages = [], []
    device_keys, message_keys = {}, {}
    records = []
    for event in events:
        device = [event['host'], event['channel'], event['device']]
        key = tuple(device)
        if key not in device_keys:
            device_keys[key] = len(devices)
            devices.append(device)
        if event['content'] not in message_keys:
            message_keys[event['content']] = len(messages)
            messages.append(event['content'])
        offset = (time_of(event) - start) // timedelta(milliseconds=1)
        records.append([offset, device_keys[key], message_keys[event['content']],
                        event['event_level_text'], event['timestamp_marker']])
    return {'devices': devices, 'messages': messages, 'records': records}


def expand_events(bundle, start):
    result = []
    for offset, device_key, message_key, level, marker in bundle['records']:
        host, channel, device = bundle['devices'][device_key]
        result.append({'event_time': (start + timedelta(milliseconds=offset)).isoformat(timespec='milliseconds'),
                       'station': '姑苏', 'host': host, 'channel': channel, 'device': device,
                       'content': bundle['messages'][message_key], 'event_level_text': level,
                       'timestamp_marker': marker})
    return result


def model_row(case, registry, rules, rotation=0):
    # The scope/schema definition belongs in the input; candidate labels,
    # matched families, extracted rationales and analyst timing do not.
    sequence = [registry[k] for k in case['preceding_event_ids'] + case['event_ids']]
    start = datetime.fromisoformat(case['start'])
    bundle = compact_events(sequence, start)
    for original, restored in zip(sequence, expand_events(bundle, start)):
        if any(original[k] != value for k, value in restored.items()):
            raise ValueError('Lossy event serialization')
    state = {
        'scope': '姑苏极2低端阀1阀冷P221VCC1/P221VCC2，A/B分别保留',
        'window': {'start_inclusive': case['start'], 'end_exclusive': case['end']},
        'context': '调试归档，未确认对应试验，未提供初始全量状态、模拟量趋势或采集完整性证明。',
        'target_signals': '风机故障、冷却冗余丢失、膨胀罐低液位、泄漏报警、冷却水低流量、进阀温度/压力异常及对应跳闸、仪表回路异常/故障、传感器冗余超差、电导率高报警。',
        'interpretation': '运行/屏蔽/维护状态变化本身不等于故障；没有数字等级字典。仪表异常不等于被测设备本体故障。',
        'encoding': '字典索引从0开始。devices=[host,channel,device]；records=[相对window.start的毫秒数,devices索引,messages索引,原始事件等级,原始时间标记]。负时间是窗口前5分钟上下文。所有原始消息均保留。',
        **bundle,
        'rule_applicability': '原文作为信号语义参考，2022年现场软件与当前规程适用性未确认，不用现有数值定值反推历史状态。',
        'references': [{'locator': r['locator'], 'quotes': r['quotes']} for r in rules if r['id'] != 'reg1-3.5.5'],
    }
    questions, gold, mappings = {}, {}, {}
    for qid, options in OPTIONS.items():
        labels = list(options)
        labels = labels[rotation % len(labels):] + labels[:rotation % len(labels)]
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


def review_case(case, registry):
    annotation = case['annotation']
    labels = annotation['candidate_labels']
    text = [f"# {case['id']}", '', f"窗口：`[{case['start']}, {case['end']})`；建议分组：{case['proposed_split']}。", '',
            '标签是基于事件语义和规则的候选，尚无业务专家确认；不证明设备损坏或全系统健康。', '',
            '| 判断 | 候选含义 |', '| --- | --- |']
    text += [f'| {q} | {OPTIONS[q][label]} |' for q, label in labels.items()]
    text += ['', '## 核对重点', '',
             '- 本批未找到与此低端窗口直接对应的试验命令；高端试验日报不能直接套用。',
             '- 起点没有全量状态；末端无复归仅为最后已知记录。各通道保持独立。',
             '- 风机启停与短时屏蔽的触发/重计时条件、数字等级、点表映射和历史软件版本待补齐。',
             f"- 有 {len(annotation['unclassified_non_normal_events'])} 条非“正常”等级事件未进入明确目标异常词表，全部保留在原始记录中，须核读。", '',
             '## 已观察到的目标异常状态', '', '| 主机/通道 | 信号 | 窗口末最后已知事件 | 时间 |', '| --- | --- | --- | --- |']
    for s in annotation['tracked_signals']:
        text.append(f"| {s['host']}/{s['channel']} | {s['signal']} | {s['last_edge']} | {s['last_event_time']} |")
    timing = case['mask_timing']
    text += ['', '## 屏蔽时序（不作为定值）', '',
             '| 通道 | 屏蔽出现 | 屏蔽消失 | 持续秒数 | 最后风机事件至解除秒数 |',
             '| --- | --- | --- | --- | --- |']
    for interval in timing['complete_intervals']:
        first, last = registry[interval['start_event_id']], registry[interval['end_event_id']]
        text.append(f"| {first['host']}/{first['channel']} | {first['event_time']} | {last['event_time']} | {interval['duration_s']} | {interval['last_fan_to_clear_s']} |")
    text += ['', f"仅见解除、缺少起点的片段：{len(timing['left_censored_clear_events'])}；仅见出现、未见解除的片段：{len(timing['right_censored_assert_events'])}。不能将边界截断当作屏蔽超时。", '']
    for title, ids in [('前置上下文', case['preceding_event_ids']), ('窗口内全部记录', case['event_ids'])]:
        text += [f'## {title}', '', '| 时间 | 主机/通道 | 原始等级 | 原始事件 | 来源 |', '| --- | --- | --- | --- | --- |']
        for key in ids:
            e = registry[key]
            source = e['provenance'][0]
            link = '../../../../' + source['source']
            text.append(f"| {e['event_time']} | {e['host']}/{e['channel']} | {e['event_level_text']} | {e['content'].replace('|', '／')} | [{Path(source['source']).name}:{source['line']}](<{link}>) |")
        text.append('')
    text += ['', '## 业务复核记录', '', '- 窗口内候选判断：待复核', '- 窗口末候选判断：待复核',
             '- 试验/操作背景及依据：待补充', '- 需要调整的范围、遗漏信号、适用规则：待填写', '- 复核人/日期：未填写', '']
    return '\n'.join(text)


def build(output=DEFAULT_OUTPUT):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a new output directory; candidate snapshots are immutable')
    catalog = json.loads(CATALOG.read_text())
    documents = json.loads(SOURCES.read_text())
    for source in documents:
        if digest(ROOT / source['source']) != source['sha256']:
            raise ValueError(f"Document changed: {source['source']}")
    rules = rule_bundle(documents)
    # Check each source quote, allowing whitespace/layout differences only.
    normalize = lambda x: re.sub(r'\s+', '', x)
    for rule in rules:
        document = next(d for d in documents if d['source'] == rule['source'])
        source_text = normalize('\n'.join(s['text'] for s in document['sections']))
        for quote in rule['quotes']:
            if normalize(quote) not in source_text:
                raise ValueError(f"Quote absent from preserved source: {rule['id']} / {quote}")
    events, sources, duplicates = load_events()
    cases, included = [], set()
    for spec in catalog['windows']:
        start, end = datetime.fromisoformat(spec['start']), datetime.fromisoformat(spec['end'])
        preceding = [e for e in events if start-timedelta(seconds=300) <= time_of(e) < start]
        current = [e for e in events if start <= time_of(e) < end]
        if not current:
            raise ValueError(f"Empty window: {spec['id']}")
        case = {**spec, 'scope': catalog['scope'],
                'event_ids': [e['id'] for e in current], 'preceding_event_ids': [e['id'] for e in preceding],
                'annotation': annotate(current, preceding), 'mask_timing': mask_intervals(preceding+current),
                'capture_completeness': 'unknown; all exported records within declared host/time scope retained',
                'candidate_only': True, 'expert_gold': None}
        cases.append(case)
        included.update(case['event_ids'] + case['preceding_event_ids'])
    selected = [e for e in events if e['id'] in included]
    registry = {e['id']: e for e in selected}
    output.mkdir(parents=True)
    (output / 'review').mkdir()
    write_jsonl(output / 'events.jsonl', selected)
    write_jsonl(output / 'windows.jsonl', cases)
    write_json(output / 'documents.json', documents)
    write_json(output / 'rules.json', rules)
    write_json(output / 'signal_families.json', FAMILIES)
    write_json(output / 'schema.json', {'options': OPTIONS, 'instructions': INSTRUCTIONS})
    rows, audit, probes = [], [], []
    for case in cases:
        # Training proposal rotates semantic-to-key mapping across windows.
        rotation = int(hashlib.sha256(case['id'].encode()).hexdigest()[:8], 16) % 3
        row, mapping = model_row(case, registry, rules, rotation)
        rows.append(row)
        audit.append({'id': row['id'], 'window_id': case['id'], 'group_id': case['group_id'],
                      'proposed_split': case['proposed_split'], 'option_mapping': mapping,
                      'candidate_labels': case['annotation']['candidate_labels'], 'expert_approved': False})
        if case['proposed_split'] == 'test':
            for rotation in range(3):
                probe, mapping = model_row(case, registry, rules, rotation)
                # No gold passed to inference. Expected labels stay in windows.
                probe.pop('gold')
                probe['label_map'] = mapping
                probe['window_id'] = case['id']
                probes.append(probe)
        (output / 'review' / (case['id'] + '.md')).write_text(review_case(case, registry), encoding='utf-8')
    write_jsonl(output / 'candidate_rows.jsonl', rows)
    write_jsonl(output / 'audit.jsonl', audit)
    write_jsonl(output / 'robustness_requests.jsonl', probes)
    counts = {qid: dict(Counter(c['annotation']['candidate_labels'][qid] for c in cases)) for qid in OPTIONS}
    index = ['# 姑苏阀冷事件窗口候选集', '',
             '**状态：待业务复核，当前不满足训练启动条件。** 目标是先核对时序、语义及标签，不是把规则候选冒充现场金标。', '',
             f"{len(cases)}个窗口，{len(registry)}条去重后的范围内事件；覆盖{len({c['group_id'] for c in cases})}个事件日。窗口及前置上下文可重叠，不能视作完全独立案例。", '',
             '仅覆盖姑苏极2低端VSC1的P221VCC1/P221VCC2，两个主机、A/B通道都保留。窗外其他设备未纳入；完整性只针对本地导出的所选范围，不证明采集无缺失。', '',
             '## 逐窗复核', '', '| 窗口 | 建议集合 | 窗内记录/前置上下文 | 窗内判断 | 末端判断 | 已见异常信号复归 |',
             '| --- | --- | --- | --- | --- | --- |']
    for c in cases:
        a = c['annotation']['candidate_labels']
        index.append(f"| [{c['id']}](review/{c['id']}.md) | {c['proposed_split']} | {len(c['event_ids'])}/{len(c['preceding_event_ids'])} | {LABEL_NAMES[a['during_window']]} | {LABEL_NAMES[a['at_window_end']]} | {LABEL_NAMES[a['observed_recovery']]} |")
    index += ['', '## 候选标签分布', '', '```json', json.dumps(counts, ensure_ascii=False, indent=2), '```', '',
              'verified_expected目前没有可靠候选：缺少与低端事件直接对应的完整操作/运行确认。不得用“没有故障词”生成正常标签。', '',
              '## 分组和复现', '',
              '按实际事件日期分组，整日的两个主机、A/B通道、相邻窗口及选项变体均同组。train/calibration/test只是冻结的建议划分；规则跨集合重用，不宣称未见规则泛化。', '',
              '用户2026-09-11案例留在原独立评测目录，本数据集不读取它的日志或标签。', '',
              '`candidate_rows.jsonl`符合Unsloth字段格式，其中gold仅为候选；没有生成可直接接入现有训练入口的train/calibration/test文件。', '',
              '模型输入采用可逆的字典编码：设备和原文消息只写一次，逐条事件保留相对时间、设备/消息索引和原始等级；原始记录仍在events.jsonl和逐窗Markdown中。编码逐字段反解核对，没有删除事件或生成摘要。', '',
              '```sh', 'python -m experiments.decision_model.valve_windows build --output /新的空目录',
              'python -m experiments.decision_model.valve_windows verify --check-sources', '```', '']
    (output / 'README.md').write_text('\n'.join(index), encoding='utf-8')
    manifest = {'dataset': catalog['dataset'], 'status': 'candidate_review', 'training_ready': False,
                'source_scope': catalog['scope'], 'review_status': 'not_expert_approved',
                'window_count': len(cases), 'event_count': len(selected),
                'event_day_groups': len({c['group_id'] for c in cases}),
                'candidate_label_counts': counts, 'export_files': sources,
                'identical_duplicate_records_merged': duplicates,
                'selection': catalog, 'no_truncation': True,
                'blockers': ['缺少verified_expected正常/符合预期候选', '现场及试验背景尚未逐窗核验',
                             '缺少初始/末端全量状态、历史软件逻辑/点表和屏蔽计时规则',
                             '需要实际token长度核对，长窗口不得静默截断'],
                'code_sha256': {p.name: digest(p) for p in [Path(__file__), CATALOG, SOURCES]},
                'files': {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob('*')) if p.is_file()}}
    write_json(output / 'manifest.json', manifest)
    report = verify(output, check_sources=True)
    write_json(output / 'validation.json', report)
    return report


def verify(output=DEFAULT_OUTPUT, check_sources=False):
    output = Path(output)
    manifest = json.loads((output / 'manifest.json').read_text())
    for name, sha in manifest['files'].items():
        if digest(output / name) != sha:
            raise ValueError(f'Snapshot hash mismatch: {name}')
    registry = {e['id']: e for e in read_jsonl(output / 'events.jsonl')}
    windows = read_jsonl(output / 'windows.jsonl')
    rows = {r['id']: r for r in read_jsonl(output / 'candidate_rows.jsonl')}
    audit = read_jsonl(output / 'audit.jsonl')
    groups, event_splits, window_by_id = {}, {}, {}
    for c in windows:
        if c['id'] in window_by_id:
            raise ValueError('Duplicate window')
        window_by_id[c['id']] = c
        if c['group_id'] != 'gusu-commissioning-' + c['start'][:10]:
            raise ValueError('Group ID is not the actual event date')
        split = c['proposed_split']
        if groups.setdefault(c['group_id'], split) != split:
            raise ValueError('Event-day group crosses splits')
        start, end = datetime.fromisoformat(c['start']), datetime.fromisoformat(c['end'])
        for key in c['event_ids'] + c['preceding_event_ids']:
            if event_splits.setdefault(key, split) != split:
                raise ValueError('Event/context leakage across splits')
            if registry[key]['host'] not in HOSTS or registry[key]['event_time'].startswith('2026-09-11'):
                raise ValueError('Out-of-scope/held-out event')
        if any(not start <= time_of(registry[k]) < end for k in c['event_ids']):
            raise ValueError('Window boundary violation')
        if any(not start-timedelta(seconds=300) <= time_of(registry[k]) < start for k in c['preceding_event_ids']):
            raise ValueError('Future information or invalid preceding context')
        expected = annotate([registry[k] for k in c['event_ids']], [registry[k] for k in c['preceding_event_ids']])
        if c['annotation'] != expected:
            raise ValueError('Candidate annotations differ from preserved evidence')
    if set(rows) != {a['id'] for a in audit}:
        raise ValueError('Row/audit ID mismatch')
    for a in audit:
        row = validate_row(rows[a['id']])
        case = window_by_id[a['window_id']]
        if (a['candidate_labels'] != case['annotation']['candidate_labels']
                or a['proposed_split'] != case['proposed_split'] or a['group_id'] != case['group_id']):
            raise ValueError('Audit/window mismatch')
        for qid, gold in row['gold'].items():
            if a['option_mapping'][qid][gold['label']] != a['candidate_labels'][qid]:
                raise ValueError('Wrong option/gold mapping')
        if any(term in row['state'] for term in ['candidate_labels', '候选标签', 'last_fan_to_clear_s']):
            raise ValueError('Annotation leakage into model input')
        rotation = int(row['id'].rsplit('-', 1)[1])
        regenerated, mapping = model_row(case, registry, json.loads((output / 'rules.json').read_text()), rotation)
        if rows[a['id']] != regenerated or mapping != a['option_mapping']:
            raise ValueError('Model row lost events, contains future evidence, or changed schema')
    for probe in read_jsonl(output / 'robustness_requests.jsonl'):
        case = window_by_id[probe['window_id']]
        if case['proposed_split'] != 'test':
            raise ValueError('Robustness probe not in held-out group')
        regenerated, mapping = model_row(case, registry, json.loads((output / 'rules.json').read_text()), int(probe['id'].rsplit('-', 1)[1]))
        regenerated.pop('gold')
        if probe != {**regenerated, 'label_map': mapping, 'window_id': case['id']}:
            raise ValueError('Robustness probe differs from frozen evidence')
    if check_sources:
        originals = {}
        for source in manifest['export_files']:
            path = ROOT / source['path']
            if digest(path) != source['sha256']:
                raise ValueError('Changed raw export')
            originals[source['path']] = path.read_text(encoding=source['encoding']).splitlines()
        for event in registry.values():
            for ref in event['provenance']:
                raw = originals[ref['source']][ref['line']-1]
                if raw != ref['raw'] or parse_line(raw, ref['source'], ref['line'])['id'] != event['id']:
                    raise ValueError('Source line mismatch')
        # Re-read the entire source scope: a valid subset can still omit a
        # critical fault. Verify all records were kept, not just cited ones.
        all_events, _, _ = load_events()
        for case in windows:
            start, end = datetime.fromisoformat(case['start']), datetime.fromisoformat(case['end'])
            expected = [e['id'] for e in all_events if start <= time_of(e) < end]
            previous = [e['id'] for e in all_events if start-timedelta(seconds=300) <= time_of(e) < start]
            if expected != case['event_ids'] or previous != case['preceding_event_ids']:
                raise ValueError('Incomplete scoped window or preceding context')
        for doc in json.loads((output / 'documents.json').read_text()):
            if digest(ROOT / doc['source']) != doc['sha256']:
                raise ValueError('Changed supporting document')
    return {'status': 'passed', 'windows': len(windows), 'unique_events': len(registry),
            'event_day_groups': len(groups), 'candidate_decisions': sum(len(a['candidate_labels']) for a in audit),
            'source_lines_and_scope_completeness_checked': check_sources,
            'training_ready': False, 'expert_gold_available': False,
            'label_counts': manifest['candidate_label_counts']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'verify'])
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--check-sources', action='store_true')
    args = parser.parse_args()
    result = build(args.output) if args.command == 'build' else verify(args.output, args.check_sources)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
