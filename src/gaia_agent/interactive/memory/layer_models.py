"""Scope and revision pointers, never model-supplied authorization."""

from dataclasses import dataclass, field

from gaia_agent.models import Usage


@dataclass(frozen=True, repr=False)
class MemoryScope:
    owner: str
    provider: str
    generation: int


@dataclass(frozen=True)
class FactRef:
    id: str
    revision: int
    kind: str = "fact"


@dataclass(frozen=True, repr=False)
class SearchPage:
    items: tuple = ()
    next_cursor: str | None = None
    total: int = 0
    coverage: str = "complete"


@dataclass(frozen=True, repr=False)
class MemoryState:
    scope: MemoryScope
    enabled: bool
    content_revision: int


@dataclass(frozen=True, repr=False)
class MemoryReadResult:
    items: tuple = ()
    coverage: str = "complete"
    next_cursor: str | None = None


@dataclass(frozen=True, repr=False)
class SummaryDraft:
    kind: str
    topic_id: str
    content: str
    source_refs: tuple[FactRef, ...] = ()
    child_refs: tuple[FactRef, ...] = ()


@dataclass(frozen=True, repr=False)
class SummaryView:
    id: str
    revision: int
    content: str
    source_refs: tuple[FactRef, ...]
    state: str = "ready"
    child_refs: tuple[FactRef, ...] = ()
    kind: str = "topic"
    topic_id: str = ""


@dataclass(frozen=True, repr=False)
class MaintenanceClaim:
    id: str
    scope: MemoryScope
    input_revision: int
    selected_model: dict
    credential_epoch: int
    cursor: dict
    lease_id: str
    topic_id: str


@dataclass(frozen=True, repr=False)
class MemoryEvidence:
    context_text: str = ""
    refs: tuple[FactRef, ...] = ()
    coverage: str = "complete"
    generation: int = 0
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True, repr=False)
class MemoryLoopResult:
    decision: object
    evidence: MemoryEvidence
    usage: Usage
    termination: str
