import importlib.util
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location(
        "memory_smoke", Path(__file__).parents[1] / "scripts/memory_smoke.py"
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_budget_survives_reload_and_blocks_ninth_and_third_task(tmp_path):
    smoke = module()
    ledger = smoke.MemoryBudget(tmp_path / "budget.json")
    for provider in ("openai", "deepseek"):
        for task in ("capture", "recall"):
            ledger.begin(provider, task)
            ledger.reserve(provider, task)
            ledger.reserve(provider, task)
            ledger.finish(provider, task, {"status": "completed"})
        with pytest.raises(ValueError):
            ledger.begin(provider, "third")
    restored = smoke.MemoryBudget(tmp_path / "budget.json")
    assert restored.calls == 8
    with pytest.raises(ValueError):
        restored.reserve("openai", "recall")
    with pytest.raises(ValueError):
        restored.begin("openai", "capture")


@pytest.mark.asyncio
async def test_meter_blocks_tools_and_over_limit_before_transport(tmp_path):
    smoke = module()
    ledger = smoke.MemoryBudget(tmp_path / "budget.json")
    ledger.begin("openai", "capture")
    seen = []

    async def transport(**kwargs):
        seen.append(kwargs)
        return "ok"

    with pytest.raises(ValueError):
        await smoke.metered(ledger, "openai", "capture", transport, tools=[{"type": "web_search"}])
    assert ledger.calls == 0 and not seen
    for _ in range(2):
        assert await smoke.metered(ledger, "openai", "capture", transport, tools=[]) == "ok"
    with pytest.raises(ValueError):
        await smoke.metered(ledger, "openai", "capture", transport, tools=[])
    assert len(seen) == 2


def test_recheck_only_failed_recall_within_original_case_budget(tmp_path):
    smoke = module()
    ledger = smoke.MemoryBudget(tmp_path / "budget.json")
    ledger.begin("openai", "recall")
    ledger.reserve("openai", "recall")
    ledger.finish("openai", "recall", {"status": "completed", "chinese_answer": False})
    ledger.recheck("openai", "recall")
    ledger.reserve("openai", "recall")
    ledger.finish("openai", "recall", {"status": "completed", "chinese_answer": True})
    assert len(ledger.entries) == 1 and ledger.calls == 2
    assert ledger.entries[0]["before_fix"]["chinese_answer"] is False
    with pytest.raises(ValueError):
        ledger.recheck("openai", "recall")


def test_parallel_live_jobs_cannot_share_one_allowance(tmp_path):
    smoke = module()
    path = tmp_path / "budget.json"
    with smoke.budget_lock(path), pytest.raises(BlockingIOError), smoke.budget_lock(path):
        pytest.fail("a second job acquired the same paid allowance")
