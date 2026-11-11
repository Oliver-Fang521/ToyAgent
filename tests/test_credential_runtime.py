import asyncio
import os

import pytest
from pydantic import SecretStr

from gaia_agent.interactive.api import create_app
from gaia_agent.interactive.credential_crypto import KeyCipher
from gaia_agent.interactive.credential_runtime import (
    InteractiveRuntimeSettings,
    SafePublicProvider,
    UserRuntime,
)
from gaia_agent.interactive.credential_sessions import CredentialSessions
from gaia_agent.interactive.credential_vault import (
    CredentialError,
    CredentialMaterial,
    CredentialVault,
)
from gaia_agent.interactive.models import InteractiveConfig, InteractiveRequest, InteractiveResult
from gaia_agent.interactive.service import RunService
from gaia_agent.interactive.store import ConversationStore
from gaia_agent.mcp_client import MCPToolClient
from gaia_agent.models import Usage


def setup_runtime(tmp_path, clock=lambda: 0):
    store = ConversationStore(tmp_path / "state.sqlite")
    vault = CredentialVault(store.db, KeyCipher({"v1": b"a" * 32}, "v1"), store.lock)
    return store, CredentialSessions(vault, clock)


@pytest.mark.asyncio
async def test_scanned_pdf_enabled_for_openai_and_deepseek_visual_provider(tmp_path, monkeypatch):
    from pypdf import PdfWriter

    import gaia_agent.interactive.credential_runtime as module

    _store, sessions = setup_runtime(tmp_path)
    pdf = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(pdf)
    seen = []

    class FakeClient:
        def __init__(self, **kwargs): pass
        def with_options(self, **kwargs): return self
        async def close(self): pass

    async def execute(request, config, workspace, history, on_progress, **kwargs):
        seen.append((kwargs["provider"].supports_pdf_input, kwargs["provider"].supports_pdf_images))
        return InteractiveResult(answer="ok", evidence=[], usage=Usage())

    monkeypatch.setattr(module, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(module, "execute_interactive", execute)
    runtime = UserRuntime(InteractiveRuntimeSettings(base_url="https://api.openai.com/v1"), sessions)
    for provider, model in (("openai", "gpt-6.1-sol"), ("deepseek", "deepseek-flash")):
        material = CredentialMaterial(provider, SecretStr("synthetic-key-for-testing"))
        ref = sessions.reference("alice", provider, sessions.issue("alice", material))
        request = InteractiveRequest(provider=provider, model=model, question="Explain")
        await runtime.execute(ref, request, tmp_path, [], lambda *args: None, pdf)
    assert seen == [(True, False), (False, True)]


def test_mcp_explicit_environment_excludes_owner_keys(tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "SERPER_API_KEY", "SERPAPI_API_KEY", "HF_TOKEN"):
        monkeypatch.setenv(key, "synthetic-owner-secret-9812")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://owner.example/v1")
    before = dict(os.environ)
    env = {
        "AGENT_TOOL_MODE": "openai",
        "OPENAI_API_KEY": "synthetic-alice-model-8134",
        "OPENAI_BASE_URL": "https://api.openai.com/v1",
    }
    client = MCPToolClient(
        task_root=tmp_path,
        config=InteractiveConfig(openai_api_key=env["OPENAI_API_KEY"]),
        runtime_env=env,
    )
    child = client._minimal_server_env()
    assert child["OPENAI_API_KEY"] == env["OPENAI_API_KEY"]
    assert child["OPENAI_BASE_URL"] == "https://api.openai.com/v1"
    assert not any(k in child for k in ("SERPER_API_KEY", "SERPAPI_API_KEY", "HF_TOKEN"))
    assert "synthetic-owner-secret-9812" not in str(child)
    assert client._redact(env["OPENAI_API_KEY"]) == "[REDACTED]"
    assert dict(os.environ) == before


def test_public_tool_redaction_covers_nested_dictionary_keys(tmp_path):
    secret = "synthetic-nested-private-274918"
    config = InteractiveConfig(openai_api_key=secret)
    client = MCPToolClient(
        task_root=tmp_path,
        config=config,
        runtime_env={"AGENT_TOOL_MODE": "openai", "OPENAI_API_KEY": secret},
    )
    assert client._redact_value({"nested": [{secret: secret}]}) == {
        "nested": [{"[REDACTED]": "[REDACTED]"}]
    }


@pytest.mark.asyncio
async def test_runtime_concurrent_parent_and_child_user_isolation(tmp_path, monkeypatch):
    import gaia_agent.interactive.credential_runtime as module

    _store, sessions = setup_runtime(tmp_path)
    settings = InteractiveRuntimeSettings(base_url="https://api.openai.com/v1")
    clients = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.options = kwargs
            self.closed = False
            clients.append(self)

        def with_options(self, **kwargs):
            return self

        async def close(self):
            self.closed = True

    observed = []

    async def execute(request, config, workspace, history, on_progress, **kwargs):
        task = type("Task", (), {"attachment_path": None})()
        child = kwargs["tools_factory"](task)._minimal_server_env()
        observed.append((config.openai_api_key.get_secret_value(), child))
        await asyncio.sleep(0)
        return InteractiveResult(answer="ok", evidence=[], usage=Usage())

    monkeypatch.setattr(module, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(module, "execute_interactive", execute)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-owner-private-9123")
    before = dict(os.environ)
    refs = []
    for owner in ("alice", "bob"):
        material = CredentialMaterial(
            "openai" if owner == "alice" else "deepseek",
            SecretStr(f"synthetic-{owner}-private-4471"),
            SecretStr(f"synthetic-{owner}-search-3871") if owner == "alice" else None,
        )
        refs.append(sessions.reference(owner, material.provider, sessions.issue(owner, material)))
    runtime = UserRuntime(settings, sessions)
    await asyncio.gather(
        *(
            runtime.execute(
                ref,
                InteractiveRequest(
                    provider=ref.provider,
                    model="gpt-6.1-sol" if ref.provider == "openai" else "deepseek-flash",
                    question="q",
                ),
                tmp_path / ref.owner,
                [],
                lambda *args: None,
                None,
            )
            for ref in refs
        )
    )
    assert {c.options["api_key"] for c in clients} == {
        "synthetic-alice-private-4471",
        "synthetic-bob-private-4471",
    }
    assert {c.options["base_url"] for c in clients} == {
        "https://api.openai.com/v1",
        "https://api.deepseek.com",
    }
    assert all(c.closed for c in clients)
    for key, child in observed:
        if "alice" in key:
            assert child["OPENAI_API_KEY"] == key and child["AGENT_TOOL_MODE"] == "openai"
        else:
            assert "OPENAI_API_KEY" not in child and child["AGENT_TOOL_MODE"] == "portable"
            assert child["DEEPSEEK_API_KEY"] == key
        assert "synthetic-owner-private-9123" not in str(child)
        assert ("SERPER_API_KEY" in child) == ("alice" in key)
    assert dict(os.environ) == before


def test_deepseek_tools_use_only_owner_key_and_nullable_effort(tmp_path):
    from gaia_agent.interactive.catalog import ModelSelection, resolve_profile
    from gaia_agent.interactive.tool_policy import allowed_tools

    profile = resolve_profile(ModelSelection(provider="deepseek", model="deepseek-flash"))
    assert "web_search" in allowed_tools(profile, set())
    client = MCPToolClient(
        task_root=tmp_path,
        config=InteractiveConfig(openai_api_key="synthetic", reasoning_effort=None),
        runtime_env={
            "AGENT_TOOL_MODE": "portable",
            "DEEPSEEK_API_KEY": "synthetic-ds",
            "OPENAI_API_KEY": "wrong",
        },
    )
    env = client._minimal_server_env()
    assert env["DEEPSEEK_API_KEY"] == "synthetic-ds"
    assert "OPENAI_API_KEY" not in env
    assert env["GAIA_REASONING_EFFORT"] == ""
    assert client._redact("synthetic-ds") == "[REDACTED]"


@pytest.mark.asyncio
async def test_queued_expiry_and_idempotent_completed_retry_without_credentials(tmp_path):
    now = [0]
    store, sessions = setup_runtime(tmp_path, lambda: now[0])
    calls = []

    async def execute(ref, *args):
        sessions.resolve(ref)
        calls.append(ref.owner)
        return InteractiveResult(answer="paid answer", evidence=[], usage=Usage())

    service = RunService(store, tmp_path / "runs", execute, credential_sessions=sessions)
    cid = store.create("alice", "openai")["id"]
    req = InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="q")
    handle = sessions.issue(
        "alice", CredentialMaterial("openai", SecretStr("synthetic-queue-private-1239"))
    )
    await service.slots.acquire()
    await service.slots.acquire()
    run = service.start(
        "alice", cid, req, "expired", credential_ref=sessions.reference("alice", "openai", handle)
    )
    now[0] = 1800
    service.slots.release()
    await service.tasks[run["id"]]
    assert not calls
    assert service.get("alice", run["id"])["error"] == "credential_expired"
    handle = sessions.issue(
        "alice", CredentialMaterial("openai", SecretStr("synthetic-queue-private-1239"))
    )
    run = service.start(
        "alice", cid, req, "completed", credential_ref=sessions.reference("alice", "openai", handle)
    )
    await service.tasks[run["id"]]
    sessions.invalidate("alice")
    retry = service.start("alice", cid, req, "completed")
    assert retry["result"]["answer"] == "paid answer" and calls == ["alice"]
    with pytest.raises(CredentialError):
        service.start("alice", cid, req, "missing")
    assert store.db.execute("SELECT count(*) FROM runs").fetchone()[0] == 2
    await service.shutdown()


@pytest.mark.asyncio
async def test_provider_errors_are_safe_before_notebook_or_orchestrator(caplog):
    class UnsafeProvider:
        supports_vision = True

        async def complete(self, *args, **kwargs):
            raise RuntimeError({"nested": "synthetic-upstream-private-7318"})

    provider = SafePublicProvider(UnsafeProvider())
    with pytest.raises(CredentialError) as error:
        await provider.complete(None)
    assert str(error.value) == "provider_client_error"
    assert error.value.diagnostic["exception_type"] == "RuntimeError"
    assert "synthetic-upstream-private-7318" not in caplog.text


def test_public_startup_without_owner_model_key_and_no_key_run_rejected(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(
        tmp_path,
        "test-service-token",
        cipher=KeyCipher({"v1": b"a" * 32}, "v1"),
        runtime_settings=InteractiveRuntimeSettings(base_url="https://api.openai.com/v1"),
    )
    with TestClient(app) as client:
        headers = {
            "authorization": "Bearer test-service-token",
            "x-agent-user": "alice",
            "idempotency-key": "new",
        }
        cid = client.post("/v1/conversations", headers=headers, json={"provider": "openai"}).json()[
            "id"
        ]
        response = client.post(
            f"/v1/conversations/{cid}/runs",
            headers=headers,
            json={"provider": "openai", "model": "gpt-6.1-sol", "question": "q"},
        )
        assert response.status_code == 422 and response.json()["error"] == "credential_required"
        assert (
            client.get("/v1/credentials", headers=headers).json()["provider_hostname"]
            == "api.openai.com"
        )


@pytest.mark.asyncio
async def test_delete_cancels_active_and_queued_credentials_without_new_calls(tmp_path):
    store, sessions = setup_runtime(tmp_path)
    started = asyncio.Event()
    calls = []

    async def execute(ref, *args):
        calls.append(ref.owner)
        started.set()
        await asyncio.Event().wait()

    service = RunService(store, tmp_path / "runs", execute, credential_sessions=sessions)
    req = InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="q")
    cid = store.create("alice", "openai")["id"]
    handle = sessions.issue(
        "alice", CredentialMaterial("openai", SecretStr("synthetic-cancel-private-7481"))
    )
    ref = sessions.reference("alice", "openai", handle)
    run = service.start("alice", cid, req, "first", credential_ref=ref)
    await started.wait()
    sessions.invalidate("alice")
    await service.cancel_owner("alice")
    assert service.get("alice", run["id"])["status"] == "cancelled"
    await service.slots.acquire()
    await service.slots.acquire()
    handle = sessions.issue(
        "alice", CredentialMaterial("openai", SecretStr("synthetic-cancel-private-7481"))
    )
    run = service.start(
        "alice", cid, req, "second", credential_ref=sessions.reference("alice", "openai", handle)
    )
    sessions.invalidate("alice")
    await service.cancel_owner("alice")
    service.slots.release()
    assert service.get("alice", run["id"])["status"] == "cancelled" and calls == ["alice"]
    await service.shutdown()

@pytest.mark.asyncio
async def test_runtime_passes_selected_history_pdf_to_agent(tmp_path, monkeypatch):
    from pypdf import PdfWriter
    import gaia_agent.interactive.credential_runtime as module
    import gaia_agent.interactive.conversation_context as context
    store, sessions = setup_runtime(tmp_path)
    cid = store.create('alice', 'openai')['id']
    source = tmp_path / 'original.pdf'
    writer = PdfWriter(); writer.add_blank_page(width=200, height=200); writer.write(source)
    aid = store.upload('alice', cid, source, '习题.pdf')
    req = InteractiveRequest(provider='openai', model='gpt-6.1-sol', question='讲第一题', attachment_id=aid)
    run = store.reserve('alice', cid, req, 'first')
    store.update(run['id'], status='completed', result={'answer':'第一题答案','evidence':[],'usage':{}})
    class FakeClient:
        def __init__(self, **kwargs): pass
        def with_options(self, **kwargs): return self
        async def close(self): pass
    async def choose(*args):
        return context.FileSelection(action='read', attachment_id=aid, clarification='')
    seen = []
    async def execute(request, config, workspace, history, on_progress, **kwargs):
        seen.append(kwargs['attachment_path'].read_bytes())
        return InteractiveResult(answer='第二题', evidence=[], usage=Usage())
    monkeypatch.setattr(module, 'AsyncOpenAI', FakeClient)
    monkeypatch.setattr(module, 'execute_interactive', execute)
    monkeypatch.setattr(context, 'select_history_file', choose)
    runtime = UserRuntime(InteractiveRuntimeSettings(base_url='https://api.openai.com/v1'), sessions)
    material = CredentialMaterial('openai', SecretStr('synthetic-key-for-testing'))
    ref = sessions.reference('alice', 'openai', sessions.issue('alice', material))
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    result = await runtime.execute(ref, InteractiveRequest(provider='openai', model='gpt-6.1-sol', question='讲第二题'), workspace, store.history('alice',cid), lambda *args: None, None,
        conversation_files=store.conversation_attachments('alice',cid), resolve_conversation_file=lambda i: store.attachment('alice',cid,i))
    assert result.answer == '第二题'
    assert seen == [source.read_bytes()]

@pytest.mark.asyncio
async def test_service_followup_reopens_same_conversation_file(tmp_path, monkeypatch):
    from pypdf import PdfWriter
    import gaia_agent.interactive.credential_runtime as module
    import gaia_agent.interactive.conversation_context as context
    store, sessions = setup_runtime(tmp_path)
    cid = store.create('alice', 'openai')['id']
    pdf = tmp_path / 'questions.pdf'
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100); writer.write(pdf)
    aid = store.upload('alice', cid, pdf, '习题.pdf')
    first = store.reserve('alice', cid, InteractiveRequest(provider='openai',model='gpt-6.1-sol',question='讲第一题',attachment_id=aid), 'first')
    store.update(first['id'],status='completed',result={'answer':'第一题答案','usage':{},'evidence':[]})
    class FakeClient:
        def __init__(self, **kwargs): pass
        def with_options(self, **kwargs): return self
        async def close(self): pass
    async def choose(provider, question, history, files, effort):
        assert question == '讲第二题'
        assert any(item['id'] == aid for item in files)
        return context.FileSelection(action='read',attachment_id=aid,clarification='')
    seen = []
    async def execute(request, config, workspace, history, on_progress, **kwargs):
        seen.append(kwargs['attachment_path'].read_bytes())
        return InteractiveResult(answer='已讲第二题', evidence=[], usage=Usage())
    monkeypatch.setattr(module,'AsyncOpenAI',FakeClient)
    monkeypatch.setattr(module,'execute_interactive',execute)
    monkeypatch.setattr(context,'select_history_file',choose)
    runtime = UserRuntime(InteractiveRuntimeSettings(base_url='https://api.openai.com/v1'),sessions)
    service = RunService(store,tmp_path / 'runs',runtime.execute,credential_sessions=sessions)
    material = CredentialMaterial('openai',SecretStr('synthetic-key-for-testing'))
    ref = sessions.reference('alice','openai',sessions.issue('alice',material))
    run = service.start('alice',cid,InteractiveRequest(provider='openai',model='gpt-6.1-sol',question='讲第二题'),'second',credential_ref=ref)
    await service.tasks[run['id']]
    assert service.get('alice',run['id'])['result']['answer'] == '已讲第二题'
    assert seen == [pdf.read_bytes()]

@pytest.mark.asyncio
async def test_completed_answer_and_followup_do_not_wait_for_conversation_index(tmp_path, monkeypatch):
    import gaia_agent.interactive.conversation_context as context
    import gaia_agent.interactive.credential_runtime as module

    store, sessions = setup_runtime(tmp_path)
    cid = store.create("alice", "openai")["id"]
    index_entered, release_index = asyncio.Event(), asyncio.Event()
    clients, histories = [], []

    class FakeClient:
        def __init__(self, **kwargs):
            self.closed = False
            clients.append(self)

        def with_options(self, **kwargs):
            return self

        async def close(self):
            self.closed = True

    async def execute(request, config, workspace, history, *args, **kwargs):
        histories.append(history)
        return InteractiveResult(answer="线性变换的主方向", evidence=[], usage=Usage(total_tokens=4))

    async def index(provider, question, answer, files, effort):
        if question == "请解释主方向":
            index_entered.set()
            await release_index.wait()
        return context.ConversationIndex(summary="线性代数中的特征向量", keywords=["eigenvector"],
                                         usage=Usage(total_tokens=7))

    monkeypatch.setattr(module, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(module, "execute_interactive", execute)
    monkeypatch.setattr(context, "build_conversation_index", index)
    runtime = UserRuntime(InteractiveRuntimeSettings(base_url="https://api.openai.com/v1"), sessions)
    service = RunService(store, tmp_path/"runs", runtime.execute, credential_sessions=sessions,
                         conversation_indexer=runtime.index_conversation)
    ref = sessions.reference("alice", "openai", sessions.issue("alice", CredentialMaterial("openai", SecretStr("synthetic-test-key"))))
    request = InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="请解释主方向")
    run = service.start("alice", cid, request, "index", credential_ref=ref)
    try:
        await asyncio.wait_for(service.tasks[run["id"]], 2)
        await asyncio.wait_for(index_entered.wait(), 2)
        first_index = service.index_tasks["alice", run["id"]]
        assert service.get("alice", run["id"])["status"] == "completed"
        assert clients[0].closed and not clients[1].closed
        assert not store.search_conversation("alice", cid, "eigenvector")
        followup = service.start("alice", cid, request.model_copy(update={"question": "能举个例子吗？"}),
                                 "next", credential_ref=ref)
        await asyncio.wait_for(service.tasks[followup["id"]], 2)
        assert histories[1][:2] == [{"role": "user", "content": "请解释主方向"},
                                    {"role": "assistant", "content": "线性变换的主方向"}]
        release_index.set()
        await first_index
        assert any(item["run_id"] == run["id"] for item in store.search_conversation("alice", cid, "eigenvector"))
        assert service.get("alice", run["id"])["result"]["usage"]["total_tokens"] == 11
    finally:
        release_index.set()
        await service.shutdown()
    assert all(client.closed for client in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["index_error", "user_revoked", "service_shutdown"])
async def test_background_index_stop_preserves_delivered_answer(tmp_path, stop):
    store, sessions = setup_runtime(tmp_path)
    cid = store.create("alice", "openai")["id"]
    entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def execute(*args, **kwargs):
        return InteractiveResult(answer="已交付", evidence=[], usage=Usage())

    async def index(ref, request, run, files):
        assert run["status"] == "completed"
        entered.set()
        try:
            await release.wait()
            raise ValueError("index unavailable")
        finally:
            exited.set()

    service = RunService(store, tmp_path/"runs", execute, credential_sessions=sessions,
                         conversation_indexer=index)
    ref = sessions.reference("alice", "openai", sessions.issue("alice", CredentialMaterial("openai", SecretStr("synthetic-test-key"))))
    run = service.start("alice", cid, InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="hello"), "stop", credential_ref=ref)
    try:
        await asyncio.wait_for(service.tasks[run["id"]], 2)
        await asyncio.wait_for(entered.wait(), 2)
        job = service.index_tasks["alice", run["id"]]
        if stop == "index_error":
            release.set()
            await job
        elif stop == "user_revoked":
            sessions.invalidate("alice")
            await service.cancel_owner("alice")
        else:
            await service.shutdown()
        assert exited.is_set()
        assert service.get("alice", run["id"])["result"]["answer"] == "已交付"
        assert service.get("alice", run["id"])["status"] == "completed"
        assert store.db.execute("SELECT count(*) FROM conversation_index").fetchone()[0] == 0
    finally:
        release.set()
        await service.shutdown()
