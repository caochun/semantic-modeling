"""Temporal and evidence-integrity checks for the new candidate window builder."""
from datetime import datetime
import json
import shutil

import pytest

from experiments.decision_model.data import digest, read_jsonl, write_json, write_jsonl
from experiments.decision_model.valve_windows import (
    DEFAULT_OUTPUT, annotate, compact_events, expand_events, mask_intervals, parse_line, verify,
)


def event(content, second=0, channel='A', level='正常'):
    # Deliberately use normal event severity even for a fault signal.
    return parse_line(f'2022-06-06 11:00:{second:02d}.000   P221VCC2  {channel}  {level}  极2低端阀1许继阀冷{channel}2  {content}', 'fixture.txt', second+1)


def test_run_mask_transitions_are_not_faults_or_proof_of_normality():
    events = [event('E4.M01风机运行消失'),
              event('泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽出现', 1),
              event('泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽消失', 2)]
    result = annotate(events, [])['candidate_labels']
    assert result == {'during_window': 'insufficient_evidence', 'at_window_end': 'insufficient_evidence',
                      'observed_recovery': 'not_observed'}


def test_redundant_channels_do_not_clear_each_others_faults():
    result = annotate([event('E4.M03风机变频器故障出现'), event('E4.M03风机变频器故障出现', 1, 'B'),
                       event('E4.M03风机变频器故障消失', 2)], [])
    assert result['candidate_labels']['during_window'] == 'abnormal_evidence'
    assert result['candidate_labels']['at_window_end'] == 'abnormal_evidence'
    assert result['candidate_labels']['observed_recovery'] == 'some_unresolved'


def test_fault_cleared_does_not_mean_whole_system_healthy():
    result = annotate([event('E4.M03风机变频器故障出现'), event('E4.M03风机变频器故障消失', 2)], [])
    assert result['candidate_labels'] == {'during_window': 'abnormal_evidence', 'at_window_end': 'insufficient_evidence',
                                          'observed_recovery': 'all_observed_cleared'}
    assert not result['physical_fault_confirmed']


def test_clear_only_does_not_invent_an_assertion():
    result = annotate([event('E4.M03风机变频器故障消失', 2)], [])
    assert result['candidate_labels']['observed_recovery'] == 'not_observed'
    assert not result['assertion_evidence']


def test_pre_window_closed_fault_is_not_new_occurrence():
    result = annotate([event('E4.M01风机运行出现', 3)],
                      [event('E4.M03风机变频器故障出现'), event('E4.M03风机变频器故障消失', 2)])
    assert result['candidate_labels']['during_window'] == 'insufficient_evidence'
    result = annotate([event('E4.M03风机变频器故障消失', 3)], [event('E4.M03风机变频器故障出现')])
    assert result['candidate_labels']['during_window'] == 'abnormal_evidence'
    assert result['candidate_labels']['observed_recovery'] == 'all_observed_cleared'


def test_same_time_contradiction_is_not_resolved_by_arbitrary_sort_order():
    events = [event('E4.M03风机变频器故障出现'), event('E4.M03风机变频器故障消失')]
    for order in [events, events[::-1]]:
        result = annotate(order, [])
        assert result['candidate_labels']['observed_recovery'] == 'indeterminate'
        assert result['candidate_labels']['at_window_end'] == 'insufficient_evidence'


def test_compaction_is_lossless_and_preserves_negative_context_time():
    events = [event('E4.M01风机运行出现'), event('E4.M01风机运行消失', 3, 'B')]
    start = datetime.fromisoformat('2022-06-06T11:00:02')
    bundle = compact_events(events, start)
    assert bundle['records'][0][0] == -2000
    for original, restored in zip(events, expand_events(bundle, start)):
        assert all(original[k] == v for k, v in restored.items())


def test_mask_pairing_tracks_channels_and_censoring():
    records = [event('泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽消失'),
               event('泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽出现', 1, 'B'),
               event('E4.M03风机运行消失', 2, 'B'),
               event('泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽消失', 5, 'B')]
    result = mask_intervals(records)
    assert len(result['left_censored_clear_events']) == 1
    assert result['complete_intervals'][0]['duration_s'] == 4
    assert result['complete_intervals'][0]['last_fan_to_clear_s'] == 3


def test_snapshot_and_reserved_user_case():
    report = verify()
    assert report['windows'] == 24 and report['candidate_decisions'] == 72
    assert not report['training_ready']
    assert all(not e['event_time'].startswith('2026-09-11') for e in read_jsonl(DEFAULT_OUTPUT / 'events.jsonl'))


def test_missing_model_evidence_detected_even_with_updated_file_hash(tmp_path):
    folder = tmp_path / 'snapshot'
    shutil.copytree(DEFAULT_OUTPUT, folder)
    rows = read_jsonl(folder / 'candidate_rows.jsonl')
    state = json.loads(rows[0]['state'])
    state['records'].pop()
    rows[0]['state'] = json.dumps(state, ensure_ascii=False)
    write_jsonl(folder / 'candidate_rows.jsonl', rows)
    manifest = json.loads((folder / 'manifest.json').read_text())
    manifest['files']['candidate_rows.jsonl'] = digest(folder / 'candidate_rows.jsonl')
    write_json(folder / 'manifest.json', manifest)
    with pytest.raises(ValueError, match='Model row lost events'):
        verify(folder)


def test_false_signal_family_soft_plate_is_not_a_trip():
    candidate = event('进阀温度超高跳闸软压板退出出现')
    assert annotate([candidate], [])['candidate_labels']['during_window'] == 'insufficient_evidence'
