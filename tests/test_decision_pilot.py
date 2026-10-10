"""Dataset corruption/leakage checks run without Torch, Unsloth or source PDFs."""
import copy
import json
import shutil

import pytest

from experiments.decision_model.data import (
    DEFAULT_DATA, decode_row, digest, read_jsonl, validate_row, verify, write_json, write_jsonl,
)
from experiments.decision_model.train import canonical_rows
from experiments.decision_model.runtime_check import compare_predictions


def copy_snapshot(tmp_path):
    folder = tmp_path / 'data'
    shutil.copytree(DEFAULT_DATA, folder)
    return folder


def refresh_hash(folder, name):
    manifest = json.loads((folder / 'manifest.json').read_text())
    manifest['files'][name] = digest(folder / name)
    write_json(folder / 'manifest.json', manifest)


def test_published_snapshot_and_independent_evaluation_units():
    result = verify()
    assert result['status'] == 'passed'
    for split in ('calibration', 'test'):
        rows = read_jsonl(DEFAULT_DATA / f'{split}.jsonl')
        assert len(canonical_rows(rows)) == result['counts'][split]['scenarios']
    # Every decision's known/unknown scenarios and representations stay together.
    audit = read_jsonl(DEFAULT_DATA / 'audit.jsonl')
    for family in {r['family_id'] for r in audit}:
        assert len({r['split'] for r in audit if r['family_id'] == family}) == 1


def test_hash_detects_changed_training_data(tmp_path):
    folder = copy_snapshot(tmp_path)
    with (folder / 'train.jsonl').open('a') as stream:
        stream.write('\n')
    with pytest.raises(ValueError, match='hash mismatch'):
        verify(folder)


def test_family_leak_is_detected_even_when_file_hash_is_updated(tmp_path):
    folder = copy_snapshot(tmp_path)
    audit = read_jsonl(folder / 'audit.jsonl')
    training_family = next(r['family_id'] for r in audit if r['split'] == 'train')
    next(r for r in audit if r['split'] == 'test')['family_id'] = training_family
    write_jsonl(folder / 'audit.jsonl', audit)
    refresh_hash(folder, 'audit.jsonl')
    with pytest.raises(ValueError, match='Cross-split leakage'):
        verify(folder)


def test_wrong_option_mapping_is_rejected(tmp_path):
    folder = copy_snapshot(tmp_path)
    rows = read_jsonl(folder / 'train.jsonl')
    row = decode_row(rows[0])
    gold = row['gold']['next_check']['label']
    row['gold']['next_check']['label'] = next(k for k in row['questions']['next_check']['criteria'] if k != gold)
    rows[0] = row
    write_jsonl(folder / 'train.jsonl', rows)
    refresh_hash(folder, 'train.jsonl')
    with pytest.raises(ValueError, match='mapping/gold mismatch'):
        verify(folder)


@pytest.mark.parametrize('mutation', ['missing_gold', 'invalid_label', 'audit_in_input'])
def test_invalid_model_rows_fail(mutation):
    row = copy.deepcopy(decode_row(read_jsonl(DEFAULT_DATA / 'train.jsonl')[0]))
    if mutation == 'missing_gold':
        del row['gold']['blocking']
    elif mutation == 'invalid_label':
        row['gold']['blocking']['label'] = 'probably'
    else:
        row['rationale'] = 'Answer copied from audit'
    with pytest.raises(ValueError):
        validate_row(row)


def test_table_labels_do_not_collapse_unknown_into_no():
    rows = [decode_row(r) for r in read_jsonl(DEFAULT_DATA / 'test.jsonl')]
    known = next(r for r in rows if r['id'] == 'pcs974-table-03/identified/narrative')
    unknown = next(r for r in rows if r['id'] == 'pcs974-table-03/unknown-device/narrative')
    assert known['gold']['blocking']['label'] == 'blocks_device'
    assert unknown['gold']['blocking']['label'] == 'insufficient_evidence'


def test_input_does_not_include_annotation_or_rule_family_id():
    for split in ('train', 'calibration', 'test'):
        for raw in read_jsonl(DEFAULT_DATA / f'{split}.jsonl'):
            row = validate_row(raw)
            assert raw['id'].split('/')[0] not in row['state']
            assert '候选标签' not in row['state']
            assert isinstance(raw['questions'], str) and isinstance(raw['gold'], str)


def test_reload_check_detects_small_probability_drift_and_wrong_labels():
    before = [{'blocking': {'answer': 'blocks_device', 'probabilities': {'blocks_device': 0.7, 'no': 0.3}}}]
    after = copy.deepcopy(before)
    after[0]['blocking']['probabilities'].update({'blocks_device': 0.701, 'no': 0.299})
    report = compare_predictions(before, after, 0.02)
    assert report['passed'] and report['max_absolute_probability_delta'] == pytest.approx(0.001)
    assert not compare_predictions(before, after, 0.0001)['passed']
    after[0]['blocking']['answer'] = 'no'
    with pytest.raises(ValueError, match='prediction differs'):
        compare_predictions(before, after, 0.02)


def test_reload_check_rejects_nonfinite_probability():
    with pytest.raises(ValueError, match='Nonfinite'):
        compare_predictions({'confidence': 0.6}, {'confidence': float('nan')}, 0.02)
