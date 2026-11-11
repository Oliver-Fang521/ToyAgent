"""Persistent, per-domain serialized, explicitly retryable summary dispatches."""

from uuid import uuid4

from .crypto import MemoryEnvelope
from .layer_models import MaintenanceClaim, MemoryScope
from .models import MemoryFailure


class MaintenanceQueue:
    def __init__(self, repository):
        self.repo = repository

    def _data(self, row):
        return self.repo.crypto.decrypt_object(row["owner"], "maintenance", row["id"], MemoryEnvelope(row["version"], row["nonce"], row["ciphertext"]))

    def _write(self, identifier, owner, data):
        e = self.repo.crypto.encrypt_object(owner, "maintenance", identifier, data)
        self.repo.db.execute("UPDATE memory_maintenance SET version=?,nonce=?,ciphertext=? WHERE id=? AND owner=?",
                             (e.version, e.nonce, e.ciphertext, identifier, owner))

    def mark_dirty(self, scope, selection, credential_epoch, topic_ids, *, now=None):
        repo = self.repo
        now = repo.clock() if now is None else now
        with repo.lock, repo.db:
            for topic in set(topic_ids):
                old = repo.db.execute("SELECT * FROM memory_maintenance WHERE owner=? AND provider=? AND topic_id=?",
                                     (scope.owner, scope.provider, topic)).fetchone()
                if old is not None and not old["ciphertext"]:
                    old = None  # Erased prior work cannot be decrypted or resurrected.
                # A dispatched failed version never becomes retryable just because another turn ran.
                revision = repo.state_snapshot(scope.owner, scope.provider).content_revision
                if old and self._data(old).get("generation") == scope.generation and old["revision"] == revision and old["state"] not in {"completed", "pending"}:
                    continue
                identifier = old["id"] if old else str(uuid4())
                data = {"selection": selection, "epoch": credential_epoch, "generation": scope.generation,
                        "phase": "shard", "offset": 0, "level": 0}
                if old and old['state'] in {'leased', 'dispatched'}:
                    active = self._data(old)
                    active['successor'] = dict(data, revision=revision, ready_at=now + 10)
                    self._write(identifier, scope.owner, active)
                    continue
                e = repo.crypto.encrypt_object(scope.owner, "maintenance", identifier, data)
                repo.db.execute("INSERT OR REPLACE INTO memory_maintenance VALUES(?,?,?,?,?,'pending',?,NULL,NULL,0,NULL,?,?,?)",
                                (identifier, scope.owner, scope.provider, topic, revision, now + 10, e.version, e.nonce, e.ciphertext))
                repo.db.execute("DELETE FROM memory_summary_nodes WHERE job_id=?", (identifier,))
                repo.db.execute("DELETE FROM memory_overview_inputs WHERE job_id=?", (identifier,))

    def activate_dirty(self, event):
        # Only new changes are eligible; an old database is not automatically bulk summarized.
        repo = self.repo
        with repo.lock, repo.db:
            topics = tuple(r[0] for r in repo.db.execute("SELECT topic_id FROM memory_dirty_topics WHERE owner=? AND provider=?", (event.owner, event.provider)))
            self.mark_dirty(repo.state_snapshot(event.owner, event.provider).scope,
                {"model": event.model, "reasoning_effort": event.reasoning_effort,
                 "capability_version": event.capability_version, "credential_source": event.credential_source,
                 "source_run_id": event.source_run_id}, event.credential_epoch, topics)
            repo.db.execute("DELETE FROM memory_dirty_topics WHERE owner=? AND provider=?", (event.owner, event.provider))

    def recover(self):
        with self.repo.lock, self.repo.db:
            self.repo.db.execute("UPDATE memory_maintenance SET state='uncertain',failure_code='dispatch_uncertain',lease_id=NULL WHERE state='dispatched'")
            self.repo.db.execute("UPDATE memory_maintenance SET state='pending',lease_id=NULL WHERE state='leased'")

    def claim(self, now, worker_id):
        repo = self.repo
        with repo.lock, repo.db:
            rows = repo.db.execute("""SELECT * FROM memory_maintenance j WHERE state='pending' AND ready_at<=?
                AND NOT EXISTS(SELECT 1 FROM memory_maintenance active WHERE active.owner=j.owner AND active.provider=j.provider AND active.state IN ('leased','dispatched'))
                AND (SELECT COUNT(*) FROM memory_maintenance_dispatch d WHERE d.owner=j.owner AND d.provider=j.provider AND d.dispatched_at>?)<4
                ORDER BY ready_at,id LIMIT 1""", (now, now - 3600)).fetchall()
            if not rows:
                return None
            row = rows[0]
            data = self._data(row)
            state = repo.state_snapshot(row['owner'], row['provider'])
            if not state.enabled:
                repo.db.execute("UPDATE memory_maintenance SET state='blocked',failure_code='memory_conflict' WHERE id=?", (row['id'],))
                return None
            # Pending work has not dispatched: safely restart its derivations on the
            # latest canonical version instead of permanently stranding old topics.
            revision = row['revision']
            if data['generation'] != state.scope.generation or revision != state.content_revision:
                revision = state.content_revision
                data.update(generation=state.scope.generation, phase='shard', offset=0, level=0)
                data.pop('output_topic', None)
                data.pop('successor', None)
                self._write(row['id'], row['owner'], data)
                repo.db.execute("UPDATE memory_maintenance SET revision=? WHERE id=?", (revision, row['id']))
                repo.db.execute("DELETE FROM memory_summary_nodes WHERE job_id=?", (row['id'],))
                repo.db.execute("DELETE FROM memory_overview_inputs WHERE job_id=?", (row['id'],))
            lease = str(uuid4())
            repo.db.execute("UPDATE memory_maintenance SET state='leased',lease_id=?,lease_until=? WHERE id=?", (lease, now + 40, row["id"]))
            return MaintenanceClaim(row["id"], MemoryScope(row["owner"], row["provider"], data["generation"]), revision, data["selection"], data["epoch"], data, lease, row["topic_id"])

    def _owned(self, claim):
        row = self.repo.db.execute("SELECT * FROM memory_maintenance WHERE id=? AND owner=? AND lease_id=? AND revision=?", (claim.id, claim.scope.owner, claim.lease_id, claim.input_revision)).fetchone()
        if row is None:
            raise MemoryFailure("memory_conflict", 409)
        return row

    def reserve_dispatch(self, claim, *, now=None):
        repo = self.repo
        now = repo.clock() if now is None else now
        with repo.lock, repo.db:
            row = self._owned(claim)
            state = repo.state_snapshot(claim.scope.owner, claim.scope.provider)
            if row["state"] != "leased" or not state.enabled or state.scope != claim.scope or state.content_revision != claim.input_revision:
                raise MemoryFailure("memory_conflict", 409)
            used = repo.db.execute("SELECT COUNT(*) FROM memory_maintenance_dispatch WHERE owner=? AND provider=? AND dispatched_at>?", (claim.scope.owner, claim.scope.provider, now - 3600)).fetchone()[0]
            if used >= 4:
                raise MemoryFailure("memory_busy", 409)
            repo.db.execute("INSERT INTO memory_maintenance_dispatch(id,job_id,owner,provider,dispatched_at) VALUES(?,?,?,?,?)", (claim.lease_id, claim.id, claim.scope.owner, claim.scope.provider, now))
            repo.db.execute("UPDATE memory_maintenance SET state='dispatched' WHERE id=?", (claim.id,))

    def record_usage(self, claim, usage):
        with self.repo.lock, self.repo.db:
            self.repo.db.execute("UPDATE memory_maintenance_dispatch SET input_tokens=input_tokens+?,output_tokens=output_tokens+? WHERE id=?", (usage.input_tokens, usage.output_tokens, claim.lease_id))

    def finish(self, claim, *, state="completed", code=None, cursor=None, now=None):
        if state not in {"completed", "failed", "uncertain", "blocked", "pending"}:
            raise MemoryFailure("memory_invalid", 422)
        if code not in {None, "memory_timeout", "memory_unavailable", "credential_revoked", "memory_conflict", "dispatch_uncertain"}:
            code = "memory_unavailable"
        with self.repo.lock, self.repo.db:
            owned = self._owned(claim)
            latest = self._data(owned)
            successor = latest.get('successor')
            if successor is not None:
                revision = successor.pop('revision')
                ready_at = successor.pop('ready_at')
                self._write(claim.id, claim.scope.owner, successor)
                self.repo.db.execute("UPDATE memory_maintenance SET state='pending',revision=?,lease_id=NULL,lease_until=NULL,retry_count=0,failure_code=NULL,ready_at=? WHERE id=?", (revision, ready_at, claim.id))
                self.repo.db.execute("DELETE FROM memory_summary_nodes WHERE job_id=?", (claim.id,))
                self.repo.db.execute("DELETE FROM memory_overview_inputs WHERE job_id=?", (claim.id,))
                return
            if cursor is not None:
                self._write(claim.id, claim.scope.owner, cursor)
            self.repo.db.execute("UPDATE memory_maintenance SET state=?,failure_code=?,lease_id=NULL,ready_at=? WHERE id=?", (state, code, self.repo.clock() if now is None else now, claim.id))

    def retry(self, owner, job_id, confirm_cost):
        if confirm_cost is not True:
            raise MemoryFailure("memory_invalid", 422)
        with self.repo.lock, self.repo.db:
            result = self.repo.db.execute("UPDATE memory_maintenance SET state='pending',retry_count=1,ready_at=0,failure_code=NULL WHERE owner=? AND id=? AND state IN ('failed','uncertain') AND retry_count=0", (owner, job_id))
            if result.rowcount != 1:
                raise MemoryFailure("memory_conflict", 409)

    def status(self, owner):
        with self.repo.lock:
            return [dict(r) for r in self.repo.db.execute("SELECT id,provider,state,failure_code,retry_count FROM memory_maintenance WHERE owner=? ORDER BY ready_at DESC LIMIT 50", (owner,))]
