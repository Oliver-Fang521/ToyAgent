from test_memory_consolidation import prepare

pytest_plugins = ("test_memory_consolidation",)


def test_ordinary_delete_can_relearn_from_new_source_without_global_hold(system):
    repo, _, consolidator, _ = system
    consolidator.apply(*prepare(system))
    record = repo.list("alice")[0]
    repo.delete("alice", record.id, record.revision)
    assert repo.db.execute("SELECT COUNT(*) FROM memory_tombstones").fetchone()[0] == 0
    consolidator.apply(*prepare(system))
    assert repo.list("alice")[0].content == "我是方林"
    consolidator.apply(*prepare(system, "我喜欢数学", "interests", cardinality="set"))
    assert len(repo.list("alice")) == 2


def test_permanent_barrier_blocks_scoped_topic_but_not_other_learning(system):
    repo, _, consolidator, _ = system
    consolidator.apply(*prepare(system))
    record = repo.list("alice")[0]
    repo.delete("alice", record.id, record.revision, permanent=True)
    assert repo.db.execute("SELECT COUNT(*) FROM memory_barriers").fetchone()[0] == 1
    assert consolidator.apply(*prepare(system)).state == "unchanged"
    consolidator.apply(*prepare(system, "我喜欢数学", "interests", cardinality="set"))
    assert [r.content for r in repo.list("alice")] == ["我喜欢数学"]
    row = repo.db.execute("SELECT * FROM memory_barriers").fetchone()
    from gaia_agent.interactive.memory.crypto import MemoryEnvelope

    payload = repo.crypto.decrypt_object(
        "alice",
        "barrier",
        row["id"],
        MemoryEnvelope(row["version"], row["nonce"], row["ciphertext"]),
    )
    assert "我是方林" not in str(payload)
    repo.clear("alice")
    assert repo.db.execute("SELECT COUNT(*) FROM memory_barriers").fetchone()[0] == 1


def test_explicit_restore_allows_new_source(system):
    repo, _, consolidator, _ = system
    consolidator.apply(*prepare(system))
    record = repo.list("alice")[0]
    repo.delete("alice", record.id, record.revision, permanent=True)
    barrier = repo.db.execute("SELECT id FROM memory_barriers WHERE owner='alice'").fetchone()[0]
    result = consolidator.apply(
        *prepare(system, "允许重新记住这个姓名", action="restore", barrier_id=barrier)
    )
    assert result.state == "completed"
    assert repo.db.execute("SELECT COUNT(*) FROM memory_barriers").fetchone()[0] == 0
    consolidator.apply(*prepare(system))
    assert repo.list("alice")[0].content == "我是方林"
