"""Scoped forgetting descriptors; never retain the erased fact or source text."""

from uuid import uuid4

from .crypto import MemoryEnvelope
from .repository import provider_mask


class MemoryBarriers:
    def __init__(self, repository):
        self.repository = repository

    def create(self, owner, row, watermark):
        repo = self.repository
        payload = repo._payload(row)
        identifier = str(uuid4())
        descriptor = {
            "topic": payload.get("topic", payload["facet"]),
            "entity": payload["entity"],
            "category": row["category"],
            "canonical_key": row["canonical_key"],
            "cardinality": payload.get("cardinality", "single"),
            "subject": payload.get("subject"),
        }
        envelope = repo.crypto.encrypt_object(owner, "barrier", identifier, descriptor)
        repo.db.execute(
            """INSERT INTO memory_barriers
            (id,owner,revision,source_watermark,provider_mask,version,nonce,ciphertext,created_at)
            VALUES(?,?,1,?,?,?,?,?,?)""",
            (
                identifier,
                owner,
                watermark,
                row["provider_mask"],
                envelope.version,
                envelope.nonce,
                envelope.ciphertext,
                repo.clock(),
            ),
        )
        return identifier

    def visible(self, owner, provider):
        repo = self.repository
        rows = repo.db.execute(
            "SELECT * FROM memory_barriers WHERE owner=? AND (provider_mask & ?) != 0",
            (owner, provider_mask((provider,))),
        ).fetchall()
        return tuple(
            {
                "id": row["id"],
                "revision": row["revision"],
                "cardinality": "single",
                "subject": None,
                **repo.crypto.decrypt_object(
                    owner,
                    "barrier",
                    row["id"],
                    MemoryEnvelope(row["version"], row["nonce"], row["ciphertext"]),
                ),
            }
            for row in rows
        )
