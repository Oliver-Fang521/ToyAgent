import sqlite3
from threading import RLock

import pytest
from test_memory_repository import write

from gaia_agent.interactive.memory.crypto import MemoryCrypto
from gaia_agent.interactive.memory.models import MemoryFailure
from gaia_agent.interactive.memory.repository import MemoryRepository


@pytest.fixture
def repo(tmp_path):
    db = sqlite3.connect(tmp_path / "memory.sqlite")
    db.row_factory = sqlite3.Row
    value = MemoryRepository(db, RLock(), MemoryCrypto({"v1": b"a" * 32}, "v1"))
    yield value
    db.close()


def project(repo, provider="openai"):
    from gaia_agent.interactive.memory.projections import MemoryProjectionStore

    settings = repo.settings("alice")
    revision = repo.db.execute(
        "SELECT content_revision FROM memory_settings WHERE owner='alice'"
    ).fetchone()[0]
    return MemoryProjectionStore(repo).get_or_build(
        "alice", provider, settings.generation, revision
    )


def test_projection_scope_revision_and_encryption(repo):
    item = repo.save_manual("alice", write(), ("openai",))
    first = project(repo)
    assert first.record_ids == (item.id,)
    assert project(repo, "deepseek").record_ids == ()
    assert (
        b"learning_subjects"
        not in repo.db.execute("SELECT ciphertext FROM memory_projections LIMIT 1").fetchone()[0]
    )
    repo.update("alice", item.id, {"allowed_providers": ("openai", "deepseek")}, 1)
    assert project(repo, "deepseek").record_ids == (item.id,)
    assert project(repo).generation != first.generation


def test_stale_projection_request_cannot_resurrect_deleted_fact(repo):
    from gaia_agent.interactive.memory.projections import MemoryProjectionStore

    item = repo.save_manual("alice", write(), ("openai",))
    old = project(repo)
    repo.delete("alice", item.id, item.revision)
    with pytest.raises(MemoryFailure, match="memory_conflict"):
        MemoryProjectionStore(repo).get_or_build(
            "alice", "openai", old.generation, old.content_revision
        )
    assert project(repo).record_ids == ()


def test_index_loses_only_revoked_source(repo):
    one = repo.save_manual("alice", write("I study Python", entity="one"), ("openai",))
    two = repo.save_manual("alice", write("I study mathematics", entity="two"), ("openai",))
    assert set(project(repo).record_ids) == {one.id, two.id}
    repo.delete("alice", one.id, 1, permanent=True)
    assert project(repo).record_ids == (two.id,)
