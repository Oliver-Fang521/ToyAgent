from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

from gaia_agent.interactive.failures import failure_details
from gaia_agent.interactive.models import InteractiveRequest, resolve_selection
from gaia_agent.interactive.public_results import public_result


class ServiceError(RuntimeError):
    def __init__(self, code: str, status_code: int = 404):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class ConversationStore:
    def __init__(self, path: Path):
        self.data_root = path.parent
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, owner TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, conversation TEXT, owner TEXT,
                question TEXT, attachment TEXT, status TEXT, created REAL, result TEXT,
                progress TEXT DEFAULT '[]', error TEXT, idem TEXT, fingerprint TEXT,
                UNIQUE(owner, idem));
            CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY, conversation TEXT, owner TEXT,
                path TEXT, name TEXT, created REAL);
            CREATE INDEX IF NOT EXISTS runs_owner_created ON runs(owner, created);
            CREATE TABLE IF NOT EXISTS conversation_index (
                run_id TEXT PRIMARY KEY, owner TEXT NOT NULL, conversation TEXT NOT NULL,
                summary TEXT NOT NULL, keywords TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS conversation_index_scope ON conversation_index(owner, conversation);
        """)
        self.db.commit()
        if "failure" not in {row[1] for row in self.db.execute("PRAGMA table_info(runs)")}:
            with self.db:
                self.db.execute("ALTER TABLE runs ADD COLUMN failure TEXT")
        for table, column in (
            ("conversations", "provider"),
            ("runs", "provider"),
            ("runs", "model"),
            ("runs", "reasoning_effort"),
            ("runs", "capability_version"),
        ):
            if column not in {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}:
                with self.db:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")

    def create(self, owner: str, provider: str) -> dict:
        if provider not in {"openai", "deepseek"}:
            raise ServiceError("model_unavailable", 422)
        identifier = str(uuid4())
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO conversations(id,owner,created,provider) VALUES(?,?,?,?)",
                (identifier, owner, time.time(), provider),
            )
        return self.get(owner, identifier)

    def get(self, owner: str, identifier: str) -> dict:
        row = self.db.execute(
            "SELECT * FROM conversations WHERE id=? AND owner=?", (identifier, owner)
        ).fetchone()
        if row is None:
            raise ServiceError("not_found")
        return dict(row)

    def list(self, owner: str) -> list[dict]:
        rows = self.db.execute(
            """SELECT c.*, (SELECT r.question FROM runs r
                   WHERE r.conversation=c.id AND r.owner=c.owner
                   ORDER BY r.created ASC, r.rowid ASC LIMIT 1) AS first_question
               FROM conversations c WHERE c.owner=? ORDER BY c.created DESC LIMIT 100""",
            (owner,),
        )
        conversations = []
        for row in rows:
            item = dict(row)
            question = " ".join((item.pop("first_question") or "").split())
            item["title"] = question[:60] + ("…" if len(question) > 60 else "")
            conversations.append(item)
        return conversations

    def reserve(self, owner: str, cid: str, request: InteractiveRequest, idem: str) -> dict:
        selection = resolve_selection(request)
        fingerprint = self._fingerprint(cid, request, selection.reasoning_effort)
        with self.lock, self.db:
            self.get(owner, cid)
            existing = self.db.execute(
                "SELECT * FROM runs WHERE owner=? AND idem=?", (owner, idem)
            ).fetchone()
            if existing:
                return self.lookup(owner, cid, request, idem)
            self.check_provider(owner, cid, request.provider)
            if self.db.execute(
                "SELECT count(*) FROM runs WHERE owner=? AND status IN ('queued','running')",
                (owner,),
            ).fetchone()[0]:
                raise ServiceError("active_run_limit", 429)
            if (
                self.db.execute(
                    "SELECT count(*) FROM runs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                >= 12
            ):
                raise ServiceError("service_busy", 503)
            identifier = str(uuid4())
            self.db.execute(
                "INSERT INTO runs(id,conversation,owner,question,attachment,status,created,idem,fingerprint,provider,model,reasoning_effort,capability_version) VALUES(?,?,?,?,?,'queued',?,?,?,?,?,?,?)",
                (
                    identifier,
                    cid,
                    owner,
                    request.question,
                    request.attachment_id,
                    time.time(),
                    idem,
                    fingerprint,
                    request.provider,
                    request.model,
                    selection.reasoning_effort,
                    selection.capability_version,
                ),
            )
            return self.run(owner, identifier)

    def check_provider(self, owner: str, cid: str, provider: str):
        if self.get(owner, cid)["provider"] != provider:
            raise ServiceError("conversation_provider_mismatch", 409)

    def lookup(self, owner: str, cid: str, request: InteractiveRequest, idem: str) -> dict | None:
        self.get(owner, cid)
        row = self.db.execute(
            "SELECT id,fingerprint,reasoning_effort,capability_version FROM runs WHERE owner=? AND idem=?",
            (owner, idem),
        ).fetchone()
        if row is None:
            return None
        if row["capability_version"] is None:
            fingerprint = hashlib.sha256(
                (cid + request.model_dump_json(exclude={"reasoning_effort"})).encode()
            ).hexdigest()
            if request.reasoning_effort is not None:
                raise ServiceError("idempotency_conflict", 409)
        else:
            effort = (
                request.reasoning_effort
                if request.reasoning_effort is not None
                else row["reasoning_effort"]
            )
            fingerprint = self._fingerprint(cid, request, effort)
        if row["fingerprint"] != fingerprint:
            raise ServiceError("idempotency_conflict", 409)
        return self.run(owner, row["id"])

    @staticmethod
    def _fingerprint(cid, request, effort):
        body = request.model_dump()
        body["reasoning_effort"] = effort
        return hashlib.sha256(
            (cid + json.dumps(body, sort_keys=True, separators=(",", ":"))).encode()
        ).hexdigest()

    def run(self, owner: str, identifier: str) -> dict:
        row = self.db.execute(
            "SELECT * FROM runs WHERE id=? AND owner=?", (identifier, owner)
        ).fetchone()
        if row is None:
            raise ServiceError("not_found")
        result = dict(row)
        result["result"] = json.loads(result["result"]) if result["result"] else None
        result["result"] = public_result(result["result"], self.data_root / "runs" / identifier)
        result["progress"] = json.loads(result["progress"])
        result["failure"] = (
            json.loads(result["failure"])
            if result["failure"]
            else (
                failure_details(result["error"], result["progress"])
                if result["status"] in {"failed", "interrupted"}
                else None
            )
        )
        return {
            key: value
            for key, value in result.items()
            if key not in {"fingerprint", "idem", "owner"}
        }

    def update(self, identifier: str, *, status: str, result=None, error=None, failure=None):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE runs SET status=?,result=?,error=?,failure=? WHERE id=? AND status NOT IN ('cancelled','interrupted')",
                (
                    status,
                    json.dumps(result) if result is not None else None,
                    error,
                    json.dumps(failure) if failure else None,
                    identifier,
                ),
            )

    def progress(self, identifier: str, event: str, payload: dict):
        # Only semantic stage names and counts; never model/tool text or arguments.
        if event not in {
            "research_activity",
            "first_model_text",
            "first_answer_ready",
            "tool_call_started",
            "provider_retry_exhausted",
            "stage_response_diagnostic",
            "validation_rejected",
            "chat_completed",
            "clarification_completed",
            "routing_started",
            "routing_completed",
            "tool_call_failed",
            "tool_call_completed",
            "evidence_repair_started",
            "plan_started",
            "plan_completed",
            "act_started",
            "act_completed",
            "verify_started",
            "verify_completed",
            "submit_started",
            "submit_completed",
            "correction_started",
            "correction_completed",
        }:
            return
        entry = {"event": event, "time": time.time()}
        if event == "research_activity":
            detail = payload.get("detail")
            tool = payload.get("tool")
            if tool not in {"web_search", "read_webpage"} or not isinstance(detail, str):
                return
            detail = re.sub(r"(?i)\b(?:sk|api[_-]?key|token|bearer)[-_:= ]+[a-z0-9_-]{8,}", "[REDACTED]", detail)
            entry.update(tool=tool, detail=" ".join(detail.split())[:160])
        if event == "provider_retry_exhausted":
            categories = {"APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError", "provider_transient"}
            if payload.get("category") not in categories or type(payload.get("attempt_count")) is not int or not 1 <= payload["attempt_count"] <= 100:
                return
            entry.update(category=payload["category"], attempt_count=payload["attempt_count"])
        if event == "stage_response_diagnostic":
            allowed = {
                "stage": {"planner", "specialist", "critic"},
                "phase": {"initial", "repair"},
                "code": {"valid", "model_call", "role_mismatch", "evidence_shape", "source_url_missing", "candidate_format", "checked_evidence", "schema"},
                "response_status": {"completed", "incomplete", "failed", "unknown"},
                "incomplete_reason": {"none", "max_output_tokens", "content_filter", "other"},
            }
            if not all(payload.get(key) in values for key, values in allowed.items()):
                return
            entry.update({key: payload[key] for key in allowed})
            from gaia_agent.diagnostics import safe_model_metrics
            entry.update(safe_model_metrics(payload))
        if event == "validation_rejected" and payload.get("reason") in {
            "insufficient_evidence",
            "revise",
        }:
            entry["reason"] = payload["reason"]
        if event.startswith("tool_call_") and payload.get("tool") in {
            "web_search",
            "read_webpage",
            "execute_python",
            "search_memory",
            "read_memory",
            "read_attachment",
            "transcribe_audio",
            "submit_answer",
        }:
            entry["tool"] = payload["tool"]
            if payload.get("provider") in {"openai", "deepseek", "serper", "serpapi"}:
                entry["provider"] = payload["provider"]
            if payload.get("category") in {
                "tool_error",
                "repeated_call",
                "tool_budget_exhausted",
                "timeout",
                "authentication",
                "balance",
                "forbidden",
                "invalid_input",
                "rate_limit",
                "upstream",
                "policy_rejection",
                "http_status",
                "parse_failure",
                "search_unavailable",
                "transport",
            }:
                entry["category"] = payload["category"]
            status = payload.get("http_status")
            if type(status) is int and 100 <= status <= 599:
                entry["http_status"] = status
        with self.lock, self.db:
            row = self.db.execute("SELECT progress FROM runs WHERE id=?", (identifier,)).fetchone()
            events = json.loads(row[0])[-99:] + [entry]
            self.db.execute(
                "UPDATE runs SET progress=? WHERE id=?", (json.dumps(events), identifier)
            )

    def messages(self, owner: str, cid: str, offset: int = 0) -> dict:
        self.get(owner, cid)
        rows = self.db.execute(
            "SELECT id,question,result,provider,model,reasoning_effort,capability_version FROM runs WHERE conversation=? AND owner=? AND status='completed' ORDER BY created,rowid LIMIT 101 OFFSET ?",
            (cid, owner, offset),
        ).fetchall()
        messages = []
        for row in rows[:100]:
            result = public_result(json.loads(row["result"]), self.data_root / "runs" / row["id"])
            messages.extend(
                [
                    {"role": "user", "content": row["question"]},
                    {
                        "role": "assistant",
                        "content": result["answer"],
                        "evidence": result.get("evidence", []),
                        "provider": row["provider"],
                        "model": row["model"],
                        "reasoning_effort": row["reasoning_effort"],
                        "capability_version": row["capability_version"],
                        **(
                            {"memory": result["memory"]} if result.get("memory") is not None else {}
                        ),
                    },
                ]
            )
        return {"messages": messages, "next_offset": offset + 100 if len(rows) > 100 else None}

    def bind_run_attachment(self, owner: str, cid: str, run_id: str, attachment_id: str) -> Path:
        path = self.attachment(owner, cid, attachment_id)
        with self.lock, self.db:
            cursor = self.db.execute(
                "UPDATE runs SET attachment=? WHERE id=? AND owner=? AND conversation=? AND status='running'",
                (attachment_id, run_id, owner, cid),
            )
            if cursor.rowcount != 1:
                raise ServiceError("not_found")
        return path

    def save_conversation_index(self, owner: str, cid: str, run_id: str, summary: str, keywords: list[str], *, usage=None) -> None:
        self.get(owner, cid)
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT result FROM runs WHERE id=? AND owner=? AND conversation=? AND status='completed'",
                (run_id, owner, cid),
            ).fetchone()
            if not row:
                raise ServiceError("not_found")
            fields = (run_id, owner, cid, summary[:500], json.dumps(keywords[:20], ensure_ascii=False))
            if usage is None:
                self.db.execute("INSERT OR REPLACE INTO conversation_index VALUES (?,?,?,?,?)", fields)
                return
            inserted = self.db.execute("INSERT OR IGNORE INTO conversation_index VALUES (?,?,?,?,?)", fields)
            if inserted.rowcount:
                from gaia_agent.interactive.routing import combined_usage
                from gaia_agent.models import Usage

                result = json.loads(row["result"])
                result["usage"] = combined_usage(Usage.model_validate(result.get("usage") or {}), usage).model_dump()
                self.db.execute("UPDATE runs SET result=? WHERE id=? AND owner=? AND conversation=?",
                                (json.dumps(result), run_id, owner, cid))

    def conversation_attachments(self, owner: str, cid: str) -> list[dict]:
        self.get(owner, cid)
        rows = self.db.execute(
            """SELECT DISTINCT u.id, u.name, u.path, u.created FROM uploads u
               JOIN runs r ON r.attachment=u.id AND r.owner=u.owner AND r.conversation=u.conversation
               WHERE u.owner=? AND u.conversation=? ORDER BY u.created DESC""",
            (owner, cid),
        ).fetchall()
        result = []
        for row in rows:
            path = Path(row["path"])
            result.append({"id": row["id"], "name": row["name"],
                "type": path.suffix.lower(), "available": path.is_file() and not path.is_symlink()
                and row["created"] >= time.time() - 7 * 86400})
        return result

    def search_conversation(self, owner: str, cid: str, query: str, limit: int = 8) -> list[dict]:
        import re
        self.get(owner, cid)
        query = query.lower()[:256]
        terms = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", query)
        tokens = set()
        for term in terms:
            if re.fullmatch(r"[\u4e00-\u9fff]+", term) and len(term) > 2:
                tokens.update(term[i:i+2] for i in range(len(term)-1))
            else:
                tokens.add(term)
        if not tokens:
            return []
        rows = self.db.execute(
            """SELECT r.id,r.question,r.result,r.attachment,u.name,i.summary,i.keywords FROM runs r
               LEFT JOIN conversation_index i ON i.run_id=r.id AND i.owner=r.owner AND i.conversation=r.conversation
               LEFT JOIN uploads u ON u.id=r.attachment AND u.owner=r.owner AND u.conversation=r.conversation
               WHERE r.owner=? AND r.conversation=? AND r.status='completed'
               ORDER BY r.created DESC, r.rowid DESC""", (owner, cid)).fetchall()
        ranked = []
        catalog = {item['id']: item for item in self.conversation_attachments(owner, cid)}
        for row in rows:
            try:
                answer = json.loads(row['result'] or '{}').get('answer', '')
            except (ValueError, AttributeError):
                continue
            text = f"{row['question']} {answer} {row['name'] or ''} {row['summary'] or ''} {row['keywords'] or ''}".lower()
            score = sum(token in text for token in tokens)
            if score:
                attachment = catalog.get(row['attachment'])
                ranked.append((score, {"run_id": row['id'], "question": row['question'],
                    "answer": answer, "summary": row['summary'], "attachments": [attachment] if attachment else []}))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in ranked[:max(0, min(limit, 20))]]

    def history(self, owner: str, cid: str) -> list[dict[str, str]]:
        self.get(owner, cid)
        rows = self.db.execute(
            "SELECT question,result FROM runs WHERE conversation=? AND owner=? AND status='completed' ORDER BY created DESC LIMIT 4",
            (cid, owner),
        ).fetchall()
        result = []
        for row in reversed(rows):
            result.extend(
                [
                    {"role": "user", "content": row[0]},
                    {"role": "assistant", "content": json.loads(row[1])["answer"]},
                ]
            )
        return result

    def latest_run(self, owner, cid):
        self.get(owner, cid)
        row = self.db.execute(
            "SELECT id FROM runs WHERE conversation=? AND owner=? ORDER BY created DESC,rowid DESC LIMIT 1",
            (cid, owner),
        ).fetchone()
        return self.run(owner, row[0]) if row else None

    def interrupt_unfinished(self):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE runs SET status='interrupted',error='service_restarted' WHERE status IN ('queued','running')"
            )

    def upload(self, owner: str, cid: str, path: Path, name: str) -> str:
        self.get(owner, cid)
        identifier = str(uuid4())
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO uploads VALUES(?,?,?,?,?,?)",
                (identifier, cid, owner, str(path), name, time.time()),
            )
        return identifier

    def attachment(self, owner: str, cid: str, identifier: str) -> Path:
        row = self.db.execute(
            "SELECT path,created FROM uploads WHERE id=? AND owner=? AND conversation=?",
            (identifier, owner, cid),
        ).fetchone()
        if row is None:
            raise ServiceError("attachment_not_found")
        path = Path(row[0])
        if not path.is_file() or path.is_symlink() or row["created"] < time.time() - 7 * 86400:
            raise ServiceError("attachment_expired")
        return path
