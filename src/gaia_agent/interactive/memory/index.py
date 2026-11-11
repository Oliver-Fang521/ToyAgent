"""Account-domain inverted index; bounded decryption and resumable legacy scans."""

import asyncio
import base64
import json

from .crypto import MemoryEnvelope
from .layer_models import MemoryScope, SearchPage
from .models import MemoryFailure
from .repository import provider_mask
from .retrieval import terms


class MemoryIndex:
    def __init__(self, repository):
        self.repo = repository

    def _stamp(self, scope):
        row = self.repo._settings(scope.owner)
        if row["generation"] != scope.generation:
            raise MemoryFailure("memory_conflict", 409)
        revision = self.repo.db.execute(
            "SELECT content_revision FROM memory_settings WHERE owner=?", (scope.owner,)
        ).fetchone()[0]
        epoch = self.repo.db.execute("SELECT revision FROM memory_index_epochs WHERE owner=?", (scope.owner,)).fetchone()
        return (scope.owner, scope.provider, scope.generation, revision, epoch[0] if epoch else 0)

    def cursor(self, owner, value):
        e = self.repo.crypto.encrypt_object(owner, "cursor", "page", value)
        return base64.urlsafe_b64encode(json.dumps([
            e.version, base64.b64encode(e.nonce).decode(), base64.b64encode(e.ciphertext).decode()
        ]).encode()).decode()

    def decode(self, owner, cursor, expected):
        try:
            if not isinstance(cursor, str) or len(cursor) > 4096:
                raise ValueError
            v, n, c = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            value = self.repo.crypto.decrypt_object(
                owner, "cursor", "page", MemoryEnvelope(v, base64.b64decode(n), base64.b64decode(c))
            )
            if value["key"] != list(expected) or type(value["offset"]) is not int or value["offset"] < 0:
                raise ValueError
            return value["offset"]
        except (ValueError, TypeError, KeyError, MemoryFailure):
            raise MemoryFailure("memory_conflict", 409) from None

    def sync(self, owner, identifier):
        db = self.repo.db
        db.execute("DELETE FROM memory_terms WHERE owner=? AND fact_id=?", (owner, identifier))
        db.execute("DELETE FROM memory_indexed WHERE owner=? AND fact_id=?", (owner, identifier))
        db.execute("DELETE FROM memory_topic_members WHERE owner=? AND fact_id=?", (owner, identifier))
        row = db.execute("SELECT * FROM memory_entries WHERE owner=? AND id=?", (owner, identifier)).fetchone()
        if row is None:
            return
        record = self.repo._record(row)
        version = self.repo._settings(owner)["hash_version"]
        hashes = [self.repo.crypto.canonical_hash(owner, "term:" + t, version)
                  for t in terms(record.content + " " + (record.topic or "") + " " + record.entity)]
        db.executemany("INSERT OR IGNORE INTO memory_terms VALUES(?,?,?)",
                       ((owner, identifier, t) for t in hashes))
        db.execute("INSERT INTO memory_indexed VALUES(?,?,?)", (owner, identifier, record.revision))
        topic_id = self.repo.crypto.canonical_hash(owner, "topic:" + (record.topic or "") + ":" + record.entity, version)
        db.executemany("INSERT INTO memory_topic_members VALUES(?,?,?,?)",
                       ((owner, provider, topic_id, identifier) for provider in record.allowed_providers))

    def backfill(self, scope: MemoryScope, cursor=None, batch_size=100):
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise MemoryFailure("memory_invalid", 422)
        with self.repo.lock, self.repo.db:
            self._stamp(scope)
            rows = self.repo.db.execute("""SELECT e.id FROM memory_entries e
                LEFT JOIN memory_indexed i ON i.owner=e.owner AND i.fact_id=e.id
                WHERE e.owner=? AND (i.revision IS NULL OR i.revision!=e.revision)
                ORDER BY e.id LIMIT ?""", (scope.owner, batch_size)).fetchall()
            for row in rows:
                self.sync(scope.owner, row[0])
            pending = self.repo.db.execute("""SELECT 1 FROM memory_entries e
                LEFT JOIN memory_indexed i ON i.owner=e.owner AND i.fact_id=e.id
                WHERE e.owner=? AND (i.revision IS NULL OR i.revision!=e.revision) LIMIT 1""",
                (scope.owner,)).fetchone()
            return SearchPage(coverage="index_pending" if pending else "complete")

    def search(self, scope: MemoryScope, query, cursor=None, limit=20, *, include_summaries=False):
        if not isinstance(query, str) or len(query) > 512 or type(limit) is not int or not 1 <= limit <= 50:
            raise MemoryFailure("memory_invalid", 422)
        repo = self.repo
        with repo.lock, repo.db:
            stamp = self._stamp(scope)
            if not repo.settings(scope.owner).enabled and scope.provider != "management":
                return SearchPage()
            query = query.casefold().strip()
            key = (*stamp, query, include_summaries)
            offset = self.decode(scope.owner, cursor, key) if cursor else 0
            version = repo._settings(scope.owner)["hash_version"]
            hashes = tuple(repo.crypto.canonical_hash(scope.owner, "term:" + t, version) for t in sorted(terms(query))[:128])
            mask = 3 if scope.provider == "management" else provider_mask((scope.provider,))
            where = "e.owner=? AND (e.provider_mask & ?)!=0"
            args = [scope.owner, mask]
            if scope.provider != "management":
                where += " AND (e.expires_at IS NULL OR e.expires_at>?)"
                args.append(repo.clock())
            pending = repo.db.execute("""SELECT 1 FROM memory_entries e LEFT JOIN memory_indexed i
                ON i.owner=e.owner AND i.fact_id=e.id WHERE """ + where +
                " AND (i.revision IS NULL OR i.revision!=e.revision) LIMIT 1", args).fetchone()
            if hashes:
                matching = "SELECT COUNT(*) FROM memory_terms t WHERE t.owner=e.owner AND t.fact_id=e.id AND t.term IN (" + ",".join("?" for _ in hashes) + ")"
                where += " AND (i.revision IS NULL OR i.revision!=e.revision OR (" + matching + ")" + ("=?" if scope.provider == "management" else ">0") + ")"
                args.extend(hashes)
                if scope.provider == "management":
                    args.append(len(hashes))
            source = "memory_entries e"
            if include_summaries:
                source = """(SELECT id,owner,provider_mask,expires_at,revision,'fact' AS item_kind FROM memory_entries
                    UNION ALL SELECT id,owner,CASE provider WHEN 'openai' THEN 1 ELSE 2 END,NULL,revision,'topic'
                    FROM memory_summaries WHERE state='ready' AND kind='topic') e"""
            join = " FROM " + source + " LEFT JOIN memory_indexed i ON i.owner=e.owner AND i.fact_id=e.id WHERE "
            total = repo.db.execute("SELECT COUNT(*)" + join + where, args).fetchone()[0]
            # Scan at most 100 candidates per request. Continuation includes skipped legacy rows.
            score = "(" + matching + ")" if hashes else "0"
            rows = repo.db.execute("SELECT e.*," + score + " AS match_score" + join + where + " ORDER BY match_score DESC,e.id LIMIT 100 OFFSET ?", (*(hashes if hashes else ()), *args, offset)).fetchall()
            found, consumed = [], 0
            for row in rows:
                if include_summaries and row["item_kind"] == "topic":
                    from .summaries import SummaryStore

                    try:
                        record = SummaryStore(repo).read(scope, row["id"])
                    except MemoryFailure:
                        consumed += 1
                        continue
                else:
                    record = repo._record(repo._row(scope.owner, row["id"]))
                consumed += 1
                available = terms(record.content + " " + (getattr(record, "topic", "") or "") + " " + getattr(record, "entity", ""))
                matches = terms(query) <= available if scope.provider == "management" else bool(terms(query) & available)
                if not query or matches:
                    found.append(record)
                if len(found) == limit:
                    break
            more = offset + consumed < total
            next_cursor = self.cursor(scope.owner, {"key": key, "offset": offset + consumed}) if more else None
            coverage = "index_pending" if pending else ("truncated" if more else ("complete" if found else "not_found_in_search"))
            return SearchPage(tuple(found), next_cursor, total, coverage)


class MemoryIndexBackfill:
    """Deterministic migration only: no summary requests, no old-chat reads."""

    def __init__(self, repository):
        self.repo, self.task = repository, None

    def run_once(self):
        with self.repo.lock:
            row = self.repo.db.execute("""SELECT e.owner FROM memory_entries e
                LEFT JOIN memory_indexed i ON i.owner=e.owner AND i.fact_id=e.id
                WHERE i.revision IS NULL OR i.revision!=e.revision ORDER BY e.owner LIMIT 1""").fetchone()
            if row is None:
                return False
            scope = self.repo.state_snapshot(row[0], "openai").scope
            MemoryIndex(self.repo).backfill(scope)
            return True

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._loop())

    async def _loop(self):
        import sqlite3

        while True:
            try:
                progressed = self.run_once()
            except (MemoryFailure, sqlite3.Error):
                progressed = False
            await asyncio.sleep(0.01 if progressed else 1)

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
