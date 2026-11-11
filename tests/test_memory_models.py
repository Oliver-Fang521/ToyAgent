import pytest
from pydantic import ValidationError

from gaia_agent.interactive.memory.models import MemoryCandidate, MemorySettings


def candidate(**patch):
    return MemoryCandidate(
        **dict(
            category="profile",
            facet="learning_subjects",
            entity="self",
            content="I study Python",
            source_start=0,
            source_end=14,
            explicit_relearn=False,
            **patch,
        )
    )


def test_candidate_is_frozen_and_has_no_plaintext_repr():
    value = candidate()
    assert "Python" not in repr(value)
    with pytest.raises(ValidationError):
        value.content = "changed"


@pytest.mark.parametrize("content", ["", "a" * 501])
def test_candidate_bounds(content):
    data = candidate().model_dump()
    data["content"] = content
    with pytest.raises(ValidationError) as error:
        MemoryCandidate(**data)
    assert content not in str(error.value) if content else True


def test_required_schema_and_literal_boolean():
    schema = MemoryCandidate.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    assert MemorySettings(revision=0, generation=0, capture_after=0).enabled is True
    with pytest.raises(ValidationError):
        MemorySettings(enabled=1, revision=0, generation=0, capture_after=0)
