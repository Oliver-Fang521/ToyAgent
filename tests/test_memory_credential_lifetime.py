import sqlite3
from threading import RLock

import pytest
from pydantic import SecretStr

from gaia_agent.interactive.credential_crypto import KeyCipher
from gaia_agent.interactive.credential_sessions import CredentialSessions
from gaia_agent.interactive.credential_vault import (
    CredentialError,
    CredentialMaterial,
    CredentialVault,
)


def test_saved_epochs_survive_restart_but_change_on_replacement_and_delete(tmp_path):
    path = tmp_path / "vault.sqlite"
    db = sqlite3.connect(path)
    cipher = KeyCipher({"v1": b"a" * 32}, "v1")
    vault = CredentialVault(db, cipher, RLock())
    assert vault.background_epoch("alice", "openai") == 0
    vault.put("alice", "openai", SecretStr("synthetic-saved-one"), consent=True)
    assert vault.background_epoch("alice", "openai") == 1
    CredentialSessions(vault).close()
    db.close()
    db = sqlite3.connect(path)
    reopened = CredentialVault(db, cipher, RLock())
    assert reopened.background_epoch("alice", "openai") == 1
    reopened.put("alice", "openai", SecretStr("synthetic-saved-two"), consent=True)
    assert reopened.background_epoch("alice", "openai") == 2
    reopened.delete("alice", "openai")
    assert reopened.background_epoch("alice", "openai") == 3
    assert reopened.background_epoch("bob", "openai") == 0
    db.close()


def test_expired_temporary_background_reference_never_uses_saved_key(tmp_path):
    db = sqlite3.connect(tmp_path / "vault.sqlite")
    vault = CredentialVault(db, KeyCipher({"v1": b"a" * 32}, "v1"), RLock())
    vault.put("alice", "openai", SecretStr("synthetic-saved-key"), consent=True)
    now = [100.0]
    sessions = CredentialSessions(vault, lambda: now[0])
    handle = sessions.issue("alice", CredentialMaterial("openai", SecretStr("synthetic-temp-key")))
    ref = sessions.reference("alice", "openai", handle)
    token = sessions.background_reference(ref)
    assert token != handle
    assert sessions.resolve_background("alice", "openai", token).model == SecretStr(
        "synthetic-temp-key"
    )
    now[0] = 1900
    with pytest.raises(CredentialError):
        sessions.resolve_background("alice", "openai", token)
    with pytest.raises(CredentialError):
        CredentialSessions(vault).resolve_background("alice", "openai", token)
    db.close()
