from dataclasses import replace

from gaia_agent.interactive.memory.models import MemoryRecord
from gaia_agent.interactive.memory.retrieval import MemoryRetriever


def record(identifier="one", content="I prefer Chinese", category="profile", **patch):
    value = MemoryRecord(
        identifier,
        "alice",
        category,
        "opaque",
        1,
        "openai",
        ("openai",),
        "active",
        content,
        None,
        None,
        None,
        0,
        0,
        0,
        None,
        False,
    )
    return replace(value, **patch)


def test_related_project_expiry_and_stable_profile():
    values = (
        record(),
        record("python", "My Python project uses SQLite", "project"),
        record("other", "My gardening goal", "project"),
        record("expired", expires_at=1),
    )
    selected = MemoryRetriever().select(values, "Python SQLite help", 2)
    assert [r.id for r in selected] == ["one", "python"]


def test_chinese_byte_and_count_budget_and_untrusted_context():
    retriever = MemoryRetriever()
    values = tuple(record(str(i), "我正在学习数学" * 60) for i in range(20))
    selected = retriever.select(values, "数学", 2)
    assert 0 < len(selected) <= 8
    rendered = retriever.render(selected)
    assert len(rendered.encode()) <= 6000
    assert "untrusted" in rendered
    assert "current user instructions" in rendered
    # Every reported selection must really be sent; render must not silently drop it.
    import json

    assert len(json.loads(rendered.split("\n", 1)[1])) == len(selected)


def test_chinese_project_overlap():
    selected = MemoryRetriever().select(
        (record("cn", "我的项目是数学教学", "project"),), "数学问题", 0
    )
    assert len(selected) == 1


def test_identity_base_does_not_disappear_under_recent_profile_noise():
    values = (
        record("name", "以后叫我小林", topic="name"),
        *(record(str(i), f"I prefer music style {i}", last_confirmed_at=i + 1) for i in range(15)),
    )
    assert "name" in [r.id for r in MemoryRetriever().select(values, "我是谁", 100)]


def test_progressive_context_is_owner_provider_filtered_and_bounded():
    from gaia_agent.interactive.memory.models import MemorySnapshot

    values = (
        record("name", "我是方林", topic="name"),
        record("wrong", owner="bob"),
        record("unshared", allowed_providers=("deepseek",)),
        record("project", "我的 Python 项目", "project"),
    )
    context = MemoryRetriever().select_context(
        MemorySnapshot("alice", "openai", 0, "normal", values), "Python项目"
    )
    assert "wrong" not in context.used_ids and "unshared" not in context.used_ids
    assert "project" in context.used_ids and "name" in context.used_ids
    assert len(context.text.encode()) <= 6000 and len(context.used_ids) <= 8


def test_render_preserves_name_vs_preferred_name_semantics():
    import json

    text = MemoryRetriever().render(
        (
            record("legal", "方林", topic="name"),
            record("preferred", "小林", topic="preferred_name"),
        )
    )
    payload = json.loads(text.split("\n", 1)[1])
    assert [(item["topic"], item["content"]) for item in payload] == [
        ("name", "方林"),
        ("preferred_name", "小林"),
    ]


def test_project_entity_retrieves_atomic_database_fact_and_disambiguates_goals():
    import json

    retriever = MemoryRetriever()
    records = (
        record("db", "PostgreSQL", "project", topic="tech_stack", entity="Orion"),
        record("goal", "离线运行", "project", topic="release_goal", entity="Orion"),
        record("other", "稳定记忆", "project", topic="release_goal", entity="ToyAgent"),
    )
    selected = retriever.select(records, "Orion 使用哪个数据库？", 10)
    assert "db" in {r.id for r in selected}
    payload = json.loads(retriever.render(selected).split("\n", 1)[1])
    assert next(r for r in payload if r["id"] == "db")["entity"] == "Orion"
