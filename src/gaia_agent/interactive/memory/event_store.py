"""Encrypted job storage, separate from conversation history ownership."""

import sqlite3

from pydantic import ValidationError

from .crypto import MemoryEnvelope
from .models import MemoryEvent, MemoryFailure
from .schema import migrate_memory_schema


class MemoryEventStore:
    def __init__(self, db, lock, crypto):
        self.db, self.lock, self.crypto = db, lock, crypto
        with lock:
            migrate_memory_schema(db)

    def put(self, event: MemoryEvent, *, now: float, ready=True) -> None:
        envelope = self.crypto.encrypt_object(
            event.owner,
            "event",
            event.event_id,
            {"schema_version": 2, "event": event.model_dump(mode="json")},
        )
        try:
            with self.lock, self.db:
                self.db.execute(
                    """INSERT INTO memory_jobs (
                    id,owner,provider,source_run_id,source_seq,memory_generation,
                    credential_epoch,credential_source,state,version,nonce,ciphertext,
                    created_at,updated_at,expires_at,ready)
                    VALUES(?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?)""",
                    (
                        event.event_id,
                        event.owner,
                        event.provider,
                        event.source_run_id,
                        event.source_seq,
                        event.memory_generation,
                        event.credential_epoch,
                        event.credential_source,
                        envelope.version,
                        envelope.nonce,
                        envelope.ciphertext,
                        now,
                        now,
                        now + 86400,
                        int(ready),
                    ),
                )
        except sqlite3.Error:
            raise MemoryFailure("memory_unavailable") from None

    def get(self, owner: str, identifier: str) -> MemoryEvent:
        try:
            with self.lock:
                row = self.db.execute(
                    "SELECT version,nonce,ciphertext FROM memory_jobs WHERE owner=? AND id=?",
                    (owner, identifier),
                ).fetchone()
                if row is None or not row[2]:
                    raise MemoryFailure("memory_not_found", 404)
                value = self.crypto.decrypt_object(
                    owner,
                    "event",
                    identifier,
                    MemoryEnvelope(row[0], row[1], row[2]),
                )
                if value.get("schema_version") != 2:
                    raise MemoryFailure("memory_integrity", 422)
                event = MemoryEvent.model_validate(value.get("event"))
                if event.owner != owner or event.event_id != identifier:
                    raise MemoryFailure("memory_integrity", 422)
                return event
        except sqlite3.Error:
            raise MemoryFailure("memory_unavailable") from None
        except ValidationError:
            raise MemoryFailure("memory_integrity", 422) from None
