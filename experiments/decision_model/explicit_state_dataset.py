"""Build an auditable dataset from procedure clauses with explicit branches.

The source procedure contains many paragraphs that only say "check ...".  A
smaller subset has a usable decision contract: after a named observation or
verification, mutually exclusive conditions lead to distinct procedure
branches.  This module encodes those predicates as task-level natural-language
States and keeps the raw synthetic observations, clause text, and gold origin
in ``audit.jsonl``.

The generated rows are a supervised-learning seed, not operator-approved
labels or real event records.  Missing/ambiguous evidence is deliberately
represented as its own branch where the procedure does not authorize a
decision; no action is invented from silence.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .data import ROOT, digest, dumps, read_jsonl, write_json, write_jsonl


SOURCE_MD = ROOT / "output/markdown/姑苏站/规程五：±800kV姑苏换流站运行规程（设备异常与事故处理）.md"
DATASET_NAME = "gusu-explicit-state-v1"
SCHEMA_VERSION = "gusu-explicit-state-decision.v1"
STATE_BUILDER_VERSION = "gusu-explicit-state-renderer.v1"
DEFAULT_OUTPUT = ROOT / "data/decision" / DATASET_NAME
SPLITS = ("train", "calibration", "test")


QUESTION_SCHEMAS: dict[str, dict[str, Any]] = {
    "14.1": {
        "title": "阀厅空调室外机故障",
        "instructions": "依据已完成的复归核查，判断规程14.1当前应进入哪个分支。只能使用状态中明确记录的报警复归结果，不把未知当作复归。",
        "criteria": {
            "monitor_after_recovery": "报警已复归，继续加强监视",
            "switch_backup_and_maintenance": "报警未复归，切换备用机组并通知检修",
            "needs_recovery_check": "尚未获得报警复归结果，不能选择上述分支",
        },
    },
    "14.2": {
        "title": "压差传感器报警",
        "instructions": "依据传感器健康状态和读数与设定值的关系，判断规程14.2当前分支。没有完成测量时保留待核查。",
        "criteria": {
            "sensor_maintenance": "传感器异常，通知检修处理",
            "switch_unit_and_replace_filter": "传感器正常且读数超过设定值，切换空调机组并更换过滤网",
            "needs_measurement": "传感器状态或读数关系尚未明确，继续完成核查",
        },
    },
    "13.4": {
        "title": "泵房漏水",
        "instructions": "依据现场是否发现明显管网破损漏水，判断规程13.4当前分支。未发现漏点不能推断没有漏水。",
        "criteria": {
            "isolate_leak_and_repair": "发现明显管网破损漏水，隔离漏水点并联系检修",
            "maintenance_inspection": "未发现明显漏水点，联系检修进一步检查分析",
            "needs_field_check": "尚未完成漏水点现场检查",
        },
    },
    "13.5": {
        "title": "生活泵故障",
        "instructions": "依据故障泵以外的生活泵自动运行能力，判断规程13.5当前分支。",
        "criteria": {
            "secure_failed_pump_and_use_other": "另一台生活泵自动状态正常，做好故障泵安措并联系检修",
            "manual_start_other_and_water_supply": "另一台生活泵不能自动运行，切手动启动并联系储水",
            "inspect_both_pumps": "两台生活泵均故障，通知检修检查处理",
            "needs_pump_status_check": "另一台生活泵状态尚未核实",
        },
    },
    "15.12.6": {
        "title": "消防水池缺水",
        "instructions": "依据现场液位显示核查结果，判断规程15.12.6当前分支。后台低报警本身不能替代现场液位核对。",
        "criteria": {
            "replenish_and_find_refill_fault": "现场实际液位低告警属实，补水并查找自动补水原因",
            "record_sensor_fault_and_repair": "现场实际液位正常，判断为液位传感器故障并记录检修",
            "needs_level_check": "现场液位核查尚未完成",
        },
    },
    "15.13.1": {
        "title": "火灾自动报警系统误动作",
        "instructions": "依据现场火情确认和复位后的重复报警结果，判断规程15.13.1当前分支。",
        "criteria": {
            "fire_response": "现场存在火情，进入相应火灾处置预案",
            "false_alarm_monitor": "现场无火情且复位后不再报警，按误报警加强巡视",
            "isolate_faulty_loop_and_repair": "现场无火情但复位后重复报警，屏蔽相应回路或装置并维修",
            "needs_fire_check": "现场火情或复位结果尚未明确",
        },
    },
    "10.5": {
        "title": "内水冷主循环泵异常且未切至备用泵",
        "instructions": "依据备用泵控制/投入结果以及主泵现场严重异常情况，判断规程10.5当前分支。这里输出的是规程分支候选，不授权现场操作。",
        "criteria": {
            "set_auto_and_monitor_backup": "备用泵控制方式不在自动，切至自动并核对备用泵投入",
            "switch_from_severely_abnormal_main_pump": "主泵存在严重发热、冒烟、异常声音或电源烧糊，执行主泵切换并确认备用泵",
            "request_converter_valve_shutdown": "备用泵无法投入或投入后仍故障，申请对应换流阀停运并隔离故障泵",
            "needs_pump_condition_check": "备用泵或主泵现场条件尚未明确",
        },
    },
}


@dataclass(frozen=True)
class Case:
    family: str
    name: str
    split: str
    observations: tuple[str, ...]
    verification: tuple[str, ...]
    missing: tuple[str, ...]
    uncertainty: tuple[str, ...]
    label: str
    clause_ids: tuple[str, ...]
    signal_events: tuple[tuple[str, str], ...]


def _case(family: str, name: str, split: str, *, observations: Iterable[str],
          verification: Iterable[str], missing: Iterable[str], uncertainty: Iterable[str],
          label: str, clause_ids: Iterable[str], signal_events: Iterable[tuple[str, str]]) -> Case:
    return Case(family, name, split, tuple(observations), tuple(verification), tuple(missing),
                tuple(uncertainty), label, tuple(clause_ids), tuple(signal_events))


CASES: tuple[Case, ...] = (
    _case("14.1", "recovered", "train", observations=("后台出现热泵机组故障报警",),
          verification=("执行复归后，后台报警不再出现", "故障机组未见新的停运记录"), missing=(),
          uncertainty=("再次发生相同报警的风险仍需监视",), label="monitor_after_recovery",
          clause_ids=("14.1.1.1", "14.1.2.1", "14.1.2.2"),
          signal_events=(("heat_pump_alarm", "asserted"), ("heat_pump_alarm", "cleared"))),
    _case("14.1", "not_recovered", "train", observations=("后台出现热泵机组故障报警",),
          verification=("执行复归后，后台报警仍然存在",), missing=(),
          uncertainty=("故障原因尚未查明",), label="switch_backup_and_maintenance",
          clause_ids=("14.1.1.1", "14.1.2.1", "14.1.2.3"),
          signal_events=(("heat_pump_alarm", "asserted"), ("heat_pump_alarm", "still_asserted"))),
    _case("14.1", "unknown_recovery", "calibration", observations=("后台出现热泵机组故障报警",),
          verification=(), missing=("复归操作结果",), uncertainty=("不能判断报警是否复归",),
          label="needs_recovery_check", clause_ids=("14.1.1.1", "14.1.2.1"),
          signal_events=(("heat_pump_alarm", "asserted"),)),
    _case("14.1", "repeat_after_recovery", "test", observations=("后台出现热泵机组故障报警",),
          verification=("第一次复归后报警暂时消失，随后再次出现",), missing=(),
          uncertainty=("当前不满足报警已稳定复归的条件",), label="switch_backup_and_maintenance",
          clause_ids=("14.1.1.1", "14.1.2.1", "14.1.2.3"),
          signal_events=(("heat_pump_alarm", "asserted"), ("heat_pump_alarm", "cleared"), ("heat_pump_alarm", "reasserted"))),

    _case("14.2", "sensor_fault", "train", observations=("后台发压差传感器报警",),
          verification=("压差传感器自检异常，读数不可信",), missing=(), uncertainty=(),
          label="sensor_maintenance", clause_ids=("14.2.1.1", "14.2.2.1", "14.2.2.2"),
          signal_events=(("pressure_sensor_alarm", "asserted"), ("pressure_sensor", "fault"))),
    _case("14.2", "high_with_healthy_sensor", "train", observations=("后台发压差传感器报警",),
          verification=("压差传感器工作正常", "读数明确超过设定值"), missing=(), uncertainty=(),
          label="switch_unit_and_replace_filter", clause_ids=("14.2.1.1", "14.2.2.1", "14.2.2.3"),
          signal_events=(("pressure_sensor_alarm", "asserted"), ("pressure_sensor", "healthy"), ("pressure_value", "above_setpoint"))),
    _case("14.2", "normal_below_setpoint", "calibration", observations=("后台发压差传感器报警",),
          verification=("压差传感器工作正常", "读数未超过设定值"), missing=("报警原因和后续处理条件",),
          uncertainty=("规程14.2未给出该组合的直接处置分支",), label="needs_measurement",
          clause_ids=("14.2.1.1", "14.2.2.1"),
          signal_events=(("pressure_sensor_alarm", "asserted"), ("pressure_sensor", "healthy"), ("pressure_value", "not_above_setpoint"))),
    _case("14.2", "sensor_status_unknown", "test", observations=("后台发压差传感器报警",),
          verification=("尚未完成传感器健康状态和读数与设定值的核对",), missing=("传感器状态", "读数与设定值关系"),
          uncertainty=("不能在核查前选择处置分支",), label="needs_measurement",
          clause_ids=("14.2.1.1", "14.2.2.1"), signal_events=(("pressure_sensor_alarm", "asserted"),)),

    _case("13.4", "leak_found", "train", observations=("泵房排污泵连续运行", "泵房内出现积水"),
          verification=("现场发现明显管网破损漏水位置",), missing=(), uncertainty=(),
          label="isolate_leak_and_repair", clause_ids=("13.4.1.1", "13.4.1.2", "13.4.2.1", "13.4.2.2"),
          signal_events=(("sump_pump", "continuous"), ("pump_room_water", "present"), ("pipe_leak", "confirmed"))),
    _case("13.4", "no_leak_found", "train", observations=("泵房排污泵频繁启动", "泵房内出现积水"),
          verification=("现场检查未发现明显漏水点",), missing=(), uncertainty=("漏水原因尚未查明",),
          label="maintenance_inspection", clause_ids=("13.4.1.1", "13.4.1.2", "13.4.2.1", "13.4.2.3"),
          signal_events=(("sump_pump", "frequent"), ("pump_room_water", "present"), ("pipe_leak", "not_found"))),
    _case("13.4", "field_check_pending", "calibration", observations=("泵房内出现积水",), verification=(),
          missing=("管道漏水位置和是否存在明显破损",), uncertainty=("尚未完成现场检查",), label="needs_field_check",
          clause_ids=("13.4.1.2", "13.4.2.1"), signal_events=(("pump_room_water", "present"),)),
    _case("13.4", "leak_point_not_obvious", "test", observations=("泵房排污泵连续运行",),
          verification=("检查后未发现明显漏水点",), missing=(), uncertainty=("仍需检修进一步检查分析",),
          label="maintenance_inspection", clause_ids=("13.4.1.1", "13.4.2.1", "13.4.2.3"),
          signal_events=(("sump_pump", "continuous"), ("pipe_leak", "not_found"))),

    _case("13.5", "other_auto_ok", "train", observations=("1号生活泵故障",),
          verification=("2号生活泵处于自动状态且运行正常",), missing=(), uncertainty=(),
          label="secure_failed_pump_and_use_other", clause_ids=("13.5.1.1", "13.5.2.1", "13.5.2.2", "13.5.2.3"),
          signal_events=(("life_pump_1", "fault"), ("life_pump_2", "auto_running"))),
    _case("13.5", "other_auto_failed", "train", observations=("1号生活泵故障",),
          verification=("2号生活泵不能自动运行",), missing=(), uncertainty=(),
          label="manual_start_other_and_water_supply", clause_ids=("13.5.1.1", "13.5.2.1", "13.5.2.2", "13.5.2.4"),
          signal_events=(("life_pump_1", "fault"), ("life_pump_2", "auto_failed"))),
    _case("13.5", "both_failed", "calibration", observations=("1号、2号生活泵均发生故障",), verification=(),
          missing=(), uncertainty=(), label="inspect_both_pumps", clause_ids=("13.5.1.1", "13.5.2.5"),
          signal_events=(("life_pump_1", "fault"), ("life_pump_2", "fault"))),
    _case("13.5", "other_status_unknown", "test", observations=("1号生活泵故障",), verification=(),
          missing=("2号生活泵自动运行状态",), uncertainty=("不能假定另一台生活泵正常",), label="needs_pump_status_check",
          clause_ids=("13.5.1.1", "13.5.2.1", "13.5.2.2"), signal_events=(("life_pump_1", "fault"),)),

    _case("15.12.6", "actual_low", "train", observations=("后台显示消防水池液位低",),
          verification=("现场显示器和磁翻板均确认实际液位低",), missing=(), uncertainty=(),
          label="replenish_and_find_refill_fault", clause_ids=("15.12.6.1.1", "15.12.6.1.2", "15.12.6.2.1", "15.12.6.2.2"),
          signal_events=(("fire_pool_alarm", "asserted"), ("fire_pool_level", "low_confirmed"))),
    _case("15.12.6", "sensor_fault", "train", observations=("后台显示消防水池液位低",),
          verification=("现场显示器和磁翻板显示实际液位正常", "后台低液位信号与现场不一致"), missing=(), uncertainty=(),
          label="record_sensor_fault_and_repair", clause_ids=("15.12.6.1.1", "15.12.6.2.1", "15.12.6.2.3"),
          signal_events=(("fire_pool_alarm", "asserted"), ("fire_pool_level", "normal"), ("level_sensor", "fault"))),
    _case("15.12.6", "field_check_pending", "calibration", observations=("后台显示消防水池液位低",), verification=(),
          missing=("现场液位显示和磁翻板核对结果",), uncertainty=("后台报警不能代替现场液位确认",), label="needs_level_check",
          clause_ids=("15.12.6.1.1", "15.12.6.2.1"), signal_events=(("fire_pool_alarm", "asserted"),)),
    _case("15.12.6", "actual_low_one_display", "test", observations=("后台显示消防水池液位低",),
          verification=("磁翻板显示液位低，但综合水泵房显示器尚未核对",), missing=("第二个现场液位显示的核对结果",),
          uncertainty=("现场证据尚不完整",), label="needs_level_check", clause_ids=("15.12.6.1.1", "15.12.6.2.1"),
          signal_events=(("fire_pool_alarm", "asserted"), ("fire_pool_level", "low_partial"))),

    _case("15.13.1", "fire_confirmed", "train", observations=("火灾报警控制系统和监控系统均报火警",),
          verification=("视频和现场人员均确认对应部位存在火情",), missing=(), uncertainty=(), label="fire_response",
          clause_ids=("15.13.1.1.1", "15.13.1.1.2", "15.13.1.2.1"), signal_events=(("fire_alarm", "asserted"), ("fire", "confirmed"))),
    _case("15.13.1", "false_alarm_once", "train", observations=("火灾报警控制系统报火警",),
          verification=("现场无火情", "按下复位键后不再报警"), missing=(), uncertainty=(), label="false_alarm_monitor",
          clause_ids=("15.13.1.1.1", "15.13.1.2.1", "15.13.1.2.2"), signal_events=(("fire_alarm", "asserted"), ("fire", "absent"), ("fire_alarm", "cleared_after_reset"))),
    _case("15.13.1", "false_alarm_repeat", "calibration", observations=("火灾报警控制系统报火警",),
          verification=("现场无火情", "按下复位键后仍多次重复报警"), missing=(), uncertainty=(), label="isolate_faulty_loop_and_repair",
          clause_ids=("15.13.1.1.1", "15.13.1.2.1", "15.13.1.2.2"), signal_events=(("fire_alarm", "asserted"), ("fire", "absent"), ("fire_alarm", "repeated_after_reset"))),
    _case("15.13.1", "fire_check_pending", "test", observations=("火灾报警控制系统报火警",), verification=(),
          missing=("现场火情确认",), uncertainty=("不能将报警直接当作已确认火灾，也不能当作误报警",), label="needs_fire_check",
          clause_ids=("15.13.1.1.1", "15.13.1.2.1"), signal_events=(("fire_alarm", "asserted"),)),

    _case("10.5", "backup_not_auto", "train", observations=("内水冷主循环泵异常且未切至备用泵",),
          verification=("备用循环泵控制方式检查为手动，不在自动位置",), missing=(), uncertainty=(), label="set_auto_and_monitor_backup",
          clause_ids=("10.5.1.1", "10.5.2.1", "10.5.2.2"), signal_events=(("main_pump", "abnormal"), ("backup_pump", "manual"))),
    _case("10.5", "main_severe", "train", observations=("内水冷主循环泵异常且未切至备用泵",),
          verification=("主泵现场检查发现严重发热和异常声音", "备用泵可以投入运行"), missing=(), uncertainty=(), label="switch_from_severely_abnormal_main_pump",
          clause_ids=("10.5.1.1", "10.5.2.3"), signal_events=(("main_pump", "abnormal"), ("main_pump", "severely_abnormal"), ("backup_pump", "available"))),
    _case("10.5", "backup_failed", "calibration", observations=("内水冷主循环泵异常且未切至备用泵",),
          verification=("备用泵无法投入运行，或投入后再次出现故障",), missing=(), uncertainty=(), label="request_converter_valve_shutdown",
          clause_ids=("10.5.1.1", "10.5.2.5"), signal_events=(("main_pump", "abnormal"), ("backup_pump", "failed"))),
    _case("10.5", "condition_unknown", "test", observations=("内水冷主循环泵异常且未切至备用泵",), verification=(),
          missing=("备用泵控制方式、备用泵投入结果、主泵现场状态",), uncertainty=("不能直接选择10.5处置分支",), label="needs_pump_condition_check",
          clause_ids=("10.5.1.1", "10.5.2.1"), signal_events=(("main_pump", "abnormal"),)),
)


def _event_id(case: Case, index: int) -> str:
    return "synthetic-explicit-event-" + hashlib.sha256(f"{case.family}:{case.name}:{index}".encode()).hexdigest()[:16]


def _events(case: Case) -> list[dict[str, Any]]:
    start = datetime(2026, 10, 1, 8, 0, 0)
    result = []
    for i, (signal, value) in enumerate(case.signal_events):
        result.append({"id": _event_id(case, i), "event_time": (start + timedelta(seconds=i * 5)).isoformat(timespec="milliseconds"),
                       "station": "姑苏", "source": "procedure-derived-synthetic", "signal_id": signal,
                       "observation": value})
    return result


def _render_state(case: Case, events: list[dict[str, Any]]) -> str:
    title = QUESTION_SCHEMAS[case.family]["title"]
    observations = "；".join(case.observations) if case.observations else "无"
    verification = "；".join(case.verification) if case.verification else "尚未完成"
    missing = "、".join(case.missing) if case.missing else "无明确缺失项"
    uncertainty = "；".join(case.uncertainty) if case.uncertainty else "暂无额外不确定性"
    return (
        f"任务场景：{title}。监视窗口内观察到：{observations}。"
        f"已完成核查：{verification}。缺失证据：{missing}。"
        f"不确定性：{uncertainty}。以上 State 只表达已记录的观察与核查结果，供规程分支判断，不等同于自动执行现场操作。"
    )


def _row(case: Case) -> tuple[dict[str, Any], dict[str, Any]]:
    events = _events(case)
    state = _render_state(case, events)
    question = {"type": "choice", "instructions": QUESTION_SCHEMAS[case.family]["instructions"],
                "criteria": QUESTION_SCHEMAS[case.family]["criteria"]}
    gold = {"decision": {"label": case.label}}
    row = {"id": f"{case.family}-{case.name}", "state": state, "questions": dumps({"decision": question}), "gold": dumps(gold)}
    audit = {
        "id": row["id"], "split": case.split, "family_id": case.family, "scenario_id": f"{case.family}/{case.name}",
        "source_type": "procedure-derived-explicit-branch", "gold_origin": "manually specified procedure branch",
        "review_status": "source_checked_not_domain_expert_approved", "procedure_title": QUESTION_SCHEMAS[case.family]["title"],
        "procedure_clause_ids": list(case.clause_ids), "observations": list(case.observations),
        "verification": list(case.verification), "missing_evidence": list(case.missing),
        "uncertainty": list(case.uncertainty), "raw_events": events, "structured_state": {
            "schema_version": SCHEMA_VERSION, "state_builder_version": STATE_BUILDER_VERSION,
            "procedure_family": case.family, "observations": list(case.observations),
            "verification": list(case.verification), "missing_evidence": list(case.missing),
            "uncertainty": list(case.uncertainty), "source_event_ids": [e["id"] for e in events],
            "decision_label": case.label,
        },
        "expected_semantic_answer": case.label, "state_input": "natural_language_only",
    }
    return row, audit


def _validate(row: dict[str, Any]) -> None:
    if set(row) != {"id", "state", "questions", "gold"}:
        raise ValueError(f"invalid row fields: {row['id']}")
    questions = json.loads(row["questions"]) if isinstance(row["questions"], str) else row["questions"]
    gold = json.loads(row["gold"]) if isinstance(row["gold"], str) else row["gold"]
    if set(questions) != {"decision"} or set(gold) != {"decision"}:
        raise ValueError(f"each row must contain one decision question: {row['id']}")
    q = questions["decision"]
    if q.get("type") != "choice" or not isinstance(q.get("criteria"), dict):
        raise ValueError(f"invalid choice question: {row['id']}")
    if gold["decision"].get("label") not in q["criteria"]:
        raise ValueError(f"gold is not in criteria: {row['id']}")


def build(destination: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"output must be new or empty: {destination}")
    if not SOURCE_MD.exists():
        raise FileNotFoundError(SOURCE_MD)
    rows: dict[str, list[dict[str, Any]]] = {split: [] for split in SPLITS}
    audit: list[dict[str, Any]] = []
    for case in CASES:
        row, meta = _row(case)
        _validate(row)
        rows[case.split].append(row)
        audit.append(meta)
    destination.mkdir(parents=True, exist_ok=True)
    for split in SPLITS:
        write_jsonl(destination / f"{split}.jsonl", rows[split])
    write_jsonl(destination / "audit.jsonl", audit)
    write_json(destination / "schema.json", {"schema_version": SCHEMA_VERSION, "state_input": "natural_language_only",
                                               "questions": QUESTION_SCHEMAS, "state_builder_version": STATE_BUILDER_VERSION})
    write_json(destination / "source.json", {"source_path": str(SOURCE_MD.relative_to(ROOT)), "source_sha256": digest(SOURCE_MD),
                                               "selected_clause_families": sorted(QUESTION_SCHEMAS)})
    (destination / "README.md").write_text(
        "# 姑苏站明确分支 State 数据集\n\n"
        "这是根据规程五中具有互斥核查分支的 7 个条款族构建的合成种子集。模型输入只有任务级自然语言 State 和一个分支问题；原始合成事件、结构化核查、条款定位和标签来源保存在 `audit.jsonl`。\n\n"
        "当前包含 28 条 canonical cases：14 条 train、7 条 calibration、7 条 test。每个案例表达一个规程分支条件，`needs_*` 标签只表示关键核查尚未完成，不是由数据缺失推导出的正常状态。\n\n"
        "数据来自规程五 Markdown 转写，来源哈希记录在 `source.json`。数据未经运行人员或领域专家审核，不可作为现场自动操作依据。\n",
        encoding="utf-8",
    )
    manifest = {"dataset": DATASET_NAME, "schema_version": SCHEMA_VERSION, "purpose": "explicit_procedure_branch_state_seed",
                "review_status": "source_checked_synthetic_not_expert_approved", "state_input": "natural_language_semantic_state_only",
                "audit_excluded_from_model_input": True, "split_unit": "branch_case", "files": {},
                "source_sha256": digest(SOURCE_MD), "family_count": len(QUESTION_SCHEMAS), "case_count": len(CASES)}
    for p in sorted(destination.iterdir()):
        if p.name != "manifest.json": manifest["files"][p.name] = digest(p)
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
    labels: dict[str, dict[str, int]] = {}
    states: dict[str, str] = {}
    for split in SPLITS:
        rows = read_jsonl(destination / f"{split}.jsonl")
        if not rows: raise ValueError(f"empty split: {split}")
        counter: dict[str, int] = {}
        for raw in rows:
            row = dict(raw); _validate(row)
            if row["id"] in ids or row["id"] not in audit: raise ValueError(f"audit/model mismatch: {row['id']}")
            ids.add(row["id"])
            if audit[row["id"]]["split"] != split: raise ValueError(f"split mismatch: {row['id']}")
            if row["state"] in states and states[row["state"]] != split: raise ValueError(f"state leakage: {row['id']}")
            states[row["state"]] = split
            label=json.loads(row["gold"])["decision"]["label"]
            counter[label]=counter.get(label,0)+1
        counts[split] = {"rows": len(rows), "decisions": len(rows)}
        labels[split]=dict(sorted(counter.items()))
    if ids != set(audit): raise ValueError("audit rows do not exactly cover model rows")
    return {"status": "passed", "counts": counts, "labels": labels, "total_rows": len(ids),
            "family_count": len(QUESTION_SCHEMAS), "limitations": [
                "Synthetic procedure-derived states; no expert approval or field fault-rate claim.",
                "The state renderer is deterministic and does not test raw-log recognition.",
                "A branch label represents the procedure text, not permission to execute an operation.",
            ]}


def main() -> None:
    parser = argparse.ArgumentParser(description="构建规程五明确分支 State 数据集")
    parser.add_argument("command", choices=("build", "verify"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.output) if args.command == "build" else verify(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
