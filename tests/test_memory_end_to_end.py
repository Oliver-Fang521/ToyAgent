import json
import subprocess
import sys
from contextlib import asynccontextmanager

import pytest
from conftest import FakeProvider
from test_memory_worker import admitted, settled, setup

from gaia_agent.interactive.memory.consolidation import MemoryConsolidator
from gaia_agent.interactive.memory.models import MemoryOperation
from gaia_agent.interactive.memory.semantic import SemanticMemoryPlanner
from gaia_agent.interactive.memory.worker import MemoryWorker


@pytest.mark.asyncio
async def test_real_queue_worker_semantic_transport_publishes_cross_conversation_memory(tmp_path):
    store, sessions, memory, queue = setup(tmp_path)
    requests = []

    @asynccontextmanager
    async def factory(event, material):
        provider = FakeProvider()
        provider.return_parsed(
            {
                "operations": [
                    MemoryOperation(
                        action="add",
                        category="profile",
                        topic="name",
                        entity="self",
                        cardinality="single",
                        content="我是方林",
                        source_start=0,
                        source_end=4,
                        source_kind="user",
                        intent="automatic",
                    ).model_dump()
                ]
            }
        )
        requests.append(provider.requests)
        yield SemanticMemoryPlanner(provider)

    worker = MemoryWorker(queue, factory, MemoryConsolidator(memory.repository, queue), sessions)
    identifier = admitted(store, sessions, worker)
    assert memory.snapshot("alice", "openai", "normal").records == ()
    worker.start()
    try:
        result = await settled(worker, identifier, {"completed", "failed"})
        assert result["state"] == "completed"
        cid = store.create("alice", "openai")["id"]
        assert store.history("alice", cid) == []
        context = memory.retriever.select_context(
            memory.snapshot("alice", "openai", "normal"), "我是谁"
        )
        assert "我是方林" in context.text
        assert context.used_ids
        assert memory.snapshot("bob", "openai", "normal").records == ()
        assert [r.content for r in memory.snapshot("alice", "deepseek", "normal").records] == ["我是方林"]
        assert memory.snapshot("bob", "deepseek", "normal").records == ()
        assert len(requests) == 1 and len(requests[0]) == 1
        assert requests[0][0].allowed_tools == ()
    finally:
        await worker.close()
        store.db.close()


def test_acceptance_cli_dry_run_never_needs_keys_or_source_data():
    result = subprocess.run(
        [sys.executable, "scripts/memory_acceptance.py", "--offline-plan"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["paid_calls"] == 0 and len(data["scenarios"]) == 6
    assert "secret" not in result.stdout.lower()


def test_paid_budget_survives_failures_and_never_resets_on_restart(tmp_path):
    import runpy

    AcceptanceBudget = runpy.run_path("scripts/memory_acceptance.py")["AcceptanceBudget"]

    path = tmp_path / "budget.json"
    budget = AcceptanceBudget(path)
    for index in range(12):
        budget.reserve("openai", f"uncertain-{index}")
    reopened = AcceptanceBudget(path)
    with pytest.raises(ValueError, match="call_budget_exhausted"):
        reopened.reserve("openai", "not-authorized")
    for index in range(12):
        reopened.reserve("deepseek", f"test-{index}")
    assert len(AcceptanceBudget(path).entries) == 24


def test_explicit_diagnostic_extension_is_bounded_and_preserves_ledger(tmp_path):
    import runpy

    budget_class = runpy.run_path("scripts/memory_acceptance.py")["AcceptanceBudget"]
    path = tmp_path / "budget.json"
    original = budget_class(path)
    for index in range(12):
        original.reserve("openai", str(index))
    extended = budget_class(path, openai_extra_calls=4)
    for index in range(4):
        extended.reserve("openai", f"diagnostic-{index}")
    with pytest.raises(ValueError, match="call_budget_exhausted"):
        extended.reserve("openai", "excess")
    assert len(budget_class(path).entries) == 16


def test_extended_testing_reuses_actual_returned_cost_and_bounds_new_calls(tmp_path):
    import runpy

    budget_class = runpy.run_path("scripts/memory_acceptance.py")["AcceptanceBudget"]
    budget = budget_class(tmp_path / "budget.json", extended_testing=True)
    for index in range(40):
        entry = budget.reserve("openai", str(index))
        budget.entries[entry].update(state="returned", estimated_usd=0.005)
        budget.save()
    with pytest.raises(ValueError, match="call_budget_exhausted"):
        budget.reserve("openai", "excess")
