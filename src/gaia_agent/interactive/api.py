import asyncio
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import UploadFile

from gaia_agent.interactive.catalog import ProviderId, public_catalog
from gaia_agent.interactive.credential_api import mount_credential_routes
from gaia_agent.interactive.credential_crypto import cipher_from_environment
from gaia_agent.interactive.credential_runtime import (
    InteractiveRuntimeSettings,
    UserRuntime,
    memory_planner_client,
)
from gaia_agent.interactive.credential_sessions import CredentialSessions
from gaia_agent.interactive.credential_vault import CredentialError, CredentialVault
from gaia_agent.interactive.memory.api import mount_memory_routes
from gaia_agent.interactive.memory.consolidation import MemoryConsolidator
from gaia_agent.interactive.memory.crypto import MemoryCrypto
from gaia_agent.interactive.memory.event_store import MemoryEventStore
from gaia_agent.interactive.memory.models import MemoryFailure
from gaia_agent.interactive.memory.policy import MemoryPolicy
from gaia_agent.interactive.memory.queue import MemoryQueue
from gaia_agent.interactive.memory.repository import MemoryRepository
from gaia_agent.interactive.memory.retrieval import MemoryRetriever
from gaia_agent.interactive.memory.service import MemoryService
from gaia_agent.interactive.memory.worker import MemoryWorker
from gaia_agent.interactive.model_discovery import ModelDiscovery
from gaia_agent.interactive.models import InteractiveRequest
from gaia_agent.interactive.service import RunService
from gaia_agent.interactive.store import ConversationStore, ServiceError
from gaia_agent.interactive.streaming import run_event_updates

MAX_UPLOAD_BYTES = 32 * 1024 * 1024


class CreateConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: ProviderId


class RefreshCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    provider: ProviderId


ALLOWED_TYPES = {
    ".txt": {"text/plain"},
    ".csv": {"text/csv", "text/plain"},
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".webp": {"image/webp"},
    ".mp3": {"audio/mpeg"},
    ".wav": {"audio/wav", "audio/x-wav"},
    ".m4a": {"audio/mp4", "audio/x-m4a"},
}


class RequestBoundary:
    """Authenticate and bound actual bytes before multipart can spool to disk."""

    def __init__(self, app, service_token):
        self.app = app
        self.token = service_token
        self.active_uploads = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/v1/"):
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        auth = headers.get(b"authorization", b"")
        user = headers.get(b"x-agent-user", b"")
        catalog = scope["path"] == "/v1/model-catalog" and scope["method"] == "GET"
        if not hmac.compare_digest(auth, ("Bearer " + self.token).encode()) or (
            not catalog and (not user or len(user) > 128 or any(c < 33 or c > 127 for c in user))
        ):
            return await JSONResponse({"error": "unauthorized"}, status_code=401)(
                scope, receive, send
            )
        if scope["method"] != "POST":
            return await self.app(scope, receive, send)
        upload = scope["path"].endswith("/uploads")
        limit = MAX_UPLOAD_BYTES + 1024 * 1024 if upload else 64 * 1024
        if upload and self.active_uploads >= 2:
            return await JSONResponse({"error": "service_busy"}, status_code=503)(
                scope, receive, send
            )
        if upload:
            self.active_uploads += 1
        try:
            body = bytearray()
            while True:
                try:
                    message = await asyncio.wait_for(receive(), timeout=15)
                except TimeoutError:
                    return await JSONResponse({"error": "upload_timeout"}, status_code=408)(
                        scope, receive, send
                    )
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > limit:
                    return await JSONResponse({"error": "file_too_large"}, status_code=413)(
                        scope, receive, send
                    )
                body.extend(chunk)
                if not message.get("more_body", False):
                    break
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            await self.app(scope, bounded_receive, send)
        finally:
            if upload:
                self.active_uploads -= 1


def create_app(
    root: Path | None = None,
    service_token: str | None = None,
    *,
    execute=None,
    cipher=None,
    provider_hostname="",
    runtime_settings=None,
    memory_crypto=None,
    memory_planner_factory=None,
) -> FastAPI:
    root = root or Path(os.environ.get("AGENT_DATA_DIR", "interactive-data"))
    token = service_token or os.environ.get("AGENT_SERVICE_TOKEN", "")
    if len(token) < 16:
        raise RuntimeError("service_credential_missing")
    production = execute is None
    if execute is None:
        try:
            runtime_settings = runtime_settings or InteractiveRuntimeSettings(
                base_url=os.environ.get("AGENT_MODEL_BASE_URL", "")
            )
        except ValueError:
            raise CredentialError("credential_configuration", 503) from None
        cipher = cipher or cipher_from_environment()
    store = ConversationStore(root / "state.sqlite")
    if memory_crypto is None and production:
        try:
            memory_crypto = MemoryCrypto.from_environment()
        except MemoryFailure:
            memory_crypto = None
    memory_service = (
        MemoryService(
            MemoryRepository(store.db, store.lock, memory_crypto), MemoryPolicy(), MemoryRetriever()
        )
        if memory_crypto
        else None
    )
    sessions = CredentialSessions(CredentialVault(store.db, cipher, store.lock)) if cipher else None
    memory_worker = None
    if (
        memory_service is not None
        and sessions is not None
        and (production or memory_planner_factory)
    ):
        queue = MemoryQueue(MemoryEventStore(store.db, store.lock, memory_crypto))
        consolidator = MemoryConsolidator(memory_service.repository, queue)
        memory_worker = MemoryWorker(
            queue, memory_planner_factory or memory_planner_client, consolidator, sessions
        )
        consolidator.temporary_valid = lambda event: bool(memory_worker._material(event))
    summary_worker = None
    index_worker = None
    if memory_service is not None:
        from gaia_agent.interactive.memory.index import MemoryIndexBackfill

        index_worker = MemoryIndexBackfill(memory_service.repository)
    if production and memory_worker is not None:
        from gaia_agent.interactive.credential_runtime import create_summary_worker

        summary_worker = create_summary_worker(memory_worker, sessions, memory_service.repository)
    if production:
        runtime = UserRuntime(runtime_settings, sessions, memory_service)
        execute = runtime.execute
    elif sessions:
        fake_execute = execute

        async def execute(ref, *args, **kwargs):
            sessions.resolve(ref)
            return await fake_execute(*args, **kwargs)

    service = RunService(
        store,
        root / "runs",
        execute,
        credential_sessions=sessions,
        memory_service=memory_service,
        memory_worker=memory_worker,
        conversation_indexer=runtime.index_conversation if production else None,
    )
    active_upload_users: set[str] = set()

    @asynccontextmanager
    async def lifespan(app):
        discovery_client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
        app.state.model_discovery = ModelDiscovery(sessions, discovery_client) if sessions else None
        store.interrupt_unfinished()
        if memory_worker:
            memory_worker.start()
        if summary_worker:
            summary_worker.start()
        if index_worker:
            index_worker.start()
        # Remove stale upload records/files without touching any outside path.
        for row in store.db.execute(
            "SELECT id,path FROM uploads WHERE created<?", (time.time() - 7 * 86400,)
        ).fetchall():
            path = Path(row["path"])
            if path.resolve().is_relative_to((root / "uploads").resolve()) and path.is_file():
                path.unlink()
            with store.db:
                store.db.execute("DELETE FROM uploads WHERE id=?", (row["id"],))
        yield
        await service.shutdown()
        if summary_worker:
            await summary_worker.close()
        if index_worker:
            await index_worker.close()
        if memory_worker:
            await memory_worker.close()
        await discovery_client.aclose()
        if sessions:
            sessions.close()
        store.db.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    app.state.memory_service = memory_service
    app.state.memory_worker = memory_worker
    app.state.summary_worker = summary_worker
    app.add_middleware(RequestBoundary, service_token=token)

    async def owner(request: Request):
        authorization = request.headers.get("authorization", "")
        if not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "unauthorized")
        user = request.headers.get("x-agent-user", "")
        if not user or len(user) > 128 or not user.isascii() or any(ord(c) < 33 for c in user):
            raise HTTPException(401, "identity_missing")
        return user

    owner_dependency = Depends(owner)
    mount_memory_routes(
        app,
        owner_dependency=owner_dependency,
        memory_service=memory_service,
        cancel_owner=service.cancel_owner,
    )
    if sessions:
        app.state.credential_sessions = sessions

        async def cancel_owner(user):
            await service.cancel_owner(user)

        mount_credential_routes(
            app,
            owner_dependency=owner_dependency,
            vault=sessions.vault,
            sessions=sessions,
            cancel_owner=cancel_owner,
            provider_hostname=runtime_settings.provider_hostname
            if runtime_settings
            else provider_hostname,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({"error": "invalid_request"}, status_code=422)

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        return JSONResponse({"error": error.code}, status_code=error.status_code)

    @app.exception_handler(Exception)
    async def internal_error(request, error):
        return JSONResponse({"error": "internal_error"}, status_code=500)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/model-catalog")
    async def model_catalog():
        return public_catalog()

    @app.post("/v1/model-catalog/refresh")
    async def refresh_catalog(body: RefreshCatalog, request: Request, user=owner_dependency):
        discovery = app.state.model_discovery
        if discovery is None:
            return JSONResponse({"error": "credential_required"}, status_code=422)
        result = await discovery.discover(
            user, body.provider, request.headers.get("x-agent-credential-handle")
        )
        return JSONResponse(result, headers={"cache-control": "no-store"})

    @app.get("/v1/conversations")
    async def conversations(user=owner_dependency):
        return store.list(user)

    @app.post("/v1/conversations")
    async def create_conversation(body: CreateConversation, user=owner_dependency):
        return store.create(user, body.provider)

    @app.get("/v1/conversations/{cid}")
    async def conversation(cid: str, offset: int = 0, user=owner_dependency):
        return {
            **store.get(user, cid),
            **store.messages(user, cid, max(0, offset)),
            "latest_run": store.latest_run(user, cid),
        }

    @app.post("/v1/conversations/{cid}/uploads")
    async def upload(cid: str, request: Request, user=owner_dependency):
        store.get(user, cid)
        form = await request.form(max_files=2, max_fields=2)
        files = [value for _, value in form.multi_items() if isinstance(value, UploadFile)]
        if len(files) != 1 or len(form.getlist("file")) != 1 or form.get("file") is not files[0]:
            await form.close()
            raise HTTPException(422, "one_file_required")
        file = files[0]
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in ALLOWED_TYPES or file.content_type not in ALLOWED_TYPES[suffix]:
            await form.close()
            raise HTTPException(415, "unsupported_file")
        if user in active_upload_users:
            await form.close()
            raise HTTPException(429, "upload_in_progress")
        if (
            store.db.execute(
                "SELECT count(*) FROM uploads WHERE owner=? AND created>?",
                (user, time.time() - 3600),
            ).fetchone()[0]
            >= 3
        ):
            await form.close()
            raise HTTPException(429, "upload_limit")
        recent_bytes = sum(
            Path(row[0]).stat().st_size
            for row in store.db.execute(
                "SELECT path FROM uploads WHERE owner=? AND created>?", (user, time.time() - 3600)
            )
            if Path(row[0]).is_file()
        )
        if recent_bytes >= 3 * MAX_UPLOAD_BYTES:
            await form.close()
            raise HTTPException(429, "upload_limit")
        directory = root / "uploads" / cid
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / (str(uuid4()) + suffix)
        total = 0
        active_upload_users.add(user)
        try:
            with destination.open("xb") as stream:
                while chunk := await file.read(64 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "file_too_large")
                    stream.write(chunk)
            if total == 0:
                raise HTTPException(422, "empty_file")
            identifier = store.upload(user, cid, destination, (file.filename or "attachment")[:160])
            return {"id": identifier, "name": file.filename, "bytes": total}
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        finally:
            active_upload_users.discard(user)
            await form.close()

    @app.post("/v1/conversations/{cid}/runs")
    async def start(cid: str, body: InteractiveRequest, request: Request, user=owner_dependency):
        if not body.question.strip():
            if not body.attachment_id:
                raise HTTPException(422, "empty_question")
            body = body.model_copy(update={"question": "请查看附件，简要介绍内容，并询问我需要什么帮助。"})
        idem = request.headers.get("idempotency-key", "")
        if not idem or len(idem) > 128:
            raise HTTPException(422, "idempotency_key_required")
        handle = request.headers.get("x-agent-credential-handle")
        if handle is not None and (len(handle) > 128 or not handle.isascii()):
            raise CredentialError("invalid_credential", 422)
        return service.start(user, cid, body, idem, credential_handle=handle)

    @app.get("/v1/runs/{identifier}")
    async def get_run(identifier: str, user=owner_dependency):
        return service.get(user, identifier)

    @app.get("/v1/runs/{identifier}/events")
    async def run_events(identifier: str, user=owner_dependency):
        service.get(user, identifier)

        async def events():
            previous = None
            sent_answer = False
            while True:
                current = service.get(user, identifier)
                if current != previous:
                    for kind, data in run_event_updates(previous, current):
                        # Stamp only the first frame carrying visible text on this connection.
                        if not sent_answer and (
                            (kind in {"answer.delta", "answer.reset"} and (data.get("delta") or data.get("text")))
                            or (kind.startswith("run.") and (data.get("partial_answer") or (data.get("result") or {}).get("answer")))
                        ):
                            data = {**data, "first_answer_sent_at": time.time()}
                            sent_answer = True
                        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                        yield f"event: {kind}\ndata: {payload}\n\n"
                    previous = current
                if current["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                    break
                await asyncio.sleep(0.2)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"cache-control": "no-store", "x-accel-buffering": "no"})

    @app.post("/v1/runs/{identifier}/cancel")
    async def cancel(identifier: str, user=owner_dependency):
        return await service.cancel(user, identifier)

    return app
