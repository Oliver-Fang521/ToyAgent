"""Migration must preserve existing encrypted bytes and transaction ownership."""

import sqlite3
from threading import RLock

import pytest

from gaia_agent.interactive.memory.crypto import MemoryCrypto
from gaia_agent.interactive.memory.models import MemoryFailure
from gaia_agent.interactive.memory.repository import MemoryRepository


def test_migration_is_additive_idempotent_and_preserves_v1(tmp_path):
    from gaia_agent.interactive.memory.schema import migrate_memory_schema

    db = sqlite3.connect(tmp_path / "old.sqlite")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE memory_settings(owner TEXT PRIMARY KEY, enabled INTEGER, "
        "revision INTEGER, generation INTEGER, capture_after INTEGER, hash_version TEXT)"
    )
    db.execute("INSERT INTO memory_settings VALUES('alice',1,7,9,12,'v1')")
    db.execute(
        "CREATE TABLE memory_tombstones(owner TEXT, canonical_key TEXT, "
        "facet_hash TEXT, hash_version TEXT, source_watermark INTEGER, created_at REAL)"
    )
    db.execute("INSERT INTO memory_tombstones VALUES('alice','opaque','hash','v1',11,1)")
    db.commit()
    before = tuple(db.execute("SELECT * FROM memory_tombstones").fetchone())
    migrate_memory_schema(db)
    migrate_memory_schema(db)
    row = db.execute("SELECT * FROM memory_settings").fetchone()
    assert (row["revision"], row["generation"], row["capture_after"], row["hash_version"]) == (
        7,
        9,
        12,
        "v1",
    )
    assert row["content_revision"] == 0
    assert tuple(db.execute("SELECT * FROM memory_tombstones").fetchone()) == before
    db.close()


def test_migration_does_not_commit_callers_transaction():
    from gaia_agent.interactive.memory.schema import migrate_memory_schema

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE sentinel(value INTEGER)")
    db.execute("INSERT INTO sentinel VALUES(1)")
    migrate_memory_schema(db)
    db.rollback()
    assert db.execute("SELECT * FROM sentinel").fetchall() == []


def test_legacy_record_metadata_defaults_and_encrypted_bytes_survive(tmp_path):
    from test_memory_repository import write

    db = sqlite3.connect(tmp_path / "memory.sqlite")
    db.row_factory = sqlite3.Row
    crypto = MemoryCrypto({"v1": b"a" * 32}, "v1")
    repo = MemoryRepository(db, RLock(), crypto)
    item = repo.save_manual("alice", write(), ("openai", "deepseek"))
    before = tuple(db.execute("SELECT * FROM memory_entries").fetchone())
    reopened = MemoryRepository(db, RLock(), crypto)
    record = reopened.get("alice", item.id)
    assert record.slot_id == item.id
    assert record.topic == "learning_subjects"
    assert record.cardinality == "single"
    assert record.origin_kind == "manual"
    assert tuple(db.execute("SELECT * FROM memory_entries").fetchone()) == before
    assert record.revision == 1
    assert record.allowed_providers == ("openai", "deepseek")
    db.close()


def test_closed_database_has_safe_failure():
    from gaia_agent.interactive.memory.event_store import MemoryEventStore

    db = sqlite3.connect(":memory:")
    db.close()
    with pytest.raises(MemoryFailure, match="^memory_unavailable$"):
        MemoryEventStore(db, RLock(), MemoryCrypto({"v1": b"a" * 32}, "v1"))


def event_values():
    return {
        "event_id": "00000000-0000-4000-8000-000000000001",
        "owner": "alice",
        "provider": "openai",
        "source_run_id": "run-one",
        "source_seq": 1,
        "memory_generation": 0,
        "credential_epoch": 0,
        "credential_source": "saved",
        "model": "test-model",
        "reasoning_effort": "low",
        "capability_version": "v1",
        "source": "我是方林",
    }


def test_event_source_and_model_are_encrypted_on_disk(tmp_path):
    from gaia_agent.interactive.memory.event_store import MemoryEventStore
    from gaia_agent.interactive.memory.models import MemoryEvent

    path = tmp_path / "events.sqlite"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    store = MemoryEventStore(db, RLock(), MemoryCrypto({"v1": b"a" * 32}, "v1"))
    event = MemoryEvent(**event_values())
    store.put(event, now=100)
    assert store.get("alice", event.event_id) == event
    with pytest.raises(MemoryFailure, match="memory_not_found"):
        store.get("bob", event.event_id)
    raw = path.read_bytes() + path.with_name(path.name + "-wal").read_bytes()
    assert "我是方林".encode() not in raw
    assert b"test-model" not in raw
    assert b'"reasoning_effort"' not in raw
    db.close()


def test_event_bounds_and_operation_spans():
    from pydantic import ValidationError

    from gaia_agent.interactive.memory.models import MemoryEvent, MemoryOperation

    with pytest.raises(ValidationError):
        MemoryEvent(**(event_values() | {"source": "汉" * 2667}))
    event = MemoryEvent(**event_values())
    with pytest.raises(ValidationError):
        event.source = "changed"
    with pytest.raises(ValidationError):
        MemoryOperation(
            action="add",
            category="profile",
            topic="name",
            entity="self",
            cardinality="single",
            content="方林",
            source_start=4,
            source_end=1,
            source_kind="user",
            intent="automatic",
        )
