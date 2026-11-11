import time

import pytest
from pydantic import SecretStr

from gaia_agent.reliability import RetryPolicy
from gaia_agent.search import SearchProviderChain, SearchProviderError


@pytest.mark.asyncio
async def test_deepseek_balance_failure_moves_to_serper():
    calls=[]
    class Provider:
        def __init__(self,name): self.provider=name
        async def search(self,query):
            calls.append(self.provider)
            if self.provider=="deepseek": raise SearchProviderError(provider="deepseek",category="balance",retryable=False)
            return {"answer":"ok","sources":[]}
    chain=SearchProviderChain([Provider("openai"),Provider("deepseek"),Provider("serper")],RetryPolicy(max_attempts=1),time.monotonic)
    async def fail(query):
        calls.append("openai")
        raise SearchProviderError(provider="openai",category="timeout",retryable=True)
    chain._providers[0].search=fail
    result=await chain.search("test")
    assert result.provider=="serper" and calls==["openai","deepseek","serper"]


def test_saved_openai_session_loads_only_same_owner_deepseek_key():
    from gaia_agent.interactive.credential_sessions import CredentialSessions
    from gaia_agent.interactive.credential_vault import CredentialError
    class Vault:
        def read(self,owner,provider):
            values={("alice","openai"):"oa",("alice","deepseek"):"ds",("bob","deepseek"):"other"}
            if (owner,provider) not in values: raise CredentialError("credential_required",422)
            return SecretStr(values[owner,provider])
    sessions=CredentialSessions(Vault())
    material=sessions.resolve(sessions.reference("alice","openai",None))
    assert material.deepseek_search.get_secret_value()=="ds"


@pytest.mark.asyncio
async def test_deepseek_then_serper_then_serpapi_after_errors():
    calls=[]
    class Provider:
        def __init__(self,name): self.provider=name
        async def search(self,query):
            calls.append(self.provider)
            if self.provider!="serpapi":
                raise SearchProviderError(provider=self.provider,category="timeout",retryable=True)
            return {"answer":"ok","sources":[]}
    result=await SearchProviderChain([Provider("deepseek"),Provider("serper"),Provider("serpapi")],RetryPolicy(max_attempts=2,base_delay_seconds=0),time.monotonic,operation_timeout_seconds=10).search("test")
    assert result.provider=="serpapi"
    assert calls==["deepseek","deepseek","serper","serper","serpapi"]


def test_openai_child_receives_and_redacts_only_explicit_deepseek_search_key(tmp_path):
    from gaia_agent.interactive.models import InteractiveConfig
    from gaia_agent.mcp_client import MCPToolClient
    client=MCPToolClient(task_root=tmp_path,config=InteractiveConfig(openai_api_key="synthetic-openai"),runtime_env={"AGENT_TOOL_MODE":"openai","OPENAI_API_KEY":"synthetic-openai","DEEPSEEK_API_KEY":"synthetic-deepseek"})
    assert client._minimal_server_env()["DEEPSEEK_API_KEY"]=="synthetic-deepseek"
    assert client._redact("synthetic-deepseek")=="[REDACTED]"


def test_openai_mcp_builds_complete_search_priority(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from gaia_agent import mcp_server
    for key,value in {"AGENT_TOOL_MODE":"openai","OPENAI_API_KEY":"synthetic-openai","DEEPSEEK_API_KEY":"synthetic-deepseek","SERPER_API_KEY":"synthetic-serper","SERPAPI_API_KEY":"synthetic-serpapi","GAIA_TASK_ROOT":str(tmp_path)}.items():
        monkeypatch.setenv(key,value)
    monkeypatch.setattr(mcp_server,"AsyncOpenAI",lambda **kwargs:SimpleNamespace())
    settings=mcp_server._settings_from_env()
    assert [item.provider for item in settings.search_provider._providers]==["openai","deepseek","serper","serpapi"]
