from pathlib import Path

from fastapi.testclient import TestClient

from decision_service.app import create_app
from decision_service.state_builder import normalize_state


class FakeStateBuilder:
    api_url = "http://fake"
    api_key = "redacted"
    model = "fake-glm"

    async def build(self, *, logs, task, station, context):
        state, structured, warnings = normalize_state(
            {
                "window": {"start": "2026-09-11T12:00:00", "end": "2026-09-11T12:01:00", "event_count": 2},
                "observations": [
                    {"description": "A 通道风机运行反馈丢失", "entity": "阀冷风机", "status": "observed",
                     "evidence_event_ids": ["row-1"]},
                ],
                "missing_evidence": ["风机控制和电源状态"],
                "uncertainties": ["运行反馈丢失不等于机械故障"],
                "data_quality": {"unknown_event_count": 0},
                "state_text": "窗口内观察到 A 通道风机运行反馈丢失；尚缺少风机控制和电源状态。",
            },
            station=station,
            raw_logs=str(logs),
        )
        return {"state": state, "structured_state": structured, "warnings": warnings, "model": self.model, "usage": {}}


def test_state_endpoint_uses_llm_builder_and_returns_reviewable_state():
    with TestClient(create_app(Path("/tmp/no-adapter"), state_builder=FakeStateBuilder())) as client:
        response = client.post("/v1/state", json={
            "logs": "event_time\\tcontent\\n2026-09-11T12:00:00\\t风机运行消失 消失",
            "station": "姑苏",
        })
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "fake-glm"
    assert body["structured_state"]["observations"][0]["evidence_event_ids"] == ["row-1"]
    assert body["state"].startswith("窗口内观察到")
    assert "运行反馈丢失不等于机械故障" in body["structured_state"]["uncertainties"]


def test_state_stream_emits_stages_and_done():
    with TestClient(create_app(Path("/tmp/no-adapter"), state_builder=FakeStateBuilder())) as client:
        response = client.post("/v1/state/stream", json={"logs": [{"content": "保护屏蔽条件出现"}]})
    assert response.status_code == 200
    assert "event: stage" in response.text
    assert "event: state" in response.text
    assert "event: state_details" in response.text
    assert "event: done" in response.text


def test_decision_service_does_not_expose_previous_monitoring_route():
    with TestClient(create_app(Path("/tmp/no-adapter"), state_builder=FakeStateBuilder())) as client:
        response = client.post("/v1/monitoring", json={"events": []})
    assert response.status_code == 404
