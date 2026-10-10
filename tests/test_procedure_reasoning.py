from experiments.decision_model.phenomenon import analyze_events
from experiments.decision_model.procedure_reasoning import reason_from_analysis


def _row(content, *, second="00.000", channel="A", device=None):
    device = device or f"极2低端阀1许继阀冷{channel}2"
    return {
        "event_time": f"2026-09-11T12:00:{second}",
        "station": "姑苏",
        "host": "P221VCC2",
        "system_alarm": channel,
        "device": device,
        "content": f"{device}_{content}",
        "alarm_level": "3",
    }


def test_current_case_gets_checks_instead_of_a_fault_decision():
    rows = [
        _row("E4.M01风机运行消失 消失"),
        _row("泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽出现 出现", second="01.000"),
    ]
    reasoning = reason_from_analysis(analyze_events(rows))

    assert {rule["id"] for rule in reasoning["rules_applied"]} == {
        "reg5-11.3-fan-feedback-loss",
        "reg5-10.3-leak-evidence",
    }
    assert reasoning["summary"]["disposition"] == "not_ready_for_disposition"
    assert reasoning["summary"]["physical_fault_confirmed"] is False
    assert reasoning["summary"]["missing_check_count"] == 7
    assert all(check["status"] == "missing" for check in reasoning["checks"])
    assert all(edge["relation"] in {"requires_check", "satisfies", "supports", "insufficient_for"}
               for edge in reasoning["evidence_graph"]["edges"])


def test_insufficient_leak_candidate_does_not_activate_rule_by_itself():
    analysis = analyze_events([_row("某个尚未建立映射的状态 出现")])
    reasoning = reason_from_analysis(analysis)
    assert reasoning["rules_applied"] == []
    assert reasoning["checks"] == []
    assert reasoning["summary"]["disposition"] == "no_applicable_procedure_rule"


def test_observed_leak_and_backup_events_satisfy_matching_checks():
    rows = [
        _row("主循环泵故障出现"),
        _row("备用泵未切至运行出现", second="01.000"),
        _row("24小时泄漏报警出现", second="02.000"),
        _row("阀厅实际漏水出现", second="03.000"),
    ]
    reasoning = reason_from_analysis(analyze_events(rows))
    checks = {check["id"]: check for check in reasoning["checks"]}
    assert checks["backup_pump_status"]["status"] == "observed"
    assert checks["backup_pump_failure_confirmation"]["status"] == "observed"
    assert checks["leak_alarm_or_level"]["status"] == "observed"
    assert checks["field_leak_check"]["status"] == "observed"
    assert reasoning["summary"]["disposition"] == "not_ready_for_disposition"
