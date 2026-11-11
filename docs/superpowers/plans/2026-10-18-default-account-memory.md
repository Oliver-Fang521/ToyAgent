# Default-on Account Memory Implementation Plan

Implementation record: see `../reports/2026-10-18-account-memory-verification.md` for completed
local work, actual test evidence and remaining release gates. Original checklists below retain
the reviewed plan; they are not a claim that real-provider/mobile/hosted checks have passed.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Preserve the approved inline/no-subagent method; do not dispatch implementation or review subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add default-on, owner-isolated, cross-conversation personalization with visible editing, deletion, pause and temporary-mode controls.

**Architecture:** A dedicated `interactive/memory/` package owns encrypted storage, provenance policy, bounded retrieval and extraction, and revocable run snapshots. The existing public runtime injects memory as untrusted user context and accounts for one optional extraction call after a successful answer. Native authentication and the strict research/CLI boundaries stay intact.

**Tech Stack:** Existing Python, FastAPI, Pydantic, SQLite, AES-GCM/HMAC, ModelProvider, LangGraph, React/TypeScript. No new cloud memory/vector/embedding service or runtime dependency.

**Spec:** `docs/superpowers/specs/2026-10-18-default-account-memory-design.md` (approved).

## Global Constraints

- 默认开启，不要求用户先授权开启，也不逐条弹窗询问；用户随时可以查看、修改、删除、暂停记忆。
- Anonymous traffic never creates or retrieves account memories. Owner always comes from existing trusted identity, never request JSON.
- Only new current-user text is eligible. No history backfill, upload/web/tool extraction, assistant-inferred user facts or Codex personal-memory changes.
- Same owner/provider may share across conversations. OpenAI/DeepSeek sharing is off by default and changes only through user-controlled allowed-provider settings.
- Keys/passwords/tokens and sensitive identity records are rejected, not saved for later confirmation. Do not log raw inputs, candidates, plaintext or reasoning.
- Limits: 200 active memories/account; 500 characters/body; 5 candidates/turn; retrieve at most 8 records and 6000 UTF-8 bytes.
- Extraction: same selected provider/model, no tools, at most 1 logical request, 800 output tokens and 8 seconds, no retries, inside the existing 500-second deadline. No successful answer becomes a failure due solely to memory maintenance.
- Preserve 24 logical tool calls, existing upload/concurrency limits, strict research validation, immutable run model attribution and no operator-Key fallback.
- Manual CRUD and pause/delete work without a model Key. Generation changes revoke old queued/active memory snapshots; no automatic paid resubmission.
- No extra paid tests: previous six-task permission is exhausted. New real tests require a separately described and approved budget. No push, merge or deployment.
- Use the existing managed worktree `/Users/HPOMEN/.codex/worktrees/public-agent-web/agent`; preserve unrelated `.DS_Store` files and do not change the main checkout.

## Review Focus

1. A user edits/deletes memory while a provider is retrying or a tool turn continues: no later external call may reuse the revoked snapshot; pin in Tasks 5–6.
2. Editing a memory with a stale revision or applying a delayed extraction after manual correction: no lost user edits or resurrection; pin in Tasks 2, 3 and 6.
3. AES active-key rotation with tombstones and old HMAC keys: forgetting must survive rotation, and a missing historical key must fail memory closed rather than recreate facts; pin in Tasks 1–2.
4. A failed response/expired Key after otherwise successful answer generation: extraction usage is counted, but the answer remains usable; pin in Tasks 4 and 6.
5. A draft is restored, history reopened, or an idempotent run replayed in temporary mode: mode is preserved and no memory extraction is repeated; pin in Tasks 6–7.

## File and Interface Map

Create `src/gaia_agent/interactive/memory/{__init__,models,crypto,repository,policy,retrieval,extraction,guard,service,api}.py`.
Each module owns the responsibility in its name; do not append the whole subsystem to `store.py`.
Modify existing `interactive/{api,models,service,credential_runtime,composition,routing,store}.py` only at named integration seams below.
Frontend additions: `app/lib/memory.ts`, `app/components/MemorySettings.tsx`, `tests/memory.test.ts`; modify existing chat, draft/history helpers, proxy and stylesheet.
All frontend paths below are relative to `sites/agent-web/`. All test data is synthetic.

### Task 1: Typed Contracts and Domain-Separated Encryption

**Files:** Create `memory/{__init__,models,crypto}.py`, `tests/test_memory_crypto.py`, `tests/test_memory_models.py`.

**Interfaces:**
- `MemoryCategory = Literal['profile','project','decision']`, `MemoryMode = Literal['normal','temporary']`.
- Frozen `MemoryCandidate(category, facet, entity, content, source_start, source_end, explicit_relearn)`; the boolean is required (normal candidates explicitly pass false) and all fields are required in model JSON schema. At most 5 candidates; body length 1–500. Memory models hide inputs in validation errors and never repr plaintext payloads.
- Internal `MemoryWrite(candidate: MemoryCandidate, normalized_key: str, source_fragment: str|None)` is a policy-validated write, not an API request schema. Repository tests may construct synthetic writes directly; production obtains them only from MemoryPolicy.
- Frozen internal `MemoryRecord(id, owner, category, canonical_key, revision, origin_provider, allowed_providers, status, content, source_run_id, source_start, source_end, created_at, updated_at, last_confirmed_at, expires_at, edited_by_user)`; internal secret-bearing objects use `repr=False`. `MemoryView` omits owner, canonical_key and ciphertext.
- `MemorySettings(enabled: bool=True, revision: int, generation: int, capture_after: int)`; persisted switches require literal booleans.
- `MemorySnapshot(owner, provider, generation, mode, records)`; `MemoryReport(status, used_ids, saved_ids, skipped_count)` has no memory text. Status enum: `ready,updated,unchanged,paused,temporary,unavailable,skipped`.
- `MemoryCrypto(keys: Mapping[str,bytes], active_version: str)`; `encrypt(owner,id,payload)->MemoryEnvelope`, `decrypt(owner,id,envelope)->dict`, `canonical_hash(owner,normalized_key,hash_version)->str`, `from_environment()->MemoryCrypto`.
- AES AAD is `['toy-agent-memory-v1',owner,id,encryption_version]`; purpose-separated HMAC includes owner and normalized key. Pin a hash-key version per account; retain it for tombstones when AES active version rotates. Missing pinned key disables memory operations safely, never bypasses tombstones. Define `MemoryFailure(code,status_code)` in models (do not shadow Python's built-in MemoryError).

- [ ] **Step 1 — RED tests.** Add wrong-owner/id/version AAD, ciphertext tamper, no plaintext repr, profile bounds and literal boolean tests. Core assertion: `decrypt('bob',id,envelope_for_alice)` raises `MemoryFailure('memory_integrity')`; `enabled=1` is invalid. Assert AES rotation v1→v2 with both keys preserves v1 canonical hashes.
- [ ] **Step 2 — Run.** `uv run pytest tests/test_memory_crypto.py tests/test_memory_models.py -q`. Expected: new tests fail on missing module/contracts, not fixture syntax.
- [ ] **Step 3 — Implement.** Add exact types, bounded serialization and domain-separated crypto; reuse infrastructure keyring format without accessing another class's private `_keys` or exposing values through status APIs. Define allowlisted memory failure codes here.
- [ ] **Step 4 — GREEN.** Rerun the command; all pass. Run existing `tests/test_credential_crypto.py` too; its credential AAD behavior is unchanged.
- [ ] **Step 5 — Commit.** Stage only task files; `feat: define encrypted account memory contracts`.

### Task 2: Repository, Revisions and Durable Forgetting

**Files:** Create `memory/repository.py`, `tests/test_memory_repository.py`; modify `interactive/store.py` only to expose the shared connection/lock through existing attributes, without touching old rows.

**Consumes:** Task 1 contracts/crypto. **Produces:** `MemoryRepository(db,lock,crypto,clock=time.time)` with:
`settings(owner)->MemorySettings`, `get(owner,id)->MemoryRecord`, `list(owner,offset=0,limit=50)->list[MemoryView]`,
`register_turn(owner,run_id)->int` (unique monotonically increasing source sequence),
`eligible(owner,provider,now)->tuple[MemoryRecord,...]`, `save_manual(owner,write: MemoryWrite,allowed_providers,expected_revision=None)->MemoryView`,
`apply_candidates(owner,provider,run_id,writes: tuple[MemoryWrite,...],expected_generation)->tuple[str,...]`,
`update(owner,id,patch,expected_revision)->MemoryView`, `delete(owner,id,expected_revision)->None`,
`clear(owner)->None`, `set_enabled(owner,enabled,expected_revision)->MemorySettings`.
Task 3 supplies validated MemoryWrite objects and authorization decisions, not SQL. Task 2's repository tests use synthetic MemoryWrite objects and do not import the not-yet-built policy.

Additive tables: `memory_settings`, `memory_entries`, `memory_tombstones`, `memory_turns` (unique owner/run ID plus integer source sequence). Metadata stays text-free; encrypted payload holds content, semantic keys and source fragments. Provider permission is an integer mask so SQL filtering requires no new JSON/FTS extension. Persist pinned hash-key version and generation. No migration reads old questions/results.

- [ ] **Step 1 — RED tests.** `test_default_enabled_without_backfill`, `test_disabled_survives_restart`, `test_owner_and_provider_filter_before_decrypt`, `test_manual_crud_without_model_key`, `test_stale_revision_conflicts`, `test_delete_purges_payload_keeps_opaque_barrier`, `test_rotation_cannot_revive_deleted_key`, `test_200_record_limit_preserves_manual_records`, `test_clear_blocks_pre_watermark_turns`. Assert foreign-owner get/update/delete return 404 and do not reveal existence; provider-filtered rows are never decrypted (spy crypto).
- [ ] **Step 2 — Run.** `uv run pytest tests/test_memory_repository.py -q`. Expected: RED missing repository.
- [ ] **Step 3 — Implement.** Use explicit column names, owner predicates and one RLock/SQLite transaction. CAS updates return 409. User mutations increment generation; automatic merges only compare generation and update row revisions, so they do not revoke their own run. Remove encrypted old body on replacement/deletion; retain only opaque forgetting metadata and version/time. Deletion/clear advance a durable per-owner capture sequence, not a wall-clock-only comparison. A repeated owner/run ID is a no-op and never calls extraction again.
- [ ] **Step 4 — GREEN.** Rerun repository tests plus credential vault/store regressions. Verify ciphertext/tombstones contain neither the synthetic fact nor its canonical plaintext key, including SQLite/WAL bytes in the temporary database. Missing pinned hash key yields `memory_unavailable`, not a fresh index.
- [ ] **Step 5 — Commit.** `feat: persist owner-scoped memory and forgetting barriers`.

### Task 3: Provenance, Canonicalization and Safe Merge Policy

**Files:** Create `memory/policy.py`, `tests/test_memory_policy.py`.

**Consumes:** Candidate/record/settings contracts. **Produces:** `MemoryPolicy.normalize(candidate)->str`, `validate_current(candidate,current_text)->MemoryWrite|None`, `validate_manual(candidate)->MemoryWrite`, `decide(write: MemoryWrite,existing,tombstone,current_text)->Literal['add','replace','skip','restore']`, `prepare_text(text)->str|None`.
Profile facets are a closed enum: `preferred_language,response_style,education_background,professional_background,learning_subjects,skill_experience`. Projects use `goal,stack,constraint,status`; decisions use `choice`. Entity normalization is NFKC/casefold/whitespace normalization; existing in-scope aliases resolve to their stable entity ID, never arbitrary model-generated record IDs. General deletion suppresses auto-capture for that erased category/facet until the user explicitly restores it; this conservative suppression prevents a new alias bypassing a tombstone. The UI exposes that suppression and manual restore, not a promise of perfect semantic alias detection.

- [ ] **Step 1 — RED tests.** Pin exact source offsets with Unicode, bounds and exact substring comparison. Cases: current self-report accepted; third-party quote, hypothetical, role-play, bare “好的”, assistant/tool/web statement rejected. Test common credential formats, password labels, sensitive profile fields, invalid free-form facets, user-edited priority and synonym re-insertion after deletion. Assert current “请重新记住…” plus matching source may restore, while a quoted phrase or candidate's `explicit_relearn=true` alone cannot.
- [ ] **Step 2 — Run.** `uv run pytest tests/test_memory_policy.py -q`; expected RED missing policy.
- [ ] **Step 3 — Implement.** Deterministic validators gate model-proposed candidates; reject uncertain cases. User text passed to extraction is prefiltered/redacted; both manual and automatic writes apply the same credential/sensitive-data rules. Remembered text remains data, not privileged instructions. Do not rely solely on the model's confidence flag or regex for all semantic judgments: the strict extractor must classify direct self-report and source provenance, followed by server span/facet checks.
- [ ] **Step 4 — GREEN.** Rerun tests; verify corrections replace only corresponding in-scope records, manual edits cannot be silently overwritten, and no unrelated user's facts enter conflict checks.
- [ ] **Step 5 — Commit.** `feat: validate memory provenance and user corrections`.

### Task 4: Bounded Retrieval and Single-Attempt Extraction

**Files:** Create `memory/{retrieval,extraction}.py`, `tests/test_memory_retrieval.py`, `tests/test_memory_extraction.py`.

**Consumes:** Tasks 1–3. **Produces:**
- `MemoryRetriever.select(records,question,now)->tuple[MemoryRecord,...]`, `render(records)->str`: records must already be owner/provider-authorized.
- `MemoryExtractor(provider: ModelProvider).extract(current_text,existing,*,usage_observer)->tuple[MemoryCandidate,...]`.

- [ ] **Step 1 — RED tests.** Use synthetic records for profile defaults, relevant project, irrelevant project, expiration and recency. Assert `len(selected)<=8` and UTF-8 `len(render(...).encode())<=6000`, including Chinese. Extractor fixture asserts tools empty, max output 800, only current user text plus relevant allowed records, exact source spans, <=5 candidates; malformed/refused/incomplete output returns no candidates and safe status, never a repaired second call. Observer-reported partial usage remains visible if the call later fails.
- [ ] **Step 2 — Run.** `uv run pytest tests/test_memory_retrieval.py tests/test_memory_extraction.py -q`; expected RED.
- [ ] **Step 3 — Implement.** Rank stable small profile records first, then deterministic token/CJK-bigram overlap, confirmed recency and stable ID tie-break; expired records are excluded, irrelevant project/decision records are not included just to fill the budget. Render explicit untrusted-context delimiters and source/confirmation dates. Extraction uses a strict JSON schema and direct-self-report instruction, not full history or model-generated answers. Disable its generation tracing/recording of content.
- [ ] **Step 4 — GREEN.** Rerun plus `tests/test_deepseek_responses.py tests/test_openai_provider.py`; transport contracts remain unchanged. API-level retries are disabled for the extraction-specific provider in Task 6, not by mutating the normal provider's retry policy.
- [ ] **Step 5 — Commit.** `feat: retrieve relevant memories and extract bounded candidates`.

### Task 5: Memory Service, Revocable Guards and Authenticated CRUD

**Files:** Create `memory/{guard,service,api}.py`, `tests/test_memory_service.py`, `tests/test_memory_api.py`; modify `interactive/{api,service}.py`, frontend `app/lib/proxy.ts`, `tests/proxy.test.ts` narrowly.

**Consumes:** Tasks 1–4. **Produces:**
- `MemoryService(repository,policy,retriever)` with `snapshot(owner,provider,mode)->MemorySnapshot`, `remember(snapshot,run_id,text,extractor,*,deadline,usage_observer)->MemoryReport`, `async mutate(owner,action,cancel_owner)->object`.
- `MemoryLease(snapshot,repository,deadline,clock)` subclasses existing `InvocationDeadline`; `ensure_open()` checks generation/mutation state, then the inherited deadline. Revocation raises an `asyncio.CancelledError` subtype, not a raw exception that SafePublicProvider could relabel as a model error. A service registry can close all leases for an owner before waiting on cancellation.
- `mount_memory_routes(app,*,owner_dependency,memory_service,cancel_owner)` adds the spec's GET/POST endpoints. `create_app(...,memory_crypto=None)` permits explicit synthetic crypto injection; production derives it from infrastructure configuration, never existing credential object private fields.

- [ ] **Step 1 — RED tests.** Assert all memory routes need trusted identity/token, no wildcard anonymous exception; cross-owner 404; invalid provider/body/long content rejected; manual create/edit/delete with no model Key; no-store responses; 50-record pages with validated offsets. Mutation tests suspend a queued/active fake task, revoke/await cancellation and prove a late candidate cannot commit. A failed CAS does not change content/generation; a DB failure never reopens a revoked lease or asserts successful deletion.
- [ ] **Step 2 — Run.** `uv run pytest tests/test_memory_service.py tests/test_memory_api.py -q` and Node proxy tests; expected new behavior RED.
- [ ] **Step 3 — Implement.** Memory mutation barrier is separate from credential generations: block admissions while mutating, close old leases, transactionally mutate, await `RunService.cancel_owner`, release barrier. Add strict route allowlist for memory settings, list/create, UUID update/delete, clear; forward only validated list offset, never owner query params. Revision travels in JSON (current proxy already forwards JSON), not an unforwarded header. Same temporary-mode run replay still returns its original result before new credential/memory admission checks.
- [ ] **Step 4 — GREEN.** Rerun tests plus interactive API/service and credential mutation suites; no Key deletion/rotation is performed by memory operations. The service works with a shared SQLite lock without nested commits.
- [ ] **Step 5 — Commit.** `feat: expose revocable account memory controls`.

### Task 6: Public Runtime Integration, Context and Usage

**Files:** Modify `interactive/{models,service,credential_runtime,composition,routing,store}.py`, add `memory/runtime.py`, `tests/test_memory_runtime.py`; update existing interactive fixture callers explicitly where executor keywords change.

**Interfaces:**
- `InteractiveRequest.memory_mode: MemoryMode='normal'` is the only new public default; provider/model remain required. Mode is part of the existing request fingerprint.
- `InteractiveResult.memory: MemoryReport|None=None` is additive and contains no private content.
- Extend public executor with keyword-only `memory_snapshot=None, run_id=None, deadline=None`; keep old CLI composition unchanged. Fake executors must explicitly accept these new public keywords.
- `execute_interactive(...,memory_context: str='')` and `route_request(...,memory_context: str='')` inject a separate untrusted user-data block. Do not alter system instructions with retrieved content or add memories to `result.evidence`.
- `MemoryContextProvider(provider,context)` delegates ModelProvider while prepending a bounded, explicitly untrusted user-data item to transport input. Extend `build_interactive_orchestrator(...,user_context_provider=None)` so planner/specialists may receive this wrapper while critic/evidence-linker retain the ordinary provider. Keep `GaiaTask.question`, checkpoints and research-notebook task content free of injected profile text; do not implement memory by concatenating it to the task question. No memory generation text is sent to tracing.
- `run_with_memory(execute,snapshot,extractor,current_text,run_id,deadline)->InteractiveResult` in `memory/runtime.py` coordinates answer-first, usage, optional maintenance and safe downgrade; no detached background jobs.

- [ ] **Step 1 — RED tests.** Fake providers prove two conversations use the same owner/provider profile, Bob receives none, DeepSeek receives no OpenAI-only memory, and temporary/paused runs receive none. Assert a fresh explicit user instruction beats a conflicting memory instruction. Queued mutation, nested tool continuation and provider retry after revocation must create zero further external requests via the existing `ensure_current_invocation_allowed` hooks.
- [ ] **Step 2 — Run.** `uv run pytest tests/test_memory_runtime.py tests/test_interactive_composition.py tests/test_credential_runtime.py -q`; expected new assertions RED, previous behavior remains characterized.
- [ ] **Step 3 — Integrate read path.** After reserving a genuinely new normal/enabled run, register its source ticket with `register_turn`; never register old completed/idempotent runs or backfill history. Reject admission while the memory mutation barrier is held. Snapshot only at actual dispatch; wrap normal model/tool execution in `invocation_deadline_scope(lease)` so inherited inner deadline scopes cannot replace the outer revocation guard. Recheck at each model stage and actual transport boundary. Source sequence must exceed settings.capture_after and generation must still match before any merge. Restarted tasks remain interrupted rather than paid-resumed. Do not persist plaintext snapshots or credential handles in task rows.
- [ ] **Step 4 — Integrate write path.** After a successful answer and before closing its selected client, create a separate SafePublicProvider of the same concrete type/model/client with `RetryPolicy(max_attempts=1)`, no observability generation recorder, tools empty. Maintenance budget is `min(8, remaining_total-0.5)` seconds; skip when nonpositive. Use the normal parent deadline, not an additional 500 seconds. Aggregate each observer usage exactly once; do not double-add the returned response usage. Call `remember` once per owner/run ID. Cancellations/revocations propagate; ordinary extraction timeout/auth/parse/storage failures only set safe memory status and return the original answer. If main run fails/cancels, no automatic memory writes occur.
- [ ] **Step 5 — GREEN.** Add failure fixtures for partial usage then auth failure, malformed response, time exhaustion, cancellation during commit, manual edit defeating delayed extraction, terminal replay and changed-mode idempotency conflict. Expected main answer unchanged, one extraction attempt at most, no new replay charge. Run focused suites then full `uv run pytest -o addopts='' -q`; record the actual count, not a hardcoded expected 787.
- [ ] **Step 6 — Commit.** `feat: personalize public runs with bounded account memory`.

### Task 7: Visible Default-on Memory and User Management

**Files:** Create frontend `app/lib/memory.ts`, `app/components/MemorySettings.tsx`, `tests/memory.test.ts`; modify `AgentChat.tsx`, `globals.css`, `app/lib/{onboarding,history}.ts` and associated tests.

**Interfaces:**
- Mirror `MemoryMode`, `MemoryView`, `MemorySettings`, `MemoryReport` without owner/ciphertext fields.
- `memoryApi(path,body?)` uses current trusted proxy and typed safe errors; no browser memory-content persistence.
- Controlled `MemorySettings({open,onClose,onChanged,onSignInRequired})`; management never starts a model task.
- Draft schema gains only `memoryMode`, default normal for older drafts; never includes memory body/ID list. Include mode in the UI attempt equality check, preserve it across accepted-disconnected retry, and do not reuse a normal run's idempotency key for changed temporary mode.

- [ ] **Step 1 — RED tests.** Pin absent settings to enabled-but-loading (not false “saved”), explicit disabled persistence, temporary mode, identity-change cleanup, current-body payloads, revisions, clear vs pause and safe errors. Draft consumes once with mode preserved. Accepted run's memory report survives reopening; no auth/save/edit callback sends a question. Management without model credentials remains enabled. Test a 409 preserves user's unsaved editor text and offers reload instead of overwrite.
- [ ] **Step 2 — Run.** `node --experimental-strip-types --test tests/*.test.ts`; expected new tests RED.
- [ ] **Step 3 — Implement.** Add input-area “长期记忆已开启 / 已暂停 / 临时对话”, management trigger and per-run saved/used/skipped status. Native dialog supports pagination, category/time/provider scope, create/edit/delete/clear/restore suppressed facet, and pause. Explain stored chat versus temporary memory, possible model cost, and that mutations stop active tasks without recalling already-sent requests. Keep deletion controls available during activity; reconcile the cancelled run without automatically retrying. Clear dialogue text on owner change; handle storage-disabled drafts visibly. No consent popup for enabling default memory.
- [ ] **Step 4 — GREEN.** Node suite, `node node_modules/typescript/bin/tsc --noEmit --incremental false`, production `node scripts/run-framework.mjs build` using bundled Node. Personally verify desktop, 390px layout, keyboard, default status, edit/409 recovery, delete, pause, temporary, provider switch and reopened result using synthetic records/fake model paths. Use CUA for browser interactions; local mock identity is not real hosted login.
- [ ] **Step 5 — Commit.** `feat: add editable account memory controls to chat`.

### Task 8: Product Acceptance, Security Boundaries and Handoff

**Files:** Create `tests/fixtures/memory_acceptance.json`, `tests/test_memory_acceptance.py`, `docs/superpowers/reports/2026-10-18-account-memory-verification.md`; modify `README.md`, `docs/web-deployment.md`. Extend `scripts/interactive_smoke.py` only with credential-free memory planning output, never silently reset the exhausted paid ledger.

- [ ] **Step 1 — RED fixture contract and integration tests.** Before creating the fixture file, add a loader test requiring exactly 12 distinct case IDs, each with expected and forbidden behavior. Cases: new-conversation preference recall; independent second owner; provider-isolated fact; edited preference; deleted fact/alias plus old history; pause/resume gap; temporary request and restored draft; injection/role-play; credential/sensitive text; delayed extraction/manual race; failed extraction preserving answer/usage; restart and replay. Assert no raw profile in progress/logs/traces/checkpoint task content and no memory plaintext in temporary database/WAL.
- [ ] **Step 2 — Run.** `uv run pytest tests/test_memory_acceptance.py -q`; expected fixture contract RED because its new JSON fixture is absent. Other integration tests may already pass and are reported as characterization, not fabricated RED→GREEN fixes. Do not call a fake response “real personalization quality”.
- [ ] **Step 3 — Complete acceptance.** Add the 12-case fixture, fix reproducible integration failures with targeted regression tests, and rerun the contract. Run all Python tests, `uv run ruff check src tests deploy scripts`, Node tests, TypeScript, production build and `git diff --check`. Use fail-fast command sequencing so a successful build cannot mask a failed type check. Document exact commands/counts and whether browser checks used mocks.
- [ ] **Step 4 — Operational verification.** Confirm no active tasks before a local backend restart. Use existing infrastructure keyring without printing it; inspect only synthetic memory readback/counts and safe statuses. Do not backfill real history or write this chat's user background. Preserve local services if safe. Request a separate explicit budget before any real memory-model tests; without it mark them unverified, not failed or passed. Real hosted authentication, independent security review and platform backup/delete/rotation behavior remain release gates.
- [ ] **Step 5 — Document and review.** Record defaults, bounded lexical retrieval limits, conservative facet suppression, cross-provider controls, extra memory usage and active-task cancellation effects. Self-review all five Review Focus cases and full feature diff inline under the approved no-subagent constraint. Independent production review is not replaced. Commit only feature files: `test: verify default-on account memory isolation and lifecycle`. Do not push/deploy.

## Self-review of This Plan

- Spec coverage: encryption/settings/storage → 1–2; source/merge/forgetting → 3; retrieval/extraction → 4; authenticated controls/revocation → 5; public runtime/usage/idempotency → 6; visible management/drafts → 7; acceptance/restart/privacy documentation → 8.
- Same types/names travel through task Interfaces; default normal applies only to memory mode, never required provider/model. Both old and new accounts default enabled unless an explicit persisted setting overrides it.
- Revocation is an outer invocation guard, not only an initial snapshot check or a swallowed progress callback. Automatic merges compare generation without cancelling themselves; manual mutations revoke first.
- HMAC key retention, facet-level suppression and lexical retrieval are disclosed engineering tradeoffs; none is represented as semantic SOTA or a physical-backup deletion guarantee.
- Inline execution/no subagents is retained. This document is a plan for review, not authorization to run paid tests or deploy. Implementation begins after the user reviews this plan.
