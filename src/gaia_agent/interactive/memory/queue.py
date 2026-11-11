"""Durable, owner-bound dispatch journal. Never automatically repeats paid work."""

import sqlite3
import time
from dataclasses import dataclass
from uuid import uuid4

from gaia_agent.interactive.credential_vault import SAFE_CODES

from .crypto import MemoryEnvelope
from .models import MemoryEvent, MemoryFailure, MemoryJobState, MemoryOperation

ACTIVE = ("pending", "leased", "provider_started", "proposed")


@dataclass(frozen=True)
class MemoryJobView:
    id: str
    state: MemoryJobState
    dispatch_count: int
    failure_code: str | None


@dataclass(frozen=True, repr=False)
class MemoryJobLease:
    id: str
    owner: str
    lease_id: str
    worker_id: str
    event: MemoryEvent
    proposal: tuple[MemoryOperation, ...] | None = None


class MemoryQueue:
    def __init__(self, store, *, clock=time.time):
        self.store, self.db, self.lock, self.clock = store, store.db, store.lock, clock

    @staticmethod
    def _view(row):
        return MemoryJobView(row["id"], row["state"], row["dispatch_count"], row["failure_code"])

    def status(self, owner, identifier):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM memory_jobs WHERE owner=? AND id=?", (owner, identifier)
            ).fetchone()
            if row is None:
                raise MemoryFailure("memory_not_found", 404)
            return self._view(row)

    def enqueue(self, event, *, ready=True):
        # BEGIN IMMEDIATE also serializes claimants using another SQLite connection.
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                prior = self.db.execute(
                    "SELECT * FROM memory_jobs WHERE owner=? AND source_run_id=?",
                    (event.owner, event.source_run_id),
                ).fetchone()
                if prior:
                    self.db.commit()
                    return self._view(prior)
                total, account = self.db.execute(
                    """SELECT COUNT(*),
                    COALESCE(SUM(owner=?),0) FROM memory_jobs
                    WHERE state IN ('pending','leased','provider_started','proposed')""",
                    (event.owner,),
                ).fetchone()
                if total >= 1000 or account >= 100:
                    raise MemoryFailure("memory_busy", 503)
                # store.put's context commits this transaction after the insert.
                self.store.put(event, now=self.clock(), ready=ready)
                return self.status(event.owner, event.event_id)
            except (sqlite3.Error, MemoryFailure) as error:
                self.db.rollback()
                if isinstance(error, MemoryFailure):
                    raise
                raise MemoryFailure("memory_unavailable") from None

    def recover(self, now):
        from .retention import retire_finalized

        with self.lock, self.db:
            self.db.execute(
                """UPDATE memory_jobs SET state='blocked',failure_code='expired',
                lease_id=NULL,lease_until=NULL,updated_at=? WHERE expires_at<=?
                AND state IN ('pending','leased','proposed')""",
                (now, now),
            )
            self.db.execute(
                """UPDATE memory_jobs SET state='uncertain',
                failure_code='dispatch_uncertain',lease_id=NULL,lease_until=NULL,updated_at=?
                WHERE state='provider_started' AND lease_until<=?""",
                (now, now),
            )
            self.db.execute(
                """UPDATE memory_jobs SET state='pending',lease_id=NULL,
                lease_until=NULL,updated_at=? WHERE state='leased' AND lease_until<=?""",
                (now, now),
            )
            self.db.execute(
                """UPDATE memory_jobs SET lease_id=NULL,lease_until=NULL,updated_at=?
                WHERE state='proposed' AND lease_until<=?""",
                (now, now),
            )
            retire_finalized(self.db, now=now)

    def claim(self, now, worker_id):
        self.recover(now)
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                count = self.db.execute("""SELECT COUNT(*) FROM memory_jobs WHERE
                    state IN ('leased','provider_started','proposed') AND lease_id IS NOT NULL""").fetchone()[
                    0
                ]
                if count >= 2:
                    self.db.commit()
                    return None
                row = self.db.execute("""SELECT j.* FROM memory_jobs j
                    WHERE j.ready=1 AND (j.state='pending' OR (j.state='proposed' AND j.lease_id IS NULL))
                    AND NOT EXISTS(SELECT 1 FROM memory_jobs active WHERE active.owner=j.owner
                        AND active.state IN ('leased','provider_started','proposed')
                        AND active.lease_id IS NOT NULL)
                    ORDER BY j.source_seq,j.created_at,j.id LIMIT 1""").fetchone()
                if row is None:
                    self.db.commit()
                    return None
                event = self.store.get(row["owner"], row["id"])
                proposal = None
                if row["state"] == "proposed":
                    raw = self.store.crypto.decrypt_object(
                        row["owner"],
                        "proposal",
                        row["id"],
                        MemoryEnvelope(
                            row["proposal_version"],
                            row["proposal_nonce"],
                            row["proposal_ciphertext"],
                        ),
                    )
                    proposal = tuple(MemoryOperation.model_validate(v) for v in raw["operations"])
                token = str(uuid4())
                self.db.execute(
                    """UPDATE memory_jobs SET state=?,lease_id=?,worker_id=?,
                    lease_until=?,updated_at=? WHERE id=?""",
                    (
                        "proposed" if proposal is not None else "leased",
                        token,
                        worker_id,
                        now + 60,
                        now,
                        row["id"],
                    ),
                )
                self.db.commit()
                return MemoryJobLease(row["id"], row["owner"], token, worker_id, event, proposal)
            except Exception:
                self.db.rollback()
                raise

    def _leased(self, lease, now=None):
        row = self.db.execute(
            """SELECT * FROM memory_jobs WHERE id=? AND owner=?
            AND lease_id=? AND worker_id=? AND state IN ('leased','provider_started','proposed')""",
            (lease.id, lease.owner, lease.lease_id, lease.worker_id),
        ).fetchone()
        if row is None or row["lease_until"] <= (self.clock() if now is None else now):
            raise MemoryFailure("memory_conflict", 409)
        return row

    def heartbeat(self, lease, now):
        with self.lock, self.db:
            self._leased(lease, now)
            self.db.execute(
                "UPDATE memory_jobs SET lease_until=?,updated_at=? WHERE id=?",
                (now + 60, now, lease.id),
            )

    def mark_dispatched(self, lease):
        with self.lock, self.db:
            row = self._leased(lease)
            if row["state"] != "leased" or row["dispatch_count"] >= 1 + row["retry_count"]:
                raise MemoryFailure("memory_conflict", 409)
            self.db.execute(
                """UPDATE memory_jobs SET state='provider_started',
                dispatch_count=dispatch_count+1,dispatch_lease_id=?,dispatched_at=?,updated_at=? WHERE id=?""",
                (lease.lease_id, self.clock(), self.clock(), lease.id),
            )

    def store_proposal(self, lease, proposal):
        operations = tuple(MemoryOperation.model_validate(op) for op in proposal)
        if len(operations) > 5:
            raise MemoryFailure("memory_invalid", 422)
        envelope = self.store.crypto.encrypt_object(
            lease.owner,
            "proposal",
            lease.id,
            {"operations": [op.model_dump(mode="json") for op in operations]},
        )
        with self.lock, self.db:
            if self._leased(lease)["state"] != "provider_started":
                raise MemoryFailure("memory_conflict", 409)
            self.db.execute(
                """UPDATE memory_jobs SET state='proposed',proposal_version=?,
                proposal_nonce=?,proposal_ciphertext=?,updated_at=? WHERE id=?""",
                (envelope.version, envelope.nonce, envelope.ciphertext, self.clock(), lease.id),
            )

    def finish(self, lease, result, *, reason=None):
        if result not in {
            "completed",
            "unchanged",
            "needs_clarification",
            "blocked",
            "failed",
            "uncertain",
        }:
            raise MemoryFailure("memory_invalid", 422)
        with self.lock, self.db:
            self._leased(lease)
            if reason is not None and reason not in (
                MemoryFailure.CODES | SAFE_CODES | {"memory_timeout", "dispatch_uncertain"}
            ):
                reason = "memory_unavailable"
            self.db.execute(
                """UPDATE memory_jobs SET state=?,failure_code=?,lease_id=NULL,lease_until=NULL,
                updated_at=? WHERE id=?""",
                (result, reason, self.clock(), lease.id),
            )

    def invalidate(self, owner, reason):
        # Never persist caller-supplied exception text.
        code = (
            reason
            if reason in {"credential_revoked", "memory_paused", "generation_changed"}
            else "invalidated"
        )
        with self.lock, self.db:
            self.db.execute(
                """UPDATE memory_jobs SET state='blocked',failure_code=?,
                lease_id=NULL,lease_until=NULL,updated_at=? WHERE owner=?
                AND state IN ('pending','leased','provider_started','proposed')""",
                (code, self.clock(), owner),
            )

    def retry(self, owner, identifier):
        with self.lock, self.db:
            result = self.db.execute(
                """UPDATE memory_jobs SET retry_count=retry_count+1,
                state=CASE WHEN proposal_ciphertext IS NULL THEN 'pending' ELSE 'proposed' END,
                lease_id=NULL,lease_until=NULL,failure_code=NULL,updated_at=?
                WHERE owner=? AND id=? AND state IN ('failed','uncertain') AND ready=1
                AND retry_count=0 AND expires_at>?""",
                (self.clock(), owner, identifier, self.clock()),
            )
            if result.rowcount != 1:
                raise MemoryFailure("memory_conflict", 409)
            return self.status(owner, identifier)
