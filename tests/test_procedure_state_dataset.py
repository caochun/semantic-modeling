import json

from experiments.decision_model.procedure_state_dataset import (
    CASES,
    QUESTION_SCHEMA,
    build,
    verify,
)


def test_seed_case_matrix_covers_four_procedure_scenarios():
    assert {case.scenario for case in CASES} == {"10.3", "10.4", "10.5", "11.3"}
    assert {case.split for case in CASES} == {"train", "calibration", "test"}
    assert len(CASES) == 16


def test_build_outputs_state_only_model_rows_and_separate_audit(tmp_path):
    output = tmp_path / "dataset"
    result = build(output)

    assert result["status"] == "passed"
    assert result["total_rows"] == 16
    assert result["decisions_per_row"] == len(QUESTION_SCHEMA)
    assert set(json.loads((output / "train.jsonl").read_text().splitlines()[0])) == {
        "id", "state", "questions", "gold"
    }
    audit = json.loads((output / "audit.jsonl").read_text().splitlines()[0])
    assert audit["source_type"] == "procedure-derived-synthetic"
    assert audit["state_builder_version"] == "gusu-state-builder.v1"
    assert audit["raw_events"]
    assert audit["structured_state"]["source_event_ids"]


def test_state_does_not_expose_clause_ids_or_gold_labels(tmp_path):
    output = tmp_path / "dataset"
    build(output)
    rows = [json.loads(line) for line in (output / "test.jsonl").read_text().splitlines()]
    for row in rows:
        assert "10.3" not in row["state"]
        assert "10.4" not in row["state"]
        assert "10.5" not in row["state"]
        assert "11.3" not in row["state"]
        assert "confirmed" not in row["state"]
        assert "candidate" not in row["state"]


def test_verify_rejects_tampered_snapshot(tmp_path):
    output = tmp_path / "dataset"
    build(output)
    path = output / "test.jsonl"
    path.write_text(path.read_text().replace("姑苏站", "测试站", 1), encoding="utf-8")
    try:
        verify(output)
    except ValueError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("tampered snapshot unexpectedly verified")
