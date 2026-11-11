# Semantic memory: independent review and repair decisions

One fresh-context read-only reviewer inspected cf92f72..192c4ca. Its verdict at that head was **not ready**, with nine Important findings and no P0/Critical finding. The author reproduced the concrete failures and performed one regression-backed repair pass. There was no second independent review; passing author-run gates below is not an independent security certification.

## Findings addressed

1. **Provider-hidden canonical mutation:** append requires a compatible set target; every implicit canonical lookup is checked against the provider-eligible IDs. `test_append_cannot_retarget_a_provider_hidden_canonical_record` was RED then GREEN.
2. **Existing saved credentials:** constructor migration idempotently seeds missing epoch rows at zero without rewriting credential ciphertext or resaving a Key. `test_existing_saved_credentials_gain_a_zero_epoch_without_resaving` was RED then GREEN.
3. **Populated-account request overflow:** select bounded relevant records/barriers, retain the complete sanitized current source, and enforce the full16000-byte request budget. Preflight now precedes dispatch journaling. `test_planner_bounds_populated_account_and_many_barriers_without_losing_relevant_target` was RED then GREEN, using200 records and200 barriers.
4. **Erased live maintenance copies:** retire finalized/unretryable and expired payloads while retaining journal/accounting metadata; explicit deletion blanks only the deleted fact in mixed retryable events and removes its proposal references. Explicit clear-all retires earlier memory-owned work within its owner/watermark. Erasure tests for ordinary/permanent/clear were RED then GREEN; mixed-source preservation has separate coverage.
5. **Set scope and restoration:** distinguish single-slot and set-member barriers, retain an encrypted minimal subject descriptor, keep exact member HMAC checks, and refresh the transaction's barrier view after restore. An unrelated same-topic set member is no longer banned. Main set-member regression was RED then GREEN; paraphrase, restore-plus-add and stale-subject-after-manual-edit regressions are also covered. The manual-edit regression was independently observed RED then GREEN.
6. **Sensitive admission:** pre-persistence redaction includes SSN and payment identifiers/multilingual labels. The original two cases were RED then GREEN; tests also inspect actual decrypted worker-admitted source events and preserve independent safe clauses.
7. **Temporary-key retry:** keep the original opaque reference for its still-valid eligible retry, bounded to1000 process references with expiry/invalidation/completion pruning. Never substitute a saved/new Key. `test_failed_temporary_job_retains_its_original_valid_reference_for_one_retry` was RED then GREEN and completes exactly two synthetic dispatches.
8. **Capacity visibility:** persist `memory_limit` on failed/partial batches, retain successful candidates, and show an actionable bilingual frontend reason. The200-record rejection and frontend reason were RED then GREEN; a199-record/two-operation batch proves one fact remains saved and the second rejection remains visible. Capacity is still a provisional200 canonical-fact limit, not a model limit or a measured optimum. No capacity-based automatic fact eviction was introduced.
9. **Truthful foreground memory state:** return pending/paused/temporary/unavailable/ready accurately, report the IDs actually sent, and send bounded no-false-save guidance even with empty memory. Five state/context cases were RED then GREEN. Per-turn memory notifications remain hidden.

## Additional live-effect regression

The first combined post-review DeepSeek corpus failed Rust capture and Orion database recall. Investigation showed project entity metadata was not carried through planner/retrieval/renders, so an atomic database fact could be omitted and two projects' goals appeared ambiguous. Added entity metadata, entity-aware retrieval, and a RED→GREEN project-entity regression. Positive-answer assertions now reject explicit inability/absence claims rather than accepting a database name mentioned merely as an example. Previously failed runs remain reported.

Latest full combined corpus in one synthetic account per provider: OpenAI10/10 and DeepSeek10/10, each with fresh empty-history recall per question. Private synthetic reports: `/private/tmp/toyagent-semantic-acceptance-20261021-zVW0kn/openai-post-review.json` and `deepseek-entity-retrieval.json`. Cumulative calls across all initial runs, failures, diagnostics and final runs: OpenAI56/DeepSeek93; estimated usage USD0.303205. Paid testing stopped. No real source chats or memories were read, and source credentials were queried read-only and kept in RAM. No finite corpus establishes universal intent or forgetting accuracy.

## Rulings, in execution order

1. Bind verified observations in encrypted events and add operation observation IDs, since a source-kind label alone cannot prove evidence provenance. Cost if wrong: adjust additive fields; legacy events keep defaults.
2. Explicit destructive/restoration actions bump control generation; ordinary automatic additions bump content revision only. Cost if wrong: an explicit deletion can interrupt an older answer rather than disclose erased information.
3. Increase the fixed output allowance1600→6400 after actual DeepSeek truncation at1600 and3200; retain one dispatch and30s timeout. Cost if wrong: a pathological call spends more tokens before a safe failure, never an automatic paid repair loop.
4. Reuse distributed real-store crash/privacy/capacity tests instead of duplicating them all into one end-to-end file; explicitly distinguish limited browser checks from controlled owner/CAS/race tests. Cost if wrong: browser-only integration defects can remain.
5. Explicit erasure is an exception to source immutability: blank only the deleted fact in mixed retryable events, retire finalized/expired bodies, and retain accounting. A broad purge on every generation bump was rejected and was not implemented. Cost if wrong: logical redaction does not physically erase SQLite pages/backups, or semantically identify every paraphrase in arbitrary failed-source text.
6. Set barriers retain a minimal encrypted extracted subject, not the erased assertion/source; legacy subject-less descriptors do not become topic-wide bans. Cost if wrong: ambiguous old paraphrases may need clarification/restoration, and model matching is not perfect.
7. Bounded relevant planner context replaces full-account serialization; complete canonical permission/conflict/barrier checks remain server-side, and local preflight precedes charging journal reservation. Cost if wrong: an unavailable semantic target requires clarification, not an invented mutation.

## Deferred minors

- The management usage section is hidden when only successful/unchanged jobs remain; accumulated successful usage is not independently accessible there.
- Local-self-hosting status navigation says About memory rather than Memory maintenance.

## Reviewer exclusions and author rulings

- Real3025 upgrade/user data: excluded and untouched. Cost: the old running instance has not loaded this code.
- Paid reliability/new cases: use measured root-run synthetic reports, not the reviewer's authority. Cost: no statistical reliability guarantee.
- Invoices/pricing: usage-based estimate only, not independent billing reconciliation. Cost: actual invoices can differ.
- Docker/backup/scans/browser artifacts: rely on scoped author-run checks, not independent reruns. Cost: environment-only issues can remain.
- Full suite/build/lint: rely on fresh author-run command evidence. Cost: not an independent execution.
- Universal semantic guarantees: explicitly not claimed. Cost: novel phrasing may fail.
- Production load/soak/disk growth/latency distribution: not performed; acceptance is single-instance bounded behavior. Cost: no production throughput or latency guarantee.
- Cryptography/penetration-test certification: not claimed. Cost: residual security risks remain.
- Unrelated dirt/pre-existing behavior: preserved, not incorporated into this patch. Cost: unrelated defects may remain.
- Reminders/vector services/multi-instance deployment/historical backfill: remain out of scope. Cost: none of those capabilities are promised.

The deployment decision remains separate. No push, merge, public hosting or real3025 upgrade is authorized by these test results alone.
