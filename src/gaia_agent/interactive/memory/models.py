from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from gaia_agent.interactive.catalog import ProviderId

MemoryCategory = Literal["profile", "project", "decision"]
MemoryMode = Literal["normal", "temporary"]
MemoryStatus = Literal[
    "ready", "pending", "updated", "unchanged", "paused", "temporary", "unavailable", "skipped"
]
MemoryJobState = Literal[
    "pending",
    "leased",
    "provider_started",
    "proposed",
    "completed",
    "unchanged",
    "needs_clarification",
    "blocked",
    "failed",
    "uncertain",
]


class MemoryObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    id: str = Field(min_length=1, max_length=128)
    source_run_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=500, repr=False)
    verified: StrictBool = False


class MemoryEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    event_id: str = Field(min_length=1, max_length=128)
    owner: str = Field(min_length=1, max_length=256, repr=False)
    provider: ProviderId
    source_run_id: str = Field(min_length=1, max_length=128)
    source_seq: int = Field(ge=0, strict=True)
    memory_generation: int = Field(ge=0, strict=True)
    credential_epoch: int = Field(ge=0, strict=True)
    credential_source: Literal["saved", "temporary"]
    model: str = Field(min_length=1, max_length=128, repr=False)
    reasoning_effort: str | None = Field(default=None, max_length=32, repr=False)
    capability_version: str = Field(min_length=1, max_length=64, repr=False)
    source: str = Field(min_length=1, repr=False)
    observations: tuple[MemoryObservation, ...] = Field(default=(), max_length=5, repr=False)

    @model_validator(mode="after")
    def bounded_source(self):
        if len(self.source.encode("utf-8")) > 8000:
            raise ValueError("input_too_large")
        return self


class MemoryOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    action: Literal["add", "append", "replace", "delete", "forget", "restore", "noop", "clarify"]
    category: MemoryCategory
    topic: str = Field(min_length=1, max_length=64, repr=False)
    entity: str = Field(min_length=1, max_length=128, repr=False)
    cardinality: Literal["single", "set"]
    content: str | None = Field(default=None, min_length=1, max_length=500, repr=False)
    source_start: int = Field(ge=0, strict=True)
    source_end: int = Field(ge=0, strict=True)
    source_kind: Literal["user", "verified_task"]
    intent: Literal["automatic", "explicit", "clarify"]
    target_id: str | None = Field(default=None, max_length=128)
    expected_revision: int | None = Field(default=None, ge=0, strict=True)
    barrier_id: str | None = Field(default=None, max_length=128)
    observation_id: str | None = Field(default=None, max_length=128)
    subject: str | None = Field(default=None, min_length=1, max_length=64, repr=False)

    @model_validator(mode="after")
    def valid_shape(self):
        if self.source_end < self.source_start:
            raise ValueError("invalid_source_span")
        if self.action in {"add", "append", "replace"} and self.content is None:
            raise ValueError("missing_content")
        return self


class MemoryBatchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    state: MemoryJobState
    saved_ids: tuple[str, ...] = ()
    skipped_count: int = Field(default=0, ge=0)
    content_revision: int = Field(default=0, ge=0)


class MemoryFailure(Exception):
    CODES = frozenset(
        {
            "memory_integrity",
            "memory_unavailable",
            "memory_invalid",
            "memory_conflict",
            "memory_not_found",
            "memory_limit",
            "memory_busy",
        }
    )

    def __init__(self, code: str, status_code: int = 503):
        self.code = code if code in self.CODES else "memory_unavailable"
        self.status_code = status_code
        super().__init__(self.code)


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    category: MemoryCategory
    facet: str = Field(min_length=1, max_length=64, repr=False)
    entity: str = Field(min_length=1, max_length=128, repr=False)
    content: str = Field(min_length=1, max_length=500, repr=False)
    source_start: int = Field(ge=0)
    source_end: int = Field(ge=0)
    explicit_relearn: StrictBool


@dataclass(frozen=True, repr=False)
class MemoryWrite:
    candidate: MemoryCandidate
    normalized_key: str
    source_fragment: str | None


@dataclass(frozen=True, repr=False)
class MemoryRecord:
    id: str
    owner: str
    category: MemoryCategory
    canonical_key: str
    revision: int
    origin_provider: ProviderId
    allowed_providers: tuple[ProviderId, ...]
    status: str
    content: str
    source_run_id: str | None
    source_start: int | None
    source_end: int | None
    created_at: float
    updated_at: float
    last_confirmed_at: float
    expires_at: float | None
    edited_by_user: bool
    topic: str | None = None
    slot_id: str | None = None
    cardinality: Literal["single", "set"] = "single"
    origin_kind: Literal["automatic", "explicit", "manual"] = "automatic"
    subject: str | None = None
    entity: str = "self"


class MemoryView(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)
    id: str
    category: MemoryCategory
    facet: str = Field(repr=False)
    entity: str = Field(repr=False)
    content: str = Field(repr=False)
    revision: int
    origin_provider: ProviderId
    allowed_providers: tuple[ProviderId, ...]
    created_at: float
    updated_at: float
    last_confirmed_at: float
    expires_at: float | None
    edited_by_user: bool
    topic: str | None = None
    slot_id: str | None = None
    cardinality: Literal["single", "set"] = "single"
    origin_kind: Literal["automatic", "explicit", "manual"] = "automatic"


class MemorySettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    enabled: StrictBool = True
    revision: int = Field(ge=0)
    generation: int = Field(ge=0)
    capture_after: int = Field(ge=0)


class MemoryListSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    settings: MemorySettings
    items: tuple[MemoryView, ...]
    suppressed_facets: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True, repr=False)
class MemoryProjection:
    owner: str
    provider: ProviderId
    generation: int
    content_revision: int
    entries: tuple[tuple[str, int, str], ...]
    base_ids: tuple[str, ...]

    @property
    def record_ids(self):
        return tuple(entry[0] for entry in self.entries)


@dataclass(frozen=True, repr=False)
class MemorySnapshot:
    owner: str
    provider: ProviderId
    generation: int
    mode: MemoryMode
    records: tuple[MemoryRecord, ...]
    content_revision: int = 0
    projection: MemoryProjection | None = None
    enabled: bool = True


class MemoryReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: MemoryStatus
    used_ids: tuple[str, ...] = ()
    saved_ids: tuple[str, ...] = ()
    skipped_count: int = Field(default=0, ge=0)
