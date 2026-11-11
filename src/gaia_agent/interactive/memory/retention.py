"""Scoped logical retirement, not physical SQLite/chat/backup erasure.

Callers own the transaction. Ordinary changes never discard an owner's pending jobs.
"""

from .crypto import MemoryEnvelope


def retire_finalized(db, *, owner=None, identifier=None, now=None):
    clauses, args = [], []
    for field, value in (("owner", owner), ("id", identifier)):
        if value is not None:
            clauses.append(f"{field}=?")
            args.append(value)
    final = "state IN ('completed','unchanged','needs_clarification','blocked')"
    if now is not None:
        final = f"({final} OR expires_at<=?)"
        args.append(now)
    if not clauses and now is None:
        raise ValueError("retirement_requires_scope")
    db.execute(
        "UPDATE memory_jobs SET ciphertext=X'',nonce=X'',proposal_version=NULL,"
        "proposal_nonce=NULL,proposal_ciphertext=NULL WHERE " + " AND ".join([*clauses, final]),
        args,
    )


def redact_deleted_fact(repo, row):
    """Blank only this deleted fact in mixed retryable events; preserve other facts.

    Equal-length blanks preserve other source offsets. Generation fences reject an
    already-held pre-redaction proposal. This is an explicit-erasure exception to
    immutable source payloads, never a way to rewrite historical evidence silently.
    """
    fact = repo._payload(row)["content"]
    jobs = repo.db.execute(
        "SELECT * FROM memory_jobs WHERE owner=? AND length(ciphertext)>0", (row["owner"],)
    ).fetchall()
    for job in jobs:
        value = repo.crypto.decrypt_object(
            row["owner"],
            "event",
            job["id"],
            MemoryEnvelope(job["version"], job["nonce"], job["ciphertext"]),
        )
        event = value["event"]
        source = event["source"]
        observations = event.get("observations", [])
        if fact not in source and not any(fact in item["content"] for item in observations):
            continue
        event["source"] = source.replace(fact, " " * len(fact))
        event["observations"] = [item for item in observations if fact not in item["content"]]
        envelope = repo.crypto.encrypt_object(row["owner"], "event", job["id"], value)
        repo.db.execute(
            "UPDATE memory_jobs SET version=?,nonce=?,ciphertext=? WHERE owner=? AND id=?",
            (envelope.version, envelope.nonce, envelope.ciphertext, row["owner"], job["id"]),
        )
        if job["proposal_ciphertext"] is not None:
            proposal = repo.crypto.decrypt_object(
                row["owner"],
                "proposal",
                job["id"],
                MemoryEnvelope(
                    job["proposal_version"], job["proposal_nonce"], job["proposal_ciphertext"]
                ),
            )
            proposal["operations"] = [
                op
                for op in proposal["operations"]
                if op.get("target_id") != row["id"] and fact not in (op.get("content") or "")
            ]
            encrypted = repo.crypto.encrypt_object(row["owner"], "proposal", job["id"], proposal)
            repo.db.execute(
                "UPDATE memory_jobs SET proposal_version=?,proposal_nonce=?,proposal_ciphertext=? WHERE owner=? AND id=?",
                (encrypted.version, encrypted.nonce, encrypted.ciphertext, row["owner"], job["id"]),
            )
    retire_finalized(repo.db, owner=row["owner"])


def clear_payloads(db, owner, watermark):
    """Only an explicit clear-all action retires all prior memory-owned work."""
    db.execute(
        """UPDATE memory_jobs SET ciphertext=X'',nonce=X'',proposal_version=NULL,
        proposal_nonce=NULL,proposal_ciphertext=NULL,lease_id=NULL,lease_until=NULL,
        state=CASE WHEN state IN ('completed','unchanged') THEN state ELSE 'blocked' END,
        failure_code=CASE WHEN state IN ('completed','unchanged') THEN failure_code ELSE 'memory_cleared' END
        WHERE owner=? AND source_seq<=?""",
        (owner, watermark),
    )
