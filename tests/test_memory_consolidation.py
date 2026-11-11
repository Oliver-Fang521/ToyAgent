import sqlite3
from threading import RLock

import pytest
from pydantic import SecretStr
from test_memory_schema import event_values

from gaia_agent.interactive.credential_crypto import KeyCipher
from gaia_agent.interactive.credential_vault import CredentialVault
from gaia_agent.interactive.memory.crypto import MemoryCrypto
from gaia_agent.interactive.memory.event_store import MemoryEventStore
from gaia_agent.interactive.memory.models import MemoryEvent, MemoryFailure, MemoryOperation
from gaia_agent.interactive.memory.queue import MemoryQueue
from gaia_agent.interactive.memory.repository import MemoryRepository


@pytest.fixture
def system(tmp_path):
    from gaia_agent.interactive.memory.consolidation import MemoryConsolidator

    db = sqlite3.connect(tmp_path / "memory.sqlite")
    db.row_factory = sqlite3.Row
    lock = RLock()
    crypto = MemoryCrypto({"v1": b"a" * 32}, "v1")
    repo = MemoryRepository(db, lock, crypto)
    vault = CredentialVault(db, KeyCipher({"v1": b"a" * 32}, "v1"), lock)
    vault.put("alice", "openai", SecretStr("synthetic-model-key"), consent=True)
    queue = MemoryQueue(MemoryEventStore(db, lock, crypto), clock=lambda: 100)
    yield repo, queue, MemoryConsolidator(repo, queue), vault
    db.close()


def prepare(
    system,
    text="我是方林",
    topic="name",
    *,
    action="add",
    target=None,
    cardinality="single",
    barrier_id=None,
):
    repo, queue, _, vault = system
    number = repo.db.execute("SELECT COUNT(*) FROM memory_jobs").fetchone()[0] + 1
    run = f"run-{number}"
    seq = repo.register_turn("alice", run)
    event = MemoryEvent(
        **(
            event_values()
            | {
                "event_id": f"event-{number}",
                "source_run_id": run,
                "source_seq": seq,
                "source": text,
                "memory_generation": repo.settings("alice").generation,
                "credential_epoch": vault.background_epoch("alice", "openai"),
            }
        )
    )
    op = MemoryOperation(
        action=action,
        category="profile",
        topic=topic,
        entity="self",
        cardinality=cardinality,
        content=text,
        source_start=0,
        source_end=len(text),
        source_kind="user",
        intent="explicit" if action in {"delete", "forget", "restore"} else "automatic",
        target_id=target.id if target else None,
        expected_revision=target.revision if target else (1 if barrier_id else None),
        barrier_id=barrier_id,
    )
    queue.enqueue(event)
    lease = queue.claim(100, "worker")
    queue.mark_dispatched(lease)
    queue.store_proposal(lease, [op])
    return lease, (op,)


def test_publication_is_atomic_and_does_not_cancel_foreground_generation(system):
    repo, queue, consolidator, _ = system
    lease, operations = prepare(system)
    before = repo.settings("alice")
    result = consolidator.apply(lease, operations)
    assert result.state == "completed"
    assert repo.list("alice")[0].content == "我是方林"
    assert repo.settings("alice").generation == before.generation
    assert result.content_revision == 1
    assert queue.status("alice", lease.id).state == "completed"
    with pytest.raises(MemoryFailure):
        consolidator.apply(lease, operations)


def test_duplicate_single_and_set_members_do_not_erase_each_other(system):
    repo, _, consolidator, _ = system
    consolidator.apply(*prepare(system))
    assert consolidator.apply(*prepare(system)).state == "unchanged"
    consolidator.apply(*prepare(system, "我喜欢数学", "interests", cardinality="set"))
    consolidator.apply(*prepare(system, "我喜欢Python", "interests", cardinality="set"))
    assert {v.content for v in repo.list("alice")} == {"我是方林", "我喜欢数学", "我喜欢Python"}


def test_stale_revision_and_manual_edits_are_not_overwritten(system):
    repo, _, consolidator, _ = system
    consolidator.apply(*prepare(system))
    old = repo.list("alice")[0]
    lease, ops = prepare(system, "我现在叫小林", target=old, action="replace")
    repo.update("alice", old.id, {"content": "用户手动姓名"}, old.revision)
    with pytest.raises(MemoryFailure, match="memory_conflict"):
        consolidator.apply(lease, ops)
    assert repo.list("alice")[0].content == "用户手动姓名"


def test_storage_failure_rolls_back_canonical_and_job_state(system):
    repo, queue, consolidator, _ = system
    lease, ops = prepare(system)
    repo.db.execute("""CREATE TRIGGER refuse_publish BEFORE UPDATE OF state ON memory_jobs
        WHEN NEW.state='completed' BEGIN SELECT RAISE(ABORT,'synthetic'); END""")
    with pytest.raises(MemoryFailure, match="memory_unavailable"):
        consolidator.apply(lease, ops)
    assert repo.list("alice") == []
    assert queue.status("alice", lease.id).state == "proposed"
    assert (
        repo.db.execute(
            "SELECT content_revision FROM memory_settings WHERE owner='alice'"
        ).fetchone()[0]
        == 0
    )


def test_revoked_credential_cannot_commit(system):
    repo, _, consolidator, vault = system
    lease, ops = prepare(system)
    vault.delete("alice", "openai")
    with pytest.raises(MemoryFailure):
        consolidator.apply(lease, ops)
    assert repo.list("alice") == []


def test_lease_cannot_swap_event_or_persisted_proposal(system):
    from dataclasses import replace

    repo, _, consolidator, _ = system
    lease, ops = prepare(system)
    forged = replace(lease, event=lease.event.model_copy(update={"source": "我是假冒用户"}))
    with pytest.raises(MemoryFailure, match="memory_conflict"):
        consolidator.apply(forged, ops)
    with pytest.raises(MemoryFailure, match="memory_conflict"):
        consolidator.apply(lease, (ops[0].model_copy(update={"topic": "changed"}),))
    assert repo.list("alice") == []


def test_explicit_forget_fences_older_foreground_snapshots(system):
    repo, _, consolidator, _ = system
    consolidator.apply(*prepare(system))
    record = repo.list("alice")[0]
    before = repo.settings("alice").generation
    consolidator.apply(*prepare(system, "永久忘记这个姓名", action="forget", target=record))
    assert repo.list("alice") == []
    assert repo.settings("alice").generation > before


def test_addition_above_200_does_not_evict_existing_memories(system):
    from test_memory_repository import write

    repo, _, consolidator, _ = system
    for index in range(200):
        repo.save_manual(
            "alice", write(f"ordinary fact {index}", entity=f"item-{index}"), ("openai",)
        )
    result = consolidator.apply(*prepare(system))
    assert result.state == "completed" and result.skipped_count == 0
    assert repo.page_records("alice").total == 201


def test_target_written_by_newer_source_is_not_replaced(system):
    repo, _, consolidator, _ = system
    consolidator.apply(*prepare(system))
    record = repo.list("alice")[0]
    lease, ops = prepare(system, "我现在叫小林", action="replace", target=record)
    repo.register_turn("alice", "future-correction")
    with repo.db:
        repo.db.execute(
            "UPDATE memory_entries SET source_run_id=? WHERE id=?", ("future-correction", record.id)
        )
    assert consolidator.apply(lease, ops).state == "unchanged"
    assert repo.list("alice")[0].content == "我是方林"


def test_manual_conflict_is_visible_as_clarification_not_silent_noop(system):
    from test_memory_repository import write

    repo, _, consolidator, _ = system
    record = repo.save_manual("alice", write("手动背景", facet="name"), ("openai",))
    result = consolidator.apply(*prepare(system, "我现在叫小林", action="replace", target=record))
    assert result.state == "needs_clarification"
    assert repo.list("alice")[0].content == "手动背景"


def test_background_new_memory_is_shared_between_providers(system):
    repo,_,consolidator,_=system
    result=consolidator.apply(*prepare(system))
    assert result.state=='completed'
    assert [r.content for r in repo.eligible('alice','deepseek',repo.clock())]==['我是方林']
    assert repo.eligible('bob','deepseek',repo.clock())==()
