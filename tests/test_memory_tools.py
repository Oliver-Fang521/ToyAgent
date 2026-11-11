# ruff: noqa: F811 -- shared fixture
import json

import pytest
from test_memory_repository import repo, write  # noqa: F401

from gaia_agent.interactive.memory.models import MemoryFailure


def tools_for(repo, provider="openai", mode="normal"):
    from gaia_agent.interactive.memory.tools import MemoryTools
    return MemoryTools(repo, repo.state_snapshot("alice", provider).scope, mode=mode)


def call(name, **arguments):
    from gaia_agent.interactive.memory.tools import MemoryToolCall
    return MemoryToolCall(name=name, arguments=arguments)


def test_search_read_old_fact_with_no_overview(repo):
    saved = repo.save_manual("alice", write("学习Python"), ("openai",))
    tools = tools_for(repo)
    result = tools.execute(call("search_memory", query="Python"))
    assert result.items[0]["id"] == saved.id
    read = tools.execute(call("read_memory", kind="fact", ids=[saved.id]))
    assert read.items[0]["content"] == "学习Python"
    assert "owner" not in read.items[0]


def test_scope_cannot_be_model_selected(repo):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        call("search_memory", query="Python", owner="bob")


def test_unknown_id_path_and_sql_rejected(repo):
    tools = tools_for(repo)
    with pytest.raises(MemoryFailure):
        tools.execute(call("read_memory", kind="fact", ids=["../../state.sqlite"]))


def test_4000_byte_result_is_utf8_valid_and_marked(repo):
    for i in range(10):
        repo.save_manual("alice", write("数学" * 200, entity=str(i)), ("openai",))
    result = tools_for(repo).execute(call("search_memory", query="数学", limit=10))
    assert len(json.dumps(result.items, ensure_ascii=False).encode()) <= 4000
    assert result.coverage == "truncated" and result.next_cursor


def test_paused_temporary_return_no_content(repo):
    saved = repo.save_manual("alice", write("数学"), ("openai",))
    with pytest.raises(MemoryFailure):
        tools_for(repo, mode="temporary").execute(call("read_memory", kind="fact", ids=[saved.id]))
    with pytest.raises(MemoryFailure):
        tools_for(repo, provider="deepseek").execute(call("read_memory", kind="fact", ids=[saved.id]))
    repo.set_enabled("alice", False, repo.settings("alice").revision)
    with pytest.raises(MemoryFailure):
        tools_for(repo).execute(call("search_memory", query="数学"))


def test_long_topic_can_be_read_completely_through_continuation(repo):
    from gaia_agent.interactive.memory.layer_models import FactRef, SummaryDraft
    from gaia_agent.interactive.memory.summaries import SummaryStore
    fact = repo.save_manual("alice", write("数学"), ("openai",))
    state = repo.state_snapshot("alice", "openai")
    summary = SummaryStore(repo).publish(state.scope, SummaryDraft("topic", "math", "数学" * 600, (FactRef(fact.id, 1),)), state.content_revision)
    tools, cursor, chunks = tools_for(repo), None, []
    while True:
        result = tools.execute(call("read_memory", kind="topic", ids=[summary.id], cursor=cursor))
        assert len(__import__("gaia_agent.interactive.memory.tools", fromlist=["result_json"]).result_json(result).encode()) <= 4000
        chunks.extend(item["content"] for item in result.items)
        cursor = result.next_cursor
        if not cursor:
            break
    assert "".join(chunks) == "数学" * 600


def test_search_finds_model_topic_even_when_fact_uses_other_word(repo):
    from gaia_agent.interactive.memory.layer_models import FactRef, SummaryDraft
    from gaia_agent.interactive.memory.summaries import SummaryStore
    fact = repo.save_manual("alice", write("我学习数学"), ("openai",))
    state = repo.state_snapshot("alice", "openai")
    summary = SummaryStore(repo).publish(state.scope, SummaryDraft("topic", "math", "数学背景与代数学习", (FactRef(fact.id, 1),)), state.content_revision)
    result = tools_for(repo).execute(call("search_memory", query="代数"))
    assert result.items[0]["id"] == summary.id
    assert result.items[0]["kind"] == "topic"


def test_cross_provider_search_reads_shared_fact_and_delete_removes_both(repo):
    generation=repo.settings('alice').generation
    repo.register_turn('alice','cross-run')
    identifier=repo.apply_candidates('alice','openai','cross-run',(write('学习 Python'),),generation)[0]
    tools=tools_for(repo,provider='deepseek')
    assert any(item['id']==identifier for item in tools.execute(call('search_memory',query='Python')).items)
    assert tools.execute(call('read_memory',kind='fact',ids=[identifier])).items[0]['content']=='学习 Python'
    item=repo.get('alice',identifier)
    repo.delete('alice',identifier,item.revision,permanent=True)
    assert not tools_for(repo,'openai').execute(call('search_memory',query='Python')).items
    assert not tools_for(repo,'deepseek').execute(call('search_memory',query='Python')).items
