from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from gaia_agent.config import RunConfig
from gaia_agent.interactive.catalog import ModelSelection, ProviderId, resolve_profile
from gaia_agent.interactive.memory.models import MemoryMode, MemoryReport
from gaia_agent.models import Evidence, Usage


class InteractiveConfig(RunConfig):
    answer_style: Literal["general"] = "general"
    quality_policy: Literal["strict"] = "strict"
    stage_context_max_bytes: int = 98_304
    private_notebook_max_bytes: int = 262_144
    hf_token: SecretStr = SecretStr("")
    model: str = "gpt-6.1-terra"
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = "medium"
    timeout_seconds: float = 500
    planner_timeout_seconds: float = 30
    specialist_timeout_seconds: float = 410
    critic_timeout_seconds: float = 60
    provider_max_attempts: int = 3
    output_dir: Path = Path("interactive-data")


class InteractiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    question: str = Field(default="", max_length=12_000)
    provider: ProviderId
    model: str = Field(max_length=128)
    attachment_id: str | None = Field(default=None, max_length=64)
    memory_mode: MemoryMode = "normal"
    reasoning_effort: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def registered_selection(self):
        resolve_selection(self)
        return self


class ResolvedSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: ProviderId
    model: str
    reasoning_effort: str | None
    capability_version: str


def resolve_selection(request: InteractiveRequest) -> ResolvedSelection:
    profile = resolve_profile(ModelSelection(provider=request.provider, model=request.model))
    effort = (
        request.reasoning_effort
        if request.reasoning_effort is not None
        else profile.default_reasoning_effort
    )
    if effort is not None and effort not in profile.allowed_reasoning_efforts:
        raise ValueError("reasoning_unavailable")
    return ResolvedSelection(
        provider=profile.provider,
        model=profile.model,
        reasoning_effort=effort,
        capability_version=profile.capability_version,
    )


class InteractiveResult(BaseModel):
    answer: str
    evidence: list[Evidence]
    usage: Usage
    memory: MemoryReport | None = None
    tool_usage: dict[str, int] = Field(
        default_factory=lambda: {"search_requests": 0, "fetch_requests": 0}
    )
