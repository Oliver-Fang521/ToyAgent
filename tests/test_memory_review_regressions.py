import json
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from conftest import FakeProvider
from pydantic import SecretStr
from test_memory_consolidation import prepare
from test_memory_retrieval import record
from test_memory_schema import event_values
from test_memory_worker import admitted, settled, setup

from gaia_agent.interactive.credential_vault import CredentialMaterial, CredentialVault
from gaia_agent.interactive.memory.consolidation import MemoryConsolidator
from gaia_agent.interactive.memory.models import MemoryEvent, MemoryFailure, MemoryOperation
from gaia_agent.interactive.memory.semantic import SemanticMemoryPlanner
from gaia_agent.interactive.memory.worker import MemoryWorker

pytest_plugins = ("test_memory_consolidation",)


def publish(
    system,
    text,
    topic,
    *,
    provider="openai",
    action="add",
    target=None,
    cardinality="single",
    operations=None,
    subject=None,
):
    repo, queue, consolidator, vault = system
    identifier = str(uuid4())
    event = MemoryEvent(
        **(
            event_values()
            | {
                "event_id": identifier,
                "source_run_id": identifier,
                "source_seq": repo.register_turn("alice", identifier),
                "provider": provider,
                "source": text,
                "memory_generation": repo.settings("alice").generation,
                "credential_epoch": vault.background_epoch("alice", provider),
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
        intent="automatic",
        target_id=target.id if target else None,
        expected_revision=target.revision if target else None,
        subject=subject,
    )
    queue.enqueue(event)
    lease = queue.claim(100, "review-test")
    queue.mark_dispatched(lease)
    queue.store_proposal(lease, operations or [op])
    return consolidator.apply(lease, tuple(operations or [op])), identifier


def test_append_cannot_retarget_a_provider_hidden_canonical_record(system):
    repo, _, _, vault = system
    publish(system, "Original private name", "name")
    hidden = repo.list("alice")[0]
    publish(system, "Visible nickname", "nickname")
    visible = next(r for r in repo.list("alice") if r.id != hidden.id)
    with repo.db:
        repo.db.execute(
            "UPDATE memory_entries SET provider_mask=2,origin_provider='deepseek' WHERE id=?",
            (visible.id,),
        )
    vault.put("alice", "deepseek", SecretStr("synthetic-deepseek"), consent=True)
    publish(
        system, "My name is changed", "name", provider="deepseek", action="append", target=visible
    )
    assert repo.get("alice", hidden.id).content == "Original private name"


def test_existing_saved_credentials_gain_a_zero_epoch_without_resaving(system):
    repo, _, _, vault = system
    with repo.db:
        repo.db.execute("DELETE FROM memory_credential_epochs")
    CredentialVault(repo.db, vault.cipher, repo.lock)
    result, _ = publish(system, "我是方林", "name")
    assert result.state == "completed"
    assert (
        repo.db.execute(
            "SELECT epoch FROM memory_credential_epochs WHERE owner='alice' AND provider='openai'"
        ).fetchone()[0]
        == 0
    )


@pytest.mark.asyncio
async def test_planner_bounds_populated_account_and_many_barriers_without_losing_relevant_target():
    provider = FakeProvider()
    provider.return_parsed({"operations": []})
    values = tuple(
        record(str(i), f"target-{i} " + "普通背景" * 60, topic=f"target-{i}") for i in range(200)
    )
    barriers = tuple(
        {
            "id": f"barrier-{i}",
            "revision": 1,
            "category": "profile",
            "topic": f"other-{i}",
            "entity": "self",
            "canonical_key": "a" * 64,
        }
        for i in range(200)
    )
    event = MemoryEvent(**(event_values() | {"source": "Update target-199"}))
    await SemanticMemoryPlanner(provider).plan(
        event, values, barriers, (), usage_observer=lambda _: None
    )
    request = provider.requests[0]
    assert len(request.model_dump_json().encode()) <= 16000
    assert any(r["id"] == "199" for r in json.loads(request.input_items[0]["content"])["records"])


@pytest.mark.parametrize("mode", ["ordinary", "permanent", "clear"])
def test_erasure_removes_live_source_and_proposal_copies(system, mode):
    repo, queue, consolidator, _ = system
    lease, ops = prepare(system, "我喜欢数学", "interests", cardinality="set")
    consolidator.apply(lease, ops)
    item = repo.list("alice")[0]
    if mode == "clear":
        repo.clear("alice")
    else:
        repo.delete("alice", item.id, item.revision, permanent=mode == "permanent")
    with pytest.raises(MemoryFailure, match="memory_not_found"):
        queue.store.get("alice", lease.id)
    row = repo.db.execute(
        "SELECT ciphertext,proposal_ciphertext FROM memory_jobs WHERE id=?", (lease.id,)
    ).fetchone()
    assert not row[0] and row[1] is None


def test_forgetting_one_set_member_does_not_ban_an_unrelated_member(system):
    repo, _, _, _ = system
    publish(system, "我喜欢数学", "interests", cardinality="set")
    item = repo.list("alice")[0]
    repo.delete("alice", item.id, item.revision, permanent=True)
    publish(system, "我喜欢Python", "interests", cardinality="set")
    assert [r.content for r in repo.list("alice")] == ["我喜欢Python"]


def test_set_subject_blocks_paraphrase_without_retaining_erased_sentence(system):
    from gaia_agent.interactive.memory.barriers import MemoryBarriers

    repo, _, _, _ = system
    publish(system, "我喜欢数学", "interests", cardinality="set", subject="数学")
    item = repo.list("alice")[0]
    repo.delete("alice", item.id, item.revision, permanent=True)
    descriptor = MemoryBarriers(repo).visible("alice", "openai")[0]
    assert descriptor["subject"] == "数学" and "我喜欢数学" not in str(descriptor)
    publish(system, "我爱数学", "interests", cardinality="set", subject="数学")
    assert repo.list("alice") == []
    publish(system, "我喜欢Python", "interests", cardinality="set", subject="Python")
    assert [r.content for r in repo.list("alice")] == ["我喜欢Python"]


def test_manual_content_edit_does_not_retain_a_stale_set_subject(system):
    repo, _, _, _ = system
    publish(system, "我喜欢数学", "interests", cardinality="set", subject="数学")
    item = repo.list("alice")[0]
    repo.update("alice", item.id, {"content": "我喜欢Python"}, item.revision)
    assert repo.get("alice", item.id).subject is None


def test_restore_and_new_fact_can_publish_in_the_same_proposal(system):
    from gaia_agent.interactive.memory.barriers import MemoryBarriers

    repo, _, _, _ = system
    publish(system, "我是方林", "name")
    item = repo.list("alice")[0]
    repo.delete("alice", item.id, item.revision, permanent=True)
    barrier = MemoryBarriers(repo).visible("alice", "openai")[0]
    first, second = "允许重新记住姓名", "我是方林"
    source = first + "；" + second
    common = {
        "category": "profile",
        "topic": "name",
        "entity": "self",
        "cardinality": "single",
        "source_kind": "user",
        "intent": "explicit",
    }
    operations = [
        MemoryOperation(
            **common,
            action="restore",
            source_start=0,
            source_end=len(first),
            barrier_id=barrier["id"],
            expected_revision=barrier["revision"],
        ),
        MemoryOperation(
            **common,
            action="add",
            content=second,
            source_start=len(first) + 1,
            source_end=len(source),
        ),
    ]
    publish(system, source, "name", operations=operations)
    assert [r.content for r in repo.list("alice")] == [second]


def test_deleted_fact_is_redacted_from_a_mixed_retryable_event_only(system):
    repo, queue, _, _ = system
    publish(system, "我喜欢数学", "interests", cardinality="set")
    item = repo.list("alice")[0]
    identifier = str(uuid4())
    source = "我喜欢数学；我在学习Python"
    event = MemoryEvent(
        **(
            event_values()
            | {
                "event_id": identifier,
                "source_run_id": identifier,
                "source_seq": repo.register_turn("alice", identifier),
                "source": source,
            }
        )
    )
    queue.enqueue(event, ready=False)
    repo.delete("alice", item.id, item.revision)
    retained = queue.store.get("alice", identifier)
    assert "我喜欢数学" not in retained.source and "我在学习Python" in retained.source
    assert len(retained.source) == len(source)


@pytest.mark.parametrize("secret", ["My SSN is 123-45-6789", "信用卡号是 4111 1111 1111 1111"])
def test_sensitive_identifiers_are_redacted_before_event_storage(secret, tmp_path):
    from gaia_agent.interactive.memory.policy import prepare_event_source

    result = prepare_event_source(f"我是方林；{secret}；我喜欢Python")
    assert secret not in result and "[REDACTED]" in result
    assert "我是方林" in result and "我喜欢Python" in result
    from gaia_agent.interactive.models import InteractiveRequest

    store, sessions, memory, queue = setup(tmp_path)
    request = InteractiveRequest(
        question=f"我是方林；{secret}；我喜欢Python", provider="openai", model="gpt-6.1-sol"
    )
    cid = store.create("alice", "openai")["id"]
    run = store.reserve("alice", cid, request, "redaction-fixture")
    worker = MemoryWorker(queue, None, MemoryConsolidator(memory.repository, queue), sessions)
    worker.admit("alice", run, request, sessions.reference("alice", "openai", None))
    decrypted = queue.store.get("alice", run["id"]).source
    assert secret not in decrypted and "[REDACTED]" in decrypted
    assert "我喜欢Python" in decrypted
    sessions.close()
    store.db.close()


@pytest.mark.asyncio
async def test_failed_temporary_job_retains_its_original_valid_reference_for_one_retry(tmp_path):
    store, sessions, memory, queue = setup(tmp_path)
    calls = []

    class Planner:
        async def plan(self, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError()
            return ()

    @asynccontextmanager
    async def factory(*args):
        yield Planner()

    worker = MemoryWorker(queue, factory, MemoryConsolidator(memory.repository, queue), sessions)
    worker.consolidator.temporary_valid = lambda event: bool(worker._material(event))
    handle = sessions.issue("alice", CredentialMaterial("openai", SecretStr("synthetic-temporary")))
    identifier = admitted(store, sessions, worker, handle=handle)
    worker.start()
    try:
        await settled(worker, identifier, {"failed"})
        event = queue.store.get("alice", identifier)
        assert worker._material(event).model.get_secret_value() == "synthetic-temporary"
        queue.retry("alice", identifier)
        worker.wake()
        await settled(worker, identifier, {"unchanged"})
        assert len(calls) == 2
    finally:
        await worker.close()
        store.db.close()


def test_addition_above_old_capacity_is_persisted(system):
    from test_memory_repository import write

    repo, queue, _, _ = system
    for i in range(200):
        repo.save_manual("alice", write(f"fact-{i}", entity=f"entity-{i}"), ("openai",))
    result, identifier = publish(system, "新背景", "new_topic")
    assert result.state == "completed"
    assert queue.status("alice", identifier).failure_code is None


def test_batch_crossing_old_capacity_preserves_both_facts(system):
    from test_memory_repository import write

    repo, queue, _, _ = system
    for i in range(199):
        repo.save_manual("alice", write(f"fact-{i}", entity=f"entity-{i}"), ("openai",))
    parts = ("我养猫", "我喜欢茶")
    source = "；".join(parts)
    operations = []
    start = 0
    for topic, content in zip(("pet", "drink"), parts, strict=True):
        operations.append(
            MemoryOperation(
                action="add",
                category="profile",
                topic=topic,
                entity="self",
                cardinality="single",
                content=content,
                source_start=start,
                source_end=start + len(content),
                source_kind="user",
                intent="explicit",
            )
        )
        start += len(content) + 1
    result, identifier = publish(system, source, "unused", operations=operations)
    assert result.state == "completed" and len(result.saved_ids) == 2 and result.skipped_count == 0
    assert repo.page_records("alice").total == 201
    assert repo.get("alice", result.saved_ids[0]).content == parts[0]
    assert repo.get("alice", result.saved_ids[1]).content == parts[1]
    assert queue.status("alice", identifier).failure_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,enabled,admission,expected",
    [
        ("normal", True, "pending", "pending"),
        ("normal", False, None, "paused"),
        ("temporary", True, None, "temporary"),
        ("normal", True, "failed", "unavailable"),
        ("normal", True, None, "ready"),
    ],
)
async def test_foreground_report_and_empty_context_receive_truthful_memory_state(
    tmp_path, monkeypatch, mode, enabled, admission, expected
):
    from gaia_agent.interactive import credential_runtime as module
    from gaia_agent.interactive.models import InteractiveRequest, InteractiveResult
    from gaia_agent.models import Usage

    store, sessions, memory, queue = setup(tmp_path)
    if not enabled:
        memory.repository.set_enabled("alice", False, memory.repository.settings("alice").revision)
    request = InteractiveRequest(
        question="请记住我学习Python", provider="openai", model="gpt-6.1-sol", memory_mode=mode
    )
    worker = MemoryWorker(queue, None, MemoryConsolidator(memory.repository, queue), sessions)
    cid = store.create("alice", "openai")["id"]
    run = store.reserve("alice", cid, request, "test-memory-state")
    if admission == "pending":
        worker.admit("alice", run, request, sessions.reference("alice", "openai", None))
    elif admission == "failed":
        with store.db:
            store.db.execute(
                "INSERT INTO memory_admissions VALUES(?,?,?,?)",
                ("alice", run["id"], "failed", "memory_limit"),
            )
    observed = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def with_options(self, **kwargs):
            return self

        async def close(self):
            pass

    async def execute(*args, **kwargs):
        observed.append(kwargs["memory_context"])
        return InteractiveResult(answer="synthetic", evidence=[], usage=Usage())

    monkeypatch.setattr(module, "AsyncOpenAI", Client)
    monkeypatch.setattr(module, "execute_interactive", execute)
    runtime = module.UserRuntime(
        module.InteractiveRuntimeSettings(base_url="https://api.openai.com/v1"), sessions, memory
    )
    result = await runtime.execute(
        sessions.reference("alice", "openai", None),
        request,
        tmp_path,
        [],
        lambda *args: None,
        None,
        memory_snapshot=memory.snapshot("alice", "openai", mode),
        run_id=run["id"],
    )
    assert result.memory.status == expected
    assert expected in observed[0]
    assert "Do not claim" in observed[0]
    sessions.close()
    store.db.close()
