import base64
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .models import MemoryFailure


@dataclass(frozen=True, repr=False)
class MemoryEnvelope:
    version: str
    nonce: bytes
    ciphertext: bytes


class MemoryCrypto:
    OBJECT_KINDS = frozenset({"event", "proposal", "projection", "barrier", "cursor", "summary", "maintenance"})

    def __init__(self, keys: Mapping[str, bytes], active_version: str):
        if (
            not isinstance(keys, Mapping)
            or active_version not in keys
            or not all(
                isinstance(v, str) and v and isinstance(k, bytes) and len(k) == 32
                for v, k in keys.items()
            )
        ):
            raise MemoryFailure("memory_unavailable")
        self._keys = dict(keys)
        self.active_version = active_version

    @staticmethod
    def _aad(owner, record_id, version):
        return json.dumps(
            ["toy-agent-memory-v1", owner, record_id, version],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()

    def encrypt(self, owner: str, record_id: str, payload: dict) -> MemoryEnvelope:
        version = self.active_version
        nonce = secrets.token_bytes(12)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(raw) > 16384:
            raise MemoryFailure("memory_invalid", 422)
        ciphertext = AESGCM(self._keys[version]).encrypt(
            nonce, raw, self._aad(owner, record_id, version)
        )
        return MemoryEnvelope(version, nonce, ciphertext)

    def decrypt(self, owner: str, record_id: str, envelope: MemoryEnvelope) -> dict:
        try:
            raw = AESGCM(self._keys[envelope.version]).decrypt(
                envelope.nonce, envelope.ciphertext, self._aad(owner, record_id, envelope.version)
            )
            value = json.loads(raw)
            if not isinstance(value, dict) or len(raw) > 16384:
                raise ValueError
            return value
        except (InvalidTag, ValueError, KeyError, TypeError, UnicodeDecodeError):
            raise MemoryFailure("memory_integrity", 422) from None

    def canonical_hash(self, owner: str, normalized_key: str, hash_version: str) -> str:
        try:
            key = self._keys[hash_version]
        except KeyError:
            raise MemoryFailure("memory_unavailable") from None
        data = json.dumps(
            ["toy-agent-memory-index-v1", owner, normalized_key],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        return hmac.new(key, data, hashlib.sha256).hexdigest()

    @classmethod
    def _object_aad(cls, owner, kind, identifier, version):
        if kind not in cls.OBJECT_KINDS:
            raise MemoryFailure("memory_invalid", 422)
        return json.dumps(
            ["toy-agent-memory-v2", kind, owner, identifier, version],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()

    def encrypt_object(self, owner, kind, identifier, payload) -> MemoryEnvelope:
        try:
            raw = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode()
            if not isinstance(payload, dict) or len(raw) > 32768:
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise MemoryFailure("memory_invalid", 422) from None
        version = self.active_version
        nonce = secrets.token_bytes(12)
        aad = self._object_aad(owner, kind, identifier, version)
        return MemoryEnvelope(version, nonce, AESGCM(self._keys[version]).encrypt(nonce, raw, aad))

    def decrypt_object(self, owner, kind, identifier, envelope) -> dict:
        aad = self._object_aad(owner, kind, identifier, envelope.version)
        try:
            raw = AESGCM(self._keys[envelope.version]).decrypt(
                envelope.nonce, envelope.ciphertext, aad
            )
            value = json.loads(raw)
            if not isinstance(value, dict) or len(raw) > 32768:
                raise ValueError
            return value
        except (InvalidTag, ValueError, KeyError, TypeError, UnicodeError):
            raise MemoryFailure("memory_integrity", 422) from None

    @classmethod
    def from_environment(cls):
        try:
            raw = json.loads(os.environ.get("AGENT_CREDENTIAL_KEYRING", ""))
            if not isinstance(raw, dict):
                raise TypeError
            keys = {v: base64.b64decode(k, validate=True) for v, k in raw.items()}
            return cls(keys, os.environ.get("AGENT_CREDENTIAL_ACTIVE_VERSION", ""))
        except (ValueError, TypeError):
            raise MemoryFailure("memory_unavailable") from None
