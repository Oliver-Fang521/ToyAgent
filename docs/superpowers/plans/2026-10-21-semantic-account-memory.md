# Semantic Account Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed-field, synchronous memory extraction with reliable asynchronous semantic memory that learns across conversations and respects ordinary deletion versus explicit permanent forgetting.

**Architecture:** Persist encrypted memory-input jobs, let bounded workers propose semantic operations, then validate and publish them transactionally. Canonical records remain authoritative; generation/revision-scoped summary/index projections support progressive retrieval. Adapt Codex's extraction/consolidation/read separation without copying plaintext memory folders or granting a memory agent filesystem/tool authority.

**Tech Stack:** Existing Python/FastAPI/SQLite/AES-GCM, current OpenAI/DeepSeek providers, React/TypeScript/Vinext, Docker Compose. No new external memory service, vector database or provider Key.

**Spec:** `docs/superpowers/specs/2026-10-20-semantic-account-memory-design.md` (including the 2026-10-21 asynchronous revision).
**Source study:** `docs/superpowers/reports/2026-10-21-codex-memory-source-study.md`; Codex commit `430a16799d90e296fb27b1e94f206023902b7e56`.
**Execution:** Preserve the chosen native/in-session implementation method, followed by one independent final review. No implementation before user review of this plan.

## Global Constraints

- 语义判断由接入模型完成，不依赖“请记住”等固定关键词或封闭 facet 白名单；规则只作安全/格式检查。
- 不要求保存完成后立刻确认；不阻塞正常回复，不每轮提示记忆更新；排队绝不等于已保存。
- profile/project/decision 保留为粗分类；用户正文不翻译，新增 UI 中英双语。
- 普通删除不新增永久屏障，不改变自动学习；明确永久遗忘才保护该信息，其他信息继续学习。
- 旧 ID/revision/provider mask、密钥版本、tombstone 和 clear 水位保留；不回填旧聊天，不重置真实账户。
- 所有事件、记录、主题、概要按账户和 provider 权限隔离；可读内容加密，索引仅 opaque ID/HMAC；不保存秘密、隐藏推理或原始工具大输出。
- 检索概要与明细合计最多 8 条记录/6000 UTF-8 bytes；账户最多 200 条规范记录，每条正文最多 500 字符。
- 后台复用提交当时的模型、推理档位和对应用户凭据，不借用其他 Key/provider；不会因临时 Key 失效改用已保存 Key。
- 单实例 SQLite；至少在实际 Python 3.12/Node 22 Compose 镜像验收。更新3025前检查活动任务、先备份；不 push/merge/deploy，不未经授权做真实付费烟测。

## Review Focus

1. Crash after provider dispatch but before commit must not silently repeat paid work or lose already committed memory (Tasks 2, 5).
2. A delayed old candidate, changed Key, permanent deletion or provider-sharing edit must not overwrite newer facts or revive stale summaries (Tasks 2, 4, 6).
3. No-keyword/multilingual explicit saves and ordinary names must survive real extraction; quotations, negations, reminders and assistant guesses must not become false memories (Tasks 3, 8).
4. Ordinary deletion may relearn only from new input; permanent forgetting must block paraphrases without blocking unrelated information, including after restart/migration (Tasks 4, 8).
5. Background billing, temporary-Key expiry and browser status must remain truthful even when extraction/storage fails after the answer succeeds (Tasks 5, 7, 8).

## Shared contracts and bounds

All Python paths below are under `src/gaia_agent/interactive/memory/` unless absolute repo paths are shown. Frontend paths are under `sites/agent-web/`. Commands `PYTEST ...` mean `UV_CACHE_DIR=/tmp/uv-cache /Users/HPOMEN/.local/bin/uv run --no-sync pytest -o addopts='' -q -p no:cacheprovider ...`; `NODE` is the configured bundled Node runtime, not an invented global executable.

`MemoryEvent` binds event_id, owner, provider, source_run_id, source_seq, memory_generation, credential_epoch, credential_source(saved/temporary), model, reasoning_effort and capability_version. Source text is encrypted and pre-redacted, max 8000 UTF-8 bytes; oversize events get a safe terminal input_too_large state rather than silent truncation. No raw Key, bearer handle or readable personal text in job columns/logs.

`MemoryOperation` contains action(add/append/replace/delete/forget/restore/noop/clarify), category, topic (1..64 chars), entity (1..128), cardinality(single/set), content (1..500 where applicable), source spans, source_kind(user/verified_task), intent(automatic/explicit/clarify), optional target_id/expected_revision and barrier_id references. Referenced IDs must belong to the server-supplied eligible view. Max 5 operations/event. Ordinary automatic self-reports require exact source spans; explicit non-first-person notes are allowed. A derived task lesson must cite a verified structured observation, never become a profile fact.

`MemoryJobState`: pending/leased/provider_started/proposed/completed/unchanged/needs_clarification/blocked/failed/uncertain. Every transition is owner-bound and recorded durably. Control `generation` invalidates old work; independent `content_revision` invalidates projections without cancelling every foreground answer when background memory is published.

Worker defaults: 2 jobs globally, 1 per account (therefore at most 1 per account/provider); at most 100 nonterminal jobs/account and 1000 globally. Admission of normal chat remains independent: this is not a new hourly question limit. One model dispatch/job, no automatic paid retry, timeout30s, output≤1600 tokens, zero model tools, request input≤16000 UTF-8 bytes. Each dispatch is reserved durably before the call. Storage publication may retry one non-paid CAS attempt with the same validated proposal; stale evidence cannot be replanned automatically at extra cost. Lost lease after provider_started becomes uncertain and requires explicit retry, not automatic recharge. Lease60s, heartbeat10s, poll/wake≤1s, recover unstarted expired leases; jobs expire after24h. Never evict older explicit work to make queue space; queue rejection remains visible as a safe run-level memory status.

Opaque credential epochs for saved Keys persist in SQLite and change transactionally on save/replace/delete, not on process shutdown. Temporary credential material remains only in existing in-memory sessions, referenced via a bounded worker-local opaque token; queued temporary jobs become blocked after expiry/restart. User-requested retry is an explicit paid action on the same event, at most one additional dispatch and never automatic resubmission of the original question. Accepted events outlive a foreground response but do not gain new access permissions.

---

### Task 1: Versioned semantic records, object encryption and migration

**Files:** Modify `models.py`, `crypto.py`, `repository.py`; Create `schema.py`, `event_store.py`; tests `tests/test_memory_schema.py`, `tests/test_memory_crypto.py`, `tests/test_memory_repository.py`.
**Interfaces:** Produce immutable `MemoryEvent`, `MemoryOperation`, `MemoryJobState`, `MemoryBatchResult`; `migrate_memory_schema(db) -> None`; additive `MemoryCrypto.encrypt_object(owner, kind, identifier, payload)` / `decrypt_object(...)` with distinct v2 AAD domains for event/proposal/projection/barrier. Existing encrypt/decrypt remain byte-compatible. `MemoryView/Record` gain optional topic/slot_id/cardinality/origin_kind with legacy defaults; existing id serves as legacy slot_id. `MemoryEventStore(db, lock, crypto)` owns new job tables, not the existing ConversationStore's business logic.

- [ ] Add RED tests: migrate a synthetic populated v1 DB twice; same IDs, revisions, key versions, masks and barriers; old records decrypt; ciphertext moved across owner/object-kind/id rejected; new plaintext source/topic/key values absent from SQLite/WAL; content_revision starts0 without modifying control generation.
- [ ] Run `PYTEST tests/test_memory_schema.py tests/test_memory_crypto.py tests/test_memory_repository.py`; expect new-contract failures.
- [ ] Implement additive migrations under existing RLock/transaction boundaries; version encrypted payloads, keep old HMAC key pinning, avoid nested context commits. New jobs have encrypted payload and durable dispatch/lease/status/usage metadata; plaintext columns restricted to existing owner IDs, opaque references, numeric counters and finite safe enums.
- [ ] Run same tests GREEN, including closed DB/integrity failures with no input in exceptions. Commit explicit task files: `feat: version semantic memory records and encrypted events`.

### Task 2: Durable queue, dispatch journal and credential lifetime

**Files:** Modify `event_store.py`, repo files `credential_vault.py`, `credential_sessions.py`, `credential_api.py`; Create `queue.py`; tests `tests/test_memory_queue.py`, `tests/test_memory_credential_lifetime.py`.
**Interfaces:** `MemoryQueue.enqueue(event) -> MemoryJobView`, `claim(now, worker_id) -> MemoryJobLease|None`, `mark_dispatched(lease)`, `store_proposal(lease, proposal)`, `finish(lease, result)`, `recover(now)`, `invalidate(owner, reason)`; every method validates lease ownership. `CredentialVault.background_epoch(owner, provider) -> int`; save/delete update persistent epochs atomically. `CredentialSessions` exposes a bounded worker-local temporary reference, never serializes material/handles. A failed queue admission is represented in safe per-run memory status even if no queue row could be created.

- [ ] Add RED tests for unique source_run_id idempotency, FIFO source_seq, two competing claimants, account/global caps, owner spoofing, lease heartbeat/loss, safe unstarted recovery versus uncertain paid dispatch, proposal persisted before crash then committed without another model call. Include saved-Key replacement/delete across restart and temporary expiry with a saved Key also present: no fallback.
- [ ] Run `PYTEST tests/test_memory_queue.py tests/test_memory_credential_lifetime.py tests/test_credential_api.py`; observe RED.
- [ ] Implement journal transitions and exact bounds above, stable persisted saved epochs separate from current process session generations. Config/key mutation hooks invalidate queued/running memory jobs before returning; shutdown only clears ephemeral references, not saved epochs. No status response reveals handles or source content.
- [ ] Run same tests GREEN; commit: `feat: add durable bounded memory job queue`.

### Task 3: Semantic extraction and evidence-checked operation proposals

**Files:** Modify `extraction.py`, `policy.py`; Create `semantic.py`; Modify `tests/test_memory_extraction.py`, `tests/test_memory_policy.py`; Create `tests/test_memory_semantic.py`.
**Interfaces:** `SemanticMemoryPlanner.plan(event, visible_records, visible_barriers, observations, *, usage_observer) -> tuple[MemoryOperation,...]`; produces strict structured operations only, never direct database mutations. `MemoryPolicy.validate_operation(event, operation, eligible_ids) -> ValidatedMemoryOperation|None` with typed safe reason. `prepare_event_source(text)` redacts secrets before persistence while preserving exact spans in the sanitized source; candidate spans cannot include redaction placeholders.

- [ ] Add failing contract tests with real provider request construction and controlled transport responses: “我是方林”, no-old-facet facts, “以后叫我小林”, “这个偏好以后都按这个来”, “帮我记下来，下次还要用”, “Save this for future conversations”, explicit non-first-person deadline, five atomic facts, append versus correction. Assert request has no facet enum/keyword prerequisite, at most5 operations, no tools, legal actual model effort, source and allowed record IDs.
- [ ] Add adversarial tests: quoted/negated/role-play instructions, reminders, unsupported sensitive data, mixed ordinary fact+Key (safe fact may survive, Key never persists), invented/cross-owner target, uncertain scope, assistant-only proposal and unverified tool claim. User default versus one-task correction must preserve scope; verified task evidence cannot create identity/profile facts.
- [ ] Run `PYTEST tests/test_memory_extraction.py tests/test_memory_policy.py tests/test_memory_semantic.py`; observe RED, then implement planner/validators. Model semantic correctness is NOT established by a canned response; reserve actual-model corpus for Task8.
- [ ] Run tests GREEN; commit: `feat: extract open-topic memory operations with provenance`.

### Task 4: Transactional consolidation and two deletion semantics

**Files:** Create `consolidation.py`, `barriers.py`; Modify `repository.py`, `api.py`, `service.py`; tests `tests/test_memory_consolidation.py`, `tests/test_memory_forgetting.py`, `tests/test_memory_api.py`.
**Interfaces:** `MemoryConsolidator.apply(lease, operations) -> MemoryBatchResult`; atomically records job outcome, canonical updates and content_revision. Target revision, source_seq, credentials, control generation and current permission are rechecked inside transaction. Add typed `DeleteBody(revision, permanent: StrictBool=False)`. Existing delete route defaults ordinary; clear removes current records and fences all earlier source_seq without inventing new permanent barriers. Existing v1 tombstones remain protective until explicit restoration.

- [ ] Add RED tests: single-value replacement and independent set additions, exact duplicate, maximum200, partial safe outcome, stale source cannot overwrite newer correction, unknown ID rejected, manual value requires explicit conflict confirmation. Storage failure rolls back record+revision+job status together; crash after commit cannot replay effects.
- [ ] Add deletion tests: ordinary delete followed by old queued event never revives; a new source may relearn; unrelated topics learn normally. Permanent forget creates a scoped encrypted topic barrier with no deleted fact/source fragment, blocks controlled paraphrase candidates, never broad category/account hold; explicit semantic restore only. Existing old barriers survive migration; clear retains permanent barriers and does not backfill. Model ambiguous barrier match affects that candidate only. Actual paraphrase accuracy is measured separately, not claimed perfect.
- [ ] Run `PYTEST tests/test_memory_consolidation.py tests/test_memory_forgetting.py tests/test_memory_api.py`; observe RED, implement transactions and permanent-forget source filtering. Manual mutations still cancel old foreground leases; automatic publication bumps content_revision only.
- [ ] Run same tests GREEN; commit: `feat: consolidate memories and distinguish delete from permanent forget`.

### Task 5: Background lifecycle, foreground integration and truthful accounting

**Files:** Create `worker.py`; Modify repo files `api.py`, `service.py`, `credential_runtime.py`, `composition.py`, `routing.py`, memory `runtime.py`; tests `tests/test_memory_worker.py`, `tests/test_memory_runtime.py`, `tests/test_credential_runtime.py`.
**Interfaces:** `MemoryWorker(queue, planner_factory, consolidator, sessions).start()/wake()/close()`; provider factory creates a memory-only client with the event's frozen model/effort and a revalidated reference. `RunService` admits encrypted source events without blocking model inference, marks eligible sources ready after the normal run finishes, wakes worker and returns run result independently. Do not infer successful task observations from failed/cancelled runs; current-user explicit data remains independently eligible only when its committed run/source is valid and not revoked. `GET run` can merge safe current job status dynamically; stored answer and model usage are immutable.

- [ ] Write failing tests that hold planner behind an asyncio gate: normal answer completes first, no immediate saved claim, worker later publishes exactly once; shutdown/restart resumes safe queued work. Persist dispatcher state before paid call; simulate crash at every boundary. Timeout30s, max1 automatic model call, max1600 output, zero tools, caps2 globally/1 account actually constrain concurrent workers. No hourly chat limit introduced.
- [ ] Cover failures after answer success, terminal uncertain state, explicit bounded retry, key revoke mid-call, disabled memory, temporary mode, owner generation change, no-key/manual-only use, queued status during “我是谁” and no invented identity. User-facing query must not read pending text as canonical memory or falsely promise completion. Reminder intent never claims a scheduled reminder.
- [ ] Run `PYTEST tests/test_memory_worker.py tests/test_memory_runtime.py tests/test_credential_runtime.py`; observe RED. Replace synchronous `run_with_memory` maintenance with queue handoff while retaining `MemoryContextProvider`. Lifespan starts/stops worker before sessions/store close, no detached task leaks. Retain memory read/write controls, separate memory_usage from completed answer tokens; safe reason codes only.
- [ ] Run tests GREEN; commit: `feat: run bounded memory maintenance asynchronously`.

### Task 6: Versioned summary/index and progressive retrieval

**Files:** Create `projections.py`; Modify `retrieval.py`, `service.py`; tests `tests/test_memory_projections.py`, `tests/test_memory_retrieval.py`.
**Interfaces:** `MemoryProjectionStore.get_or_build(owner, provider, generation, content_revision) -> MemoryProjection`; deterministic bounded projection from eligible canonical records, no extra paid summarization call. Holds compact topic index and source pointers, not independent truth. `MemoryRetriever.select_context(snapshot, question) -> MemoryContext` includes selected base profile plus relevant detail/source pointers; serialized total≤6000 bytes, at most8 record IDs.

- [ ] Add RED tests: name/basic identity retrieval for “我是谁”, relevant project knowledge, unrelated records excluded, long Unicode budget, provider-filtered summary, manual edit/delete/forget/sharing-change invalidates cached projection before next read. Old projection response racing a deletion cannot become current. Multiple-source conclusions lose only revoked support; unsupported conclusion disappears. No speculative scan of all historical transcripts.
- [ ] Run `PYTEST tests/test_memory_projections.py tests/test_memory_retrieval.py`; observe RED, implement deterministic initial projection rather than a second LLM job. Persisted encrypted views are versioned; if missing/stale, render from current records, never silently reuse old data.
- [ ] Run tests GREEN; commit: `feat: add generation-safe progressive memory retrieval`.

### Task 7: Minimal bilingual management and asynchronous status

**Files:** Modify memory `api.py`; frontend `app/lib/memory.ts`, `app/lib/memory-editor.ts`, `app/components/MemoryEditor.tsx`, `app/components/MemorySettings.tsx`, `app/components/AgentChat.tsx`, `app/lib/proxy.ts`, `app/lib/ui-language.ts`, `app/workspace.css`; tests `tests/memory-editor.test.ts`, `tests/memory.test.ts`, `tests/proxy.test.ts` and new `tests/memory-status.test.ts`.
**Interfaces:** Extend MemoryView with optional topic/cardinality; preserve old response fields. Snapshot adds safe maintenance counts/states, latest failure code and aggregate memory_usage (never raw source/job secrets). Add authenticated no-store `POST /v1/memories/jobs/{id}/retry` and `/v1/memories/barriers/{id}/restore`, validated UUID/owner/revision and same-origin proxy allowlist. Job list/status API returns safe opaque references, no archived deleted content; memory management owns explicit retries, never auto-resends a question.

- [ ] Add RED Node/API tests: open topic not limited to old enum, old history displays without false success, pending/failed/uncertain differentiated, no per-answer memory-update widget, false “已记住” from model text not used as status. Correct delete/permanent request bodies, permanent operation explanation only at action point, ordinary delete has no auto-learning warning. Retry explicitly acknowledges possible model cost once, max additional dispatch enforced; unavailable/expired Key leads to settings, not fallback.
- [ ] Run new tests with existing memory/proxy suites RED; implement free-topic editor with optional coarse category, advanced provider/expiry folded, existing animated menus/44px targets/CAS409 draft retention. Keep display/user content separate from translated UI. Status lives in management, not recurring chat toasts; resume polling only while page open and jobs pending, cancel on owner/close.
- [ ] Run all Node tests, TypeScript noEmit and Python API tests GREEN; real browser checking belongs Task8. Commit: `feat: expose simple semantic memory controls and background status`.

### Task 8: End-to-end corpus, Docker migration and release evidence

**Files:** Create `tests/fixtures/memory_scenarios.json`, `tests/test_memory_end_to_end.py`, `scripts/memory_acceptance.py`, `docs/superpowers/reports/2026-10-21-semantic-memory-verification.md`; Modify `tests/manual_memory_app.py`, `scripts/local_self_host_smoke.py`, `docs/local-self-hosting.md`, `docs/web-deployment.md`, `README.md`.
**Interfaces:** Corpus cases specify input messages, scope/provider, expected persisted facts/operations, forbidden facts and follow-up questions—not prescribed prose. `memory_acceptance.py --offline-plan` never reads credentials; real mode requires explicit provider/model/budget/count confirmation and synthetic account, no operator-Key fallback. Live tests report truth separately from controlled-transport tests. No default reset of old smoke budgets.

- [ ] Build failing deterministic end-to-end tests for all Review Focus rows using actual store/queue/worker/planner transport/retrieval/foreground path, not direct save_manual substitutes. Include user intro→background publish→new conversation recall, non-whitelist facts, multi-fact/set additions/correction, no-keyword explicit notes, reminders/negation, ordinary deletion/new source, permanent paraphrase block/unrelated learning, manual conflict, provider/account isolation, failed storage, paused/temporary, queue expiry/caps and no duplicate paid retry after crash.
- [ ] Run `PYTEST tests/test_memory_end_to_end.py` RED then fix integration under systematic-debugging/TDD, never lower assertions. Add safe fake latency to verify foreground returns before maintenance while eventual canonical result becomes available; record queue/dispatch/publication timing and model call counts.
- [ ] With separate disposable Compose projects, build actual Python3.12/Node22 images and migrate synthetic v1 populated encrypted DB; restart, verify key epochs/projections/barriers/jobs and backup/restore; source v1 bytes/IDs preserved as promised. Test provider_started crash becomes uncertain, not charged automatically after restart. Scan image/SQLite/WAL/logs/public errors for synthetic secrets. Never touch3025 data for this acceptance.
- [ ] Browser desktop/320/390: free-topic CRUD, async pending/error status, no per-turn reminders, bilingual controls, ordinary versus permanent actions, same-category unrelated record, CAS409 preservation, owner-change cleanup, retry cost confirmation. Inspect terminal/API output and actual persisted result after publish, not just generated answer. Save screenshots using synthetic data only.
- [ ] Real-model release gate: after explicit bounded paid authorization, run the corpus subset against OpenAI and DeepSeek, including the exact name regression and multiple paraphrases without fixed keywords; measure saved fact recall, false writes, correction, scoped forgetting false positives/negatives, latency and usage. No live authorization means report NOT RUN and do not claim “语义效果已验证/可发布”; offline implementation may still be delivered with that gate explicit.
- [ ] Run full Python suite/Ruff, all frontend tests/TypeScript/production build, final Compose smoke and git diff--check. Document actual passed counts, semantic corpus failures, cost/latency and unverified gates. Commit explicit test/docs files, request one independent whole-branch review and make one regression-backed repair pass.
- [ ] Only after suitable tests, user authorization and zero active real runs: offline backup3025 including secrets/data, rebuild/restart without volume reset, verify read-only health/migration/status. Do not replay the user's old “我是方林” chat to claim success; let new authorized tests establish behavior. Otherwise leave running3025 unchanged and clearly state which code is not loaded. Clean up only explicitly named disposable QA projects/volumes and synthetic backup; preserve unrelated dirty files and real services.

## Self-review and handoff

Spec coverage: Codex-style layers and source safety→1/3/6; asynchronous durable foreground-independent operation and truthful accounting→2/5/7; semantic intent and open topics→3/7/8; merges, correction, scoped forgetting and no learning hold→4/8; old-data and provider privacy→1/2/6/8; complete UI/effect acceptance→7/8. No implementation of reminders, arbitrary skills, external vector services or plaintext memory directories.

Shared contracts are fixed above: Event/Operation→queue/planner/consolidator; content_revision separate from control generation; persistent saved-Key epoch separate from process sessions; projection scope includes owner/provider and both revisions. A lost paid dispatch is uncertain, not silently replayed. Explicit priority never permits older sources to overwrite newer corrections. This plan contains no live-test success claims and creates no authorization to spend real provider budget.
