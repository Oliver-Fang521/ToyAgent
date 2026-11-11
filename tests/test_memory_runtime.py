import time

import pytest
from conftest import FakeProvider

from gaia_agent.interactive.composition import execute_interactive
from gaia_agent.interactive.memory.extraction import MemoryExtractor
from gaia_agent.interactive.memory.models import MemoryCandidate, MemoryFailure
from gaia_agent.interactive.memory.runtime import run_with_memory
from gaia_agent.interactive.models import InteractiveConfig, InteractiveRequest, InteractiveResult
from gaia_agent.models import Usage
from gaia_agent.reliability import ensure_current_invocation_allowed, invocation_deadline_scope

pytest_plugins = ("test_memory_service",)


def test_memory_provider_preserves_pdf_capability():
    from gaia_agent.interactive.memory.runtime import MemoryContextProvider

    provider = FakeProvider()
    provider.supports_pdf_input = True
    assert MemoryContextProvider(provider, "user context").supports_pdf_input is True
    provider.supports_pdf_input = False
    assert MemoryContextProvider(provider, "user context").supports_pdf_input is False


def save(service):
    write = service.policy.validate_manual(
        MemoryCandidate(
            category="profile",
            facet="response_style",
            entity="self",
            content="I prefer short Chinese explanations",
            source_start=0,
            source_end=0,
            explicit_relearn=False,
        )
    )
    return service.repository.save_manual("alice", write, ("openai",))


@pytest.mark.asyncio
async def test_new_conversation_context_is_separate_from_question_and_verifiers(service, tmp_path):
    save(service)
    records = service.snapshot("alice", "openai", "normal").records
    context = service.retriever.render(records)
    provider = FakeProvider()
    provider.return_parsed(
        {"route": "direct", "answer": "English as requested", "needs_verification": False}
    )
    result = await execute_interactive(
        InteractiveRequest(question="Explain in English", provider="openai", model="gpt-6.1-sol"),
        InteractiveConfig(openai_api_key="fake"),
        tmp_path,
        provider=provider,
        memory_context=context,
    )
    assert result.answer == "English as requested"
    assert "untrusted" in str(provider.requests[0].input_items)
    assert "Explain in English" in str(provider.requests[0].input_items)
    assert service.snapshot("bob", "openai", "normal").records == ()
    assert service.snapshot("alice", "deepseek", "normal").records == ()
    assert service.snapshot("alice", "openai", "temporary").records == ()


@pytest.mark.asyncio
async def test_failed_extraction_keeps_answer_and_partial_usage_once(service):
    class Partial:
        async def complete(self, request, executor=None, *, usage_observer=None):
            usage_observer(Usage(input_tokens=11, output_tokens=2))
            raise MemoryFailure("memory_unavailable")

    async def answer():
        return InteractiveResult(answer="useful", evidence=[], usage=Usage(input_tokens=7))

    snapshot = service.snapshot("alice", "openai", "normal")
    service.repository.register_turn("alice", "one")
    result = await run_with_memory(
        answer,
        snapshot,
        MemoryExtractor(Partial()),
        "I study Python",
        "one",
        time.monotonic() + 10,
        memory_service=service,
    )
    assert result.answer == "useful" and result.usage.input_tokens == 18
    assert result.memory.status == "unavailable"
    result = await run_with_memory(
        answer,
        snapshot,
        MemoryExtractor(Partial()),
        "I study Python",
        "one",
        time.monotonic() + 10,
        memory_service=service,
    )
    assert result.usage.input_tokens == 7  # claimed before failed extraction, never charged twice


@pytest.mark.asyncio
async def test_answer_failure_never_extracts_and_time_budget_skips(service):
    async def failure():
        raise ValueError("main failed")

    provider = FakeProvider()  # No queued responses: any call would fail.
    snapshot = service.snapshot("alice", "openai", "normal")
    with pytest.raises(ValueError):
        await run_with_memory(
            failure,
            snapshot,
            MemoryExtractor(provider),
            "I study Python",
            "one",
            time.monotonic() + 10,
            memory_service=service,
        )

    async def answer():
        return InteractiveResult(answer="ok", evidence=[], usage=Usage())

    result = await run_with_memory(
        answer,
        snapshot,
        MemoryExtractor(provider),
        "I study Python",
        "two",
        time.monotonic() + 0.1,
        memory_service=service,
    )
    assert result.memory.status == "skipped" and provider.requests == []


@pytest.mark.asyncio
async def test_revoked_outer_guard_cannot_be_loosened(service):
    from gaia_agent.interactive.memory.guard import MemoryRevoked
    from gaia_agent.reliability import InvocationDeadline

    snapshot = service.snapshot("alice", "openai", "normal")
    with service.lease(snapshot, time.monotonic() + 10) as lease, invocation_deadline_scope(lease):
        lease.close()
        with (
            invocation_deadline_scope(InvocationDeadline(time.monotonic() + 500, time.monotonic)),
            pytest.raises(MemoryRevoked),
        ):
            ensure_current_invocation_allowed()


def test_mode_changes_request_fingerprint(tmp_path):
    from gaia_agent.interactive.store import ConversationStore, ServiceError

    store = ConversationStore(tmp_path / "state.sqlite")
    cid = store.create("alice", "openai")["id"]
    request = InteractiveRequest(question="hello", provider="openai", model="gpt-6.1-sol")
    store.reserve("alice", cid, request, "same")
    with pytest.raises(ServiceError, match="idempotency_conflict"):
        store.lookup("alice", cid, request.model_copy(update={"memory_mode": "temporary"}), "same")


@pytest.mark.asyncio
async def test_retrieval_failure_after_answer_degrades_only_memory(service, monkeypatch):
    snapshot = service.snapshot("alice", "openai", "normal")

    async def answer():
        return InteractiveResult(answer="still useful", evidence=[], usage=Usage())

    def broken(*args):
        raise RuntimeError("private retrieval failure")

    monkeypatch.setattr(service.retriever, "select", broken)
    result = await run_with_memory(
        answer,
        snapshot,
        MemoryExtractor(FakeProvider()),
        "I study Python",
        "one",
        time.monotonic() + 10,
        memory_service=service,
    )
    assert result.answer == "still useful" and result.memory.status == "unavailable"


@pytest.mark.asyncio
async def test_revocation_stops_tool_continuation_transport(service):
    from test_openai_provider import FakeResponsesClient, model_request

    from gaia_agent.interactive.memory.guard import MemoryRevoked
    from gaia_agent.providers import OpenAIProvider

    client = FakeResponsesClient()
    client.queue_function_call("execute_python", '{"code":"print(42)"}', "one")
    client.queue_text('{"answer":"42"}')
    snapshot = service.snapshot("alice", "openai", "normal")
    with service.lease(snapshot, time.monotonic() + 10) as lease:

        class Executor:
            async def call(self, name, arguments):
                lease.close()
                return {"stdout": "42"}

        with invocation_deadline_scope(lease), pytest.raises(MemoryRevoked):
            await OpenAIProvider(client, "test").complete(model_request(), Executor())
        assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_context_preparation_failure_still_runs_without_memory(
    service, tmp_path, monkeypatch
):
    from pydantic import SecretStr
    from test_credential_runtime import setup_runtime

    import gaia_agent.interactive.credential_runtime as module
    from gaia_agent.interactive.credential_vault import CredentialMaterial

    _, sessions = setup_runtime(tmp_path / "credentials")
    ref = sessions.reference(
        "alice",
        "openai",
        sessions.issue("alice", CredentialMaterial("openai", SecretStr("synthetic-model-key"))),
    )

    class Client:
        def __init__(self, **kwargs):
            pass

        def with_options(self, **kwargs):
            return self

        async def close(self):
            pass

    async def answer(*args, **kwargs):
        assert "Memory state: unavailable" in kwargs["memory_context"]
        assert "Do not claim" in kwargs["memory_context"]
        return InteractiveResult(answer="still useful", evidence=[], usage=Usage())

    snapshot = service.snapshot("alice", "openai", "normal")

    def broken(*args):
        raise RuntimeError("private retrieval failure")

    from gaia_agent.interactive.memory.summaries import SummaryStore

    monkeypatch.setattr(SummaryStore, "overview", broken)
    monkeypatch.setattr(module, "AsyncOpenAI", Client)
    monkeypatch.setattr(module, "execute_interactive", answer)
    runtime = module.UserRuntime(
        module.InteractiveRuntimeSettings(base_url="https://api.openai.com/v1"), sessions, service
    )
    result = await runtime.execute(
        ref,
        InteractiveRequest(question="q", provider="openai", model="gpt-6.1-sol"),
        tmp_path,
        (),
        lambda *args: None,
        None,
        memory_snapshot=snapshot,
        run_id="one",
        deadline=time.monotonic() + 10,
    )
    assert result.answer == "still useful" and result.memory.status == "unavailable"


def test_reopened_history_preserves_safe_memory_report(tmp_path):
    from gaia_agent.interactive.store import ConversationStore

    store = ConversationStore(tmp_path / "state.sqlite")
    cid = store.create("alice", "openai")["id"]
    run = store.reserve(
        "alice",
        cid,
        InteractiveRequest(question="hello", provider="openai", model="gpt-6.1-sol"),
        "one",
    )
    report = {"status": "temporary", "used_ids": [], "saved_ids": [], "skipped_count": 0}
    store.update(
        run["id"], status="completed", result={"answer": "hello", "evidence": [], "memory": report}
    )
    assert store.messages("alice", cid)["messages"][-1]["memory"] == report


@pytest.mark.asyncio
async def test_public_service_registers_only_new_normal_runs_and_terminal_replay(service, tmp_path):
    from gaia_agent.interactive.service import RunService
    from gaia_agent.interactive.store import ConversationStore

    store = ConversationStore(tmp_path / "runs.sqlite")
    calls = []

    async def execute(
        request,
        workspace,
        history,
        on_progress,
        attachment_path,
        *,
        memory_snapshot=None,
        run_id=None,
        deadline=None,
    ):
        calls.append((memory_snapshot.mode, run_id))
        ensure_current_invocation_allowed()
        return InteractiveResult(answer="ok", evidence=[], usage=Usage())

    runs = RunService(store, tmp_path / "runs", execute, memory_service=service)
    request = InteractiveRequest(question="I study Python", provider="openai", model="gpt-6.1-sol")
    cid = store.create("alice", "openai")["id"]
    first = runs.start("alice", cid, request, "normal")
    await runs.tasks[first["id"]]
    runs.start("alice", cid, request, "normal")
    assert len(calls) == 1
    request = request.model_copy(update={"memory_mode": "temporary"})
    second = runs.start("alice", cid, request, "temporary")
    await runs.tasks[second["id"]]
    assert [c[0] for c in calls] == ["normal", "temporary"]
    assert service.repository.db.execute("SELECT count(*) FROM memory_turns").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_model_result_then_manual_edit_defeats_delayed_extraction(service):
    item = save(service)
    snapshot = service.snapshot("alice", "openai", "normal")
    service.repository.register_turn("alice", "late")

    class Delayed:
        async def extract(self, text, existing, *, usage_observer):
            service.repository.update("alice", item.id, {"content": "I prefer English"}, 1)
            return (
                MemoryCandidate(
                    category="profile",
                    facet="response_style",
                    entity="self",
                    content=text,
                    source_start=0,
                    source_end=len(text),
                    explicit_relearn=False,
                ),
            )

    report = await service.remember(
        snapshot,
        "late",
        "I prefer Chinese",
        Delayed(),
        deadline=time.monotonic() + 10,
        usage_observer=lambda _: None,
    )
    assert not report.saved_ids
    assert service.repository.get("alice", item.id).content == "I prefer English"


@pytest.mark.asyncio
async def test_revocation_stops_real_provider_retry_transport(service):
    import httpx
    from openai import APIConnectionError
    from test_openai_provider import model_request

    from gaia_agent.interactive.memory.guard import MemoryRevoked
    from gaia_agent.providers import OpenAIProvider
    from gaia_agent.reliability import RetryPolicy

    snapshot = service.snapshot("alice", "openai", "normal")
    with service.lease(snapshot, time.monotonic() + 10) as lease:

        class Transport:
            def __init__(self):
                self.responses = self
                self.calls = 0

            def with_options(self, **options):
                return self

            async def create(self, **kwargs):
                self.calls += 1
                lease.close()
                raise APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))

        client = Transport()
        provider = OpenAIProvider(client, "test", retry_policy=RetryPolicy(max_attempts=3))
        with invocation_deadline_scope(lease), pytest.raises(MemoryRevoked):
            await provider.complete(model_request())
        assert client.calls == 1


def test_memory_wrapper_preserves_pdf_image_and_output_budget_capabilities():
    from gaia_agent.interactive.memory.runtime import MemoryContextProvider
    from types import SimpleNamespace
    provider=SimpleNamespace(supports_vision=True,supports_pdf_images=True,structured_high_output_tokens=8000)
    wrapped=MemoryContextProvider(provider,'context')
    assert wrapped.supports_pdf_images and wrapped.structured_high_output_tokens == 8000
