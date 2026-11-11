"""Synthetic layered-memory verification. Offline by default; no operator-key fallback.

Live mode requires explicit source credential paths and a persisted per-round
authorization. It appends to the existing cumulative budget rather than resetting it.
Returned-token cost is an estimate; unknown/failed calls retain their reservation.
"""

import argparse
import asyncio
import json
import secrets
import sqlite3
import statistics
import tempfile
import time
import tracemalloc
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock
from uuid import uuid4

from memory_acceptance import MODELS, RATES, AcceptanceBudget, source_key

from gaia_agent.interactive.catalog import ModelSelection, resolve_profile
from gaia_agent.interactive.credential_crypto import KeyCipher
from gaia_agent.interactive.credential_runtime import memory_planner_client
from gaia_agent.interactive.credential_vault import (
    CredentialError,
    CredentialMaterial,
    CredentialVault,
)
from gaia_agent.interactive.memory.consolidation import MemoryConsolidator
from gaia_agent.interactive.memory.crypto import MemoryCrypto
from gaia_agent.interactive.memory.event_store import MemoryEventStore
from gaia_agent.interactive.memory.index import MemoryIndex, MemoryIndexBackfill
from gaia_agent.interactive.memory.maintenance import MemoryMaintenanceWorker
from gaia_agent.interactive.memory.maintenance_queue import MaintenanceQueue
from gaia_agent.interactive.memory.models import (
    MemoryCandidate,
    MemoryEvent,
    MemoryFailure,
    MemoryWrite,
)
from gaia_agent.interactive.memory.queue import MemoryQueue
from gaia_agent.interactive.memory.read_loop import route_with_memory
from gaia_agent.interactive.memory.repository import MemoryRepository
from gaia_agent.interactive.memory.summaries import SummaryStore
from gaia_agent.interactive.memory.summary_planner import SummaryPlanner
from gaia_agent.interactive.memory.tools import MemoryTools
from gaia_agent.providers import complete_with_usage_observer


def fact_write(text, entity):
    return MemoryWrite(MemoryCandidate(category="profile", facet="learning_subjects", entity=entity,
        content=text, source_start=0, source_end=len(text), explicit_relearn=False), "profile:learning_subjects:" + entity, text)


def temporary_repo(root):
    db = sqlite3.connect(root / "state.sqlite")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return MemoryRepository(db, RLock(), MemoryCrypto({"test": secrets.token_bytes(32)}, "test"))


class LayeredBudget(AcceptanceBudget):
    def __init__(self, path, *, max_calls, authorize=False):
        super().__init__(path, release_testing=True)
        if type(max_calls) is not int or not 1 <= max_calls <= 20:
            raise ValueError("invalid_budget")
        marker = self.path.with_suffix(".layered-authorization.json")
        if marker.is_symlink():
            raise ValueError("unsafe_ledger")
        if marker.exists():
            record = json.loads(marker.read_text())
            if record.get("max_calls") != max_calls or record.get("budget_usd") != 2:
                raise ValueError("authorization_mismatch")
            self.limits = record["limits"]
        else:
            if not authorize:
                raise ValueError("authorization_required")
            self.limits = {p: sum(e["provider"] == p for e in self.entries) + max_calls for p in MODELS}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with marker.open("x") as output:
                json.dump({"max_calls": max_calls, "budget_usd": 2, "limits": self.limits}, output)


class LayeredProvider:
    def __init__(self, provider, budget, name, phase):
        self.provider, self.budget, self.name, self.phase = provider, budget, name, phase

    async def complete(self, request, executor=None, *, usage_observer=None):
        if request.allowed_tools or request.max_tool_calls or request.max_output_tokens > 6400 or len(request.model_dump_json().encode()) > 24000:
            raise ValueError("request_budget_exceeded")
        index = self.budget.reserve(self.name, self.phase)
        entry = self.budget.entries[index]
        def observe(usage):
            entry["input_tokens"] = entry.get("input_tokens", 0) + usage.input_tokens
            entry["output_tokens"] = entry.get("output_tokens", 0) + usage.output_tokens
            if usage_observer:
                usage_observer(usage)
        try:
            result = await complete_with_usage_observer(self.provider, request, executor, usage_observer=observe)
            if entry.get("input_tokens", 0) + entry.get("output_tokens", 0) > 0:
                entry["state"] = "returned"
                entry["estimated_usd"] = (entry.get("input_tokens", 0) * RATES[self.name][0] + entry.get("output_tokens", 0) * RATES[self.name][1]) / 1_000_000
            return result
        finally:
            self.budget.save()


def offline(count):
    with tempfile.TemporaryDirectory(prefix="toyagent-layered-offline-") as folder:
        tracemalloc.start()
        repo = temporary_repo(Path(folder))
        started = time.perf_counter()
        for i in range(count):
            repo.save_manual("synthetic", fact_write(f"Synthetic background unique{i}", f"item-{i}"), ("openai",))
        save_seconds = time.perf_counter() - started
        state = repo.state_snapshot("synthetic", "openai")
        index = MemoryIndex(repo)
        durations, recalled, oldest, decryptions = [], 0, False, []
        decrypt = repo.crypto.decrypt
        def observed(*args):
            decryptions.append(1)
            return decrypt(*args)
        repo.crypto.decrypt = observed
        for i in range(min(count, 30)):
            before = time.perf_counter()
            result = index.search(state.scope, f"unique{i}", limit=1)
            durations.append((time.perf_counter() - before) * 1000)
            matched = bool(result.items and result.items[0].content == f"Synthetic background unique{i}")
            recalled += matched
            if i == 0:
                oldest = matched
        repo.crypto.decrypt = decrypt
        ids, cursor, pages = set(), None, 0
        while True:
            page = repo.page_records("synthetic", cursor, 50)
            ids.update(r.id for r in page.items)
            pages += 1
            cursor = page.next_cursor
            if not cursor:
                break
        with repo.db:
            repo.db.execute("DELETE FROM memory_indexed")
            repo.db.execute("DELETE FROM memory_terms")
        started = time.perf_counter()
        backfill, batches = MemoryIndexBackfill(repo), 0
        while backfill.run_once():
            batches += 1
        build_seconds = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        repo.db.close()
        return {"paid_calls": 0, "saved_count": len(ids), "oldest_recalled": oldest,
            "decrypted_for_queries": len(decryptions),
            "tested_recall": recalled, "tested_queries": len(durations), "pages": pages,
            "save_seconds": round(save_seconds, 3), "index_build_seconds": round(build_seconds, 3),
            "index_batches": batches, "query_p50_ms": round(statistics.median(durations), 3),
            "query_p95_ms": round(sorted(durations)[int((len(durations) - 1) * .95)], 3),
            "python_peak_mib": round(peak / 1024 ** 2, 3), "tracemalloc_enabled": True}


async def live_provider(options, budget, name):
    key = source_key(options, name)  # read-only credentials query; never chats or memories
    profile = resolve_profile(ModelSelection(provider=name, model=MODELS[name]))
    with tempfile.TemporaryDirectory(prefix="toyagent-layered-live-") as folder:
        repo = temporary_repo(Path(folder))
        owner = "synthetic-layered-" + str(uuid4())
        vault = CredentialVault(repo.db, KeyCipher({"test": secrets.token_bytes(32)}, "test"), repo.lock)
        vault.put(owner, name, key, consent=True)
        material = CredentialMaterial(name, key)
        if options.controls:
            return await live_controls(repo, vault, material, owner, name, profile, budget)
        queue = MemoryQueue(MemoryEventStore(repo.db, repo.lock, repo.crypto))
        consolidator = MemoryConsolidator(repo, queue)
        for i in range(210):
            repo.save_manual(owner, fact_write(f"Synthetic unrelated unique{i}", f"fixture-{i}"), (name,))
        sources = ("我是数学专业，讲解代码时先给具体例子。", "我在开发Orion测试项目，这个项目的数据库用PostgreSQL。")
        last_event = None
        for source in sources:
            run_id = str(uuid4())
            event = MemoryEvent(event_id=run_id, owner=owner, provider=name, source_run_id=run_id,
                source_seq=repo.register_turn(owner, run_id), memory_generation=repo.settings(owner).generation,
                credential_epoch=vault.background_epoch(owner, name), credential_source="saved",
                model=profile.model, reasoning_effort="low", capability_version=profile.capability_version, source=source)
            queue.enqueue(event)
            lease = queue.claim(queue.clock(), "acceptance")
            async with memory_planner_client(event, material) as planner:
                planner.provider = LayeredProvider(planner.provider, budget, name, "layered:capture")
                prepared = planner.prepare(event, repo.eligible(owner, name, repo.clock(), query=source), (), ())
                queue.mark_dispatched(lease)
                ops = await planner.plan_prepared(prepared, usage_observer=lambda _: None)
            queue.store_proposal(lease, ops)
            consolidator.apply(lease, ops)
            last_event = event
        # Exercise model compression of one topic; other facts stay searchable without summaries.
        math = next((r for r in repo.eligible(owner, name, repo.clock(), query="数学") if "数学" in r.content), None)
        if math is None:
            raise ValueError("capture_assertion_failed")
        topic = repo.db.execute("SELECT topic_id FROM memory_topic_members WHERE owner=? AND provider=? AND fact_id=?", (owner, name, math.id)).fetchone()[0]
        maintenance = MaintenanceQueue(repo)
        selected = {"model": profile.model, "reasoning_effort": "low", "capability_version": profile.capability_version}
        maintenance.mark_dirty(repo.state_snapshot(owner, name).scope, selected, last_event.credential_epoch, (topic,))
        @asynccontextmanager
        async def summary_factory(claim):
            async with memory_planner_client(last_event, material) as planner:
                yield SummaryPlanner(LayeredProvider(planner.provider, budget, name, "layered:summary"))
        worker = MemoryMaintenanceWorker(maintenance, summary_factory, credential_check=lambda _: True)
        now = repo.clock() + 11
        for _ in range(3):
            await worker.run_once(now=now)
        state = repo.state_snapshot(owner, name)
        overview = SummaryStore(repo).overview(state.scope)
        if overview is None:
            raise ValueError("summary_assertion_failed")
        results = []
        questions = (("根据你记得的我的学习背景，应该怎么向我解释Python装饰器？", "数学"),
                     ("我之前说Orion测试项目选用什么数据库？只根据我的记忆回答。", "PostgreSQL"))
        for question, expected in questions:
            async with memory_planner_client(last_event, material) as planner:
                loop = await route_with_memory(LayeredProvider(planner.provider, budget, name, "layered:recall"),
                    question=question, history="[]", reasoning_effort="low", tools=MemoryTools(repo, state.scope),
                    overview=overview, deadline=time.monotonic() + 180)
            results.append({"expected": expected, "passed": expected.casefold() in loop.decision.answer.casefold(),
                "answer": loop.decision.answer, "route": loop.decision.route, "termination": loop.termination,
                "read_count": len(loop.evidence.refs)})
        repo.db.close()
        return {"provider": name, "passed": all(r["passed"] for r in results), "cases": results}


async def live_controls(repo, vault, material, owner, name, profile, budget):
    """Current canonical correction/forgetting, not a test of automatic extraction."""
    item = repo.save_manual(owner, fact_write('Use PostgreSQL', 'Orion'), (name,))
    run_id = str(uuid4())
    event = MemoryEvent(event_id=run_id, owner=owner, provider=name, source_run_id=run_id,
        source_seq=repo.register_turn(owner, run_id), memory_generation=repo.settings(owner).generation,
        credential_epoch=vault.background_epoch(owner, name), credential_source='saved',
        model=profile.model, reasoning_effort='low', capability_version=profile.capability_version,
        source='synthetic control fixture')
    topic = repo.db.execute('SELECT topic_id FROM memory_topic_members WHERE owner=? AND provider=? AND fact_id=?', (owner, name, item.id)).fetchone()[0]
    q = MaintenanceQueue(repo)
    q.mark_dirty(repo.state_snapshot(owner, name).scope, {'model': profile.model, 'reasoning_effort': 'low', 'capability_version': profile.capability_version}, event.credential_epoch, (topic,), now=0)
    claim = q.claim(10, 'controls')
    q.reserve_dispatch(claim, now=10)
    async with memory_planner_client(event, material) as planner:
        draft = await SummaryPlanner(LayeredProvider(planner.provider, budget, name, 'layered:controls-summary')).summarize(claim, (repo.get(owner, item.id),), usage_observer=lambda _: None)
    summary = SummaryStore(repo).publish(claim.scope, draft, claim.input_revision)
    q.finish(claim)
    scoped = 'orion' in summary.content.casefold() and 'postgresql' in summary.content.casefold()
    changed = repo.update(owner, item.id, {'content': 'Use SQLite'}, item.revision)
    invalidated = repo.db.execute('SELECT state FROM memory_summaries WHERE id=?', (summary.id,)).fetchone()[0] != 'ready'
    cases = []
    for forgotten in (False, True):
        if forgotten:
            repo.delete(owner, item.id, changed.revision, permanent=True)
        state = repo.state_snapshot(owner, name)
        async with memory_planner_client(event, material) as planner:
            loop = await route_with_memory(LayeredProvider(planner.provider, budget, name, 'layered:controls-recall'),
                question='只根据你保存的我的个人记忆：Orion项目现在使用什么数据库？没有相关记忆就明确说不知道，不要推荐。',
                history='[]', reasoning_effort='low', tools=MemoryTools(repo, state.scope), overview=None, deadline=time.monotonic() + 180)
        answer = loop.decision.answer.casefold()
        passed = ('sqlite' not in answer and 'postgresql' not in answer) if forgotten else ('sqlite' in answer and 'postgresql' not in answer)
        cases.append({'case': 'permanent_forget' if forgotten else 'canonical_correction', 'passed': passed,
            'answer': loop.decision.answer, 'termination': loop.termination, 'read_count': len(loop.evidence.refs)})
    repo.db.close()
    return {'provider': name, 'passed': scoped and invalidated and all(c['passed'] for c in cases),
        'summary_preserved_entity': scoped, 'old_summary_invalidated': invalidated, 'cases': cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--controls", action="store_true", help="live canonical correction/forgetting fixtures")
    parser.add_argument("--provider", choices=tuple(MODELS), default="openai")
    parser.add_argument("--max-calls", type=int, default=20)
    parser.add_argument("--confirm-budget-usd", type=float)
    parser.add_argument("--authorize-new-budget", action="store_true")
    parser.add_argument("--budget-ledger", type=Path)
    parser.add_argument("--source-db", type=Path)
    parser.add_argument("--source-env", type=Path)
    parser.add_argument("--source-owner")
    parser.add_argument("--source-config", type=Path)
    parser.add_argument("--owner-config", type=Path)
    options = parser.parse_args()
    if not options.live:
        if not 1 <= options.count <= 10000:
            parser.error("offline count must be 1..10000")
        print(json.dumps(offline(options.count)))
        return 0
    if options.offline or options.confirm_budget_usd != 2 or options.budget_ledger is None or options.source_db is None or not ((options.source_env and options.source_owner) or (options.source_config and options.owner_config)):
        parser.error("live requires explicit budget and read-only credential source paths")
    # One CLI process owns the ledger, including a persisted new-scope authorization.
    import fcntl
    options.budget_ledger.parent.mkdir(parents=True, exist_ok=True)
    with options.budget_ledger.with_suffix(".layered.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        budget = LayeredBudget(options.budget_ledger, max_calls=options.max_calls, authorize=options.authorize_new_budget)
        result = asyncio.run(live_provider(options, budget, options.provider))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, MemoryFailure, CredentialError) as error:
        code = getattr(error, "code", "acceptance_failed")
        print(json.dumps({"passed": False, "error": code}))
        raise SystemExit(1) from None
