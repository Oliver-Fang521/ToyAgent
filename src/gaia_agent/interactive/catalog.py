"""Server-owned official endpoint and capability registry. No account secrets."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from gaia_agent.interactive.model_registry import MODEL_ROWS

ProviderId = Literal["openai", "deepseek"]
ENDPOINTS = {"openai": "https://api.openai.com/v1", "deepseek": "https://api.deepseek.com"}


class ModelSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: ProviderId
    model: str


class Capabilities(BaseModel):
    model_config = ConfigDict(frozen=True)
    text: bool = True
    image: bool = False
    structured_output: bool = True
    function_tools: bool = True
    hosted_search: bool = False
    hosted_python: bool = False
    audio: bool = False


class ModelProfile(ModelSelection):
    display_name: str
    endpoint: str
    capabilities: Capabilities
    allowed_reasoning_efforts: tuple[str, ...] = ("low",)
    default_reasoning_effort: str | None = "low"
    protocol: str = "responses"
    family: str = ""
    legacy: bool = False
    evidence_url: str = ""
    capability_version: str = "2026-10-19.1"
    adapter_status: Literal["supported", "unsupported"] = "supported"
    disabled_reason: str | None = None
    verification: Literal["documented", "contract_tested", "live_verified"] = "documented"
    checked_at: str = "2026-10-19"


PROFILES = tuple(
    ModelProfile(
        provider="openai",
        model=id,
        display_name=name,
        family=family,
        legacy=legacy,
        endpoint=ENDPOINTS["openai"],
        allowed_reasoning_efforts=efforts,
        default_reasoning_effort=default,
        evidence_url="https://developers.openai.com/api/docs/models/"
        + re.sub(r"-\d{4}-\d{2}-\d{2}$", "", id),
        capabilities=Capabilities(
            image=image, hosted_search=search, hosted_python=python, audio=True
        ),
        adapter_status="unsupported" if reason else "supported",
        disabled_reason=reason,
        checked_at="2026-11-05" if id == "gpt-6.1-terra" else "2026-10-19",
        capability_version="2026-11-05.1" if id == "gpt-6.1-terra" else "2026-10-19.1",
    )
    for id, name, family, legacy, efforts, default, image, search, python, reason in MODEL_ROWS
) + tuple(
    ModelProfile(
        provider="deepseek",
        model=id,
        display_name=name,
        family="DeepSeek V4",
        endpoint=ENDPOINTS["deepseek"],
        allowed_reasoning_efforts=("none", "low", "high", "max"),
        default_reasoning_effort="high",
        evidence_url="https://api-docs.deepseek.com/api/list-models/",
        capabilities=Capabilities(image=id == "deepseek-flash"),
    )
    for id, name in (
        ("deepseek-flash", "DeepSeek-V4.1-Flash"),
        ("deepseek-v4-pro", "DeepSeek-V4-Pro"),
    )
)


def resolve_profile(selection: ModelSelection) -> ModelProfile:
    for profile in PROFILES:
        if (profile.provider, profile.model) == (
            selection.provider,
            selection.model,
        ) and profile.adapter_status == "supported":
            return profile
    raise ValueError("model_unavailable")


def public_catalog() -> dict:
    return {
        "providers": [
            {"id": "openai", "name": "OpenAI", "hostname": "api.openai.com"},
            {"id": "deepseek", "name": "DeepSeek", "hostname": "api.deepseek.com"},
        ],
        "models": [p.model_dump(mode="json") for p in PROFILES],
    }
