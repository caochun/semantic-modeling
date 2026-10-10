import json
from pathlib import Path

from experiments.decision_model.phenomenon import read_rows
from experiments.decision_model.state_builder import (
    STATE_BUILDER_VERSION,
    build_task_state,
    write_candidate_snapshot,
)


def test_real_log_window_becomes_state_without_gold_or_clause_leakage():
    rows = read_rows(
        Path("experiments/decision_model/cases/gusu-valve-cooling-20260911/raw.tsv")
    )
    result = build_task_state(rows)
    request = result["request"]
    structured = result["structured_state"]

    assert STATE_BUILDER_VERSION in result["audit"]["state_builder_version"]
    assert len(structured["source_event_ids"]) == 20
    assert structured["procedure_disposition"] == "not_ready_for_disposition"
    assert "风机运行反馈" in request["state"]
    assert "泄漏保护" in request["state"]
    assert "日志支持日志" not in request["state"]
    assert "confirmed" not in request["state"]
    assert "candidate" not in request["state"]
    assert "10.3" not in request["state"]
    assert "10.4" not in request["state"]
    assert "10.5" not in request["state"]
    assert "11.3" not in request["state"]

    questions = json.loads(request["questions"])
    assert set(questions) == {
        "scenario_10_3_applicable",
        "scenario_10_4_applicable",
        "scenario_10_5_applicable",
        "scenario_11_3_applicable",
        "physical_fault",
        "evidence_sufficiency",
        "needs_human_review",
        "next_check",
        "urgency",
    }
    assert "gold" not in request


def test_state_audit_keeps_raw_records_and_evidence_graph():
    rows = read_rows(
        Path("experiments/decision_model/cases/gusu-valve-cooling-20260911/raw.tsv")
    )
    result = build_task_state(rows)
    audit = result["audit"]

    assert audit["gold_status"] == "not_available_pending_operator_review"
    assert len(audit["raw_rows"]) == 20
    assert audit["recognition"]["evidence_graph"]["edges"]
    assert audit["recognition"]["procedure_reasoning"]["next_checks"]


def test_candidate_snapshot_separates_request_from_audit(tmp_path):
    source = Path("experiments/decision_model/cases/gusu-valve-cooling-20260911/raw.tsv")
    output = tmp_path / "candidate"
    manifest = write_candidate_snapshot(source, output)

    request = json.loads((output / "request.json").read_text(encoding="utf-8"))
    audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
    assert manifest["training_ready"] is False
    assert manifest["gold_status"] == "not_available_pending_operator_review"
    assert set(request) == {"id", "state", "questions"}
    assert audit["raw_rows"]
    assert "gold" not in request
