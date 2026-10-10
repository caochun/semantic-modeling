import json

from experiments.decision_model.typed_contract_check import check


def test_procedure_typed_dataset_matches_pinned_upstream_semantics():
    result = check(tokenizer=False)

    assert result["status"] == "passed"
    assert result["upstream_revision"] == "cc9128134156cc51f94eeb76d3d19f80bddbc8a7"
    assert result["semantic_contract"]["train"]["decisions"] == 72
    assert result["semantic_contract"]["train"]["question_types"] == {
        "noul": 40,
        "choice": 24,
        "score": 8,
    }
    assert result["tokenizer_contract"]["status"] == "not_requested"


def test_contract_checker_accepts_json_string_questions_and_gold(tmp_path):
    # Keep this test focused on the checker boundary: its inputs are the same
    # JSON-string fields that the model dataset stores on disk.
    source = tmp_path / "dataset"
    from experiments.decision_model.procedure_state_dataset import build

    build(source)
    result = check(source, tokenizer=False)
    assert result["dataset_validation"]["total_rows"] == 16
    first = json.loads((source / "train.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert isinstance(first["questions"], str)
    assert isinstance(first["gold"], str)
