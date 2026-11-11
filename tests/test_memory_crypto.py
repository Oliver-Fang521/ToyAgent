from dataclasses import replace

import pytest

from gaia_agent.interactive.memory.crypto import MemoryCrypto
from gaia_agent.interactive.memory.models import MemoryFailure


@pytest.fixture
def crypto():
    return MemoryCrypto({"v1": b"a" * 32}, "v1")


def test_roundtrip_and_no_plaintext_repr(crypto):
    envelope = crypto.encrypt("alice", "one", {"content": "private Python profile"})
    assert crypto.decrypt("alice", "one", envelope) == {"content": "private Python profile"}
    assert "Python" not in repr(envelope)
    assert b"Python" not in envelope.ciphertext


@pytest.mark.parametrize("owner,record_id", [("bob", "one"), ("alice", "two")])
def test_owner_and_id_bind_aad(crypto, owner, record_id):
    envelope = crypto.encrypt("alice", "one", {"content": "secret"})
    with pytest.raises(MemoryFailure, match="memory_integrity"):
        crypto.decrypt(owner, record_id, envelope)


def test_tamper_and_version_fail_closed(crypto):
    envelope = crypto.encrypt("alice", "one", {"content": "secret"})
    for broken in [
        replace(envelope, ciphertext=b"x" + envelope.ciphertext[1:]),
        replace(envelope, version="missing"),
    ]:
        with pytest.raises(MemoryFailure, match="memory_integrity"):
            crypto.decrypt("alice", "one", broken)


def test_aes_rotation_preserves_pinned_owner_hash(crypto):
    rotated = MemoryCrypto({"v1": b"a" * 32, "v2": b"b" * 32}, "v2")
    original = crypto.canonical_hash("alice", "profile:language", "v1")
    assert original == rotated.canonical_hash("alice", "profile:language", "v1")
    assert original != rotated.canonical_hash("bob", "profile:language", "v1")
    assert rotated.encrypt("alice", "one", {}).version == "v2"
    with pytest.raises(MemoryFailure, match="memory_unavailable"):
        MemoryCrypto({"v2": b"b" * 32}, "v2").canonical_hash("alice", "x", "v1")


@pytest.mark.parametrize("kind", ["event", "proposal", "projection", "barrier"])
def test_object_ciphertext_is_domain_separated(crypto, kind):
    payload = {"source": "synthetic personal topic", "schema_version": 2}
    envelope = crypto.encrypt_object("alice", kind, "one", payload)
    assert crypto.decrypt_object("alice", kind, "one", envelope) == payload
    for owner, other_kind, identifier in [
        ("bob", kind, "one"),
        ("alice", kind, "two"),
        ("alice", "proposal" if kind != "proposal" else "event", "one"),
    ]:
        with pytest.raises(MemoryFailure, match="memory_integrity"):
            crypto.decrypt_object(owner, other_kind, identifier, envelope)
    with pytest.raises(MemoryFailure, match="memory_integrity"):
        crypto.decrypt("alice", "one", envelope)
