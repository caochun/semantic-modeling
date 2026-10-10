from pathlib import Path

import pytest

from experiments.decision_model.phenomenon import analyze_events, read_rows


ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "experiments/decision_model/cases/gusu-valve-cooling-20260911/raw.tsv"


def _row(content, *, second="00.000", host="P221VCC2", channel="A", device=None):
    device = device or f"极2低端阀1许继阀冷{channel}2"
    return {
        "event_time": f"2026-09-11T12:00:{second}",
        "station": "姑苏",
        "host": host,
        "system_alarm": channel,
        "device": device,
        "content": f"{device}_{content}",
        "alarm_level": "3",
    }


def test_user_case_is_normalized_and_has_four_independent_complete_groups():
    result = analyze_events(read_rows(CASE))

    assert len(result["events"]) == 20
    assert result["summary"]["known_event_count"] == 20
    assert result["summary"]["unknown_event_count"] == 0
    assert result["physical_fault_confirmed"] is False

    groups = result["state"]["fan_groups"]
    assert len(groups) == 4
    assert {group["status"] for group in groups} == {"supported"}
    assert len({group["id"] for group in groups}) == 4
    assert {group["group"] for group in groups} == {"M01_M03_M05", "M02_M04_M06"}

    facts = {fact["id"]: fact for fact in result["phenomena"]}
    assert facts["fan_running_feedback_loss"]["status"] == "supported"
    assert facts["leak_protection_masked"]["status"] == "supported"
    assert facts["inner_cooling_water_leak"]["status"] == "insufficient_evidence"


def test_alarm_edge_is_separate_from_observation():
    result = analyze_events(
        [
            _row("E4.M01风机运行消失 消失"),
            _row("泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽出现 出现", second="01.000"),
        ]
    )
    fan, mask = result["events"]
    assert fan["alarm_edge"] == "消失"
    assert fan["observation"] == "not_running"
    assert mask["alarm_edge"] == "出现"
    assert mask["observation"] == "masked"


def test_mask_clear_does_not_claim_leak_or_normality():
    result = analyze_events(
        [
            _row("泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽出现"),
            _row("泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽消失", second="02.000"),
        ]
    )
    facts = {fact["id"]: fact for fact in result["phenomena"]}
    assert facts["leak_protection_masked"]["details"]["cleared_in_window"] is True
    assert facts["leak_protection_masked"]["missing"]
    assert facts["inner_cooling_water_leak"]["status"] == "insufficient_evidence"
    assert result["physical_fault_confirmed"] is False


def test_running_feedback_does_not_count_as_feedback_loss():
    result = analyze_events([_row("E4.M01风机运行出现")])
    ids = {fact["id"] for fact in result["phenomena"]}
    assert "fan_running_feedback_loss" not in ids
    assert not result["state"]["fan_groups"]


def test_clear_only_mask_event_is_insufficient_for_masked_fact():
    result = analyze_events(
        [_row("泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽消失")]
    )
    fact = next(f for f in result["phenomena"] if f["id"] == "leak_protection_masked")
    assert fact["status"] == "insufficient_evidence"


def test_unknown_event_is_preserved_and_not_forced_into_a_signal():
    row = _row("某个尚未建立映射的状态 出现")
    result = analyze_events([row])
    event = result["events"][0]
    assert event["signal_id"] is None
    assert event["mapping_status"] == "unknown"
    assert event["id"] in result["state"]["unknown_event_ids"]
    assert event["raw"] == row


def test_a_and_b_channels_have_separate_partial_group_state():
    rows = [
        _row("E4.M01风机运行消失", channel="A"),
        _row("E4.M03风机运行消失", second="01.000", channel="B"),
    ]
    result = analyze_events(rows)
    groups = result["state"]["fan_groups"]
    assert {(group["channel"], group["status"]) for group in groups} == {
        ("A", "partial"),
        ("B", "partial"),
    }
    assert all(len(group["missing"]) == 2 for group in groups)


def test_empty_input_is_rejected():
    with pytest.raises(ValueError, match="输入没有事件"):
        analyze_events([])
