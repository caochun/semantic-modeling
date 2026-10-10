"""Apply a small, auditable procedure-check layer to phenomenon recognition.

The procedure layer answers *what must be checked next* from the evidence
already present in a log window.  It deliberately stops before operational
commands or a claim that a physical fault has been confirmed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from .phenomenon import DEFAULT_INPUT, analyze_events, read_rows
except ImportError:
    # Allow the service deployment to place these three files in one directory.
    from phenomenon import DEFAULT_INPUT, analyze_events, read_rows


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULES = Path(__file__).with_name("procedure_rules_v1.json")
_PRIORITY = {"urgent": 0, "high": 1, "medium": 2, "low": 3}


def load_rules(path: Path = DEFAULT_RULES) -> list[dict[str, Any]]:
    """Load the versioned procedure rules without modifying the source text."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not value:
        raise ValueError(f"规程规则文件必须是非空数组: {path}")
    return value


def _fact_matches(fact_id: str, patterns: Iterable[str]) -> bool:
    return any(
        (pattern.endswith("*") and fact_id.startswith(pattern[:-1]))
        or pattern == fact_id
        for pattern in patterns
    )


def _path_value(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _rule_facts(rule: Mapping[str, Any], facts: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    patterns = rule.get("trigger_fact_ids", [])
    # An ``insufficient_evidence`` fact describes a possible procedure branch,
    # but its absence of evidence must not activate that branch by itself.
    matches = [
        fact
        for fact in facts
        if _fact_matches(str(fact.get("id", "")), patterns)
        and fact.get("status") in {"supported", "partial", "suspected"}
    ]
    conditions = rule.get("when", {})
    if not conditions:
        return matches
    return [
        fact
        for fact in matches
        if all(_path_value(fact, key) == expected for key, expected in conditions.items())
    ]


def _event_index(analysis: Mapping[str, Any]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for event in analysis.get("events", []):
        signal_id = event.get("signal_id")
        if signal_id:
            index.setdefault(str(signal_id), []).append(str(event["id"]))
    return index


def reason_from_analysis(
    analysis: Mapping[str, Any],
    rules: list[Mapping[str, Any]] | None = None,
    ruleset_name: str = DEFAULT_RULES.name,
) -> dict[str, Any]:
    """Return procedure checks and an extended evidence graph for one analysis."""
    rules = rules if rules is not None else load_rules()
    facts = [fact for fact in analysis.get("phenomena", []) if isinstance(fact, Mapping)]
    signal_events = _event_index(analysis)
    checks: dict[str, dict[str, Any]] = {}
    applied_rules: list[dict[str, Any]] = []

    for rule in rules:
        matching_facts = _rule_facts(rule, facts)
        if not matching_facts:
            continue
        rule_id = str(rule["id"])
        applied_rules.append(
            {
                "id": rule_id,
                "title": rule.get("title", rule_id),
                "trigger_fact_ids": [str(fact["id"]) for fact in matching_facts],
                "procedure_refs": list(rule.get("procedure_refs", [])),
            }
        )
        for check in rule.get("checks", []):
            check_id = str(check["id"])
            evidence_ids = [
                event_id
                for signal_id in check.get("evidence_signal_ids", [])
                for event_id in signal_events.get(str(signal_id), [])
            ]
            existing = checks.get(check_id)
            if existing is None:
                checks[check_id] = {
                    "id": check_id,
                    "title": check["title"],
                    "priority": check.get("priority", "medium"),
                    "status": "observed" if evidence_ids else "missing",
                    "evidence_signal_ids": list(check.get("evidence_signal_ids", [])),
                    "evidence_event_ids": list(dict.fromkeys(evidence_ids)),
                    "missing_evidence": check.get("missing_evidence", ""),
                    "trigger_fact_ids": [str(fact["id"]) for fact in matching_facts],
                    "procedure_rule_ids": [rule_id],
                    "procedure_refs": list(rule.get("procedure_refs", [])),
                }
            else:
                existing["evidence_event_ids"] = list(
                    dict.fromkeys(existing["evidence_event_ids"] + evidence_ids)
                )
                existing["status"] = "observed" if existing["evidence_event_ids"] else "missing"
                existing["trigger_fact_ids"] = list(
                    dict.fromkeys(
                        existing["trigger_fact_ids"]
                        + [str(fact["id"]) for fact in matching_facts]
                    )
                )
                existing["procedure_rule_ids"] = list(
                    dict.fromkeys(existing["procedure_rule_ids"] + [rule_id])
                )
                existing["procedure_refs"] = list(
                    dict.fromkeys(existing["procedure_refs"] + list(rule.get("procedure_refs", [])))
                )

    ordered_checks = sorted(
        checks.values(),
        key=lambda check: (_PRIORITY.get(check["priority"], 9), check["id"]),
    )
    missing = [check for check in ordered_checks if check["status"] == "missing"]
    observed = [check for check in ordered_checks if check["status"] == "observed"]

    base_graph = analysis.get("evidence_graph", {})
    nodes = [dict(node) for node in base_graph.get("nodes", [])]
    edges = [dict(edge) for edge in base_graph.get("edges", [])]
    node_ids = {node.get("id") for node in nodes}
    for check in ordered_checks:
        node_id = f"check:{check['id']}"
        if node_id not in node_ids:
            nodes.append({"id": node_id, "type": "procedure_check", "label": check["title"]})
            node_ids.add(node_id)
        for fact_id in check["trigger_fact_ids"]:
            edges.append({"source": fact_id, "target": node_id, "relation": "requires_check"})
        for event_id in check["evidence_event_ids"]:
            edges.append({"source": event_id, "target": node_id, "relation": "satisfies"})

    disposition = (
        "no_applicable_procedure_rule"
        if not ordered_checks
        else "not_ready_for_disposition"
        if missing
        else "ready_for_manual_procedure_review"
    )
    return {
        "schema_version": "procedure-reasoning.v1",
        "ruleset": ruleset_name,
        "rules_applied": applied_rules,
        "checks": ordered_checks,
        "next_checks": missing,
        "evidence_graph": {"nodes": nodes, "edges": edges},
        "summary": {
            "applied_rule_count": len(applied_rules),
            "check_count": len(ordered_checks),
            "missing_check_count": len(missing),
            "observed_check_count": len(observed),
            "disposition": disposition,
            "physical_fault_confirmed": False,
            "interpretation": "输出下一项核查建议，不是自动处置指令或现场故障确认。",
        },
    }


def analyze_with_procedure(
    rows: Iterable[Mapping[str, Any]],
    rules_path: Path = DEFAULT_RULES,
) -> dict[str, Any]:
    """Run signal recognition and procedure checks as one reviewable result."""
    analysis = analyze_events(rows)
    reasoning = reason_from_analysis(analysis, load_rules(rules_path), rules_path.name)
    return {**analysis, "procedure_reasoning": reasoning}


def main() -> None:
    parser = argparse.ArgumentParser(description="从阀冷日志识别现象并生成规程核查建议")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze_with_procedure(read_rows(args.input), args.rules)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
