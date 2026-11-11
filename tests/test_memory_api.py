import pytest
from fastapi.testclient import TestClient

from gaia_agent.interactive.api import create_app
from gaia_agent.interactive.memory.crypto import MemoryCrypto

HEADERS = {"authorization": "Bearer test-service-token", "x-agent-user": "alice"}
BODY = {
    "category": "profile",
    "facet": "learning_subjects",
    "entity": "self",
    "content": "I study Python",
    "allowed_providers": ["openai"],
}


def test_management_search_pages_all_records_and_binds_cursor(client):
    from test_memory_repository import write
    repo = client.app.state.memory_service.repository
    first = None
    for i in range(210):
        item = repo.save_manual("alice", write(f"background key{i}", entity=str(i)), ("openai",))
        first = first or item
    page = client.get("/v1/memories/search?q=&limit=50", headers=HEADERS)
    assert page.status_code == 200 and page.json()["total"] == 210
    cursor = page.json()["next_cursor"]
    assert cursor
    assert client.get("/v1/memories/search", params={"cursor": cursor}, headers=HEADERS | {"x-agent-user": "bob"}).status_code == 409
    result = client.get("/v1/memories/search?q=key0", headers=HEADERS).json()
    assert [r["id"] for r in result["items"]] == [first.id]
    assert client.get("/v1/memories/search").status_code == 401


def test_summary_maintenance_is_safe_and_retry_requires_cost_confirmation(client):
    from gaia_agent.interactive.memory.maintenance_queue import MaintenanceQueue
    repo = client.app.state.memory_service.repository
    q = MaintenanceQueue(repo)
    state = repo.state_snapshot("alice", "openai")
    q.mark_dirty(state.scope, {"model": "test", "private": "must-not-return"}, 0, ("opaque",), now=0)
    claim = q.claim(10, "worker")
    q.reserve_dispatch(claim, now=10)
    q.finish(claim, state="failed", code="memory_timeout")
    result = client.get("/v1/memories/maintenance", headers=HEADERS)
    assert result.status_code == 200 and "must-not-return" not in result.text
    path = f"/v1/memories/maintenance/{claim.id}/retry"
    assert client.post(path, headers=HEADERS, json={"confirm_cost": False}).status_code == 422
    assert client.post(path, headers=HEADERS, json={"confirm_cost": True}).status_code == 200


def test_memory_snapshot_authenticated_no_store_and_isolated(client):
    path = "/v1/memories/snapshot"
    assert client.get(path).status_code == 401
    client.post("/v1/memories", headers=HEADERS, json=BODY)
    result = client.get(path, headers=HEADERS)
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["items"][0]["content"] == "I study Python"
    assert result.json()["settings"]["enabled"] is True
    assert client.get(path, headers=HEADERS | {"x-agent-user": "bob"}).json()["items"] == []


def test_snapshot_overview_is_scoped_and_invalidated_when_fact_changes(client):
    from gaia_agent.interactive.memory.layer_models import FactRef, SummaryDraft
    from gaia_agent.interactive.memory.summaries import SummaryStore
    repo = client.app.state.memory_service.repository
    item = client.post("/v1/memories", headers=HEADERS, json=BODY).json()
    state = repo.state_snapshot("alice", "openai")
    SummaryStore(repo).publish(state.scope, SummaryDraft("overview", "account-overview", "Learns Python", (FactRef(item["id"], item["revision"]),)), state.content_revision)
    path = "/v1/memories/snapshot"
    assert client.get(path, headers=HEADERS).json()["overviews"]["openai"] == "Learns Python"
    assert client.get(path, headers=HEADERS | {"x-agent-user": "bob"}).json()["overviews"]["openai"] is None
    assert client.post(f"/v1/memories/{item['id']}/delete", headers=HEADERS, json={"revision": item["revision"]}).status_code == 200
    assert client.get(path, headers=HEADERS).json()["overviews"]["openai"] is None


@pytest.fixture
def client(tmp_path):
    app = create_app(
        tmp_path,
        "test-service-token",
        execute=lambda *args: None,
        memory_crypto=MemoryCrypto({"v1": b"a" * 32}, "v1"),
    )
    with TestClient(app) as client:
        yield client


def test_authenticated_crud_without_key_and_no_store(client):
    for path in ("/v1/memory/settings", "/v1/memories"):
        assert client.get(path).status_code == 401
        response = client.get(path, headers=HEADERS)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/v1/memory/settings", headers=HEADERS).json()["enabled"] is True
    saved = client.post("/v1/memories", headers=HEADERS, json=BODY)
    assert saved.status_code == 200
    item = saved.json()
    assert not {"owner", "canonical_key", "ciphertext"} & item.keys()
    path = f"/v1/memories/{item['id']}"
    assert (
        client.post(
            path + "/update", headers=HEADERS, json={"revision": 0, "content": "Rust"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            path + "/delete", headers=HEADERS | {"x-agent-user": "bob"}, json={"revision": 1}
        ).status_code
        == 404
    )
    assert (
        client.post(
            path + "/update", headers=HEADERS, json={"revision": 1, "content": "I study Rust"}
        ).json()["revision"]
        == 2
    )
    assert client.post(path + "/delete", headers=HEADERS, json={"revision": 2}).json()["deleted"]
    assert client.get("/v1/memories", headers=HEADERS).json()["items"] == []
    assert client.get("/v1/memory/settings", headers=HEADERS).json()["suppressed_facets"] == []


def test_open_topic_and_safe_maintenance_snapshot(client):
    saved = client.post(
        "/v1/memories", headers=HEADERS, json=BODY | {"facet": "ordinary_custom_topic"}
    )
    assert saved.status_code == 200
    data = client.get("/v1/memories/snapshot", headers=HEADERS).json()
    assert "maintenance" in data and "barriers" in data
    assert not {"source", "ciphertext", "nonce"} & data["maintenance"].keys()


def test_permanent_delete_and_revision_bound_restore(client):
    item = client.post("/v1/memories", headers=HEADERS, json=BODY).json()
    assert (
        client.post(
            f"/v1/memories/{item['id']}/delete",
            headers=HEADERS,
            json={"revision": 1, "permanent": True},
        ).status_code
        == 200
    )
    barriers = client.get("/v1/memories/snapshot", headers=HEADERS).json()["barriers"]
    assert len(barriers) == 1 and "content" not in barriers[0]
    path = f"/v1/memories/barriers/{barriers[0]['id']}/restore"
    assert (
        client.post(
            path, headers=HEADERS | {"x-agent-user": "bob"}, json={"revision": 1}
        ).status_code
        == 404
    )
    assert client.post(path, headers=HEADERS, json={"revision": 0}).status_code == 409
    assert client.post(path, headers=HEADERS, json={"revision": 1}).status_code == 200
    assert client.get("/v1/memories/snapshot", headers=HEADERS).json()["barriers"] == []


@pytest.mark.parametrize(
    "patch",
    [
        {"owner": "bob"},
        {"content": "a" * 501},
        {"content": "My password is abc123"},
        {"allowed_providers": ["anthropic"]},
    ],
)
def test_invalid_input_rejected_without_echo(client, patch):
    response = client.post("/v1/memories", headers=HEADERS, json=BODY | patch)
    assert response.status_code == 422
    assert "abc123" not in response.text


def test_settings_literal_boolean_and_offsets(client):
    assert (
        client.post(
            "/v1/memory/settings", headers=HEADERS, json={"enabled": 1, "revision": 0}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/memory/settings", headers=HEADERS, json={"enabled": False, "revision": 0}
        ).json()["enabled"]
        is False
    )
    assert client.get("/v1/memories?offset=-1", headers=HEADERS).status_code == 422
    assert client.get("/v1/memories?limit=51", headers=HEADERS).status_code == 422


def test_nonfinite_expiry_is_rejected(client):
    item = client.post("/v1/memories", headers=HEADERS, json=BODY).json()
    response = client.post(
        f"/v1/memories/{item['id']}/update",
        headers=HEADERS | {"content-type": "application/json"},
        content='{"revision":1,"expires_at":NaN}',
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "auth", [{}, {"authorization": "Bearer wrong-token", "x-agent-user": "alice"}]
)
def test_all_memory_mutations_require_service_auth(client, auth):
    item = client.post("/v1/memories", headers=HEADERS, json=BODY).json()
    actions = [
        ("/v1/memories", BODY),
        (f"/v1/memories/{item['id']}/update", {"revision": 1, "content": "I study Rust"}),
        (f"/v1/memories/{item['id']}/delete", {"revision": 1}),
        ("/v1/memories/clear", {"revision": 1}),
        ("/v1/memory/settings", {"enabled": False, "revision": 0}),
    ]
    for path, body in actions:
        response = client.post(path, headers=auth, json=body)
        assert response.status_code == 401
        assert BODY["content"] not in response.text
    records = client.get("/v1/memories", headers=HEADERS).json()["items"]
    assert len(records) == 1
    assert records[0]["revision"] == 1
    assert client.get("/v1/memory/settings", headers=HEADERS).json()["enabled"] is True
