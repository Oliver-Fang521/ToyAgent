from dataclasses import asdict
from typing import Literal

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from gaia_agent.interactive.catalog import ProviderId, public_catalog
from gaia_agent.interactive.credential_vault import (
    CredentialError,
    CredentialMaterial,
    ProviderName,
    validate_secret,
)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class SaveCredential(_Body):
    provider: ProviderName
    key: SecretStr
    consent: Literal[True]

    @field_validator("consent", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise CredentialError("consent_required", 422)
        return value

    @field_validator("key")
    @classmethod
    def valid_key(cls, value):
        validate_secret(value)
        return value


class DeleteCredential(_Body):
    provider: Literal["openai", "deepseek", "serper", "serpapi", "model"]


class MigrateCredential(_Body):
    provider: Literal["openai"]
    consent: Literal[True]

    @field_validator("consent", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise CredentialError("consent_required", 422)
        return value


class TemporaryCredentials(_Body):
    provider: ProviderId
    model_key: SecretStr
    serper_key: SecretStr | None = None
    serpapi_key: SecretStr | None = None

    @field_validator("model_key", "serper_key", "serpapi_key")
    @classmethod
    def valid_key(cls, value):
        if value is not None:
            validate_secret(value)
        return value


def mount_credential_routes(
    app, *, owner_dependency, vault, sessions, cancel_owner, provider_hostname
):
    @app.exception_handler(CredentialError)
    async def credential_error(request, error):
        return JSONResponse({"error": error.code}, status_code=error.status_code)

    @app.get("/v1/credentials")
    async def status(user=owner_dependency):
        statuses = [asdict(s) for s in vault.status(user)]
        return {
            "provider_hostname": provider_hostname,
            "providers": public_catalog()["providers"],
            "statuses": statuses,
            "legacy_available": any(s["provider"] == "model" and s["configured"] for s in statuses),
            "legacy_migration_allowed": provider_hostname == "api.openai.com",
        }

    async def mutate(user, action):
        if user in sessions.mutating:
            raise CredentialError("credential_revoked", 409)
        sessions.mutating.add(user)
        try:
            sessions.invalidate(user)
            result = action()
            await cancel_owner(user)
            return result
        finally:
            sessions.mutating.discard(user)

    @app.post("/v1/credentials")
    async def save(body: SaveCredential, user=owner_dependency):
        return await mutate(
            user, lambda: asdict(vault.put(user, body.provider, body.key, consent=body.consent))
        )

    @app.post("/v1/credentials/delete")
    async def delete(body: DeleteCredential, user=owner_dependency):
        def action():
            vault.delete(user, body.provider)
            return {"deleted": True}

        return await mutate(user, action)

    @app.post("/v1/credentials/migrate-model")
    async def migrate(body: MigrateCredential, user=owner_dependency):
        if provider_hostname != "api.openai.com":
            raise CredentialError("legacy_provider_unknown", 409)
        return await mutate(
            user, lambda: asdict(vault.migrate_legacy_openai(user, consent=body.consent))
        )

    @app.post("/v1/credential-sessions")
    async def temporary(body: TemporaryCredentials, user=owner_dependency):
        return await mutate(
            user,
            lambda: {
                "handle": sessions.issue(
                    user,
                    CredentialMaterial(
                        body.provider, body.model_key, body.serper_key, body.serpapi_key
                    ),
                    _mutation=True,
                ),
                "expires_in": 1800,
            },
        )
