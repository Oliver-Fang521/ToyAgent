import sqlite3

import pytest
from pydantic import SecretStr

from gaia_agent.interactive.credential_crypto import KeyCipher
from gaia_agent.interactive.credential_vault import CredentialError, CredentialVault
from gaia_agent.interactive.store import ConversationStore


@pytest.fixture
def vault(tmp_path):
    store = ConversationStore(tmp_path / "vault.sqlite")
    result = CredentialVault(store.db, KeyCipher({"old": b"a" * 32}, "old"), store.lock)
    yield result
    store.db.close()


def test_consent_owner_atomic_replace_metadata_and_delete(vault):
    secret = SecretStr("synthetic-vault-private-key-892134")
    with pytest.raises(CredentialError, match="consent_required"):
        vault.put("alice", "openai", secret, consent=False)
    assert not vault.status("alice")[0].configured
    status = vault.put("alice", "openai", secret, consent=True)
    assert status.last_four == "2134" and status.consent_at
    assert vault.read("alice", "openai") == secret
    with pytest.raises(CredentialError, match="credential_required"):
        vault.read("bob", "openai")
    assert secret.get_secret_value() not in repr(status)
    vault.put("alice", "openai", SecretStr("replacement-private-key-7878"), consent=True)
    assert vault.read("alice", "openai").get_secret_value() == "replacement-private-key-7878"
    vault.delete("alice", "openai")
    vault.delete("alice", "openai")
    assert not vault.status("alice")[0].configured


def test_rotation_restart_and_no_plaintext_in_database_or_wal(vault, tmp_path):
    secret = SecretStr("synthetic-database-private-key-572193")
    vault.put("alice", "openai", secret, consent=True)
    vault.cipher = KeyCipher({"old": b"a" * 32, "new": b"b" * 32}, "new")
    assert vault.read("alice", "openai") == secret
    vault.rotate_to_active("alice", "openai")
    vault.cipher = KeyCipher({"new": b"b" * 32}, "new")
    assert vault.read("alice", "openai") == secret
    for path in tmp_path.iterdir():
        assert secret.get_secret_value().encode() not in path.read_bytes()
    connection = sqlite3.connect(tmp_path / "vault.sqlite")
    import threading

    reopened = CredentialVault(connection, vault.cipher, threading.RLock())
    assert reopened.read("alice", "openai") == secret
    connection.close()


def test_temporary_session_owner_expiry_restart_and_revocation(vault):
    from gaia_agent.interactive.credential_sessions import CredentialSessions
    from gaia_agent.interactive.credential_vault import CredentialMaterial

    now = [10.0]
    sessions = CredentialSessions(vault, lambda: now[0])
    material = CredentialMaterial("openai", SecretStr("synthetic-temporary-private-key-9981"))
    handle = sessions.issue("alice", material)
    ref = sessions.reference("alice", "openai", handle)
    assert material.model.get_secret_value() not in repr(material) + repr(ref)
    with pytest.raises(CredentialError):
        sessions.reference("bob", "openai", handle)
    now[0] = 1809
    assert sessions.resolve(ref).model == material.model
    now[0] = 1810
    with pytest.raises(CredentialError, match="credential_expired"):
        sessions.resolve(ref)
    new_handle = sessions.issue("alice", material)
    queued = sessions.reference("alice", "openai", new_handle)
    sessions.invalidate("alice")
    with pytest.raises(CredentialError, match="credential_revoked"):
        sessions.resolve(queued)
    with pytest.raises(CredentialError):
        CredentialSessions(vault, lambda: now[0]).reference("alice", "openai", new_handle)
    vault.put("alice", "openai", material.model, consent=True)
    saved_ref = sessions.reference("alice", "openai", None)
    assert sessions.resolve(saved_ref).model == material.model
    sessions.issue("alice", material)
    with pytest.raises(CredentialError, match="credential_revoked"):
        sessions.resolve(saved_ref)
    sessions.close()


def test_temporary_session_cap_and_expired_reclamation(vault):
    from gaia_agent.interactive.credential_sessions import CredentialSessions
    from gaia_agent.interactive.credential_vault import CredentialMaterial

    now = [0.0]
    sessions = CredentialSessions(vault, lambda: now[0])
    material = CredentialMaterial("openai", SecretStr("synthetic-cap-key-2239"))
    for i in range(100):
        sessions.issue(f"user-{i}", material)
    sessions.issue("user-0", material)
    with pytest.raises(CredentialError, match="credential_sessions_busy"):
        sessions.issue("overflow", material)
    now[0] = 1800
    handle = sessions.issue("overflow", material)
    assert (
        sessions.resolve(sessions.reference("overflow", "openai", handle)).model == material.model
    )
