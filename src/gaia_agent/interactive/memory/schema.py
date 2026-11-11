"""Additive migrations; callers own locking and transaction commit.

Do not use executescript: sqlite3 implicitly commits an outer transaction.
"""

import sqlite3

from .models import MemoryFailure


def migrate_memory_schema(db) -> None:
    try:
        db.execute("SAVEPOINT memory_schema_v2")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_dirty_topics (
            owner TEXT NOT NULL, provider TEXT NOT NULL, topic_id TEXT NOT NULL,
            PRIMARY KEY(owner,provider,topic_id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_maintenance (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, provider TEXT NOT NULL,
            topic_id TEXT NOT NULL, revision INTEGER NOT NULL, state TEXT NOT NULL,
            ready_at REAL NOT NULL, lease_id TEXT, lease_until REAL,
            retry_count INTEGER NOT NULL DEFAULT 0, failure_code TEXT,
            version TEXT NOT NULL, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
            UNIQUE(owner,provider,topic_id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_maintenance_dispatch (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, owner TEXT NOT NULL, provider TEXT NOT NULL,
            dispatched_at REAL NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0)""")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_topic_members (
            owner TEXT NOT NULL, provider TEXT NOT NULL, topic_id TEXT NOT NULL, fact_id TEXT NOT NULL,
            PRIMARY KEY(owner,provider,fact_id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_summary_nodes (
            job_id TEXT NOT NULL, level INTEGER NOT NULL, position INTEGER NOT NULL,
            summary_id TEXT NOT NULL, PRIMARY KEY(job_id,level,position))""")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_overview_inputs (
            job_id TEXT NOT NULL, position INTEGER NOT NULL, summary_id TEXT NOT NULL,
            revision INTEGER NOT NULL, PRIMARY KEY(job_id,position))""")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_summaries (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, provider TEXT NOT NULL,
            kind TEXT NOT NULL, scope_key TEXT NOT NULL, revision INTEGER NOT NULL,
            state TEXT NOT NULL, version TEXT NOT NULL, nonce BLOB NOT NULL,
            ciphertext BLOB NOT NULL, updated_at REAL NOT NULL,
            UNIQUE(owner,provider,scope_key))""")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_summary_deps (
            owner TEXT NOT NULL, summary_id TEXT NOT NULL, source_id TEXT NOT NULL,
            source_revision INTEGER NOT NULL, source_kind TEXT NOT NULL,
            PRIMARY KEY(owner,summary_id,source_id,source_kind))""")
        db.execute("CREATE INDEX IF NOT EXISTS memory_summary_sources ON memory_summary_deps(owner,source_id)")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_terms (
            owner TEXT NOT NULL, fact_id TEXT NOT NULL, term TEXT NOT NULL,
            PRIMARY KEY(owner,term,fact_id))""")
        db.execute("CREATE INDEX IF NOT EXISTS memory_terms_fact ON memory_terms(owner,fact_id)")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_indexed (
            owner TEXT NOT NULL, fact_id TEXT NOT NULL, revision INTEGER NOT NULL,
            PRIMARY KEY(owner,fact_id))""")
        db.execute("CREATE TABLE IF NOT EXISTS memory_index_epochs(owner TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0)")
        for action, row in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS memory_index_epoch_{action.lower()}
                AFTER {action} ON memory_indexed BEGIN
                INSERT INTO memory_index_epochs(owner,revision) VALUES({row}.owner,1)
                ON CONFLICT(owner) DO UPDATE SET revision=revision+1;
                END""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(memory_settings)")}
        if columns and "content_revision" not in columns:
            db.execute(
                "ALTER TABLE memory_settings ADD COLUMN content_revision INTEGER NOT NULL DEFAULT 0"
            )
        db.execute("""CREATE TABLE IF NOT EXISTS memory_jobs (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL,
            provider TEXT NOT NULL CHECK(provider IN ('openai','deepseek')),
            source_run_id TEXT NOT NULL, source_seq INTEGER NOT NULL,
            memory_generation INTEGER NOT NULL, credential_epoch INTEGER NOT NULL,
            credential_source TEXT NOT NULL CHECK(credential_source IN ('saved','temporary')),
            state TEXT NOT NULL CHECK(state IN (
                'pending','leased','provider_started','proposed','completed','unchanged',
                'needs_clarification','blocked','failed','uncertain')),
            version TEXT NOT NULL, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
            proposal_version TEXT, proposal_nonce BLOB, proposal_ciphertext BLOB,
            lease_id TEXT, worker_id TEXT, lease_until REAL,
            dispatch_count INTEGER NOT NULL DEFAULT 0 CHECK(dispatch_count BETWEEN 0 AND 2),
            retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND 1),
            dispatched_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            expires_at REAL NOT NULL, failure_code TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            UNIQUE(owner,source_run_id))""")
        db.execute("CREATE INDEX IF NOT EXISTS memory_jobs_claim ON memory_jobs(state,source_seq)")
        db.execute("CREATE INDEX IF NOT EXISTS memory_jobs_owner ON memory_jobs(owner,state)")
        job_columns = {row[1] for row in db.execute("PRAGMA table_info(memory_jobs)")}
        for name, declaration in (
            ("ready", "INTEGER NOT NULL DEFAULT 1"),
            ("dispatch_lease_id", "TEXT"),
        ):
            if name not in job_columns:
                db.execute(f"ALTER TABLE memory_jobs ADD COLUMN {name} {declaration}")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_admissions (
            owner TEXT NOT NULL, run_id TEXT NOT NULL, state TEXT NOT NULL,
            reason TEXT NOT NULL, PRIMARY KEY(owner,run_id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_credential_epochs (
            owner TEXT NOT NULL, provider TEXT NOT NULL, epoch INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(owner,provider))""")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_projections (
            owner TEXT NOT NULL, provider TEXT NOT NULL, generation INTEGER NOT NULL,
            content_revision INTEGER NOT NULL, id TEXT NOT NULL,
            version TEXT NOT NULL, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
            PRIMARY KEY(owner,provider))""")
        db.execute("""CREATE TABLE IF NOT EXISTS memory_barriers (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, revision INTEGER NOT NULL,
            source_watermark INTEGER NOT NULL, provider_mask INTEGER NOT NULL,
            version TEXT NOT NULL, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
            created_at REAL NOT NULL)""")
        db.execute("RELEASE memory_schema_v2")
    except sqlite3.Error:
        try:
            db.execute("ROLLBACK TO memory_schema_v2")
            db.execute("RELEASE memory_schema_v2")
        except sqlite3.Error:
            pass
        raise MemoryFailure("memory_unavailable") from None
