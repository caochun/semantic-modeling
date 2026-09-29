"""Project public text from partially received tool/review JSON, with bounded updates."""
import json
import re
import time


def partial_string(text, start):
    if start >= len(text) or text[start] != '"':
        return None
    i = start + 1
    safe = i
    while i < len(text):
        ch = text[i]
        if ch == '"':
            return json.loads(text[start:i + 1])
        if ch == "\\":
            if i + 1 >= len(text):
                break
            if text[i + 1] == 'u':
                if not re.fullmatch(r"[0-9a-fA-F]{4}", text[i + 2:i + 6]):
                    break
                code = int(text[i + 2:i + 6], 16)
                if 0xD800 <= code <= 0xDBFF:
                    # Never emit a lone half of a surrogate pair.
                    if not re.fullmatch(r"\\u[dD][c-fC-F][0-9a-fA-F]{2}", text[i + 6:i + 12]):
                        break
                    i += 12
                elif 0xDC00 <= code <= 0xDFFF:
                    break
                else:
                    i += 6
            else:
                i += 2
        else:
            i += 1
        safe = i
    return json.loads(text[start:safe] + '"')


def json_string_prefix(text, path):
    """Read one exact object path. Earlier values must be complete to skip them.

    Keys or answer-like text inside other values cannot be mistaken for this path.
    Incomplete escapes are held until their next chunk arrives.
    """
    text = re.sub(r"^\s*```(?:json)?\s*", "", text)
    decoder = json.JSONDecoder()

    def whitespace(i):
        while i < len(text) and text[i].isspace():
            i += 1
        return i

    def walk(i, remaining):
        i = whitespace(i)
        if not remaining:
            return partial_string(text, i)
        if i >= len(text) or text[i] != '{':
            return None
        i += 1
        while True:
            i = whitespace(i)
            key, i = decoder.raw_decode(text, i)
            if not isinstance(key, str):
                return None
            i = whitespace(i)
            if i >= len(text) or text[i] != ':':
                return None
            i = whitespace(i + 1)
            if key == remaining[0]:
                return walk(i, remaining[1:])
            _, i = decoder.raw_decode(text, i)
            i = whitespace(i)
            if i >= len(text) or text[i] != ',':
                return None
            i += 1
    try:
        return walk(0, path)
    except (ValueError, TypeError, RecursionError):
        return None


class StreamReporter:
    def __init__(self, store, run_id, step, *, review=False):
        self.store, self.run_id, self.step = store, run_id, step
        self.review = review
        self.last_emit = 0
        self.last = {}
        self.started = False

    async def __call__(self, message):
        self.emit(message)

    def emit(self, message, *, force=False):
        if not self.started:
            self.store.trace(self.run_id, "receiving", "复核模型已开始响应" if self.review else "模型已开始响应，正在组织本轮内容")
            self.started = True
        now = time.monotonic()
        if not force and now - self.last_emit < 0.15:
            return
        self.last_emit = now
        content = message.get("content") or ""
        if self.review:
            answer = json_string_prefix(content, ("answer_review", "answer"))
            if answer:
                self.publish("answer", {"text": answer, "stage": "review", "step": self.step})
        else:
            marker = "【回答草稿】"
            if content and marker.startswith(content.lstrip()):
                return
            if content.lstrip().startswith(marker):
                answer = content.lstrip()[len(marker):].lstrip()
                if answer:
                    self.publish("answer", {"text": answer, "stage": "draft", "step": self.step})
                return
            elif content:
                self.publish("progress", {"text": content[:4000], "step": self.step})
            for call in message.get("tool_calls") or []:
                fn = call.get("function", {})
                if fn.get("name") == "submit_result":
                    answer = json_string_prefix(fn.get("arguments", ""), ("answer",))
                    if answer:
                        self.publish("answer", {"text": answer, "stage": "draft", "step": self.step})
                    break

    def publish(self, kind, data):
        if self.last.get(kind) != data:
            self.store.event(self.run_id, kind, data)
            self.last[kind] = data
