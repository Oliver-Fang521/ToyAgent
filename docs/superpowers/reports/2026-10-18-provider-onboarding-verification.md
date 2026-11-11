# OpenAI / DeepSeek selection and onboarding — local verification

Date: 2026-10-18, America/Chicago. Implemented in the managed public-agent-web worktree.
No push, merge, cloud deployment, or GAIA rerun is part of this result.

## Implemented

- Server-owned catalog and fixed official endpoints; explicit provider/model on every new run.
- Provider-bound encrypted credentials, temporary references, conversation binding and immutable
  answer attribution. Old rows keep unknown metadata; new provider starts new history.
- Explicit atomic legacy OpenAI migration; conflict/failure retains the original credential.
- Stateless DeepSeek Responses function-tool loop, real image payload gate, safe refusal/incomplete
  handling, aggregate usage, bounded retries and local tool budgets. Legacy CLI adapter unchanged.
- Portable DeepSeek tool child never creates an OpenAI client or receives a model Key. Optional
  independent search, registered file reads and bounded public page reads remain available.
- Controlled provider settings, missing-login/Key guidance, explicit send after returning, optional
  15-minute single-consume tab draft, mobile picker and per-answer model attribution.

## Actual paid acceptance: 6 accepted tasks, no resubmission

The user's existing encrypted OpenAI Key was explicitly migrated through the approved confirmation
flow, not read into tool output. The provided DeepSeek Key entered through non-echoed TTY stdin,
was used only in an owner/provider-bound temporary session, and was not saved as a long-term Key.
Only official model endpoints were used. No operator-key fallback or aggregator was used.

| Provider/model | Case | Outcome/route | Seconds | Total tokens | Tool calls |
| --- | --- | --- | ---: | ---: | ---: |
| OpenAI / gpt-6.1-sol, low | Translation | Completed/direct | 4.25 | 406 | 0 |
| OpenAI / gpt-6.1-sol, low | Same-conversation formal rewrite | Completed/direct | 3.04 | 481 | 0 |
| OpenAI / gpt-6.1-sol, low | TXT + official-source check | Completed/research | 34.32 | 7,490 | 3 |
| DeepSeek / deepseek-flash, provider default | Translation | Completed/direct | 2.22 | 549 | 0 |
| DeepSeek / deepseek-flash, provider default | Same-conversation formal rewrite | Completed/direct | 2.03 | 599 | 0 |
| DeepSeek / deepseek-flash, provider default | TXT + official-source check | Completed/research | 23.22 | 13,328 | 3 |

Manual quality checks, not just HTTP status:

- Both translations retain Friday, 3 p.m. and five minutes early without adding “this Friday” or
  a calendar date. Both follow-ups use the prior notification and retain those facts.
- Both research answers identify **Orion**, read from a tiny registered TXT, and correctly describe
  Python as interpreted, object-oriented, high-level, with dynamic semantics. Supporting evidence
  includes the actual official page `https://www.python.org/doc/essays/blurb/`.
- Stored progress confirms real file/page tools and terminal answer submission, rather than model
  claims of tool execution. Strict validation remains enabled. OpenAI's research run used a bounded
  correction before approval; DeepSeek's final answer is accurate but more terse/GAIA-like English.
- Aggregate tokens: OpenAI 8,377; DeepSeek 14,476. Do not infer general speed, cost or quality ranking
  from these single-trial samples. No dollar estimate is fabricated.

Non-secret task IDs: OpenAI `655971b9-4701-452c-902d-819e2f0662f0`,
`b3359f90-e3e6-46f9-99a2-a612188dbfc5`, `ae97bb66-e02c-48b6-b7db-aa4922cfa49f`;
DeepSeek `3eec2264-7334-4045-a1f4-23350a532952`,
`eaf3abdc-e046-4ef4-b967-528c489f8f06`, `896e470a-71dd-42c9-9e3a-7f2aaf18d962`.
The local non-secret six-task ledger prevents accidental additional accepted tasks. Reopening a
completed ledger reads saved results only. No more paid tests are authorized in this round.

## Bugs caught and fixed before DeepSeek paid research

The legacy file/lookup/critic agents treated all OpenAI tools as mandatory. Merely filtering MCP
tools would therefore reject DeepSeek file reading because Python/audio/search were missing.
A regression first failed; public role allowlists now intersect the actual configured capabilities.
Computation still requires real Python, and unsupported calculation returns `capability_unavailable`
before a computation-model/tool invocation. The CLI's role policy is unchanged. DeepSeek research
then passed with portable tools, no model Key in its subprocess.

## Offline and browser evidence

- Python: `uv run --no-sync pytest -o addopts='' -q -p no:cacheprovider` → **787 passed**
  in 51.08 seconds after final backend review fixes. The pre-feature baseline was 756.
- `uv run --no-sync ruff check --no-cache src tests deploy scripts/interactive_smoke.py` passed.
- Frontend: `node --experimental-strip-types --test tests/*.test.ts` → **27 passed**;
  TypeScript `--noEmit --incremental false` and production `run-framework.mjs build` passed.
- Dry run returns exactly 15 distinct product acceptance cases without network or credential reads.
  They are synthetic product cases, not GAIA. Tests cover ledger limits/resume, redaction, provider
  isolation, migration conflict/rollback, queued revocation, replay after expiry, capability gates,
  routing, bounded tools, cancellation and historical metadata.
- Personally exercised local browser: missing DeepSeek Key opens the matching domain/settings;
  synthetic temporary save clears input and does not send; switching providers retains the unsent
  question and clears history; local mock sign-out/sign-in restores the draft without auto-send;
  Escape closes native dialogs; 390×844 mobile layout is usable and viewport was reset.
- Screenshots: `/private/tmp/toy-agent-mobile-2026-10-18.png` and
  `/private/tmp/toy-agent-provider-settings-2026-10-18.png` (masked status only), plus
  `/private/tmp/toy-agent-live-result-2026-10-18.png` showing actual DeepSeek research reopened.

## Final implementer review and rulings

The whole feature range from `6a8c41b` was reviewed inline, against the five plan Review Focus
cases. The approved no-subagent constraint was retained; this is not independent security review.
One fix pass added regression cases observed RED then GREEN:

- Public result projection removes local `file://` references and task-workspace prefixes after
  evidence validation; old results are projected safely on read without rewriting stored history.
- DeepSeek transport now allowlists documented fields and rejects returned hosted-tool output
  types, rather than allowing an ignored/unexecuted hosted operation to look successful.
- Expired authentication has a sign-in recovery action, distinct from Key failure; polling stops
  on 401 instead of repeatedly retrying. Settings preserve HTTP status for the same guidance.
- Reopening queued/running history restores the original cancelable task instead of discarding
  its run state; it never resubmits the question. Pure history/recovery tests cover this behavior.

These last hardening changes were verified offline, not with additional paid tasks: the six-task
authorization was exhausted. Hosted auth-expiry behavior remains an online release check.
Final frontend verification used fail-fast sequencing: all 27 Node tests, TypeScript noEmit and
the production build passed after narrowing the unknown JSON error response type. Final backend
restart cleared the temporary DeepSeek test session from memory. A read-only GET of the existing
DeepSeek run returned completed/200 with neither `file://` nor private task paths exposed; the
browser reopened the same answer/source cards, with zero captured browser warnings/errors and no
synthetic UI Key in those logs. No additional paid task was submitted.

Rulings made during execution, with costs if wrong:

1. Keep inline/no-subagent execution over the generic review-dispatch workflow: author blind spots
   remain possible, so independent production security approval is still required.
2. Use an explicit temporary OpenAI internal bridge while Task 2 changed credential references:
   risk was wrong-provider fallback if retained; Task 4 removed it and public selection is required.
3. Omit DeepSeek reasoning effort because the exact partially supported enum could not be verified:
   costs explicit effort control, but does not invent equivalence to OpenAI low.
4. Include previously authored semantic-routing code/tests in the execution commit because the
   runtime already depended on that untracked module: costs a wider regression surface; full tests ran.

Deferred minors: catalog verification labels remain conservatively documented rather than claiming
all enabled capabilities were live-tested; a richer per-capability evidence UI can follow. Research
answers can still be terse/GAIA-like; user-language polish needs its own evaluated follow-up.
Cloud deployment, real authentication and platform retention were not silently waived: they remain
explicit release blockers below, not behaviors certified by this self-review.

Real provider tests used the actual local backend/API/routing/MCP path; browser authentication used
Sites' existing local mock identity. No real hosted ChatGPT login is claimed. No real live coverage
is claimed for clarification, verified arithmetic, audio, vision, V4 Pro, PDF/CSV/XLSX, cancellation,
expired-session recovery or every one of the 15 cases; those have only the stated offline coverage.

## Release boundary

This is a locally usable implementation with a small successful smoke sample, not evidence of
mainstream-agent equivalence or unrestricted public readiness. Before public promotion:

1. Independent BYOK security review (this plan explicitly uses inline implementation/self-review).
2. Actual hosted HTTPS/native sign-in, cross-user isolation and platform logging audits.
3. Verified Modal/Sites retention, backup/restore and Key deletion semantics with accurate privacy text.
4. Separate infrastructure transfer/deployment authorization and a broader repeatable evaluation.
5. Rotate the Key disclosed in chat; this task does not rotate credentials on the user's behalf.

The local frontend/backend can remain running on ports 3000/8000. Keep API billing limits at each
provider; task/time/tool limits are not a dollar-spend cap. See `docs/web-deployment.md`.
