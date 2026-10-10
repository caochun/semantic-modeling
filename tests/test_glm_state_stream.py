import json

import decision_service.state_builder as module
from decision_service.state_builder import GLMStateBuilder


class FakeResponse:
    status_code = 200
    headers = {"content-type": "text/event-stream"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self):
        payload = {
            "window": {"start": "2026-09-11T12:00:00", "end": "2026-09-11T12:01:00", "event_count": 1},
            "observations": [{"description": "风机运行反馈消失", "evidence_event_ids": ["row-1"]}],
            "transitions": [], "missing_evidence": ["实际电流"], "uncertainties": [],
            "data_quality": {}, "state_text": "观察到风机运行反馈消失",
        }
        text = json.dumps(payload, ensure_ascii=False)
        for index in range(0, len(text), 7):
            piece = text[index:index + 7]
            chunk = {"choices": [{"delta": {"content": piece}}]}
            yield "data: " + json.dumps(chunk, ensure_ascii=False)
        yield "data: [DONE]"


class FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url, headers, json):
        assert method == "POST"
        assert json["stream"] is True
        return FakeResponse()


def test_build_stream_forwards_deltas_then_normalized_state(monkeypatch):
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    builder = GLMStateBuilder(api_url="http://fake/v1", api_key="key", model="glm-test")

    async def collect():
        return [item async for item in builder.build_stream(
            logs="event_time\tcontent\n2026-09-11T12:00:00\t风机运行反馈消失",
            task="监盘状态归纳", station="姑苏",
        )]

    import asyncio
    events = asyncio.run(collect())
    deltas = [item for item in events if item["kind"] == "delta"]
    complete = [item for item in events if item["kind"] == "complete"]
    assert len(deltas) > 1
    assert complete[0]["result"]["model"] == "glm-test"
    assert complete[0]["result"]["state"].startswith("观察到风机运行反馈消失")
