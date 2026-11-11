import secrets
import time
from dataclasses import dataclass

from gaia_agent.interactive.credential_vault import (
    CredentialError,
    CredentialMaterial,
    CredentialRef,
    validate_secret,
)


@dataclass(repr=False)
class _Session:
    handle: str
    expires_at: float
    material: CredentialMaterial


class CredentialSessions:
    """Event-loop-owned ephemeral references; never serialize this object."""

    def __init__(self, vault, clock=time.time):
        self.vault = vault
        self.clock = clock
        self._sessions: dict[str, _Session] = {}
        self._generations: dict[str, int] = {}
        self.mutating: set[str] = set()
        self.on_invalidate = []
        self._background: dict[str, CredentialRef] = {}

    def issue(self, owner, material: CredentialMaterial, *, _mutation=False) -> str:
        if material.provider not in {"openai", "deepseek"}:
            raise CredentialError("invalid_credential", 422)
        if owner in self.mutating and not _mutation:
            raise CredentialError("credential_revoked", 409)
        for value in (material.model, material.serper, material.serpapi, material.deepseek_search):
            if value is not None:
                validate_secret(value)
        now = self.clock()
        self._sessions = {o: s for o, s in self._sessions.items() if s.expires_at > now}
        if owner not in self._sessions and len(self._sessions) >= 100:
            raise CredentialError("credential_sessions_busy", 503)
        self.invalidate(owner)
        handle = secrets.token_urlsafe(32)
        self._sessions[owner] = _Session(handle, now + 1800, material)
        return handle

    def reference(self, owner, provider, handle: str | None) -> CredentialRef:
        if provider not in {"openai", "deepseek"}:
            raise CredentialError("invalid_credential", 422)
        ref = CredentialRef(owner, provider, self._generations.get(owner, 0), handle)
        self.resolve(ref)
        return ref

    def resolve(self, ref: CredentialRef) -> CredentialMaterial:
        if ref.owner in self.mutating:
            raise CredentialError("credential_revoked", 409)
        if ref.generation != self._generations.get(ref.owner, 0):
            raise CredentialError("credential_revoked", 422)
        if ref.handle is not None:
            entry = self._sessions.get(ref.owner)
            if entry is None or not secrets.compare_digest(ref.handle, entry.handle):
                raise CredentialError("credential_required", 422)
            if self.clock() >= entry.expires_at:
                self._sessions.pop(ref.owner, None)
                raise CredentialError("credential_expired", 422)
            if entry.material.provider != ref.provider:
                raise CredentialError("credential_required", 422)
            return entry.material
        model = self.vault.read(ref.owner, ref.provider)
        optional = {}
        for provider in ("serper", "serpapi"):
            try:
                optional[provider] = self.vault.read(ref.owner, provider)
            except CredentialError as error:
                if error.code != "credential_required":
                    raise
        if ref.provider == "openai":
            try:
                optional["deepseek_search"] = self.vault.read(ref.owner, "deepseek")
            except CredentialError as error:
                if error.code != "credential_required":
                    raise
        return CredentialMaterial(ref.provider, model, **optional)

    def invalidate(self, owner) -> None:
        self._generations[owner] = self._generations.get(owner, 0) + 1
        self._sessions.pop(owner, None)
        self._background = {
            token: ref for token, ref in self._background.items() if ref.owner != owner
        }
        for callback in self.on_invalidate:
            callback(owner)

    def close(self) -> None:
        for owner in list(self._generations):
            self.invalidate(owner)
        self.on_invalidate.clear()
        self._background.clear()

    def background_reference(self, ref: CredentialRef) -> str:
        if ref.handle is None:
            raise CredentialError("credential_required", 422)
        self.resolve(ref)
        for token, stored in self._background.items():
            if stored == ref:
                return token
        # One temporary session per owner; bounded independently of queue length.
        if len(self._background) >= 100:
            raise CredentialError("credential_sessions_busy", 503)
        token = secrets.token_urlsafe(32)
        self._background[token] = ref
        return token

    def resolve_background(self, owner, provider, token) -> CredentialMaterial:
        ref = self._background.get(token)
        if ref is None or ref.owner != owner or ref.provider != provider or ref.handle is None:
            raise CredentialError("credential_required", 422)
        return self.resolve(ref)
