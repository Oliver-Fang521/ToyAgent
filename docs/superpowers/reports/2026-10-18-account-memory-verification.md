# Default-on account memory — local verification

Date: 2026-10-18. Implementation: existing managed detached worktree, inline author execution
and self-review, no subagents. This report separates implemented behavior from release gates.
No push, merge, deployment, personal Codex memory edit, historical backfill or new paid test.

## Result

Implemented encrypted owner/provider-isolated memory, current-text provenance gates, bounded
retrieval/extraction, source-sequence forgetting barriers, revisions, cancellation leases, usage
accounting, authenticated management APIs and visible chat controls. Manual management works
without a model Key. Default is enabled; pause and per-request temporary mode stop reads/writes.
Changes stop queued/active tasks without changing credentials or automatically resubmitting.

The eight implementation tasks are locally delivered. This is **not a claim of zero errors,
excellent real-model personalization, verified mobile behavior or public production readiness**.
The strongest complete GAIA result remains the same 7/15; none of these checks is a new GAIA run.

## Fresh verification evidence

Commands below run in the managed worktree. Python uses the existing environment through
`UV_CACHE_DIR=/tmp/uv-cache ~/.local/bin/uv run --no-sync`; Node uses the bundled runtime.

| Check | Actual result |
| --- | --- |
| Initial `pytest -o addopts='' -q -p no:cacheprovider` | 787 passed, 48.98s |
| Final same full Python command | **876 passed, 50.57s** |
| `ruff check --no-cache src tests deploy scripts` | All checks passed |
| `node --experimental-strip-types --test tests/*.test.ts` in frontend | **33 passed, 0 failed** |
| `node node_modules/typescript/bin/tsc --noEmit --incremental false` | Exit 0 |
| `node scripts/run-framework.mjs build` with bundled Node in PATH | All 5 build stages passed |
| `git diff --check` | Exit 0 |

Frontend verification used fail-fast sequencing so a successful build could not hide a failed
type check. The earlier unknown-JSON type error was fixed before the passing build. No failing
test was omitted: interim failures included missing new modules/fixture, strict revision mismatch,
byte-budget selection mismatch, missing suppressed-field display, NaN expiry, secret/address
gates and retrieval-failure downgrade. Targeted tests were observed failing before implementation
or fixes, then passing. Additional lifecycle acceptance tests that passed immediately are
characterization of already implemented behavior, not invented RED→GREEN claims.

## Twelve acceptance scenarios

The checked fixture is `tests/fixtures/memory_acceptance.json`; executable lifecycle coverage is
`tests/test_memory_acceptance.py`, supplemented by crypto/repository/policy/runtime/API/Node tests.

| Case | Verified local boundary |
| --- | --- |
| cross_conversation | New same-owner/provider snapshots retrieve saved profile |
| owner_isolation | Other owners receive none; foreign CRUD gives 404 |
| provider_isolation | Default isolation; explicit sharing enables the other provider |
| edit_priority | Manual edit survives later automatic candidates; stale revision gives 409 |
| forget_alias_history | Deleted field rejects synonyms and bare old-history acknowledgements |
| pause_gap | Pause stops read/write; resume retains data without filling the gap |
| temporary_draft | No read/write/source registration; draft mode preserved once in Node test |
| roleplay_injection | Quoted/hypothetical/role-play/tool/web claims rejected |
| credential_sensitive | Common credentials, JWT/AWS formats, passwords and sensitive/address text skipped |
| delayed_extraction | Manual generation/revision wins over a delayed extractor |
| failed_usage | Successful answer survives maintenance failure; observed partial usage counted once |
| restart_replay | Persisted state/forgetting keys retained; extraction claim prevents replay charge |

Additional real-component tests show revoked leases prevent OpenAI transport retry and tool
continuation. Nesting a later deadline cannot loosen the outer guard. Research task.question,
progress and synthetic notebook files do not contain the separately injected profile; verifier
provider requests remain unwrapped. These structural tests do not prove a real model can never
echo user data into its own answer; ordinary answers/chat history are intentionally retained.

Synthetic memory database/WAL scans found no plaintext body or readable canonical key. This
applies to the new encrypted memory layer, **not existing ordinary question/result columns**.
Online delete does not promise physical WAL, process-memory or backup erasure.

## Browser verification

Used CUA on the actual built UI, a separate backend with process-owned temporary SQLite, a fixed
synthetic account and a fake executor (`tests/manual_memory_app.py`, `manual_memory_proxy.py`).
No model credentials or network model requests were used. These localhost fixtures are never
deployment components and are not real Sites authentication verification.

Observed: default enabled status; no-Key create/edit; pause/resume; two-window 409 preserving
unsaved editor text; reload preserving that draft; Escape closing the native dialog; provider
switch; temporary-mode disclosure; normal synthetic run using 1 record versus temporary run
using 0; reopening history preserving the safe memory report. Delete confirmation copy was
checked; the final destructive browser click was not performed. Actual delete/clear behavior
passed backend tests against disposable data.

390px QA remains **unverified**: the documented viewport override returned successfully, but
DOM `innerWidth`/scrollWidth stayed 1280 on existing and fresh tabs. The override was reset.
Do not call the resulting desktop screenshot a mobile test. Responsive CSS is implemented,
but a functioning narrow-screen browser test is still needed.

Example synthetic temporary-result screenshot: `/private/tmp/toy-agent-memory-temporary-qa.png`.
The real local service was checked separately: no active tasks before restart, health returned
`{"status":"ok"}`, and the user's management page showed an empty list/default enabled. No
real background fact or old chat was inserted. Existing front/backend services are preserved.

## Five review-focus checks

1. Revocation: service closes leases before awaiting cancellation; actual provider retry and
   tool continuation tests make no second transport request. Credential generations are separate.
2. Lost updates: JSON revisions, generation comparisons and source watermarks defeat stale
   manual writes and delayed candidates. Failed mutations never claim deletion success.
3. Rotation/forgetting: AES v2 can coexist with pinned v1 HMAC; v1 tombstones still block writes.
   Missing pinned key fails memory closed rather than creating a fresh index.
4. Optional failure: extraction partial usage survives auth/timeout failure, one claim precedes
   paid work, successful answers survive both pre-context and post-answer retrieval failures.
5. Replay/mode: mode is in request fingerprints and frontend attempt equality/drafts; terminal
   replay precedes fresh admission checks and does not extract again. Restart interrupts tasks.

## Rulings made during implementation

- Reuse existing public `store.db/store.lock` rather than modify store initialization. Cost if
  wrong: a small adapter refactor, not a new database or historical migration.
- Automatic bodies must equal exact source spans instead of model paraphrases. Cost: conservative
  recall; legitimate paraphrased preferences may be skipped until a quality evaluation warrants it.
- `run_with_memory` receives explicit keyword-only `memory_service`, avoiding hidden globals.
  Cost: one internal signature seam if changed later.
- Preserve legacy fake-executor call shape when no memory service exists; memory-enabled
  executors receive the new explicit keywords. Cost: test fixture migration if that seam changes.
- Keep browser authentication/execution fixtures under `tests`, never product routes. Cost:
  local harness maintenance; these fixtures cannot establish hosted-auth security.
- Do not declare 390px success when the actual viewport stays 1280. Cost: narrow-screen defects
  remain possible, so release needs that check.
- Real-model quality waits for a separately approved 4-task/8-request allowance, rather than
  reusing the exhausted budget. Cost if wrong: unproven quality or unauthorized charges.
- Hosted authentication, independent security and retention remain release gates, not inferred
  successes from synthetic QA. Cost if wrong: privacy/data-retention exposure.

## Known limits and deferred minor

Current profile normalization keeps one canonical self record per facet; it is not a guaranteed
additive skill-history merger. Projects use conservative lexical entity normalization, not an
arbitrary semantic alias graph. Facet-wide forgetting intentionally suppresses even new facts of
the same erased facet until explicitly restored. Manual edits are protected from automatic changes.

Deferred minor: after backend recovery, a previously rendered “memory unavailable” composer
badge may need a page reload even though the management dialog fetches fresh working status.
This does not enable old memory or trigger a model call; reload is the current recovery path.

## Remaining release gates / next authority needed

- Real-provider memory extraction, provenance and personalization **quality are unverified**.
  The prior six paid product tasks are exhausted. Proposed next separate allowance: 4 ordinary
  tasks, 2/provider, at most 8 model requests total, no search/tools; charges to the user's API
  accounts. Do not start them until explicitly approved and credentials are available securely.
- 390px/keyboard follow-up on a working narrow-screen surface; desktop Escape is already checked.
- Independent security review: this author's inline review is not a fresh independent reviewer.
- Real native hosted sign-in, HTTPS, platform logging audit and backup/delete/restore/retention.
  No deployment or infrastructure-secret transfer is authorized by the local implementation.

Assessment: ready for continued local use and a separately bounded real-model evaluation,
**not cleared for public promotion/deployment** until the above gates are resolved.
