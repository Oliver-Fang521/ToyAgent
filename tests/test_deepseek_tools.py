import json
import time

import httpx
import pytest

from gaia_agent.reliability import RetryPolicy
from gaia_agent.search import SearchProviderChain, SearchProviderError


@pytest.mark.asyncio
async def test_unicode_secret_and_private_url_guards():
    from gaia_agent.deepseek_tools import DeepSeekSearchProvider, DeepSeekWebReader

    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), trust_env=False
    ) as client:
        search = DeepSeekSearchProvider("synthetic-secret", client, timeout_seconds=2)
        assert (await search.search("😀" * 128))["sources"] == []
        for query in ("😀" * 129, " ", "synthetic-secret"):
            with pytest.raises(SearchProviderError) as error:
                await search.search(query)
            assert not error.value.retryable
        reader = DeepSeekWebReader("synthetic-secret", client, timeout_seconds=2)
        for url in (
            "http://127.0.0.1",
            "http://169.254.169.254",
            "http://user:pass@example.com",
            "http://[::1]",
            "file:///etc/passwd",
        ):
            with pytest.raises(SearchProviderError):
                await reader.read(url)
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_deepseek_search_shape_and_bounded_normalization():
    from gaia_agent.deepseek_tools import DeepSeekSearchProvider

    def transport(request):
        assert str(request.url) == "https://api.deepseek.com/v1/tools/search"
        assert request.headers["authorization"] == "Bearer synthetic-secret"
        assert json.loads(request.content) == {"query": "research", "count": 5}
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "rerank_score": 2.4,
                        "title": "Title",
                        "url": "https://example.com",
                        "snippet": "public synthetic-secret",
                        "summary": "evidence",
                        "content": "",
                        "site_name": None,
                        "site_icon": None,
                        "date": None,
                        "authority": 3,
                    }
                ]
                * 10
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        value = await DeepSeekSearchProvider("synthetic-secret", client, timeout_seconds=2).search(
            "research"
        )
        assert len(value["sources"]) == 5
        assert value["sources"][0]["title"] == "Title"
        assert value["sources"][0]["retrieved_at"]
        assert "synthetic-secret" not in str(value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,category,retryable",
    [
        (401, "authentication", False),
        (402, "balance", False),
        (403, "forbidden", False),
        (429, "rate_limit", True),
        (500, "upstream", True),
    ],
)
async def test_deepseek_safe_http_errors(status, category, retryable):
    from gaia_agent.deepseek_tools import DeepSeekSearchProvider

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(status, text="synthetic-secret"))
    ) as client:
        with pytest.raises(SearchProviderError) as error:
            await DeepSeekSearchProvider("synthetic-secret", client, timeout_seconds=2).search("q")
        assert error.value.category == category
        assert error.value.retryable is retryable
        assert "synthetic-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_empty_deepseek_results_are_terminal_and_retry_is_bounded():
    from gaia_agent.deepseek_tools import DeepSeekSearchProvider

    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(429 if len(calls) == 1 else 200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = DeepSeekSearchProvider("synthetic-secret", client, timeout_seconds=2)
        chain = SearchProviderChain(
            [provider],
            RetryPolicy(max_attempts=2, base_delay_seconds=0),
            time.monotonic,
            operation_timeout_seconds=2,
        )
        result = await chain.search("q")
        assert result["sources"] == []
        assert result.provider == "deepseek"
        assert len(calls) == 2
