# ruff: noqa: F811 -- shared pytest fixture imported from repository tests
import pytest
from test_memory_repository import repo, write  # noqa: F401

from gaia_agent.interactive.memory.models import MemoryFailure


def store_and_scope(repo):
    from gaia_agent.interactive.memory.summaries import SummaryStore
    return SummaryStore(repo), repo.state_snapshot("alice", "openai")


def draft(item, kind="topic", children=()):
    from gaia_agent.interactive.memory.layer_models import FactRef, SummaryDraft
    return SummaryDraft(kind, "math", "用户学习数学。", (FactRef(item.id, item.revision),), children)


def test_summary_never_overwrites_facts(repo):
    item = repo.save_manual("alice", write("我学习数学"), ("openai",))
    store, state = store_and_scope(repo)
    view = store.publish(state.scope, draft(item), state.content_revision)
    assert view.content == "用户学习数学。"
    assert repo.get("alice", item.id).content == "我学习数学"


def test_delete_during_generation_rejects_publish(repo):
    item = repo.save_manual("alice", write("我学习数学"), ("openai",))
    store, state = store_and_scope(repo)
    repo.delete("alice", item.id, 1, permanent=True)
    with pytest.raises(MemoryFailure, match="memory_conflict"):
        store.publish(state.scope, draft(item), state.content_revision)


def test_provider_shrink_invalidates_parent_overview(repo):
    from gaia_agent.interactive.memory.layer_models import FactRef
    item = repo.save_manual("alice", write("我学习数学"), ("openai", "deepseek"))
    store, state = store_and_scope(repo)
    child = store.publish(state.scope, draft(item), state.content_revision)
    parent = store.publish(state.scope, draft(item, "overview", (FactRef(child.id, 1),)), state.content_revision)
    assert store.overview(state.scope).id == parent.id
    repo.update("alice", item.id, {"allowed_providers": ("deepseek",)}, 1)
    assert store.overview(repo.state_snapshot("alice", "openai").scope) is None
    assert repo.db.execute("SELECT COUNT(*) FROM memory_summaries WHERE state='ready'").fetchone()[0] == 0


def test_unknown_cross_account_dependencies_rejected(repo):
    item = repo.save_manual("bob", write("private"), ("openai",))
    store, state = store_and_scope(repo)
    with pytest.raises(MemoryFailure):
        store.publish(state.scope, draft(item), state.content_revision)


def test_unrelated_fact_does_not_invalidate_summary(repo):
    a = repo.save_manual("alice", write("我学习数学", entity="math"), ("openai",))
    b = repo.save_manual("alice", write("我喜欢茶", entity="tea"), ("openai",))
    store, state = store_and_scope(repo)
    view = store.publish(state.scope, draft(a), state.content_revision)
    repo.update("alice", b.id, {"content": "我喜欢咖啡"}, 1)
    assert store.read(repo.state_snapshot("alice", "openai").scope, view.id).content == "用户学习数学。"


def test_summary_cannot_store_hallucinated_secret(repo):
    from dataclasses import replace
    item = repo.save_manual("alice", write("我学习数学"), ("openai",))
    store, state = store_and_scope(repo)
    with pytest.raises(MemoryFailure, match="memory_invalid"):
        store.publish(state.scope, replace(draft(item), content="密码是 secret-value"), state.content_revision)
    assert repo.db.execute("SELECT COUNT(*) FROM memory_summaries").fetchone()[0] == 0


def test_migration_invalidates_old_summary_and_indexes_new_provider(repo):
    from gaia_agent.interactive.memory.index import MemoryIndex
    item=repo.save_manual('alice',write('I study mathematics'),('openai',))
    store,state=store_and_scope(repo)
    old=store.publish(state.scope,draft(item),state.content_revision)
    with repo.lock,repo.db:
        repo.db.execute('DELETE FROM memory_shared_migration WHERE owner=?',('alice',))
    state2=repo.state_snapshot('alice','deepseek')
    assert repo.db.execute('select state from memory_summaries where id=?',(old.id,)).fetchone()[0]=='stale'
    assert any(x.id==item.id for x in MemoryIndex(repo).search(state2.scope,'mathematics').items)
    assert repo.db.execute('select count(*) from memory_topic_members where owner=? and provider=? and fact_id=?',('alice','deepseek',item.id)).fetchone()[0]==1
