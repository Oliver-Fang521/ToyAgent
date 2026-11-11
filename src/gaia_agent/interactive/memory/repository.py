"""Bounded encrypted records. All SQL authorization precedes decryption."""

import math
import time
from uuid import uuid4

from .crypto import MemoryEnvelope
from .models import MemoryFailure, MemoryListSnapshot, MemoryRecord, MemorySettings, MemoryView
from .schema import migrate_memory_schema

PROVIDER_BITS = {"openai": 1, "deepseek": 2}


def shared_providers(origin):
    if origin not in PROVIDER_BITS:
        raise MemoryFailure("memory_invalid", 422)
    return (origin, "deepseek" if origin == "openai" else "openai")


def provider_mask(providers):
    if not providers or len(set(providers)) != len(providers):
        raise MemoryFailure("memory_invalid", 422)
    try:
        return sum(PROVIDER_BITS[p] for p in providers)
    except (KeyError, TypeError):
        raise MemoryFailure("memory_invalid", 422) from None


class MemoryRepository:
    def __init__(self, db, lock, crypto, clock=time.time):
        self.db, self.lock, self.crypto, self.clock = db, lock, crypto, clock
        with lock:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS memory_settings(
                    owner TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
                    revision INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL DEFAULT 0,
                    capture_after INTEGER NOT NULL DEFAULT 0, hash_version TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memory_entries(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, category TEXT NOT NULL,
                    canonical_key TEXT NOT NULL, facet_hash TEXT NOT NULL,
                    revision INTEGER NOT NULL, origin_provider TEXT NOT NULL,
                    provider_mask INTEGER NOT NULL, version TEXT NOT NULL,
                    nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
                    source_run_id TEXT, source_start INTEGER, source_end INTEGER,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    last_confirmed_at REAL NOT NULL, expires_at REAL,
                    edited_by_user INTEGER NOT NULL,
                    UNIQUE(owner,canonical_key));
                CREATE INDEX IF NOT EXISTS memory_owner ON memory_entries(owner);
                CREATE TABLE IF NOT EXISTS memory_tombstones(
                    owner TEXT NOT NULL, canonical_key TEXT NOT NULL,
                    facet_hash TEXT NOT NULL, hash_version TEXT NOT NULL,
                    source_watermark INTEGER NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(owner,canonical_key));
                CREATE TABLE IF NOT EXISTS memory_turns(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL,
                    run_id TEXT NOT NULL, processed INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(owner,run_id));
                CREATE TABLE IF NOT EXISTS memory_shared_migration(
                    owner TEXT PRIMARY KEY);
            """)
            migrate_memory_schema(db)

    def _settings(self, owner):
        self.db.execute(
            """INSERT OR IGNORE INTO memory_settings(owner,hash_version)
                           VALUES(?,?)""",
            (owner, self.crypto.active_version),
        )
        self._migrate_shared_memory(owner)
        row = self.db.execute(
            """SELECT enabled,revision,generation,capture_after,hash_version
                                 FROM memory_settings WHERE owner=?""",
            (owner,),
        ).fetchone()
        # Verify the pinned historical key exists even when no active rows remain.
        self.crypto.canonical_hash(owner, "", row["hash_version"])
        return row

    def _migrate_shared_memory(self, owner):
        if self.db.execute("SELECT 1 FROM memory_shared_migration WHERE owner=?", (owner,)).fetchone():
            return
        # Mark first so calls through index or summary helpers cannot re-enter the migration.
        inserted = self.db.execute(
            "INSERT OR IGNORE INTO memory_shared_migration(owner) VALUES(?)", (owner,)
        ).rowcount
        if not inserted:
            return
        changed = self.db.execute(
            "UPDATE memory_entries SET provider_mask=3 WHERE owner=? AND provider_mask!=3", (owner,)
        ).rowcount
        barriers = self.db.execute(
            "UPDATE memory_barriers SET provider_mask=3 WHERE owner=? AND provider_mask!=3", (owner,)
        ).rowcount
        if not changed and not barriers:
            return
        for source, target in (("openai", "deepseek"), ("deepseek", "openai")):
            self.db.execute("""INSERT OR IGNORE INTO memory_topic_members(owner,provider,topic_id,fact_id)
                SELECT owner,?,topic_id,fact_id FROM memory_topic_members WHERE owner=? AND provider=?""",
                (target, owner, source))
        self.db.execute("""INSERT OR IGNORE INTO memory_dirty_topics(owner,provider,topic_id)
            SELECT DISTINCT owner,provider,topic_id FROM memory_topic_members WHERE owner=?""", (owner,))
        from .summaries import SummaryStore
        SummaryStore(self).invalidate(owner, (), all_scopes=True)
        self.db.execute("DELETE FROM memory_projections WHERE owner=?", (owner,))
        self.db.execute("UPDATE memory_settings SET generation=generation+1,revision=revision+1,content_revision=content_revision+1 WHERE owner=?", (owner,))

    def settings(self, owner):
        with self.lock, self.db:
            row = self._settings(owner)
            return MemorySettings(
                enabled=bool(row["enabled"]),
                revision=row["revision"],
                generation=row["generation"],
                capture_after=row["capture_after"],
            )

    def _row(self, owner, identifier):
        row = self.db.execute(
            "SELECT * FROM memory_entries WHERE owner=? AND id=?", (owner, identifier)
        ).fetchone()
        if row is None:
            raise MemoryFailure("memory_not_found", 404)
        return row

    def _payload(self, row):
        return self.crypto.decrypt(
            row["owner"], row["id"], MemoryEnvelope(row["version"], row["nonce"], row["ciphertext"])
        )

    @staticmethod
    def _providers(row):
        return tuple(p for p, bit in PROVIDER_BITS.items() if row["provider_mask"] & bit)

    def _record(self, row):
        payload = self._payload(row)
        return MemoryRecord(
            id=row["id"],
            owner=row["owner"],
            category=row["category"],
            canonical_key=row["canonical_key"],
            revision=row["revision"],
            origin_provider=row["origin_provider"],
            allowed_providers=self._providers(row),
            status="active",
            content=payload["content"],
            source_run_id=row["source_run_id"],
            source_start=row["source_start"],
            source_end=row["source_end"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_confirmed_at=row["last_confirmed_at"],
            expires_at=row["expires_at"],
            edited_by_user=bool(row["edited_by_user"]),
            topic=payload.get("topic", payload.get("facet")),
            slot_id=payload.get("slot_id", row["id"]),
            cardinality=payload.get("cardinality", "single"),
            origin_kind=payload.get(
                "origin_kind", "manual" if row["edited_by_user"] else "automatic"
            ),
            subject=payload.get("subject"),
            entity=payload["entity"],
        )

    def _view(self, row):
        payload = self._payload(row)
        return MemoryView(
            id=row["id"],
            category=row["category"],
            facet=payload["facet"],
            entity=payload["entity"],
            content=payload["content"],
            revision=row["revision"],
            origin_provider=row["origin_provider"],
            allowed_providers=self._providers(row),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_confirmed_at=row["last_confirmed_at"],
            expires_at=row["expires_at"],
            edited_by_user=bool(row["edited_by_user"]),
            topic=payload.get("topic", payload.get("facet")),
            slot_id=payload.get("slot_id", row["id"]),
            cardinality=payload.get("cardinality", "single"),
            origin_kind=payload.get(
                "origin_kind", "manual" if row["edited_by_user"] else "automatic"
            ),
        )

    def get(self, owner, identifier):
        with self.lock, self.db:
            self._settings(owner)
            return self._record(self._row(owner, identifier))

    def list(self, owner, offset=0, limit=50):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise MemoryFailure("memory_invalid", 422)
        with self.lock, self.db:
            self._settings(owner)
            rows = self.db.execute(
                """SELECT * FROM memory_entries WHERE owner=?
                ORDER BY updated_at DESC,id LIMIT ? OFFSET ?""",
                (owner, limit, offset),
            ).fetchall()
            return [self._view(row) for row in rows]

    def list_snapshot(self, owner: str) -> MemoryListSnapshot:
        from .policy import FACETS

        with self.lock, self.db:
            row = self._settings(owner)
            settings = MemorySettings(
                enabled=bool(row["enabled"]),
                revision=row["revision"],
                generation=row["generation"],
                capture_after=row["capture_after"],
            )
            rows = self.db.execute(
                "SELECT * FROM memory_entries WHERE owner=? ORDER BY updated_at DESC,id LIMIT 50",
                (owner,),
            ).fetchall()
            blocked = {
                entry[0]
                for entry in self.db.execute(
                    "SELECT DISTINCT facet_hash FROM memory_tombstones WHERE owner=?", (owner,)
                )
            }
            suppressed = tuple(
                {"category": category, "facet": facet}
                for category, facets in FACETS.items()
                for facet in sorted(facets)
                if self.crypto.canonical_hash(
                    owner, f"facet:{category}:{facet}", row["hash_version"]
                )
                in blocked
            )
            return MemoryListSnapshot(
                settings=settings,
                items=tuple(self._view(entry) for entry in rows),
                suppressed_facets=suppressed,
            )

    def eligible(self, owner, provider, now, *, query=None, limit=50):
        if query is not None:
            from .index import MemoryIndex
            from .layer_models import MemoryScope

            return MemoryIndex(self).search(
                MemoryScope(owner, provider, self.settings(owner).generation), query, limit=limit
            ).items
        mask = provider_mask((provider,))
        with self.lock, self.db:
            if not self._settings(owner)["enabled"]:
                return ()
            rows = self.db.execute(
                """SELECT * FROM memory_entries WHERE owner=?
                AND (provider_mask & ?) != 0 AND (expires_at IS NULL OR expires_at>?)
                ORDER BY updated_at DESC,id LIMIT ?""",
                (owner, mask, now, limit),
            ).fetchall()
            return tuple(self._record(row) for row in rows)

    def state_snapshot(self, owner, provider):
        from .layer_models import MemoryScope, MemoryState

        with self.lock, self.db:
            settings = self.settings(owner)
            revision = self.db.execute("SELECT content_revision FROM memory_settings WHERE owner=?", (owner,)).fetchone()[0]
            return MemoryState(MemoryScope(owner, provider, settings.generation), settings.enabled, revision)

    def page_records(self, owner, cursor=None, limit=50):
        from .index import MemoryIndex
        from .layer_models import SearchPage

        if type(limit) is not int or not 1 <= limit <= 50:
            raise MemoryFailure("memory_invalid", 422)
        with self.lock, self.db:
            settings = self._settings(owner)
            key = (owner, "management", settings["generation"])
            index = MemoryIndex(self)
            offset = index.decode(owner, cursor, key) if cursor else 0
            total = self.db.execute("SELECT COUNT(*) FROM memory_entries WHERE owner=?", (owner,)).fetchone()[0]
            items = tuple(self.list(owner, offset, limit))
            next_cursor = index.cursor(owner, {"key": key, "offset": offset + len(items)}) if offset + len(items) < total else None
            return SearchPage(items, next_cursor, total, "truncated" if next_cursor else "complete")

    def _sync_index(self, owner, identifier):
        from .index import MemoryIndex
        from .summaries import SummaryStore

        old = list(self.db.execute("SELECT owner,provider,topic_id FROM memory_topic_members WHERE owner=? AND fact_id=?", (owner, identifier)))
        SummaryStore(self).invalidate(owner, (identifier,))
        MemoryIndex(self).sync(owner, identifier)
        updated = list(self.db.execute("SELECT owner,provider,topic_id FROM memory_topic_members WHERE owner=? AND fact_id=?", (owner, identifier)))
        self.db.executemany("INSERT OR IGNORE INTO memory_dirty_topics VALUES(?,?,?)", (tuple(r) for r in (*old, *updated)))

    def register_turn(self, owner, run_id):
        with self.lock, self.db:
            self._settings(owner)
            self.db.execute(
                "INSERT OR IGNORE INTO memory_turns(owner,run_id) VALUES(?,?)", (owner, run_id)
            )
            return self.db.execute(
                "SELECT seq FROM memory_turns WHERE owner=? AND run_id=?", (owner, run_id)
            ).fetchone()[0]

    def suppressed_facets(self, owner, known_facets):
        with self.lock, self.db:
            settings = self._settings(owner)
            blocked = {
                row[0]
                for row in self.db.execute(
                    "SELECT DISTINCT facet_hash FROM memory_tombstones WHERE owner=?", (owner,)
                )
            }
            return [
                {"category": category, "facet": facet}
                for category, facets in known_facets.items()
                for facet in sorted(facets)
                if self.crypto.canonical_hash(
                    owner, f"facet:{category}:{facet}", settings["hash_version"]
                )
                in blocked
            ]

    def _hashes(self, owner, write, version):
        return (
            self.crypto.canonical_hash(owner, write.normalized_key, version),
            self.crypto.canonical_hash(
                owner, f"facet:{write.candidate.category}:{write.candidate.facet}", version
            ),
        )

    def _bump(self, owner):
        self.db.execute(
            """UPDATE memory_settings SET generation=generation+1,revision=revision+1
                           WHERE owner=?""",
            (owner,),
        )

    @staticmethod
    def _cas(row, revision):
        if type(revision) is not int or row["revision"] != revision:
            raise MemoryFailure("memory_conflict", 409)

    def _save(self, owner, write, providers, manual, source_run_id=None, existing=None):
        settings = self._settings(owner)
        key, facet = self._hashes(owner, write, settings["hash_version"])
        mask = provider_mask(providers)
        identifier = existing["id"] if existing else str(uuid4())
        candidate = write.candidate
        payload = {
            "content": candidate.content,
            "facet": candidate.facet,
            "entity": candidate.entity,
            "normalized_key": write.normalized_key,
            "source_fragment": write.source_fragment,
        }
        envelope = self.crypto.encrypt(owner, identifier, payload)
        now = self.clock()
        revision = existing["revision"] + 1 if existing else 1
        if existing:
            self.db.execute(
                "DELETE FROM memory_entries WHERE owner=? AND id=? AND revision=?",
                (owner, identifier, existing["revision"]),
            )
        self.db.execute(
            """INSERT INTO memory_entries(id,owner,category,canonical_key,facet_hash,
            revision,origin_provider,provider_mask,version,nonce,ciphertext,source_run_id,
            source_start,source_end,created_at,updated_at,last_confirmed_at,expires_at,edited_by_user)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                identifier,
                owner,
                candidate.category,
                key,
                facet,
                revision,
                existing["origin_provider"] if existing else providers[0],
                mask,
                envelope.version,
                envelope.nonce,
                envelope.ciphertext,
                source_run_id,
                candidate.source_start if source_run_id else None,
                candidate.source_end if source_run_id else None,
                existing["created_at"] if existing else now,
                now,
                now,
                existing["expires_at"] if existing else None,
                int(manual),
            ),
        )
        self._sync_index(owner, identifier)
        return identifier

    def save_manual(self, owner, write, allowed_providers, expected_revision=None):
        with self.lock, self.db:
            settings = self._settings(owner)
            key, facet = self._hashes(owner, write, settings["hash_version"])
            existing = self.db.execute(
                "SELECT * FROM memory_entries WHERE owner=? AND canonical_key=?", (owner, key)
            ).fetchone()
            if existing:
                self._cas(existing, expected_revision)
            identifier = self._save(owner, write, allowed_providers, True, existing=existing)
            # Explicit manual re-add restores this conservative facet-level capture permission.
            self.db.execute(
                "DELETE FROM memory_tombstones WHERE owner=? AND facet_hash=?", (owner, facet)
            )
            self._bump(owner)
            return self._view(self._row(owner, identifier))

    def apply_candidates(self, owner, provider, run_id, writes, expected_generation):
        if len(writes) > 5:
            raise MemoryFailure("memory_invalid", 422)
        with self.lock, self.db:
            settings = self._settings(owner)
            turn = self.db.execute(
                "SELECT seq,processed FROM memory_turns WHERE owner=? AND run_id=?", (owner, run_id)
            ).fetchone()
            if (
                not turn
                or turn["processed"] == 1
                or turn["seq"] <= settings["capture_after"]
                or not settings["enabled"]
                or settings["generation"] != expected_generation
            ):
                return ()
            ids = []
            from .barriers import MemoryBarriers

            barriers = MemoryBarriers(self).visible(owner, provider)
            for write in writes:
                key, facet = self._hashes(owner, write, settings["hash_version"])
                if any(
                    b["canonical_key"] == key
                    or (
                        b["category"] == write.candidate.category
                        and b["topic"] == write.candidate.facet
                        and b["entity"] == write.candidate.entity
                    )
                    for b in barriers
                ):
                    continue
                blocked = self.db.execute(
                    "SELECT 1 FROM memory_tombstones WHERE owner=? AND facet_hash=?", (owner, facet)
                ).fetchone()
                if blocked and not write.candidate.explicit_relearn:
                    continue
                existing = self.db.execute(
                    "SELECT * FROM memory_entries WHERE owner=? AND canonical_key=?", (owner, key)
                ).fetchone()
                if existing and (
                    existing["edited_by_user"]
                    or not existing["provider_mask"] & provider_mask((provider,))
                ):
                    continue
                if existing and self._payload(existing)["content"] == write.candidate.content:
                    continue
                try:
                    identifier = self._save(
                        owner,
                        write,
                        self._providers(existing) if existing else shared_providers(provider),
                        False,
                        run_id,
                        existing,
                    )
                except MemoryFailure as error:
                    if error.code == "memory_limit":
                        continue
                    raise
                ids.append(identifier)
                if blocked:
                    self.db.execute(
                        "DELETE FROM memory_tombstones WHERE owner=? AND facet_hash=?",
                        (owner, facet),
                    )
            self.db.execute(
                "UPDATE memory_turns SET processed=1 WHERE owner=? AND run_id=?", (owner, run_id)
            )
            return tuple(ids)

    def claim_extraction(self, owner, run_id, generation):
        """Claim before paid work; a failed extraction is deliberately not retried."""
        with self.lock, self.db:
            settings = self._settings(owner)
            if not settings["enabled"] or settings["generation"] != generation:
                return False
            result = self.db.execute(
                """UPDATE memory_turns SET processed=2 WHERE owner=? AND run_id=?
                AND processed=0 AND seq>?""",
                (owner, run_id, settings["capture_after"]),
            )
            return result.rowcount == 1

    def update(self, owner, identifier, patch, expected_revision):
        with self.lock, self.db:
            self._settings(owner)
            row = self._row(owner, identifier)
            self._cas(row, expected_revision)
            if not patch or set(patch) - {"content", "allowed_providers", "expires_at"}:
                raise MemoryFailure("memory_invalid", 422)
            payload = self._payload(row)
            if "content" in patch:
                if (
                    not isinstance(patch["content"], str)
                    or not 1 <= len(patch["content"].strip()) <= 500
                ):
                    raise MemoryFailure("memory_invalid", 422)
                payload["content"] = patch["content"].strip()
                payload["source_fragment"] = None
                payload["subject"] = None
                payload["origin_kind"] = "manual"
            expiry = patch.get("expires_at", row["expires_at"])
            if expiry is not None and (
                type(expiry) not in (int, float)
                or not math.isfinite(expiry)
                or expiry <= self.clock()
            ):
                raise MemoryFailure("memory_invalid", 422)
            mask = (
                provider_mask(patch["allowed_providers"])
                if "allowed_providers" in patch
                else row["provider_mask"]
            )
            envelope = self.crypto.encrypt(owner, identifier, payload)
            self.db.execute(
                """UPDATE memory_entries SET revision=revision+1,version=?,nonce=?,ciphertext=?,
                provider_mask=?,expires_at=?,edited_by_user=1,updated_at=?,last_confirmed_at=?,
                source_run_id=NULL,source_start=NULL,source_end=NULL WHERE owner=? AND id=? AND revision=?""",
                (
                    envelope.version,
                    envelope.nonce,
                    envelope.ciphertext,
                    mask,
                    expiry,
                    self.clock(),
                    self.clock(),
                    owner,
                    identifier,
                    expected_revision,
                ),
            )
            self._sync_index(owner, identifier)
            self._bump(owner)
            return self._view(self._row(owner, identifier))

    def _watermark(self, owner):
        return self.db.execute(
            "SELECT coalesce(max(seq),0) FROM memory_turns WHERE owner=?", (owner,)
        ).fetchone()[0]

    def _erase(self, owner, row, settings):
        self.db.execute(
            """INSERT OR REPLACE INTO memory_tombstones
            (owner,canonical_key,facet_hash,hash_version,source_watermark,created_at) VALUES(?,?,?,?,?,?)""",
            (
                owner,
                row["canonical_key"],
                row["facet_hash"],
                settings["hash_version"],
                self._watermark(owner),
                self.clock(),
            ),
        )
        self.db.execute("DELETE FROM memory_entries WHERE owner=? AND id=?", (owner, row["id"]))
        self._sync_index(owner, row["id"])

    def delete(self, owner, identifier, expected_revision, *, permanent=False):
        with self.lock, self.db:
            self._settings(owner)
            row = self._row(owner, identifier)
            self._cas(row, expected_revision)
            if permanent:
                from .barriers import MemoryBarriers

                MemoryBarriers(self).create(owner, row, self._watermark(owner))
            from .retention import redact_deleted_fact

            redact_deleted_fact(self, row)
            self.db.execute("DELETE FROM memory_projections WHERE owner=?", (owner,))
            self.db.execute(
                "DELETE FROM memory_entries WHERE owner=? AND id=?", (owner, identifier)
            )
            self._sync_index(owner, identifier)
            self.db.execute(
                "UPDATE memory_settings SET capture_after=? WHERE owner=?",
                (self._watermark(owner), owner),
            )
            self._bump(owner)

    def clear(self, owner):
        with self.lock, self.db:
            self._settings(owner)
            from .retention import clear_payloads

            clear_payloads(self.db, owner, self._watermark(owner))
            from .summaries import SummaryStore

            SummaryStore(self).invalidate(owner, (), all_scopes=True)
            self.db.execute("DELETE FROM memory_projections WHERE owner=?", (owner,))
            self.db.execute("DELETE FROM memory_entries WHERE owner=?", (owner,))
            self.db.execute("DELETE FROM memory_terms WHERE owner=?", (owner,))
            self.db.execute("DELETE FROM memory_indexed WHERE owner=?", (owner,))
            self.db.execute("DELETE FROM memory_topic_members WHERE owner=?", (owner,))
            self.db.execute("DELETE FROM memory_dirty_topics WHERE owner=?", (owner,))
            self.db.execute("UPDATE memory_maintenance SET state='blocked',failure_code='memory_cleared',nonce=X'',ciphertext=X'' WHERE owner=?", (owner,))
            self.db.execute(
                "UPDATE memory_settings SET capture_after=? WHERE owner=?",
                (self._watermark(owner), owner),
            )
            self._bump(owner)

    def set_enabled(self, owner, enabled, expected_revision):
        if type(enabled) is not bool:
            raise MemoryFailure("memory_invalid", 422)
        with self.lock, self.db:
            settings = self._settings(owner)
            self._cas(settings, expected_revision)
            if not enabled:
                from .summaries import SummaryStore

                SummaryStore(self).invalidate(owner, (), all_scopes=True)
            self.db.execute(
                "UPDATE memory_settings SET enabled=?,capture_after=? WHERE owner=?",
                (int(enabled), self._watermark(owner), owner),
            )
            self._bump(owner)
            row = self._settings(owner)
            return MemorySettings(
                enabled=bool(row["enabled"]),
                revision=row["revision"],
                generation=row["generation"],
                capture_after=row["capture_after"],
            )
