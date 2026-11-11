"""Disposable localhost browser fixture. No real model clients or credentials.

Run uvicorn tests.manual_memory_app:app on port 8001 and point a separate local
frontend at it. Its database lives in a process-owned temporary directory.
"""

import asyncio
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from gaia_agent.interactive.api import create_app
from gaia_agent.interactive.catalog import public_catalog
from gaia_agent.interactive.composition import InteractiveFailure
from gaia_agent.interactive.memory.crypto import MemoryCrypto
from gaia_agent.interactive.memory.extraction import MemoryExtractor
from gaia_agent.interactive.memory.runtime import run_with_memory
from gaia_agent.interactive.models import InteractiveResult
from gaia_agent.models import Usage
from gaia_agent.providers import ModelResponse

_directory = tempfile.TemporaryDirectory(prefix="toy-agent-memory-browser-qa-")


class SyntheticProvider:
    async def complete(self, request, executor=None, *, usage_observer=None):
        return ModelResponse(parsed={"candidates": []}, response_status="completed")


async def execute(
    request,
    workspace,
    history,
    on_progress,
    attachment_path,
    *,
    memory_snapshot=None,
    run_id=None,
    deadline=None,
):
    if request.question == "qa:tool-error":
        on_progress("act_started", {})
        on_progress("tool_call_started", {"tool": "web_search"})
        on_progress(
            "tool_call_failed",
            {
                "tool": "web_search",
                "provider": "deepseek",
                "category": "balance",
                "http_status": 402,
            },
        )
        raise InteractiveFailure("retry_exhausted")
    if request.question == "qa:slow":
        await asyncio.sleep(10)

    async def answer():
        on_progress("chat_completed", {})
        used = len(memory_snapshot.records) if memory_snapshot else 0
        return InteractiveResult(
            answer=f"Synthetic browser fixture — {used} authorized memory records. Selection: {request.provider}/{request.model}/{request.reasoning_effort}. No model request was sent.",
            evidence=[],
            usage=Usage(),
        )

    return await run_with_memory(
        answer,
        memory_snapshot,
        MemoryExtractor(SyntheticProvider()),
        request.question,
        run_id,
        deadline,
        memory_service=app.state.memory_service,
    )


app = create_app(
    Path(_directory.name),
    "synthetic-browser-service-token",
    execute=execute,
    memory_crypto=MemoryCrypto({"test": b"t" * 32}, "test"),
)


class SyntheticDiscovery:
    async def discover(self, owner, provider, handle=None):
        return {
            "models": [
                dict(model, availability="visible")
                for model in public_catalog()["models"]
                if model["provider"] == provider
            ],
            "discovery": {"status": "fresh", "checked_at": 0, "expires_at": 9999999999},
        }


_lifespan = app.router.lifespan_context


@asynccontextmanager
async def synthetic_lifespan(app):
    async with _lifespan(app):
        app.state.model_discovery = SyntheticDiscovery()
        yield


app.router.lifespan_context = synthetic_lifespan


@app.get("/v1/credentials")
async def synthetic_status():
    # This disposable executor never connects to a model, so no Key exists here.
    return {
        "statuses": [
            {"provider": "openai", "configured": True},
            {"provider": "deepseek", "configured": True},
        ]
    }
