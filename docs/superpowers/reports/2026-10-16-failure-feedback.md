# Stage- and component-aware failure feedback

Approved bounded change: show which stage failed, whether the model, a named tool, credentials,
model-output parsing or verification failed, and give a safe next action without exposing keys.

## Implementation

- Add optional persisted `runs.failure` JSON with additive SQLite migration. Existing records and
  credential tables are preserved; legacy failures derive their stage from existing progress and
  explicitly report missing details rather than inventing a cause.
- Keep stage events in chronological order (including correction/research re-entry). Persist only
  allowlisted tool names from tool start/failure/completion events, never arguments or result text.
- Preserve safe SDK diagnostics before wrapping exceptions: allowlisted type, HTTP status,
  allowlisted provider code and bounded request ID; omit raw messages, headers, body and traceback.
- Separate model service, local model-client code, tool, credentials, validation, model output and
  attachment components. Identify a tool only when terminal failure and unrecovered tool evidence
  support it. A later model failure is not blamed on an earlier recovered tool failure.
- Clear stale model-failure diagnostics after successful retries and preserve safe orchestrator
  failure categories through the BYOK wrapper (previously collapsed to execution_failed).
- Frontend uses the same failure card for polling, idempotent terminal responses, refresh and
  reopening a conversation. It highlights the stage and shows suggestions, diagnostic details and
  a copyable task ID. `latest_run` is owner-scoped and enables reloading failed conversations.
- Backend emits safe structured `agent.run_failed` events to its existing process log and persists
  the same failure metadata in SQLite; no raw provider exception is logged.

## Verification

- New tests observed failing before implementation, including actual UserRuntime → RunService
  tool failure propagation, retry recovery, legacy reload and cross-user denial.
- Python full suite: **732 passed in 50.15s**.
- Frontend Node suite: **16 passed**; TypeScript noEmit, production build, Ruff and diff checks passed.
- Real local read-only API check and browser inspection: existing failure appears as planning-stage
  model-call failure with unknown cause, task ID and expandable diagnostics. Screenshot retained at
  `/private/tmp/toy-agent-failure-feedback-20261016.png`.
- Detailed quota/tool failures exercised with synthetic errors only. No model generation or tool
  execution was triggered by verification. Existing user tasks were not retried.

Local backend restarted only with no active tasks, retaining existing data/master key; frontend
remains at http://127.0.0.1:3000/. Old exceptions cannot be reconstructed retroactively. These are
local changes, not a cloud deployment or a claim that the underlying model-call failure is fixed.
