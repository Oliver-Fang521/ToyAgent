import asyncio

import httpx
import pytest
from pydantic import SecretStr

from gaia_agent.interactive.credential_crypto import KeyCipher
from gaia_agent.interactive.credential_sessions import CredentialSessions
from gaia_agent.interactive.credential_vault import (
    CredentialError,
    CredentialMaterial,
    CredentialVault,
)
from gaia_agent.interactive.store import ConversationStore


@pytest.fixture
def sessions(tmp_path):
    store = ConversationStore(tmp_path / "state.sqlite")
    sessions = CredentialSessions(
        CredentialVault(store.db, KeyCipher({"v": b"a" * 32}, "v"), store.lock)
    )
    for owner in ("alice", "bob"):
        for provider in ("openai", "deepseek"):
            sessions.vault.put(
                owner, provider, SecretStr(f"synthetic-{owner}-{provider}"), consent=True
            )
    yield sessions
    store.db.close()


@pytest.mark.asyncio
async def test_owner_provider_cache_and_static_fallback(sessions):
    from gaia_agent.interactive.model_discovery import ModelDiscovery

    now, calls = [0], []

    async def transport(request):
        calls.append(request)
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"id": "gpt-6.1-sol"}, {"id": "unknown", "owned_by": "private-org"}],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), trust_env=False, follow_redirects=False
    ) as client:
        discovery = ModelDiscovery(sessions, client, clock=lambda: now[0])
        first = await discovery.discover("alice", "openai")
        assert first["discovery"]["status"] == "fresh"
        visible = [m["model"] for m in first["models"] if m["availability"] == "visible"]
        assert visible == ["gpt-6.1-sol"]
        assert "private-org" not in str(first)
        await discovery.discover("alice", "openai")
        assert len(calls) == 1
        await discovery.discover("bob", "openai")
        assert len(calls) == 2
        now[0] = 900
        await discovery.discover("alice", "openai")
        assert len(calls) == 3
        sessions.invalidate("alice")
        await discovery.discover("alice", "openai")
        assert len(calls) == 4
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(401, text="private-error"))
    ) as client:
        result = await ModelDiscovery(sessions, client).discover("alice", "openai")
        assert result["discovery"]["status"] == "unavailable"
        assert all(m["availability"] == "unknown" for m in result["models"])
        assert "private-error" not in str(result)


@pytest.mark.asyncio
async def test_discovery_rotation_during_refresh(sessions):
    from gaia_agent.interactive.model_discovery import ModelDiscovery

    started, release = asyncio.Event(), asyncio.Event()

    async def transport(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json={"data": [{"id": "gpt-6.1-sol"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        discovery = ModelDiscovery(sessions, client)
        task = asyncio.create_task(discovery.discover("alice", "openai"))
        await started.wait()
        sessions.invalidate("alice")
        release.set()
        with pytest.raises(CredentialError):
            await task
        assert not discovery._cache


@pytest.mark.asyncio
async def test_temporary_discovery_expiry_and_concurrent_refresh(sessions):
    from gaia_agent.interactive.model_discovery import ModelDiscovery

    calls = []

    async def transport(request):
        calls.append(request.headers["authorization"])
        await asyncio.sleep(0)
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}]})

    handle = sessions.issue(
        "alice", CredentialMaterial("deepseek", SecretStr("synthetic-temporary"))
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        discovery = ModelDiscovery(sessions, client)
        await asyncio.gather(*(discovery.discover("alice", "deepseek", handle) for _ in range(3)))
        assert calls == ["Bearer synthetic-temporary"]
        sessions.invalidate("alice")
        with pytest.raises(CredentialError):
            await discovery.discover("alice", "deepseek", handle)


def test_discovery_route_rejects_untrusted_fields_and_identity(tmp_path):
    from fastapi.testclient import TestClient

    from gaia_agent.interactive.api import create_app

    with TestClient(
        create_app(
            tmp_path,
            "test-service-token",
            execute=lambda *a: None,
            cipher=KeyCipher({"v": b"a" * 32}, "v"),
        )
    ) as client:
        path = "/v1/model-catalog/refresh"
        assert client.post(path, json={"provider": "openai"}).status_code == 401
        headers = {"authorization": "Bearer test-service-token", "x-agent-user": "alice"}
        assert (
            client.post(
                path,
                headers=headers,
                json={"provider": "openai", "base_url": "https://evil.example"},
            ).status_code
            == 422
        )
        assert (
            client.post(path, headers=headers, json={"provider": "openai"}).json()["error"]
            == "credential_required"
        )


@pytest.mark.asyncio
async def test_temporary_and_saved_discovery_never_share_cache(sessions):
    from gaia_agent.interactive.model_discovery import ModelDiscovery

    now, calls = [0], []
    sessions.clock = lambda: now[0]
    handle = sessions.issue(
        "alice", CredentialMaterial("deepseek", SecretStr("synthetic-temporary"))
    )

    def transport(request):
        key = request.headers["authorization"]
        calls.append(key)
        model = "deepseek-flash" if key == "Bearer synthetic-temporary" else "deepseek-v4-pro"
        return httpx.Response(200, json={"data": [{"id": model}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        discovery = ModelDiscovery(sessions, client)
        temporary = await discovery.discover("alice", "deepseek", handle)
        saved = await discovery.discover("alice", "deepseek")
        assert [m["model"] for m in temporary["models"] if m["availability"] == "visible"] == [
            "deepseek-flash"
        ]
        assert [m["model"] for m in saved["models"] if m["availability"] == "visible"] == [
            "deepseek-v4-pro"
        ]
        assert calls == ["Bearer synthetic-temporary", "Bearer synthetic-alice-deepseek"]
        now[0] = 1800
        with pytest.raises(CredentialError, match="credential_expired"):
            await discovery.discover("alice", "deepseek", handle)
        assert [
            m["model"]
            for m in (await discovery.discover("alice", "deepseek"))["models"]
            if m["availability"] == "visible"
        ] == ["deepseek-v4-pro"]
