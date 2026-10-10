"""Regression checks for causal window boundaries and frozen pilot evidence."""
from datetime import datetime
import json
import shutil

import pytest

from experiments.decision_model.data import digest, read_jsonl, write_json, write_jsonl
from experiments.decision_model.valve_windows import parse_line
from experiments.decision_model.valve_windows_v2 import (
    DEFAULT_OUTPUT, MASK, annotate, carry_ids, ledger, verify,
)


def event(message, second=0, channel='A'):
    return parse_line(f'2022-06-06 11:00:{second:02d}.000   P221VCC2  {channel}  正常  极2低端阀1许继阀冷{channel}2  {message}',
                      'fixture.txt', second+1)


def carried(events):
    chosen = set(carry_ids(events))
    return [e for e in events if e['id'] in chosen]


def test_cut_inside_fault_preserves_assertion_and_recovery():
    first = event('E4.M03风机变频器故障出现')
    last = event('E4.M03风机变频器故障消失', 30)
    result = annotate([last], carried([first]))['candidate_labels']
    assert result == {'during_window': 'abnormal_evidence', 'at_window_end': 'insufficient_evidence',
                      'observed_recovery': 'all_observed_cleared'}


def test_closed_history_is_not_new_occurrence_and_is_not_normal():
    history = [event('E4.M03风机变频器故障出现'), event('E4.M03风机变频器故障消失', 5)]
    assert carry_ids(history) == []
    result = annotate([event(MASK+'消失', 10)], carried(history))['candidate_labels']
    assert result['during_window'] == 'insufficient_evidence'
    assert result['observed_recovery'] == 'not_observed'


def test_mask_original_onset_survives_repeated_assertion_and_fan_changes():
    history = [event(MASK+'出现'), event('E4.M01风机运行消失', 2), event(MASK+'出现', 5),
               event('E4.M01风机运行出现', 6)]
    keep = carry_ids(history)
    assert history[0]['id'] in keep and history[2]['id'] in keep
    assert history[3]['id'] in keep and history[1]['id'] not in keep
    state = next(s for s in ledger(carried(history)).values() if s['category'] == 'mask')
    assert state['onset_id'] == history[0]['id']


def test_ambiguous_carry_survives_boundary_until_later_observation():
    conflicting = [event('E4.M03风机变频器故障出现'), event('E4.M03风机变频器故障消失')]
    for order in (conflicting, conflicting[::-1]):
        assert len(carry_ids(order)) == 2
        assert annotate([], carried(order))['candidate_labels']['observed_recovery'] == 'indeterminate'
        assert annotate([event('E4.M03风机变频器故障消失', 2)], carried(order))['candidate_labels']['observed_recovery'] == 'all_observed_cleared'


def test_channels_and_clear_only_stay_independent():
    history = [event('E4.M03风机变频器故障出现')]
    result = annotate([event('E4.M03风机变频器故障消失', 1, 'B')], carried(history))['candidate_labels']
    assert result['observed_recovery'] == 'all_unresolved'
    assert annotate([event('E4.M03风机变频器故障消失')], [])['candidate_labels']['observed_recovery'] == 'not_observed'


def test_online_test_mode_is_context_not_normal_or_confirmed_injection():
    history = [event('有设备处于在线测试模式出现'), event('系统调试模式出现', 2)]
    assert len(carry_ids(history)) == 2
    result = annotate([], carried(history))
    assert result['candidate_labels']['during_window'] == 'insufficient_evidence'
    assert not result['physical_fault_confirmed']


def test_ledger_after_each_cut_matches_full_history():
    records = [event(MASK+'出现'), event('E4.M03风机变频器故障出现', 1),
               event('E4.M03风机变频器故障出现', 2, 'B'), event('E4.M01风机运行消失', 3),
               event(MASK+'出现', 4), event('E4.M03风机变频器故障消失', 5),
               event(MASK+'消失', 6), event('E4.M01风机运行出现', 7)]
    for cut in range(1, len(records)):
        whole = ledger(records)
        split = ledger(carried(records[:cut]) + records[cut:])
        for key, expected in whole.items():
            if expected['category'] == 'alarm' and expected['edge'] == '消失' and key not in split:
                continue
            # Onset of alarms is not an input feature; last observations are.
            for field in ('edge', 'last_ids', 'last_time'):
                assert split[key][field] == expected[field]
            if expected['category'] == 'mask':
                assert split[key]['onset_id'] == expected['onset_id']


@pytest.mark.skipif(not DEFAULT_OUTPUT.exists(), reason='Frozen v2 not built')
def test_v2_frozen_scope_lengths_and_date_separation():
    result = verify(DEFAULT_OUTPUT, check_sources=True)
    assert result['parents'] == 24
    assert result['normal_examples'] == 0 and result['max_tokens'] <= 4096
    assert result['evidence_pilot_ready'] and not result['full_health_classifier_ready']
    for c in read_jsonl(DEFAULT_OUTPUT / 'windows.jsonl'):
        assert c['start'] < c['end']


@pytest.mark.skipif(not DEFAULT_OUTPUT.exists(), reason='Frozen v2 not built')
def test_forged_future_carry_detected_even_with_rehashed_manifest(tmp_path):
    target = tmp_path / 'bad'
    shutil.copytree(DEFAULT_OUTPUT, target)
    cases = read_jsonl(target / 'windows.jsonl')
    cases[0]['carry_ids'].append(cases[0]['event_ids'][-1])
    write_jsonl(target / 'windows.jsonl', cases)
    manifest = json.loads((target / 'manifest.json').read_text())
    manifest['files']['windows.jsonl'] = digest(target / 'windows.jsonl')
    write_json(target / 'manifest.json', manifest)
    with pytest.raises(ValueError, match='causal carry'):
        verify(target)


@pytest.mark.skipif(not DEFAULT_OUTPUT.exists(), reason='Frozen v2 not built')
def test_training_augmentation_and_counterfactuals_do_not_leak_test_labels():
    from experiments.decision_model.train_valve import expanded_training, evaluation_plan
    from experiments.decision_model.data import decode_row
    rows = expanded_training(DEFAULT_OUTPUT)
    assert len(rows) == 125
    assert all('20220608' not in r['id'] and '20220612' not in r['id'] for r in rows)
    assert all('verified_expected' not in r['questions'] for r in rows)
    plan = evaluation_plan(DEFAULT_OUTPUT)
    assert all('gold' not in p for p in plan)
    altered = [p for p in plan if p['kind'] == 'counterfactual_missing_observations']
    assert len(altered) == 4
    assert all(p['removed_event_ids'] and p['expected']['observed_recovery'] == 'not_observed' for p in altered)
    assert all(p['expected']['during_window'] == 'insufficient_evidence' for p in altered)


@pytest.mark.skipif(not DEFAULT_OUTPUT.exists(), reason='Frozen v2 not built')
def test_metrics_count_windows_and_detect_position_dependence():
    from experiments.decision_model.train_valve import evaluation_plan, metrics
    plan = evaluation_plan(DEFAULT_OUTPUT)
    predictions = [{k: p[k] for k in ('id', 'window_id', 'kind', 'expected')} |
                   {'semantic_choices': dict(p['expected'])} for p in plan]
    report = metrics(predictions, read_jsonl(DEFAULT_OUTPUT / 'audit.jsonl'))
    assert report['test_windows'] == 36 and report['test_parent_windows'] == 7
    assert all(v['correct_across_all_positions'] == 36 for v in report['tasks'].values())
    expected = predictions[0]['expected']['during_window']
    predictions[0]['semantic_choices']['during_window'] = ('insufficient_evidence' if expected == 'abnormal_evidence' else 'abnormal_evidence')
    report = metrics(predictions, read_jsonl(DEFAULT_OUTPUT / 'audit.jsonl'))
    assert report['tasks']['during_window']['stable_across_all_positions'] == 35
