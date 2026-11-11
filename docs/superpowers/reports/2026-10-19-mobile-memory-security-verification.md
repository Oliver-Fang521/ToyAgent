# Mobile, memory lifecycle and security follow-up

Date: 2026-10-19. Local verification in the existing managed worktree. No deployment,
push, new paid model calls, credential changes or real-user memory mutations.

## Browser verification

The previously ineffective viewport override now works. CUA measured actual DOM widths:

| Viewport | Document scroll width | Memory dialog width / scroll width |
| --- | --- | --- |
| 390 × 844 | 390 | 356 / 356 |
| 320 × 740 | 320 | 286 / 286 |

Inspected screenshots of the homepage, navigation and memory dialog. The dialog scrolls
vertically (at 390px: client height 741, scroll height 943); the bottom return button was
successfully activated at both widths. Navigation opened and closed at 390px. Model
selection, composer and memory controls remained visible. Real local account memory
remained empty. No test facts were added to that account. Override reset afterwards.

Evidence: `/private/tmp/toy-agent-memory-mobile-320.png`.
This supersedes the earlier report's viewport-tool blocker, not its other release gates.
These are desktop browser responsive checks, not physical iOS/Android keyboard, touch,
screen-reader or safe-area certification. Populated-list/conflict flows were previously
checked with disposable desktop fixtures, not repeated on physical phones here.

## Reproduced issue and fix

Repository updates accepted NaN and positive infinity as expiration timestamps. SQLite
can convert NaN to NULL, unintentionally removing expiry. The HTTP API already rejects
nonfinite numbers; this was a lower-level defensive validation gap, not a demonstrated
HTTP bypass. Added finite-number validation before mutation. Tests first produced two
failures (NaN/+infinity), then passed for NaN, both infinities and unchanged revision/state.

Additional real SQLite tests verify default provider isolation, explicitly enabled sharing,
other-owner exclusion, and exclusion exactly at and after expiration. API tests exercise
all five mutation routes with absent and incorrect service authentication, requiring 401,
no input echo, and unchanged stored records/settings. Existing lifecycle acceptance tests
also re-run: pause/resume gaps, temporary mode, deletion suppression/relearning barriers,
manual-edit priority, delayed extraction, source rejection and replay prevention.

## Verification

- Repository targeted suite: 14 passed after fix.
- Memory API suite including new mutation-auth cases: 9 passed.
- Frontend Node tests: 33 passed; TypeScript no-emit check exit 0.
- Frontend production build: all five stages passed; no deployment.
- Ruff check: passed.
- Interim full Python suite: 884 passed in 50.42s (before two final auth cases).
- Final full Python suite after all code/test edits: 886 passed in 51.06s.
- Existing local backend health check: `{"status":"ok"}`. No server restart performed.

An initial baseline run was 879 passed / 1 failed: the 10ms orchestrator deadline elapsed
before planner admission, whereas its test expected one planner call. A standalone retry
also reproduced this on cold initialization, while both subsequent full runs passed.
Corrected the test's call expectation to permit either no planner admission or one planner
call: timeout is still mandatory and later stages remain forbidden. No production timeout
was extended or disabled. Targeted cold-start verification was repeated after this
test-only change, followed by the final 886-test full verification above.

## Remaining release boundaries

Local tests cover authorization, owner/provider filtering before decryption, expiry,
encrypted memory storage, mutation revisions/revocation, safe errors and proxy-origin gates.
They do not certify native hosted login/header sanitization, production HTTPS, deployed
secret/log handling, cloud backup retention/deletion, recovery drills, load or an independent
security review. Ordinary conversation storage is not covered by memory-body encryption.

Memory quality also remains bounded: one profile record per facet is replacement-based,
not additive merging of every learning subject; retrieval is lexical rather than semantic.
No new live-model quality measurement or GAIA benchmark was performed in this follow-up.
