# ruff: noqa: F811 -- shared pytest fixture imported from repository tests
import pytest
from test_memory_repository import repo, write  # noqa: F401

from gaia_agent.interactive.memory.models import MemoryFailure


def index_for(repository, owner="alice", provider="openai"):
    from gaia_agent.interactive.memory.index import MemoryIndex
    from gaia_agent.interactive.memory.layer_models import MemoryScope

    return MemoryIndex(repository), MemoryScope(owner, provider, repository.settings(owner).generation)


def test_1000_facts_preserved_and_pageable(repo):
    first = None
    for i in range(1000):
        item = repo.save_manual("alice", write(f"fact unique{i}", entity=str(i)), ("openai",))
        first = first or item
    assert repo.get("alice", first.id).content == "fact unique0"
    found, cursor = set(), None
    while True:
        page = repo.page_records("alice", cursor=cursor, limit=50)
        assert page.total == 1000
        found.update(item.id for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(found) == 1000
    index, scope = index_for(repo)
    assert [r.id for r in index.search(scope, "unique0").items] == [first.id]


def test_partial_backfill_falls_back_to_old_rows(repo):
    saved = repo.save_manual("alice", write("rare historicalrecord", entity="old"), ("openai",))
    index, scope = index_for(repo)
    with repo.db:
        repo.db.execute("DELETE FROM memory_indexed WHERE owner='alice'")
        repo.db.execute("DELETE FROM memory_terms WHERE owner='alice'")
    page = index.search(scope, "historicalrecord")
    assert [r.id for r in page.items] == [saved.id]
    assert page.coverage == "index_pending"
    index.backfill(scope, batch_size=1)
    assert index.search(scope, "historicalrecord").coverage == "complete"


def test_backfill_rejects_unstable_legacy_continuation(repo):
    items = [repo.save_manual("alice", write("old", entity=str(i)), ("openai",)) for i in range(3)]
    items.sort(key=lambda item: item.id)
    for item in items[1:]:
        repo.update("alice", item.id, {"content": "matching"}, item.revision)
    index, scope = index_for(repo)
    with repo.db:
        repo.db.execute("DELETE FROM memory_terms")
        repo.db.execute("DELETE FROM memory_indexed")
    first = index.search(scope, "matching", limit=1)
    assert first.next_cursor
    index.backfill(scope)
    with pytest.raises(MemoryFailure, match="memory_conflict"):
        index.search(scope, "matching", cursor=first.next_cursor, limit=1)
    assert len(index.search(scope, "matching").items) == 2


def test_cursor_owner_query_and_mutation(repo):
    for i in range(4):
        repo.save_manual("alice", write(f"shared fact{i}", entity=str(i)), ("openai",))
    index, scope = index_for(repo)
    cursor = index.search(scope, "shared", limit=1).next_cursor
    assert cursor
    _, bob = index_for(repo, "bob")
    with pytest.raises(MemoryFailure):
        index.search(bob, "shared", cursor)
    with pytest.raises(MemoryFailure):
        index.search(scope, "changed", cursor)
    repo.save_manual("alice", write("new", entity="new"), ("openai",))
    with pytest.raises(MemoryFailure, match="memory_conflict"):
        index.search(scope, "shared", cursor)


def test_index_filters_before_decrypt_and_updates_on_edit_delete(repo, monkeypatch):
    saved = repo.save_manual("alice", write("rare privacytoken"), ("openai",))
    index, scope = index_for(repo)
    changed = repo.update("alice", saved.id, {"content": "replacement newtoken"}, 1)
    index, scope = index_for(repo)
    assert index.search(scope, "privacytoken").items == ()
    assert [r.id for r in index.search(scope, "newtoken").items] == [saved.id]
    _, forbidden = index_for(repo, provider="deepseek")
    monkeypatch.setattr(repo.crypto, "decrypt", lambda *args: pytest.fail("unauthorized decrypt"))
    assert index.search(forbidden, "newtoken").items == ()
    monkeypatch.undo()
    repo.delete("alice", saved.id, changed.revision)
    index, scope = index_for(repo)
    assert index.search(scope, "newtoken").items == ()


def test_candidate_pages_do_not_decrypt_entire_matching_corpus(repo, monkeypatch):
    for i in range(80):
        repo.save_manual("alice", write("common fact", entity=str(i)), ("openai",))
    index, scope = index_for(repo)
    decrypt = repo.crypto.decrypt
    calls = []
    def observed(*args):
        calls.append(1)
        return decrypt(*args)
    monkeypatch.setattr(repo.crypto, "decrypt", observed)
    page = index.search(scope, "common", limit=3)
    assert len(page.items) == 3 and page.next_cursor
    assert len(calls) <= 4


def test_management_search_requires_all_words_and_model_search_ranks_overlap(repo):
    first = repo.save_manual("alice", write("sentinel-0 background", entity="first"), ("openai",))
    repo.save_manual("alice", write("sentinel-1 background", entity="other"), ("openai",))
    from gaia_agent.interactive.memory.layer_models import MemoryScope
    index, scope = index_for(repo)
    management = MemoryScope("alice", "management", scope.generation)
    assert [r.id for r in index.search(management, "sentinel-0").items] == [first.id]
    assert index.search(scope, "sentinel-0").items[0].id == first.id
