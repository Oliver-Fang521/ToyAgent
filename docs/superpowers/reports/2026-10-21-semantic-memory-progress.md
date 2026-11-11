# Semantic account memory implementation progress

This is an intermediate implementation, not a release acceptance report.

Plan: `../plans/2026-10-21-semantic-account-memory.md`.
Base: `cf92f72`. Existing externally managed detached worktree retained.

## Implemented layers

1. Additive schema migration, encrypted object domains, legacy record defaults and immutable event/operation contracts (`eda63d3`). Migration preserves old encrypted bytes, revisions, masks, key pinning and tombstones.
2. Durable bounded job queue, owner-bound leases, pre-dispatch journal, persisted proposals, safe crash recovery and persistent credential epochs (`cd08ddc`). Temporary references remain in RAM; expiry/restart never falls back to a saved Key.
3. Open-topic semantic proposal planner with source-span validation, a strict nullable-field schema, one tool-free bounded model call and no keyword/first-person admission gate (`cf7667d`). Controlled provider responses verify transport/validation only, not actual semantic accuracy.
4. Transactional canonical publication, separate content revision, independent set members, duplicate/capacity/CAS/source-order protection, ordinary deletion versus scoped encrypted permanent barriers, explicit restoration and old-path barrier compatibility.

5. Background main path is now wired in this checkout: encrypted unready admission, completed-source release, two bounded consumers, frozen user-owned model clients, journaled proposals, separate maintenance status/usage and orderly shutdown. Gated RunService tests show the answer completes while maintenance is still waiting; the completed answer's usage remains unchanged. This is a Task 5 checkpoint, not completion of its complete failure matrix.

The running3025 Docker instance has not loaded this checkout. Controlled transport/worker results do not establish real-model resolution of the reported cross-conversation name regression.

## Remaining release work

- Task 5 remaining: complete crash/mutation/temporary-expiry/concurrency matrix, production client contract coverage and detailed safe failure codes. Do not treat the happy-path gated test as complete production acceptance.
- Task 6: versioned projections and progressive retrieval.
- Task 7: minimal bilingual free-topic management, maintenance status and explicit paid retry controls.
- Task 8: end-to-end transport corpus, isolated Docker migration/restart/backup acceptance, browser checks and one independent whole-branch review.
- Real OpenAI/DeepSeek semantic corpus: NOT RUN; no paid test authorization assumed. No claim of model recall, semantic-forgetting accuracy or release readiness.

No 3025 service restart, volume reset, chat backfill, real credential use, push, merge or deployment was performed.

## Current verification

- Latest full Python regression: 987 passed (50.04s).
- Frontend Node tests: 49 passed.
- Ruff and `git diff --check`: passed.
- New regressions were observed failing before fixes for object AAD/migration, queue/credential epochs, source provenance, forged event/proposal replacement, forgetting invalidation, manual conflict status and full-request byte budgets.
- Browser, production build, actual Python 3.12/Node 22 Docker migration and paid semantic effectiveness checks for this new pipeline remain pending. These unit/transport results are not a substitute.

## Implementation rulings

- Bind verified task observations inside encrypted events and add optional operation `observation_id`: `source_kind` alone cannot establish which server-verified observation supports a lesson. Cost if wrong: adjust additive fields; old events retain compatible defaults.
- Explicit destructive/restore operations also bump control generation; ordinary automatic additions only bump content revision. Content-only invalidation cannot fence existing foreground leases after forgetting. Cost if wrong: an explicit deletion may interrupt an older answer rather than permit use of erased information.

Final live-effect and deployment evidence belongs in the Task 8 verification report, not this intermediate progress note.
