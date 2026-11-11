# ruff: noqa: F811 -- shared fixture
import time

import pytest
from test_memory_repository import repo, write  # noqa: F401

from gaia_agent.models import Usage
from gaia_agent.providers import ModelResponse


class ScriptedProvider:
    def __init__(self, outputs):
        self.outputs, self.requests = list(outputs), []

    async def complete(self, request, executor=None):
        self.requests.append(request)
        output = self.outputs.pop(0)
        return ModelResponse(response_status="completed", parsed=output, usage=Usage(input_tokens=10, output_tokens=5))


def answer(text="可以"):
    return {"action": "decide", "tool_calls": [], "decision": {"route": "direct", "answer": text, "needs_verification": False}}


def search(query="数学"):
    return {"action": "tools", "tool_calls": [{"name": "search_memory", "arguments": {"query": query}}], "decision": None}


async def run(provider, repo, **kwargs):
    from gaia_agent.interactive.memory.read_loop import route_with_memory
    from gaia_agent.interactive.memory.tools import MemoryTools
    tools = MemoryTools(repo, repo.state_snapshot("alice", "openai").scope)
    return await route_with_memory(provider, question="按我的背景解释", history="[]", reasoning_effort="low", tools=tools, overview=None, deadline=time.monotonic() + 30, **kwargs)


@pytest.mark.asyncio
async def test_zero_tool_decision_has_one_model_call(repo):
    provider = ScriptedProvider([answer()])
    result = await run(provider, repo)
    assert result.decision.answer == "可以" and len(provider.requests) == 1
    assert result.usage.input_tokens == 10


@pytest.mark.asyncio
async def test_search_then_read_then_answer_is_three_calls_with_continuous_context(repo):
    saved = repo.save_manual("alice", write("我学习数学"), ("openai",))
    read = {"action": "tools", "tool_calls": [{"name": "read_memory", "arguments": {"kind": "fact", "ids": [saved.id]}}], "decision": None}
    provider = ScriptedProvider([search(), read, answer("先用数学类比")])
    result = await run(provider, repo)
    assert len(provider.requests) == 3 and result.usage.input_tokens == 30
    assert "我学习数学" in str(provider.requests[-1].input_items)
    assert len(provider.requests[-1].input_items) > len(provider.requests[1].input_items)
    assert saved.id in {r.id for r in result.evidence.refs}


@pytest.mark.asyncio
async def test_two_empty_search_rounds_force_final_decision(repo):
    provider = ScriptedProvider([search("没有甲"), search("没有乙"), answer("缺少背景")["decision"]])
    result = await run(provider, repo)
    assert result.termination == "no_new_information"
    assert "tool_calls" not in provider.requests[-1].output_schema["properties"]


@pytest.mark.asyncio
async def test_six_rounds_then_seventh_decision_has_no_extra_classifier(repo):
    for i in range(6):
        repo.save_manual("alice", write(f"独立事实 key{i}", entity=str(i)), ("openai",))
    provider = ScriptedProvider([*(search(f"key{i}") for i in range(6)), answer()])
    # Final budget request accepts a RouteDecision, not another tool request.
    provider.outputs[-1] = answer()["decision"]
    result = await run(provider, repo)
    assert len(provider.requests) == 7
    assert result.termination == "round_budget"
    assert result.usage.input_tokens == 70


@pytest.mark.asyncio
async def test_invalid_action_fails_without_model_repair(repo):
    from gaia_agent.interactive.memory.models import MemoryFailure
    provider = ScriptedProvider([{"action": "execute_sql", "sql": "select private"}])
    with pytest.raises(MemoryFailure):
        await run(provider, repo)
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_eight_tools_stop_without_ninth_execution(repo):
    for i in range(8):
        repo.save_manual("alice", write(f"fact key{i}", entity=str(i)), ("openai",))
    batch = {"action": "tools", "tool_calls": [search(f"key{i}")["tool_calls"][0] for i in range(8)], "decision": None}
    provider = ScriptedProvider([batch, answer()["decision"]])
    result = await run(provider, repo)
    assert result.termination == "tool_budget"
    assert len(provider.requests) == 2
    assert len(result.evidence.refs) == 8


@pytest.mark.asyncio
async def test_input_budget_checked_before_paid_call(repo):
    from gaia_agent.interactive.memory.models import MemoryFailure
    provider = ScriptedProvider([answer()])
    with pytest.raises(MemoryFailure, match="memory_limit"):
        await run(provider, repo, input_token_budget=100)
    assert provider.requests == []


@pytest.mark.asyncio
async def test_cross_provider_agentic_search_uses_same_owner_memory(repo):
    from gaia_agent.interactive.memory.read_loop import route_with_memory
    from gaia_agent.interactive.memory.tools import MemoryTools
    generation=repo.settings('alice').generation
    repo.register_turn('alice','openai-memory')
    identifier=repo.apply_candidates('alice','openai','openai-memory',(write('我学习数学'),),generation)[0]
    read={'action':'tools','tool_calls':[{'name':'read_memory','arguments':{'kind':'fact','ids':[identifier]}}], 'decision':None}
    provider=ScriptedProvider([search(),read,answer('你学习数学')])
    tools=MemoryTools(repo,repo.state_snapshot('alice','deepseek').scope)
    result=await route_with_memory(provider,question='我的数学背景是什么',history='[]',reasoning_effort='low',tools=tools,overview=None,deadline=time.monotonic()+30)
    assert result.decision.answer=='你学习数学'
    assert identifier in {ref.id for ref in result.evidence.refs}
    assert '我学习数学' in str(provider.requests[-1].input_items)
