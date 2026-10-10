"""Decision Model service: raw input -> task state -> typed decisions."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
import threading
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

try:
    from .state_builder import GLMStateBuilder, StateBuilderError
except ImportError:
    from state_builder import GLMStateBuilder, StateBuilderError


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DEFAULT_ADAPTER = Path(os.environ.get(
    "DECISION_ADAPTER",
    "/home/chun/semantic-decision/experiments/gusu_procedure_20261010/formal/adapter",
))
SERVE_MAX_TOKENS = int(os.environ.get("DECISION_SERVE_MAX_TOKENS", "16384"))
TRAINED_MAX_TOKENS = 4096

VALVE_TASKS: dict[str, dict[str, Any]] = {
    "during_window": {
        "title": "窗口内是否有异常证据",
        "description": "判断日志窗口内是否存在明确阀冷异常信号或异常现象证据。",
        "instructions": "区分信号证据、试验注入与设备真实损坏；不能用事件等级或单条消失记录推断系统健康。",
        "criteria": {
            "abnormal_evidence": "窗口内有明确异常信号或异常现象证据",
            "verified_expected": "有完整且适用的依据确认过程符合预期",
            "insufficient_evidence": "记录不足以确认异常或符合预期",
        },
    },
    "at_window_end": {
        "title": "窗口末状态",
        "description": "判断截至窗口结束时仍可追溯的异常证据状态。",
        "instructions": "无复归只是最后已知记录；个别信号复归或没有新报警，不能证明整个评估范围正常。",
        "criteria": {
            "abnormal_evidence": "末端仍有未见复归的明确异常证据",
            "verified_expected": "有充分末端状态和操作依据确认符合预期",
            "insufficient_evidence": "末端状态证据不足",
        },
    },
    "observed_recovery": {
        "title": "已见信号是否复归",
        "description": "按主机、A/B 通道和信号分别追踪出现与后续消失。",
        "instructions": "只检查明确观察到出现的目标信号；单独消失不能补造此前出现，也不能把通道混合。",
        "criteria": {
            "all_observed_cleared": "已见目标信号均有后续复归记录",
            "some_unresolved": "部分已见目标信号已复归，部分未见复归",
            "all_unresolved": "已见目标信号均未见后续复归",
            "not_observed": "未观察到目标信号出现",
            "indeterminate": "存在矛盾边沿，无法确定复归状态",
        },
    },
}

# The explicit procedure adapter was trained with one question named
# ``decision`` whose criteria depend on the selected procedure family.  Keep
# the family selector separate from the model question key so the serving
# protocol matches the training rows exactly.
PROCEDURE_TASKS: dict[str, dict[str, Any]] = {
    "procedure_14_1": {
        "title": "14.1 阀厅空调室外机故障",
        "description": "根据报警复归核查，判断规程14.1当前分支。",
        "instructions": "依据已完成的复归核查，判断规程14.1当前应进入哪个分支。只能使用状态中明确记录的报警复归结果，不把未知当作复归。",
        "criteria": {
            "monitor_after_recovery": "报警已复归，继续加强监视",
            "switch_backup_and_maintenance": "报警未复归，切换备用机组并通知检修",
            "needs_recovery_check": "尚未获得报警复归结果，不能选择上述分支",
        },
    },
    "procedure_14_2": {
        "title": "14.2 压差传感器报警",
        "description": "根据传感器状态和压差读数核查，判断规程14.2当前分支。",
        "instructions": "依据传感器健康状态和读数与设定值的关系，判断规程14.2当前分支。没有完成测量时保留待核查。",
        "criteria": {
            "sensor_maintenance": "传感器异常，通知检修处理",
            "switch_unit_and_replace_filter": "传感器正常且读数超过设定值，切换空调机组并更换过滤网",
            "needs_measurement": "传感器状态或读数关系尚未明确，继续完成核查",
        },
    },
    "procedure_13_4": {
        "title": "13.4 泵房漏水",
        "description": "根据漏水点现场检查结果，判断规程13.4当前分支。",
        "instructions": "依据现场是否发现明显管网破损漏水，判断规程13.4当前分支。未发现漏点不能推断没有漏水。",
        "criteria": {
            "isolate_leak_and_repair": "发现明显管网破损漏水，隔离漏水点并联系检修",
            "maintenance_inspection": "未发现明显漏水点，联系检修进一步检查分析",
            "needs_field_check": "尚未完成漏水点现场检查",
        },
    },
    "procedure_13_5": {
        "title": "13.5 生活泵故障",
        "description": "根据另一台生活泵的自动运行能力，判断规程13.5当前分支。",
        "instructions": "依据故障泵以外的生活泵自动运行能力，判断规程13.5当前分支。",
        "criteria": {
            "secure_failed_pump_and_use_other": "另一台生活泵自动状态正常，做好故障泵安措并联系检修",
            "manual_start_other_and_water_supply": "另一台生活泵不能自动运行，切手动启动并联系储水",
            "inspect_both_pumps": "两台生活泵均故障，通知检修检查处理",
            "needs_pump_status_check": "另一台生活泵状态尚未核实",
        },
    },
    "procedure_15_12_6": {
        "title": "15.12.6 消防水池缺水",
        "description": "根据现场液位显示核查结果，判断规程15.12.6当前分支。",
        "instructions": "依据现场液位显示核查结果，判断规程15.12.6当前分支。后台低报警本身不能替代现场液位核对。",
        "criteria": {
            "replenish_and_find_refill_fault": "现场实际液位低告警属实，补水并查找自动补水原因",
            "record_sensor_fault_and_repair": "现场实际液位正常，判断为液位传感器故障并记录检修",
            "needs_level_check": "现场液位核查尚未完成",
        },
    },
    "procedure_15_13_1": {
        "title": "15.13.1 火灾自动报警系统误动作",
        "description": "根据现场火情确认和复位后的重复报警结果，判断规程15.13.1当前分支。",
        "instructions": "依据现场火情确认和复位后的重复报警结果，判断规程15.13.1当前分支。",
        "criteria": {
            "fire_response": "现场存在火情，进入相应火灾处置预案",
            "false_alarm_monitor": "现场无火情且复位后不再报警，按误报警加强巡视",
            "isolate_faulty_loop_and_repair": "现场无火情但复位后重复报警，屏蔽相应回路或装置并维修",
            "needs_fire_check": "现场火情或复位结果尚未明确",
        },
    },
    "procedure_10_5": {
        "title": "10.5 内水冷主循环泵异常",
        "description": "根据备用泵投入结果和主泵现场状态，判断规程10.5当前分支。",
        "instructions": "依据备用泵控制/投入结果以及主泵现场严重异常情况，判断规程10.5当前分支。这里输出的是规程分支候选，不授权现场操作。",
        "criteria": {
            "set_auto_and_monitor_backup": "备用泵控制方式不在自动，切至自动并核对备用泵投入",
            "switch_from_severely_abnormal_main_pump": "主泵存在严重发热、冒烟、异常声音或电源烧糊，执行主泵切换并确认备用泵",
            "request_converter_valve_shutdown": "备用泵无法投入或投入后仍故障，申请对应换流阀停运并隔离故障泵",
            "needs_pump_condition_check": "备用泵或主泵现场条件尚未明确",
        },
    },
}

_configured_profile = os.environ.get("DECISION_TASK_PROFILE", "")
if _configured_profile == "procedure_explicit_v2" or "gusu_explicit_state_v2" in str(DEFAULT_ADAPTER):
    TASKS = PROCEDURE_TASKS
    TASK_SELECTION_MODE = "single"
else:
    TASKS = VALVE_TASKS
    TASK_SELECTION_MODE = "multi"

DEFAULT_MODEL_ID = os.environ.get(
    "DECISION_MODEL_ID",
    "gusu-explicit-state-v2" if TASK_SELECTION_MODE == "single" else "gusu-procedure-v1",
)

class Question(BaseModel):
    type: str = "choice"
    instructions: str = Field(min_length=1, max_length=4000)
    criteria: dict[str, str] | list[str] | None = None

    @field_validator("type")
    @classmethod
    def supported_type(cls, value: str) -> str:
        if value not in {"choice", "score", "noul"}:
            raise ValueError("问题类型只能是 choice、score 或 noul")
        return value

    @model_validator(mode="after")
    def criteria_valid(self):
        if self.type == "choice":
            if not isinstance(self.criteria, dict) or not 2 <= len(self.criteria) <= 8:
                raise ValueError("choice 问题需要 2 到 8 个选项")
            if any(not str(k).strip() or not str(v).strip() for k, v in self.criteria.items()):
                raise ValueError("choice 选项不能为空")
        elif self.type == "score":
            if not isinstance(self.criteria, list) or not 2 <= len(self.criteria) <= 8:
                raise ValueError("score 问题需要 2 到 8 个评分等级")
        elif self.criteria is not None and (not isinstance(self.criteria, dict) or set(self.criteria) != {"true", "false"}):
            raise ValueError("noul 问题的 criteria 只能包含 true 和 false")
        return self


class DecisionRequest(BaseModel):
    state: str | dict[str, Any] | list[Any]
    questions: dict[str, Question]
    model: str = DEFAULT_MODEL_ID

    @field_validator("state")
    @classmethod
    def state_present(cls, value):
        if not value or (isinstance(value, str) and not value.strip()):
            raise ValueError("state 不能为空")
        return value

    @field_validator("questions")
    @classmethod
    def questions_valid(cls, value):
        if not value or len(value) > 8:
            raise ValueError("至少需要一个问题，最多八个问题")
        return value


class StateBuildRequest(BaseModel):
    logs: str | list[dict[str, Any]] | dict[str, Any]
    task: str = Field(default="换流站监盘：根据原始日志归纳当前任务级状态", min_length=2, max_length=1000)
    station: str = Field(default="姑苏", min_length=1, max_length=100)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("logs")
    @classmethod
    def logs_present(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("原始日志不能为空")
        if isinstance(value, (list, dict)) and not value:
            raise ValueError("原始日志不能为空")
        return value


class ModelRuntime:
    def __init__(self, adapter: Path):
        self.adapter = adapter
        self.model = None
        self.tokenizer = None
        self.error: str | None = None
        self.loading = False
        self.lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.model is not None and self.tokenizer is not None and self.error is None

    def load(self) -> None:
        with self.lock:
            if self.ready or self.loading:
                return
            self.loading = True
        try:
            if not self.adapter.is_dir():
                raise FileNotFoundError(f"adapter directory not found: {self.adapter}")
            import unsloth
            from unsloth import FastDecisionModel
            model, tokenizer = FastDecisionModel.from_pretrained(
                model_name=str(self.adapter), max_seq_length=TRAINED_MAX_TOKENS,
                load_in_4bit=True, local_files_only=True,
            )
            FastDecisionModel.for_inference(model)
            model.eval()
            self.model, self.tokenizer = model, tokenizer
            self.error = None
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.loading = False

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.ready, "loading": self.loading, "error": self.error,
            "adapter": str(self.adapter), "backend": "Unsloth FastDecisionModel / PyTorch",
            "gpu": self._gpu_name(), "trained_max_tokens": TRAINED_MAX_TOKENS,
            "serve_max_tokens": SERVE_MAX_TOKENS, "task_count": len(TASKS),
            "trained_question_types": ["choice", "score", "noul"],
            "protocol_question_types": ["choice", "score", "noul"],
            "scope": "实验性候选判断；不确认现场故障，不生成操作命令",
        }

    @staticmethod
    def _gpu_name() -> str | None:
        try:
            import torch
            return torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
        except Exception:
            return None

    def predict(self, payload: DecisionRequest) -> dict[str, Any]:
        if not self.ready:
            self.load()
        if not self.ready:
            raise RuntimeError(self.error or "model is not ready")
        from unsloth import FastDecisionModel
        from unsloth.models._decision_common import _parsed
        from unsloth.models.clef import encode_record

        questions = {key: value.model_dump(exclude_none=True) for key, value in payload.questions.items()}
        text_tokenizer = getattr(self.tokenizer, "tokenizer", self.tokenizer)
        parsed = _parsed(payload.state)
        full = encode_record(text_tokenizer, {"state": parsed, "questions": questions}, max_length=SERVE_MAX_TOKENS)
        if len(full.input_ids) >= SERVE_MAX_TOKENS:
            raise ValueError(f"输入超过服务上限 {SERVE_MAX_TOKENS} token，请缩短 state")
        answers = FastDecisionModel.predict(self.model, self.tokenizer, payload.state, questions)
        return {
            "model": payload.model, "answers": answers,
            "usage": {"input_tokens": len(full.input_ids), "output_tokens": 0},
            "limits": {"trained_max_tokens": TRAINED_MAX_TOKENS, "served_max_tokens": SERVE_MAX_TOKENS,
                       "over_training_length": len(full.input_ids) > TRAINED_MAX_TOKENS},
            "experimental": True, "field_validity_claimed": False,
            "backend": "Unsloth FastDecisionModel / PyTorch",
        }


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(adapter: Path = DEFAULT_ADAPTER, state_builder=None) -> FastAPI:
    runtime = ModelRuntime(adapter)
    state_builder = state_builder or GLMStateBuilder()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime
        await asyncio.to_thread(runtime.load)
        yield
        runtime.model = None
        runtime.tokenizer = None

    app = FastAPI(title="任务级 State Decision Model 服务", version="1.0.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def add_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Decision-Model-Scope"] = "state-builder-then-decision"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    async def health():
        result = runtime.status()
        result["state_builder"] = {
            "configured": bool(getattr(state_builder, "api_url", "")) and bool(getattr(state_builder, "api_key", "")),
            "model": getattr(state_builder, "model", None),
        }
        return result

    @app.get("/api/tasks")
    async def tasks():
        return {
            "tasks": TASKS,
            "selection_mode": TASK_SELECTION_MODE,
            "profile": _configured_profile or ("procedure_explicit_v2" if TASK_SELECTION_MODE == "single" else "valve"),
            "default_task": "换流站监盘：根据原始日志归纳当前任务级状态，并判断证据和下一项核查",
            "flow": ["raw_logs", "llm_state", "decision_model"],
            "decision_endpoint": "/v1/decision",
            "state_endpoint": "/v1/state",
            "state_stream_endpoint": "/v1/state/stream",
            "trained_question_types": ["choice"],
        }

    @app.post("/v1/state")
    async def build_state(payload: StateBuildRequest):
        try:
            return await state_builder.build(logs=payload.logs, task=payload.task,
                                             station=payload.station, context=payload.context)
        except StateBuilderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/v1/state/stream")
    async def build_state_stream(payload: StateBuildRequest):
        async def events():
            yield _sse("stage", {"stage": "parse", "message": "正在读取并整理原始日志"})
            await asyncio.sleep(0)
            yield _sse("stage", {"stage": "model", "message": "GLM 正在生成任务级 State"})
            try:
                stream_builder = getattr(state_builder, "build_stream", None)
                result = None
                if stream_builder is not None:
                    async for item in stream_builder(logs=payload.logs, task=payload.task,
                                                     station=payload.station, context=payload.context):
                        if item.get("kind") == "delta":
                            yield _sse("model_delta", {
                                "channel": item.get("channel", "content"),
                                "text": item.get("text", ""),
                            })
                        elif item.get("kind") == "complete":
                            result = item.get("result")
                            yield _sse("model_done", {"usage": (result or {}).get("usage", {})})
                else:
                    result = await state_builder.build(logs=payload.logs, task=payload.task,
                                                       station=payload.station, context=payload.context)
                if not result:
                    raise StateBuilderError("State Builder 未返回结果")
                # Keep the first usable State event small. Some browsers and
                # reverse proxies buffer or split large SSE records; details
                # are sent separately so the UI can display the State as soon
                # as the model text is available.
                yield _sse("state", {"state": result["state"], "model": result.get("model")})
                yield _sse("state_details", {"structured_state": result["structured_state"],
                                               "warnings": result.get("warnings", []),
                                               "model": result.get("model")})
                yield _sse("done", result)
            except StateBuilderError as exc:
                yield _sse("error", {"message": str(exc)})
            except Exception:
                yield _sse("error", {"message": "State 构建失败，请检查输入并重试"})
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/v1/decision")
    async def decision(payload: DecisionRequest):
        try:
            return await asyncio.to_thread(runtime.predict, payload)
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("DECISION_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DECISION_PORT", "9120")))
    parser.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.adapter), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
