import json
from pathlib import Path


def test_frontend_case_catalog_contains_one_pasteable_log_per_canonical_case():
    path = Path("decision_service/static/samples.json")
    samples = json.loads(path.read_text(encoding="utf-8"))
    assert len(samples) == 28
    assert len({item["case_id"] for item in samples}) == 28
    assert all(item["task_id"].startswith("procedure_") for item in samples)
    assert all("event_time\tstation\thost" in item["raw_tsv"] for item in samples)
    assert all(item["gold_decision"] for item in samples)
