import asyncio
from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr

from gaia_agent.interactive.credential_crypto import KeyCipher
from gaia_agent.interactive.credential_sessions import CredentialSessions
from gaia_agent.interactive.credential_vault import CredentialVault
from gaia_agent.interactive.memory.consolidation import MemoryConsolidator
from gaia_agent.interactive.memory.crypto import MemoryCrypto
from gaia_agent.interactive.memory.event_store import MemoryEventStore
from gaia_agent.interactive.memory.models import MemoryOperation
from gaia_agent.interactive.memory.policy import MemoryPolicy
from gaia_agent.interactive.memory.queue import MemoryQueue
from gaia_agent.interactive.memory.repository import MemoryRepository
from gaia_agent.interactive.memory.retrieval import MemoryRetriever
from gaia_agent.interactive.memory.service import MemoryService
from gaia_agent.interactive.models import InteractiveRequest, InteractiveResult
from gaia_agent.interactive.service import RunService
from gaia_agent.interactive.store import ConversationStore
from gaia_agent.models import Usage


def setup(tmp_path):
    store = ConversationStore(tmp_path / "state.sqlite")
    vault = CredentialVault(store.db, KeyCipher({"v1": b"a" * 32}, "v1"), store.lock)
    vault.put("alice", "openai", SecretStr("synthetic-memory-key"), consent=True)
    sessions = CredentialSessions(vault)
    repo = MemoryRepository(store.db, store.lock, MemoryCrypto({"v1": b"a" * 32}, "v1"))
    memory = MemoryService(repo, MemoryPolicy(), MemoryRetriever())
    queue = MemoryQueue(MemoryEventStore(store.db, store.lock, repo.crypto))
    return store, sessions, memory, queue


def admitted(store, sessions, worker, *, handle=None, mode="normal"):
    request = InteractiveRequest(
        question="我是方林", provider="openai", model="gpt-6.1-sol", memory_mode=mode
    )
    cid = store.create("alice", "openai")["id"]
    run = store.reserve("alice", cid, request, cid)
    worker.admit("alice", run, request, sessions.reference("alice", "openai", handle))
    store.update(run["id"], status="completed", result={"answer": "ok"})
    worker.release("alice", run["id"], True)
    return run["id"]


async def settled(worker, identifier, states):
    async with asyncio.timeout(2):
        while worker.status("alice", identifier)["state"] not in states:
            await asyncio.sleep(0.01)
    return worker.status("alice", identifier)


@pytest.mark.asyncio
async def test_provider_failure_has_safe_reason_and_partial_usage(tmp_path):
    from gaia_agent.interactive.memory.worker import MemoryWorker

    store, sessions, memory, queue = setup(tmp_path)

    class Planner:
        async def plan(self, *args, usage_observer):
            usage_observer(Usage(input_tokens=9))
            raise TimeoutError("synthetic-secret-never-return")

    @asynccontextmanager
    async def factory(*args):
        yield Planner()

    worker = MemoryWorker(queue, factory, MemoryConsolidator(memory.repository, queue), sessions)
    identifier = admitted(store, sessions, worker)
    worker.start()
    try:
        status = await settled(worker, identifier, {"failed", "uncertain"})
        assert status["failure_code"] == "memory_timeout"
        assert status["input_tokens"] == 9
        assert "synthetic-secret" not in str(status)
    finally:
        await worker.close()
        store.db.close()


@pytest.mark.asyncio
async def test_restart_commits_persisted_proposal_without_another_provider_call(tmp_path):
    from gaia_agent.interactive.memory.worker import MemoryWorker

    store, sessions, memory, queue = setup(tmp_path)

    @asynccontextmanager
    async def forbidden(*args):
        raise AssertionError("persisted proposal must not call provider again")
        yield

    worker = MemoryWorker(queue, forbidden, MemoryConsolidator(memory.repository, queue), sessions)
    identifier = admitted(store, sessions, worker)
    lease = queue.claim(queue.clock(), "crashed")
    queue.mark_dispatched(lease)
    op = MemoryOperation(
        action="add",
        category="profile",
        topic="name",
        entity="self",
        cardinality="single",
        content="我是方林",
        source_start=0,
        source_end=4,
        source_kind="user",
        intent="automatic",
    )
    queue.store_proposal(lease, [op])
    with store.db:
        store.db.execute("UPDATE memory_jobs SET lease_until=0 WHERE id=?", (identifier,))
    worker.start()
    try:
        result = await settled(worker, identifier, {"completed", "failed"})
        assert result["state"] == "completed" and result["dispatch_count"] == 1
        assert memory.repository.list("alice")[0].content == "我是方林"
    finally:
        await worker.close()
        store.db.close()


@pytest.mark.asyncio
async def test_expired_temporary_key_is_blocked_even_when_saved_key_exists(tmp_path):
    from gaia_agent.interactive.credential_vault import CredentialMaterial
    from gaia_agent.interactive.memory.worker import MemoryWorker

    store, sessions, memory, queue = setup(tmp_path)
    now = [100.0]
    sessions.clock = lambda: now[0]
    handle = sessions.issue("alice", CredentialMaterial("openai", SecretStr("synthetic-temp-key")))

    @asynccontextmanager
    async def forbidden(*args):
        raise AssertionError("no fallback to saved key")
        yield

    worker = MemoryWorker(queue, forbidden, MemoryConsolidator(memory.repository, queue), sessions)
    identifier = admitted(store, sessions, worker, handle=handle)
    now[0] = 1900
    worker.start()
    try:
        result = await settled(worker, identifier, {"blocked", "failed"})
        assert result["state"] == "blocked"
        assert result["failure_code"] == "credential_expired"
        assert result["dispatch_count"] == 0
    finally:
        await worker.close()
        store.db.close()


@pytest.mark.asyncio
async def test_shutdown_after_dispatch_is_uncertain_and_restart_never_repeats_call(tmp_path):
    from gaia_agent.interactive.memory.worker import MemoryWorker

    store, sessions, memory, queue = setup(tmp_path)
    entered = asyncio.Event()
    calls = []

    class Planner:
        async def plan(self, *args, **kwargs):
            calls.append("paid")
            entered.set()
            await asyncio.Event().wait()

    @asynccontextmanager
    async def factory(*args):
        yield Planner()

    worker = MemoryWorker(queue, factory, MemoryConsolidator(memory.repository, queue), sessions)
    identifier = admitted(store, sessions, worker)
    worker.start()
    await asyncio.wait_for(entered.wait(), 2)
    await worker.close()
    assert worker.status("alice", identifier)["state"] == "uncertain"
    restarted = MemoryWorker(queue, factory, MemoryConsolidator(memory.repository, queue), sessions)
    restarted.start()
    await asyncio.sleep(0)
    await restarted.close()
    assert calls == ["paid"]
    assert sessions.vault.background_epoch("alice", "openai") == 1
    store.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "pause", "temporary-mode"])
async def test_account_controls_prevent_stale_publication(tmp_path, change):
    from gaia_agent.interactive.memory.worker import MemoryWorker

    store, sessions, memory, queue = setup(tmp_path)
    entered, gate = asyncio.Event(), asyncio.Event()

    class Planner:
        async def plan(self, event, *args, usage_observer):
            entered.set()
            await gate.wait()
            usage_observer(Usage(input_tokens=3))
            return (
                MemoryOperation(
                    action="add",
                    category="profile",
                    topic="name",
                    entity="self",
                    cardinality="single",
                    content=event.source,
                    source_start=0,
                    source_end=4,
                    source_kind="user",
                    intent="automatic",
                ),
            )

    @asynccontextmanager
    async def factory(*args):
        yield Planner()

    worker = MemoryWorker(queue, factory, MemoryConsolidator(memory.repository, queue), sessions)
    identifier = admitted(
        store, sessions, worker, mode="temporary" if change == "temporary-mode" else "normal"
    )
    worker.start()
    try:
        if change == "temporary-mode":
            await asyncio.sleep(0)
            assert queue.db.execute("SELECT count(*) FROM memory_jobs").fetchone()[0] == 0
            assert not entered.is_set()
            return
        await asyncio.wait_for(entered.wait(), 2)
        if change == "revoke":
            sessions.vault.delete("alice", "openai")
        else:
            memory.repository.set_enabled("alice", False, 0)
        gate.set()
        result = await settled(worker, identifier, {"blocked", "failed"})
        assert result["state"] != "completed"
        assert memory.repository.list("alice") == []
    finally:
        await worker.close()
        store.db.close()


@pytest.mark.asyncio
async def test_foreground_completes_while_memory_planner_is_blocked(tmp_path):
    from gaia_agent.interactive.memory.worker import MemoryWorker

    store, sessions, memory, queue = setup(tmp_path)
    gate, entered = asyncio.Event(), asyncio.Event()
    calls = []

    class Planner:
        async def plan(self, event, *args, usage_observer):
            calls.append(event.source)
            entered.set()
            await gate.wait()
            usage_observer(Usage(input_tokens=11, output_tokens=3))
            return (
                MemoryOperation(
                    action="add",
                    category="profile",
                    topic="name",
                    entity="self",
                    cardinality="single",
                    content=event.source,
                    source_start=0,
                    source_end=len(event.source),
                    source_kind="user",
                    intent="automatic",
                ),
            )

    @asynccontextmanager
    async def factory(event, material):
        assert material.model == SecretStr("synthetic-memory-key")
        yield Planner()

    worker = MemoryWorker(queue, factory, MemoryConsolidator(memory.repository, queue), sessions)

    async def execute(ref, *args, **kwargs):
        return InteractiveResult(answer="普通回答", evidence=[], usage=Usage(input_tokens=7))

    service = RunService(
        store,
        tmp_path / "runs",
        execute,
        credential_sessions=sessions,
        memory_service=memory,
        memory_worker=worker,
    )
    worker.start()
    try:
        cid = store.create("alice", "openai")["id"]
        request = InteractiveRequest(question="我是方林", provider="openai", model="gpt-6.1-sol")
        run = service.start("alice", cid, request, "one")
        await service.tasks[run["id"]]
        await asyncio.wait_for(entered.wait(), 2)
        result = service.get("alice", run["id"])
        assert result["status"] == "completed"
        assert result["result"]["usage"]["input_tokens"] == 7
        assert memory.repository.list("alice") == []
        assert result["memory_maintenance"]["state"] == "provider_started"
        gate.set()
        async with asyncio.timeout(2):
            while service.get("alice", run["id"])["memory_maintenance"]["state"] != "completed":
                await asyncio.sleep(0.01)
        assert memory.repository.list("alice")[0].content == "我是方林"
        assert service.get("alice", run["id"])["memory_maintenance"]["input_tokens"] == 11
        assert store.run("alice", run["id"])["result"]["usage"]["input_tokens"] == 7
        assert calls == ["我是方林"]
    finally:
        await service.shutdown()
        await worker.close()
        sessions.close()
        store.db.close()


@pytest.mark.asyncio
async def test_failed_foreground_never_dispatches_memory_and_shutdown_has_no_tasks(tmp_path):
    from gaia_agent.interactive.memory.worker import MemoryWorker

    store, sessions, memory, queue = setup(tmp_path)

    @asynccontextmanager
    async def forbidden(*args):
        raise AssertionError("memory must not dispatch for failed run")
        yield

    worker = MemoryWorker(queue, forbidden, MemoryConsolidator(memory.repository, queue), sessions)

    async def execute(*args, **kwargs):
        raise TimeoutError

    service = RunService(
        store,
        tmp_path / "runs",
        execute,
        credential_sessions=sessions,
        memory_service=memory,
        memory_worker=worker,
    )
    worker.start()
    run = service.start(
        "alice",
        store.create("alice", "openai")["id"],
        InteractiveRequest(question="我是方林", provider="openai", model="gpt-6.1-sol"),
        "one",
    )
    await service.tasks[run["id"]]
    assert service.get("alice", run["id"])["memory_maintenance"]["state"] == "blocked"
    await worker.close()
    assert worker.tasks == []
    store.db.close()
