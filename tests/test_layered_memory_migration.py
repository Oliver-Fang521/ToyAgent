# ruff: noqa: F811 -- shared fixture
from test_memory_repository import repo, write  # noqa: F401


def test_interrupted_legacy_index_backfill_resumes_without_model_or_fact_changes(repo):
    for i in range(210):
        repo.save_manual("alice", write(f"legacy unique{i}", entity=str(i)), ("openai",))
    before = [tuple(r) for r in repo.db.execute("SELECT * FROM memory_entries ORDER BY id")]
    with repo.db:
        repo.db.execute("DELETE FROM memory_indexed")
        repo.db.execute("DELETE FROM memory_terms")
        repo.db.execute("DELETE FROM memory_dirty_topics")
    from gaia_agent.interactive.memory.index import MemoryIndex, MemoryIndexBackfill
    from gaia_agent.interactive.memory.repository import MemoryRepository
    worker = MemoryIndexBackfill(repo)
    assert worker.run_once() is True
    reopened = MemoryRepository(repo.db, repo.lock, repo.crypto)
    worker = MemoryIndexBackfill(reopened)
    while worker.run_once():
        pass
    assert [tuple(r) for r in repo.db.execute("SELECT * FROM memory_entries ORDER BY id")] == before
    assert repo.db.execute("SELECT COUNT(*) FROM memory_dirty_topics").fetchone()[0] == 0
    state = repo.state_snapshot("alice", "openai")
    assert MemoryIndex(repo).search(state.scope, "unique0").items[0].content == "legacy unique0"


def test_rebuild_after_clear_does_not_reuse_erased_job_payload(repo):
    from gaia_agent.interactive.memory.maintenance_queue import MaintenanceQueue
    q = MaintenanceQueue(repo)
    state = repo.state_snapshot("alice", "openai")
    q.mark_dirty(state.scope, {"model": "fake"}, 0, ("topic",), now=0)
    repo.clear("alice")
    state = repo.state_snapshot("alice", "openai")
    q.mark_dirty(state.scope, {"model": "fake"}, 0, ("topic",), now=0)
    assert q.claim(10, "worker") is not None
