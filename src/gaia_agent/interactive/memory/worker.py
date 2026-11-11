"""Two bounded background consumers; the foreground never awaits maintenance."""

import asyncio
import sqlite3
from uuid import uuid4

from gaia_agent.interactive.credential_vault import CredentialError
from gaia_agent.interactive.models import resolve_selection

from .barriers import MemoryBarriers
from .models import MemoryEvent, MemoryFailure
from .policy import prepare_event_source


class MemoryWorker:
    def __init__(self, queue, planner_factory, consolidator, sessions):
        self.queue, self.planner_factory, self.consolidator, self.sessions = (
            queue,
            planner_factory,
            consolidator,
            sessions,
        )
        self.tasks = []
        self._wake = asyncio.Event()
        self._closed = False
        self._temporary = {}

    def start(self):
        if not self.tasks:
            self._closed = False
            with self.queue.lock, self.queue.db:
                if self.queue.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'"
                ).fetchone():
                    self.queue.db.execute("""UPDATE memory_jobs SET ready=1 WHERE ready=0
                        AND EXISTS(SELECT 1 FROM runs r WHERE r.id=memory_jobs.source_run_id
                            AND r.owner=memory_jobs.owner AND r.status='completed')""")
                    self.queue.db.execute("""UPDATE memory_jobs SET state='blocked',failure_code='source_interrupted'
                        WHERE ready=0 AND state='pending' AND NOT EXISTS(SELECT 1 FROM runs r
                            WHERE r.id=memory_jobs.source_run_id AND r.owner=memory_jobs.owner
                            AND r.status IN ('queued','running'))""")
            self.sessions.on_invalidate.append(self._invalidate)
            self.tasks = [asyncio.create_task(self._loop(str(uuid4()))) for _ in range(2)]

    def wake(self):
        self._wake.set()

    def _invalidate(self, owner):
        self.queue.invalidate(owner, "credential_revoked")
        self._temporary = {
            key: value for key, value in self._temporary.items() if value[0] != owner
        }

    def admit(self, owner, run, request, ref):
        repo = self.consolidator.repository
        try:
            if request.memory_mode == "temporary" or not repo.settings(owner).enabled:
                return
            self._prune_temporary()
            selected = resolve_selection(request)
            source = prepare_event_source(request.question)
            event = MemoryEvent(
                event_id=run["id"],
                owner=owner,
                provider=request.provider,
                source_run_id=run["id"],
                source_seq=repo.register_turn(owner, run["id"]),
                memory_generation=repo.settings(owner).generation,
                credential_epoch=self.sessions.vault.background_epoch(owner, request.provider),
                credential_source="temporary" if ref.handle else "saved",
                model=selected.model,
                reasoning_effort=selected.reasoning_effort,
                capability_version=selected.capability_version,
                source=source,
            )
            if ref.handle:
                if len(self._temporary) >= 1000:
                    raise MemoryFailure("memory_busy", 503)
                self._temporary[event.event_id] = (owner, self.sessions.background_reference(ref))
            self.queue.enqueue(event, ready=False)
        except Exception as error:  # noqa: BLE001 - memory admission must never fail the answer
            self._temporary.pop(run["id"], None)
            code = error.code if isinstance(error, MemoryFailure) else "memory_unavailable"
            try:
                with self.queue.lock, self.queue.db:
                    self.queue.db.execute(
                        "INSERT OR REPLACE INTO memory_admissions VALUES(?,?,?,?)",
                        (owner, run["id"], "failed", code),
                    )
            except sqlite3.Error:
                pass  # Foreground remains usable even if maintenance status cannot persist.

    def release(self, owner, identifier, success):
        with self.queue.lock, self.queue.db:
            if success:
                self.queue.db.execute(
                    "UPDATE memory_jobs SET ready=1 WHERE owner=? AND id=? AND state='pending'",
                    (owner, identifier),
                )
            else:
                self.queue.db.execute(
                    """UPDATE memory_jobs SET state='blocked',failure_code='source_incomplete'
                    WHERE owner=? AND id=? AND state='pending'""",
                    (owner, identifier),
                )
        self.wake()

    def status(self, owner, identifier):
        try:
            return self._status(owner, identifier)
        except sqlite3.Error:
            return {"state": "failed", "failure_code": "memory_unavailable"}

    def _status(self, owner, identifier):
        with self.queue.lock:
            row = self.queue.db.execute(
                "SELECT id,state,dispatch_count,failure_code,input_tokens,output_tokens FROM memory_jobs WHERE owner=? AND source_run_id=?",
                (owner, identifier),
            ).fetchone()
            if row:
                return dict(row)
            row = self.queue.db.execute(
                "SELECT state,reason FROM memory_admissions WHERE owner=? AND run_id=?",
                (owner, identifier),
            ).fetchone()
            return {"state": row[0], "failure_code": row[1]} if row else None

    def _material(self, event):
        repo = self.consolidator.repository
        settings = repo.settings(event.owner)
        if not settings.enabled or settings.generation != event.memory_generation:
            raise MemoryFailure("memory_conflict", 409)
        if event.credential_source == "temporary":
            token = self._temporary.get(event.event_id)
            if token is None or token[0] != event.owner:
                raise CredentialError("credential_expired", 422)
            return self.sessions.resolve_background(event.owner, event.provider, token[1])
        if (
            self.sessions.vault.background_epoch(event.owner, event.provider)
            != event.credential_epoch
        ):
            raise CredentialError("credential_revoked", 422)
        ref = self.sessions.reference(event.owner, event.provider, None)
        return self.sessions.resolve(ref)

    async def _loop(self, worker_id):
        while not self._closed:
            lease = None
            heartbeat = None
            try:
                self._prune_temporary()
                lease = self.queue.claim(self.queue.clock(), worker_id)
                if lease is None:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), 1)
                    except TimeoutError:
                        pass
                    continue
                material = self._material(lease.event)
                heartbeat = asyncio.create_task(self._heartbeat(lease))
                if lease.proposal is not None:
                    operations = lease.proposal
                else:
                    repo = self.consolidator.repository
                    records = repo.eligible(
                        lease.owner, lease.event.provider, self.queue.clock(), query=lease.event.source[:512]
                    )
                    repo_records = tuple(records)
                    # The planner owns the final byte budget; do not silently trim source.
                    barriers = MemoryBarriers(repo).visible(lease.owner, lease.event.provider)
                    async with (
                        asyncio.timeout(30),
                        self.planner_factory(lease.event, material) as planner,
                    ):
                        prepared = (
                            planner.prepare(
                                lease.event, repo_records, barriers, lease.event.observations
                            )
                            if hasattr(planner, "prepare")
                            else None
                        )
                        self.queue.mark_dispatched(lease)
                        if prepared is not None:
                            operations = await planner.plan_prepared(
                                prepared,
                                usage_observer=lambda usage, current=lease: self._usage(
                                    current, usage
                                ),
                            )
                        else:
                            operations = await planner.plan(
                                lease.event,
                                repo_records,
                                barriers,
                                lease.event.observations,
                                usage_observer=lambda usage, current=lease: self._usage(
                                    current, usage
                                ),
                            )
                    self.queue.store_proposal(lease, operations)
                self._material(lease.event)
                self.consolidator.apply(lease, operations)
                from .maintenance_queue import MaintenanceQueue

                try:
                    summary_worker = getattr(self, "summary_worker", None)
                    token = self._temporary.get(lease.id)
                    if summary_worker is not None and token is not None:
                        summary_worker.capture_reference(lease.event, token[1])
                    MaintenanceQueue(self.consolidator.repository).activate_dirty(lease.event)
                except Exception:  # noqa: BLE001, S110 -- optional summary must not change a saved result
                    pass
            except asyncio.CancelledError:
                if lease:
                    self._fail(lease, "uncertain", "dispatch_uncertain")
                raise
            except Exception as error:  # noqa: BLE001 - no raw provider/source data in status or logs
                if lease:
                    code = (
                        error.code
                        if isinstance(error, (CredentialError, MemoryFailure))
                        else (
                            "memory_timeout"
                            if isinstance(error, TimeoutError)
                            else "memory_unavailable"
                        )
                    )
                    self._fail(
                        lease, "blocked" if isinstance(error, CredentialError) else "failed", code
                    )
                else:
                    await asyncio.sleep(1)
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                if lease:
                    row = self.queue.db.execute(
                        "SELECT state,retry_count,expires_at FROM memory_jobs WHERE owner=? AND id=?",
                        (lease.owner, lease.id),
                    ).fetchone()
                    keep = (
                        row
                        and row["state"] in {"failed", "uncertain"}
                        and row["retry_count"] == 0
                        and row["expires_at"] > self.queue.clock()
                    )
                    if not keep:
                        self._temporary.pop(lease.id, None)

    def _prune_temporary(self):
        for identifier, (owner, reference) in tuple(self._temporary.items()):
            row = self.queue.db.execute(
                "SELECT state,retry_count,expires_at,provider FROM memory_jobs WHERE owner=? AND id=?",
                (owner, identifier),
            ).fetchone()
            keep = (
                row
                and row["expires_at"] > self.queue.clock()
                and (
                    row["state"] in {"pending", "leased", "provider_started", "proposed"}
                    or row["state"] in {"failed", "uncertain"}
                    and row["retry_count"] == 0
                )
            )
            try:
                if keep:
                    self.sessions.resolve_background(owner, row["provider"], reference)
            except CredentialError:
                keep = False
            if not keep:
                self._temporary.pop(identifier, None)

    async def _heartbeat(self, lease):
        while True:
            await asyncio.sleep(10)
            try:
                self.queue.heartbeat(lease, self.queue.clock())
            except MemoryFailure:
                return

    def _usage(self, lease, usage):
        with self.queue.lock, self.queue.db:
            self.queue.db.execute(
                """UPDATE memory_jobs SET input_tokens=input_tokens+?,output_tokens=output_tokens+?
                WHERE owner=? AND id=? AND dispatch_lease_id=?""",
                (usage.input_tokens, usage.output_tokens, lease.owner, lease.id, lease.lease_id),
            )

    def _fail(self, lease, state, code="memory_unavailable"):
        try:
            row = self.queue._leased(lease)
            if state == "uncertain" and row["state"] == "leased":
                with self.queue.db:
                    self.queue.db.execute(
                        "UPDATE memory_jobs SET state='pending',lease_id=NULL,lease_until=NULL WHERE owner=? AND id=?",
                        (lease.owner, lease.id),
                    )
                return
            self.queue.finish(lease, state, reason=code)
        except MemoryFailure:
            pass

    async def close(self):
        self._closed = True
        if self._invalidate in self.sessions.on_invalidate:
            self.sessions.on_invalidate.remove(self._invalidate)
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self._temporary.clear()
