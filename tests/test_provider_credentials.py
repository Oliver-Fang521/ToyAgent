import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from gaia_agent.interactive.api import create_app
from gaia_agent.interactive.credential_crypto import KeyCipher
from gaia_agent.interactive.credential_sessions import CredentialSessions
from gaia_agent.interactive.credential_vault import (
    CredentialError,
    CredentialMaterial,
    CredentialVault,
)
from gaia_agent.interactive.store import ConversationStore


def legacy(vault):
    value = SecretStr("synthetic-legacy-provider-8877")
    envelope = vault.cipher.encrypt("alice", "model", value)
    with vault.db:
        vault.db.execute(
            "INSERT INTO credentials VALUES(?,?,?,?,?,?,?,?)",
            (
                "alice",
                "model",
                envelope.version,
                envelope.nonce,
                envelope.ciphertext,
                "8877",
                1,
                1,
            ),
        )
    return value


@pytest.fixture
def vault(tmp_path):
    store = ConversationStore(tmp_path / "credentials.sqlite")
    result = CredentialVault(store.db, KeyCipher({"v1": b"a" * 32}, "v1"), store.lock)
    yield result
    store.db.close()


def test_wrong_provider_reference_rejected_before_key_resolution(vault):
    sessions = CredentialSessions(vault)
    handle = sessions.issue(
        "alice", CredentialMaterial(provider="deepseek", model=SecretStr("fake-ds"))
    )
    ref = sessions.reference("alice", "deepseek", handle)
    assert sessions.resolve(ref).provider == "deepseek"
    with pytest.raises(CredentialError):
        sessions.reference("alice", "openai", handle)
    with pytest.raises(CredentialError):
        sessions.reference("bob", "deepseek", handle)
    with pytest.raises(CredentialError):
        vault.put("alice", "model", SecretStr("no-new-legacy"), consent=True)


def test_migration_reencrypts_under_new_aad_without_plaintext_export(vault):
    expected = legacy(vault)
    old_ciphertext = vault.db.execute("SELECT ciphertext FROM credentials").fetchone()[0]
    with pytest.raises(CredentialError, match="consent_required"):
        vault.migrate_legacy_openai("alice", consent=False)
    result = vault.migrate_legacy_openai("alice", consent=True)
    assert result.provider == "openai" and vault.read("alice", "openai") == expected
    assert (
        vault.db.execute("SELECT count(*) FROM credentials WHERE provider='model'").fetchone()[0]
        == 0
    )
    assert old_ciphertext != vault.db.execute("SELECT ciphertext FROM credentials").fetchone()[0]
    assert expected.get_secret_value() not in repr(result)


def test_migration_conflict_and_transaction_failure_preserve_legacy(vault):
    expected = legacy(vault)
    replacement = SecretStr("synthetic-new-provider-1133")
    vault.put("alice", "openai", replacement, consent=True)
    with pytest.raises(CredentialError):
        vault.migrate_legacy_openai("alice", consent=True)
    assert vault.read("alice", "model") == expected
    assert vault.read("alice", "openai") == replacement
    vault.delete("alice", "openai")
    vault.db.execute(
        "CREATE TRIGGER refuse_new BEFORE INSERT ON credentials WHEN NEW.provider='openai' BEGIN SELECT RAISE(ABORT,'fixture'); END"
    )
    with pytest.raises(CredentialError):
        vault.migrate_legacy_openai("alice", consent=True)
    assert vault.read("alice", "model") == expected


def test_session_api_requires_provider_and_migration_rejects_nonofficial_host(tmp_path):
    app = create_app(
        tmp_path,
        "test-service-token",
        execute=lambda *args: None,
        cipher=KeyCipher({"v1": b"a" * 32}, "v1"),
        provider_hostname="untrusted.example",
    )
    headers = {"authorization": "Bearer test-service-token", "x-agent-user": "alice"}
    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/credential-sessions", headers=headers, json={"model_key": "fake"}
            ).status_code
            == 422
        )
        legacy(app.state.credential_sessions.vault)
        assert (
            client.post(
                "/v1/credentials/migrate-model",
                headers=headers,
                json={"provider": "openai", "consent": True},
            ).status_code
            == 409
        )
        assert client.get("/v1/credentials", headers=headers).json()["legacy_available"] is True
