"""Exercise real planner requests and server gates, not real model accuracy."""

import json

import pytest
from conftest import FakeProvider
from test_memory_schema import event_values

from gaia_agent.interactive.memory.models import MemoryEvent, MemoryOperation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "我是方林",
        "以后叫我小林",
        "这个偏好以后都按这个来",
        "帮我记下来，下次还要用",
        "Save this for future conversations",
        "项目的截止日期是周五",
    ],
)
async def test_no_keyword_or_first_person_prefilter(text):
    from gaia_agent.interactive.memory.semantic import SemanticMemoryPlanner

    provider = FakeProvider()
    provider.return_parsed({"operations": []})
    event = MemoryEvent(**(event_values() | {"source": text}))
    assert (
        await SemanticMemoryPlanner(provider).plan(event, (), (), (), usage_observer=lambda _: None)
        == ()
    )
    request = provider.requests[0]
    assert json.loads(request.input_items[0]["content"])["source"] == text
    assert request.max_tool_calls == 0 and request.allowed_tools == ()
    assert request.max_output_tokens == 6400
    assert request.reasoning_effort == "low"
    topic_schema = request.output_schema["$defs"]["MemoryOperation"]["properties"]["topic"]
    assert "enum" not in topic_schema
    for schema in [request.output_schema, *request.output_schema["$defs"].values()]:
        if schema.get("type") == "object":
            assert set(schema["required"]) == set(schema["properties"])


def operation(**changes):
    return MemoryOperation(
        **(
            {
                "action": "add",
                "category": "profile",
                "topic": "name",
                "entity": "self",
                "cardinality": "single",
                "content": "我是方林",
                "source_start": 0,
                "source_end": 4,
                "source_kind": "user",
                "intent": "automatic",
            }
            | changes
        )
    )


def test_evidence_gate_accepts_open_topics_but_not_unknown_targets_or_guesses():
    from gaia_agent.interactive.memory.policy import MemoryPolicy

    policy = MemoryPolicy()
    event = MemoryEvent(**event_values())
    assert policy.validate_operation(event, operation(), set())
    assert policy.validate_operation(event, operation(content="我住在火星"), set()) is None
    assert policy.validate_operation(event, operation(target_id="unknown"), set()) is None
    assert policy.validate_operation(event, operation(source_kind="verified_task"), set()) is None


def test_redaction_preserves_safe_fact_and_never_allows_redacted_span():
    from gaia_agent.interactive.memory.policy import MemoryPolicy, prepare_event_source

    source = prepare_event_source("我是方林；API key sk-synthetic1234567890；我喜欢数学")
    assert "sk-synthetic1234567890" not in source
    assert source.startswith("我是方林；") and source.endswith("；我喜欢数学")
    event = MemoryEvent(**(event_values() | {"source": source}))
    assert MemoryPolicy().validate_operation(event, operation(), set())
    start = source.index("[REDACTED]")
    assert (
        MemoryPolicy().validate_operation(
            event,
            operation(
                content="[REDACTED]", source_start=start, source_end=start + 10, intent="explicit"
            ),
            set(),
        )
        is None
    )


@pytest.mark.parametrize("source,start,end", [("他说“我是方林”", 3, 7), ("假设我是方林", 2, 6)])
def test_quoted_or_hypothetical_fragment_is_not_direct_user_evidence(source, start, end):
    from gaia_agent.interactive.memory.policy import MemoryPolicy

    event = MemoryEvent(**(event_values() | {"source": source}))
    fragment = source[start:end]
    assert (
        MemoryPolicy().validate_operation(
            event, operation(content=fragment, source_start=start, source_end=end), set()
        )
        is None
    )


def test_verified_project_observation_is_separate_from_identity_source():
    from gaia_agent.interactive.memory.models import MemoryObservation
    from gaia_agent.interactive.memory.policy import MemoryPolicy

    observation = MemoryObservation(
        id="verified-one", source_run_id="run-one", content="Project tests passed", verified=True
    )
    event = MemoryEvent(**(event_values() | {"observations": (observation,)}))
    op = operation(
        category="project",
        topic="test_result",
        entity="toyagent",
        content="Project tests passed",
        source_kind="verified_task",
        source_start=0,
        source_end=20,
        observation_id="verified-one",
    )
    assert MemoryPolicy().validate_operation(event, op, set())
    assert (
        MemoryPolicy().validate_operation(
            event, op.model_copy(update={"category": "profile"}), set()
        )
        is None
    )
    assert (
        MemoryPolicy().validate_operation(
            event, op.model_copy(update={"observation_id": "invented"}), set()
        )
        is None
    )


@pytest.mark.asyncio
async def test_malformed_output_fails_safely_without_second_paid_call():
    from gaia_agent.interactive.memory.models import MemoryFailure
    from gaia_agent.interactive.memory.semantic import SemanticMemoryPlanner

    provider = FakeProvider()
    provider.return_parsed({"operations": [{}]})
    with pytest.raises(MemoryFailure, match="memory_invalid"):
        await SemanticMemoryPlanner(provider).plan(
            MemoryEvent(**event_values()), (), (), (), usage_observer=lambda _: None
        )
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_oversized_context_is_not_silently_truncated_or_sent():
    from gaia_agent.interactive.memory.models import MemoryFailure
    from gaia_agent.interactive.memory.semantic import SemanticMemoryPlanner

    provider = FakeProvider()
    with pytest.raises(MemoryFailure, match="memory_limit"):
        await SemanticMemoryPlanner(provider).plan(
            MemoryEvent(**event_values()),
            (),
            ({"id": "barrier", "topic": "汉" * 8000},),
            (),
            usage_observer=lambda _: None,
        )
    assert provider.requests == []


@pytest.mark.asyncio
async def test_input_budget_includes_structured_output_schema():
    from gaia_agent.interactive.memory.models import MemoryFailure
    from gaia_agent.interactive.memory.semantic import SemanticMemoryPlanner

    provider = FakeProvider()
    with pytest.raises(MemoryFailure, match="memory_limit"):
        await SemanticMemoryPlanner(provider).plan(
            MemoryEvent(**(event_values() | {"source": "I study " + "a" * 7490})),
            (),
            ({"id": "barrier", "topic": "b" * 5200},),
            (),
            usage_observer=lambda _: None,
        )
    assert provider.requests == []


@pytest.mark.asyncio
async def test_extracts_five_independent_open_topic_operations_from_transport():
    from gaia_agent.interactive.memory.semantic import SemanticMemoryPlanner

    text = "我是方林；我喜欢数学；我在学Python；我养了猫；我在做ToyAgent"
    parts = text.split("；")
    operations = []
    offset = 0
    for topic, part in zip(["name", "interest", "learning", "pet", "project"], parts, strict=True):
        operations.append(
            operation(
                topic=topic, content=part, source_start=offset, source_end=offset + len(part)
            ).model_dump()
        )
        offset += len(part) + 1
    provider = FakeProvider()
    provider.return_parsed({"operations": operations})
    result = await SemanticMemoryPlanner(provider).plan(
        MemoryEvent(**(event_values() | {"source": text})),
        (),
        (),
        (),
        usage_observer=lambda _: None,
    )
    assert [op.content for op in result] == parts
    assert len(provider.requests) == 1


@pytest.mark.parametrize("text", ["这件事别记下来", "记得提醒我明天开会", "假装我是大学教授"])
@pytest.mark.asyncio
async def test_negative_messages_reach_semantic_classifier_without_regex_routing(text):
    from gaia_agent.interactive.memory.semantic import SemanticMemoryPlanner

    provider = FakeProvider()
    provider.return_parsed({"operations": []})
    result = await SemanticMemoryPlanner(provider).plan(
        MemoryEvent(**(event_values() | {"source": text})),
        (),
        (),
        (),
        usage_observer=lambda _: None,
    )
    assert result == ()
    assert len(provider.requests) == 1
