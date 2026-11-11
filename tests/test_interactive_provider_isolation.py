import pytest
from pydantic import ValidationError

from gaia_agent.interactive.models import InteractiveRequest
from gaia_agent.interactive.store import ConversationStore, ServiceError


def test_request_requires_registered_selection():
    for payload in (
        {"question": "q"},
        {"question": "q", "provider": "deepseek", "model": "gpt-6.1-sol"},
    ):
        with pytest.raises(ValidationError):
            InteractiveRequest(**payload)


def test_binding_metadata_and_fingerprint(tmp_path):
    store = ConversationStore(tmp_path / "state.sqlite")
    cid = store.create("alice", "openai")["id"]
    request = InteractiveRequest(question="q", provider="openai", model="gpt-6.1-sol")
    run = store.reserve("alice", cid, request, "same")
    assert (run["provider"], run["model"]) == ("openai", "gpt-6.1-sol")
    alternate = InteractiveRequest(question="q", provider="deepseek", model="deepseek-flash")
    with pytest.raises(ServiceError, match="idempotency_conflict"):
        store.lookup("alice", cid, alternate, "same")
    with pytest.raises(ServiceError, match="conversation_provider_mismatch"):
        store.reserve("alice", cid, alternate, "different")


def test_portable_environment_and_policy(tmp_path, monkeypatch):
    from gaia_agent.interactive.catalog import ModelSelection, resolve_profile
    from gaia_agent.interactive.models import InteractiveConfig
    from gaia_agent.interactive.tool_policy import allowed_tools
    from gaia_agent.mcp_client import MCPToolClient

    monkeypatch.setenv("OPENAI_API_KEY", "operator-secret")
    profile = resolve_profile(ModelSelection(provider="deepseek", model="deepseek-flash"))
    allowed = allowed_tools(profile, set())
    assert allowed == frozenset({"read_webpage", "read_attachment", "submit_answer", "web_search"})
    client = MCPToolClient(
        task_root=tmp_path,
        config=InteractiveConfig(openai_api_key="unused"),
        allowed=allowed,
        runtime_env={"AGENT_TOOL_MODE": "portable"},
    )
    child = client._minimal_server_env()
    assert child["AGENT_TOOL_MODE"] == "portable"
    assert "OPENAI_API_KEY" not in child and "HF_TOKEN" not in child


def test_official_runtime_endpoints_only():
    from gaia_agent.interactive.credential_runtime import InteractiveRuntimeSettings

    for endpoint in (
        "https://models.example/v1",
        "https://api.openai.com/evil",
        "https://api.openai.com:444/v1",
    ):
        with pytest.raises(ValidationError):
            InteractiveRuntimeSettings(base_url=endpoint)


@pytest.mark.asyncio
async def test_portable_bootstrap_never_constructs_model_client(tmp_path, monkeypatch):
    from gaia_agent import mcp_server

    monkeypatch.setenv("GAIA_TASK_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_TOOL_MODE", "portable")
    monkeypatch.setenv("OPENAI_API_KEY", "operator-secret")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

    def forbidden(**kwargs):
        pytest.fail("portable tool process created an OpenAI client")

    monkeypatch.setattr(mcp_server, "AsyncOpenAI", forbidden)
    settings = mcp_server._settings_from_env()
    try:
        assert settings.api_key == "" and settings.search_provider is None
        with pytest.raises(Exception, match="capability_unavailable"):
            await settings.backend.execute_python("1+1", [])
    finally:
        await settings.aclose()


def test_historical_rows_keep_unknown_provider_and_are_read_only(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE conversations(id TEXT PRIMARY KEY, owner TEXT, created REAL)")
    db.execute("INSERT INTO conversations VALUES('old','alice',0)")
    db.commit()
    db.close()
    store = ConversationStore(path)
    assert store.get("alice", "old")["provider"] is None
    request = InteractiveRequest(question="q", provider="openai", model="gpt-6.1-sol")
    with pytest.raises(ServiceError, match="conversation_provider_mismatch"):
        store.reserve("alice", "old", request, "new")


@pytest.mark.asyncio
async def test_wrong_provider_denied_before_history_or_credentials(tmp_path):
    from gaia_agent.interactive.service import RunService

    store = ConversationStore(tmp_path / "state.sqlite")
    cid = store.create("alice", "openai")["id"]

    class ForbiddenSessions:
        def reference(self, *args):
            pytest.fail("read credentials for rejected conversation")

    service = RunService(store, tmp_path / "runs", None, credential_sessions=ForbiddenSessions())
    request = InteractiveRequest(question="q", provider="deepseek", model="deepseek-flash")
    with pytest.raises(ServiceError, match="conversation_provider_mismatch"):
        service.start("alice", cid, request, "new")
    assert not service.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["audio", "image", "scanned_pdf"])
async def test_unsupported_attachments_fail_before_model_creation(tmp_path, monkeypatch, kind):
    from types import SimpleNamespace

    from pydantic import SecretStr

    from gaia_agent.interactive import credential_runtime as module
    from gaia_agent.interactive.credential_vault import CredentialError, CredentialMaterial

    if kind == "scanned_pdf":
        from pypdf import PdfWriter

        attachment = tmp_path / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with attachment.open("wb") as stream:
            writer.write(stream)
    else:
        attachment = tmp_path / ("test.wav" if kind == "audio" else "test.png")
        attachment.write_bytes(b"synthetic")

    class Sessions:
        def resolve(self, ref):
            return CredentialMaterial("deepseek", SecretStr("synthetic-private"))

    def forbidden(**kwargs):
        pytest.fail("unsupported attachment caused a paid client to be created")

    monkeypatch.setattr(module, "AsyncOpenAI", forbidden)
    runtime = module.UserRuntime(
        module.InteractiveRuntimeSettings(base_url="https://api.openai.com/v1"), Sessions()
    )
    request = InteractiveRequest(question="analyze", provider="deepseek", model="deepseek-v4-pro")
    with pytest.raises(CredentialError, match="capability_unavailable"):
        await runtime.execute(
            SimpleNamespace(provider="deepseek"), request, tmp_path, [], lambda *a: None, attachment
        )


def test_safe_provider_preserves_structured_high_output_budget():
    from types import SimpleNamespace
    from gaia_agent.interactive.credential_runtime import SafePublicProvider
    inner = SimpleNamespace(supports_vision=False, structured_high_output_tokens=8000)
    assert SafePublicProvider(inner).structured_high_output_tokens == 8000
