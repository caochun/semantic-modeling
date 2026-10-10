import json
from pathlib import Path

from experiments.decision_model.explicit_state_dataset import (
    CASES,
    QUESTION_SCHEMAS,
    build,
    verify,
)


def test_explicit_dataset_builds_auditable_branch_cases(tmp_path):
    output = tmp_path / "dataset"
    result = build(output)
    assert result["status"] == "passed"
    assert result["total_rows"] == len(CASES)
    assert result["family_count"] == len(QUESTION_SCHEMAS)

    audit = {
        row["id"]: row
        for row in (json.loads(line) for line in (output / "audit.jsonl").read_text().splitlines())
    }
    for split in ("train", "calibration", "test"):
        rows = [json.loads(line) for line in (output / f"{split}.jsonl").read_text().splitlines()]
        assert rows
        for row in rows:
            assert "clause" not in row["state"]
            assert row["id"] in audit
            assert audit[row["id"]]["procedure_clause_ids"]


def test_explicit_dataset_snapshot_verifies_without_sources(tmp_path):
    output = tmp_path / "dataset"
    build(output)
    assert verify(output)["status"] == "passed"
