import asyncio
import json
import socket

import httpx


class ModelError(RuntimeError):
    pass


_original_getaddrinfo = socket.getaddrinfo


def ipv4_first(*args, **kwargs):
    return sorted(_original_getaddrinfo(*args, **kwargs), key=lambda item: item[0] != socket.AF_INET)


class GLMClient:
    def __init__(self, settings):
        self.settings = settings
        if settings.ipv4_first:
            socket.getaddrinfo = ipv4_first

    async def chat(self, messages, tools=None, *, review=False, on_delta=None):
        """Consume provider SSE; retain reasoning only in the private model conversation.

        on_delta receives public content/tool-call snapshots, never reasoning text.
        Once any chunk arrives, connection failures are not retried: partial output
        must not be silently combined with a second generation.
        """
        settings = self.settings
        if not settings.api_url:
            raise ModelError("请在 .env 设置 GLM_API_URL")
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if settings.api_key:
            headers["Authorization"] = "Bearer " + settings.api_key
        payload = {"model": settings.model, "messages": messages,
                   "stream": True, "stream_options": {"include_usage": True}}
        if settings.max_output_tokens:
            payload["max_tokens"] = settings.max_output_tokens
        if settings.thinking_enabled is not None:
            payload["thinking"] = {"type": "enabled" if settings.thinking_enabled else "disabled"}
        if settings.reasoning_effort:
            payload["reasoning_effort"] = settings.reasoning_effort
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        if review:
            payload["response_format"] = {"type": "json_object"}
        timeout = httpx.Timeout(settings.timeout, connect=20)
        for attempt in range(2):
            received = False
            message = {"role": "assistant", "content": ""}
            calls, usage, finish_reason = {}, {}, None
            try:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    async with client.stream("POST", settings.endpoint, headers=headers, json=payload) as response:
                        if response.status_code in {429, 502, 503, 504} and attempt == 0:
                            await asyncio.sleep(2)
                            continue
                        if response.is_error:
                            hints = {401: "认证失败，请检查 GLM_API_KEY", 403: "模型或接口权限不足",
                                     404: "请检查 GLM_API_URL 的 API 路径", 429: "接口限流，请稍后重试"}
                            detail = hints.get(response.status_code, "模型请求失败，请检查接口兼容性与参数")
                            raise ModelError(f"GLM HTTP {response.status_code}：{detail}")
                        # Some compatible proxies ignore stream=true. Still accept a
                        # complete JSON response, without pretending it was streamed.
                        if "application/json" in response.headers.get("content-type", ""):
                            data = json.loads(await response.aread())
                            choice = data["choices"][0]
                            message = choice["message"]
                            finish_reason = choice.get("finish_reason")
                            usage = data.get("usage") or {}
                            if on_delta:
                                await on_delta({k: v for k, v in message.items() if k in {"content", "tool_calls"}})
                        else:
                            async for line in response.aiter_lines():
                                if not line.startswith("data:"):
                                    continue
                                raw = line[5:].strip()
                                if not raw:
                                    continue
                                if raw == "[DONE]":
                                    break
                                chunk = json.loads(raw)
                                if "error" in chunk:
                                    raise ModelError("模型流返回错误，请稍后重试")
                                first = not received
                                received = True
                                if chunk.get("usage"):
                                    usage = chunk["usage"]
                                choices = chunk.get("choices") or []
                                if not choices:
                                    continue
                                choice = choices[0]
                                finish_reason = choice.get("finish_reason") or finish_reason
                                delta = choice.get("delta") or {}
                                for key in ("content", "reasoning_content"):
                                    if delta.get(key):
                                        message[key] = message.get(key, "") + delta[key]
                                for part in delta.get("tool_calls") or []:
                                    index = part.get("index", 0)
                                    call = calls.setdefault(index, {"id": "", "type": "function",
                                                                   "function": {"name": "", "arguments": ""}})
                                    if part.get("id"):
                                        call["id"] = part["id"]
                                    for key in ("name", "arguments"):
                                        if part.get("function", {}).get(key):
                                            call["function"][key] += part["function"][key]
                                if calls:
                                    message["tool_calls"] = [calls[i] for i in sorted(calls)]
                                if on_delta and (first or delta.get("content") or delta.get("tool_calls")):
                                    await on_delta({k: message[k] for k in ("content", "tool_calls") if k in message})
                if finish_reason == "length":
                    raise ModelError("模型输出达到接口长度限制，本次输出未完整结束")
                if finish_reason not in {"stop", "tool_calls", "function_call"}:
                    raise ModelError("模型输出中途断开或未完整结束，请重试；已生成内容仅供参考")
                if not message.get("content") and not message.get("tool_calls"):
                    raise ModelError("GLM 返回了空消息")
                return message, usage
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                if attempt == 0 and not received:
                    await asyncio.sleep(1)
                    continue
                raise ModelError("模型连接中断或超时；已生成内容仅供参考，可重新分析") from None
            except (ValueError, KeyError, TypeError, IndexError):
                raise ModelError("模型接口未返回有效的流式消息") from None
