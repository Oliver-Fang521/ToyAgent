from types import SimpleNamespace

import pytest

from gaia_agent.interactive.streaming import PartialDecision, answer_stream
from gaia_agent.providers.openai import OpenAIProvider
from gaia_agent.reliability import RetryPolicy


def test_stream_updates_classify_answer_tools_verification_and_terminal():
    from gaia_agent.interactive.streaming import classify_run_updates

    previous = {"status": "running", "progress": [], "partial_answer": "Hi"}
    current = {"status": "running", "progress": [{"event": "tool_call_started", "tool": "web_search"}, {"event": "verify_started"}], "partial_answer": "Hi there"}
    assert classify_run_updates(previous, current) == ["answer.updated", "tool.started", "verification.started"]
    assert classify_run_updates(current, {"status": "failed", "progress": current["progress"]}) == ["answer.updated", "run.failed"]


def test_run_events_emit_deltas_and_individual_progress_without_drafts():
    from gaia_agent.interactive.streaming import run_event_updates

    old = {"status": "running", "progress": [], "partial_answer": "Hi"}
    new = {"status": "running", "progress": [{"event": "tool_call_started", "tool": "web_search"}, {"event": "verify_started"}], "partial_answer": "Hi there"}
    assert run_event_updates(old, new) == [
        ("answer.delta", {"delta": " there"}),
        ("tool.started", {"event": "tool_call_started", "tool": "web_search"}),
        ("verification.started", {"event": "verify_started"}),
    ]
    assert run_event_updates(new, {"status": "running", "progress": new["progress"], "partial_answer": "retry"}) == [("answer.reset", {"text": "retry"})]
    assert run_event_updates(new, {"status": "completed", "progress": new["progress"], "result": {"answer": "verified"}})[-1][0] == "run.completed"


def test_partial_decision_only_emits_direct_public_answer_and_handles_json_escapes():
    seen=[]
    parser=PartialDecision(seen.append)
    for part in ['{"action":"decide","tool_calls":[],"decision":{"route":"direct",', '"needs_verification":false,"answer":"你好', '\\n世', '界"}}']:
        parser.feed(part)
    assert seen==["你好","你好\n世","你好\n世界"]
    for text in ['{"route":"research","needs_verification":true,"answer":"secret"}', '{"action":"tools","tool_calls":[],"decision":{"route":"direct","needs_verification":false,"answer":"secret"}}']:
        output=[]
        PartialDecision(output.append).feed(text)
        assert output==[]


def test_partial_redaction_hides_secret_even_when_it_arrives_in_fragments():
    from gaia_agent.interactive.credential_runtime import SafePublicProvider
    provider=SafePublicProvider(SimpleNamespace(supports_vision=False),["secret-token"])
    assert provider.sanitize_partial("hello secret-")=="hello "
    assert provider.sanitize_partial("hello secret-token") == "hello [REDACTED]"


@pytest.mark.asyncio
async def test_provider_delivers_model_deltas_before_completion():
    seen=[]
    completed=SimpleNamespace(id="done")
    class Stream:
        async def __aiter__(self):
            yield SimpleNamespace(type="response.output_text.delta",delta='{"route":"direct","needs_verification":false,"answer":"Hello')
            assert seen==["", "Hello"]
            yield SimpleNamespace(type="response.output_text.delta",delta=' world"}')
            yield SimpleNamespace(type="response.completed",response=completed)
        async def close(self): pass
    class Responses:
        async def create(self,**kwargs):
            assert kwargs["stream"] is True
            return Stream()
    class Client:
        responses=Responses()
        def with_options(self,**kwargs): return self
    provider=OpenAIProvider(Client(),model="synthetic",retry_policy=RetryPolicy(max_attempts=1))
    with answer_stream(seen.append):
        result=await provider._create_with_retry(input=[])
    assert result is completed and seen[-1]=="Hello world"


@pytest.mark.asyncio
async def test_routing_streams_real_provider_answer_without_waiting_for_final_response():
    from gaia_agent.interactive.routing import route_request
    output='{"route":"direct","needs_verification":false,"answer":"Hello world"}'
    content=SimpleNamespace(type="output_text",text=output,annotations=[])
    completed=SimpleNamespace(id="done",status="completed",incomplete_details=None,usage=None,output=[SimpleNamespace(type="message",content=[content])])
    seen=[]
    class Stream:
        async def __aiter__(self):
            yield SimpleNamespace(type="response.output_text.delta",delta=output[:-7])
            assert seen[-1]=="Hello "
            yield SimpleNamespace(type="response.output_text.delta",delta=output[-7:])
            yield SimpleNamespace(type="response.completed",response=completed)
        async def close(self): pass
    async def self_create(**kwargs): return Stream()
    class Client:
        responses=SimpleNamespace(create=self_create)
        def with_options(self,**kwargs): return self
    provider=OpenAIProvider(Client(),model="synthetic",retry_policy=RetryPolicy(max_attempts=1))
    with answer_stream(seen.append):
        decision,_=await route_request(provider,"hello","[]","low")
    assert decision.answer=="Hello world" and seen[-1]=="Hello world"


@pytest.mark.asyncio
async def test_event_stream_emits_live_preview_and_rejects_other_owner(tmp_path):
    import asyncio
    import json

    from gaia_agent.interactive.api import create_app
    from gaia_agent.interactive.models import InteractiveRequest, InteractiveResult
    from gaia_agent.interactive.store import ServiceError
    from gaia_agent.models import Usage

    emitted=asyncio.Event()
    release=asyncio.Event()
    async def execute(request,workspace,history,progress,attachment):
        progress("answer_preview",{"text":"first"})
        emitted.set()
        await release.wait()
        progress("answer_preview",{"text":"first second"})
        await asyncio.sleep(.25)
        return InteractiveResult(answer="first second",evidence=[],usage=Usage())
    app=create_app(tmp_path,"test-service-token",execute=execute)
    service=app.state.service
    cid=service.store.create("alice","openai")["id"]
    run=service.start("alice",cid,InteractiveRequest(provider="openai",model="gpt-6.1-sol",question="hello"),"synthetic")
    await asyncio.wait_for(emitted.wait(),2)
    route=next(route for route in app.routes if getattr(route,"path",None)=="/v1/runs/{identifier}/events")
    with pytest.raises(ServiceError):
        await route.endpoint(identifier=run["id"],user="bob")
    response=await route.endpoint(identifier=run["id"],user="alice")
    first=await anext(response.body_iterator)
    assert first.startswith("event: run.snapshot\n")
    assert json.loads(first.split("data: ",1)[1])["partial_answer"]=="first"
    assert service.store.run("alice",run["id"])["status"]=="running"
    release.set()
    second=await asyncio.wait_for(anext(response.body_iterator),2)
    assert second.startswith("event: answer.delta\n")
    assert json.loads(second.split("data: ",1)[1])["delta"]==" second"
    final=await asyncio.wait_for(anext(response.body_iterator),2)
    assert final.startswith("event: answer.reset\n")
    final=await asyncio.wait_for(anext(response.body_iterator),2)
    assert final.startswith("event: run.completed\n")
    assert json.loads(final.split("data: ",1)[1])["status"]=="completed"
    assert run["id"] not in service.answer_previews
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_http_event_stream_flushes_before_run_completes(tmp_path):
    import asyncio
    import json
    import socket

    import httpx
    import uvicorn

    from gaia_agent.interactive.api import create_app
    from gaia_agent.interactive.models import InteractiveResult
    from gaia_agent.models import Usage

    ready, release = asyncio.Event(), asyncio.Event()
    async def execute(request,workspace,history,progress,attachment):
        progress("answer_preview",{"text":"partial"})
        ready.set()
        await release.wait()
        return InteractiveResult(answer="complete",evidence=[],usage=Usage())
    app=create_app(tmp_path,"test-service-token",execute=execute)
    sock=socket.socket()
    sock.bind(("127.0.0.1",0))
    sock.listen(10)
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level="error",lifespan="on"))
    task=asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(200):
            if server.started: break
            await asyncio.sleep(.01)
        assert server.started
        headers={"authorization":"Bearer test-service-token","x-agent-user":"alice"}
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",headers=headers,timeout=3,trust_env=False) as client:
            cid=(await client.post("/v1/conversations",json={"provider":"openai"})).json()["id"]
            run=(await client.post(f"/v1/conversations/{cid}/runs",headers={"idempotency-key":"stream-test"},json={"provider":"openai","model":"gpt-6.1-sol","question":"hello"})).json()
            await asyncio.wait_for(ready.wait(),2)
            async with client.stream("GET",f"/v1/runs/{run['id']}/events") as response:
                assert response.status_code==200
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        first=json.loads(line[6:])
                        break
                assert first["status"]=="running" and first["partial_answer"]=="partial"
                release.set()
    finally:
        release.set()
        server.should_exit=True
        await asyncio.wait_for(task,5)
        sock.close()


def test_research_draft_streams_only_top_level_candidate_field():
    from gaia_agent.interactive.streaming import PartialCandidate
    outputs=[]
    parser=PartialCandidate(outputs.append)
    parser.feed('{"specialist":"file","evidence":[{"summary":"candidate_answer: private"}],"candidate_answer":"答案')
    assert outputs == ['答案']
    parser.feed('一\\n第二行"}')
    assert outputs[-1] == '答案一\n第二行'
    assert all('private' not in value for value in outputs)


def test_research_draft_waits_for_complete_escape_and_ignores_null():
    from gaia_agent.interactive.streaming import PartialCandidate
    outputs=[]
    parser=PartialCandidate(outputs.append)
    parser.feed('{"specialist":"file","evidence":[],"candidate_answer":null}')
    assert outputs == []
    parser=PartialCandidate(outputs.append)
    parser.feed('{"candidate_answer":"ok\\u4e')
    assert outputs == ['ok']
    parser.feed('2d"}')
    assert outputs[-1] == 'ok中'


@pytest.mark.asyncio
async def test_provider_streams_candidate_before_specialist_completes():
    seen=[]
    completed=SimpleNamespace(id='done')
    class Stream:
        async def __aiter__(self):
            yield SimpleNamespace(type='response.output_text.delta',delta='{"specialist":"file","evidence":[],"candidate_answer":"草稿')
            assert seen[-1]=='草稿'
            yield SimpleNamespace(type='response.output_text.delta',delta='正文"}')
            yield SimpleNamespace(type='response.completed',response=completed)
        async def close(self): pass
    class Responses:
        async def create(self,**kwargs): return Stream()
    class Client:
        responses=Responses()
        def with_options(self,**kwargs): return self
    provider=OpenAIProvider(Client(),model='synthetic',retry_policy=RetryPolicy(max_attempts=1))
    with answer_stream(seen.append, mode='candidate'):
        await provider._create_with_retry(input=[])
    assert seen[-1]=='草稿正文'
