# ruff: noqa: F811 -- shared fixture
import time
from types import SimpleNamespace

import pytest
from test_memory_read_loop import ScriptedProvider, answer, search
from test_memory_repository import repo, write  # noqa: F401

from gaia_agent.interactive.composition import execute_interactive
from gaia_agent.interactive.models import InteractiveConfig, InteractiveRequest
from gaia_agent.models import SpecialistName, Usage


@pytest.mark.asyncio
async def test_direct_uses_loop_and_reports_used_fact(repo, tmp_path):
    from gaia_agent.interactive.memory.tools import MemoryTools
    saved = repo.save_manual("alice", write("我学习数学"), ("openai",))
    provider = ScriptedProvider([search(), answer("按数学背景解释")])
    result = await execute_interactive(InteractiveRequest(question="解释一下", provider="openai", model="gpt-6.1-sol"), InteractiveConfig(openai_api_key="fake"), tmp_path,
        provider=provider, memory_tools=MemoryTools(repo, repo.state_snapshot("alice", "openai").scope), memory_deadline=time.monotonic() + 30)
    assert result.answer == "按数学背景解释"
    assert result.memory.used_ids == (saved.id,)
    assert result.usage.input_tokens == 20


@pytest.mark.asyncio
async def test_research_receives_same_evidence_without_second_read_loop(repo, tmp_path, monkeypatch):
    import gaia_agent.interactive.composition as module
    from gaia_agent.interactive.memory.tools import MemoryTools
    repo.save_manual("alice", write("我学习数学"), ("openai",))
    decision = answer()
    decision["decision"] = {"route": "research", "answer": "", "needs_verification": True}
    provider = ScriptedProvider([search(), decision])
    def build(*args, **kwargs):
        assert "我学习数学" in kwargs["user_context_provider"].context
        return SimpleNamespace(_specialists={SpecialistName.COMPUTATION: SimpleNamespace(capability_unavailable=False)}, run=run_graph, result_evidence=[])
    async def run_graph(task):
        return SimpleNamespace(answer="verified", error_category=None, usage=Usage(input_tokens=3))
    monkeypatch.setattr(module, "build_interactive_orchestrator", build)
    result = await execute_interactive(InteractiveRequest(question="研究一下", provider="openai", model="gpt-6.1-sol"), InteractiveConfig(openai_api_key="fake"), tmp_path,
        provider=provider, memory_tools=MemoryTools(repo, repo.state_snapshot("alice", "openai").scope), memory_deadline=time.monotonic() + 30)
    assert result.answer == "verified" and len(provider.requests) == 2
    assert result.usage.input_tokens == 23


@pytest.mark.asyncio
async def test_delete_after_search_prevents_next_paid_dispatch(repo):
    from gaia_agent.interactive.memory.models import MemoryFailure
    from gaia_agent.interactive.memory.read_loop import route_with_memory
    from gaia_agent.interactive.memory.tools import MemoryTools
    saved = repo.save_manual("alice", write("我学习数学"), ("openai",))
    tools = MemoryTools(repo, repo.state_snapshot("alice", "openai").scope)
    execute = tools.execute
    def changed(call):
        result = execute(call)
        repo.delete("alice", saved.id, 1)
        return result
    tools.execute = changed
    provider = ScriptedProvider([search(), answer()])
    with pytest.raises(MemoryFailure):
        await route_with_memory(provider, question="我是谁", history="[]", reasoning_effort="low", tools=tools, overview=None, deadline=time.monotonic() + 30)
    assert len(provider.requests) == 1
