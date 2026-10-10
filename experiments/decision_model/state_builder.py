"""Compile recognized log phenomena into a task-level natural-language state.

The state builder is the boundary between raw monitoring records and a typed
Decision Model request.  It consumes the evidence-first phenomenon recognizer
and the procedure checking layer, but never inserts procedure clause IDs,
answer labels, or operational commands into the model state.  The complete
recognition result remains in the audit object for review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .phenomenon import read_rows
from .procedure_reasoning import DEFAULT_RULES, analyze_with_procedure
from .procedure_state_dataset import QUESTION_SCHEMA


STATE_BUILDER_VERSION = "gusu-task-state-builder.v1"


def _unique(values: Iterable[str]) -> list[str]:
    return list(
        dict.fromkeys(
            value.strip().rstrip("。；; ")
            for value in values
            if value and value.strip().rstrip("。；; ")
        )
    )


def _fact_sentence(fact: Mapping[str, Any]) -> str:
    status = str(fact.get("status", "unknown"))
    description = str(fact.get("description", fact.get("id", "未知现象"))).strip().rstrip("。；; ")
    if status == "supported":
        return description
    if status == "suspected":
        return f"日志提示{description}"
    if status == "partial":
        return f"日志部分支持{description}"
    if status == "insufficient_evidence":
        return f"当前证据不足以确认{description}"
    return f"当前状态为{description}"


def render_task_state(structured_state: Mapping[str, Any]) -> str:
    """Render only semantic observations and evidence gaps for model input."""
    window = structured_state["window"]
    facts = structured_state["observed_facts"]
    insufficient = structured_state["insufficient_facts"]
    missing = structured_state["missing_evidence"]
    uncertainty = structured_state["uncertainty"]
    fact_text = "；".join(facts) if facts else "没有归纳出可直接支持的现象"
    if insufficient:
        fact_text += "；" + "；".join(insufficient)
    missing_text = "、".join(missing) if missing else "暂无明确缺失项"
    uncertainty_text = "；".join(uncertainty) if uncertainty else "暂无额外不确定性"
    disposition = structured_state["procedure_disposition"]
    disposition_text = {
        "not_ready_for_disposition": "尚未具备进入规程处置分支的完整证据",
        "ready_for_manual_procedure_review": "证据可提交运行人员进行规程审核",
        "no_applicable_procedure_rule": "当前未触发已配置的规程核查分支",
    }.get(disposition, "规程核查状态需要人工确认")
    return (
        f"在姑苏站 {window['start']} 至 {window['end']} 的监视窗口内，"
        f"状态构建器从 {window['event_count']} 条原始事件归纳出：{fact_text}。"
        f"当前{disposition_text}。仍缺少或尚未确认的证据包括：{missing_text}。"
        f"需要注意：{uncertainty_text}。"
        "以上内容描述日志语义和证据边界，不等同于现场物理故障确认，也不表示未出现的信号已经正常。"
    )


def build_task_state(
    rows: Iterable[Mapping[str, Any]],
    *,
    rules_path: Path = DEFAULT_RULES,
) -> dict[str, Any]:
    """Return a model request plus a complete, reviewable audit payload."""
    rows = list(rows)
    result = analyze_with_procedure(rows, rules_path)
    phenomena = [fact for fact in result["phenomena"] if isinstance(fact, Mapping)]
    observed = _unique(
        _fact_sentence(fact)
        for fact in phenomena
        if fact.get("status") in {"supported", "suspected", "partial"}
    )
    insufficient = _unique(
        _fact_sentence(fact)
        for fact in phenomena
        if fact.get("status") == "insufficient_evidence"
    )
    missing = _unique(
        missing
        for fact in phenomena
        for missing in fact.get("missing", [])
    )
    checks = result["procedure_reasoning"].get("checks", [])
    missing.extend(
        check.get("missing_evidence", "")
        for check in checks
        if check.get("status") == "missing"
    )
    missing = _unique(missing)
    uncertainty = _unique(
        str(fact.get("details", {}).get("interpretation", ""))
        for fact in phenomena
    )
    structured = {
        "schema_version": "task-semantic-state.v1",
        "state_builder_version": STATE_BUILDER_VERSION,
        "window": result["state"]["window"],
        "observed_facts": observed,
        "insufficient_facts": insufficient,
        "missing_evidence": missing,
        "uncertainty": uncertainty,
        "procedure_disposition": result["procedure_reasoning"]["summary"]["disposition"],
        "physical_fault_confirmed": False,
        "source_event_ids": [event["id"] for event in result["events"]],
    }
    request = {
        "id": "gusu-log-window-" + hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16],
        "state": render_task_state(structured),
        "questions": json.dumps(QUESTION_SCHEMA, ensure_ascii=False, sort_keys=True),
    }
    audit = {
        "id": request["id"],
        "source_type": "real-log-window-candidate",
        "gold_status": "not_available_pending_operator_review",
        "state_builder_version": STATE_BUILDER_VERSION,
        "structured_state": structured,
        "recognition": result,
        "raw_rows": rows,
    }
    return {"request": request, "structured_state": structured, "audit": audit}


def write_candidate_snapshot(
    source_path: Path,
    destination: Path,
    *,
    rules_path: Path = DEFAULT_RULES,
) -> dict[str, Any]:
    """Write a request/audit snapshot that is explicitly not training data."""
    source_path = Path(source_path)
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"snapshot output must be new or empty: {destination}")
    result = build_task_state(read_rows(source_path), rules_path=rules_path)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "request.json").write_text(
        json.dumps(result["request"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (destination / "audit.json").write_text(
        json.dumps(result["audit"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "dataset": "gusu-procedure-state-candidates-v1",
        "purpose": "real_log_state_builder_candidate",
        "review_status": "operator_gold_pending",
        "training_ready": False,
        "gold_status": "not_available_pending_operator_review",
        "state_builder_version": STATE_BUILDER_VERSION,
        "source": str(source_path),
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "request_id": result["request"]["id"],
        "files": {
            "request.json": hashlib.sha256((destination / "request.json").read_bytes()).hexdigest(),
            "audit.json": hashlib.sha256((destination / "audit.json").read_bytes()).hexdigest(),
        },
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="将姑苏日志编译为任务级自然语言 state")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        help="写入 request.json、audit.json 和 manifest.json 的候选快照目录",
    )
    args = parser.parse_args()
    if bool(args.output) == bool(args.snapshot_dir):
        parser.error("必须且只能指定 --output 或 --snapshot-dir")
    if args.snapshot_dir:
        manifest = write_candidate_snapshot(args.input, args.snapshot_dir, rules_path=args.rules)
        print(json.dumps({"status": "passed", **manifest}, ensure_ascii=False, indent=2))
        return
    result = build_task_state(read_rows(args.input), rules_path=args.rules)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"status": "passed", "id": result["request"]["id"], "output": str(args.output)},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
