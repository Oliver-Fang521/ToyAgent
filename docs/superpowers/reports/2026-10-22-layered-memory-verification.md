# Layered memory verification — 2026-10-22

## Implemented scope

No account-wide fact-count cap; encrypted canonical facts, HMAC lexical indexes with resumable deterministic backfill; revision-bound encrypted shard/topic/overview layers; selected-model summary maintenance; bounded foreground main-model search/read/decision loop; authenticated management search, pagination and safe maintenance status. No classifier, operator-Key fallback, automatic uncertain paid replay or age/count-based fact eviction was added.

The original five-candidate extraction bound remains: removing the storage cap does not claim exhaustive extraction from every message. Overview is a partial navigation sample, not the whole corpus. The same research/verification safeguards remain.

## Offline and UI evidence

- Final full Python suite: **1075 passed**, 60.05 seconds, host Python3.14.
- Frontend Node tests: **55 passed**; TypeScript noEmit passed; production Docker frontend build passed (Node22).
- Disposable backend Python3.12 image: **44 targeted tests passed**, 3.64 seconds, including summaries, maintenance races, tools, loop/runtime and migration/acceptance.
- Ruff and whitespace checks are separate final gates, not implied by pytest.
- 210-record authenticated browser fixture: load-more retained100 cards; exact `sentinel-0` search found one result. A duplicate initial page request was observed resetting100 cards to50 before the fix. English390×844 viewport: dialog left16, width358, right374, document scrollWidth390. Screenshots retained privately in `/private/tmp/toyagent-layered-ui-20261022-vx9Sdz/`.
- Legacy210-fact migration/restart tests preserve encrypted canonical rows and resume index building without model dispatch or old-chat reads. Reinitialization is not the migration strategy.

Performance scripts use synthetic independent temporary databases and tracemalloc. Exact lexical probes are not semantic model-recall benchmarks:

| Corpus | Saved/page-read | Query hits | Query decryptions | Index rebuild | Query p50/p95 |
| --- | --- | --- | --- | --- | --- |
| 1,000 | 1,000 /20 pages | 30/30 | 30 | 0.189s /10 batches | 1.237 /1.343ms |
| 10,000 (final) | 10,000 /200 pages | 30/30 | 30 | 5.286s /100 batches | 19.174 /24.757ms |

Final10k save time9.707s; measured Python allocation peak1.462MiB, **not whole-process RSS**. Oldest stored probe recalled. Paid model calls:0. This does not measure full-corpus automatic publication cost.

## Real models and cost boundary

The user explicitly approved this round: at most20 calls/provider, combined USD2 estimate ceiling. Source credential reads queried only the credentials table on existing read-only sources, with encryption configuration loaded into RAM. No real source chats or memories were read or modified. Every test account/data set was newly generated. No real deployment was restarted.

Models: OpenAI `gpt-6.1-sol`, DeepSeek `deepseek-flash`, reasoning `low`. Both providers passed:

1. Actual model extraction and consolidation of synthetic mathematics background and an Orion/PostgreSQL project fact amid210 unrelated facts.
2. Three actual model summary stages: shard, topic and small overview.
3. Fresh empty-history model-controlled retrieval and personalized explanation using the mathematics background.
4. Fresh empty-history retrieval of the project database.
5. Post-review compression of a terse fact whose **entity metadata alone** carries `Orion`; generated summary retained both Orion and PostgreSQL.
6. User-style canonical correction to SQLite invalidated the old summary; fresh model retrieval answered SQLite, not PostgreSQL.
7. Permanent deletion followed by a fresh empty-history question: neither provider supplied SQLite/PostgreSQL. OpenAI answered unknown; DeepSeek reached no-new-information and answered unknown.

Cases5–7 exercise manual canonical controls and foreground behavior, **not automatic model interpretation of arbitrary correction/forgetting prose**. Assertions include literal expected/forbidden terms; answers were inspected, not graded by another paid judge. These finite synthetic cases cannot establish universal fidelity or intent accuracy. Multi-round stop/duplicate/eight-action behavior is additionally exercised with scripted providers in offline tests.

This round used **OpenAI14 / DeepSeek16** dispatches. The persistent pre-existing ledger was appended, not reset: cumulative **OpenAI70 / DeepSeek109**, estimated aggregate **USD0.352914**, of which this round approximately USD0.049709. Calls include extraction, summaries and recall. No further paid calls are planned for this implementation. Returned usage is an estimate, not an invoice; uncertain calls retain conservative reservations.

Pricing was checked against [OpenAI model documentation](https://developers.openai.com/api/docs/models/gpt-6.1-sol) and [DeepSeek official pricing](https://api-docs.deepseek.com/quick_start/pricing/). The script uses uncached standard OpenAI2/10 and peak DeepSeek0.3/1.2 USD per million input/output tokens; actual caching/off-peak pricing can be lower. The initial attempted DeepSeek source had no credential and stopped **before** any paid dispatch; the existing host credential vault was then used read-only. Credentials and raw provider errors are not included in the report.

## Docker evidence

Disposable project `toyagent-qa-layered-20261022`, frontend3034/private backend; not the real3025 deployment. Builds passed. Authenticated Compose cycle acceptance passed: login, spoofed identity denial, search/status routes, memory CRUD, restart persistence, permanent-forget barrier persistence, synthetic password reset and old-cookie invalidation. No model call was made by the smoke test.

Offline backup/restore into distinct empty test volumes succeeded both before and after the final index-epoch migration. The final24 table row sets and secret configuration JSON bytes matched exactly. The rebuilt image and Python3.12 tests also cover the final migration. Private archives retained outside Git; no volumes or real data were deleted.

## Independent review and resolution

One fresh-context read-only independent reviewer inspected the whole branch plus Task8 changes. No Critical issue was substantiated. Four Important issues were independently reproduced and fixed in one review-resolution pass, each regression observed RED then GREEN:

- Older pending topic jobs stranded by unrelated account revisions → rebase **undispatched** work to the current snapshot, clearing obsolete derivation nodes.
- Same-topic replacement erased active lease and permitted overlapping paid calls → keep the active attempt/lease and encrypted successor intent; promote only after the current attempt terminates.
- Backfill/summary index changes silently invalidated pagination offsets → additive per-owner index epochs, maintained transactionally by triggers, invalidate stale cursors with an explicit conflict rather than silently skipping facts.
- Compression omitted entity/topic/category metadata → structured fact provenance and scope now accompany evidence; bounded grouping measures the full evidence representation.

Reproduction tests cover these mechanisms, not a second reviewer verdict. The reviewer was not asked to rerun a second review after fixes.

### Deferred Minor and explicit rulings

- Automatic consolidation still materializes the account's encrypted rows for at most five operations. This is a real O(corpus) allocation/lock-time scalability follow-up, not covered by the fast lexical-search figures. No claim of unbounded physical capacity/high-scale production readiness is made.
- Catalog lacks tokenizer/context-window metadata. Full serialized UTF-8 request bytes plus output/protocol reserve use a conservative24k engineering bound, not exact model token counting. Long inputs may be refused earlier than necessary.
- Preserve existing `/v1/memories`, with search/maintenance aliases; the plan's `/v1/memory/entries` was not an existing route.
- The planning-stage prohibition on product code ceased to apply after explicit implementation approval; changes remain in the isolated worktree, unpublished.
- Stale continuation now requires restarting search. It never means that omitted facts do not exist.
- Physical erasure from backups/provider systems, old-chat bulk extraction, universal model fidelity and public multi-tenant production certification remain outside this scope. All reviewer declined-to-judge items are retained here rather than silently discarded.

This is a verified local feature branch, not authorization to push, merge, migrate the user's live instance or declare public production certification.
