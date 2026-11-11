"""Publish validated proposals with canonical records and job status atomically."""

import sqlite3

from .barriers import MemoryBarriers
from .crypto import MemoryEnvelope
from .models import MemoryBatchResult, MemoryCandidate, MemoryFailure, MemoryWrite
from .policy import MemoryPolicy
from .repository import provider_mask, shared_providers


class MemoryConsolidator:
    def __init__(self, repository, queue, *, temporary_valid=None):
        self.repository, self.queue = repository, queue
        self.temporary_valid = temporary_valid

    def apply(self, lease, operations):
        repo, queue = self.repository, self.queue
        event = lease.event
        if len(operations) > 5:
            raise MemoryFailure("memory_invalid", 422)
        with repo.lock:
            try:
                repo.db.execute("BEGIN IMMEDIATE")
                job = queue._leased(lease)
                if job["state"] != "proposed":
                    raise MemoryFailure("memory_conflict", 409)
                persisted_event = queue.store.get(lease.owner, lease.id)
                proposal = repo.crypto.decrypt_object(
                    lease.owner,
                    "proposal",
                    lease.id,
                    MemoryEnvelope(
                        job["proposal_version"], job["proposal_nonce"], job["proposal_ciphertext"]
                    ),
                )
                if event != persisted_event or proposal.get("operations") != [
                    op.model_dump(mode="json") for op in operations
                ]:
                    raise MemoryFailure("memory_conflict", 409)
                settings = repo._settings(event.owner)
                if (
                    not settings["enabled"]
                    or settings["generation"] != event.memory_generation
                    or event.source_seq <= settings["capture_after"]
                ):
                    raise MemoryFailure("memory_conflict", 409)
                epoch = repo.db.execute(
                    "SELECT epoch FROM memory_credential_epochs WHERE owner=? AND provider=?",
                    (event.owner, event.provider),
                ).fetchone()
                if event.credential_source == "saved":
                    if (
                        not epoch
                        or epoch[0] != event.credential_epoch
                        or not repo.db.execute(
                            "SELECT 1 FROM credentials WHERE owner=? AND provider=?",
                            (event.owner, event.provider),
                        ).fetchone()
                    ):
                        raise MemoryFailure("memory_conflict", 409)
                elif self.temporary_valid is None or not self.temporary_valid(event):
                    raise MemoryFailure("memory_conflict", 409)
                barriers = MemoryBarriers(repo).visible(event.owner, event.provider)
                rows = repo.db.execute(
                    "SELECT * FROM memory_entries WHERE owner=?", (event.owner,)
                ).fetchall()
                eligible = {
                    r["id"]: r
                    for r in rows
                    if r["provider_mask"] & provider_mask((event.provider,))
                }
                ids = set(eligible) | {b["id"] for b in barriers}
                saved, skipped, changed = [], 0, False
                capacity_rejected = False
                control_changed = False
                needs_clarification = any(op.action == "clarify" for op in operations)
                for op in operations:
                    validated = MemoryPolicy().validate_operation(event, op, ids)
                    if validated is None or op.action in {"noop", "clarify"}:
                        skipped += 1
                        continue
                    target = eligible.get(op.target_id)
                    if op.action == "restore":
                        barrier = next((b for b in barriers if b["id"] == op.barrier_id), None)
                        if barrier and barrier["revision"] == op.expected_revision:
                            repo.db.execute(
                                "DELETE FROM memory_barriers WHERE owner=? AND id=?",
                                (event.owner, op.barrier_id),
                            )
                            changed = True
                            control_changed = True
                            barriers = tuple(b for b in barriers if b["id"] != op.barrier_id)
                        else:
                            skipped += 1
                        continue
                    if target:
                        target_payload = repo._payload(target)
                        if op.action == "append" and (
                            target_payload.get("cardinality", "single") != "set"
                            or target["category"] != op.category
                            or target_payload.get("topic", target_payload["facet"]) != op.topic
                            or target_payload["entity"] != op.entity
                        ):
                            skipped += 1
                            needs_clarification = True
                            continue
                        if target["revision"] != op.expected_revision or (
                            target["edited_by_user"] and op.action not in {"delete", "forget"}
                        ):
                            needs_clarification = True
                            skipped += 1
                            continue
                        prior_turn = repo.db.execute(
                            "SELECT seq FROM memory_turns WHERE owner=? AND run_id=?",
                            (event.owner, target["source_run_id"]),
                        ).fetchone()
                        if prior_turn and prior_turn[0] >= event.source_seq:
                            skipped += 1
                            continue
                    if op.action in {"delete", "forget"}:
                        if not target:
                            skipped += 1
                            continue
                        if op.action == "forget":
                            MemoryBarriers(repo).create(event.owner, target, event.source_seq)
                            barriers = MemoryBarriers(repo).visible(event.owner, event.provider)
                        from .retention import redact_deleted_fact

                        redact_deleted_fact(repo, target)
                        repo.db.execute(
                            "DELETE FROM memory_projections WHERE owner=?", (event.owner,)
                        )
                        repo.db.execute(
                            "DELETE FROM memory_entries WHERE owner=? AND id=?",
                            (event.owner, target["id"]),
                        )
                        repo._sync_index(event.owner, target["id"])
                        changed = True
                        control_changed = True
                        continue
                    if op.action not in {"add", "append", "replace"}:
                        skipped += 1
                        continue
                    normalized = validated.normalized_key
                    if op.cardinality == "set":
                        normalized += ":" + repo.crypto.canonical_hash(
                            event.owner, op.content, settings["hash_version"]
                        )
                    candidate = MemoryCandidate(
                        category=op.category,
                        facet=op.topic,
                        entity=op.entity,
                        content=op.content,
                        source_start=op.source_start,
                        source_end=op.source_end,
                        explicit_relearn=False,
                    )
                    write = MemoryWrite(candidate, normalized, validated.source_fragment)
                    key, facet = repo._hashes(event.owner, write, settings["hash_version"])
                    if (
                        any(
                            b["canonical_key"] == key
                            or (
                                b.get("cardinality", "single") != "set"
                                and b["category"] == op.category
                                and b["topic"] == op.topic
                                and b["entity"] == op.entity
                            )
                            or (
                                b.get("cardinality") == "set"
                                and b.get("subject")
                                and b["category"] == op.category
                                and b["topic"] == op.topic
                                and b["entity"] == op.entity
                                and (
                                    b["subject"].casefold() == (op.subject or "").casefold()
                                    or b["subject"].casefold() in op.content.casefold()
                                )
                            )
                            or b["id"] == op.barrier_id
                            for b in barriers
                        )
                        or repo.db.execute(
                            "SELECT 1 FROM memory_tombstones WHERE owner=? AND facet_hash=?",
                            (event.owner, facet),
                        ).fetchone()
                    ):
                        skipped += 1
                        continue
                    existing = (
                        target
                        if op.action == "replace"
                        else repo.db.execute(
                            "SELECT * FROM memory_entries WHERE owner=? AND canonical_key=?",
                            (event.owner, key),
                        ).fetchone()
                    )
                    if existing and existing["id"] not in eligible:
                        skipped += 1
                        needs_clarification = True
                        continue
                    if existing and (
                        repo._payload(existing)["content"] == op.content
                        or existing["edited_by_user"]
                        or op.action == "add"
                    ):
                        skipped += 1
                        continue
                    try:
                        identifier = repo._save(
                            event.owner,
                            write,
                            repo._providers(existing) if existing else shared_providers(event.provider),
                            False,
                            event.source_run_id,
                            existing,
                        )
                    except MemoryFailure as error:
                        if error.code != "memory_limit":
                            raise
                        capacity_rejected = True
                        skipped += 1
                        continue
                    row = repo._row(event.owner, identifier)
                    payload = repo._payload(row) | {
                        "schema_version": 2,
                        "topic": op.topic,
                        "slot_id": identifier,
                        "cardinality": op.cardinality,
                        "origin_kind": op.intent,
                        "subject": op.subject,
                    }
                    envelope = repo.crypto.encrypt(event.owner, identifier, payload)
                    repo.db.execute(
                        "UPDATE memory_entries SET version=?,nonce=?,ciphertext=? WHERE owner=? AND id=?",
                        (
                            envelope.version,
                            envelope.nonce,
                            envelope.ciphertext,
                            event.owner,
                            identifier,
                        ),
                    )
                    repo._sync_index(event.owner, identifier)
                    saved.append(identifier)
                    changed = True
                if changed:
                    repo.db.execute(
                        "UPDATE memory_settings SET content_revision=content_revision+1 WHERE owner=?",
                        (event.owner,),
                    )
                if control_changed:
                    repo._bump(event.owner)
                revision = repo.db.execute(
                    "SELECT content_revision FROM memory_settings WHERE owner=?", (event.owner,)
                ).fetchone()[0]
                state = (
                    "failed"
                    if capacity_rejected
                    else "needs_clarification"
                    if needs_clarification
                    else ("completed" if changed else "unchanged")
                )
                repo.db.execute(
                    """UPDATE memory_jobs SET state=?,failure_code=?,lease_id=NULL,lease_until=NULL,updated_at=?
                    WHERE owner=? AND id=?""",
                    (
                        state,
                        "memory_limit" if capacity_rejected else None,
                        queue.clock(),
                        event.owner,
                        lease.id,
                    ),
                )
                from .retention import retire_finalized

                retire_finalized(repo.db, owner=event.owner, identifier=lease.id)
                repo.db.commit()
                return MemoryBatchResult(
                    state=state,
                    saved_ids=tuple(saved),
                    skipped_count=skipped,
                    content_revision=revision,
                )
            except sqlite3.Error:
                repo.db.rollback()
                raise MemoryFailure("memory_unavailable") from None
            except Exception:
                repo.db.rollback()
                raise
