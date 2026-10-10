"""Build a source-linked inventory of the complete Gusu Procedure Five.

This is an inventory and extraction layer, not an automatic authorization to
execute every sentence in the procedure.  It preserves the original clause
number, heading role, source pages and text so each later executable rule can
be reviewed against the Markdown conversion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "output/markdown/姑苏站/规程五：±800kV姑苏换流站运行规程（设备异常与事故处理）.md"
DEFAULT_RULES = Path(__file__).with_name("procedure_rules_v1.json")
DEFAULT_OUTPUT = ROOT / "data/decision/gusu-procedure-five-inventory-v1/procedure_inventory.json"

CLAUSE_RE = re.compile(
    r"^<!-- clause=(?P<clause>[^;]+); pdf_pages=(?P<pdf>[^;]+); printed_pages=(?P<printed>[^ ]+) -->$"
)
HEADING_RE = re.compile(r"^(?P<marks>#{2,6})\s+(?P<title>.+?)\s*$")
BOLD_CLAUSE_RE = re.compile(r"^\*\*(?P<clause>\d+(?:\.\d+)+)\*\*\s*(?P<text>.*)$")
ANCHOR_RE = re.compile(r"^<a id=\"clause-[^\"]+\"></a>$")
ROLE_TERMS = (
    ("phenomenon", ("现象",)),
    ("treatment", ("处理", "处置", "事故处理")),
    ("principle", ("原则", "规定")),
)


def _parts(clause_id: str) -> tuple[int, ...]:
    return tuple(int(part) for part in clause_id.split("."))


def _parent(clause_id: str) -> str | None:
    parts = clause_id.split(".")
    return ".".join(parts[:-1]) or None


def _page_value(value: str) -> int | list[int]:
    pages = [int(page) for page in value.split(",")]
    return pages[0] if len(pages) == 1 else pages


def _role(title: str) -> str:
    for role, terms in ROLE_TERMS:
        if any(term in title for term in terms):
            return role
    return "topic"


def _marker_role(label: str) -> str:
    """Classify conversion lines that act as headings but use bold syntax.

    A few source pages encode section labels such as ``处理原则`` as a bold
    numbered line instead of a Markdown heading.  Keep their original record
    kind, while still allowing them to anchor the section hierarchy.
    """
    normalized = label.strip()
    if normalized == "现象":
        return "phenomenon"
    if re.fullmatch(r"(?:处理原则|处理(?:[（(].*)?)", normalized):
        return "treatment"
    if normalized.startswith(("原则", "规定")):
        return "principle"
    return "topic"


def _clean_body(lines: list[str]) -> str:
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or ANCHOR_RE.fullmatch(stripped):
            continue
        # A clause boundary always starts a new record.  Other conversion
        # comments are metadata and should not become operational text.
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def _clause_refs(text: str) -> list[str]:
    """Extract references such as 10.3 or 11.1/11.3 from a rule citation."""
    return re.findall(r"(?<!\d)(\d+(?:\.\d+)+)(?!\d)", text)


def parse_procedure(
    path: Path = DEFAULT_SOURCE,
    rules_path: Path = DEFAULT_RULES,
) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    comments: list[tuple[int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        match = CLAUSE_RE.fullmatch(line.strip())
        if match:
            comments.append((index, match))

    records: list[dict[str, Any]] = []
    for position, (comment_line, match) in enumerate(comments):
        end_line = comments[position + 1][0] if position + 1 < len(comments) else len(lines)
        clause_id = match.group("clause")
        chunk = lines[comment_line + 1 : end_line]
        first_index = next(
            (i for i, value in enumerate(chunk) if value.strip() and not ANCHOR_RE.fullmatch(value.strip())),
            None,
        )
        if first_index is None:
            first_line = ""
            body_start = len(chunk)
            kind = "unknown"
            title = clause_id
            body = ""
        else:
            first_line = chunk[first_index].strip()
            heading = HEADING_RE.fullmatch(first_line)
            bold = BOLD_CLAUSE_RE.fullmatch(first_line)
            section_marker = False
            if heading:
                kind = "heading"
                title = heading.group("title").strip()
                body_start = first_index + 1
                body = _clean_body(chunk[body_start:])
            elif bold:
                kind = "clause"
                title = clause_id
                label = bold.group("text").strip()
                section_marker = _marker_role(label) != "topic"
                body_start = first_index
                body = _clean_body(chunk[body_start:])
            else:
                kind = "text"
                title = clause_id
                label = ""
                body_start = first_index
                body = _clean_body(chunk[body_start:])

        if first_index is None or heading:
            label = title if first_index is not None and heading else ""
            section_marker = False
        record_role = _marker_role(label) if label else _role(title)
        if record_role == "topic":
            record_role = _role(title)

        depth = len(_parts(clause_id))
        if kind == "heading" and depth == 1:
            record_kind = "chapter"
        elif kind == "heading" and depth == 2:
            record_kind = "topic"
        else:
            record_kind = kind
        records.append(
            {
                "id": f"reg5:{clause_id}",
                "clause_id": clause_id,
                "parent_clause_id": _parent(clause_id),
                "depth": depth,
                # ``level`` is kept as a descriptive alias for consumers that
                # model the inventory as a tree.  ``depth`` remains the
                # canonical field used by this parser.
                "level": depth,
                "kind": record_kind,
                "title": title,
                "text": body,
                "role": record_role,
                "label": label,
                "section_marker": section_marker,
                "source": {
                    "markdown_line": comment_line + 1,
                    "pdf_pages": _page_value(match.group("pdf")),
                    "printed_pages": _page_value(match.group("printed")),
                },
            }
        )

    by_clause = {record["clause_id"]: record for record in records}
    for record in records:
        parts = _parts(record["clause_id"])
        if len(parts) >= 2:
            scenario_clause_id = ".".join(str(part) for part in parts[:2])
            scenario = by_clause.get(scenario_clause_id)
            record["scenario_clause_id"] = scenario_clause_id
            record["scenario_title"] = scenario["title"] if scenario else None
            record["scenario_source"] = scenario["source"] if scenario else None
        else:
            record["scenario_clause_id"] = None
            record["scenario_title"] = None
            record["scenario_source"] = None

    headings = [
        record
        for record in records
        if record["kind"] in {"chapter", "topic", "heading"}
        or record.get("section_marker", False)
    ]
    # Derive the section from the complete heading set rather than carrying a
    # mutable stack across the document.  This handles sibling sections,
    # chapter changes and occasional numbering irregularities deterministically.
    for record in records:
        record_parts = _parts(record["clause_id"])
        ancestors = [
            heading
            for heading in headings
            if len(_parts(heading["clause_id"])) <= len(record_parts)
            and record_parts[: len(_parts(heading["clause_id"]))]
            == _parts(heading["clause_id"])
        ]
        if ancestors:
            nearest = max(ancestors, key=lambda item: len(_parts(item["clause_id"])))
            record["section_clause_id"] = nearest["clause_id"]
            record["section_title"] = nearest["title"]
            record["section_role"] = nearest["role"]
        else:
            record["section_clause_id"] = None
            record["section_title"] = None
            record["section_role"] = None

    chapters = [record for record in records if record["kind"] == "chapter"]
    topics = [record for record in records if record["kind"] == "topic"]
    section_records = [
        record
        for record in records
        if record["kind"] == "heading" or record.get("section_marker", False)
    ]
    phenomenon_sections = [record for record in section_records if record["role"] == "phenomenon"]
    treatment_sections = [record for record in section_records if record["role"] == "treatment"]
    rules: list[dict[str, Any]] = []
    if rules_path.exists():
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
    coverage = []
    for rule in rules:
        citations = [ref for ref in rule.get("procedure_refs", []) if "规程五" in ref]
        source_clause_ids = sorted({ref for citation in citations for ref in _clause_refs(citation) if ref in by_clause}, key=lambda x: _parts(x))
        coverage.append(
            {
                "rule_id": rule["id"],
                "source_clause_ids": source_clause_ids,
                "source_locations": [by_clause[ref]["source"] for ref in source_clause_ids],
                "coverage_status": "linked" if source_clause_ids else "unlinked",
            }
        )

    declared = re.search(r"结构：(?P<chapters>\d+) 章、(?P<topics>[\d,]+) 个原目录项、(?P<units>[\d,]+) 个编号单元", "\n".join(lines[:20]))
    return {
        "schema_version": "procedure-five-inventory.v1",
        "source": {
            "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "version": "6.0",
            "pdf_pages": 96,
            "printed_pages": "1-86",
        },
        "declared_counts": {
            "chapters": int(declared.group("chapters")) if declared else None,
            "directory_items": int(declared.group("topics").replace(",", "")) if declared else None,
            "numbered_units": int(declared.group("units").replace(",", "")) if declared else None,
        },
        "counts": {
            "records": len(records),
            "chapters": len(chapters),
            "topics": len(topics),
            "phenomenon_sections": len(phenomenon_sections),
            "treatment_sections": len(treatment_sections),
            "records_with_text": sum(bool(record["text"]) for record in records),
        },
        "chapters": [
            {
                "clause_id": chapter["clause_id"],
                "title": chapter["title"],
                "source": chapter["source"],
                "record_count": sum(record["clause_id"].startswith(chapter["clause_id"] + ".") for record in records),
            }
            for chapter in chapters
        ],
        "coverage": coverage,
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="建立规程五完整条款和源文定位清单")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = parse_procedure(args.source, args.rules)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "counts": result["counts"], "coverage": result["coverage"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
