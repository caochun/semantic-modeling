"""Generate a small, auditable Typed Decisions dataset from Procedure Five.

The generator deliberately has two layers:

* a synthetic event stream and deterministic semantic-state builder;
* a typed-decision row whose ``state`` is only the natural-language state.

Raw events, structured state, procedure clauses and gold provenance are kept in
``audit.jsonl``.  They are not model input.  The first snapshot covers four
curated scenarios (10.3, 10.4, 10.5 and 11.3); it is a protocol seed, not
expert-approved training data or evidence of field fault rates.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .data import ROOT, digest, dumps, write_json, write_jsonl, read_jsonl
from .procedure_inventory import DEFAULT_OUTPUT as DEFAULT_INVENTORY


DATASET_NAME = "gusu-procedure-state-v1"
SCHEMA_VERSION = "gusu-procedure-typed-decisions.v1"
STATE_BUILDER_VERSION = "gusu-state-builder.v1"
DEFAULT_OUTPUT = ROOT / "data/decision" / DATASET_NAME
INVENTORY = DEFAULT_INVENTORY
SPLITS = ("train", "calibration", "test")


QUESTION_SCHEMA: dict[str, dict[str, Any]] = {
    "scenario_10_3_applicable": {
        "type": "noul",
        "instructions": "根据任务级状态，判断是否存在需要按内水冷泄漏预案核查的候选现象。保护屏蔽本身不能证明实际漏水。",
        "criteria": {
            "false": "没有足够的内水冷泄漏候选现象",
            "true": "存在需要按内水冷泄漏预案继续核查的候选现象",
        },
    },
    "scenario_10_4_applicable": {
        "type": "noul",
        "instructions": "根据任务级状态，判断是否存在内水冷主循环泵故障候选。",
        "criteria": {
            "false": "没有观察到主循环泵故障候选",
            "true": "观察到主循环泵故障候选，需要核对备用泵和泵体状态",
        },
    },
    "scenario_10_5_applicable": {
        "type": "noul",
        "instructions": "判断是否已经同时具备主循环泵异常和备用泵未投入这两个条件。",
        "criteria": {
            "false": "两个条件没有同时成立或信息不足",
            "true": "主循环泵异常且备用泵未投入的条件同时成立",
        },
    },
    "scenario_11_3_applicable": {
        "type": "noul",
        "instructions": "根据任务级状态，判断是否存在阀外水冷冷却塔风机全停候选现象。部分风机反馈丢失不能直接等同于全停。",
        "criteria": {
            "false": "没有足够证据表明冷却塔风机全停",
            "true": "多个风机组的运行反馈在窗口内全部丢失，形成全停候选",
        },
    },
    "physical_fault": {
        "type": "choice",
        "instructions": "根据当前语义状态判断物理设备故障是否已经确认。信号异常或保护状态变化不等于物理故障确认。",
        "criteria": {
            "confirmed": "已有直接现场或独立测量证据确认物理故障",
            "candidate": "存在异常现象，但物理故障尚未确认",
            "insufficient": "现有信息不足以判断物理故障状态",
        },
    },
    "evidence_sufficiency": {
        "type": "choice",
        "instructions": "判断当前证据是否足以进入人工规程处置审核。这里不授权执行现场操作。",
        "criteria": {
            "sufficient": "现象和关键状态足以进入人工规程处置审核",
            "insufficient": "仍缺少关键状态、趋势或现场核查结果",
            "contradictory": "存在相互冲突的状态证据，不能直接判断",
        },
    },
    "needs_human_review": {
        "type": "noul",
        "instructions": "判断当前状态是否必须由运行人员复核。",
        "criteria": {
            "false": "当前判断可以在已有证据范围内继续自动处理",
            "true": "需要运行人员复核证据、规程条件或授权前置条件",
        },
    },
    "next_check": {
        "type": "choice",
        "instructions": "在不补造缺失观测、不授权现场操作的前提下，选择当前最优先的核查类别。",
        "criteria": {
            "leak_alarm_or_level": "核对泄漏报警和膨胀罐/高位水箱液位",
            "field_leak": "现场检查阀厅、冷却塔或主循环泵是否实际漏水",
            "pump_backup": "核对备用循环泵是否投入并运行正常",
            "pump_local": "检查主循环泵电机、电源及现场状态",
            "fan_power": "核对风机控制、电源和变频器状态",
            "cooling_redundancy": "核对其他风机和冷却冗余能力",
            "thermal_hydraulic": "核对进阀温度、流量和压力趋势",
            "manual_review": "提交运行人员进行规程和授权条件复核",
        },
    },
    "urgency": {
        "type": "score",
        "instructions": "按照当前已知状态评估监盘核查紧迫程度，不把缺失信息自动解释为正常。",
        "criteria": [
            "常规观察，没有明确异常证据",
            "需要加强监视并尽快补充证据",
            "需要在近期完成人工核查",
            "存在持续恶化或冷却能力风险，需要立即升级复核",
        ],
    },
}


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    scenario: str
    variant: str
    split: str
    facts: tuple[str, ...]
    transitions: tuple[str, ...]
    missing: tuple[str, ...]
    uncertainty: tuple[str, ...]
    physical_fault: str
    evidence_sufficiency: str
    next_check: str
    urgency: int
    data_completeness: str = "partial"


def _case(
    scenario: str,
    variant: str,
    split: str,
    *,
    facts: Iterable[str],
    transitions: Iterable[str] = (),
    missing: Iterable[str],
    uncertainty: Iterable[str],
    physical_fault: str,
    evidence_sufficiency: str,
    next_check: str,
    urgency: int,
    data_completeness: str = "partial",
) -> CaseSpec:
    return CaseSpec(
        case_id=f"{scenario}-{variant}",
        scenario=scenario,
        variant=variant,
        split=split,
        facts=tuple(facts),
        transitions=tuple(transitions),
        missing=tuple(missing),
        uncertainty=tuple(uncertainty),
        physical_fault=physical_fault,
        evidence_sufficiency=evidence_sufficiency,
        next_check=next_check,
        urgency=urgency,
        data_completeness=data_completeness,
    )


CASES: tuple[CaseSpec, ...] = (
    _case(
        "10.3", "mask-only", "train",
        facts=("泄漏保护短时屏蔽条件出现",),
        transitions=("泄漏保护屏蔽条件随后消失",),
        missing=("24小时泄漏报警或膨胀罐液位", "实际漏水检查", "水位变化趋势"),
        uncertainty=("保护屏蔽条件不直接证明发生泄漏",),
        physical_fault="insufficient", evidence_sufficiency="insufficient",
        next_check="leak_alarm_or_level", urgency=1,
    ),
    _case(
        "10.3", "alarm-level", "train",
        facts=("出现24小时泄漏报警", "膨胀罐液位低报警出现"),
        missing=("阀厅、冷却塔和主循环泵的现场漏水结果",),
        uncertainty=("报警可以由测量或回路问题引起",),
        physical_fault="candidate", evidence_sufficiency="sufficient",
        next_check="field_leak", urgency=2,
    ),
    _case(
        "10.3", "missing", "calibration",
        facts=("监控窗口内没有泄漏报警或液位低信号",),
        missing=("初始和末端完整液位状态", "现场漏水检查", "连续水位趋势"),
        uncertainty=("没有目标报警不能证明系统正常",),
        physical_fault="insufficient", evidence_sufficiency="insufficient",
        next_check="manual_review", urgency=1,
    ),
    _case(
        "10.3", "physical-leak", "test",
        facts=("现场检查发现阀厅存在实际漏水", "膨胀罐液位呈下降趋势"),
        missing=("漏点能否封堵以及能否隔离",),
        uncertainty=("漏点位置和可隔离性仍未确认",),
        physical_fault="confirmed", evidence_sufficiency="sufficient",
        next_check="manual_review", urgency=3, data_completeness="complete_for_observation",
    ),
    _case(
        "10.4", "pump-backup-running", "train",
        facts=("内水冷主循环泵故障信号出现", "备用循环泵投入并运行反馈正常"),
        missing=("主循环泵电机、电源和现场状态",),
        uncertainty=("故障原因和主泵是否可恢复尚未确认",),
        physical_fault="candidate", evidence_sufficiency="sufficient",
        next_check="pump_local", urgency=2,
    ),
    _case(
        "10.4", "pump-backup-unknown", "train",
        facts=("内水冷主循环泵故障信号出现",),
        missing=("备用泵投入状态", "主循环泵电机和电源现场状态"),
        uncertainty=("没有备用泵状态不能判断冷却冗余",),
        physical_fault="candidate", evidence_sufficiency="insufficient",
        next_check="pump_backup", urgency=2,
    ),
    _case(
        "10.4", "no-pump-signal", "calibration",
        facts=("窗口内没有主循环泵故障信号",),
        missing=("完整泵状态快照",),
        uncertainty=("没有故障信号不等于已证明主泵正常",),
        physical_fault="insufficient", evidence_sufficiency="insufficient",
        next_check="manual_review", urgency=1,
    ),
    _case(
        "10.4", "pump-both-failed", "test",
        facts=("主循环泵故障信号出现", "备用循环泵投入后再次出现故障"),
        missing=("对应阀组当前热工参数和调度状态",),
        uncertainty=("阀组停运条件需要运行人员依据当前状态确认",),
        physical_fault="candidate", evidence_sufficiency="sufficient",
        next_check="manual_review", urgency=3, data_completeness="complete_for_observation",
    ),
    _case(
        "10.5", "backup-not-running", "train",
        facts=("内水冷主循环泵异常", "备用循环泵未切至运行"),
        missing=("备用泵控制方式", "备用泵投入试验结果"),
        uncertainty=("备用泵未投入的原因尚未确认",),
        physical_fault="candidate", evidence_sufficiency="sufficient",
        next_check="pump_backup", urgency=3,
    ),
    _case(
        "10.5", "backup-status-unknown", "train",
        facts=("内水冷主循环泵异常",),
        missing=("备用泵是否投入", "备用泵控制方式",),
        uncertainty=("不能因为没有备用泵记录就认定其未投入",),
        physical_fault="candidate", evidence_sufficiency="insufficient",
        next_check="pump_backup", urgency=2,
    ),
    _case(
        "10.5", "backup-only", "calibration",
        facts=("记录到备用泵状态异常，但没有主泵异常证据",),
        missing=("主循环泵当前状态",),
        uncertainty=("10.5的两个触发条件未同时成立",),
        physical_fault="insufficient", evidence_sufficiency="insufficient",
        next_check="manual_review", urgency=1,
    ),
    _case(
        "10.5", "both-pumps-failed", "test",
        facts=("主循环泵异常", "备用泵无法投入运行"),
        missing=("相应阀组进阀温度、流量和压力趋势",),
        uncertainty=("高风险处置条件需要人工确认",),
        physical_fault="candidate", evidence_sufficiency="sufficient",
        next_check="thermal_hydraulic", urgency=3, data_completeness="complete_for_observation",
    ),
    _case(
        "11.3", "fan-full-loss", "train",
        facts=("一个阀外水冷冷却塔风机组的运行反馈全部丢失",),
        missing=("风机控制和电源状态", "其他风机及冷却冗余状态", "进阀温度趋势"),
        uncertainty=("运行反馈丢失不等于风机机械故障",),
        physical_fault="candidate", evidence_sufficiency="sufficient",
        next_check="fan_power", urgency=2,
    ),
    _case(
        "11.3", "fan-partial-loss", "train",
        facts=("部分风机运行反馈丢失",),
        missing=("其余风机状态", "控制和电源状态"),
        uncertainty=("部分反馈丢失不能证明风机全停",),
        physical_fault="insufficient", evidence_sufficiency="insufficient",
        next_check="cooling_redundancy", urgency=1,
    ),
    _case(
        "11.3", "fan-no-thermal", "calibration",
        facts=("多个冷却塔风机运行反馈全部丢失",),
        missing=("进阀温度、流量和压力趋势",),
        uncertainty=("尚不清楚冷却能力是否已经下降",),
        physical_fault="candidate", evidence_sufficiency="insufficient",
        next_check="thermal_hydraulic", urgency=2,
    ),
    _case(
        "11.3", "fan-temperature-rising", "test",
        facts=("多个冷却塔风机运行反馈全部丢失", "内水冷进阀温度持续上升并接近跳闸温度"),
        missing=("调度和阀组当前运行边界",),
        uncertainty=("停运条件和授权前置条件需要人工确认",),
        physical_fault="candidate", evidence_sufficiency="sufficient",
        next_check="manual_review", urgency=3, data_completeness="complete_for_observation",
    ),
)


def _event_id(case_id: str, index: int) -> str:
    return "synthetic-event-" + hashlib.sha256(f"{case_id}:{index}".encode()).hexdigest()[:16]


def _synthetic_events(case: CaseSpec) -> list[dict[str, Any]]:
    start = datetime(2026, 9, 11, 12, 0, 0)
    events: list[dict[str, Any]] = []
    facts_to_signal = {
        "泄漏保护短时屏蔽条件出现": ("leak_protection_mask", "masked"),
        "出现24小时泄漏报警": ("inner_cooling_leak_alarm", "asserted"),
        "膨胀罐液位低报警出现": ("expansion_tank_low_level", "asserted"),
        "现场检查发现阀厅存在实际漏水": ("physical_cooling_water_leak", "asserted"),
        "内水冷主循环泵故障信号出现": ("main_cooling_pump_failure", "asserted"),
        "内水冷主循环泵异常": ("main_cooling_pump_failure", "asserted"),
        "备用循环泵投入并运行反馈正常": ("backup_pump_running", "asserted"),
        "备用循环泵未切至运行": ("backup_pump_not_running", "asserted"),
        "备用泵无法投入运行": ("backup_pump_not_running", "asserted"),
        "备用泵状态异常，但没有主泵异常证据": ("backup_pump_not_running", "asserted"),
        "一个阀外水冷冷却塔风机组的运行反馈全部丢失": ("fan_group_loss", "all_lost"),
        "部分风机运行反馈丢失": ("fan_group_loss", "partial"),
        "多个冷却塔风机运行反馈全部丢失": ("fan_group_loss", "all_lost"),
        "内水冷进阀温度持续上升并接近跳闸温度": ("inlet_temperature", "rising_near_trip"),
    }
    for index, fact in enumerate(case.facts):
        signal_id, observation = facts_to_signal.get(fact, ("context_observation", "observed"))
        events.append({
            "id": _event_id(case.case_id, index),
            "event_time": (start + timedelta(seconds=index * 7)).isoformat(timespec="milliseconds"),
            "station": "姑苏",
            "host": "P221VCC2",
            "channel": "A" if index % 2 == 0 else "B",
            "signal_id": signal_id,
            "observation": observation,
            "source": "procedure-derived-synthetic",
        })
    for index, transition in enumerate(case.transitions, len(events)):
        events.append({
            "id": _event_id(case.case_id, index),
            "event_time": (start + timedelta(seconds=index * 7)).isoformat(timespec="milliseconds"),
            "station": "姑苏",
            "host": "P221VCC2",
            "channel": "A" if index % 2 == 0 else "B",
            "signal_id": "state_transition",
            "observation": transition,
            "source": "procedure-derived-synthetic",
        })
    return events


def _build_semantic_state(case: CaseSpec, events: list[dict[str, Any]]) -> dict[str, Any]:
    signals = {event["signal_id"] for event in events}
    fan_all = "fan_group_loss" in signals and any(event["observation"] == "all_lost" for event in events)
    leak_candidate = bool(signals & {"leak_protection_mask", "inner_cooling_leak_alarm", "expansion_tank_low_level", "physical_cooling_water_leak"})
    pump_failure = "main_cooling_pump_failure" in signals
    backup_not_running = "backup_pump_not_running" in signals
    candidates = []
    if leak_candidate:
        candidates.append("10.3")
    if pump_failure:
        candidates.append("10.4")
    if pump_failure and backup_not_running:
        candidates.append("10.5")
    if fan_all:
        candidates.append("11.3")
    return {
        "time_scope": {
            "start": events[0]["event_time"],
            "end": events[-1]["event_time"],
            "event_count": len(events),
        },
        "observed_facts": list(case.facts),
        "transitions": list(case.transitions),
        "candidate_scenarios": candidates,
        "missing_evidence": list(case.missing),
        "uncertainty": list(case.uncertainty),
        "data_completeness": case.data_completeness,
        "source_event_ids": [event["id"] for event in events],
        "state_builder_version": STATE_BUILDER_VERSION,
    }


def _render_state(state: dict[str, Any]) -> str:
    facts = "；".join(state["observed_facts"]) + "。"
    transitions = "；".join(state["transitions"]) + "。" if state["transitions"] else ""
    missing = "、".join(state["missing_evidence"]) if state["missing_evidence"] else "无明确缺失项"
    uncertainty = "；".join(state["uncertainty"]) if state["uncertainty"] else "暂无额外不确定性。"
    return (
        f"在姑苏站 {state['time_scope']['start']} 至 {state['time_scope']['end']} 的监视窗口内，"
        f"状态构建器从 {state['time_scope']['event_count']} 条事件中归纳出：{facts}"
        f"{transitions}当前缺少或尚未确认的证据包括：{missing}。"
        f"需要注意：{uncertainty}"
    )


def _one_hot(options: Iterable[str], label: str) -> dict[str, float]:
    return {option: 1.0 if option == label else 0.0 for option in options}


def _gold_for(case: CaseSpec, state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    scenario_options = [
        "scenario_10_3_applicable", "scenario_10_4_applicable",
        "scenario_10_5_applicable", "scenario_11_3_applicable",
    ]
    gold: dict[str, dict[str, Any]] = {}
    for name in scenario_options:
        scenario = name.removeprefix("scenario_").removesuffix("_applicable").replace("_", ".")
        value = scenario in state["candidate_scenarios"]
        label = "true" if value else "false"
        gold[name] = {
            "type": "noul", "label": label,
            "probabilities": {"true": 1.0 if value else 0.0, "false": 0.0 if value else 1.0},
            "probability_true": 1.0 if value else 0.0,
        }
    choice_values = {
        "physical_fault": case.physical_fault,
        "evidence_sufficiency": case.evidence_sufficiency,
        "next_check": case.next_check,
    }
    for name, label in choice_values.items():
        options = QUESTION_SCHEMA[name]["criteria"]
        gold[name] = {"type": "choice", "label": label, "probabilities": _one_hot(options, label)}
    review = case.physical_fault != "insufficient" or case.evidence_sufficiency != "sufficient" or bool(state["missing_evidence"])
    gold["needs_human_review"] = {
        "type": "noul", "label": "true" if review else "false",
        "probabilities": {"true": 1.0 if review else 0.0, "false": 0.0 if review else 1.0},
        "probability_true": 1.0 if review else 0.0,
    }
    score_options = [str(index) for index in range(len(QUESTION_SCHEMA["urgency"]["criteria"]))]
    gold["urgency"] = {
        "type": "score", "label": str(case.urgency), "score": float(case.urgency),
        "probabilities": _one_hot(score_options, str(case.urgency)),
    }
    return gold


def _validate_row(row: dict[str, Any]) -> None:
    if set(row) != {"id", "state", "questions", "gold"}:
        raise ValueError(f"model row fields mismatch: {row['id']}")
    if not isinstance(row["state"], str) or not row["state"].strip():
        raise ValueError("state must be non-empty text")
    questions = json.loads(row["questions"]) if isinstance(row["questions"], str) else row["questions"]
    gold = json.loads(row["gold"]) if isinstance(row["gold"], str) else row["gold"]
    if set(questions) != set(gold) or set(questions) != set(QUESTION_SCHEMA):
        raise ValueError(f"questions/gold schema mismatch: {row['id']}")
    for name, question in questions.items():
        if question["type"] not in {"noul", "choice", "score"}:
            raise ValueError(f"unsupported question type: {name}")
        answer = gold[name]
        if answer.get("type") != question["type"]:
            raise ValueError(f"gold type mismatch: {name}")
        if question["type"] in {"noul", "choice"}:
            options = question["criteria"]
            if answer["label"] not in options and not (question["type"] == "noul" and answer["label"] in {"true", "false"}):
                raise ValueError(f"gold label not in criteria: {name}")
            probabilities = answer.get("probabilities")
            if set(probabilities or {}) != set(options if question["type"] == "choice" else {"true", "false"}):
                raise ValueError(f"gold probability keys mismatch: {name}")
            if abs(sum(probabilities.values()) - 1.0) > 1e-6:
                raise ValueError(f"gold probabilities do not sum to one: {name}")
        else:
            if str(answer["label"]) not in {str(i) for i in range(len(question["criteria"]))}:
                raise ValueError(f"score outside criteria: {name}")


def _clause_metadata() -> tuple[dict[str, Any], dict[str, Any]]:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    by_clause = {record["clause_id"]: record for record in inventory["records"]}
    required = {
        "10.3": ["10.3.1.1", "10.3.1.2", "10.3.2.1", "10.3.2.3", "10.3.2.9"],
        "10.4": ["10.4.1.1", "10.4.2.1"],
        "10.5": ["10.5.1.1", "10.5.2.1", "10.5.2.5"],
        "11.3": ["11.3.1.1", "11.3.1.2", "11.3.2.1", "11.3.2.3", "11.3.2.4"],
    }
    for scenario, clauses in required.items():
        for clause in clauses:
            if clause not in by_clause:
                raise ValueError(f"procedure clause missing from inventory: {scenario}/{clause}")
    return inventory["source"], {scenario: clauses for scenario, clauses in required.items()}


def _row_and_audit(case: CaseSpec, source: dict[str, Any], clauses: dict[str, list[str]]) -> tuple[dict[str, Any], dict[str, Any]]:
    events = _synthetic_events(case)
    structured = _build_semantic_state(case, events)
    state_text = _render_state(structured)
    gold = _gold_for(case, structured)
    row = {
        "id": case.case_id,
        "state": state_text,
        "questions": dumps(QUESTION_SCHEMA),
        "gold": dumps(gold),
    }
    _validate_row(row)
    audit = {
        "id": case.case_id,
        "split": case.split,
        "workflow": "gusu_valve_cooling_monitoring",
        "scenario": case.scenario,
        "variant": case.variant,
        "factors": {
            "observed_facts": list(case.facts),
            "transitions": list(case.transitions),
            "missing_evidence": list(case.missing),
            "physical_fault": case.physical_fault,
            "data_completeness": case.data_completeness,
        },
        "raw_events": events,
        "structured_state": structured,
        "procedure_clause_ids": clauses[case.scenario],
        "source": source,
        "source_type": "procedure-derived-synthetic",
        "gold_origin": "procedure_oracle_one_hot",
        "review_status": "source_checked_not_domain_expert_approved",
        "state_builder_version": STATE_BUILDER_VERSION,
        "expected_semantic_answers": {name: value["label"] for name, value in gold.items()},
    }
    return row, audit


def build(destination: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"output must be new or empty: {destination}")
    source, clauses = _clause_metadata()
    rows: dict[str, list[dict[str, Any]]] = {split: [] for split in SPLITS}
    audit: list[dict[str, Any]] = []
    for case in CASES:
        row, meta = _row_and_audit(case, source, clauses)
        rows[case.split].append(row)
        audit.append(meta)
    destination.mkdir(parents=True, exist_ok=True)
    for split, split_rows in rows.items():
        write_jsonl(destination / f"{split}.jsonl", split_rows)
    write_jsonl(destination / "audit.jsonl", audit)
    write_json(destination / "schema.json", {"schema_version": SCHEMA_VERSION, "questions": QUESTION_SCHEMA})
    write_json(destination / "source.json", {"procedure_inventory": str(INVENTORY.relative_to(ROOT)), "source": source, "clauses": clauses})
    manifest = {
        "dataset": DATASET_NAME,
        "schema_version": SCHEMA_VERSION,
        "purpose": "procedure_derived_semantic_state_typed_decision_seed",
        "review_status": "source_checked_synthetic_not_expert_approved",
        "state_input": "natural_language_semantic_state_only",
        "audit_excluded_from_model_input": True,
        "state_builder_version": STATE_BUILDER_VERSION,
        "scenario_scope": sorted({case.scenario for case in CASES}),
        "split_unit": "case_id; generated variants are grouped and never duplicated across splits",
        "files": {},
        "source_sha256": source["sha256"],
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
    for filename, expected in manifest["files"].items():
        if digest(destination / filename) != expected:
            raise ValueError(f"snapshot hash mismatch: {filename}")
    audit = {row["id"]: row for row in read_jsonl(destination / "audit.jsonl")}
    ids: set[str] = set()
    counts: dict[str, Any] = {}
    state_fingerprints: dict[str, str] = {}
    for split in SPLITS:
        rows = read_jsonl(destination / f"{split}.jsonl")
        if not rows:
            raise ValueError(f"empty split: {split}")
        for row in rows:
            _validate_row(row)
            if row["id"] in ids or row["id"] not in audit:
                raise ValueError(f"audit/model ID mismatch: {row['id']}")
            ids.add(row["id"])
            if audit[row["id"]]["split"] != split:
                raise ValueError(f"split mismatch: {row['id']}")
            fingerprint = hashlib.sha256(row["state"].encode()).hexdigest()
            if fingerprint in state_fingerprints and state_fingerprints[fingerprint] != split:
                raise ValueError("state appears across splits")
            state_fingerprints[fingerprint] = split
        counts[split] = {"rows": len(rows), "decisions": len(rows) * len(QUESTION_SCHEMA)}
    if ids != set(audit):
        raise ValueError("audit rows do not exactly cover model rows")
    return {
        "status": "passed",
        "counts": counts,
        "total_rows": len(ids),
        "decisions_per_row": len(QUESTION_SCHEMA),
        "source_sha256": manifest["source_sha256"],
        "limitations": [
            "Synthetic procedure-derived cases; no expert approval or field fault-rate claim.",
            "Gold is a deterministic procedure oracle, not a teacher-model probability distribution.",
            "State compiler is deterministic in this seed; LLM paraphrase variants are not included.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="生成规程五驱动的语义状态 Typed Decisions 数据集种子")
    parser.add_argument("command", choices=("build", "verify"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.output) if args.command == "build" else verify(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
