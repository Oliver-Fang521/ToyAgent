# ruff: noqa: F811 -- shared fixture
from contextlib import asynccontextmanager

import pytest
from test_memory_repository import repo, write  # noqa: F401

from gaia_agent.interactive.memory.models import MemoryFailure
from gaia_agent.models import Usage
from gaia_agent.providers import ModelResponse


def queue_for(repo):
    from gaia_agent.interactive.memory.maintenance_queue import MaintenanceQueue
    return MaintenanceQueue(repo)


def dirty(repo, q, topic="topic", now=0):
    state = repo.state_snapshot("alice", "openai")
    q.mark_dirty(state.scope, {"model": "test-model", "reasoning_effort": "low", "capability_version": "test"}, 0, (topic,), now=now)


def test_debounce_exclusive_claim_and_hourly_dispatch_budget(repo):
    q = queue_for(repo)
    for n in range(5):
        dirty(repo, q, str(n))
    assert q.claim(9, "worker") is None
    for n in range(4):
        claim = q.claim(10, "worker")
        assert claim is not None
        assert q.claim(10, "other") is None
        q.reserve_dispatch(claim, now=10)
        q.finish(claim, state="completed")
    assert q.claim(10, "worker") is None
    assert q.claim(3611, "worker") is not None


def test_dispatch_uncertainty_never_automatically_repeats(repo):
    q = queue_for(repo)
    dirty(repo, q)
    claim = q.claim(10, "worker")
    q.reserve_dispatch(claim, now=10)
    q.recover()
    assert q.claim(20, "new-worker") is None
    with pytest.raises(MemoryFailure):
        q.retry("alice", claim.id, False)
    q.retry("alice", claim.id, True)
    retry = q.claim(4000, "new-worker")
    assert retry is not None
    q.reserve_dispatch(retry, now=4000)
    q.finish(retry, state="failed", code="memory_timeout")
    with pytest.raises(MemoryFailure):
        q.retry("alice", claim.id, True)


def test_unrelated_revision_rebases_pending_job_before_dispatch(repo):
    repo.save_manual("alice", write("math", entity="a"), ("openai",))
    q = queue_for(repo)
    dirty(repo, q, "a")
    repo.save_manual("alice", write("database", entity="b"), ("openai",))
    dirty(repo, q, "b", now=1)
    first = q.claim(10, "worker")
    q.reserve_dispatch(first, now=10)
    assert first.input_revision == repo.state_snapshot("alice", "openai").content_revision


def test_same_topic_change_preserves_active_lease_and_queues_successor(repo):
    repo.save_manual("alice", write("math", entity="a"), ("openai",))
    q = queue_for(repo)
    dirty(repo, q, "a")
    first = q.claim(10, "worker")
    q.reserve_dispatch(first, now=10)
    repo.save_manual("alice", write("database", entity="b"), ("openai",))
    dirty(repo, q, "a", now=11)
    assert q.claim(22, "other") is None
    q.finish(first, state="failed", code="memory_conflict", now=22)
    successor = q.claim(32, "other")
    assert successor is not None
    assert successor.input_revision == repo.state_snapshot("alice", "openai").content_revision
    q.reserve_dispatch(successor, now=32)
    assert repo.db.execute("SELECT COUNT(*) FROM memory_maintenance_dispatch").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_model_summary_has_source_checks_and_output_budget(repo):
    from gaia_agent.interactive.memory.summary_planner import SummaryPlanner
    item = repo.save_manual("alice", write("我学习数学"), ("openai",))
    q = queue_for(repo)
    dirty(repo, q)
    claim = q.claim(10, "worker")
    class Provider:
        async def complete(self, request, executor=None):
            assert request.max_output_tokens == 1000
            assert request.reasoning_effort == "low"
            assert "我学习数学" in str(request.input_items)
            return ModelResponse(response_status="completed", parsed={"content": "用户学习数学。", "source_ids": [item.id]}, usage=Usage())
    result = await SummaryPlanner(Provider()).summarize(claim, (repo.get("alice", item.id),), usage_observer=lambda _: None)
    assert result.content == "用户学习数学。"
    assert result.source_refs[0].id == item.id


@pytest.mark.asyncio
async def test_summary_evidence_preserves_entity_and_topic_scope(repo):
    import json

    from gaia_agent.interactive.memory.summary_planner import SummaryPlanner
    items = [repo.save_manual("alice", write("Use PostgreSQL", entity=entity), ("openai",)) for entity in ("Orion", "Vega")]
    q = queue_for(repo)
    dirty(repo, q)
    claim = q.claim(10, "worker")
    class Provider:
        async def complete(self, request, executor=None):
            body = json.loads(request.input_items[0]["content"])["evidence"]
            assert {row['entity'] for row in body} == {"Orion", "Vega"}
            assert all(row['category'] == 'profile' and row['topic'] == 'learning_subjects' for row in body)
            assert all(row['edited_by_user'] is True for row in body)
            return ModelResponse(response_status="completed", parsed={"content": "Orion and Vega use PostgreSQL", "source_ids": [item.id for item in items]})
    await SummaryPlanner(Provider()).summarize(claim, tuple(repo.get("alice", item.id) for item in items), usage_observer=lambda _: None)


@pytest.mark.asyncio
async def test_revoked_credential_prevents_model_dispatch(repo):
    from gaia_agent.interactive.memory.maintenance import MemoryMaintenanceWorker
    q = queue_for(repo)
    dirty(repo, q)
    called = []
    @asynccontextmanager
    async def factory(claim):
        called.append(1)
        yield None
    worker = MemoryMaintenanceWorker(q, factory, credential_check=lambda _: False)
    await worker.run_once(now=10)
    assert called == []
    assert q.status("alice")[0]["state"] == "blocked"


@pytest.mark.asyncio
async def test_worker_builds_shard_topic_and_overview_without_changing_fact(repo):
    from gaia_agent.interactive.memory.maintenance import MemoryMaintenanceWorker
    from gaia_agent.interactive.memory.summaries import SummaryStore
    from gaia_agent.interactive.memory.summary_planner import SummaryPlanner
    item = repo.save_manual("alice", write("我学习数学"), ("openai",))
    topic = repo.db.execute("SELECT topic_id FROM memory_topic_members WHERE fact_id=?", (item.id,)).fetchone()
    assert topic is not None
    q = queue_for(repo)
    dirty(repo, q, topic[0])
    class Provider:
        async def complete(self, request, executor=None):
            import json
            entries = json.loads(request.input_items[0]["content"])["evidence"]
            return ModelResponse(response_status="completed", parsed={"content": "用户学习数学。", "source_ids": [r["id"] for r in entries]})
    @asynccontextmanager
    async def factory(claim):
        yield SummaryPlanner(Provider())
    worker = MemoryMaintenanceWorker(q, factory, credential_check=lambda _: True)
    for _ in range(3):
        await worker.run_once(now=10)
    assert q.status("alice")[0]["state"] == "completed"
    assert SummaryStore(repo).overview(repo.state_snapshot("alice", "openai").scope).content == "用户学习数学。"
    assert repo.get("alice", item.id).content == "我学习数学"
    assert repo.db.execute("SELECT COUNT(*) FROM memory_maintenance_dispatch").fetchone()[0] == 3


@pytest.mark.asyncio
async def test_timeout_after_dispatch_is_recorded_without_automatic_retry(repo):
    from gaia_agent.interactive.memory.maintenance import MemoryMaintenanceWorker
    item = repo.save_manual("alice", write("我学习数学"), ("openai",))
    topic = repo.db.execute("SELECT topic_id FROM memory_topic_members WHERE fact_id=?", (item.id,)).fetchone()[0]
    q = queue_for(repo)
    dirty(repo, q, topic)
    class Planner:
        async def summarize(self, *args, **kwargs):
            raise TimeoutError
    @asynccontextmanager
    async def factory(claim):
        yield Planner()
    worker = MemoryMaintenanceWorker(q, factory, credential_check=lambda _: True)
    await worker.run_once(now=10)
    await worker.run_once(now=100)
    assert q.status("alice")[0]["failure_code"] == "memory_timeout"
    assert repo.db.execute("SELECT COUNT(*) FROM memory_maintenance_dispatch").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_large_topic_is_split_before_model_input_limit(repo):
    from gaia_agent.interactive.memory.maintenance import MemoryMaintenanceWorker
    # Different entities normally define distinct topics; use one membership scope to model a large topic.
    ids = [repo.save_manual("alice", write("数学" * 240, entity=str(i)), ("openai",)).id for i in range(20)]
    topic = "large-topic"
    with repo.db:
        repo.db.execute("UPDATE memory_topic_members SET topic_id=?", (topic,))
    q = queue_for(repo)
    dirty(repo, q, topic)
    claim = q.claim(10, "worker")
    worker = MemoryMaintenanceWorker(q, None, credential_check=lambda _: True)
    inputs, continuation = worker._inputs(claim)
    assert 0 < len(inputs) < 20
    assert continuation["phase"] == "shard" and continuation["offset"] == len(inputs)
    assert {item.id for item in inputs} <= set(ids)


def test_overview_batches_all_ready_topics_without_silent_truncation(repo):
    from gaia_agent.interactive.memory.layer_models import FactRef, SummaryDraft
    from gaia_agent.interactive.memory.maintenance import MemoryMaintenanceWorker
    from gaia_agent.interactive.memory.summaries import SummaryStore
    items=[repo.save_manual("alice", write(f"fact{i}", entity=str(i)), ("openai",)) for i in range(12)]
    state=repo.state_snapshot("alice", "openai")
    store=SummaryStore(repo)
    for item in items:
        store.publish(state.scope, SummaryDraft("topic", item.id, item.content, (FactRef(item.id,item.revision),)), state.content_revision)
    q=queue_for(repo)
    dirty(repo,q)
    claim=q.claim(10,"worker")
    claim.cursor.update(phase="overview",offset=0)
    inputs,next_data=MemoryMaintenanceWorker(q,None,credential_check=lambda _:True)._inputs(claim)
    assert len(inputs)==8
    assert claim.cursor["phase"]=="overview_part"
    assert next_data["phase"]=="overview_scan" and next_data["offset"]==8
    assert repo.db.execute("SELECT COUNT(*) FROM memory_overview_inputs WHERE job_id=?",(claim.id,)).fetchone()[0]==12


@pytest.mark.asyncio
async def test_complete_overview_preserves_all_twelve_topic_dependencies(repo):
    import json

    from gaia_agent.interactive.memory.layer_models import FactRef, SummaryDraft
    from gaia_agent.interactive.memory.maintenance import MemoryMaintenanceWorker
    from gaia_agent.interactive.memory.summaries import SummaryStore
    from gaia_agent.interactive.memory.summary_planner import SummaryPlanner
    items=[repo.save_manual("alice",write(f"fact{i}",entity=str(i)),("openai",)) for i in range(12)]
    state=repo.state_snapshot("alice","openai")
    store=SummaryStore(repo)
    topics=[store.publish(state.scope,SummaryDraft("topic",item.id,item.content,(FactRef(item.id,item.revision),)),state.content_revision) for item in items]
    q=queue_for(repo)
    dirty(repo,q)
    row=repo.db.execute("SELECT * FROM memory_maintenance").fetchone()
    data=q._data(row)
    data.update(phase="overview",offset=0)
    with repo.db: q._write(row["id"],"alice",data)
    class Provider:
        async def complete(self,request,executor=None):
            evidence=json.loads(request.input_items[0]["content"])["evidence"]
            return ModelResponse(response_status="completed",parsed={"content":" | ".join(x["content"] for x in evidence),"source_ids":[x["id"] for x in evidence]})
    @asynccontextmanager
    async def factory(claim): yield SummaryPlanner(Provider())
    worker=MemoryMaintenanceWorker(q,factory,credential_check=lambda _:True)
    for i in range(10):
        await worker.run_once(now=10+i*3601)
        if q.status("alice")[0]["state"]=="completed": break
    assert q.status("alice")[0]["state"]=="completed"
    overview=store.overview(state.scope)
    assert overview is not None
    def leaves(view):
        return {ref.id for ref in view.child_refs if ref.id in {topic.id for topic in topics}} | set().union(*(leaves(store.read(state.scope,ref.id)) for ref in view.child_refs if ref.id not in {topic.id for topic in topics}))
    assert leaves(overview)=={topic.id for topic in topics}
    repo.delete("alice",items[0].id,items[0].revision)
    assert store.overview(repo.state_snapshot("alice","openai").scope) is None
