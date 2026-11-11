import pytest
from conftest import FakeProvider

from gaia_agent.interactive.memory.extraction import MemoryExtractor
from gaia_agent.models import Usage


@pytest.mark.asyncio
async def test_current_only_strict_bounded_tool_free_extract():
    provider = FakeProvider()
    provider.return_parsed(
        {
            "candidates": [
                {
                    "category": "profile",
                    "facet": "learning_subjects",
                    "entity": "self",
                    "content": "I study Python",
                    "source_start": 0,
                    "source_end": 14,
                    "explicit_relearn": False,
                }
            ]
        }
    )
    values = await MemoryExtractor(provider).extract(
        "I study Python", (), usage_observer=lambda _: None
    )
    assert len(values) == 1
    request = provider.requests[0]
    assert request.allowed_tools == () and request.max_tool_calls == 0
    assert request.max_output_tokens == 800
    assert "I study Python" in str(request.input_items)
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_memory_extraction_honors_selected_effort():
    provider = FakeProvider()
    provider.return_parsed({"candidates": []})
    await MemoryExtractor(provider, reasoning_effort="max").extract(
        "I study Python", (), usage_observer=lambda _: None
    )
    assert provider.requests[0].reasoning_effort == "max"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", ["broken", '{"candidates":[] ,"unexpected":1}', '{"candidates":[{}]}']
)
async def test_malformed_is_skipped_without_second_call(payload):
    provider = FakeProvider()
    provider.return_text(payload)
    assert (
        await MemoryExtractor(provider).extract("I study Python", (), usage_observer=lambda _: None)
        == ()
    )
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_partial_usage_propagates_when_provider_fails():
    class Partial:
        async def complete(self, request, executor=None, *, usage_observer=None):
            usage_observer(Usage(input_tokens=11))
            raise TimeoutError

    usages = []
    with pytest.raises(TimeoutError):
        await MemoryExtractor(Partial()).extract("I study Python", (), usage_observer=usages.append)
    assert sum(u.input_tokens for u in usages) == 11
