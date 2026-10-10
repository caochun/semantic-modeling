import json

from experiments.decision_model.explicit_state_dataset import QUESTION_SCHEMAS
from experiments.decision_model.explicit_state_dataset_v2 import CASES, build, verify


def test_v2_has_balanced_labels_and_variant_splits(tmp_path):
    output = tmp_path / "dataset"
    result = build(output)
    assert result["status"] == "passed"
    assert result["total_rows"] == len(CASES) * 5
    split_rows = {
        split: [json.loads(line) for line in (output / f"{split}.jsonl").read_text().splitlines()]
        for split in ("train", "calibration", "test")
    }
    assert {json.loads(row["gold"])["decision"]["label"] for rows in split_rows.values() for row in rows}
    labels = [{json.loads(row["gold"])["decision"]["label"] for row in rows} for rows in split_rows.values()]
    assert labels[0] == labels[1] == labels[2]
    assert len(split_rows["train"]) == len(CASES) * 2
    assert len(split_rows["calibration"]) == len(CASES)
    assert len(split_rows["test"]) == len(CASES) * 2
    assert len(QUESTION_SCHEMAS) == result["family_count"]


def test_v2_snapshot_verifies_without_sources(tmp_path):
    output = tmp_path / "dataset"
    build(output)
    assert verify(output)["status"] == "passed"
