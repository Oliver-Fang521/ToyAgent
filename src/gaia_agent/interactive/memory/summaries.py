"""Disposable encrypted summaries with canonical and transitive revision dependencies."""

from dataclasses import asdict
from uuid import uuid4

from .crypto import MemoryEnvelope
from .layer_models import FactRef, SummaryView
from .models import MemoryFailure
from .policy import SENSITIVE
from .retrieval import terms


class SummaryStore:
    def __init__(self, repository):
        self.repo = repository

    def _scope(self, scope):
        state = self.repo.state_snapshot(scope.owner, scope.provider)
        if not state.enabled or state.scope.generation != scope.generation:
            raise MemoryFailure("memory_conflict", 409)
        return state

    def read(self, scope, identifier, *, _seen=None):
        repo = self.repo
        with repo.lock:
            self._scope(scope)
            seen = set() if _seen is None else _seen
            if identifier in seen or len(seen) >= 100:
                raise MemoryFailure("memory_integrity", 422)
            seen.add(identifier)
            row = repo.db.execute("SELECT * FROM memory_summaries WHERE owner=? AND provider=? AND id=? AND state='ready'",
                                  (scope.owner, scope.provider, identifier)).fetchone()
            if row is None:
                raise MemoryFailure("memory_not_found", 404)
            data = repo.crypto.decrypt_object(scope.owner, "summary", row["id"],
                MemoryEnvelope(row["version"], row["nonce"], row["ciphertext"]))
            refs = tuple(FactRef(**r) for r in data["source_refs"])
            children = tuple(FactRef(**r) for r in data["child_refs"])
            for ref in refs:
                record = repo.get(scope.owner, ref.id)
                if record.revision != ref.revision or scope.provider not in record.allowed_providers or (record.expires_at is not None and record.expires_at <= repo.clock()):
                    raise MemoryFailure("memory_conflict", 409)
            for ref in children:
                child = self.read(scope, ref.id, _seen=seen.copy())
                if child.revision != ref.revision:
                    raise MemoryFailure("memory_conflict", 409)
            return SummaryView(row["id"], row["revision"], data["content"], refs,
                               "ready", children, row["kind"], data["topic_id"])

    def publish(self, scope, draft, input_revision):
        repo = self.repo
        with repo.lock, repo.db:
            state = self._scope(scope)
            if state.content_revision != input_revision:
                raise MemoryFailure("memory_conflict", 409)
            if SENSITIVE.search(draft.content) or (draft.kind == "shard" and draft.child_refs):
                raise MemoryFailure("memory_invalid", 422)
            if draft.kind not in {"shard", "topic", "overview", "overview_part"} or not draft.content.strip() or len(draft.content.encode()) > (1200 if draft.kind == "overview" else 4000) or len(draft.source_refs) + len(draft.child_refs) > 100 or not (draft.source_refs or draft.child_refs):
                raise MemoryFailure("memory_invalid", 422)
            for ref in draft.source_refs:
                record = repo.get(scope.owner, ref.id)
                if record.revision != ref.revision or scope.provider not in record.allowed_providers or (record.expires_at is not None and record.expires_at <= repo.clock()):
                    raise MemoryFailure("memory_conflict", 409)
            for ref in draft.child_refs:
                child = self.read(scope, ref.id)
                if child.revision != ref.revision or child.kind == "overview":
                    raise MemoryFailure("memory_conflict", 409)
            key = repo.crypto.canonical_hash(scope.owner, "summary:" + draft.kind + ":" + draft.topic_id, repo._settings(scope.owner)["hash_version"])
            old = repo.db.execute("SELECT id,revision FROM memory_summaries WHERE owner=? AND provider=? AND scope_key=?",
                                  (scope.owner, scope.provider, key)).fetchone()
            identifier, revision = (old["id"], old["revision"] + 1) if old else (str(uuid4()), 1)
            if any(r.id == identifier for r in draft.child_refs):
                raise MemoryFailure("memory_invalid", 422)
            if old:
                self.invalidate(scope.owner, (identifier,))
            data = {"content": draft.content, "topic_id": draft.topic_id,
                    "source_refs": [asdict(r) for r in draft.source_refs],
                    "child_refs": [asdict(r) for r in draft.child_refs]}
            e = repo.crypto.encrypt_object(scope.owner, "summary", identifier, data)
            repo.db.execute("INSERT OR REPLACE INTO memory_summaries VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, scope.owner, scope.provider, draft.kind, key, revision, "ready", e.version, e.nonce, e.ciphertext, repo.clock()))
            repo.db.execute("DELETE FROM memory_summary_deps WHERE owner=? AND summary_id=?", (scope.owner, identifier))
            repo.db.executemany("INSERT INTO memory_summary_deps VALUES(?,?,?,?,?)",
                ((scope.owner, identifier, r.id, r.revision, kind) for kind, refs in (("fact", draft.source_refs), ("summary", draft.child_refs)) for r in refs))
            repo.db.execute("DELETE FROM memory_terms WHERE owner=? AND fact_id=?", (scope.owner, identifier))
            repo.db.execute("DELETE FROM memory_indexed WHERE owner=? AND fact_id=?", (scope.owner, identifier))
            version = repo._settings(scope.owner)["hash_version"]
            repo.db.executemany("INSERT OR IGNORE INTO memory_terms VALUES(?,?,?)", ((scope.owner, identifier, repo.crypto.canonical_hash(scope.owner, "term:" + t, version)) for t in terms(draft.content)))
            repo.db.execute("INSERT INTO memory_indexed VALUES(?,?,?)", (scope.owner, identifier, revision))
            return self.read(scope, identifier)

    def overview(self, scope):
        with self.repo.lock:
            rows = self.repo.db.execute("SELECT id FROM memory_summaries WHERE owner=? AND provider=? AND kind='overview' AND state='ready' ORDER BY updated_at DESC LIMIT 1",
                                       (scope.owner, scope.provider)).fetchall()
            try:
                return self.read(scope, rows[0][0]) if rows else None
            except MemoryFailure:
                return None

    def invalidate(self, owner, fact_ids, *, all_scopes=False):
        db = self.repo.db
        if all_scopes:
            db.execute("DELETE FROM memory_terms WHERE owner=? AND fact_id IN (SELECT id FROM memory_summaries WHERE owner=?)", (owner, owner))
            db.execute("DELETE FROM memory_indexed WHERE owner=? AND fact_id IN (SELECT id FROM memory_summaries WHERE owner=?)", (owner, owner))
            db.execute("UPDATE memory_summaries SET state='stale',nonce=X'',ciphertext=X'' WHERE owner=?", (owner,))
            return
        pending, seen = list(fact_ids), set()
        while pending:
            source = pending.pop()
            if source in seen:
                continue
            seen.add(source)
            parents = db.execute("SELECT summary_id FROM memory_summary_deps WHERE owner=? AND source_id=?", (owner, source)).fetchall()
            for row in parents:
                db.execute("DELETE FROM memory_terms WHERE owner=? AND fact_id=?", (owner, row[0]))
                db.execute("DELETE FROM memory_indexed WHERE owner=? AND fact_id=?", (owner, row[0]))
                db.execute("UPDATE memory_summaries SET state='stale',nonce=X'',ciphertext=X'' WHERE owner=? AND id=?", (owner, row[0]))
                pending.append(row[0])
