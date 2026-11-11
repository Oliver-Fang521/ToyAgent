"""Encrypted, disposable topic/source index. Canonical rows remain authoritative."""

import json
from uuid import uuid4

from .crypto import MemoryEnvelope
from .models import MemoryFailure, MemoryProjection
from .retrieval import MemoryRetriever


class MemoryProjectionStore:
    def __init__(self, repository):
        self.repository = repository

    def get_or_build(self, owner, provider, generation, content_revision):
        repo = self.repository
        with repo.lock:
            settings = repo.settings(owner)
            current = repo.db.execute(
                "SELECT content_revision FROM memory_settings WHERE owner=?", (owner,)
            ).fetchone()[0]
            if settings.generation != generation or current != content_revision:
                raise MemoryFailure("memory_conflict", 409)
            records = repo.eligible(owner, provider, repo.clock()) if settings.enabled else ()
            entries = tuple((r.id, r.revision, r.topic or "") for r in records)
            while len(json.dumps(entries, ensure_ascii=False).encode()) > 24000:
                entries = entries[:-1]
            bases = tuple(
                r.id
                for r in MemoryRetriever().select(records, "我是谁 who am I", repo.clock())
                if r.category == "profile"
            )[:2]
            value = MemoryProjection(owner, provider, generation, content_revision, entries, bases)
            # Always compare canonical pointers too: time-based expiry does not bump revisions.
            row = repo.db.execute(
                "SELECT * FROM memory_projections WHERE owner=? AND provider=?", (owner, provider)
            ).fetchone()
            payload = {"entries": entries, "base_ids": bases}
            if (
                row
                and row["generation"] == generation
                and row["content_revision"] == content_revision
            ):
                old = repo.crypto.decrypt_object(
                    owner,
                    "projection",
                    row["id"],
                    MemoryEnvelope(row["version"], row["nonce"], row["ciphertext"]),
                )
                if old == json.loads(json.dumps(payload)):
                    return value
            identifier = str(uuid4())
            envelope = repo.crypto.encrypt_object(owner, "projection", identifier, payload)
            with repo.db:
                repo.db.execute(
                    "INSERT OR REPLACE INTO memory_projections VALUES(?,?,?,?,?,?,?,?)",
                    (
                        owner,
                        provider,
                        generation,
                        content_revision,
                        identifier,
                        envelope.version,
                        envelope.nonce,
                        envelope.ciphertext,
                    ),
                )
            return value
