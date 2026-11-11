from dataclasses import replace

import pytest

from gaia_agent.interactive.memory.models import MemoryCandidate, MemoryFailure
from gaia_agent.interactive.memory.policy import MemoryPolicy


def candidate(text, **patch):
    data = {
        "category": "profile",
        "facet": "learning_subjects",
        "entity": "self",
        "content": text,
        "source_start": 0,
        "source_end": len(text),
        "explicit_relearn": False,
    }
    return MemoryCandidate(**(data | patch))


def test_exact_unicode_span_and_self_report():
    policy = MemoryPolicy()
    text = "我正在学 Python，喜欢先举例。"
    assert policy.validate_current(candidate(text), text).source_fragment == text
    assert policy.validate_current(candidate(text, source_end=len(text) + 1), text) is None
    assert policy.validate_current(candidate("我学 Rust"), text) is None


@pytest.mark.parametrize(
    "text",
    [
        "好的",
        "假设我是工程师",
        "请扮演我正在学 Python",
        "他说：我在学 Python",
        "网页说我学 Python",
        "助手说我在学 Python",
        "Example: I study Python",
        'My friend says "I study Python"',
        "Pretend I am learning Python",
        "If I were a developer",
    ],
)
def test_non_self_report_rejected(text):
    assert MemoryPolicy().validate_current(candidate(text), text) is None


@pytest.mark.parametrize(
    "text",
    [
        "我的密码是 hello123",
        "sk-0123456789abcdefghijklmno",
        "api_key=secretvalue",
        "Bearer abcdefghijklmno",
        "我的住址是 12 Main St",
        "我的诊断是抑郁症",
        "My religion is X",
        "My bank account is 123456789",
    ],
)
def test_sensitive_and_credentials_never_enter_extraction_or_manual_storage(text):
    policy = MemoryPolicy()
    assert policy.prepare_text(text) is None
    with pytest.raises(MemoryFailure, match="memory_invalid"):
        policy.validate_manual(candidate(text))


def test_closed_facets_and_normalization():
    policy = MemoryPolicy()
    assert (
        policy.normalize(candidate("我学 Python", entity=" ＳＥＬＦ "))
        == "profile:learning_subjects:self"
    )
    with pytest.raises(MemoryFailure):
        policy.validate_manual(candidate("普通背景", facet="government_id"))


def test_relearn_must_be_explicit_current_not_quoted_or_model_flag():
    policy = MemoryPolicy()
    normal = "我正在学 Python"
    assert policy.validate_current(candidate(normal, explicit_relearn=True), normal) is None
    text = "请重新记住：我正在学 Python"
    write = policy.validate_current(candidate(text, explicit_relearn=True), text)
    assert write is not None
    assert policy.decide(write, None, True, text) == "restore"
    assert (
        policy.decide(policy.validate_current(candidate(normal), normal), None, True, normal)
        == "skip"
    )


@pytest.mark.parametrize(
    "text",
    [
        "I use AKIA1234567890ABCDEF",
        "I use eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmno",
        "我住在 123 Main Street",
        "My street address is 123 Main Street",
    ],
)
def test_additional_secret_formats_and_precise_address_are_skipped(text):
    assert MemoryPolicy().prepare_text(text) is None


def test_user_edit_has_priority():
    from gaia_agent.interactive.memory.models import MemoryRecord

    record = MemoryRecord(
        "id",
        "alice",
        "profile",
        "opaque",
        1,
        "openai",
        ("openai",),
        "active",
        "I study Rust",
        None,
        None,
        None,
        0,
        0,
        0,
        None,
        True,
    )
    write = MemoryPolicy().validate_current(candidate("我学 Python"), "我学 Python")
    assert MemoryPolicy().decide(write, record, False, "我学 Python") == "skip"
    assert (
        MemoryPolicy().decide(write, replace(record, edited_by_user=False), False, "我学 Python")
        == "replace"
    )
