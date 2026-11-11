import asyncio
import sqlite3
import time
from threading import RLock

import pytest

from gaia_agent.interactive.memory.crypto import MemoryCrypto
from gaia_agent.interactive.memory.guard import MemoryRevoked
from gaia_agent.interactive.memory.models import MemoryFailure
from gaia_agent.interactive.memory.policy import MemoryPolicy
from gaia_agent.interactive.memory.repository import MemoryRepository
from gaia_agent.interactive.memory.retrieval import MemoryRetriever
from gaia_agent.interactive.memory.service import MemoryService


@pytest.fixture
def service(tmp_path):
    db = sqlite3.connect(tmp_path / "memory.sqlite", check_same_thread=False)
    db.row_factory = sqlite3.Row
    repo = MemoryRepository(db, RLock(), MemoryCrypto({"v1": b"a" * 32}, "v1"))
    yield MemoryService(repo, MemoryPolicy(), MemoryRetriever())
    db.close()


@pytest.mark.asyncio
async def test_mutation_revokes_before_await_and_blocks_admission(service):
    snapshot = service.snapshot("alice", "openai", "normal")
    with service.lease(snapshot, time.monotonic() + 10) as lease:

        async def cancel(owner):
            assert owner == "alice"
            with pytest.raises(MemoryRevoked):
                lease.ensure_open()
            with pytest.raises(MemoryFailure, match="memory_busy"):
                service.snapshot(owner, "openai", "normal")
            await asyncio.sleep(0)

        await service.mutate("alice", lambda: service.repository.clear("alice"), cancel)
        with pytest.raises(MemoryRevoked):
            lease.ensure_open()
    assert not service.mutating


@pytest.mark.asyncio
async def test_failed_mutation_never_reopens_old_lease(service):
    snapshot = service.snapshot("alice", "openai", "normal")
    generation = snapshot.generation
    cancelled = []

    async def cancel(owner):
        cancelled.append(owner)

    def action():
        raise MemoryFailure("memory_conflict", 409)

    with service.lease(snapshot, time.monotonic() + 10) as lease:
        with pytest.raises(MemoryFailure):
            await service.mutate("alice", action, cancel)
        with pytest.raises(MemoryRevoked):
            lease.ensure_open()
    assert service.repository.settings("alice").generation == generation
    assert cancelled == ["alice"]
