"""Explicitly authorized, bounded memory QA. Synthetic data; secrets never printed.

Dry-run by default. Live uses a NEW ledger; never resets the old six-task allowance.
Counters are reserved before transport, so uncertain/failed calls still spend allowance.
"""

import argparse
import asyncio
import fcntl
import json
import os
import re
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import patch

from openai import AsyncOpenAI

from gaia_agent.interactive import credential_runtime as runtime_module
from gaia_agent.interactive.composition import InteractiveFailure
from gaia_agent.interactive.credential_crypto import cipher_from_environment
from gaia_agent.interactive.credential_sessions import CredentialSessions
from gaia_agent.interactive.credential_vault import CredentialMaterial, CredentialVault
from gaia_agent.interactive.memory.crypto import MemoryCrypto
from gaia_agent.interactive.memory.models import MemoryCandidate
from gaia_agent.interactive.memory.policy import MemoryPolicy
from gaia_agent.interactive.memory.repository import MemoryRepository
from gaia_agent.interactive.memory.retrieval import MemoryRetriever
from gaia_agent.interactive.memory.service import MemoryService
from gaia_agent.interactive.models import InteractiveRequest
from gaia_agent.reliability import RetryPolicy, invocation_deadline_scope

CASES = {
    "capture": "我喜欢用中文回答。请简短确认。",
    "recall": "Explain a Python list in two short sentences.",
}
MODELS = {"openai": "gpt-6.1-sol", "deepseek": "deepseek-flash"}


@contextmanager
def budget_lock(path):
    path = Path(str(path) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class MemoryBudget:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError("unsafe_ledger")
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else []
        if (
            not isinstance(self.entries, list)
            or len(self.entries) > 4
            or any(
                e.get("provider") not in MODELS
                or e.get("task") not in CASES
                or type(e.get("calls")) is not int
                or not 0 <= e["calls"] <= 2
                for e in self.entries
            )
            or self.calls > 8
        ):
            raise ValueError("invalid_ledger")

    @property
    def calls(self):
        return sum(e["calls"] for e in self.entries)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as stream:
            json.dump(self.entries, stream, ensure_ascii=False)
            temporary = stream.name
        os.replace(temporary, self.path)

    def entry(self, provider, task):
        return next(
            (e for e in self.entries if (e["provider"], e["task"]) == (provider, task)), None
        )

    def begin(self, provider, task):
        if (
            provider not in MODELS
            or task not in CASES
            or self.entry(provider, task) is not None
            or len(self.entries) >= 4
        ):
            raise ValueError("task_not_authorized_or_already_started")
        self.entries.append({"provider": provider, "task": task, "calls": 0, "status": "started"})
        self.save()

    def reserve(self, provider, task):
        entry = self.entry(provider, task)
        if entry is None or entry["status"] != "started" or entry["calls"] >= 2 or self.calls >= 8:
            raise ValueError("budget_exhausted")
        entry["calls"] += 1
        self.save()

    def finish(self, provider, task, summary):
        entry = self.entry(provider, task)
        entry.update(summary)
        self.save()

    def recheck(self, provider, task):
        entry = self.entry(provider, task)
        if (
            task != "recall"
            or entry is None
            or entry.get("status") != "completed"
            or entry.get("chinese_answer") is not False
            or entry["calls"] >= 2
            or self.calls >= 8
            or "before_fix" in entry
        ):
            raise ValueError("recheck_not_authorized")
        entry["before_fix"] = {
            key: value for key, value in entry.items() if key not in {"provider", "task", "calls"}
        }
        entry["status"] = "started"
        self.save()


async def metered(ledger, provider, task, transport, **kwargs):
    if kwargs.get("tools"):
        raise ValueError("tools_not_authorized")
    ledger.reserve(provider, task)
    return await transport(**kwargs)


class MeteredClient:
    def __init__(self, ledger, provider, task, **kwargs):
        self.client = AsyncOpenAI(**kwargs)

        async def responses(**options):
            return await metered(ledger, provider, task, self.client.responses.create, **options)

        async def chat(**options):
            return await metered(
                ledger, provider, task, self.client.chat.completions.create, **options
            )

        self.responses = SimpleNamespace(create=responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=chat))

    def with_options(self, **kwargs):
        if kwargs != {"max_retries": 0}:
            raise ValueError("retry_override_not_authorized")
        return self

    async def close(self):
        await self.client.close()


def no_research(*args, **kwargs):
    raise InteractiveFailure("research_not_authorized_in_memory_smoke")


async def live(ledger, data_dir, recheck=False):
    source = sqlite3.connect(f"file:{data_dir / 'state.sqlite'}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    vault = CredentialVault(source, cipher_from_environment(), RLock())
    try:
        for provider, model in MODELS.items():
            if ledger.entry(provider, "capture") is not None and not (
                recheck and provider == "openai"
            ):
                # Never reconstruct a partly lost state and silently re-run paid work.
                continue
            key = vault.read("local_seedy", provider)
            with tempfile.TemporaryDirectory(prefix="toy-agent-live-memory-") as temporary:
                root = Path(temporary)
                db = sqlite3.connect(root / "memory.sqlite", check_same_thread=False)
                db.row_factory = sqlite3.Row
                service = MemoryService(
                    MemoryRepository(db, RLock(), MemoryCrypto.from_environment()),
                    MemoryPolicy(),
                    MemoryRetriever(),
                )
                sessions = CredentialSessions(vault)
                owner = "synthetic_memory_qa_" + provider
                handle = sessions.issue(owner, CredentialMaterial(provider, key))
                ref = sessions.reference(owner, provider, handle)
                runtime = runtime_module.UserRuntime(
                    runtime_module.InteractiveRuntimeSettings(base_url="https://api.openai.com/v1"),
                    sessions,
                    service,
                )
                try:
                    if recheck:
                        candidate = MemoryCandidate(
                            category="profile",
                            facet="preferred_language",
                            entity="self",
                            content="我喜欢用中文回答。",
                            source_start=0,
                            source_end=10,
                            explicit_relearn=False,
                        )
                        service.repository.save_manual(
                            owner, service.policy.validate_manual(candidate), (provider,)
                        )
                    for task, question in (
                        {"recall": CASES["recall"]} if recheck else CASES
                    ).items():
                        if recheck:
                            ledger.recheck(provider, task)
                        else:
                            ledger.begin(provider, task)
                        started = time.monotonic()
                        deadline = started + 90
                        run_id = f"memory-live-{provider}-{task}"
                        service.repository.register_turn(owner, run_id)
                        snapshot = service.snapshot(owner, provider, "normal")

                        client_factory = partial(MeteredClient, ledger, provider, task)

                        try:
                            with (
                                patch.object(runtime_module, "AsyncOpenAI", client_factory),
                                patch.object(
                                    runtime_module,
                                    "RetryPolicy",
                                    lambda **kwargs: RetryPolicy(max_attempts=1),
                                ),
                                patch(
                                    "gaia_agent.interactive.composition.build_interactive_orchestrator",
                                    no_research,
                                ),
                                service.lease(snapshot, deadline) as lease,
                                invocation_deadline_scope(lease),
                            ):
                                result = await runtime.execute(
                                    ref,
                                    InteractiveRequest(
                                        question=question, provider=provider, model=model
                                    ),
                                    root / task,
                                    (),
                                    lambda *args: None,
                                    None,
                                    memory_snapshot=snapshot,
                                    run_id=run_id,
                                    deadline=deadline,
                                )
                            summary = {
                                "status": "completed",
                                "answer": result.answer,
                                "memory": result.memory.model_dump(mode="json"),
                                "usage": result.usage.model_dump(),
                                "stored_count": len(service.repository.list(owner)),
                                "chinese_answer": bool(
                                    re.search(r"[\u3400-\u9fff]", result.answer)
                                ),
                                "elapsed_seconds": round(time.monotonic() - started, 2),
                                "verification": "post_fix_seeded_profile"
                                if recheck
                                else "live_capture_and_recall",
                            }
                        except Exception as error:  # noqa: BLE001 - no raw payloads/keys in evidence
                            from gaia_agent.interactive.credential_vault import SAFE_CODES

                            code = getattr(error, "code", "")
                            summary = {
                                "status": "failed",
                                "error": code if code in SAFE_CODES else "test_execution_failed",
                                "elapsed_seconds": round(time.monotonic() - started, 2),
                            }
                        ledger.finish(provider, task, summary)
                        print(
                            json.dumps(
                                {"provider": provider, "task": task, **summary}, ensure_ascii=False
                            ),
                            flush=True,
                        )
                finally:
                    sessions.close()
                    db.close()
    finally:
        source.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument(
        "--recheck-openai",
        action="store_true",
        help="Use remaining original-case allowance once after a failed language-recall check",
    )
    options = parser.parse_args()
    if not options.live:
        print(json.dumps({"dry_run": True, "tasks": 4, "max_requests": 8, "tools": False}))
        return
    if options.ledger is None or options.data_dir is None:
        parser.error("live requires an explicitly authorized ledger and local data directory")
    with budget_lock(options.ledger):
        asyncio.run(live(MemoryBudget(options.ledger), options.data_dir, options.recheck_openai))


if __name__ == "__main__":
    main()
