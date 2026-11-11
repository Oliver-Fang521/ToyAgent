"""Read-only account-memory tools. Model arguments never select authorization."""

import json
from dataclasses import asdict
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .index import MemoryIndex
from .layer_models import MemoryReadResult, SearchPage, SummaryView
from .models import MemoryFailure
from .summaries import SummaryStore


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    query: str = Field(max_length=512)
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=10, ge=1, le=20, strict=True)


class ReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["topic", "fact", "source"]
    ids: list[str] = Field(min_length=1, max_length=8)
    cursor: str | None = Field(default=None, max_length=4096)


class MemoryToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: Literal["search_memory", "read_memory"]
    arguments: dict

    @model_validator(mode="after")
    def valid_arguments(self):
        (SearchArguments if self.name == "search_memory" else ReadArguments).model_validate(self.arguments)
        return self


def result_json(result):
    return json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":"))


class MemoryTools:
    def __init__(self, service, scope, *, mode="normal"):
        self.repo = getattr(service, "repository", service)
        self.scope, self.mode = scope, mode

    def ensure_open(self):
        state = self.repo.state_snapshot(self.scope.owner, self.scope.provider)
        if self.mode != "normal" or not state.enabled or state.scope != self.scope:
            raise MemoryFailure("memory_conflict", 409)

    @staticmethod
    def _fact(record, *, source=False):
        if isinstance(record, SummaryView):
            return {"kind": "topic", "id": record.id, "revision": record.revision,
                    "content": record.content.encode()[:600].decode("utf-8", errors="ignore"),
                    "source_refs": [], "coverage": "snippet_read_topic_for_evidence"}
        return {"kind": "source" if source else "fact", "id": record.id,
                "revision": record.revision, "topic": record.topic, "entity": record.entity,
                "content": record.content, "source_run_id": record.source_run_id,
                "source_refs": [{"id": record.id, "revision": record.revision}]}

    def execute(self, call):
        with self.repo.lock:
            self.ensure_open()
            return self._search(SearchArguments.model_validate(call.arguments)) if call.name == "search_memory" else self._read(ReadArguments.model_validate(call.arguments))

    def _search(self, arguments):
        index = MemoryIndex(self.repo)
        items, cursor, total, coverage = [], arguments.cursor, 0, "complete"
        # One candidate at a time means a byte cap never skips unreturned matches.
        for _ in range(arguments.limit):
            page = index.search(self.scope, arguments.query, cursor, limit=1, include_summaries=True)
            total, coverage = page.total, page.coverage
            proposed = [*items, *(self._fact(r) for r in page.items)]
            trial = SearchPage(tuple(proposed), page.next_cursor, total, coverage)
            if len(result_json(trial).encode()) > 4000:
                return SearchPage(tuple(items), cursor, total, "truncated")
            items, cursor = proposed, page.next_cursor
            if not cursor or not page.items:
                break
        return SearchPage(tuple(items), cursor, total,
                          "index_pending" if coverage == "index_pending" else ("truncated" if cursor else coverage))

    def _read(self, arguments):
        index = MemoryIndex(self.repo)
        key = (*index._stamp(self.scope), "read", arguments.kind, *arguments.ids)
        start = index.decode(self.scope.owner, arguments.cursor, key) if arguments.cursor else 0
        documents = []
        for identifier in arguments.ids:
            try:
                UUID(identifier)
            except ValueError:
                raise MemoryFailure("memory_invalid", 422) from None
            if arguments.kind == "topic":
                summary = SummaryStore(self.repo).read(self.scope, identifier)
                item = {"kind": "topic", "id": summary.id, "revision": summary.revision,
                        "content": summary.content, "source_refs": [asdict(r) for r in summary.source_refs],
                        "child_refs": [asdict(r) for r in summary.child_refs]}
                raw, chunks = summary.content.encode(), []
                while raw:
                    text = raw[:1000].decode("utf-8", errors="ignore")
                    chunks.append(text)
                    raw = raw[len(text.encode()):]
                count = max(len(chunks), (len(item["source_refs"]) + 3) // 4, (len(item["child_refs"]) + 3) // 4)
                for part in range(count):
                    documents.append(item | {"content": chunks[part] if part < len(chunks) else "",
                        "source_refs": item["source_refs"][part * 4:part * 4 + 4],
                        "child_refs": item["child_refs"][part * 4:part * 4 + 4],
                        "part": part, "parts": count})
                continue
            else:
                record = self.repo.get(self.scope.owner, identifier)
                if self.scope.provider not in record.allowed_providers or (record.expires_at is not None and record.expires_at <= self.repo.clock()):
                    raise MemoryFailure("memory_not_found", 404)
                item = self._fact(record, source=arguments.kind == "source")
                if arguments.kind == "source":
                    payload = self.repo._payload(self.repo._row(self.scope.owner, identifier))
                    item["content"] = payload.get("source_fragment") or ""
                    item["coverage"] = "complete" if item["content"] else "source_unavailable"
            documents.append(item)
        items = []
        for position in range(start, len(documents)):
            item = documents[position]
            more = position + 1 < len(documents)
            cursor = index.cursor(self.scope.owner, {"key": key, "offset": position + 1}) if more else None
            trial = MemoryReadResult((*items, item), "truncated" if more else "complete", cursor)
            if len(result_json(trial).encode()) > 4000:
                cursor = index.cursor(self.scope.owner, {"key": key, "offset": position})
                return MemoryReadResult(tuple(items), "truncated", cursor)
            items.append(item)
        return MemoryReadResult(tuple(items), "complete")

    def validate_refs(self, refs):
        with self.repo.lock:
            self.ensure_open()
            for ref in refs:
                if ref.kind == "topic":
                    summary = SummaryStore(self.repo).read(self.scope, ref.id)
                    if summary.revision != ref.revision:
                        raise MemoryFailure("memory_conflict", 409)
                    continue
                record = self.repo.get(self.scope.owner, ref.id)
                if record.revision != ref.revision or self.scope.provider not in record.allowed_providers or (record.expires_at is not None and record.expires_at <= self.repo.clock()):
                    raise MemoryFailure("memory_conflict", 409)
        return tuple(refs)
