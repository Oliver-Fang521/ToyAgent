"""Credential-generation-scoped discovery; no raw account metadata leaves this module."""

import asyncio
import json
import time

import httpx

from gaia_agent.interactive.catalog import ENDPOINTS, ProviderId, public_catalog
from gaia_agent.interactive.credential_vault import CredentialError


class ModelDiscovery:
    def __init__(self, sessions, client, *, clock=time.monotonic):
        self.sessions, self.client, self.clock = sessions, client, clock
        self._cache = {}
        self._locks = {}
        sessions.on_invalidate.append(self.invalidate)

    def invalidate(self, owner: str) -> None:
        self._cache = {key: value for key, value in self._cache.items() if key[0] != owner}

    async def discover(self, owner: str, provider: ProviderId, handle: str | None = None) -> dict:
        if provider not in ENDPOINTS:
            raise CredentialError("invalid_credential", 422)
        ref = self.sessions.reference(owner, provider, handle)
        # Saved vault material and a temporary handle can coexist at one owner generation.
        # A source tag separates their visibility without caching the key or handle.
        key = (owner, provider, ref.generation, "temporary" if ref.handle is not None else "saved")
        if len(self._locks) >= 200 and key not in self._locks:
            self._locks = {k: lock for k, lock in self._locks.items() if lock.locked()}
            if len(self._locks) >= 200:
                raise CredentialError("credential_sessions_busy", 503)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            material = self.sessions.resolve(ref)
            now = self.clock()
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return cached[1]
            models = [
                dict(m, availability="unknown")
                for m in public_catalog()["models"]
                if m["provider"] == provider
            ]
            result = {
                "models": models,
                "discovery": {"status": "unavailable", "checked_at": None, "expires_at": None},
            }
            try:
                async with self.client.stream(
                    "GET",
                    ENDPOINTS[provider].rstrip("/")
                    + ("/models" if provider == "openai" else "/v1/models"),
                    headers={"Authorization": "Bearer " + material.model.get_secret_value()},
                    timeout=15,
                    follow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 1_048_576:
                            raise ValueError("discovery_too_large")
                payload = json.loads(body)
                data = payload["data"]
                if not isinstance(data, list) or not all(
                    isinstance(m, dict) and isinstance(m.get("id"), str) for m in data
                ):
                    raise ValueError("invalid_discovery")
                visible = {m["id"] for m in data}
                for model in models:
                    model["availability"] = (
                        "visible" if model["model"] in visible else "unavailable"
                    )
                result["discovery"] = {
                    "status": "fresh",
                    "checked_at": time.time(),
                    "expires_at": time.time() + 900,
                }
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                pass  # Do not expose upstream errors, organizations or raw responses.
            self.sessions.resolve(ref)  # Rotation while awaiting invalidates both result and cache.
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            if result["discovery"]["status"] == "fresh":
                if len(self._cache) >= 200:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[key] = (self.clock() + 900, result)
            return result
