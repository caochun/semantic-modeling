from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import uuid

from .organization import OrganizationStore


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:16]


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def normalize(text):
    return re.sub(r"\s+", "", text).casefold()


def tokens(text):
    """FTS5's default tokenizer does not segment Chinese; add character bigrams."""
    result = []
    for part in re.findall(r"[\u3400-\u9fff]+|[A-Za-z0-9_]+", text.lower()):
        if re.fullmatch(r"[\u3400-\u9fff]+", part):
            result.extend(part[i:i + 2] for i in range(len(part) - 1))
            if len(part) == 1:
                result.append(part)
        else:
            result.append(part)
    return list(dict.fromkeys(result))


class Store(OrganizationStore):
    def __init__(self, data_dir: Path, doc_dir: Path | None = None):
        self.doc_dir = doc_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "knowledge.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, path TEXT NOT NULL, sha TEXT NOT NULL,
                    size INTEGER, mtime_ns INTEGER, active INTEGER DEFAULT 1,
                    status TEXT, detail TEXT, indexed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS passages (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                    locator TEXT, text TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS passage_doc ON passages(document_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS passage_fts USING fts5(
                    passage_id UNINDEXED, title, body
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, kind TEXT, question TEXT, status TEXT,
                    created_at TEXT, updated_at TEXT, result TEXT, error TEXT
                );
                CREATE TABLE IF NOT EXISTS trace (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
                    time TEXT, kind TEXT, message TEXT, data TEXT
                );
                CREATE TABLE IF NOT EXISTS knowledge (
                    id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE, kind TEXT,
                    title TEXT, statement TEXT, scope TEXT, conditions TEXT,
                    evidence TEXT, status TEXT, review TEXT, run_id TEXT,
                    created_at TEXT, updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS knowledge_structure (
                    knowledge_id TEXT PRIMARY KEY, fragment TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS trace_run_seq ON trace(run_id,seq);
                CREATE TABLE IF NOT EXISTS run_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    kind TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_run_seq ON run_events(run_id,seq);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS problem_models (
                    run_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_models (
                    run_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_model_versions (
                    run_id TEXT, version INTEGER, phase TEXT, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id,version,phase)
                );
                CREATE TABLE IF NOT EXISTS model_usages (
                    run_id TEXT, knowledge_id TEXT, role TEXT, origin TEXT, created_at TEXT,
                    PRIMARY KEY(run_id,knowledge_id)
                );
                CREATE INDEX IF NOT EXISTS usages_knowledge ON model_usages(knowledge_id);
                CREATE TABLE IF NOT EXISTS link_validations (
                    run_id TEXT, ordinal INTEGER, link_id TEXT, question_key TEXT,
                    eligible INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL,
                    PRIMARY KEY(run_id,ordinal)
                );
                CREATE INDEX IF NOT EXISTS validations_link ON link_validations(link_id);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def trace(self, run_id, kind, message, data=None):
        item = {"time": now(), "kind": kind, "message": message, "data": data}
        with self.connect() as db:
            cursor = db.execute("INSERT INTO trace(run_id,time,kind,message,data) VALUES(?,?,?,?,?)",
                       (run_id, item["time"], kind, message, json.dumps(data, ensure_ascii=False)))
            item["seq"] = cursor.lastrowid
            db.execute("INSERT INTO run_events(run_id,kind,data) VALUES(?,?,?)",
                       (run_id, "trace", json.dumps(item, ensure_ascii=False)))

    def event(self, run_id, kind, data):
        with self.connect() as db:
            db.execute("INSERT INTO run_events(run_id,kind,data) VALUES(?,?,?)",
                       (run_id, kind, json.dumps(data, ensure_ascii=False)))

    def events(self, run_id, after=0, limit=200):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM run_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                              (run_id, after, limit)).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def clear_knowledge(self):
        """Clear reusable/candidate knowledge, retaining sources and labelled history."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM runs WHERE status='running'").fetchone():
                raise ValueError("请等待当前任务结束后再清空知识")
            count = db.execute("SELECT count(*) FROM knowledge").fetchone()[0]
            db.execute("DELETE FROM knowledge_structure")
            db.execute("DELETE FROM knowledge")
            db.execute("DELETE FROM model_usages")
            db.execute("DELETE FROM link_validations")
            db.execute("INSERT OR REPLACE INTO metadata VALUES('knowledge_cleared_at',?)", (now(),))
        return count

    def create_run(self, kind, question=""):
        run_id = uid("run")
        with self.connect() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)",
                       (run_id, kind, question, "running", now(), now(), None, None))
        return run_id

    def finish_run(self, run_id, status, result=None, error=None):
        with self.connect() as db:
            db.execute("UPDATE runs SET status=?, result=?, error=?, updated_at=? WHERE id=?",
                       (status, json.dumps(result, ensure_ascii=False), error, now(), run_id))

    def save_task_model(self, run_id, model):
        """Persist every model version independently from the answer/knowledge tables."""
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO task_models VALUES(?,?,?)",
                       (run_id, json.dumps(model, ensure_ascii=False), now()))
            db.execute("INSERT OR REPLACE INTO task_model_versions VALUES(?,?,?,?,?)",
                       (run_id, model['version'], model['phase'], json.dumps(model, ensure_ascii=False), now()))
        return model

    def task_model_versions(self, run_id):
        with self.connect() as db:
            rows = db.execute('SELECT version,phase,payload,created_at FROM task_model_versions WHERE run_id=? ORDER BY version,created_at', (run_id,)).fetchall()
        return [{**dict(r), 'payload': json.loads(r['payload'])} for r in rows]

    def task_model(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM task_models WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def get_run(self, run_id, *, include_trace=True):
        with self.connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["result"] = json.loads(result["result"]) if result["result"] else None
            result["trace"] = [dict(r) for r in db.execute(
                "SELECT * FROM trace WHERE run_id=? ORDER BY seq", (run_id,))] if include_trace else []
            for item in result["trace"]:
                item["data"] = json.loads(item["data"]) if item["data"] else None
            cleared = db.execute("SELECT value FROM metadata WHERE key='knowledge_cleared_at'").fetchone()
            result["knowledge_cleared"] = bool(cleared and result["created_at"] < cleared["value"])
            if include_trace:
                result["event_cursor"] = db.execute(
                    "SELECT coalesce(max(seq),0) FROM run_events WHERE run_id=?", (run_id,)).fetchone()[0]
                streams = db.execute("SELECT kind,data FROM run_events WHERE run_id=? AND kind IN ('answer','progress') ORDER BY seq", (run_id,))
                result["stream"] = {}
                for event in streams:
                    result["stream"][event["kind"]] = json.loads(event["data"])
            return result

    def recent_runs(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT id,kind,question,status,created_at FROM runs ORDER BY created_at DESC LIMIT 30")]

    def stats(self):
        with self.connect() as db:
            sources = [dict(r) for r in db.execute(
                "SELECT status,count(*) AS count FROM documents WHERE active=1 GROUP BY status")]
            return {"documents": sources,
                    "passages": db.execute("SELECT count(*) FROM passages p JOIN documents d ON d.id=p.document_id WHERE d.active=1").fetchone()[0],
                    "knowledge": db.execute("SELECT count(*) FROM knowledge WHERE status!='withdrawn'").fetchone()[0]}

    def search(self, query, limit=6, path_contains="", source_type="documents"):
        terms = tokens(query)[:32]
        if not terms:
            return []
        match = " OR ".join('"' + t.replace('"', '') + '"' for t in terms)
        type_filter = ""
        if source_type == "documents":
            type_filter = " AND lower(d.path) NOT LIKE '%.txt' AND lower(d.path) NOT LIKE '%.csv'"
        elif source_type == "logs":
            type_filter = " AND (lower(d.path) LIKE '%.txt' OR lower(d.path) LIKE '%.csv')"
        with self.connect() as db:
            rows = db.execute("""
                SELECT p.*,d.path,d.sha,bm25(passage_fts,0,2,1) AS score
                FROM passage_fts JOIN passages p ON p.id=passage_fts.passage_id
                JOIN documents d ON d.id=p.document_id
                WHERE passage_fts MATCH ? AND d.active=1 AND instr(d.path, ?) > 0
            """ + type_filter + " ORDER BY score LIMIT 100", (match, path_contains)).fetchall()
        # Prefer exact phrases and query coverage while retaining BM25 ordering.
        def rank(row):
            body = normalize(row["text"])
            searchable = body + normalize(row["path"])
            coverage = sum(t in searchable for t in terms) / len(terms)
            exact = normalize(query) in body
            return (exact, coverage, -row["score"])
        rows = sorted(rows, key=rank, reverse=True)
        results = []
        per_doc = {}
        for row in rows:
            if per_doc.get(row["document_id"], 0) >= 3:
                continue
            text = row["text"]
            pos = text.casefold().find(query.casefold())
            if pos < 0:
                pos = next((text.casefold().find(t) for t in terms if t in text.casefold()), 0)
            results.append({"id": row["id"], "path": row["path"], "locator": row["locator"],
                            "snippet": text[max(0, pos - 100):max(0, pos - 100) + 550]})
            per_doc[row["document_id"]] = per_doc.get(row["document_id"], 0) + 1
            if len(results) >= limit:
                break
        return results

    def passage(self, passage_id, active_only=False):
        with self.connect() as db:
            row = db.execute("""SELECT p.*,d.path,d.sha,d.active,d.size,d.mtime_ns
                FROM passages p JOIN documents d ON d.id=p.document_id WHERE p.id=?""",
                (passage_id,)).fetchone()
        if not row or (active_only and not row["active"]):
            return None
        return dict(row)

    def document(self, document_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            return dict(row) if row else None

    def source_issues(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT path,status,detail FROM documents WHERE active=1 AND status!='indexed' ORDER BY path")]

    def knowledge(self, query="", reusable_only=False):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM knowledge ORDER BY updated_at DESC")]
            fragments = {r["knowledge_id"]: json.loads(r["fragment"])
                         for r in db.execute("SELECT * FROM knowledge_structure")}
        result = []
        query_terms = tokens(query)
        for row in rows:
            for key in ["conditions", "evidence", "review"]:
                row[key] = json.loads(row[key])
            row["model_fragment"] = fragments.get(row["id"], {"terms": [], "relations": []})
            row["sources_current"] = all(self.source_current(e["passage_id"])
                                         for e in row["evidence"])
            row["used_in"] = self.knowledge_usage(row["id"])
            if reusable_only and (row["status"] != "reviewed" or not row["sources_current"]):
                continue
            haystack = (row["title"] + row["statement"] + row["scope"] + json.dumps(row["model_fragment"], ensure_ascii=False)).lower()
            row["relevance"] = sum(t in haystack for t in query_terms)
            if not query or row["relevance"]:
                result.append(row)
        if query:
            result.sort(key=lambda r: r["relevance"], reverse=True)
        return result[:100]

    def save_knowledge(self, candidate, run_id, status, review):
        fingerprint = digest(json.dumps([normalize(candidate["statement"]),
            normalize(candidate["scope"]), sorted(candidate["conditions"])], ensure_ascii=False))
        with self.connect() as db:
            existing = db.execute("SELECT id,status FROM knowledge WHERE fingerprint=?", (fingerprint,)).fetchone()
            if existing:
                # A withdrawn item stays withdrawn. A fresh successful review can promote a candidate.
                if existing["status"] == "candidate" and status == "reviewed":
                    db.execute("UPDATE knowledge SET status=?,review=?,evidence=?,updated_at=? WHERE id=?", (
                        status, json.dumps(review, ensure_ascii=False), json.dumps(candidate["evidence"], ensure_ascii=False), now(), existing["id"]))
                    db.execute("INSERT OR REPLACE INTO knowledge_structure VALUES(?,?)", (
                        existing["id"], json.dumps(candidate.get("model_fragment", {}), ensure_ascii=False)))
                return existing["id"], False
            kid = uid("k")
            db.execute("INSERT INTO knowledge VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                kid, fingerprint, candidate["kind"], candidate["title"], candidate["statement"],
                candidate["scope"], json.dumps(candidate["conditions"], ensure_ascii=False),
                json.dumps(candidate["evidence"], ensure_ascii=False), status,
                json.dumps(review, ensure_ascii=False), run_id, now(), now()))
            db.execute("INSERT INTO knowledge_structure VALUES(?,?)", (
                kid, json.dumps(candidate.get("model_fragment", {"terms": [], "relations": []}), ensure_ascii=False)))
            return kid, True

    def knowledge_status(self, kid):
        with self.connect() as db:
            row = db.execute("SELECT status FROM knowledge WHERE id=?", (kid,)).fetchone()
            return row["status"] if row else None

    def set_knowledge_status(self, kid, status):
        with self.connect() as db:
            cur = db.execute("UPDATE knowledge SET status=?,updated_at=? WHERE id=?",
                             (status, now(), kid))
            return bool(cur.rowcount)
