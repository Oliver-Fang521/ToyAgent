import pytest
from test_openai_provider import (
    FakeResponsesClient,
    RecordingExecutor,
    _response,
    _usage,
)
from test_openai_provider import (
    model_request as openai_model_request,
)


def model_request(**kwargs):
    return openai_model_request(**kwargs).model_copy(update={"reasoning_effort": "low"})


@pytest.mark.asyncio
async def test_deepseek_stateless_tool_loop_usage_and_supported_fields():
    from gaia_agent.providers.deepseek_responses import DeepSeekResponsesProvider

    client = FakeResponsesClient()
    client.queued.append(
        _response(
            "r1",
            [
                {"type": "reasoning", "id": "reason", "summary": []},
                {
                    "type": "function_call",
                    "id": "fc",
                    "call_id": "call1",
                    "name": "execute_python",
                    "arguments": '{"code":"1"}',
                    "status": "completed",
                },
            ],
            usage=_usage(2, 3, 1),
        )
    )
    client.queue_text('{"answer":"42"}', usage=_usage(4, 5, 1))
    provider = DeepSeekResponsesProvider(client, "deepseek-flash")
    executor = RecordingExecutor({"execute_python": {"value": 42}})
    result = await provider.complete(model_request(), executor)
    assert result.parsed == {"answer": "42"} and result.usage.total_tokens == 14
    assert len(result.tool_events) == 1
    assert "previous_response_id" not in client.requests[1]
    assert any(item.get("type") == "function_call_output" for item in client.requests[1]["input"])
    assert all(
        "max_tool_calls" not in request and request["reasoning"] == {"effort": "low"}
        for request in client.requests
    )
    assert all(
        tool["type"] == "function" for request in client.requests for tool in request["tools"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("model,allowed", [("deepseek-flash", True), ("deepseek-v4-pro", False)])
async def test_images_are_real_or_rejected_never_text_markers(model, allowed):
    from gaia_agent.providers.deepseek_responses import DeepSeekResponsesProvider

    client = FakeResponsesClient()
    client.queue_text('{"answer":"image"}')
    request = model_request(
        allowed_tools=(),
        input_items=(
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": "data:image/png;base64,synthetic"},
                ],
            },
        ),
    )
    if allowed:
        await DeepSeekResponsesProvider(client, model).complete(request)
        assert client.requests[0]["input"][0]["content"][0]["type"] == "input_image"
    else:
        with pytest.raises(ValueError, match="capability_unavailable"):
            await DeepSeekResponsesProvider(client, model).complete(request)
        assert client.requests == []


@pytest.mark.asyncio
async def test_deepseek_preserves_refusal_and_incomplete_state():
    from gaia_agent.providers.deepseek_responses import DeepSeekResponsesProvider

    client = FakeResponsesClient()
    client.queue_refusal("refused")
    result = await DeepSeekResponsesProvider(client, "deepseek-flash").complete(
        model_request(allowed_tools=())
    )
    assert result.parsed is None
    raw = _response("r", []).model_dump()
    raw.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    from openai.types.responses import Response

    client.queued.append(Response.model_validate(raw))
    result = await DeepSeekResponsesProvider(client, "deepseek-flash").complete(
        model_request(allowed_tools=())
    )
    assert (
        result.response_status == "incomplete" and result.incomplete_reason == "max_output_tokens"
    )


def test_deepseek_refuses_hosted_tools_instead_of_silent_ignoring():
    from gaia_agent.providers.deepseek_responses import DeepSeekResponsesProvider

    with pytest.raises(ValueError, match="capability_unavailable"):
        DeepSeekResponsesProvider(FakeResponsesClient(), "deepseek-flash")._prepare_response_kwargs(
            {"tools": [{"type": "code_interpreter"}]}
        )


def test_deepseek_projects_only_documented_transport_fields():
    from gaia_agent.providers.deepseek_responses import DeepSeekResponsesProvider

    result = DeepSeekResponsesProvider(FakeResponsesClient())._prepare_response_kwargs(
        {
            "model": "deepseek-flash",
            "input": [],
            "include": ["web_search_call.action.sources"],
            "metadata": {"private": "value"},
        }
    )
    assert result == {"model": "deepseek-flash", "input": []}


@pytest.mark.asyncio
async def test_deepseek_cannot_treat_hosted_response_as_success():
    from gaia_agent.providers.deepseek_responses import DeepSeekResponsesProvider

    client = FakeResponsesClient()
    client.queued.append(
        _response(
            "r",
            [
                {
                    "id": "ws",
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {"type": "search", "query": "q", "sources": []},
                }
            ],
        )
    )
    with pytest.raises(ValueError, match="capability_unavailable"):
        await DeepSeekResponsesProvider(client).complete(model_request(allowed_tools=()))
