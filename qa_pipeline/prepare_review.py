"""Prepare a diverse, answer-blind roundtrip sample; does not call an API."""
import argparse
import hashlib
import json
from pathlib import Path

from .paths import ROOT, FOCUSED, TEMPLATE
DEFAULT = ROOT / '.semantic/qa_operations_20261008'
FOCUSED_KEYS = [
    'switch_abnormal', 'single_protection_alarm', 'black_start',
    'transformer_reenergize', 'control_a_unavailable_b', 'dispatch_communication',
    'slow_filter_boundary', 'cooling_leak', 'pump_isolation_power',
    'valve_water_stop_order', 'pf_overlap', 'restart_scope',
    'vbe_ip_diagnosis', 'history_unexplained_exit', 'report_relay_not_failed',
    'protection_restore_ticket',
]
SYSTEM = ('你是独立的文档问答复答器。只依据给定来源回答问题，'
          '保留适用对象、版本、条件分支、必要前提、禁止事项、顺序与恢复确认。'
          '不能补造阈值、根因、接线或未记载的验证结果；来源有歧义时说明歧义。'
          '引用证据标识，直接回答问题，不讨论评分。')


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=DEFAULT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--revision-sample', action='store_true')
    args = parser.parse_args()
    directory = args.dataset.resolve()
    rows = [json.loads(l) for l in (directory / 'qa.jsonl').read_text().splitlines()]
    contexts = {c['id']: c for c in json.loads((directory / 'contexts.json').read_text())}
    authored = [r for r in rows if r['generation'] == 'codex_source_grounded_composition']
    specifications = json.loads(FOCUSED.read_text())
    if len(authored) != len(specifications):
        raise ValueError('Authored rows and specifications differ')
    by_key = {spec['key']: r for spec, r in zip(specifications, authored)}
    keys = (['switch_abnormal', 'single_protection_alarm', 'black_start',
             'slow_filter_boundary', 'dc_filter_mode_restriction',
             'dc_filter_switch_order', 'ticket_blank_power'] if args.revision_sample else FOCUSED_KEYS)
    selected = [by_key[k] for k in keys]
    families = (['operation_sequence'] if args.revision_sample else
                ['incident_full_chain', 'operation_sequence', 'protection_action_mapping', 'operating_constraints'])
    for family in families:
        candidates = [r for r in rows if r['family'] == family]
        # Moderate-length complete units fit this sample's output budget.
        candidates.sort(key=lambda r: (abs(len(r['answer']) - 1500), r['id']))
        selected.append(candidates[0])
    if len({r['id'] for r in selected}) != len(selected):
        raise ValueError('Duplicate sample question')
    prompts, audit = [], []
    for r in selected:
        context = contexts[r['context_id']]['text']
        prompts.append({'id': r['id'], 'context_id': r['context_id'],
                        'messages': [{'role': 'system', 'content': SYSTEM},
                                     {'role': 'user', 'content': '文档证据及关联上下文：\n\n' + context + '\n\n问题：' + r['question']}]})
        audit.append({'id': r['id'], 'family': r['family'], 'question': r['question'],
                      'question_sha256': sha(r['question']), 'answer_sha256': sha(r['answer']),
                      'context_sha256': sha(context), 'context_id': r['context_id'],
                      'selection_reason': 'purposive_diverse_sample_not_random_accuracy_estimate'})
    out = args.output.resolve() if args.output else directory / 'roundtrip'
    out.mkdir(parents=True, exist_ok=True)
    text = ''.join(json.dumps(p, ensure_ascii=False) + '\n' for p in prompts)
    frozen = out / 'prompts.jsonl'
    if (out / 'run/run.json').exists() and frozen.read_text() != text:
        raise ValueError('A model run already uses these prompts; choose a new --output directory')
    frozen.write_text(text)
    (out / 'sample.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'sample': len(selected), 'candidate_answers_in_prompts': False,
                      'families': sorted({r['family'] for r in selected}),
                      'max_input_characters': max(sum(len(m['content']) for m in p['messages']) for p in prompts)},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
