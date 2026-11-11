"""Interactive research must use verified evidence when a specialist cannot finish."""
import pytest
from test_orchestrator import (
    FakeCritic,
    FakePlanner,
    FakeSpecialist,
    FakeToolFactory,
    FakeTools,
    _config,
    _decision,
    _plan,
    _task,
)

from gaia_agent.agents import StageProtocolError, StageValidationCode
from gaia_agent.models import SpecialistName, Usage
from gaia_agent.orchestrator import Orchestrator
from gaia_agent.research_notebook import ResearchNotebook


@pytest.mark.parametrize("failure", ["format", "budget"])
@pytest.mark.parametrize("has_source,approved", [(True, True), (True, False), (False, False)])
@pytest.mark.asyncio
async def test_evidence_recovery_still_requires_verification(tmp_path, failure, has_source, approved):
    calls = []
    task = _task()
    book = ResearchNotebook(tmp_path / "notes", task.task_id)
    if has_source:
        url = "https://example.org/fact"
        book.record("read_webpage", {"url": url}, {"url": url, "title": "Research", "text": "Curie discovered the synthetic element."})

    class InterruptedLookup(FakeSpecialist):
        async def summarize_evidence(self, task, evidence):
            assert "Curie" in evidence[0].summary
            calls.append("summarize")
            return "Curie", Usage()

    malformed = StageProtocolError(StageValidationCode.SCHEMA, Usage(), repair_attempted=True)
    specialist = InterruptedLookup(SpecialistName.LOOKUP, calls, [], error=malformed,
        tool_calls=[("web_search", {"query": str(i)}) for i in range(2 if failure == "budget" else 0)])
    critic = FakeCritic(calls, [_decision("approve" if approved else "insufficient_evidence", ("evidence-0001",))])
    engine = Orchestrator(planner=FakePlanner(calls, _plan(SpecialistName.LOOKUP)),
        specialists={SpecialistName.LOOKUP: specialist}, critic=critic,
        tool_client_factory=FakeToolFactory(FakeTools(calls)), notebook=book,
        config=_config(specialist_max_tool_calls=1), separate_submission_budget=True)
    result = await engine.run(task)
    assert calls.count("summarize") == int(has_source)
    assert ("submit_answer" in calls) == approved
    assert (result.answer == "Curie") == approved
