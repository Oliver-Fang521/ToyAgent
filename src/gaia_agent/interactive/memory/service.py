import asyncio
import time
from contextlib import contextmanager

from .guard import MemoryLease
from .models import MemoryFailure, MemoryReport, MemorySnapshot
from .projections import MemoryProjectionStore


class MemoryService:
    def __init__(self, repository, policy, retriever):
        self.repository, self.policy, self.retriever = repository, policy, retriever
        self.mutating = set()
        self._leases = {}

    def check_admission(self, owner):
        if owner in self.mutating:
            raise MemoryFailure("memory_busy", 409)

    def snapshot(self, owner, provider, mode):
        self.check_admission(owner)
        with self.repository.lock:
            settings = self.repository.settings(owner)
            records = (
                self.repository.eligible(owner, provider, time.time())
                if settings.enabled and mode == "normal" and not getattr(self, "agentic_read", False)
                else ()
            )
            revision = self.repository.db.execute(
                "SELECT content_revision FROM memory_settings WHERE owner=?", (owner,)
            ).fetchone()[0]
            projection = (
                MemoryProjectionStore(self.repository).get_or_build(
                    owner, provider, settings.generation, revision
                )
                if records
                else None
            )
            return MemorySnapshot(
                owner,
                provider,
                settings.generation,
                mode,
                records,
                revision,
                projection,
                settings.enabled,
            )

    @contextmanager
    def lease(self, snapshot, deadline, clock=time.monotonic):
        self.check_admission(snapshot.owner)
        lease = MemoryLease(snapshot, self.repository, deadline, clock)
        leases = self._leases.setdefault(snapshot.owner, set())
        leases.add(lease)
        try:
            lease.ensure_open()
            yield lease
        finally:
            lease.close()
            leases.discard(lease)
            if not leases:
                self._leases.pop(snapshot.owner, None)

    async def remember(self, snapshot, run_id, text, extractor, *, deadline, usage_observer):
        if snapshot.mode == "temporary":
            return MemoryReport(status="temporary")
        settings = self.repository.settings(snapshot.owner)
        if not settings.enabled:
            return MemoryReport(status="paused")
        if settings.generation != snapshot.generation:
            return MemoryReport(status="skipped")
        prepared = self.policy.prepare_text(text)
        budget = min(8, deadline - time.monotonic() - 0.5)
        if prepared is None or budget <= 0:
            return MemoryReport(status="skipped")
        if not self.repository.claim_extraction(snapshot.owner, run_id, snapshot.generation):
            return MemoryReport(status="unchanged")
        try:
            async with asyncio.timeout(budget):
                records = self.retriever.select(snapshot.records, text, time.time())
                candidates = await extractor.extract(
                    prepared, records, usage_observer=usage_observer
                )
                writes = tuple(
                    w
                    for candidate in candidates
                    if (w := self.policy.validate_current(candidate, text)) is not None
                )
                saved = self.repository.apply_candidates(
                    snapshot.owner, snapshot.provider, run_id, writes, snapshot.generation
                )
                return MemoryReport(
                    status="updated" if saved else "unchanged",
                    saved_ids=saved,
                    skipped_count=len(candidates) - len(saved),
                )
        except Exception:  # noqa: BLE001 - optional maintenance never leaks inputs or fails an answer
            return MemoryReport(status="unavailable")

    async def mutate(self, owner, action, cancel_owner):
        self.check_admission(owner)
        self.mutating.add(owner)
        try:
            for lease in tuple(self._leases.get(owner, ())):
                lease.close()
            try:
                return action()
            finally:
                # Also stop old leases after a failed CAS/storage transaction.
                await cancel_owner(owner)
        finally:
            self.mutating.discard(owner)
