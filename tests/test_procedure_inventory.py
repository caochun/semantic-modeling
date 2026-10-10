import json

from experiments.decision_model.procedure_inventory import (
    DEFAULT_SOURCE,
    parse_procedure,
)


def _inventory():
    return parse_procedure(DEFAULT_SOURCE)


def _record(inventory, clause_id):
    return next(record for record in inventory["records"] if record["clause_id"] == clause_id)


def test_inventory_covers_all_numbered_units_and_chapters():
    inventory = _inventory()

    assert inventory["counts"]["records"] == 1903
    assert inventory["counts"]["chapters"] == 15
    assert inventory["declared_counts"] == {
        "chapters": 15,
        "directory_items": 194,
        "numbered_units": 1903,
    }
    assert [chapter["clause_id"] for chapter in inventory["chapters"]] == [
        str(number) for number in range(1, 16)
    ]


def test_section_ancestry_stays_within_the_current_procedure_branch():
    inventory = _inventory()

    phenomenon = _record(inventory, "10.3.1.1")
    treatment = _record(inventory, "10.3.2.3")
    outer_cooling = _record(inventory, "11.3.2.4")
    outer_cooling_section = _record(inventory, "11.3.2")

    assert phenomenon["level"] == 4
    assert phenomenon["parent_clause_id"] == "10.3.1"
    assert phenomenon["scenario_clause_id"] == "10.3"
    assert phenomenon["scenario_title"] == "10.3 内水冷泄漏处理预案"
    assert phenomenon["section_clause_id"] == "10.3.1"
    assert phenomenon["section_role"] == "phenomenon"

    assert treatment["parent_clause_id"] == "10.3.2"
    assert treatment["section_clause_id"] == "10.3.2"
    assert treatment["section_role"] == "treatment"

    assert outer_cooling_section["section_marker"] is True
    assert outer_cooling_section["role"] == "treatment"
    assert outer_cooling["section_clause_id"] == "11.3.2"
    assert outer_cooling["section_role"] == "treatment"
    assert outer_cooling["scenario_clause_id"] == "11.3"


def test_source_text_and_page_locations_are_preserved():
    inventory = _inventory()

    leak = _record(inventory, "10.3.1.1")
    treatment = _record(inventory, "10.3.2.3")
    duplicate = _record(inventory, "10.3.2.7")

    assert "24 小时泄漏报警" in leak["text"]
    assert leak["source"] == {
        "markdown_line": 5942,
        "pdf_pages": 63,
        "printed_pages": 53,
    }
    assert "经领导批准申请调度停电处理" in treatment["text"]
    assert "与 10.3.2.3 重复" in duplicate["text"]


def test_existing_rules_are_linked_to_source_clauses():
    inventory = _inventory()
    coverage = {entry["rule_id"]: entry for entry in inventory["coverage"]}

    assert len(coverage) == 4
    assert all(entry["coverage_status"] == "linked" for entry in coverage.values())
    assert coverage["reg5-10.3-leak-evidence"]["source_clause_ids"] == ["10.3"]
    assert coverage["reg5-10.4-main-pump"]["source_clause_ids"] == ["10.4"]
    assert coverage["reg5-10.5-backup-not-running"]["source_clause_ids"] == ["10.5"]
    assert coverage["reg5-11.3-fan-feedback-loss"]["source_clause_ids"] == ["11.1", "11.3"]


def test_generated_inventory_is_reloadable(tmp_path):
    output = tmp_path / "procedure_inventory.json"
    parsed = _inventory()
    output.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == "procedure-five-inventory.v1"
    assert len(loaded["records"]) == 1903
