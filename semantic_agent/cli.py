import argparse
import asyncio
import json
from pathlib import Path

from .task_agent import TaskModelingAgent
from .config import Settings
from .ingest import index_corpus
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="从业务问题出发建立可追溯知识")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="启动本地网页")
    serve.add_argument("--port", type=int, default=8765)
    sub.add_parser("index", help="建立或增量更新资料索引")
    ask = sub.add_parser("ask", help="从命令行提出业务问题")
    ask.add_argument("question")
    ask.add_argument("--max-steps", type=int, default=None, help='可选轮数上限，默认不限制')
    ask.add_argument("--output", type=Path)
    sub.add_parser("status", help="显示资料及知识数量")
    args = parser.parse_args()
    settings = Settings.load()
    store = Store(settings.data_dir, settings.doc_dir)
    if args.command == "serve":
        import uvicorn
        from .app import create_app
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)
    elif args.command == "index":
        result = index_corpus(settings, store, lambda done, total, path: print(f"{done}/{total} {path}", flush=True))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "status":
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    elif args.command == "ask":
        if not store.stats()["passages"]:
            parser.error("请先执行 semantic-agent index")
        run_id = store.create_run("analysis", args.question)
        print("运行记录：" + run_id, flush=True)
        result = asyncio.run(TaskModelingAgent(settings, store).run(run_id, args.question, args.max_steps))
        if not result:
            print(store.get_run(run_id)["error"])
            raise SystemExit(1)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(result["answer"])
        print("\n知识变化：" + json.dumps(result["knowledge_changes"], ensure_ascii=False))


if __name__ == "__main__":
    main()
