"""Build a branch-balanced paraphrase set for the explicit procedure states.

Version 1 split canonical cases, which left several test labels completely
unseen during training.  This version keeps the same manually specified
procedure gold but renders each canonical case in five controlled natural
language forms.  Variants of every branch are present in train, calibration,
and test, so the test measures state wording robustness rather than label
emergence.  It is still synthetic and is not an operator-approved dataset.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .data import ROOT, digest, dumps, read_jsonl, write_json, write_jsonl
from .explicit_state_dataset import (
    CASES,
    QUESTION_SCHEMAS,
    SOURCE_MD,
    SCHEMA_VERSION as BASE_SCHEMA_VERSION,
    _events,
    _validate,
)

DATASET_NAME = "gusu-explicit-state-v2"
SCHEMA_VERSION = "gusu-explicit-state-decision.v2"
STATE_BUILDER_VERSION = "gusu-explicit-state-renderer.v2"
DEFAULT_OUTPUT = ROOT / "data/decision" / DATASET_NAME
SPLITS = ("train", "calibration", "test")
VARIANT_SPLITS = {0: "train", 1: "train", 2: "calibration", 3: "test", 4: "test"}


def _joined(values: tuple[str, ...], empty: str) -> str:
    return "；".join(values) if values else empty


def _render_state(case: Any, variant: int) -> str:
    title = QUESTION_SCHEMAS[case.family]["title"]
    observations = _joined(case.observations, "无")
    verification = _joined(case.verification, "尚未完成")
    missing = "、".join(case.missing) if case.missing else "无明确缺失项"
    uncertainty = _joined(case.uncertainty, "暂无额外不确定性")
    # The content is identical; only the natural-language realization changes.
    forms = (
        f"任务场景：{title}。监视窗口内观察到：{observations}。已完成核查：{verification}。"
        f"缺失证据：{missing}。不确定性：{uncertainty}。",
        f"本次需要判断{title}。当前记录的现象是{observations}；核查已经得到：{verification}。"
        f"尚未取得的证据为{missing}。目前不确定性：{uncertainty}。",
        f"围绕“{title}”进行监盘：观察到{observations}。现场或后台核查结果为{verification}。"
        f"待补充证据：{missing}；保留的不确定性：{uncertainty}。",
        f"场景={title}；观察={observations}；核查={verification}；缺失={missing}；"
        f"不确定性={uncertainty}。",
        f"在{title}任务中，已有事实包括{observations}，已核对内容包括{verification}。"
        f"证据缺口是{missing}，因此当前不确定性为{uncertainty}。",
    )
    return forms[variant] + "以上 State 仅表达已记录的观察和核查结果，供规程分支判断，不等同于自动执行现场操作。"


def _variant_events(case: Any, variant: int) -> list[dict[str, Any]]:
    events = _events(case)
    for event in events:
        event["id"] = f"{event['id']}-v{variant}"
        event["variant"] = variant
    return events


def _row(case: Any, variant: int) -> tuple[dict[str, Any], dict[str, Any]]:
    events = _variant_events(case, variant)
    row = {
        "id": f"{case.family}-{case.name}-v{variant}",
        "state": _render_state(case, variant),
        "questions": dumps({"decision": {
            "type": "choice",
            "instructions": QUESTION_SCHEMAS[case.family]["instructions"],
            "criteria": QUESTION_SCHEMAS[case.family]["criteria"],
        }}),
        "gold": dumps({"decision": {"label": case.label}}),
    }
    _validate(row)
    audit = {
        "id": row["id"],
        "split": VARIANT_SPLITS[variant],
        "family_id": case.family,
        "scenario_id": f"{case.family}/{case.name}",
        "variant": variant,
        "source_type": "procedure-derived-synthetic-paraphrase",
        "gold_origin": "manually specified procedure branch",
        "review_status": "source_checked_not_domain_expert_approved",
        "procedure_title": QUESTION_SCHEMAS[case.family]["title"],
        "procedure_clause_ids": list(case.clause_ids),
        "observations": list(case.observations),
        "verification": list(case.verification),
        "missing_evidence": list(case.missing),
        "uncertainty": list(case.uncertainty),
        "raw_events": events,
        "structured_state": {
            "schema_version": SCHEMA_VERSION,
            "state_builder_version": STATE_BUILDER_VERSION,
            "procedure_family": case.family,
            "observations": list(case.observations),
            "verification": list(case.verification),
            "missing_evidence": list(case.missing),
            "uncertainty": list(case.uncertainty),
            "source_event_ids": [e["id"] for e in events],
            "decision_label": case.label,
        },
        "expected_semantic_answer": case.label,
        "state_input": "natural_language_only",
    }
    return row, audit


def build(destination: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"output must be new or empty: {destination}")
    if not SOURCE_MD.exists():
        raise FileNotFoundError(SOURCE_MD)
    rows: dict[str, list[dict[str, Any]]] = {split: [] for split in SPLITS}
    audit: list[dict[str, Any]] = []
    for case in CASES:
        for variant, split in VARIANT_SPLITS.items():
            row, meta = _row(case, variant)
            rows[split].append(row)
            audit.append(meta)
    destination.mkdir(parents=True, exist_ok=True)
    for split in SPLITS:
        write_jsonl(destination / f"{split}.jsonl", rows[split])
    write_jsonl(destination / "audit.jsonl", audit)
    write_json(destination / "schema.json", {
        "schema_version": SCHEMA_VERSION,
        "base_schema_version": BASE_SCHEMA_VERSION,
        "state_input": "natural_language_only",
        "variants_per_case": len(VARIANT_SPLITS),
        "split_policy": "variants 0-1 train, 2 calibration, 3-4 test",
        "questions": QUESTION_SCHEMAS,
        "state_builder_version": STATE_BUILDER_VERSION,
    })
    write_json(destination / "source.json", {
        "source_path": str(SOURCE_MD.relative_to(ROOT)),
        "source_sha256": digest(SOURCE_MD),
        "selected_clause_families": sorted(QUESTION_SCHEMAS),
        "base_case_count": len(CASES),
    })
    (destination / "README.md").write_text(
        "# 姑苏站明确分支 State 数据集 v2\n\n"
        "本版本保留 v1 的 28 个规程派生 canonical cases，每个 case 生成 5 种受控自然语言 State 表达，"
        "共 140 条。每个分支的变体都进入 train、calibration、test，因此测试集不包含训练中完全未出现的标签。\n\n"
        "这是用于验证 Decision Model 训练链路和 State 表达鲁棒性的合成数据，未经运行人员或领域专家审核，"
        "不能作为现场自动操作依据。原始合成事件、条款定位和 gold 来源保存在 `audit.jsonl`。\n",
        encoding="utf-8",
    )
    manifest = {
        "dataset": DATASET_NAME,
        "schema_version": SCHEMA_VERSION,
        "purpose": "branch_balanced_procedure_state_paraphrase_seed",
        "review_status": "source_checked_synthetic_not_expert_approved",
        "state_input": "natural_language_semantic_state_only",
        "audit_excluded_from_model_input": True,
        "split_unit": "controlled_paraphrase_variant",
        "files": {},
        "source_sha256": digest(SOURCE_MD),
        "family_count": len(QUESTION_SCHEMAS),
        "base_case_count": len(CASES),
        "variant_count": len(CASES) * len(VARIANT_SPLITS),
    }
    for path in sorted(destination.iterdir()):
        if path.name != "manifest.json":
            manifest["files"][path.name] = digest(path)
    write_json(destination / "manifest.json", manifest)
    result = verify(destination)
    write_json(destination / "validation.json", result)
    manifest["files"]["validation.json"] = digest(destination / "validation.json")
    write_json(destination / "manifest.json", manifest)
    return result


def verify(destination: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    destination = Path(destination)
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        if digest(destination / name) != expected:
            raise ValueError(f"snapshot hash mismatch: {name}")
    audit = {row["id"]: row for row in read_jsonl(destination / "audit.jsonl")}
    ids: set[str] = set()
    counts: dict[str, Any] = {}
    labels: dict[str, set[str]] = {}
    states: dict[str, str] = {}
    for split in SPLITS:
        rows = read_jsonl(destination / f"{split}.jsonl")
        if not rows:
            raise ValueError(f"empty split: {split}")
        split_labels: set[str] = set()
        for raw in rows:
            row = dict(raw)
            _validate(row)
            if row["id"] in ids or row["id"] not in audit:
                raise ValueError(f"audit/model mismatch: {row['id']}")
            ids.add(row["id"])
            if audit[row["id"]]["split"] != split:
                raise ValueError(f"split mismatch: {row['id']}")
            if row["state"] in states and states[row["state"]] != split:
                raise ValueError(f"state leakage: {row['id']}")
            states[row["state"]] = split
            label = json.loads(row["gold"])["decision"]["label"]
            split_labels.add(label)
        counts[split] = {"rows": len(rows), "decisions": len(rows)}
        labels[split] = split_labels
    if ids != set(audit):
        raise ValueError("audit rows do not exactly cover model rows")
    if labels["train"] != labels["calibration"] or labels["train"] != labels["test"]:
        raise ValueError("every split must contain every branch label")
    return {
        "status": "passed",
        "counts": counts,
        "labels": {split: sorted(values) for split, values in labels.items()},
        "total_rows": len(ids),
        "family_count": len(QUESTION_SCHEMAS),
        "base_case_count": len(CASES),
        "limitations": [
            "Controlled paraphrases of synthetic procedure-derived states; no expert approval or field fault-rate claim.",
            "The state renderer is deterministic and does not test raw-log recognition.",
            "A branch label represents the procedure text, not permission to execute an operation.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="构建平衡分支的规程五 State 数据集 v2")
    parser.add_argument("command", choices=("build", "verify"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.output) if args.command == "build" else verify(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
