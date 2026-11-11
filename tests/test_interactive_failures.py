import json

import httpx
import pytest
from openai import RateLimitError

from gaia_agent.interactive.credential_runtime import SafePublicProvider
from gaia_agent.interactive.credential_vault import CredentialError
from gaia_agent.interactive.failures import failure_details, safe_diagnostic
from gaia_agent.interactive.models import InteractiveRequest
from gaia_agent.interactive.service import RunService
from gaia_agent.interactive.store import ConversationStore


def test_validation_reason_survives_storage_without_raw_feedback(tmp_path):
    store = ConversationStore(tmp_path / "reason.sqlite")
    cid = store.create("alice", "openai")["id"]
    run = store.reserve(
        "alice",
        cid,
        InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="q"),
        "reason",
    )
    store.progress(
        run["id"],
        "validation_rejected",
        {
            "reason": "insufficient_evidence",
            "feedback": "private-secret",
        },
    )
    events = store.run("alice", run["id"])["progress"]
    failure = failure_details("critic_rejection", events)
    assert failure["reason"] == "insufficient_evidence"
    assert "private-secret" not in json.dumps(events)
    store.progress(run["id"], "validation_rejected", {"reason": "private-secret"})
    assert "private-secret" not in json.dumps(store.run("alice", run["id"]))
    store.db.close()


@pytest.mark.asyncio
async def test_provider_failure_retains_safe_diagnostics_not_secret(caplog):
    secret = "synthetic-error-private-72191"
    error = RateLimitError(
        secret,
        response=httpx.Response(
            429,
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
            headers={"x-request-id": "req_test123"},
        ),
        body={"code": "insufficient_quota", "message": secret},
    )

    class Provider:
        supports_vision = True

        async def complete(self, *args, **kwargs):
            raise error

    provider = SafePublicProvider(Provider(), [secret])
    with pytest.raises(CredentialError) as caught:
        await provider.complete(None)
    diagnostic = caught.value.diagnostic
    assert diagnostic == {
        "exception_type": "RateLimitError",
        "http_status": 429,
        "upstream_code": "insufficient_quota",
        "request_id": "req_test123",
    }
    assert secret not in json.dumps(diagnostic) + caplog.text
    assert safe_diagnostic(RuntimeError(secret)) == {"exception_type": "RuntimeError"}


@pytest.mark.asyncio
async def test_real_runtime_preserves_tool_failure_category(tmp_path, monkeypatch):
    from pydantic import SecretStr

    import gaia_agent.interactive.credential_runtime as module
    from gaia_agent.interactive.composition import InteractiveFailure
    from gaia_agent.interactive.credential_crypto import KeyCipher
    from gaia_agent.interactive.credential_sessions import CredentialSessions
    from gaia_agent.interactive.credential_vault import CredentialMaterial, CredentialVault

    store = ConversationStore(tmp_path / "state.sqlite")
    sessions = CredentialSessions(
        CredentialVault(store.db, KeyCipher({"v1": b"a" * 32}, "v1"), store.lock)
    )
    handle = sessions.issue(
        "alice", CredentialMaterial("openai", SecretStr("synthetic-tool-runtime-4791"))
    )

    async def fake_execution(request, config, workspace, history, progress, **kwargs):
        progress("act_started", {})
        progress("tool_call_failed", {"tool": "execute_python"})
        raise InteractiveFailure("retry_exhausted")

    monkeypatch.setattr(module, "execute_interactive", fake_execution)
    runtime = module.UserRuntime(
        module.InteractiveRuntimeSettings(base_url="https://api.openai.com/v1"), sessions
    )
    service = RunService(store, tmp_path / "runs", runtime.execute, credential_sessions=sessions)
    cid = store.create("alice", "openai")["id"]
    run = service.start(
        "alice",
        cid,
        InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="q"),
        "tool",
        credential_ref=sessions.reference("alice", "openai", handle),
    )
    await service.tasks[run["id"]]
    failure = service.get("alice", run["id"])["failure"]
    assert failure["component"] == "tool" and failure["tool"] == "execute_python"
    await service.shutdown()
    store.db.close()


@pytest.mark.asyncio
async def test_failure_stage_persists_and_reopen_preserves_records(tmp_path):
    path = tmp_path / "state.sqlite"
    store = ConversationStore(path)

    async def execute(request, workspace, history, progress, attachment):
        progress("plan_started", {})
        progress("verify_started", {})
        progress("act_started", {})
        raise RuntimeError("synthetic-hidden-private-91274")

    service = RunService(store, tmp_path / "runs", execute)
    cid = store.create("alice", "openai")["id"]
    run = service.start(
        "alice",
        cid,
        InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="q"),
        "once",
    )
    await service.tasks[run["id"]]
    failed = service.get("alice", run["id"])
    assert failed["failure"]["stage"] == "research"
    assert failed["failure"]["code"] == "execution_failed"
    assert failed["failure"]["diagnostic"] == {"exception_type": "RuntimeError"}
    assert "synthetic-hidden" not in json.dumps(failed)
    reopened = ConversationStore(path)
    assert reopened.run("alice", run["id"])["failure"] == failed["failure"]
    assert reopened.get("alice", cid)["id"] == cid
    reopened.db.close()
    await service.shutdown()
    store.db.close()


def test_tool_failure_is_named_but_recovered_tools_do_not_blame_later_model():
    events = [{"event": "act_started"}, {"event": "tool_call_failed", "tool": "web_search"}]
    assert failure_details("retry_exhausted", events)["tool"] == "web_search"
    assert failure_details("retry_exhausted", events)["component"] == "tool"
    assert failure_details("provider_unavailable", events)["component"] == "model"
    events.append({"event": "tool_call_completed", "tool": "web_search"})
    assert failure_details("execution_failed", events)["component"] == "execution"


def test_malformed_failure_exposes_only_safe_stage_diagnostic():
    events = [{"event":"act_started"}, {"event":"stage_response_diagnostic", "stage":"specialist", "phase":"initial", "code":"schema", "response_status":"incomplete", "incomplete_reason":"max_output_tokens"}, {"event":"stage_response_diagnostic", "stage":"specialist", "phase":"repair", "code":"candidate_format", "response_status":"completed", "incomplete_reason":"none", "secret":"never expose"}]
    diagnostic = failure_details("malformed_response", events)["diagnostic"]
    assert diagnostic == {"phase":"repair", "validation_code":"candidate_format", "response_status":"completed", "incomplete_reason":"none"}
    assert "secret" not in str(diagnostic)


def test_model_diagnostic_is_saved_without_raw_payload(tmp_path):
    from gaia_agent.interactive.models import InteractiveRequest

    store = ConversationStore(tmp_path / "state.sqlite")
    cid = store.create("alice", "openai")["id"]
    run = store.reserve("alice", cid, InteractiveRequest(provider="openai",model="gpt-6.1-sol",question="question"), "request")
    store.progress(run["id"], "act_started", {})
    store.progress(run["id"], "stage_response_diagnostic", {"stage":"specialist","phase":"repair","code":"schema","response_status":"incomplete","incomplete_reason":"max_output_tokens","raw":"private"})
    events = store.run("alice", run["id"])["progress"]
    assert failure_details("malformed_response", events)["diagnostic"]["validation_code"] == "schema"
    assert "private" not in json.dumps(events)
    store.db.close()


@pytest.mark.asyncio
async def test_successful_model_retry_clears_old_failure_diagnostic():
    from gaia_agent.providers.base import ModelResponse

    class Provider:
        supports_vision = True
        calls = 0

        async def complete(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("synthetic-private-error-7791")
            return ModelResponse(output_text="ok")

    provider = SafePublicProvider(Provider())
    with pytest.raises(CredentialError):
        await provider.complete(None)
    await provider.complete(None)
    assert provider.failure_code is None
    assert provider.failure_diagnostic is None


def test_legacy_failure_and_tool_identity_survive_api_reload(tmp_path):
    from fastapi.testclient import TestClient

    from gaia_agent.interactive.api import create_app

    headers = {"authorization": "Bearer test-service-token", "x-agent-user": "alice"}
    with TestClient(
        create_app(tmp_path, "test-service-token", execute=lambda *args: None)
    ) as client:
        store = client.app.state.service.store
        cid = store.create("alice", "openai")["id"]
        run = store.reserve(
            "alice",
            cid,
            InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="q"),
            "legacy",
        )
        store.progress(run["id"], "act_started", {})
        store.progress(
            run["id"],
            "tool_call_failed",
            {"tool": "web_search", "arguments": {"key": "synthetic-private-84419"}},
        )
        store.update(run["id"], status="failed", error="retry_exhausted")
        response = client.get(f"/v1/conversations/{cid}", headers=headers)
        failure = response.json()["latest_run"]["failure"]
        assert failure["stage"] == "research" and failure["tool"] == "web_search"
        assert "synthetic-private-84419" not in response.text
        assert (
            client.get(
                f"/v1/conversations/{cid}", headers={**headers, "x-agent-user": "bob"}
            ).status_code
            == 404
        )


def test_tool_provider_and_safe_failure_category_survive_progress(tmp_path):
    from gaia_agent.interactive.failures import failure_details
    from gaia_agent.interactive.models import InteractiveRequest
    from gaia_agent.interactive.store import ConversationStore

    store = ConversationStore(tmp_path / "errors.sqlite")
    cid = store.create("alice", "deepseek")["id"]
    run = store.reserve(
        "alice",
        cid,
        InteractiveRequest(provider="deepseek", model="deepseek-flash", question="q"),
        "idem",
    )
    store.progress(
        run["id"],
        "tool_call_failed",
        {
            "tool": "web_search",
            "provider": "deepseek",
            "category": "balance",
            "http_status": 402,
            "raw": "never",
        },
    )
    events = store.run("alice", run["id"])["progress"]
    assert events[-1]["provider"] == "deepseek"
    assert "raw" not in events[-1]
    failure = failure_details("retry_exhausted", events)
    assert failure["provider"] == "deepseek"
    assert failure["tool_category"] == "balance"
    store.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['loop', 'budget', 'stage_time', 'retry'])
async def test_provider_preserves_agent_control_errors(kind):
    from gaia_agent.orchestrator import _RepeatedToolCall, _ToolBudgetExhausted, StageBudgetExceeded
    from gaia_agent.reliability import ProviderTransientExhaustedError
    errors = {'loop': _RepeatedToolCall('sensitive input'), 'budget': _ToolBudgetExhausted('sensitive input'),
              'stage_time': StageBudgetExceeded('critic','time'),
              'retry': ProviderTransientExhaustedError(retry_category='transport', attempt_count=2)}
    class Provider:
        supports_vision = True
        async def complete(self, *args, **kwargs): raise errors[kind]
    wrapper=SafePublicProvider(Provider())
    with pytest.raises(type(errors[kind])):
        await wrapper.complete(None)
    assert wrapper.failure_code is None


@pytest.mark.asyncio
async def test_unknown_local_model_error_is_not_described_as_provider_outage():
    class ParserFailure(Exception): pass
    class Provider:
        supports_vision=True
        async def complete(self,*args,**kwargs): raise ParserFailure('synthetic-private-credential')
    wrapper=SafePublicProvider(Provider())
    with pytest.raises(CredentialError) as caught:
        await wrapper.complete(None)
    assert caught.value.code == 'provider_client_error'
    assert caught.value.diagnostic['exception_type'] == 'ParserFailure'
    assert 'synthetic-private-credential' not in json.dumps(caught.value.diagnostic)


def test_loop_failure_reports_actual_tool_and_phase():
    events=[{'event':'verify_started'}, {'event':'tool_call_completed','tool':'read_webpage'}, {'event':'tool_call_failed','tool':'read_webpage','category':'repeated_call'}]
    failure=failure_details('loop_detected',events)
    assert failure['stage']=='verify'
    assert failure['component']=='tool'
    assert failure['tool']=='read_webpage'
    assert failure['tool_category']=='repeated_call'


def test_provider_retry_diagnostics_survive_store_with_no_raw_message(tmp_path):
    store=ConversationStore(tmp_path/'retry.sqlite');cid=store.create('alice','openai')['id']
    run=store.reserve('alice',cid,InteractiveRequest(provider='openai',model='gpt-6.1-terra',question='q'),'r')
    store.progress(run['id'],'verify_started',{})
    store.progress(run['id'],'provider_retry_exhausted',{'category':'APITimeoutError','attempt_count':3,'message':'private-secret'})
    events=store.run('alice',run['id'])['progress']
    failure=failure_details('retry_exhausted',events)
    assert failure['component']=='model'
    assert failure['diagnostic']['retry_category']=='APITimeoutError'
    assert failure['diagnostic']['attempt_count']==3
    assert 'private-secret' not in json.dumps(events)


def test_exception_type_is_filtered_if_it_contains_a_configured_secret():
    private='SYNTHETICPRIVATEKEY'
    custom=type('Upstream'+private+'Error',(Exception,),{})('other private text')
    diagnostic=safe_diagnostic(custom,(private,))
    assert diagnostic == {'exception_type':'UnexpectedError'}
    assert private not in json.dumps(diagnostic)


def test_model_token_metrics_are_saved_allowlisted(tmp_path):
    from gaia_agent.interactive.models import InteractiveRequest
    store=ConversationStore(tmp_path/'diagnostics.sqlite')
    cid=store.create('alice','deepseek')['id']
    run=store.reserve('alice',cid,InteractiveRequest(provider='deepseek',model='deepseek-flash',question='q'),'req')
    payload={'stage':'specialist','phase':'repair','code':'schema','response_status':'incomplete','incomplete_reason':'max_output_tokens','model':'deepseek-flash','reasoning_effort':'low','max_output_tokens':20000,'output_tokens':18000,'reasoning_tokens':14000,'secret':'secret-data'}
    store.progress(run['id'],'stage_response_diagnostic',payload)
    events=store.run('alice',run['id'])['progress']
    event=events[-1]
    assert event['model']=='deepseek-flash' and event['reasoning_tokens']==14000
    assert failure_details('malformed_response',events)['diagnostic']['max_output_tokens']==20000
    assert 'secret-data' not in json.dumps(events)
    store.db.close()


def test_untrusted_model_diagnostic_fields_are_dropped(tmp_path):
    from gaia_agent.interactive.models import InteractiveRequest
    store=ConversationStore(tmp_path/'diagnostics.sqlite')
    cid=store.create('alice','deepseek')['id']
    run=store.reserve('alice',cid,InteractiveRequest(provider='deepseek',model='deepseek-flash',question='q'),'req')
    payload={'stage':'specialist','phase':'repair','code':'schema','response_status':'incomplete','incomplete_reason':'max_output_tokens','model':'secret-data','reasoning_effort':'secret-data','max_output_tokens':True,'output_tokens':-1,'reasoning_tokens':999999999999}
    store.progress(run['id'],'stage_response_diagnostic',payload)
    event=store.run('alice',run['id'])['progress'][-1]
    assert not ({'model','reasoning_effort','max_output_tokens','output_tokens','reasoning_tokens'} & set(event))
    assert 'secret-data' not in json.dumps(event)
    store.db.close()
