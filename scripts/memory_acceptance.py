"""Isolated semantic-memory acceptance. Dry by default; never loads operator keys.

Live credentials are read ONLY from explicitly supplied, read-only source paths.
Only the credentials table is queried. Source chats/memories are never accessed.
Budget reservation precedes transport, survives failures, and is never reset.
"""

import argparse
import asyncio
import base64
import json
import secrets
import shlex
import sqlite3
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from gaia_agent.interactive.catalog import ModelSelection, resolve_profile
from gaia_agent.interactive.credential_crypto import EncryptedCredential, KeyCipher
from gaia_agent.interactive.credential_runtime import memory_planner_client
from gaia_agent.interactive.credential_sessions import CredentialSessions
from gaia_agent.interactive.credential_vault import (
    CredentialError,
    CredentialMaterial,
    CredentialVault,
)
from gaia_agent.interactive.memory.consolidation import MemoryConsolidator
from gaia_agent.interactive.memory.crypto import MemoryCrypto
from gaia_agent.interactive.memory.event_store import MemoryEventStore
from gaia_agent.interactive.memory.models import MemoryFailure
from gaia_agent.interactive.memory.policy import MemoryPolicy
from gaia_agent.interactive.memory.queue import MemoryQueue
from gaia_agent.interactive.memory.retrieval import MemoryRetriever
from gaia_agent.interactive.memory.service import MemoryService
from gaia_agent.interactive.memory.worker import MemoryWorker
from gaia_agent.interactive.models import InteractiveRequest
from gaia_agent.interactive.routing import route_request
from gaia_agent.interactive.store import ConversationStore

MODELS = {"openai": "gpt-6.1-sol", "deepseek": "deepseek-flash"}
RATES = {"openai": (2.0, 10.0), "deepseek": (0.3, 1.2)}
RESERVE = {"openai": 0.10, "deepseek": 0.02}


class AcceptanceBudget:
    def __init__(
        self, path, *, openai_extra_calls=0, extended_testing=False, release_testing=False
    ):
        if openai_extra_calls not in (0, 4):
            raise ValueError("invalid_diagnostic_allowance")
        self.limits = {"openai": 12 + openai_extra_calls, "deepseek": 12}
        self.extended_testing = extended_testing or release_testing
        if extended_testing:
            self.limits = {"openai": 40, "deepseek": 55}
        if release_testing:
            self.limits = {"openai": 60, "deepseek": 100}
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError("unsafe_ledger")
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else []
        if not isinstance(self.entries, list) or any(
            e.get("provider") not in MODELS for e in self.entries
        ):
            raise ValueError("invalid_ledger")

    def reserve(self, provider, phase):
        if (
            provider not in MODELS
            or sum(e["provider"] == provider for e in self.entries) >= self.limits[provider]
        ):
            raise ValueError("call_budget_exhausted")
        committed = sum(
            e.get("estimated_usd", RESERVE[e["provider"]])
            if self.extended_testing and e.get("state") == "returned"
            else RESERVE[e["provider"]]
            for e in self.entries
        )
        if committed + RESERVE[provider] > 2:
            raise ValueError("cost_budget_exhausted")
        index = len(self.entries)
        self.entries.append({"provider": provider, "phase": phase, "state": "reserved"})
        self.save()
        return index

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as output:
            json.dump(self.entries, output)
            temporary = Path(output.name)
        temporary.replace(self.path)


class BoundedProvider:
    def __init__(self, provider, budget, name, phase):
        self.provider, self.budget, self.name, self.phase = provider, budget, name, phase
        self.supports_vision = False
        self.diagnostics = []

    async def complete(self, request, executor=None, *, usage_observer=None):
        if (
            request.allowed_tools
            or request.max_tool_calls
            or request.max_output_tokens > 6400
            or len(request.model_dump_json().encode()) > 16000
        ):
            raise ValueError("request_budget_exceeded")
        index = self.budget.reserve(self.name, self.phase)
        response = await self.provider.complete(request, executor, usage_observer=usage_observer)
        self.diagnostics.append(
            {"status": response.response_status, "incomplete_reason": response.incomplete_reason}
        )
        if self.phase.endswith(":capture"):
            from pydantic import ValidationError

            from gaia_agent.interactive.memory.semantic import OperationBatch

            try:
                batch = OperationBatch.model_validate(
                    response.parsed
                    if response.parsed is not None
                    else json.loads(response.output_text)
                )
                self.diagnostics[-1]["synthetic_operations"] = [
                    op.model_dump(mode="json") for op in batch.operations
                ]
            except ValidationError as error:
                self.diagnostics[-1]["schema_errors"] = [
                    {"type": issue["type"], "location": list(issue["loc"])}
                    for issue in error.errors(include_input=False, include_context=False)
                ]
            except (ValueError, TypeError):
                self.diagnostics[-1]["schema_errors"] = [{"type": "invalid_json"}]
        rates = RATES[self.name]
        self.budget.entries[index].update(
            state="returned",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            estimated_usd=(
                response.usage.input_tokens * rates[0] + response.usage.output_tokens * rates[1]
            )
            / 1_000_000,
        )
        self.budget.save()
        return response


def source_key(options, provider):
    if options.source_env:
        # Parse only the two encryption settings; never execute a shell or load operator keys.
        values = {}
        for line in options.source_env.read_text().splitlines():
            parts = shlex.split(line, comments=True)
            if len(parts) == 1 and "=" in parts[0]:
                name, value = parts[0].split("=", 1)
                if name in {"AGENT_CREDENTIAL_KEYRING", "AGENT_CREDENTIAL_ACTIVE_VERSION"}:
                    values[name] = value
        config = {
            "credential_keyring": json.loads(values["AGENT_CREDENTIAL_KEYRING"]),
            "active_version": values["AGENT_CREDENTIAL_ACTIVE_VERSION"],
        }
        owner = options.source_owner
    else:
        config = json.loads(options.source_config.read_text())
        owner = json.loads(options.owner_config.read_text())["owner_id"]
    cipher = KeyCipher(
        {v: base64.b64decode(k, validate=True) for v, k in config["credential_keyring"].items()},
        config["active_version"],
    )
    connection = sqlite3.connect(f"file:{options.source_db}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT version,nonce,ciphertext FROM credentials WHERE owner=? AND provider=?",
            (owner, provider),
        ).fetchone()
        if row is None:
            raise CredentialError("credential_required", 422)
        return cipher.decrypt(owner, provider, EncryptedCredential(*row))
    finally:
        connection.close()


async def run_provider(options, budget, provider, scenarios):
    key = source_key(options, provider)
    profile = resolve_profile(ModelSelection(provider=provider, model=MODELS[provider]))
    owner = "synthetic-acceptance-" + secrets.token_hex(8)
    phase = ["capture"]
    diagnostics = []
    with tempfile.TemporaryDirectory(prefix="toyagent-semantic-acceptance-") as directory:
        store = ConversationStore(Path(directory) / "state.sqlite")
        vault = CredentialVault(
            store.db, KeyCipher({"test": secrets.token_bytes(32)}, "test"), store.lock
        )
        sessions = CredentialSessions(vault)
        material = CredentialMaterial(provider, key)
        handle = sessions.issue(owner, material)
        from gaia_agent.interactive.memory.repository import MemoryRepository

        repo = MemoryRepository(
            store.db, store.lock, MemoryCrypto({"test": secrets.token_bytes(32)}, "test")
        )
        memory = MemoryService(repo, MemoryPolicy(), MemoryRetriever())
        queue = MemoryQueue(MemoryEventStore(store.db, store.lock, repo.crypto))

        @asynccontextmanager
        async def factory(event, values):
            async with memory_planner_client(event, values) as planner:
                planner.provider = BoundedProvider(planner.provider, budget, provider, phase[0])
                try:
                    yield planner
                finally:
                    diagnostics.extend(planner.provider.diagnostics)

        consolidator = MemoryConsolidator(repo, queue)
        worker = MemoryWorker(queue, factory, consolidator, sessions)
        consolidator.temporary_valid = lambda event: bool(worker._material(event))
        worker.start()
        results = []
        try:
            for case in scenarios:
                phase[0] = case["id"] + ":capture"
                request = InteractiveRequest(
                    question=case["input"],
                    provider=provider,
                    model=profile.model,
                    reasoning_effort="low",
                )
                cid = store.create(owner, provider)["id"]
                run = store.reserve(owner, cid, request, case["id"])
                began = time.monotonic()
                worker.admit(owner, run, request, sessions.reference(owner, provider, handle))
                event = queue.store.get(owner, run["id"])
                store.update(
                    run["id"], status="completed", result={"answer": "Synthetic committed source"}
                )
                worker.release(owner, run["id"], True)
                async with asyncio.timeout(40):
                    while worker.status(owner, run["id"])["state"] in {
                        "pending",
                        "leased",
                        "provider_started",
                        "proposed",
                    }:
                        await asyncio.sleep(0.05)
                status = worker.status(owner, run["id"])
                snapshot = memory.snapshot(owner, provider, "normal")
                content = "\n".join(r.content for r in snapshot.records)
                facts_ok = all(token.casefold() in content.casefold() for token in case["present"])
                forbidden_ok = all(
                    token.casefold() not in content.casefold() for token in case["forbidden"]
                )
                result = {
                    "id": case["id"],
                    "state": status["state"],
                    "record_count": len(snapshot.records),
                    "facts_ok": facts_ok,
                    "forbidden_ok": forbidden_ok,
                    "seconds": round(time.monotonic() - began, 3),
                    "capture_transport": diagnostics[-1] if diagnostics else None,
                }
                if status["state"] in {"failed", "blocked", "uncertain"}:
                    result["failure_code"] = status["failure_code"]
                    results.append(result)
                    break
                phase[0] = case["id"] + ":recall"
                context = memory.retriever.select_context(snapshot, case["question"])
                async with factory(event, material) as planner:
                    decision, _usage = await route_request(
                        planner.provider, case["question"], "[]", "low", context.text
                    )
                # Direct model recall in a fresh history, not a canned answer.
                required = (
                    case.get("answer_present", case["present"])
                    if case["id"] not in {"negative", "forget"}
                    else []
                )
                answer_ok = decision.route == "direct" and all(
                    token.casefold() in decision.answer.casefold() for token in required
                )
                answer_ok = answer_ok and all(
                    token.casefold() not in decision.answer.casefold()
                    for token in case.get("answer_forbidden", [])
                )
                if case["id"] == "forget":
                    answer_ok = answer_ok and "小林" not in decision.answer
                result.update(
                    answer_ok=answer_ok,
                    passed=facts_ok and forbidden_ok and answer_ok,
                    route=decision.route,
                    synthetic_answer=decision.answer,
                    missing_answer_tokens=[
                        token
                        for token in required
                        if token.casefold() not in decision.answer.casefold()
                    ],
                )
                results.append(result)
            return {
                "provider": provider,
                "model": profile.model,
                "reasoning_effort": "low",
                "cases": results,
                "passed": len(results) == len(scenarios) and all(c.get("passed") for c in results),
            }
        finally:
            await worker.close()
            sessions.close()
            store.db.close()


async def live(options, scenarios):
    budget = AcceptanceBudget(
        options.ledger,
        openai_extra_calls=options.openai_extra_calls,
        extended_testing=options.extended_testing,
        release_testing=options.release_testing,
    )
    results = []
    for provider in options.providers:
        try:
            results.append(await run_provider(options, budget, provider, scenarios))
        except Exception as error:  # noqa: BLE001 - never serialize private provider exceptions
            results.append(
                {
                    "provider": provider,
                    "passed": False,
                    "failure_code": error.code
                    if isinstance(error, (CredentialError, MemoryFailure))
                    else "acceptance_failed",
                }
            )
    report = {
        "results": results,
        "calls": {
            provider: sum(e["provider"] == provider for e in budget.entries) for provider in MODELS
        },
        "historical_reservations_usd": round(
            sum(RESERVE[e["provider"]] for e in budget.entries), 4
        ),
        "current_cost_bound_usd": round(
            sum(
                e.get("estimated_usd", RESERVE[e["provider"]])
                if e.get("state") == "returned"
                else RESERVE[e["provider"]]
                for e in budget.entries
            ),
            6,
        ),
        "estimated_returned_usd": round(sum(e.get("estimated_usd", 0) for e in budget.entries), 6),
    }
    print(json.dumps(report, ensure_ascii=False))
    if options.report:
        with options.report.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
    return 0 if all(r["passed"] for r in results) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline-plan", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-budget-usd", type=float)
    parser.add_argument("--max-calls-per-provider", type=int)
    parser.add_argument("--openai-extra-calls", type=int, choices=(0, 4), default=0)
    parser.add_argument(
        "--extended-testing",
        action="store_true",
        help="Explicit additional authorization: OpenAI40/DeepSeek55 total, USD2 unchanged",
    )
    parser.add_argument("--providers", nargs="+", choices=tuple(MODELS), default=list(MODELS))
    parser.add_argument(
        "--release-testing",
        action="store_true",
        help="Renewed bounded authority: cumulative OpenAI60/DeepSeek100, USD2 unchanged",
    )
    parser.add_argument("--additional-corpus", type=Path, action="append", default=[])
    parser.add_argument("--report", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--source-db", type=Path)
    parser.add_argument("--source-config", type=Path)
    parser.add_argument("--owner-config", type=Path)
    parser.add_argument("--source-env", type=Path)
    parser.add_argument("--source-owner")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "tests/fixtures/memory_scenarios.json",
    )
    options = parser.parse_args()
    scenarios = json.loads(options.corpus.read_text())
    for extra in options.additional_corpus:
        scenarios.extend(json.loads(extra.read_text()))
    if options.report and options.report.exists():
        parser.error("report must be a new path")
    if not options.live:
        print(json.dumps({"paid_calls": 0, "scenarios": [case["id"] for case in scenarios]}))
        return 0
    if (
        options.confirm_budget_usd != 2
        or options.max_calls_per_provider != 12
        or any(
            value is None
            for value in (
                options.ledger,
                options.source_db,
            )
        )
        or not (
            (options.source_env and options.source_owner)
            or (options.source_config and options.owner_config)
        )
    ):
        parser.error("live requires explicit USD2/12-call budget and source paths")
    # One process owns this new budget; concurrent runners may not share it.
    import fcntl

    with Path(str(options.ledger) + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return asyncio.run(live(options, scenarios))


if __name__ == "__main__":
    raise SystemExit(main())
