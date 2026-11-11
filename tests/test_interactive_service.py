import asyncio

import pytest

from gaia_agent.interactive.models import InteractiveRequest, InteractiveResult
from gaia_agent.interactive.service import RunService
from gaia_agent.interactive.store import ConversationStore, ServiceError
from gaia_agent.models import Usage


def test_conversation_list_uses_first_question_as_owner_isolated_title(tmp_path):
    store = ConversationStore(tmp_path / "titles.sqlite")
    alice = store.create("alice", "openai")
    bob = store.create("bob", "openai")
    first = store.reserve("alice", alice["id"], InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="  如何学习 Python？\n请给我计划  "), "a1")
    store.update(first["id"], status="completed")
    store.reserve("alice", alice["id"], InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="另一个问题"), "a2")
    store.reserve("bob", bob["id"], InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="Bob's private question"), "b1")
    assert store.list("alice")[0]["title"] == "如何学习 Python？ 请给我计划"
    assert store.list("bob")[0]["title"] == "Bob's private question"
    assert len(store.list("alice")) == 1


def test_store_denies_cross_user_and_persists(tmp_path):
    store = ConversationStore(tmp_path / "db.sqlite")
    conversation = store.create("alice", "openai")
    with pytest.raises(ServiceError):
        store.get("bob", conversation["id"])
    assert (
        ConversationStore(tmp_path / "db.sqlite").get("alice", conversation["id"])["id"]
        == conversation["id"]
    )


@pytest.mark.asyncio
async def test_duplicate_submission_does_not_execute_twice(tmp_path):
    store = ConversationStore(tmp_path / "db.sqlite")
    calls = []

    async def execute(request, workspace, history, on_progress, attachment_path):
        calls.append(request.question)
        return InteractiveResult(answer="answer", evidence=[], usage=Usage())

    service = RunService(store, tmp_path / "runs", execute)
    cid = store.create("alice", "openai")["id"]
    request = InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="question")
    first = service.start("alice", cid, request, "key")
    second = service.start("alice", cid, request, "key")
    assert first["id"] == second["id"]
    await service.tasks[first["id"]]
    assert calls == ["question"]
    assert service.get("alice", first["id"])["status"] == "completed"
    with pytest.raises(ServiceError):
        service.get("bob", first["id"])


@pytest.mark.asyncio
async def test_active_limit_preserved_without_hourly_submission_limit(tmp_path):
    store = ConversationStore(tmp_path / "db.sqlite")
    gate = asyncio.Event()

    async def execute(*args):
        await gate.wait()

    service = RunService(store, tmp_path / "runs", execute)
    cid = store.create("alice", "openai")["id"]
    req = InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="q")
    first = service.start("alice", cid, req, "1")
    with pytest.raises(ServiceError) as active:
        service.start("alice", cid, req, "2")
    assert active.value.status_code == 429
    await service.cancel("alice", first["id"])
    for key in ("2", "3", "4", "5"):
        run = service.start("alice", cid, req, key)
        await service.cancel("alice", run["id"])


def test_restart_marks_unfinished_runs_interrupted(tmp_path):
    store = ConversationStore(tmp_path / "db.sqlite")
    cid = store.create("alice", "openai")["id"]
    run = store.reserve(
        "alice",
        cid,
        InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="q"),
        "key",
    )
    store.interrupt_unfinished()
    assert store.run("alice", run["id"])["status"] == "interrupted"


def test_effort_changes_idempotency_and_persisted_history(tmp_path):
    store = ConversationStore(tmp_path / "effort.sqlite")
    cid = store.create("alice", "openai")["id"]
    req = InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="q")
    first = store.reserve("alice", cid, req, "same")
    explicit = InteractiveRequest(
        provider="openai", model="gpt-6.1-sol", question="q", reasoning_effort="low"
    )
    assert store.lookup("alice", cid, explicit, "same")["id"] == first["id"]
    assert first["reasoning_effort"] == "low"
    assert first["capability_version"] == "2026-10-19.1"
    high = explicit.model_copy(update={"reasoning_effort": "high"})
    with pytest.raises(ServiceError, match="idempotency_conflict"):
        store.lookup("alice", cid, high, "same")
    store.update(
        first["id"], status="completed", result={"answer": "a", "evidence": [], "usage": {}}
    )
    reopened = ConversationStore(tmp_path / "effort.sqlite")
    assert reopened.messages("alice", cid)["messages"][1]["reasoning_effort"] == "low"
    with reopened.db:
        reopened.db.execute("UPDATE runs SET reasoning_effort=NULL,capability_version=NULL")
    assert reopened.messages("alice", cid)["messages"][1]["reasoning_effort"] is None
    reopened.db.close()
    store.db.close()


def test_invalid_reasoning_rejected_and_plain_model_omits_effort():
    from gaia_agent.interactive.models import resolve_selection

    with pytest.raises(ValueError):
        InteractiveRequest(
            provider="openai", model="gpt-6.1-sol", question="q", reasoning_effort="none"
        )
    with pytest.raises(ValueError):
        InteractiveRequest(
            provider="deepseek", model="deepseek-flash", question="q", reasoning_effort="medium"
        )
    plain = InteractiveRequest(provider="openai", model="gpt-4.1", question="q")
    assert resolve_selection(plain).reasoning_effort is None
