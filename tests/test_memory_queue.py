import sqlite3
from dataclasses import replace
from threading import RLock

import pytest
from test_memory_schema import event_values

from gaia_agent.interactive.memory.crypto import MemoryCrypto
from gaia_agent.interactive.memory.event_store import MemoryEventStore
from gaia_agent.interactive.memory.models import MemoryEvent, MemoryFailure


@pytest.fixture
def queue(tmp_path):
    from gaia_agent.interactive.memory.queue import MemoryQueue

    db = sqlite3.connect(tmp_path / "queue.sqlite")
    db.row_factory = sqlite3.Row
    now = [100.0]
    value = MemoryQueue(
        MemoryEventStore(db, RLock(), MemoryCrypto({"v1": b"a" * 32}, "v1")), clock=lambda: now[0]
    )
    value.test_clock = now
    yield value
    db.close()


def event(number=1, owner="alice"):
    return MemoryEvent(
        **(
            event_values()
            | {
                "event_id": f"event-{owner}-{number}",
                "owner": owner,
                "source_seq": number,
                "source_run_id": f"run-{number}",
            }
        )
    )


def test_idempotent_enqueue_and_fifo_across_claimants(queue):
    assert queue.enqueue(event(2)).id == queue.enqueue(event(2)).id
    queue.enqueue(event(1))
    first = queue.claim(100, "worker-1")
    assert first.event.source_seq == 1
    assert queue.claim(100, "worker-2") is None
    queue.finish(first, "unchanged")
    assert queue.claim(100, "worker-2").event.source_seq == 2


def test_global_concurrency_and_owner_bound_lease(queue):
    for owner in ("alice", "bob", "carol"):
        queue.enqueue(event(owner=owner))
    lease = queue.claim(100, "one")
    assert queue.claim(100, "two")
    assert queue.claim(100, "three") is None
    with pytest.raises(MemoryFailure):
        queue.mark_dispatched(replace(lease, owner="intruder"))
    queue.heartbeat(lease, 110)
    queue.mark_dispatched(lease)
    with pytest.raises(MemoryFailure):
        queue.mark_dispatched(lease)


def test_expired_pre_dispatch_recovers_but_paid_call_does_not_repeat(queue):
    queue.enqueue(event())
    queue.claim(100, "old")
    queue.recover(161)
    recovered = queue.claim(161, "new")
    assert recovered
    queue.test_clock[0] = 161
    queue.mark_dispatched(recovered)
    queue.recover(222)
    assert queue.status("alice", recovered.id).state == "uncertain"
    assert queue.claim(222, "third") is None


def test_durable_proposal_recovery_without_redispatch(queue):
    queue.enqueue(event())
    lease = queue.claim(100, "old")
    queue.mark_dispatched(lease)
    queue.store_proposal(lease, [])
    queue.recover(161)
    new_lease = queue.claim(161, "new")
    assert new_lease.proposal == ()
    with pytest.raises(MemoryFailure):
        queue.mark_dispatched(new_lease)
    queue.finish(new_lease, "unchanged")
    assert queue.status("alice", lease.id).state == "unchanged"


def test_bounded_pending_and_expiry(queue):
    for number in range(1, 101):
        queue.enqueue(event(number))
    with pytest.raises(MemoryFailure, match="memory_busy"):
        queue.enqueue(event(101))
    queue.recover(86501)
    assert queue.claim(86501, "one") is None


def test_invalidate_cancels_old_work_and_never_exposes_source(queue):
    queue.enqueue(event())
    lease = queue.claim(100, "one")
    queue.invalidate("alice", "credential_revoked")
    with pytest.raises(MemoryFailure):
        queue.store_proposal(lease, [])
    view = queue.status("alice", lease.id)
    assert view.state == "blocked"
    assert "我是方林" not in repr(view)
    with pytest.raises(MemoryFailure, match="memory_not_found"):
        queue.status("bob", lease.id)


def test_saved_credential_mutation_invalidates_jobs_in_same_database(queue):
    from pydantic import SecretStr

    from gaia_agent.interactive.credential_crypto import KeyCipher
    from gaia_agent.interactive.credential_vault import CredentialVault

    vault = CredentialVault(queue.db, KeyCipher({"v1": b"a" * 32}, "v1"), queue.lock)
    queue.enqueue(event())
    lease = queue.claim(100, "one")
    vault.put("alice", "openai", SecretStr("synthetic-replacement"), consent=True)
    assert queue.status("alice", lease.id).state == "blocked"
    with pytest.raises(MemoryFailure):
        queue.mark_dispatched(lease)


def test_claimants_on_distinct_connections_cannot_double_claim(queue):
    from gaia_agent.interactive.memory.queue import MemoryQueue

    queue.enqueue(event())
    path = queue.db.execute("PRAGMA database_list").fetchone()[2]
    other_db = sqlite3.connect(path)
    other_db.row_factory = sqlite3.Row
    other = MemoryQueue(MemoryEventStore(other_db, RLock(), queue.store.crypto), clock=lambda: 100)
    assert queue.claim(100, "one")
    assert other.claim(100, "two") is None
    other_db.close()


def test_lost_lease_cannot_publish_even_with_correct_owner(queue):
    queue.enqueue(event())
    lease = queue.claim(100, "one")
    queue.test_clock[0] = 161
    with pytest.raises(MemoryFailure, match="memory_conflict"):
        queue.store_proposal(lease, [])


def test_foreground_unready_job_cannot_be_claimed(queue):
    queue.enqueue(event(), ready=False)
    assert queue.claim(100, "one") is None


def test_explicit_retry_allows_only_one_additional_dispatch(queue):
    queue.enqueue(event())
    lease = queue.claim(100, "one")
    queue.mark_dispatched(lease)
    queue.finish(lease, "uncertain")
    assert queue.claim(100, "automatic") is None
    queue.retry("alice", lease.id)
    second = queue.claim(100, "explicit")
    queue.mark_dispatched(second)
    queue.finish(second, "failed")
    with pytest.raises(MemoryFailure, match="memory_conflict"):
        queue.retry("alice", lease.id)
    assert queue.status("alice", lease.id).dispatch_count == 2
