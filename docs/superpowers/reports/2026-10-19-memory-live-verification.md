# Memory live verification and language-priority repair

Date: 2026-10-19 (America/Chicago). User explicitly approved 4 logical tasks,
2/provider, at most 8 model requests, no research/search/tools, then requested continued
optimization of any discovered failure. No deployment or push was requested/performed.

## Actual scope and spending boundary

Used existing visitor-owned encrypted credentials, read from the local vault without printing
or copying them into source/argv. Each provider received a separate, disposable synthetic account
and temporary memory database, not the user's real history/profile. No website/search/MCP tools
were invoked. Actual requested models: `gpt-6.1-sol` and `deepseek-flash`.

`scripts/memory_smoke.py` exercises the public UserRuntime/composition/extraction/retrieval path,
with test-only restrictions: one transport attempt (no retries), no research-graph entry, no tools,
and a 90-second outer deadline. Normal production retry/deadline settings are not changed by
these test restrictions. The wrapper reserves each actual SDK request **before** network I/O.
The new ledger is separate from the previously exhausted six-task ledger and is not reset.
Process locking prevents two jobs sharing an allowance concurrently; dry-run reads no credentials.

Actual total: **7 model requests, 4 original logical cases**. OpenAI used 4, DeepSeek used 3.
One request remains unused. The extra OpenAI request rechecks the same failed recall case after
repair, within that case's two-request and the global eight-request limits; it is not a new case.
All observed token usage, including the original failing response, totals **4,233 tokens**
(3,430 input, 803 output; reasoning tokens are a subset of output, not added again).
These token counts are not a currency-cost claim or a benchmark score.

## Observed results

Setup statement: `我喜欢用中文回答。请简短确认。`
Fresh-conversation query, with no conversation history: `Explain a Python list in two short sentences.`

| Provider / stage | Requests | Observed behavior |
| --- | --- | --- |
| OpenAI capture | 2 | Chinese confirmation, 1 automatic memory saved |
| OpenAI original recall | 1 | 1 memory retrieved, but answered in English — **quality failure** |
| DeepSeek capture | 2 | Chinese confirmation, 1 automatic memory saved |
| DeepSeek recall | 1 | 1 memory retrieved and a Chinese two-sentence explanation — passed this case |
| OpenAI post-fix recall | 1 | 1 memory retrieved and a Chinese two-sentence explanation — passed this recheck |

The initial API tasks all completed without transport errors; that is **not** the same as all
quality cases passing. The OpenAI failure was retained in the private ledger's `before_fix`
record, with its usage included in the totals. The recheck manually reconstructed the known
synthetic language preference in a new temporary database (the original disposable DB had already
closed); it used a real model call but did **not** re-test automatic extraction end-to-end.
Both providers' original capture steps did independently demonstrate live extraction/storage.

## Cause, repair and evidence

The memory write/read path worked: saved_count=1 and used_ids contained that record. The routing
instruction merely said to answer “in the user's language”, while the memory wrapper said current
instructions override old preferences. An English question was therefore ambiguous about whether
its surface language overrode an explicitly stored Chinese reply preference. DeepSeek honored the
preference; OpenAI did not in the observed run. This is a model-facing priority ambiguity, not
proof that memory failed to persist or retrieve.

Changed only the direct-answer routing instructions:

1. Explicit output-language / translation-target request in the current question wins.
2. Otherwise honor a saved ordinary reply-language preference.
3. Otherwise match the current question's language.

Merely typing English does not itself request an English answer. Memory remains untrusted data,
used for presentation rather than routing, verification or tool permissions. No blanket verifier
disable, extra repair call or provider fallback was added.

Live acceptance observed the wrong-language result before the change, then a Chinese reply after
the change with the same question and known synthetic preference. This is narrow empirical
evidence, **not a guarantee** of perfect language adherence. Offline fake-provider tests are not
represented as proof of real-model instruction following.

## Verification and remaining limits

- Budget/tool/process-lock tests were observed RED→GREEN; the ledger refuses ninth requests,
  third tasks, replayed completed cases, unauthorized rechecks and concurrent live jobs.
- Existing routing, research isolation and memory lifecycle regression suites were rerun.
- Final `uv run --no-sync pytest -o addopts='' -q -p no:cacheprovider`: **880 passed, 50.67s**.
  `ruff check --no-cache src tests deploy scripts` and `git diff --check` passed. Frontend Node
  suite: **33 passed**; TypeScript noEmit exited 0. No frontend product code changed this round.
- No additional paid request is necessary just to repeat a now-passing case. A new allowance
  would be needed beyond the remaining one-request cap or for new logical cases.
- Real explicit-language override, broader languages, varied background/project memory quality,
  mobile 390px behavior, independent security review, real hosted auth and backup/retention
  checks remain unverified. The earlier local implementation report's release gates still apply.
- The synthetic databases were removed by their temporary-directory lifecycle. No test fact was
  written to the real user's memories, and no real account memory was cleared or modified.

Private evidence ledger: `.web-runtime.private/memory-smoke-2026-10-19.json` (ignored, no keys).
Safe public evidence: this report. The harness is local QA tooling, not a new product API or a
deployed authentication bypass.
