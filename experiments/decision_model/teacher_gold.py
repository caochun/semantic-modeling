"""Build soft gold from three explicit self-teacher judgments.

The Typed Decisions README defines gold as the mean of three independent
teacher samples.  This module applies the same aggregation to a reviewed state
request when no operator labels are available.  The judgments are written out
alongside their short rationales so the result is inspectable; this is teacher
consensus, not an operator or field-validity label.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


TEACHER_METHOD = "manual_self_teacher_consensus"
TEACHER_ID = "current-assistant-gpt6"


def _load(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _mode(options: list[str], labels: list[str]) -> str:
    counts = {option: labels.count(option) for option in options}
    return max(options, key=lambda option: (counts[option], -options.index(option)))


def aggregate_samples(
    questions: Mapping[str, Mapping[str, Any]],
    samples: list[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Aggregate three hard teacher judgments into typed probability gold."""
    if len(samples) != 3:
        raise ValueError("README protocol requires exactly three teacher samples")
    result: dict[str, dict[str, Any]] = {}
    for question_id, question in questions.items():
        kind = question["type"]
        if kind == "noul":
            options = ["true", "false"]
        elif kind == "choice":
            options = sorted(str(option) for option in question["criteria"])
        elif kind == "score":
            options = [str(index) for index in range(len(question["criteria"]))]
        else:
            raise ValueError(f"unsupported question type: {kind}")
        labels = []
        for sample in samples:
            answers = sample.get("answers", {})
            label = str(answers.get(question_id, ""))
            if kind == "noul":
                label = label.lower()
            if label not in options:
                raise ValueError(f"{sample.get('sample_id')}/{question_id}: invalid label {label!r}")
            labels.append(label)
        probabilities = {
            option: round(labels.count(option) / len(labels), 6) for option in options
        }
        label = _mode(options, labels)
        if kind == "noul":
            result[question_id] = {
                "type": kind,
                "label": label,
                "probabilities": probabilities,
                "probability_true": probabilities["true"],
            }
        elif kind == "score":
            result[question_id] = {
                "type": kind,
                "label": label,
                "score": round(sum(float(answer) * probabilities[answer] for answer in options), 6),
                "probabilities": probabilities,
            }
        else:
            result[question_id] = {
                "type": kind,
                "label": label,
                "probabilities": probabilities,
            }
    return result


def build_labeled_row(
    request: Mapping[str, Any],
    samples: list[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    questions = _load(request["questions"])
    gold = aggregate_samples(questions, samples)
    row = {
        "id": request["id"],
        "state": request["state"],
        "questions": request["questions"],
        "gold": json.dumps(gold, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    }
    audit = {
        "id": request["id"],
        "gold_origin": TEACHER_METHOD,
        "teacher_id": TEACHER_ID,
        "sample_count": len(samples),
        "samples": samples,
        "aggregated_gold": gold,
        "limitations": [
            "教师判断由当前助手手工独立生成，不是运行人员审核。",
            "三次判断共享同一模型和同一状态，不能估计现场故障概率。",
            "该分布表示教师在当前证据下的判断不确定性。",
        ],
    }
    return row, audit


def label_snapshot(snapshot: Path, samples_path: Path | None = None) -> dict[str, Any]:
    snapshot = Path(snapshot)
    request = json.loads((snapshot / "request.json").read_text(encoding="utf-8"))
    if samples_path is None:
        samples_path = snapshot / "teacher_samples.json"
    samples = json.loads(Path(samples_path).read_text(encoding="utf-8"))
    if isinstance(samples, dict):
        samples = samples["samples"]
    row, audit = build_labeled_row(request, samples)
    (snapshot / "teacher_gold.json").write_text(
        json.dumps(audit["aggregated_gold"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (snapshot / "labeled_row.json").write_text(
        json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (snapshot / "teacher_gold_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({
        "gold_available": True,
        "gold_status": TEACHER_METHOD,
        "teacher_id": TEACHER_ID,
        "review_status": "teacher_consensus_only",
        "training_ready": False,
        "training_readiness_reason": "single real-log candidate; teacher gold only; operator review unavailable",
    })
    for name in ("teacher_samples.json", "teacher_gold.json", "labeled_row.json", "teacher_gold_audit.json"):
        manifest.setdefault("files", {})[name] = hashlib.sha256((snapshot / name).read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "passed", "id": request["id"], "gold": audit["aggregated_gold"], "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description="按 Typed Decisions 三次教师判断聚合 soft gold")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--samples", type=Path)
    args = parser.parse_args()
    result = label_snapshot(args.snapshot, args.samples)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
