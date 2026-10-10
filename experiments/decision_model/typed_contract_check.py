"""Validate the Procedure Five typed-decisions dataset against Unsloth's contract.

The repository has an older ``contract_check`` for the choice-only alarm pilot.
This module keeps that checker unchanged and handles the newer dataset, which
contains ``noul``, ``choice`` and ``score`` questions.  The pure part runs
without PyTorch or Transformers; when a tokenizer is available it additionally
executes the pinned upstream Clef encoder and ``build_dataset`` implementation.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .contract_check import ROOT, upstream_data_api
from .data import read_jsonl, write_json
from .procedure_state_dataset import DEFAULT_OUTPUT, SPLITS, verify as verify_procedure_dataset


def _parsed(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in {"{", "["}:
            return json.loads(value)
    return value


def _rows(data: Path) -> dict[str, list[dict[str, Any]]]:
    return {
        split: [_parsed(row) for row in read_jsonl(data / f"{split}.jsonl")]
        for split in SPLITS
    }


def _check_upstream_semantics(data: Path, common: Any, clef: Any) -> dict[str, Any]:
    """Check question normalization and gold-to-option mapping without a tokenizer."""
    reports: dict[str, Any] = {}
    for split, rows in _rows(data).items():
        question_count = 0
        type_counts: dict[str, int] = {"noul": 0, "choice": 0, "score": 0}
        for row in rows:
            state = _parsed(row.get("state"))
            questions = _parsed(row.get("questions"))
            gold = _parsed(row.get("gold"))
            if state is None or not isinstance(questions, dict) or not isinstance(gold, dict):
                raise ValueError(f"{split}/{row.get('id')}: needs state, questions and gold")
            if set(questions) != set(gold):
                raise ValueError(f"{split}/{row.get('id')}: questions and gold differ")
            for question_id, question in questions.items():
                normalized = common._clef_question(question)
                kind = normalized["type"]
                keys = [key for key, _ in clef.question_options(normalized)]
                target, label_index = common._target_for(
                    kind, keys, _parsed(gold[question_id])
                )
                if len(target) != len(keys) or not target:
                    raise ValueError(f"{split}/{row['id']}/{question_id}: invalid target")
                if any(not math.isfinite(float(value)) for value in target):
                    raise ValueError(f"{split}/{row['id']}/{question_id}: non-finite target")
                if abs(sum(target) - 1.0) > 1e-6:
                    raise ValueError(f"{split}/{row['id']}/{question_id}: target is not normalized")
                expected_label = common._label(
                    kind, _parsed(gold[question_id]).get("label")
                )
                if expected_label not in keys:
                    raise ValueError(
                        f"{split}/{row['id']}/{question_id}: gold label is not an option"
                    )
                if keys[label_index] != expected_label:
                    raise ValueError(
                        f"{split}/{row['id']}/{question_id}: gold label does not match target"
                    )
                question_count += 1
                type_counts[kind] += 1
        reports[split] = {
            "rows": len(rows),
            "decisions": question_count,
            "question_types": type_counts,
        }
    return reports


def _check_tokenizer(
    data: Path,
    cache: Path,
    max_length: int,
    lock: dict[str, Any],
    api: Any,
    common: Any,
    clef: Any,
) -> dict[str, Any]:
    """Run the pinned upstream encoder when Transformers is installed."""
    try:
        from transformers import AutoTokenizer
    except Exception as exc:  # pragma: no cover - depends on optional environment
        return {
            "status": "skipped_missing_dependency",
            "reason": f"{type(exc).__name__}: {exc}",
            "required_for": "tokenizer length and upstream build_dataset execution",
        }

    tokenizer = AutoTokenizer.from_pretrained(
        lock["pilot_model"]["name"], revision=lock["pilot_model"]["revision"]
    )
    reports: dict[str, Any] = {}
    for split, rows in _rows(data).items():
        lengths: list[int] = []
        for row in rows:
            request = {
                "id": row["id"],
                "state": _parsed(row["state"]),
                "questions": _parsed(row["questions"]),
            }
            complete = clef.encode_record(tokenizer, request, max_length=1_000_000)
            if len(complete.input_ids) > max_length:
                raise ValueError(
                    f"{split}/{row['id']}: {len(complete.input_ids)} tokens exceed {max_length}"
                )
            limited = clef.encode_record(tokenizer, request, max_length=max_length)
            if complete.input_ids != limited.input_ids:
                raise ValueError(f"{split}/{row['id']}: tokenizer truncated complete state")
            lengths.append(len(complete.input_ids))

        config_only = SimpleNamespace(
            is_clef=True, decision_config={"max_len": max_length}
        )
        items, report = api.build_dataset(rows, tokenizer, config_only)
        expected_decisions = sum(
            len(_parsed(row["questions"])) for row in rows
        )
        if report["skipped"] or report["truncated"]:
            raise ValueError(f"{split}: upstream rejected or truncated records: {report}")
        if report["total"] != expected_decisions or len(items) != len(rows):
            raise ValueError(
                f"{split}: upstream counts changed: expected {len(rows)} rows / "
                f"{expected_decisions} decisions, report={report}, items={len(items)}"
            )
        for row, item in zip(rows, items):
            gold = _parsed(row["gold"])
            for question, target in zip(item["record"].questions, item["targets"]):
                option_index = target.index(max(target))
                predicted_key = question.option_ids[option_index]
                if predicted_key != gold[question.question_id]["label"]:
                    raise ValueError(
                        f"{split}/{row['id']}/{question.question_id}: option/gold mismatch"
                    )
        reports[split] = {
            **report,
            "rows": len(items),
            "min_tokens": min(lengths),
            "max_tokens": max(lengths),
            "mean_tokens": round(sum(lengths) / len(lengths), 1),
        }
    return {
        "status": "passed",
        "tokenizer": lock["pilot_model"],
        "max_length": max_length,
        "splits": reports,
    }


def check(
    data: Path = DEFAULT_OUTPUT,
    *,
    cache: Path | None = None,
    max_length: int = 4096,
    tokenizer: bool = True,
) -> dict[str, Any]:
    data = Path(data)
    base = verify_procedure_dataset(data)
    cache = cache or ROOT / ".semantic/decision-model-reference/contract"
    lock, api, common, clef = upstream_data_api(cache)
    semantic = _check_upstream_semantics(data, common, clef)
    result: dict[str, Any] = {
        "status": "passed",
        "dataset_validation": base,
        "upstream_revision": lock["revision"],
        "checks": [
            "Procedure Five dataset manifest and audit hashes",
            "upstream question type validation",
            "upstream gold probability normalization",
            "upstream option ordering and gold mapping",
        ],
        "semantic_contract": semantic,
        "model_training_tested": False,
        "scope": "CPU data contract only; no model weights, forward/backward or Trainer",
    }
    if tokenizer:
        tokenizer_report = _check_tokenizer(
            data, cache, max_length, lock, api, common, clef
        )
        result["tokenizer_contract"] = tokenizer_report
        if tokenizer_report["status"] == "passed":
            result["checks"].extend(
                [
                    "pinned tokenizer complete-state encoding",
                    "pinned upstream Clef build_dataset",
                    "no context truncation",
                ]
            )
    else:
        result["tokenizer_contract"] = {
            "status": "not_requested",
            "reason": "use --skip-tokenizer for dependency-free protocol checks",
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="检查规程五 Typed Decisions 数据契约")
    parser.add_argument("--data", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-length", type=int, default=4096)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--skip-tokenizer",
        action="store_true",
        help="仅执行规程数据和上游问题/gold协议检查，不加载tokenizer",
    )
    args = parser.parse_args()
    result = check(
        args.data,
        cache=args.cache,
        max_length=args.max_length,
        tokenizer=not args.skip_tokenizer,
    )
    if args.report:
        write_json(args.report, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
