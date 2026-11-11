from uuid import UUID

from fastapi import Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from gaia_agent.interactive.catalog import ProviderId

from .models import MemoryCandidate, MemoryCategory, MemoryFailure
from .policy import FACETS


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, allow_inf_nan=False)


class SettingBody(Body):
    enabled: StrictBool
    revision: int = Field(ge=0)


class CreateBody(Body):
    category: MemoryCategory
    facet: str = Field(min_length=1, max_length=64)
    entity: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=500)
    allowed_providers: tuple[ProviderId, ...] = Field(min_length=1, max_length=2)
    revision: int | None = Field(default=None, ge=0)


class RevisionBody(Body):
    revision: int = Field(ge=0)


class DeleteBody(RevisionBody):
    permanent: StrictBool = False


class RetryBody(Body):
    confirm_cost: StrictBool


class UpdateBody(RevisionBody):
    content: str | None = Field(default=None, min_length=1, max_length=500)
    allowed_providers: tuple[ProviderId, ...] | None = Field(
        default=None, min_length=1, max_length=2
    )
    expires_at: float | None = None


def mount_memory_routes(app, *, owner_dependency, memory_service, cancel_owner):
    def service():
        if memory_service is None:
            raise MemoryFailure("memory_unavailable")
        return memory_service

    def response(value):
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        return JSONResponse(value, headers={"cache-control": "no-store"})

    @app.exception_handler(MemoryFailure)
    async def memory_error(request, error):
        return JSONResponse(
            {"error": error.code},
            status_code=error.status_code,
            headers={"cache-control": "no-store"},
        )

    @app.get("/v1/memory/settings")
    async def settings(user=owner_dependency):
        repository = service().repository
        return response(
            repository.settings(user).model_dump(mode="json")
            | {"suppressed_facets": repository.suppressed_facets(user, FACETS)}
        )

    @app.post("/v1/memory/settings")
    async def set_settings(body: SettingBody, user=owner_dependency):
        s = service()
        return response(
            await s.mutate(
                user,
                lambda: s.repository.set_enabled(user, body.enabled, body.revision),
                cancel_owner,
            )
        )

    @app.get("/v1/memories/snapshot")
    async def snapshot(user=owner_dependency):
        from .barriers import MemoryBarriers
        from .summaries import SummaryStore

        s = service()
        repo = s.repository
        with repo.lock:
            data = repo.list_snapshot(user).model_dump(mode="json")
            overviews = {}
            for provider in ("openai", "deepseek"):
                state = repo.state_snapshot(user, provider)
                overview = SummaryStore(repo).overview(state.scope) if state.enabled else None
                overviews[provider] = overview.content if overview is not None else None
            data["overviews"] = overviews
            rows = repo.db.execute(
                "SELECT state,count(*) n FROM memory_jobs WHERE owner=? GROUP BY state", (user,)
            ).fetchall()
            counts = {row["state"]: row["n"] for row in rows}
            usage = repo.db.execute(
                "SELECT coalesce(sum(input_tokens),0),coalesce(sum(output_tokens),0) FROM memory_jobs WHERE owner=?",
                (user,),
            ).fetchone()
            jobs = [
                dict(row)
                for row in repo.db.execute(
                    """SELECT id,state,provider,retry_count,failure_code FROM memory_jobs WHERE owner=?
                AND state IN ('pending','leased','provider_started','proposed','failed','uncertain','blocked','needs_clarification')
                ORDER BY updated_at DESC,id DESC LIMIT 20""",
                    (user,),
                )
            ]
            barriers = {}
            for provider in ("openai", "deepseek"):
                for barrier in MemoryBarriers(repo).visible(user, provider):
                    barriers[barrier["id"]] = {
                        key: barrier[key]
                        for key in ("id", "revision", "topic", "entity", "category")
                    }
            data.update(
                maintenance={
                    "pending": sum(
                        counts.get(key, 0)
                        for key in ("pending", "leased", "provider_started", "proposed")
                    ),
                    "failed": counts.get("failed", 0),
                    "uncertain": counts.get("uncertain", 0),
                    "jobs": jobs,
                    "input_tokens": usage[0],
                    "output_tokens": usage[1],
                },
                barriers=list(barriers.values()),
            )
            page = repo.page_records(user)
            data.update(total=page.total, next_cursor=page.next_cursor)
            extra = summary_maintenance(user)
            data["maintenance"]["jobs"].extend(extra["jobs"])
            for field in ("pending", "failed", "uncertain", "input_tokens", "output_tokens"):
                data["maintenance"][field] += extra[field]
            return response(data)

    def summary_maintenance(user):
        from .maintenance_queue import MaintenanceQueue

        repo = service().repository
        jobs = [job | {"kind": "summary"} for job in MaintenanceQueue(repo).status(user)]
        with repo.lock:
            counts = dict(repo.db.execute("SELECT state,COUNT(*) FROM memory_maintenance WHERE owner=? GROUP BY state", (user,)).fetchall())
            usage = repo.db.execute("SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0) FROM memory_maintenance_dispatch WHERE owner=?", (user,)).fetchone()
        return {"jobs": jobs, "pending": sum(counts.get(s, 0) for s in ("pending", "leased", "dispatched")),
            "failed": counts.get("failed", 0), "uncertain": counts.get("uncertain", 0),
            "input_tokens": usage[0], "output_tokens": usage[1]}

    @app.get("/v1/memory/maintenance")
    @app.get("/v1/memories/maintenance")
    async def maintenance_status(user=owner_dependency):
        return response(summary_maintenance(user))

    @app.post("/v1/memory/maintenance/{identifier}/retry")
    @app.post("/v1/memories/maintenance/{identifier}/retry")
    async def retry_summary(identifier: UUID, body: RetryBody, user=owner_dependency):
        from .maintenance_queue import MaintenanceQueue

        MaintenanceQueue(service().repository).retry(user, str(identifier), body.confirm_cost)
        return response({"queued": True})

    @app.get("/v1/memory/search")
    @app.get("/v1/memories/search")
    async def search_entries(q: str = Query("", max_length=512), cursor: str | None = Query(None, max_length=4096),
                             limit: int = Query(50, ge=1, le=50), user=owner_dependency):
        from .index import MemoryIndex
        from .layer_models import MemoryScope

        repo = service().repository
        with repo.lock:
            scope = MemoryScope(user, "management", repo.settings(user).generation)
            page = MemoryIndex(repo).search(scope, q, cursor, limit)
            return response({"items": [repo._view(repo._row(user, item.id)).model_dump(mode="json") for item in page.items],
                "total": page.total, "next_cursor": page.next_cursor, "coverage": page.coverage})

    @app.post("/v1/memories/jobs/{identifier}/retry")
    async def retry(identifier: UUID, body: RetryBody, user=owner_dependency):
        worker = getattr(app.state, "memory_worker", None)
        if body.confirm_cost is not True:
            raise MemoryFailure("memory_invalid", 422)
        if worker is None:
            raise MemoryFailure("memory_unavailable")
        event = worker.queue.store.get(user, str(identifier))
        worker._material(event)
        worker.queue.retry(user, str(identifier))
        worker.wake()
        return response({"queued": True})

    @app.post("/v1/memories/barriers/{identifier}/restore")
    async def restore(identifier: UUID, body: RevisionBody, user=owner_dependency):
        s = service()

        def apply():
            repo = s.repository
            with repo.lock, repo.db:
                row = repo.db.execute(
                    "SELECT revision FROM memory_barriers WHERE owner=? AND id=?",
                    (user, str(identifier)),
                ).fetchone()
                if row is None:
                    raise MemoryFailure("memory_not_found", 404)
                repo._cas(row, body.revision)
                repo.db.execute(
                    "DELETE FROM memory_barriers WHERE owner=? AND id=?", (user, str(identifier))
                )
                repo._bump(user)

        await s.mutate(user, apply, cancel_owner)
        return response({"restored": True})

    @app.get("/v1/memories")
    async def listing(
        offset: int = Query(0, ge=0, le=1000000000),
        limit: int = Query(50, ge=1, le=50),
        user=owner_dependency,
    ):
        items = service().repository.list(user, offset, limit)
        return response(
            {
                "items": [item.model_dump(mode="json") for item in items],
                "offset": offset,
                "has_more": len(items) == limit,
            }
        )

    @app.post("/v1/memories")
    async def create(body: CreateBody, user=owner_dependency):
        s = service()
        candidate = MemoryCandidate(
            category=body.category,
            facet=body.facet,
            entity=body.entity,
            content=body.content,
            source_start=0,
            source_end=len(body.content),
            explicit_relearn=False,
        )
        write = s.policy.validate_manual(candidate)
        return response(
            await s.mutate(
                user,
                lambda: s.repository.save_manual(
                    user, write, body.allowed_providers, body.revision
                ),
                cancel_owner,
            )
        )

    @app.post("/v1/memories/{identifier}/update")
    async def update(identifier: UUID, body: UpdateBody, user=owner_dependency):
        s = service()
        patch = body.model_dump(exclude_unset=True, exclude={"revision"})
        if "content" in patch:
            if patch["content"] is None:
                raise MemoryFailure("memory_invalid", 422)
            s.policy.validate_manual(
                MemoryCandidate(
                    category="profile",
                    facet="response_style",
                    entity="self",
                    content=patch["content"],
                    source_start=0,
                    source_end=0,
                    explicit_relearn=False,
                )
            )
        if "allowed_providers" in patch and patch["allowed_providers"] is None:
            raise MemoryFailure("memory_invalid", 422)
        return response(
            await s.mutate(
                user,
                lambda: s.repository.update(user, str(identifier), patch, body.revision),
                cancel_owner,
            )
        )

    @app.post("/v1/memories/{identifier}/delete")
    async def delete(identifier: UUID, body: DeleteBody, user=owner_dependency):
        s = service()
        await s.mutate(
            user,
            lambda: s.repository.delete(
                user, str(identifier), body.revision, permanent=body.permanent
            ),
            cancel_owner,
        )
        return response({"deleted": True})

    @app.post("/v1/memories/clear")
    async def clear(user=owner_dependency):
        s = service()
        await s.mutate(user, lambda: s.repository.clear(user), cancel_owner)
        return response({"cleared": True})
