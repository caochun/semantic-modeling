"""Evidence-first phenomenon recognition for Gusu valve-cooling logs.

This module is deliberately a small, auditable baseline.  It does not train a
model and it does not turn an alarm into a claim that equipment is damaged.  It
keeps two meanings that are easy to conflate separate:

* ``alarm_edge`` is the event-record edge (the alarm appeared or cleared).
* ``observation`` is what the message says about the equipment (for example,
  fan running feedback was lost).

The output is intended to become the ``state`` and evidence graph supplied to
later rule traversal or a Decision Model.  Unknown phrases remain unknown.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
CASE_DIR = ROOT / "experiments/decision_model/cases/gusu-valve-cooling-20260911"
DEFAULT_INPUT = CASE_DIR / "raw.tsv"

_EDGE_RE = re.compile(r"(?P<body>.*?)(?P<edge>出现|消失)\s*$")
_FAN_RE = re.compile(r"(?P<component>E\d+\.M\d+)风机运行(?P<state>出现|消失)$")
_FAN_BASE_RE = re.compile(r"(?P<component>E\d+\.M\d+)风机运行$")
_MASK_RE = re.compile(r"泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽(?P<state>出现|消失)$")
_MASK_BASE_RE = re.compile(r"泄漏保护屏蔽条件满足，泄漏保护短时被屏蔽$")
_LEAK_ALARM_RE = re.compile(r"(?:24小时泄漏报警|渗漏报警|泄漏报警)")
_LOW_LEVEL_RE = re.compile(r"(?:高位水箱|膨胀罐)(?:水位|液位)(?:低|超低)")
_PHYSICAL_LEAK_RE = re.compile(r"(?:内水冷|阀厅|冷却塔|主循环泵).*(?:漏水|泄漏)")
_PUMP_FAILURE_RE = re.compile(r"(?:主水循环泵|主循环泵|主泵).*(?:故障|异常)")
_BACKUP_OK_RE = re.compile(r"(?:备用泵|备用循环泵).*(?:投入|运行正常)")
_BACKUP_MISSING_RE = re.compile(r"(?:备用泵|备用循环泵).*(?:未投入|未切至|不能投入|未能切)")

FAN_GROUPS = {
    "M01_M03_M05": {"M01", "M03", "M05"},
    "M02_M04_M06": {"M02", "M04", "M06"},
}


@dataclass(frozen=True)
class NormalizedEvent:
    id: str
    event_time: str
    station: str
    host: str
    channel: str
    device: str
    content: str
    message: str
    alarm_level: str
    alarm_edge: str | None
    signal_id: str | None
    observation: str | None
    component: str | None
    mapping_status: str
    raw: dict[str, Any]


def _text(row: Mapping[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _iso(value: str) -> str:
    value = value.strip().replace(" ", "T", 1)
    return datetime.fromisoformat(value).isoformat(timespec="milliseconds")


def _message_without_device_prefix(content: str, device: str) -> str:
    value = content.strip()
    prefix = f"{device}_"
    if value.startswith(prefix):
        return value[len(prefix) :]
    # Some historical exports use an unqualified message; do not remove a
    # substring in the middle because it could be meaningful text.
    return value


def _parse_message(message: str) -> tuple[str | None, str | None, str | None, str]:
    """Return signal, observation, component and mapping status."""
    match = _EDGE_RE.fullmatch(message)
    body, alarm_edge = (match.group("body").strip(), match.group("edge")) if match else (message, None)

    fan = _FAN_RE.fullmatch(body)
    if fan:
        state = fan.group("state")
        return (
            "fan_running_feedback",
            "running" if state == "出现" else "not_running",
            fan.group("component"),
            "exact",
        )

    # Some exports carry only one edge (``风机运行消失``), while the station
    # alarm export used in the held-out case repeats it (``消失 消失``).  In
    # the former form the edge removed above supplies the observation state.
    fan_base = _FAN_BASE_RE.fullmatch(body)
    if fan_base and alarm_edge:
        return (
            "fan_running_feedback",
            "running" if alarm_edge == "出现" else "not_running",
            fan_base.group("component"),
            "exact",
        )

    mask = _MASK_RE.fullmatch(body)
    if mask:
        return (
            "leak_protection_mask",
            "masked" if mask.group("state") == "出现" else "unmasked",
            None,
            "exact",
        )

    if _MASK_BASE_RE.fullmatch(body) and alarm_edge:
        return (
            "leak_protection_mask",
            "masked" if alarm_edge == "出现" else "unmasked",
            None,
            "exact",
        )

    if _LEAK_ALARM_RE.search(body):
        return "inner_cooling_leak_alarm", "asserted", None, "procedure_phrase"
    if _LOW_LEVEL_RE.search(body):
        return "expansion_tank_low_level", "asserted", None, "procedure_phrase"
    if _PHYSICAL_LEAK_RE.search(body):
        return "physical_cooling_water_leak", "asserted", None, "procedure_phrase"
    if _PUMP_FAILURE_RE.search(body):
        return "main_cooling_pump_failure", "asserted", None, "procedure_phrase"
    if _BACKUP_OK_RE.search(body):
        return "backup_pump_running", "asserted", None, "procedure_phrase"
    if _BACKUP_MISSING_RE.search(body):
        return "backup_pump_not_running", "asserted", None, "procedure_phrase"
    return None, None, None, "unknown"


def normalize_event(row: Mapping[str, Any], index: int = 0) -> NormalizedEvent:
    """Normalize a raw dict from TSV/JSON without discarding its source text."""
    event_time = _iso(_text(row, "event_time", "time", "timestamp"))
    station = _text(row, "station", default="姑苏")
    host = _text(row, "host")
    channel = _text(row, "system_alarm", "channel", "alarm_system")
    device = _text(row, "device", "alarm_group")
    content = _text(row, "content", "event", "message")
    alarm_level = _text(row, "alarm_level", "event_level_text", "level")
    message = _message_without_device_prefix(content, device)
    signal_id, observation, component, mapping_status = _parse_message(message)
    identity = {
        "event_time": event_time,
        "station": station,
        "host": host,
        "channel": channel,
        "device": device,
        "content": content,
        "alarm_level": alarm_level,
        "index": index,
    }
    event_id = "event-" + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return NormalizedEvent(
        id=event_id,
        event_time=event_time,
        station=station,
        host=host,
        channel=channel,
        device=device,
        content=content,
        message=message,
        alarm_level=alarm_level,
        alarm_edge=next((x for x in ("出现", "消失") if message.endswith(x)), None),
        signal_id=signal_id,
        observation=observation,
        component=component,
        mapping_status=mapping_status,
        raw=dict(row),
    )


def read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if path.suffix.lower() == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else value["events"]
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _sort_events(events: Iterable[NormalizedEvent]) -> list[NormalizedEvent]:
    return sorted(events, key=lambda event: (event.event_time, event.id))


def _event_dict(event: NormalizedEvent) -> dict[str, Any]:
    return asdict(event)


def _evidence_ids(events: Iterable[NormalizedEvent], signal_id: str) -> list[str]:
    return [event.id for event in events if event.signal_id == signal_id]


def _fact(
    *,
    fact_id: str,
    status: str,
    description: str,
    events: Iterable[NormalizedEvent],
    signal_ids: Iterable[str] = (),
    missing: Iterable[str] = (),
    details: Mapping[str, Any] | None = None,
    source_refs: Iterable[str] = (),
) -> dict[str, Any]:
    event_list = list(events)
    signal_set = set(signal_ids)
    evidence = [event.id for event in event_list if event.signal_id in signal_set]
    return {
        "id": fact_id,
        "status": status,
        "description": description,
        "evidence_event_ids": evidence,
        "missing": list(missing),
        "details": dict(details or {}),
        "source_refs": list(source_refs),
    }


def _fan_group_facts(events: list[NormalizedEvent]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for host, channel, device in sorted({(e.host, e.channel, e.device) for e in events}):
        lost = {
            e.component.rsplit(".", 1)[-1]
            for e in events
            if e.host == host
            and e.channel == channel
            and e.device == device
            and e.signal_id == "fan_running_feedback"
            and e.observation == "not_running"
            and e.component
        }
        for group, members in FAN_GROUPS.items():
            seen = lost & members
            if not seen:
                continue
            status = "supported" if seen == members else "partial"
            evidence = [
                e.id
                for e in events
                if e.host == host
                and e.channel == channel
                and e.device == device
                and e.component
                and e.component.rsplit(".", 1)[-1] in seen
                and e.signal_id == "fan_running_feedback"
            ]
            facts.append(
                {
                    # A graph node must identify one concrete group.  Reusing
                    # the same id for A/B channels would merge independent
                    # evidence paths and make the graph impossible to audit.
                    "id": f"outer_cooling_fan_group_loss:{host}:{channel}:{group}",
                    "status": status,
                    "description": "阀冷风机组运行反馈全部丢失" if status == "supported" else "阀冷风机组部分风机运行反馈丢失",
                    "evidence_event_ids": evidence,
                    "missing": sorted(members - seen),
                    "details": {
                        "host": host,
                        "channel": channel,
                        "device": device,
                        "group": group,
                        "components_seen": sorted(seen),
                    },
                    "source_refs": ["规程三第10章变频风机运行规定", "规程五第11.3节"],
                }
            )
    return facts


def analyze_events(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    normalized = _sort_events(normalize_event(row, index) for index, row in enumerate(rows))
    if not normalized:
        raise ValueError("输入没有事件")
    known = [event for event in normalized if event.signal_id]
    unknown = [event for event in normalized if not event.signal_id]
    facts: list[dict[str, Any]] = []

    fan_events = [event for event in normalized if event.signal_id == "fan_running_feedback"]
    fan_loss_events = [event for event in fan_events if event.observation == "not_running"]
    if fan_loss_events:
        facts.append(
            _fact(
                fact_id="fan_running_feedback_loss",
                status="supported",
                description="日志明确表达了一个或多个风机运行反馈丢失",
                events=fan_loss_events,
                signal_ids=("fan_running_feedback",),
                details={
                    "components": sorted({event.component for event in fan_loss_events if event.component}),
                    "host_channels": sorted({f"{event.host}/{event.channel}" for event in fan_loss_events}),
                    "alarm_edges": sorted({event.alarm_edge for event in fan_loss_events}),
                    "interpretation": "内容表达的运行反馈丢失与事件记录的报警边沿分开保存；报警消失不等于物理状态已恢复。",
                },
                source_refs=("规程三第10章变频风机运行规定", "规程五第11.1/11.3节"),
            )
        )

    mask_events = [event for event in normalized if event.signal_id == "leak_protection_mask"]
    if mask_events:
        active = [event for event in mask_events if event.observation == "masked"]
        cleared = [event for event in mask_events if event.observation == "unmasked"]
        facts.append(
            _fact(
                fact_id="leak_protection_masked",
                status="supported" if active else "insufficient_evidence",
                description=(
                    "日志明确表达泄漏保护短时被屏蔽条件出现"
                    if active
                    else "日志只记录了泄漏保护屏蔽条件的消失，未见对应出现记录"
                ),
                events=normalized,
                signal_ids=("leak_protection_mask",),
                missing=("实际泄漏或水位变化的独立证据",),
                details={
                    "assertion_count": len(active),
                    "clear_count": len(cleared),
                    "cleared_in_window": bool(cleared),
                    "interpretation": "保护屏蔽条件是运行/保护状态事实，不直接证明发生泄漏。",
                },
                source_refs=("规程五第10.3节", "规程五第11.3节"),
            )
        )

    leak_signals = {"inner_cooling_leak_alarm", "expansion_tank_low_level", "physical_cooling_water_leak"}
    leak_events = [event for event in normalized if event.signal_id in leak_signals]
    leak_status = "suspected" if leak_events else "insufficient_evidence"
    facts.append(
        _fact(
            fact_id="inner_cooling_water_leak",
            status=leak_status,
            description="内水冷泄漏处置预案的现象候选",
            events=normalized,
            signal_ids=leak_signals,
            missing=()
            if leak_events
            else (
                "24小时泄漏报警或高位水箱/膨胀罐水位低",
                "实际漏水或水位变化趋势",
                "阀厅、冷却塔或主循环泵现场检查结果",
            ),
            details={
                "procedure": "规程五 10.3 内水冷泄漏处理预案",
                "interpretation": "没有泄漏现象证据时，不因保护屏蔽条件或风机反馈丢失直接确认内水冷泄漏。",
            },
            source_refs=("规程五第10.3节",),
        )
    )

    pump_events = [
        event
        for event in normalized
        if event.signal_id in {"main_cooling_pump_failure", "backup_pump_running", "backup_pump_not_running"}
    ]
    if pump_events:
        pump_failure = any(event.signal_id == "main_cooling_pump_failure" for event in pump_events)
        backup_ok = any(event.signal_id == "backup_pump_running" for event in pump_events)
        backup_missing = any(event.signal_id == "backup_pump_not_running" for event in pump_events)
        facts.append(
            _fact(
                fact_id="inner_cooling_main_pump_branch",
                status="supported" if pump_failure else "insufficient_evidence",
                description="内水冷主循环泵故障及备用泵状态分支",
                events=normalized,
                signal_ids={event.signal_id for event in pump_events if event.signal_id},
                missing=() if backup_ok or backup_missing else ("备用泵是否投入运行",),
                details={
                    "procedure": "规程五 10.4/10.5",
                    "backup_status": "running" if backup_ok else "not_running" if backup_missing else "unknown",
                },
                source_refs=("规程五第10.4/10.5节",),
            )
        )

    facts.extend(_fan_group_facts(normalized))

    fan_groups = [
        {
            "id": fact["id"],
            "status": fact["status"],
            "host": fact["details"]["host"],
            "channel": fact["details"]["channel"],
            "device": fact["details"]["device"],
            "group": fact["details"]["group"],
            "components_seen": fact["details"]["components_seen"],
            "missing": fact["missing"],
            "evidence_event_ids": fact["evidence_event_ids"],
        }
        for fact in facts
        if fact["id"].startswith("outer_cooling_fan_group_loss:")
    ]

    graph_nodes = [
        {"id": event.id, "type": "raw_event", "label": event.content}
        for event in normalized
    ]
    graph_nodes.extend(
        {"id": fact["id"], "type": "phenomenon", "label": fact["description"]}
        for fact in facts
    )
    graph_edges: list[dict[str, Any]] = []
    for fact in facts:
        relation = "supports" if fact["status"] in {"supported", "suspected", "partial"} else "insufficient_for"
        for event_id in fact["evidence_event_ids"]:
            graph_edges.append({"source": event_id, "target": fact["id"], "relation": relation})

    state = {
        "scope": "姑苏阀冷日志现象识别原型",
        "window": {
            "start": normalized[0].event_time,
            "end": normalized[-1].event_time,
            "event_count": len(normalized),
        },
        "devices": sorted({event.device for event in normalized}),
        "hosts_channels": sorted({f"{event.host}/{event.channel}" for event in normalized}),
        "observed_signals": [
            {
                "signal_id": event.signal_id,
                "observation": event.observation,
                "component": event.component,
                "host": event.host,
                "channel": event.channel,
                "event_time": event.event_time,
                "alarm_edge": event.alarm_edge,
                "evidence_event_id": event.id,
            }
            for event in known
        ],
        "fan_groups": fan_groups,
        "unknown_event_ids": [event.id for event in unknown],
        "data_completeness": {
            "raw_events_preserved": True,
            "initial_full_state_available": False,
            "terminal_full_state_available": False,
            "telemetry_trends_available": False,
        },
    }
    return {
        "schema_version": "phenomenon-recognition.v1",
        "source": "procedure-five-derived-prototype",
        "physical_fault_confirmed": False,
        "state": state,
        "events": [_event_dict(event) for event in normalized],
        "phenomena": facts,
        "evidence_graph": {"nodes": graph_nodes, "edges": graph_edges},
        "summary": {
            "known_event_count": len(known),
            "unknown_event_count": len(unknown),
            "phenomenon_ids": [fact["id"] for fact in facts],
            "physical_fault_confirmed": False,
            "interpretation": "现象证据识别结果，不是现场故障确认或自动操作指令。",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="从姑苏阀冷日志识别规程现象和证据")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze_events(read_rows(args.input))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
