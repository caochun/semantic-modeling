import json
import shutil
from pathlib import Path

from experiments.decision_model.teacher_gold import aggregate_samples, label_snapshot


def test_three_self_teacher_samples_are_aggregated_as_typed_distributions(tmp_path):
    questions = {
        "yes": {"type": "noul"},
        "choice": {"type": "choice", "criteria": {"a": "A", "b": "B"}},
        "score": {"type": "score", "criteria": ["low", "high"]},
    }
    samples = [
        {"sample_id": "1", "answers": {"yes": "true", "choice": "a", "score": "1"}},
        {"sample_id": "2", "answers": {"yes": "true", "choice": "b", "score": "1"}},
        {"sample_id": "3", "answers": {"yes": "false", "choice": "a", "score": "0"}},
    ]
    gold = aggregate_samples(questions, samples)
    assert gold["yes"]["probabilities"] == {"true": 0.666667, "false": 0.333333}
    assert gold["choice"]["label"] == "a"
    assert gold["choice"]["probabilities"] == {"a": 0.666667, "b": 0.333333}
    assert gold["score"]["score"] == 0.666667


def test_snapshot_is_marked_teacher_only_and_keeps_three_samples():
    source = Path("data/decision/gusu-procedure-state-candidates-v1")
    snapshot = Path("/tmp/gusu-teacher-gold-test")
    if snapshot.exists():
        shutil.rmtree(snapshot)
    shutil.copytree(source, snapshot)
    result = label_snapshot(snapshot)
    assert result["manifest"]["gold_status"] == "manual_self_teacher_consensus"
    assert result["manifest"]["review_status"] == "teacher_consensus_only"
    assert result["manifest"]["training_ready"] is False
    audit = json.loads(
        open(snapshot / "teacher_gold_audit.json", encoding="utf-8").read()
    )
    assert audit["sample_count"] == 3
