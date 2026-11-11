# ToyAgent memory, model selection and search verification

Executed locally on 2026-10-20, against the approved 2026-10-19 plan. This is local implementation evidence, not production certification.

## Delivered

- Minimal bilingual memory management, shared animated menus, editing, pause, deletion and explicit conflict recovery. Snapshot requests are fenced so old responses cannot restore deleted entries.
- Documented model capability registry, authenticated account discovery, model-specific reasoning selection persisted into execution and history. Saved and temporary credential discovery caches are isolated by credential source as well as owner/provider/generation.
- DeepSeek Search tool with bounded requests, safe provider-specific failures, credential leakage guards and independent tool request counts. Direct web reads retain the existing guarded reader.

## Automated evidence

Commands run in the linked public-agent-web worktree:

```sh
UV_CACHE_DIR=/tmp/uv-cache ~/.local/bin/uv run --no-sync pytest -o addopts='' -q -p no:cacheprovider
UV_CACHE_DIR=/tmp/uv-cache ~/.local/bin/uv run --no-sync ruff check --no-cache src tests deploy scripts
```

Final implementation run: 912 Python tests passed; Ruff passed. Frontend, using the bundled Node runtime: 40 tests passed; `tsc --noEmit --incremental false` passed; `scripts/run-framework.mjs build` completed with exit 0. Full frontend tests were rerun during handoff. No assertions were weakened for the review fixes.

Independent review found no Critical issues and identified credential-source cache mixing and stale memory refresh as Important. Both received reproducing regression tests and fixes. Search fallback accounting was reclassified from Minor to Important because it affects paid-tool accounting, reproduced and fixed too. RED and GREEN were observed for all three. No review finding is deferred.

## Browser evidence

Synthetic account, localhost fixture, TemporaryDirectory and fake execution only; no real user memory was edited and no paid model/search request was made.

- Desktop memory creation, search, pause, deletion and suppressed-facet warning; closed menus stay hidden.
- Concurrent-edit 409 preserves draft, exposes latest content and requires a deliberate save.
- Nested Escape closes the inner menu and restores its trigger; menu Enter does not submit chat.
- Chinese/English controls switch without translating user-authored memory bodies.
- DeepSeek max reaches the synthetic executor and immutable history; search balance failure identifies research stage, web_search and DeepSeek rather than model quota.
- Active-task change can be cancelled without mutation; explicit confirmation cancels the run without resubmitting.
- Anonymous submit opens login guidance. Missing-key flow additionally covered by contracts, without changing real keys.
- Actual measured viewport widths 320 and 390 matched document scroll widths; the 320 model picker stayed within x=12..308.

Desktop screenshot: `/private/tmp/toyagent-memory-final-2026-10-20.png` (synthetic account). The file named `toyagent-models-mobile-final-2026-10-20.png` is not mobile evidence: a later capture used desktop dimensions. Reduced-motion CSS was inspected but OS/media-emulation behavior was not dynamically tested. Exhaustive browser owner-change, all loading/error permutations and every model were not independently certified.

## Implementation rulings and boundaries

1. Proxy authorization is implemented in `app/lib/proxy.ts`, the actual policy owner, rather than the delegating route.
2. Remote DeepSeek Fetch is contract-tested but intentionally not mounted: upstream redirects cannot prove equivalence to local URL/DNS-pinning policy. Guarded direct reading remains; it is not labelled or billed as DeepSeek Fetch.
3. Tool counters are additive `InteractiveResult.tool_usage`, not model token usage; clients must read the separate field. Search fallback counts as search, not fetch.
4. Existing finite-expiry validation and tests were retained with the related memory snapshot change.
5. Tasks 7–9 share locale/layout imports and were committed as one verified UI dependency graph; review scope is correspondingly broader.
6. Selection transitions receive the explicit live option list so disabled options cannot be focused or selected.
7. Active-run confirmation is in-app instead of native window.confirm, adding small confirmation state while allowing testable cancellation.
8. Known task credentials are rejected before outbound tool queries/URLs, including encoded occurrences; longest-secret-first redaction avoids prefix leaks.
9. Registry documentation, mock transport contracts and real paid execution are separate evidence levels. Real account model permissions, billing, latency, exhaustive provider compatibility and production security/operations were not certified. See the model coverage report for evidence URLs and unsupported contracts.

No push, merge or deployment performed. The unrelated pre-existing orchestrator test change remains unstaged. A backend restart was initially deferred while a real run was active. After a second read-only check confirmed zero active runs, the backend was restarted with the existing private environment and data, and health returned OK. The real frontend was refreshed and the new model control was visible. No real account records or credentials were changed.
