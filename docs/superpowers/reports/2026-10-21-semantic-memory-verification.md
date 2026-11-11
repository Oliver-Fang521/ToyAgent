# Semantic account memory verification — 2026-10-21

Worktree: public-agent-web/agent. This report concerns the new semantic memory pipeline, not a GAIA benchmark or a claim that every possible memory expression is understood. The user's existing localhost:3025 service and volumes have not been upgraded or modified.

## Architecture and controls

Successful foreground completion releases an encrypted source event to a durable bounded queue. A tool-free model proposes open-topic operations; server source-span/provenance validation precedes transactional canonical publication. Disposable encrypted projections support progressive retrieval. Account/provider boundaries, credential epochs, generation fences, manual-edit precedence and source ordering are enforced server-side. Ordinary deletion and permanent suppression have distinct semantics. No per-answer memory notification was added.

## Real-model findings and fixes

All live tests used newly generated synthetic accounts, empty conversation history for recall, actual queue/worker/planner/consolidation and committed canonical storage. Source credential reads queried only the credentials table in read-only mounts. No real source chats or memories were accessed. No operator-Key fallback was used.

- Initial OpenAI run: five of six effect assertions passed. Last storage assertion passed, but the answer rubric incorrectly required “mathematics” for a question asking what programming subjects were being learned. Two separately authorized diagnostics passed: Python/Rust recall and explicitly requested complete background recall. The original failure remains recorded, not retrospectively counted as passed.
- Initial DeepSeek runs: the final independent-learning input repeatedly returned incomplete/max_output_tokens at 1600 and 3200 output limits. No partial JSON was committed; the job failed safely without automatic paid retry. A bounded 6400 output limit resolved the observed regression. This remains one model dispatch per job, not an unbounded repair loop.
- Retrieval omitted topic metadata, causing the model to confuse a name with a preferred nickname. Topic/cardinality are now included, with explicit presentation guidance distinguishing the two. A regression test first failed for the omitted topic and then passed after the fix.
- Corrected six-case corpus: OpenAI 6/6, DeepSeek 6/6. Cases cover identity/background, preferred address/style, explicit project note, fictional/reminder negatives, permanent nickname forgetting and continued unrelated learning. Canonical assertions retain mathematics/Python after forgetting and require Rust publication; answer assertions match the actual question. One OpenAI final job reported needs_clarification while successfully publishing unrelated Rust; this is not described as fully resolved semantic intent.

Maintenance capture latency in these passing six-case runs: OpenAI 2.468–13.305s; DeepSeek 1.735–11.332s. This is background capture latency, not foreground response latency or a performance benchmark. Controlled gated integration tests separately establish that foreground completion does not wait for maintenance.

Budget ledger: /private/tmp/toyagent-semantic-acceptance-20261021-zVW0kn/budget.json. Initial authorization was USD2 and12 calls/provider, followed by four OpenAI diagnostic calls and then explicit user authorization to increase testing until adequate. Every dispatch reserves allowance before transport. Failed/uncertain calls retain the conservative reservation; returned calls use token-price estimates. The aggregate USD2 ceiling remains unchanged. At the completed six-case reruns: OpenAI28 calls, DeepSeek45 calls, estimated total USD0.143347. Later extended-corpus results and final totals are recorded below when available. These are estimates, not a provider billing invoice.

## Runtime/browser evidence

- Full Python suite before the last two added regressions: 1005 passed; final gate recorded below.
- Frontend Node tests: 52 passed; final TypeScript/build gates recorded below.
- Actual Python3.12 backend container: 26 selected migration/crypto/worker/offline-backup tests passed, including persisted proposal recovery without another model dispatch and provider_started shutdown becoming uncertain rather than automatically replayed.
- Actual Python3.12/Node22 Compose images built; disposable project toyagent-qa-semantic-20261021 at3031 initialized and passed restart/password-reset/logout-invalidating smoke. No real provider calls were made by that infrastructure smoke.
- Offline backup to a private synthetic QA archive, restore into distinct toyagent-qa-semantic-restored-20261021 volumes, then successful backend/frontend health at3032. After offline shutdown, exact table-row comparison passed for credentials, memory entries/settings/tombstones/barriers/projections/credential epochs/jobs. Encryption and authentication configuration files were byte-identical.
- Restored SQLite/WAL and runtime image scans found no plaintext synthetic provider-Key needle. This is a scoped synthetic-secret check, not a general penetration-test certification.
- Browser3031: login, arbitrary topic creation, edit/save, search, permanent-forget confirmation/cancel, animated expiry dropdown, Chinese/English controls. At320px and390px document width equaled viewport width;320px dropdown bounds were36–284px and CSS animation toy-select-in. No captured warning/error console entries. Screenshots: /private/tmp/toyagent-memory-390.png and /private/tmp/toyagent-memory-en-390.png. User content remained Chinese after switching interface to English.

## Pre-review gates (historical checkpoint)

- Extended four-case corpus passed4/4 on each provider: arbitrary project facts, SQLite→PostgreSQL correction, a durable preference phrased without a fixed remember keyword, and a quoted false-identity negative. Combined post-fix live corpus:10/10 OpenAI and10/10 DeepSeek. This is a small synthetic corpus, not a statistical reliability estimate.
- Final live ledger: OpenAI36 and DeepSeek53 dispatches across discovery, failures, diagnostics and passing runs; estimated returned usage USD0.172892. All requests returned accounted usage. Historical reservations were released on returned usage; USD4.66 is the sum of successive reservations, NOT spend or the concurrently committed budget. Current accounted bound remained below USD2. Paid testing is now stopped because identified live regressions and the named finite effect gates have been exercised, not because every possible input was tested.
- Full Python regression1007 passed in52.10s;52 frontend Node tests passed; TypeScript noEmit, production build, Ruff and git diff--check passed. The final rebuilt backend and frontend became healthy again at3031. Synthetic Key scans of both projects' container logs were also clean.
- Browser coverage is limited to the named interactions above. Retry/cost-confirmation, reversed snapshot ordering, owner cleanup and409 draft preservation are covered by controlled API/frontend tests, not claimed as separately completed browser scenarios.
- Whole-branch independent review completed against192c4ca and found nine actionable issues. Its verdict was NOT ready; the subsequent regression-backed repair pass is still in progress. No push, merge, public publication or upgrade of the user's3025 runtime has been performed.

## Post-review repair and renewed testing

The user paused for quota exhaustion, then explicitly restored paid-test authority. No paid calls were made during the pause. The same USD2 cumulative ceiling and ledger are retained.

Repairs address provider-ineligible canonical retargeting, epoch seeding for saved pre-feature credentials, populated-account request bounds/pre-dispatch preflight, memory-owned event/proposal retention, scoped set-member barriers and restore-plus-add, pre-persistence SSN/payment redaction, temporary credential retry retention, persistent capacity failures, and truthful foreground state/used IDs. The main11 regressions and five state regressions were observed failing before repair. A separate frontend capacity-message test also went RED→GREEN.

Source retirement is scoped: finalized/nonretryable or expired maintenance payloads are retired; deleting a fact blanks its exact text in mixed retryable sources and removes related proposal references. Other queued facts and accounting are preserved. Only explicit clear-all retires all earlier memory-owned work. This is logical deletion, not physical SQLite/backup erasure or deletion of chat history. Set-member suppression uses a minimal encrypted subject descriptor and exact member HMAC; legacy ambiguous descriptors do not ban an entire topic. No universal paraphrase guarantee is claimed.

Offline after initial repairs: full Python1026 passed55.51s, frontend53 passed, TypeScript/build/Ruff passed. ActualPython3.12 container subset36 passed2.57s. Later entity-retrieval changes require another final gate.

The first combined10-case DeepSeek run did NOT pass: Rust was not published from a mixed forgetting/learning input, and Orion database recall missed an already-persisted fact. Aggregate usage at that point: OpenAI36/DeepSeek73 calls, estimatedUSD0.200103. Earlier isolated10/10 results are not reused as current acceptance. Project entity metadata was absent from retrieval/renders; it is now carried through canonical records, planner context and retrieval, with an observed failing regression followed by a passing check. Positive-answer checks now also reject explicit inability/absence claims, rather than accepting a database word mentioned only as an example in a denial.

Deferred review minors: the management usage section is hidden when only successful jobs exist; local-self-hosting status navigation names About memory instead of Memory maintenance. Public release, production load/soak, independent billing reconciliation and security certification remain unverified. The current3015 browser tab is not evidence that this checkout is served there.

## Latest acceptance — after the repair pass

- **Live combined corpus:** OpenAI10/10; DeepSeek10/10, continuously in one synthetic account per provider, with a fresh empty-history recall for each question. Includes the previously failed Rust and Orion database cases. No guarantee of arbitrary phrasing, long-term production reliability or universal semantic forgetting is inferred.
- **Cumulative paid ledger:** OpenAI56/DeepSeek93 calls across initial runs, failed runs, diagnostics and accepted final runs; estimated returned-token totalUSD0.303205, below the unchangedUSD2 ceiling. All149 calls returned accounted usage; historical reservations are not spend. Paid testing is stopped.
- **Current full Python suite:**1029 passed55.65s. **Frontend:**53 passed. TypeScript noEmit, production build, Ruff and diff check passed.
- **Actual rebuilt Python3.12/Node22 images:** built successfully. Final backend-image subset38 passed3.02s. Updated disposable3031 services both became healthy; the final synthetic Compose probe passed without paid calls. Real3025 services/volumes remain unchanged.
- **Review:** nine Important findings addressed in one author-run RED→GREEN repair pass, with no second independent review. Full decision/exclusion/minor list: [review resolution](2026-10-21-semantic-memory-review-resolution.md).
- **Scope:** suitable to continue local/beta acceptance of these implemented memory flows; not a public deployment or a production load/security certification. No push/merge/public hosting or real-data backfill was performed. The200-record limit remains provisional and unchanged; canonical facts are not automatically evicted at capacity.
