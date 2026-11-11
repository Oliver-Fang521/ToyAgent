"""Two background consumers; successful steps advance, failed calls never hot-retry."""

import asyncio
import json
from uuid import uuid4

from .models import MemoryFailure
from .summaries import SummaryStore
from .summary_planner import SummaryPlanner


class MemoryMaintenanceWorker:
    def __init__(self, queue, planner_factory, *, credential_check):
        self.queue, self.factory, self.credential_check = queue, planner_factory, credential_check
        self.tasks = []

    def start(self):
        if not self.tasks:
            self.queue.recover()
            self.tasks = [asyncio.create_task(self._loop()) for _ in range(2)]

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []

    async def _loop(self):
        while True:
            await self.run_once()
            await asyncio.sleep(1)

    async def run_once(self, *, now=None):
        q, repo = self.queue, self.queue.repo
        now = repo.clock() if now is None else now
        claim = q.claim(now, "summary-worker")
        if claim is None:
            return
        try:
            if not self.credential_check(claim):
                q.finish(claim, state="blocked", code="credential_revoked")
                return
            inputs, next_data = self._inputs(claim)
            if not inputs:
                q.finish(claim)
                return
            async with asyncio.timeout(30):
                async with self.factory(claim) as planner:
                    q.reserve_dispatch(claim, now=now)
                    draft = await planner.summarize(claim, inputs, usage_observer=lambda usage: q.record_usage(claim, usage))
            if not self.credential_check(claim):
                q.finish(claim, state="blocked", code="credential_revoked")
                return
            view = SummaryStore(repo).publish(claim.scope, draft, claim.input_revision)
            with repo.lock, repo.db:
                phase = claim.cursor.get("phase", "shard")
                if phase != "overview":
                    level = claim.cursor.get("overview_output_level", -1) if phase == "overview_part" else (0 if phase == "shard" else claim.cursor["level"] + 1)
                    repo.db.execute("INSERT OR REPLACE INTO memory_summary_nodes VALUES(?,?,?,?)", (claim.id, level, claim.cursor["offset"], view.id))
                q.finish(claim, state="pending" if next_data else "completed", cursor=next_data, now=now)
        except asyncio.CancelledError:
            q.finish(claim, state="uncertain", code="dispatch_uncertain")
            raise
        except Exception as error:  # noqa: BLE001 -- no raw model or evidence exceptions
            try:
                q.finish(claim, state="failed", code="memory_timeout" if isinstance(error, TimeoutError) else getattr(error, "code", "memory_unavailable"))
            except Exception:  # noqa: BLE001, S110 -- replaced version owns its own status
                pass

    def _inputs(self, claim):
        repo, data = self.queue.repo, dict(claim.cursor)
        offset, phase = data["offset"], data["phase"]
        with repo.lock:
            if phase == "shard":
                rows = repo.db.execute("""SELECT e.* FROM memory_topic_members m JOIN memory_entries e ON e.owner=m.owner AND e.id=m.fact_id
                    WHERE m.owner=? AND m.provider=? AND m.topic_id=? AND (e.expires_at IS NULL OR e.expires_at>?) ORDER BY e.id LIMIT 21 OFFSET ?""",
                    (claim.scope.owner, claim.scope.provider, claim.topic_id, repo.clock(), offset)).fetchall()
                inputs = tuple(repo._record(r) for r in rows[:20])
                inputs = self._bounded(inputs)
                data["output_topic"] = f"{claim.topic_id}:shard:{offset}"
                # claim must carry the actual output location into the planner.
                claim.cursor["output_topic"] = data["output_topic"]
                next_data = dict(data, offset=offset + len(inputs)) if len(rows) > len(inputs) else dict(data, phase="merge", offset=0, level=0)
            elif phase == "merge":
                rows = repo.db.execute("SELECT summary_id FROM memory_summary_nodes WHERE job_id=? AND level=? ORDER BY position LIMIT 9 OFFSET ?", (claim.id, data["level"], offset)).fetchall()
                inputs = tuple(SummaryStore(repo).read(claim.scope, r[0]) for r in rows[:8])
                inputs = self._bounded(inputs)
                total = repo.db.execute("SELECT COUNT(*) FROM memory_summary_nodes WHERE job_id=? AND level=?", (claim.id, data["level"])).fetchone()[0]
                claim.cursor["output_topic"] = f"{claim.topic_id}:level:{data['level']}:{offset}"
                next_data = dict(data, offset=offset + len(inputs)) if len(rows) > len(inputs) else (dict(data, level=data["level"] + 1, offset=0) if total > len(inputs) else dict(data, phase="overview", offset=0))
            elif phase in {"overview", "overview_scan", "overview_reduce"}:
                # Snapshot the complete eligible topic set; later phases never include
                # their own intermediate summaries in that set.
                if phase == "overview":
                    data["overview_run_id"] = uuid4().hex
                    with repo.db:
                        repo.db.execute("DELETE FROM memory_overview_inputs WHERE job_id=?", (claim.id,))
                        repo.db.execute("""INSERT INTO memory_overview_inputs(job_id,position,summary_id,revision)
                            SELECT ?,ROW_NUMBER() OVER (ORDER BY id)-1,id,revision FROM memory_summaries
                            WHERE owner=? AND provider=? AND kind='topic' AND state='ready'""",
                            (claim.id, claim.scope.owner, claim.scope.provider))
                changed = repo.db.execute("""SELECT 1 FROM memory_summaries s
                    WHERE s.owner=? AND s.provider=? AND s.kind='topic' AND s.state='ready'
                    AND NOT EXISTS (SELECT 1 FROM memory_overview_inputs i WHERE i.job_id=? AND i.summary_id=s.id AND i.revision=s.revision)
                    UNION ALL SELECT 1 FROM memory_overview_inputs i WHERE i.job_id=? AND NOT EXISTS
                    (SELECT 1 FROM memory_summaries s WHERE s.owner=? AND s.provider=? AND s.id=i.summary_id AND s.kind='topic' AND s.state='ready' AND s.revision=i.revision) LIMIT 1""",
                    (claim.scope.owner,claim.scope.provider,claim.id,claim.id,claim.scope.owner,claim.scope.provider)).fetchone()
                if changed:
                    raise MemoryFailure("memory_conflict",409)
                if phase in {"overview", "overview_scan"}:
                    rows = repo.db.execute("SELECT summary_id FROM memory_overview_inputs WHERE job_id=? ORDER BY position LIMIT 9 OFFSET ?", (claim.id, offset)).fetchall()
                    total = repo.db.execute("SELECT COUNT(*) FROM memory_overview_inputs WHERE job_id=?", (claim.id,)).fetchone()[0]
                    level = -1
                else:
                    rows = repo.db.execute("SELECT summary_id FROM memory_summary_nodes WHERE job_id=? AND level=? ORDER BY position LIMIT 9 OFFSET ?", (claim.id,data["level"],offset)).fetchall()
                    total = repo.db.execute("SELECT COUNT(*) FROM memory_summary_nodes WHERE job_id=? AND level=?", (claim.id,data["level"])).fetchone()[0]
                    level = data["level"] - 1
                inputs = self._bounded(tuple(SummaryStore(repo).read(claim.scope,r[0]) for r in rows[:8]))
                if not inputs:
                    return (), None
                if phase != "overview_scan" and offset == 0 and len(inputs) == total:
                    claim.cursor.update(phase="overview",output_topic="account-overview")
                    next_data = None
                else:
                    claim.cursor.update(phase="overview_part",overview_output_level=level,output_topic=f"{data['overview_run_id']}:overview:{level}:{offset}")
                    if offset + len(inputs) < total:
                        next_data = dict(data,phase="overview_scan" if phase in {"overview","overview_scan"} else "overview_reduce",offset=offset+len(inputs))
                    else:
                        next_data = dict(data,phase="overview_reduce",level=level,offset=0)
                return inputs, next_data
            else:
                raise MemoryFailure("memory_invalid",422)
            return inputs, next_data

    @staticmethod
    def _bounded(inputs):
        while len(inputs) > 1 and len(json.dumps([SummaryPlanner.evidence(r) for r in inputs], ensure_ascii=False).encode()) > 16000:
            inputs = inputs[:-1]
        return inputs
