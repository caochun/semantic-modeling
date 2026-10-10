"""Build a task-level semantic state from raw monitoring input."""
from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

try:
    from dotenv import load_dotenv
except ImportError:  # standalone GPU bundle may not include python-dotenv
    load_dotenv = None


SYSTEM_PROMPT = r"""
你是换流站监盘系统的状态构建器。把原始日志、表格或 JSON 整理为任务级语义 state，供后续
Decision Model 判断。你不是故障诊断器，也不输出规程编号、金标准标签、自动操作命令或未经
证据支持的物理故障结论。

严格遵守：
1. 只记录输入中可观察到的事实，区分事件边沿（出现/消失）和信号表达的设备状态。
2. 报警消失只表示报警边沿消失，不能解释为设备恢复；保护屏蔽不能解释为真实泄漏。
3. 按站点、主机、设备、A/B 通道和组件保留边界，不要随意合并。
4. 没有记录某信号，不得写成该信号正常，把它放入 missing_evidence。
5. unknown、冲突边沿、时间缺失和无法判定的含义必须保留为不确定性。
6. state_text 只包含观察事实、变化、缺失证据和不确定性，不给出已确认故障或操作命令。
7. evidence_event_ids 必须引用可以定位的输入行号，例如 row-1、row-2。

只返回一个合法 JSON 对象，不要 Markdown 代码围栏。结构：
{
  "window": {"start": "", "end": "", "event_count": 0},
  "observations": [
    {"id": "obs-1", "description": "", "entity": "", "status": "observed|unknown|conflicting",
     "evidence_event_ids": ["row-1"], "evidence_type": "single|aggregated|transition"}
  ],
  "transitions": [{"description": "", "evidence_event_ids": ["row-1", "row-2"]}],
  "aggregated_phenomena": [""], "missing_evidence": [""], "uncertainties": [""],
  "data_quality": {"unknown_event_count": 0, "has_initial_full_state": false,
                   "has_terminal_full_state": false, "has_continuous_trends": false},
  "state_text": ""
}
""".strip()


class StateBuilderError(RuntimeError):
    pass


def serialize_logs(logs: str | list[dict[str, Any]] | dict[str, Any]) -> str:
    value = logs.strip() if isinstance(logs, str) else json.dumps(logs, ensure_ascii=False, indent=2)
    if not value:
        raise StateBuilderError("原始日志不能为空")
    if len(value) > 240_000:
        raise StateBuilderError("原始日志超过 240000 字符，请缩短日志窗口后重试")
    return value


def _strip_json_fence(value: str) -> str:
    value = value.strip()
    marker = chr(96) * 3
    if value.startswith(marker):
        value = re.sub(r"^" + marker + r"(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*" + marker + r"$", "", value)
    return value.strip()


def _as_list(value: Any, limit: int = 40) -> list[Any]:
    return value[:limit] if isinstance(value, list) else []


def _text_list(value: Any, limit: int = 40) -> list[str]:
    result: list[str] = []
    for item in _as_list(value, limit):
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        elif isinstance(item, Mapping):
            text = str(item.get("description") or item.get("text") or "").strip()
            if text:
                result.append(text)
    return list(dict.fromkeys(result))


def _event_count(raw: str) -> int:
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return len(parsed)
        if isinstance(parsed, dict) and isinstance(parsed.get("events"), list):
            return len(parsed["events"])
    except json.JSONDecodeError:
        pass
    lines = [line for line in raw.splitlines() if line.strip()]
    return max(0, len(lines) - 1) if len(lines) > 1 else len(lines)


def _render_state(data: Mapping[str, Any], station: str, raw: str) -> str:
    text = str(data.get("state_text") or "").strip()
    if text:
        boundary = "该状态只描述日志证据，不等同于物理故障确认或操作指令。"
        return text if boundary in text else text.rstrip("。；; ") + "。" + boundary
    window = data.get("window") if isinstance(data.get("window"), Mapping) else {}
    facts = _text_list(data.get("observations")) + _text_list(data.get("transitions"))
    missing = _text_list(data.get("missing_evidence"))
    uncertainty = _text_list(data.get("uncertainties"))
    return (
        f"在{station}站 {window.get('start', 'unknown')} 至 {window.get('end', 'unknown')} 的窗口内，"
        f"共归纳 {window.get('event_count', _event_count(raw))} 条事件。"
        f"观察事实：{'；'.join(facts) if facts else '暂无可直接确认的事实'}。"
        f"缺失证据：{'；'.join(missing) if missing else '暂无明确缺失项'}。"
        f"不确定性：{'；'.join(uncertainty) if uncertainty else '暂无额外不确定性'}。"
        "该状态只描述日志证据，不等同于物理故障确认或操作指令。"
    )


def normalize_state(data: Any, *, station: str, raw_logs: str) -> tuple[str, dict[str, Any], list[str]]:
    if not isinstance(data, Mapping):
        raise StateBuilderError("GLM 返回的 state 不是 JSON 对象")
    window = data.get("window") if isinstance(data.get("window"), Mapping) else {}
    quality = data.get("data_quality") if isinstance(data.get("data_quality"), Mapping) else {}
    count = _event_count(raw_logs)
    normalized_window = {
        "start": str(window.get("start") or "unknown"),
        "end": str(window.get("end") or "unknown"),
        "event_count": int(window.get("event_count") or count),
    }
    observations = []
    for index, item in enumerate(_as_list(data.get("observations"))):
        if isinstance(item, str):
            item = {"description": item}
        if not isinstance(item, Mapping):
            continue
        description = str(item.get("description") or "").strip()
        if not description:
            continue
        observations.append({
            "id": str(item.get("id") or f"obs-{index + 1}"),
            "description": description,
            "entity": str(item.get("entity") or "unknown"),
            "status": str(item.get("status") or "unknown"),
            "evidence_event_ids": [str(x) for x in _as_list(item.get("evidence_event_ids"), 30)],
            "evidence_type": str(item.get("evidence_type") or "single"),
        })
    transitions = []
    for item in _as_list(data.get("transitions")):
        if isinstance(item, str):
            item = {"description": item}
        if isinstance(item, Mapping) and str(item.get("description") or "").strip():
            transitions.append({
                "description": str(item["description"]).strip(),
                "evidence_event_ids": [str(x) for x in _as_list(item.get("evidence_event_ids"), 30)],
            })
    warnings: list[str] = []
    state_text_candidate = str(data.get("state_text") or "")
    if any(term in state_text_candidate for term in ("已确认故障", "执行操作", "立即停运")):
        warnings.append("模型摘要包含结论或操作措辞，请人工核对并编辑 State")
    if not observations:
        warnings.append("模型没有提取出可核对的观察事实")
    unknown_count = int(quality.get("unknown_event_count") or 0)
    if unknown_count:
        warnings.append(f"有 {unknown_count} 条事件无法可靠归类")
    missing = _text_list(data.get("missing_evidence"))
    if not missing:
        warnings.append("模型没有列出缺失证据，请人工检查日志覆盖范围")
    normalized = {
        "schema_version": "task-semantic-state.v2",
        "window": normalized_window,
        "observations": observations,
        "transitions": transitions,
        "aggregated_phenomena": _text_list(data.get("aggregated_phenomena")),
        "missing_evidence": missing,
        "uncertainties": _text_list(data.get("uncertainties")),
        "data_quality": {
            "unknown_event_count": unknown_count,
            "has_initial_full_state": bool(quality.get("has_initial_full_state", False)),
            "has_terminal_full_state": bool(quality.get("has_terminal_full_state", False)),
            "has_continuous_trends": bool(quality.get("has_continuous_trends", False)),
        },
        "physical_fault_status": "not_confirmed_by_state_builder",
    }
    state_text = _render_state({**normalized, "state_text": data.get("state_text")}, station, raw_logs)
    return state_text, normalized, warnings


class GLMStateBuilder:
    def __init__(self, *, api_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, reasoning_effort: str | None = None,
                 timeout: float | None = None):
        if load_dotenv:
            load_dotenv()
            load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        self.api_url = (api_url or os.environ.get("GLM_API_URL", "")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("GLM_API_KEY", os.environ.get("ZHIPU_API_KEY", ""))
        self.model = model or os.environ.get("GLM_MODEL", "glm-5.3")
        self.reasoning_effort = reasoning_effort or os.environ.get("GLM_STATE_REASONING_EFFORT", "low")
        if timeout is not None:
            self.timeout = timeout
        else:
            timeout_ms = float(os.environ.get("GLM_API_TIMEOUT_MS", "180000"))
            self.timeout = None if timeout_ms <= 0 else timeout_ms / 1000

    @property
    def endpoint(self) -> str:
        if self.api_url.endswith("/chat/completions"):
            return self.api_url
        parsed = urlsplit(self.api_url)
        suffix = "/v1/chat/completions" if not parsed.path.strip("/") else "/chat/completions"
        return self.api_url + suffix

    def _request_parts(self, *, logs: str | list[dict[str, Any]] | dict[str, Any], task: str,
                       station: str, context: dict[str, Any] | None = None) -> tuple[str, dict[str, Any], dict[str, str]]:
        raw = serialize_logs(logs)
        if not self.api_url or not self.api_key:
            raise StateBuilderError("尚未配置 GLM_API_URL 或 GLM_API_KEY")
        user = {"task": task, "station": station, "context": context or {}, "raw_logs": raw,
                "row_number_rule": "如果输入是表格，数据行从 row-1 开始编号；表头不是事件。"}
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "max_tokens": int(os.environ.get("GLM_STATE_MAX_TOKENS", "3000")),
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "Authorization": "Bearer " + self.api_key}
        return raw, payload, headers

    @staticmethod
    def _piece_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, Mapping):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return ""

    @staticmethod
    def _parse_body(body: Mapping[str, Any], *, station: str, raw: str, model: str) -> dict[str, Any]:
        try:
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(_strip_json_fence(str(content or "")))
        except (ValueError, KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
            raise StateBuilderError("GLM 未返回可解析的 JSON state") from exc
        state_text, structured, warnings = normalize_state(parsed, station=station, raw_logs=raw)
        usage = body.get("usage") if isinstance(body, Mapping) else None
        return {"state": state_text, "structured_state": structured, "warnings": warnings,
                "usage": usage or {}, "model": model}

    async def build(self, *, logs: str | list[dict[str, Any]] | dict[str, Any], task: str,
                    station: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        raw, payload, headers = self._request_parts(logs=logs, task=task, station=station, context=context)
        payload["stream"] = False
        try:
            request_timeout = None if self.timeout is None else httpx.Timeout(self.timeout, connect=20)
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                response = await client.post(self.endpoint, headers=headers, json=payload)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise StateBuilderError("GLM 连接超时或中断，请重试") from exc
        if response.status_code >= 400:
            hints = {401: "GLM API Key 无效", 403: "GLM 模型或接口没有权限", 404: "GLM_API_URL 路径不正确", 429: "GLM 接口限流"}
            raise StateBuilderError(f"{hints.get(response.status_code, 'GLM 请求失败')}（HTTP {response.status_code}）")
        return self._parse_body(response.json(), station=station, raw=raw, model=self.model)

    async def build_stream(self, *, logs: str | list[dict[str, Any]] | dict[str, Any], task: str,
                           station: str, context: dict[str, Any] | None = None) -> AsyncIterator[dict[str, Any]]:
        """Stream GLM deltas, then yield the normalized task State."""
        raw, payload, headers = self._request_parts(logs=logs, task=task, station=station, context=context)
        payload["stream"] = True
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: dict[str, Any] = {}
        # GLM may emit one SSE message per token. Forwarding every token to the
        # browser makes the UI spend most of its time updating a growing <pre>
        # element and can cause an intermediary to buffer/drop the response.
        # Keep the model response intact for parsing, but forward small batches
        # as feedback to the client.
        pending_channel: str | None = None
        pending_parts: list[str] = []
        pending_size = 0

        async def flush_pending() -> AsyncIterator[dict[str, Any]]:
            nonlocal pending_channel, pending_parts, pending_size
            if pending_parts:
                text = "".join(pending_parts)
                channel = pending_channel or "content"
                pending_channel = None
                pending_parts = []
                pending_size = 0
                yield {"kind": "delta", "channel": channel, "text": text}

        async def forward_piece(channel: str, piece: str) -> AsyncIterator[dict[str, Any]]:
            nonlocal pending_channel, pending_size
            if pending_channel is not None and pending_channel != channel:
                async for item in flush_pending():
                    yield item
            pending_channel = channel
            pending_parts.append(piece)
            pending_size += len(piece)
            if pending_size >= 160:
                async for item in flush_pending():
                    yield item
        try:
            request_timeout = None if self.timeout is None else httpx.Timeout(self.timeout, connect=20)
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                async with client.stream("POST", self.endpoint, headers=headers, json=payload) as response:
                    if response.status_code >= 400:
                        hints = {401: "GLM API Key 无效", 403: "GLM 模型或接口没有权限", 404: "GLM_API_URL 路径不正确", 429: "GLM 接口限流"}
                        raise StateBuilderError(f"{hints.get(response.status_code, 'GLM 请求失败')}（HTTP {response.status_code}）")
                    content_type = response.headers.get("content-type", "")
                    if "text/event-stream" not in content_type:
                        body = json.loads((await response.aread()).decode("utf-8"))
                        message = body.get("choices", [{}])[0].get("message", {})
                        piece = self._piece_text(message.get("content"))
                        if piece:
                            content_parts.append(piece)
                            async for item in forward_piece("content", piece):
                                yield item
                        usage = body.get("usage") or {}
                    else:
                        async for line in response.aiter_lines():
                            line = line.strip()
                            if not line or line.startswith(":"):
                                continue
                            if line.startswith("data:"):
                                line = line[5:].strip()
                            if line == "[DONE]":
                                break
                            try:
                                item = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            usage = item.get("usage") or usage
                            choices = item.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            for channel, key, target in (
                                ("reasoning", "reasoning_content", reasoning_parts),
                                ("reasoning", "reasoning", reasoning_parts),
                                ("content", "content", content_parts),
                            ):
                                piece = self._piece_text(delta.get(key))
                                if piece:
                                    target.append(piece)
                                    async for item in forward_piece(channel, piece):
                                        yield item
        except StateBuilderError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise StateBuilderError("GLM 流式连接超时或中断，请重试") from exc
        async for item in flush_pending():
            yield item
        content = "".join(content_parts).strip()
        if not content:
            content = "".join(reasoning_parts).strip()
        try:
            parsed = json.loads(_strip_json_fence(content))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise StateBuilderError("GLM 流式输出结束，但未返回可解析的 JSON state") from exc
        state_text, structured, warnings = normalize_state(parsed, station=station, raw_logs=raw)
        yield {"kind": "complete", "result": {"state": state_text, "structured_state": structured,
                "warnings": warnings, "usage": usage, "model": self.model}}
