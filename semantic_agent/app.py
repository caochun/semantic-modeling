from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import fcntl
import hashlib
import json
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .task_agent import TaskModelingAgent
from .config import Settings
from .ingest import index_corpus
from .store import Store


class Question(BaseModel):
    question: str = Field(min_length=5, max_length=6000)
    max_steps: int | None = Field(default=None, ge=1)


def create_app(settings=None):
    settings = settings or Settings.load()
    store = Store(settings.data_dir, settings.doc_dir)
    jobs = {}
    index_busy = False

    @asynccontextmanager
    async def lifespan(app):
        # Only one server may own this workspace; a duplicate process must not
        # mark another worker's live tasks as interrupted during startup.
        with (settings.data_dir / "server.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("该工作区已有服务在运行，请使用现有服务") from None
            try:
                with store.connect() as db:
                    db.execute("UPDATE runs SET status='interrupted',error='服务重启，运行已中断' WHERE status='running'")
                yield
            finally:
                for task in list(jobs.values()):
                    task.cancel()
                await asyncio.gather(*jobs.values(), return_exceptions=True)
                fcntl.flock(lock, fcntl.LOCK_UN)

    app = FastAPI(title="循知 · 业务问题驱动的语义建模", lifespan=lifespan)
    app.state.store = store

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return JSONResponse({"detail": "此原型仅允许本机访问"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            if origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "不接受跨站写入请求"}, status_code=403)
        return await call_next(request)

    def launch(run_id, coroutine):
        task = asyncio.create_task(coroutine)
        jobs[run_id] = task
        task.add_done_callback(lambda done: jobs.pop(run_id, None))

    @app.get("/api/status")
    def status():
        return {"model": settings.model, "api_configured": bool(settings.api_url),
                "key_configured": bool(settings.api_key), "indexing": index_busy,
                "stats": store.stats(), "issues": store.source_issues()}

    @app.post("/api/index")
    async def index():
        nonlocal index_busy
        if index_busy or jobs:
            raise HTTPException(409, "请等待当前任务结束后再建立索引")
        index_busy = True
        run_id = store.create_run("index")
        async def work():
            nonlocal index_busy
            try:
                store.trace(run_id, "start", "读取本地资料，建立可定位的文本索引")
                def progress(done, total, path):
                    store.trace(run_id, "index", f"已处理 {done}/{total} 个文件", {"path": path})
                result = await asyncio.to_thread(index_corpus, settings, store, progress)
                store.finish_run(run_id, "completed", result)
                store.trace(run_id, "complete", "资料索引已更新")
            except Exception as exc:
                store.finish_run(run_id, "failed", error="索引失败：" + type(exc).__name__)
            finally:
                index_busy = False
        launch(run_id, work())
        return {"id": run_id}

    @app.post("/api/runs")
    async def start_run(body: Question):
        if index_busy or jobs:
            raise HTTPException(409, "原型一次处理一个任务，请等待当前任务完成")
        if not store.stats()["passages"]:
            raise HTTPException(409, "请先点击“更新资料索引”")
        if not settings.api_url:
            raise HTTPException(409, "请先配置 .env 中的 GLM_API_URL")
        run_id = store.create_run("analysis", body.question.strip())
        launch(run_id, TaskModelingAgent(settings, store).run(run_id, body.question.strip(), body.max_steps))
        return {"id": run_id}

    @app.get("/api/runs")
    def runs():
        return store.recent_runs()

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        result = store.get_run(run_id)
        if not result:
            raise HTTPException(404, "分析记录不存在")
        return result

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = 0):
        if not store.get_run(run_id, include_trace=False):
            raise HTTPException(404, "分析记录不存在")
        try:
            cursor = max(0, after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(400, "无效的续接位置") from None

        def encode(kind, data, seq=None):
            prefix = f"id: {seq}\n" if seq is not None else ""
            return prefix + f"event: {kind}\ndata: " + json.dumps(data, ensure_ascii=False) + "\n\n"

        async def generate():
            nonlocal cursor
            yield "retry: 1500\n\n"
            heartbeat = 0
            while not await request.is_disconnected():
                # Read status before the batch so completion cannot skip final events.
                run = store.get_run(run_id, include_trace=False)
                batch = store.events(run_id, cursor)
                for event in batch:
                    cursor = event["seq"]
                    yield encode(event["kind"], event["data"], cursor)
                if len(batch) == 200:
                    continue
                if run["status"] != "running":
                    yield encode("done", run)
                    break
                if time.monotonic() - heartbeat >= 5:
                    yield encode("heartbeat", {"status": "running"})
                    heartbeat = time.monotonic()
                await asyncio.sleep(0.15)

        return StreamingResponse(generate(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/model")
    def problem_model(run_id: str):
        model = store.problem_model(run_id)
        if model is None:
            raise HTTPException(404, "本次分析尚未形成局部模型")
        return model

    @app.get("/api/runs/{run_id}/task-model")
    def task_model(run_id: str):
        model = store.task_model(run_id)
        if model is None:
            raise HTTPException(404, "本次分析尚未形成任务模型")
        pids = {e['passage_id'] for field in ('facts', 'rules', 'concept_links') for x in model.get(field, []) for e in x.get('evidence', [])}
        stale = [pid for pid in pids if not store.source_current(pid)]
        model['source_validation'] = {'current': not stale, 'stale_passage_ids': stale}
        return model

    @app.get("/api/runs/{run_id}/task-model/versions")
    def task_model_versions(run_id: str):
        if not store.get_run(run_id, include_trace=False):
            raise HTTPException(404, '分析记录不存在')
        return store.task_model_versions(run_id)

    @app.get("/api/knowledge-links")
    def knowledge_links(q: str = ""):
        return store.shared_links(q)

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        item = store.get_run(run_id)
        if not item or item["kind"] != "analysis":
            raise HTTPException(400, "只能取消正在进行的分析任务")
        task = jobs.get(run_id)
        if task:
            task.cancel()
        return {"cancelled": bool(task)}

    @app.get("/api/knowledge")
    def knowledge(q: str = ""):
        return store.knowledge(q)

    @app.post("/api/knowledge/clear")
    async def clear_knowledge():
        if jobs or index_busy:
            raise HTTPException(409, "请等待当前任务结束后再清空知识")
        try:
            count = store.clear_knowledge()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"cleared": count}

    @app.post("/api/reset")
    async def reset_workspace():
        if jobs or index_busy:
            raise HTTPException(409, "请等待当前任务结束后再重新开始")
        try:
            counts = store.reset_workspace()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"reset": True, **counts}

    @app.post("/api/knowledge/{kid}/withdraw")
    def withdraw(kid: str):
        if not store.set_knowledge_status(kid, "withdrawn"):
            raise HTTPException(404, "知识条目不存在")
        return {"status": "withdrawn"}

    @app.post("/api/knowledge/{kid}/restore")
    def restore(kid: str):
        if not store.set_knowledge_status(kid, "candidate"):
            raise HTTPException(404, "知识条目不存在")
        return {"status": "candidate"}

    @app.get("/api/passages/{pid}")
    def passage(pid: str):
        result = store.passage(pid)
        if not result:
            raise HTTPException(404, "片段不存在")
        return result

    @app.get("/api/documents/{did}/file")
    def source_file(did: str):
        document = store.document(did)
        if not document:
            raise HTTPException(404, "资料不存在")
        path = (settings.doc_dir / document["path"]).resolve()
        if not path.is_relative_to(settings.doc_dir.resolve()) or not path.is_file():
            raise HTTPException(404, "原文件不可用")
        if hashlib.sha256(path.read_bytes()).hexdigest() != document["sha"]:
            raise HTTPException(409, "原文件已更新，与此历史片段不一致。请在片段中核对历史证据，或重建索引。")
        return FileResponse(path, filename=path.name)

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def home():
        return FileResponse(static / "index.html")

    return app
