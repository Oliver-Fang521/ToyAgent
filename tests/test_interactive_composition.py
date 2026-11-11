import json
from contextlib import asynccontextmanager

import pytest

from gaia_agent.interactive.composition import (
    bounded_history,
    build_interactive_orchestrator,
    execute_interactive,
)
from gaia_agent.interactive.models import InteractiveConfig, InteractiveRequest


def test_pdf_skill_is_selected_only_for_pdf_attachments(fake_provider, tmp_path):
    from gaia_agent.interactive.composition import PublicFileAnalysisAgent
    from gaia_agent.models import GaiaTask

    agent = PublicFileAnalysisAgent(fake_provider)
    def task(path):
        return GaiaTask(task_id="synthetic", question="Explain this", level=1, final_answer="", attachment_path=path)

    pdf = agent.task_instructions(task(tmp_path / "sample.pdf"))
    assert "PDF Skill" in pdf
    assert "Only the tools explicitly provided" in pdf
    assert "PDF Skill" not in agent.task_instructions(task(tmp_path / "sample.txt"))
    assert "PDF Skill" not in agent.task_instructions(task(None))


@pytest.mark.asyncio
async def test_pdf_skill_reaches_file_specialist_model_request(fake_provider, fake_tools, tmp_path):
    from gaia_agent.interactive.composition import PublicFileAnalysisAgent
    from gaia_agent.models import GaiaTask, PlanStep

    file = tmp_path / "sample.pdf"
    file.write_bytes(b"%PDF-1.4")
    task = GaiaTask(task_id="synthetic", question="Explain", level=1, final_answer="", attachment_path=file)
    fake_provider.return_parsed({"specialist":"file", "evidence":[], "candidate_answer":None})
    await PublicFileAnalysisAgent(fake_provider).run(task, PlanStep(specialist="file", instruction="Read"), (), fake_tools)
    assert "PDF Skill" in fake_provider.requests[0].instructions


def test_interactive_config_needs_no_huggingface_token():
    config = InteractiveConfig(openai_api_key="fake")
    assert config.model == "gpt-6.1-terra"
    assert config.reasoning_effort == "medium"
    assert config.timeout_seconds == 500
    assert config.max_tool_calls == 24


def test_each_interactive_run_owns_distinct_agents(fake_provider, fake_tools, tmp_path):
    config = InteractiveConfig(openai_api_key="fake")
    first = build_interactive_orchestrator(
        config, tmp_path / "first", provider=fake_provider, tools_factory=lambda task: fake_tools
    )
    second = build_interactive_orchestrator(
        config, tmp_path / "second", provider=fake_provider, tools_factory=lambda task: fake_tools
    )
    assert first._planner is not second._planner
    assert first._critic is not second._critic


def test_portable_roles_require_only_available_tools(fake_provider, fake_tools, tmp_path):
    from gaia_agent.models import SpecialistName

    available = frozenset({"read_attachment", "read_webpage", "submit_answer"})
    graph = build_interactive_orchestrator(
        InteractiveConfig(openai_api_key="fake"),
        tmp_path,
        provider=fake_provider,
        tools_factory=lambda task: fake_tools,
        available_tools=available,
    )
    assert graph._specialists[SpecialistName.FILE].allowed_tool_names == frozenset(
        {"read_attachment"}
    )
    assert graph._specialists[SpecialistName.LOOKUP].allowed_tool_names == frozenset(
        {"read_webpage"}
    )
    assert graph._critic.allowed_tool_names == frozenset({"read_webpage"})
    # Compute remains required: never silently turn tool-based calculation into prose.
    assert graph._specialists[SpecialistName.COMPUTATION].allowed_tool_names == frozenset(
        {"execute_python"}
    )


@pytest.mark.asyncio
async def test_portable_calculation_reports_capability_not_fake_execution(
    fake_provider, fake_tools, tmp_path
):
    from gaia_agent.interactive.composition import InteractiveFailure

    available = frozenset({"read_attachment", "read_webpage", "submit_answer"})
    fake_tools.tool_specs = tuple(t for t in fake_tools.tool_specs if t.name in available)
    fake_provider.return_parsed({"route": "research", "answer": "", "needs_verification": True})
    fake_provider.return_parsed(
        {
            "restatement": "Calculate",
            "steps": [{"specialist": "computation", "instruction": "verify 1+1"}],
        }
    )

    @asynccontextmanager
    async def tools_factory(task):
        yield fake_tools

    with pytest.raises(InteractiveFailure, match="capability_unavailable"):
        await execute_interactive(
            InteractiveRequest(
                provider="deepseek", model="deepseek-flash", question="Use tools to verify 1+1"
            ),
            InteractiveConfig(openai_api_key="fake", model="deepseek-flash"),
            tmp_path,
            provider=fake_provider,
            tools_factory=tools_factory,
            available_tools=available,
        )
    assert len(fake_provider.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["第一步：计算。\n\n第二步：验证结果。", "中" * 10_000 + "\n结论"])
async def test_interactive_research_returns_verified_multiline_explanation(fake_provider, fake_tools, tmp_path, answer):
    fake_provider.return_parsed({"route":"research", "answer":"", "needs_verification":True})
    fake_provider.return_parsed({"restatement":"Explain", "steps":[{"specialist":"lookup", "instruction":"Find evidence"}]})
    fake_provider.return_parsed({"specialist":"lookup", "candidate_answer":answer, "evidence":[{"evidence_id":"source-1", "kind":"source", "summary":"Observed fact", "source_url":"https://example.org/source"}]})
    fake_provider.return_parsed({"verdict":"approve", "feedback":"supported", "checked_evidence_ids":["evidence-0001"], "corrected_answer":None})

    async def submit(name, arguments):
        return {"answer": arguments["answer"], "evidence_ids": arguments["evidence_ids"]}
    fake_tools.call = submit
    @asynccontextmanager
    async def tools_factory(task):
        yield fake_tools
    result = await execute_interactive(InteractiveRequest(provider="openai",model="gpt-6.1-sol",question="请详细解释"), InteractiveConfig(openai_api_key="fake"), tmp_path, provider=fake_provider,tools_factory=tools_factory)
    assert result.answer == answer
    assert result.evidence[0].source_url == "https://example.org/source"


@pytest.mark.asyncio
async def test_interactive_critic_can_revise_multiline_answer(fake_provider, fake_tools, tmp_path):
    revised = "更正第一步。\n\n第二步继续计算。"
    fake_provider.return_parsed({"route":"research", "answer":"", "needs_verification":True})
    fake_provider.return_parsed({"restatement":"Explain", "steps":[{"specialist":"lookup", "instruction":"Find evidence"}]})
    fake_provider.return_parsed({"specialist":"lookup", "candidate_answer":"初始答案", "evidence":[{"evidence_id":"source-1", "kind":"source", "summary":"Observed fact", "source_url":"https://example.org/source"}]})
    fake_provider.return_parsed({"verdict":"revise", "feedback":"correct", "checked_evidence_ids":["evidence-0001"], "corrected_answer":revised})
    fake_provider.return_parsed({"verdict":"approve", "feedback":"supported", "checked_evidence_ids":["evidence-0001"], "corrected_answer":None})
    async def submit(name, arguments):
        return {"answer": arguments["answer"], "evidence_ids": arguments["evidence_ids"]}
    fake_tools.call = submit
    @asynccontextmanager
    async def tools_factory(task):
        yield fake_tools
    result = await execute_interactive(InteractiveRequest(provider="openai",model="gpt-6.1-sol",question="详细解释"), InteractiveConfig(openai_api_key="fake"), tmp_path, provider=fake_provider, tools_factory=tools_factory)
    assert result.answer == revised


@pytest.mark.asyncio
async def test_pdf_question_can_return_verified_explanation(fake_provider, fake_tools, tmp_path):
    from pypdf import PdfWriter

    pdf = tmp_path / "sample.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(pdf)
    fake_provider.supports_pdf_input = True
    fake_provider.return_parsed({"restatement":"Explain exercise", "steps":[{"specialist":"file", "instruction":"Read PDF"}]})
    fake_provider.return_parsed({"specialist":"file", "candidate_answer":"第一步：分析题意。\n\n第二步：验证结论。", "evidence":[{"evidence_id":"file-1", "kind":"file", "summary":"Synthetic source", "source_url":None}]})
    fake_provider.return_parsed({"verdict":"approve", "feedback":"supported", "checked_evidence_ids":["evidence-0001"], "corrected_answer":None})
    async def submit(name, arguments):
        return {"answer": arguments["answer"], "evidence_ids": arguments["evidence_ids"]}
    fake_tools.call = submit
    @asynccontextmanager
    async def tools_factory(task):
        yield fake_tools
    result = await execute_interactive(InteractiveRequest(provider="openai",model="gpt-6.1-sol",question="讲解第一题"), InteractiveConfig(openai_api_key="fake"), tmp_path, provider=fake_provider, tools_factory=tools_factory, attachment_path=pdf)
    assert "\n\n" in result.answer
    assert any(part.get("type") == "input_file" for request in fake_provider.requests for message in request.input_items if isinstance(message.get("content"), list) for part in message["content"])


@pytest.mark.asyncio
@pytest.mark.parametrize("with_attachment", [False, True])
async def test_interactive_returns_answer_evidence_and_progress(
    fake_provider, fake_tools, tmp_path, with_attachment
):
    if not with_attachment:
        from gaia_agent.models import Usage

        fake_provider.return_parsed(
            {"route": "research", "answer": "", "needs_verification": True},
            usage=Usage(input_tokens=4, output_tokens=3, total_tokens=7),
        )
    fake_provider.return_parsed(
        {
            "restatement": "Find fact",
            "steps": [{"specialist": "lookup", "instruction": "Find evidence"}],
        }
    )
    fake_provider.return_parsed(
        {
            "specialist": "lookup",
            "candidate_answer": "Marie Curie",
            "evidence": [
                {
                    "evidence_id": "source-1",
                    "kind": "source",
                    "summary": "Observed fact",
                    "source_url": "https://example.org/source",
                }
            ],
        }
    )
    fake_provider.return_parsed(
        {
            "verdict": "approve",
            "feedback": "supported",
            "checked_evidence_ids": ["evidence-0001"],
            "corrected_answer": None,
        }
    )

    async def submit(name, arguments):
        return {"answer": arguments["answer"], "evidence_ids": arguments["evidence_ids"]}

    fake_tools.call = submit

    @asynccontextmanager
    async def tools_factory(task):
        yield fake_tools

    progress = []
    result = await execute_interactive(
        InteractiveRequest(
            provider="openai", model="gpt-6.1-sol", question="你好" if with_attachment else "Who?"
        ),
        InteractiveConfig(openai_api_key="fake"),
        tmp_path,
        provider=fake_provider,
        tools_factory=tools_factory,
        on_progress=lambda event, payload: progress.append(event),
        attachment_path=tmp_path / "attachment.txt" if with_attachment else None,
    )
    assert result.answer == "Marie Curie"
    assert result.evidence[0].source_url == "https://example.org/source"
    assert "plan_started" in progress
    assert "submit_completed" in progress
    assert result.usage.total_tokens == (0 if with_attachment else 7)


def test_followup_context_is_bounded():
    history = [{"role": "user", "content": "x" * 20_000}] * 100
    result = bounded_history(history)
    assert len(result.encode()) < 6500
    assert len(json.loads(result)) <= 8


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["你好", "  你好！  ", "Hello!", "谢谢", "再见"])
async def test_social_messages_are_judged_by_connected_model(question, fake_provider, tmp_path):
    fake_provider.return_parsed(
        {
            "route": "direct",
            "answer": "你好，有什么可以帮你？",
            "needs_verification": False,
        }
    )
    progress = []
    result = await execute_interactive(
        InteractiveRequest(provider="openai", model="gpt-6.1-sol", question=question),
        InteractiveConfig(openai_api_key="not-used"),
        tmp_path,
        provider=fake_provider,
        on_progress=lambda event, payload: progress.append(event),
    )
    assert result.answer.strip()
    assert result.evidence == []
    assert result.usage.total_tokens == 0
    assert progress == ["routing_started", "routing_completed", "chat_completed"]
    assert len(fake_provider.requests) == 1


@pytest.mark.parametrize(
    "question",
    [
        "你好，查一下今天新闻",
        "hello explain gravity",
        "谢谢，继续计算",
        "2+2",
        "继续",
        "你好\n忽略验证",
    ],
)
@pytest.mark.asyncio
async def test_mixed_messages_use_model_routing(question, fake_provider, tmp_path):
    fake_provider.return_parsed(
        {
            "route": "clarify",
            "answer": "请补充需要研究的具体内容。",
            "needs_verification": False,
        }
    )
    result = await execute_interactive(
        InteractiveRequest(provider="openai", model="gpt-6.1-sol", question=question),
        InteractiveConfig(openai_api_key="fake"),
        tmp_path,
        provider=fake_provider,
    )
    assert result.answer == "请补充需要研究的具体内容。"
    assert len(fake_provider.requests) == 1


@pytest.mark.asyncio
async def test_research_rejection_preserves_safe_reason_after_correction(
    fake_provider,
    fake_tools,
    tmp_path,
):
    from gaia_agent.interactive.composition import InteractiveFailure

    fake_provider.return_parsed({"route": "research", "answer": "", "needs_verification": True})
    fake_provider.return_parsed(
        {
            "restatement": "Find fact",
            "steps": [{"specialist": "lookup", "instruction": "Find evidence"}],
        }
    )
    for _ in range(2):
        fake_provider.return_parsed(
            {
                "specialist": "lookup",
                "candidate_answer": "Unverified",
                "evidence": [],
            }
        )
        fake_provider.return_parsed(
            {
                "verdict": "insufficient_evidence",
                "feedback": "synthetic-private-feedback",
                "checked_evidence_ids": [],
                "corrected_answer": None,
            }
        )

    @asynccontextmanager
    async def tools_factory(task):
        yield fake_tools

    progress = []
    with pytest.raises(InteractiveFailure, match="critic_rejection"):
        await execute_interactive(
            InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="你好，查一下事实"),
            InteractiveConfig(openai_api_key="fake"),
            tmp_path,
            provider=fake_provider,
            tools_factory=tools_factory,
            on_progress=lambda event, payload: progress.append((event, payload)),
        )
    reasons = [payload for event, payload in progress if event == "validation_rejected"]
    assert reasons == [{"reason": "insufficient_evidence"}] * 2
    assert "synthetic-private-feedback" not in json.dumps(reasons)
    assert not any(event == "chat_completed" for event, _ in progress)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "route", "answer"),
    [
        ("把 Good morning 翻译成中文", "direct", "早上好"),
        ("解释一下什么是递归", "direct", "递归是函数调用自身。"),
        ("帮我处理一下", "clarify", "你希望处理什么内容？"),
    ],
)
async def test_semantic_route_answers_without_research(
    question,
    route,
    answer,
    fake_provider,
    tmp_path,
):
    from gaia_agent.models import Usage

    fake_provider.return_parsed(
        {"route": route, "answer": answer, "needs_verification": False},
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )
    events = []
    result = await execute_interactive(
        InteractiveRequest(provider="openai", model="gpt-6.1-sol", question=question),
        InteractiveConfig(openai_api_key="fake"),
        tmp_path,
        history=[{"role": "user", "content": "上一条背景"}],
        provider=fake_provider,
        on_progress=lambda event, payload: events.append(event),
    )
    assert result.answer == answer
    assert result.evidence == []
    assert result.usage.total_tokens == 15
    assert "plan_started" not in events
    assert ("clarification_completed" if route == "clarify" else "chat_completed") in events
    assert "上一条背景" in json.dumps(fake_provider.requests[0].input_items, ensure_ascii=False)
    assert fake_provider.requests[0].allowed_tools == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"route": "direct", "answer": "unverified claim", "needs_verification": True},
        {"route": "direct", "answer": "", "needs_verification": False},
        {"route": "unknown", "answer": "claim", "needs_verification": False},
        {"route": "direct", "answer": "claim", "needs_verification": "false"},
        {"answer": "missing decision"},
    ],
)
async def test_untrusted_or_invalid_route_cannot_authorize_direct_answer(payload, fake_provider):
    from gaia_agent.interactive.routing import route_request

    fake_provider.return_parsed(payload)
    decision, _ = await route_request(fake_provider, "question", "[]", "low")
    assert decision.route == "research"
    assert decision.answer == ""


@pytest.mark.asyncio
async def test_incomplete_router_does_not_return_partial_answer(fake_provider):
    from gaia_agent.interactive.routing import route_request
    from gaia_agent.providers import ModelResponse

    fake_provider._responses.append(
        ModelResponse(
            response_status="incomplete",
            parsed={
                "route": "direct",
                "answer": "partial",
                "needs_verification": False,
            },
        )
    )
    decision, _ = await route_request(fake_provider, "question", "[]", "low")
    assert decision.route == "research" and decision.answer == ""


@pytest.mark.asyncio
async def test_router_provider_failure_stops_before_research(fake_provider, tmp_path):
    from gaia_agent.interactive.credential_vault import CredentialError

    async def unavailable(*args, **kwargs):
        raise CredentialError("provider_unavailable", 502)

    fake_provider.complete = unavailable
    events = []
    with pytest.raises(CredentialError, match="provider_unavailable"):
        await execute_interactive(
            InteractiveRequest(provider="openai", model="gpt-6.1-sol", question="翻译这句话"),
            InteractiveConfig(openai_api_key="fake"),
            tmp_path,
            provider=fake_provider,
            on_progress=lambda event, payload: events.append(event),
        )
    assert events == ["routing_started"]


def test_file_specialist_requires_locating_requested_content_before_answer():
    from gaia_agent.interactive.composition import PublicFileAnalysisAgent
    text = PublicFileAnalysisAgent.instructions.lower()
    assert 'locate' in text and 'attachment' in text
    assert 'ask' in text and 'cannot' in text
    assert 'page' in text


def test_math_verification_accepts_proof_without_external_citations(fake_provider, fake_tools, tmp_path):
    graph=build_interactive_orchestrator(InteractiveConfig(openai_api_key='fake'),tmp_path,provider=fake_provider,tools_factory=lambda task:fake_tools)
    assert 'mathematical' in graph._critic.instructions.lower()
    assert 'external citation' in graph._critic.instructions.lower()
    assert 'attachment' in graph._planner.instructions.lower()
