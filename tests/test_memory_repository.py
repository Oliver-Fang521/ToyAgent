import sqlite3
from threading import RLock

import pytest

from gaia_agent.interactive.memory.crypto import MemoryCrypto
from gaia_agent.interactive.memory.models import MemoryCandidate, MemoryFailure, MemoryWrite
from gaia_agent.interactive.memory.repository import MemoryRepository


def write(content="I study Python", facet="learning_subjects", entity="self"):
    return MemoryWrite(
        MemoryCandidate(
            category="profile",
            facet=facet,
            entity=entity,
            content=content,
            source_start=0,
            source_end=len(content),
            explicit_relearn=False,
        ),
        f"profile:{facet}:{entity}",
        content,
    )


def test_list_snapshot_generation_and_owner_are_atomic(repo):
    saved = repo.save_manual("alice", write(), ("openai",))
    repo.save_manual("bob", write("other private memory"), ("deepseek",))
    snapshot = repo.list_snapshot("alice")
    assert snapshot.settings.generation == repo.settings("alice").generation
    assert [item.content for item in snapshot.items] == ["I study Python"]
    assert (
        not {"owner", "canonical_key", "source_fragment", "ciphertext"}
        & snapshot.items[0].model_dump().keys()
    )
    repo.delete("alice", saved.id, saved.revision)
    after = repo.list_snapshot("alice")
    assert after.items == ()
    assert after.settings.generation > snapshot.settings.generation


@pytest.fixture
def repo(tmp_path):
    db = sqlite3.connect(tmp_path / "memory.sqlite", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    repository = MemoryRepository(db, RLock(), MemoryCrypto({"v1": b"a" * 32}, "v1"))
    yield repository
    db.close()


def test_default_enabled_without_backfill(repo):
    assert repo.settings("alice").enabled
    assert repo.list("alice") == []


def test_disabled_survives_restart(repo):
    repo.set_enabled("alice", False, 0)
    reopened = MemoryRepository(repo.db, repo.lock, repo.crypto)
    assert reopened.settings("alice").enabled is False
    with pytest.raises(MemoryFailure, match="memory_invalid"):
        repo.set_enabled("alice", 1, 1)


def test_owner_and_provider_filter_before_decrypt(repo, monkeypatch):
    repo.save_manual("alice", write(), ("openai",))

    def forbidden(*args):
        pytest.fail("Unauthorized record decrypted")

    monkeypatch.setattr(repo.crypto, "decrypt", forbidden)
    assert repo.eligible("bob", "openai", 0) == ()
    assert repo.eligible("alice", "deepseek", 0) == ()


def test_manual_crud_without_model_key(repo):
    item = repo.save_manual("alice", write(), ("openai",))
    changed = repo.update("alice", item.id, {"content": "I study Rust"}, item.revision)
    assert changed.content == "I study Rust"
    assert changed.edited_by_user
    assert changed.revision == 2
    for action in (
        lambda: repo.get("bob", item.id),
        lambda: repo.update("bob", item.id, {"content": "X"}, 2),
        lambda: repo.delete("bob", item.id, 2),
    ):
        with pytest.raises(MemoryFailure) as error:
            action()
        assert error.value.status_code == 404


def test_stale_revision_conflicts(repo):
    item = repo.save_manual("alice", write(), ("openai",))
    before = repo.settings("alice")
    with pytest.raises(MemoryFailure, match="memory_conflict"):
        repo.update("alice", item.id, {"content": "wrong"}, 0)
    assert repo.settings("alice") == before
    assert repo.get("alice", item.id).content == "I study Python"


@pytest.mark.parametrize("expiry", [float("nan"), float("inf"), float("-inf")])
def test_repository_rejects_nonfinite_expiry_without_mutation(repo, expiry):
    item = repo.save_manual("alice", write(), ("openai",))
    before = repo.settings("alice")
    with pytest.raises(MemoryFailure, match="memory_invalid"):
        repo.update("alice", item.id, {"expires_at": expiry}, item.revision)
    assert repo.settings("alice") == before
    assert repo.get("alice", item.id).expires_at is None


def test_expiry_and_explicit_provider_sharing(repo):
    item = repo.save_manual("alice", write(), ("openai",))
    assert repo.eligible("alice", "deepseek", 0) == ()
    updated = repo.update(
        "alice",
        item.id,
        {"allowed_providers": ("openai", "deepseek"), "expires_at": repo.clock() + 60},
        item.revision,
    )
    assert [r.id for r in repo.eligible("alice", "deepseek", repo.clock())] == [item.id]
    assert repo.eligible("bob", "deepseek", 0) == ()
    assert repo.eligible("alice", "openai", updated.expires_at) == ()
    assert repo.eligible("alice", "deepseek", updated.expires_at + 1) == ()


def test_delete_purges_payload_keeps_opaque_barrier(repo, tmp_path):
    item = repo.save_manual("alice", write(), ("openai",))
    # Synthetic pre-migration deletion retains its original v1 protection.
    with repo.lock, repo.db:
        repo._erase("alice", repo._row("alice", item.id), repo._settings("alice"))
    assert repo.list("alice") == []
    assert repo.db.execute("SELECT count(*) FROM memory_tombstones").fetchone()[0] >= 1
    for path in tmp_path.iterdir():
        assert b"I study Python" not in path.read_bytes()
        assert b"profile:learning_subjects:self" not in path.read_bytes()


def test_rotation_cannot_revive_deleted_key(repo):
    item = repo.save_manual("alice", write(), ("openai",))
    with repo.lock, repo.db:
        repo._erase("alice", repo._row("alice", item.id), repo._settings("alice"))
    rotated = MemoryRepository(
        repo.db, repo.lock, MemoryCrypto({"v1": b"a" * 32, "v2": b"b" * 32}, "v2")
    )
    rotated.register_turn("alice", "new")
    assert (
        rotated.apply_candidates(
            "alice", "openai", "new", (write(),), rotated.settings("alice").generation
        )
        == ()
    )
    missing = MemoryRepository(repo.db, repo.lock, MemoryCrypto({"v2": b"b" * 32}, "v2"))
    with pytest.raises(MemoryFailure, match="memory_unavailable"):
        missing.settings("alice")


def test_more_than_200_records_preserves_manual_records(repo):
    for index in range(200):
        repo.save_manual("alice", write(entity=str(index)), ("openai",))
    repo.save_manual("alice", write(entity="overflow"), ("openai",))
    assert sum(len(repo.list("alice", offset=offset)) for offset in range(0, 201, 50)) == 201


def test_clear_blocks_pre_watermark_turns(repo):
    old = repo.register_turn("alice", "old")
    repo.clear("alice")
    assert repo.settings("alice").capture_after >= old
    assert (
        repo.apply_candidates(
            "alice", "openai", "old", (write(),), repo.settings("alice").generation
        )
        == ()
    )


def test_automatic_merge_is_idempotent_and_cannot_override_manual(repo):
    generation = repo.settings("alice").generation
    seq = repo.register_turn("alice", "one")
    assert repo.register_turn("alice", "one") == seq
    ids = repo.apply_candidates("alice", "openai", "one", (write(),), generation)
    assert len(ids) == 1
    assert repo.settings("alice").generation == generation
    assert repo.apply_candidates("alice", "openai", "one", (write(),), generation) == ()
    repo.update("alice", ids[0], {"content": "I study Rust"}, 1)
    repo.register_turn("alice", "two")
    assert (
        repo.apply_candidates(
            "alice", "openai", "two", (write(),), repo.settings("alice").generation
        )
        == ()
    )
    assert repo.get("alice", ids[0]).content == "I study Rust"


def test_automatic_memory_shared_across_providers_and_isolated_by_owner(repo):
    generation=repo.settings('alice').generation
    repo.register_turn('alice','shared-turn')
    ids=repo.apply_candidates('alice','openai','shared-turn',(write('I study math'),),generation)
    assert len(ids)==1
    assert [x.content for x in repo.eligible('alice','deepseek',repo.clock())]==['I study math']
    assert repo.eligible('bob','deepseek',repo.clock())==()


def test_existing_provider_memory_migrates_once_and_respects_later_user_choice(repo):
    a=repo.save_manual('alice',write('I study math'),('openai',))
    b=repo.save_manual('bob',write('I study music'),('openai',))
    with repo.lock,repo.db:
        repo.db.execute('DELETE FROM memory_shared_migration WHERE owner=?',('alice',))
    assert [x.content for x in repo.eligible('alice','deepseek',repo.clock())]==['I study math']
    assert repo.db.execute('select provider_mask from memory_entries where id=?',(b.id,)).fetchone()[0]==1
    updated=repo.update('alice',a.id,{'allowed_providers':('openai',)},a.revision)
    assert updated.allowed_providers==('openai',)
    assert repo.eligible('alice','deepseek',repo.clock())==()


def test_deepseek_origin_is_preserved_and_openai_can_read(repo):
    generation=repo.settings('alice').generation
    repo.register_turn('alice','deepseek-turn')
    ids=repo.apply_candidates('alice','deepseek','deepseek-turn',(write('I use Linux'),),generation)
    record=repo.get('alice',ids[0])
    assert record.origin_provider=='deepseek'
    assert {x.content for x in repo.eligible('alice','openai',repo.clock())}=={'I use Linux'}


def test_migration_shares_permanent_forgetting_barrier(repo):
    from gaia_agent.interactive.memory.barriers import MemoryBarriers
    item=repo.save_manual('alice',write('I study math'),('openai',))
    repo.delete('alice',item.id,item.revision,permanent=True)
    assert not MemoryBarriers(repo).visible('alice','deepseek')
    with repo.lock,repo.db:
        repo.db.execute('DELETE FROM memory_shared_migration WHERE owner=?',('alice',))
    repo.settings('alice')
    assert len(MemoryBarriers(repo).visible('alice','deepseek'))==1
    assert repo.eligible('alice','deepseek',repo.clock())==()
