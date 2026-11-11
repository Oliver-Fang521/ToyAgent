# Model-driven conversational routing — local verification

Date: 2026-10-17 (America/Chicago). Changes are in the managed public-agent-web worktree.

## Approved behavior

- The connected visitor-owned `gpt-6.1-sol` model, low effort, judges text requests using
  bounded conversation history. It returns a strict direct/research/clarify decision and,
  for direct/clarify, a concise answer. Greetings use the same model path, not a keyword map.
- No tools are exposed during triage. Attachment requests conservatively enter research.
  Invalid/incomplete decisions, missing replies and contradictory verification flags cannot
  authorize direct replies; they fall back to strict research. Provider failures stop rather
  than automatically starting another paid run. Routing usage is included in token totals.
- Direct replies are not labeled as verified research. Clarification asks for missing information.
  Research retains the existing evidence policy, correction budget and submission validation.
- Only allowlisted rejection categories are retained and translated in the frontend. Raw critic
  feedback, exception bodies and internal reasoning are not persisted as public diagnostics.
- Removed the hourly task-submission cap at the user's explicit request. Preserved one active
  run/user, two global workers, ten queued tasks, deadlines, tool budgets and upload limits.
  No total API dollar budget is implied; provider billing limits remain the user's responsibility.
- Stage progress marks skipped stages as unexecuted rather than claiming they completed.

## Actual local browser checks

Tests used the user's existing saved credentials and configured endpoint, with permission for
bounded real calls. No keys were displayed, changed, copied to another provider or deployed.

| Request | Observed path and result |
| --- | --- |
| Translate an English meeting notice | Model routing → direct; natural Chinese translation |
| Follow up with "more formal, for a professor" | Direct; correctly used prior conversation |
| Retranslate preserving the original date | Direct; "周五", without the unsupported "本周" |
| Vague "帮我处理一下" in a fresh conversation | Clarify; asked what content/requirements to process |
| Read a Python official page and provide a source | Routing → plan → read_webpage → verify → submit; completed with official source |
| Earlier live 17 × 23 verification | Research/execute_python completed with 391; result survived reopening |

The initial formal rewrite introduced "this Friday" although the source only said "Friday".
The prompt now explicitly preserves dates/numbers/names/claims during transformations; an actual
retest returned the correct unqualified weekday. This is an observed improvement, not a guarantee
against all hallucinations. Intent classification is also model judgment, not a perfect detector.

Screenshot: `/private/tmp/toy-agent-semantic-routing-20261017.png`.

## Verification and release boundary

- Regression tests cover direct/clarification routing, history, tool-free classification,
  research/attachment paths, invalid/incomplete routing, strict boolean flags, provider failure,
  token accounting, persisted safe rejection reasons and removal of the hourly task limit.
- Frontend Node suite: 19 passed. TypeScript noEmit passed. Production build passed.
- Ruff across src/tests/deploy and git diff checks passed.
- Final Python full suite: 756 passed in 49.22 seconds.
- Changes have not been pushed, merged or deployed. No GAIA benchmark rerun or score relabeling.
- Independent security review remains outstanding; the existing plan forbids subagents here.
  Review was therefore inline by the implementer, not an independent security certification.
- Public release remains gated on real hosted login/HTTPS, deployment authorization, actual
  infrastructure logging and snapshot retention/deletion verification in `docs/web-deployment.md`.

Local web app remains at `http://127.0.0.1:3000/` with its backend on port 8000.
