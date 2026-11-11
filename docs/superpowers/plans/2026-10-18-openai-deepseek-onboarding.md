# OpenAI / DeepSeek Selection and Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Preserve the user's existing inline execution preference; this conversation prohibits implementation/review subagents.

**Goal:** Ship owner-isolated OpenAI/DeepSeek model selection and a recoverable login/Key onboarding flow, then personally evaluate actual product behavior.

**Architecture:** A server-owned catalog binds model IDs to official endpoints and capabilities. Provider-bound credentials and conversations feed existing semantic triage and the strict research graph; a dedicated DeepSeek Responses adapter and capability-gated tool backend preserve the common ModelProvider boundary. Onboarding is a frontend state machine and never automatically submits paid work.

**Tech Stack:** Existing Python/FastAPI/Pydantic/SQLite/AES-GCM/OpenAI SDK/MCP and React/TypeScript application. No aggregator, new execution sandbox, or new frontend rendering dependency.

**Spec:** `docs/superpowers/specs/2026-10-18-multi-provider-onboarding-design.md` (approved, including the user's subsequent explicit permission to test with the supplied credential).

## Global Constraints

- Only OpenAI and DeepSeek. Fixed official endpoints `https://api.openai.com/v1` and `https://api.deepseek.com`; no browser-controlled endpoints, credential-bearing redirects, or silent provider/model fallback.
- Catalog model IDs: `gpt-6.1-sol`, `deepseek-flash`, `deepseek-v4-pro`. Availability and supported parameters must be verified against official docs and actual contract tests; do not invent aliases if an account rejects a model.
- Preserve current semantic routing changes, historical GAIA artifacts and the legacy CLI adapters. Work in the existing managed worktree; do not reset or switch HEAD, stage `.DS_Store`, or create another checkout.
- Keep one active run/user, two workers, ten queued tasks, 500 seconds, 24 logical tool calls and current upload limits. Do not reintroduce the hourly task-submission cap.
- Key/temporary handle never enters browser persistence, task records, source, command arguments, prompts, logs or public errors. Saved Key consent defaults false; temporary references expire after 1800 seconds.
- New provider choice starts a new conversation. No cross-provider history sharing. Same-provider model changes affect only future runs and are reflected per-run, not retroactively.
- No key-save, login-return, dropdown-change or retry callback automatically generates a model request. Explicit send/continue is always required.
- Direct responses are not labeled verified research. Unsupported capabilities fail visibly before execution; no fake tool success, source or evidence.
- First real smoke-test round: at most six tasks, at most three per provider. No production/cloud deployment, key upload to infrastructure platforms or benchmark rerun.
- Supplied DeepSeek testing permission permits memory-only injection through non-echoed stdin and owner/provider-bound temporary sessions, not persistent credential saving. Recommend rotation without making an unauthorized change to the user's account.
- Preserve inline execution, no subagents. Inline review does not satisfy the outstanding independent production security review.

## Review Focus

1. Existing saved `model` credential plus an existing new `openai` row: migration must refuse to overwrite or delete either key; Task 2 pins this.
2. Page reload, disabled sessionStorage, expired login or a different identity returning from sign-in: no silent draft loss, history attachment to the wrong owner, Key persistence or automatic send; Task 5 pins this.
3. Duplicate request after the selected model changes or after Key expiry: same selection returns the original result, changed selection conflicts, and neither creates a second paid run; Task 4 pins this.
4. DeepSeek ignores OpenAI-style hosted tools or accepts a request without executing them: capability and local tool-result checks prevent a fake success; Tasks 3–4 pin this.
5. User changes/deletes a credential while a task is queued: generation/provider checks stop dispatch before a client is created; Tasks 2 and 4 pin this.

## File Map

| Unit | Files |
| --- | --- |
| Provider/model catalog | new `src/gaia_agent/interactive/catalog.py`, `tests/test_model_catalog.py` |
| Credential binding and migration | existing `interactive/credential_{vault,sessions,api,runtime}.py`; existing credential tests |
| DeepSeek public Responses adapter | new `providers/deepseek_responses.py`, `tests/test_deepseek_responses.py`; existing `providers/openai.py` only for a narrow request-transform hook |
| Run/conversation binding | existing `interactive/{models,store,service,api,composition,failures}.py` and interactive tests |
| Capability-safe tools | new `src/gaia_agent/interactive/tool_policy.py`; existing `mcp_client.py`, `mcp_server.py`, associated tests |
| UI onboarding/model choice | new `app/lib/{onboarding,models}.ts`, `app/components/ModelSelector.tsx`; existing AgentChat/ApiKeySettings/proxy/history/failures/CSS/tests |
| Acceptance | new `tests/fixtures/product_acceptance.json`, `scripts/interactive_smoke.py`, tests for its dry run, verification report and deployment documentation |

Python paths above are relative to `src/gaia_agent` where prefixed `interactive/` or `providers/`; frontend paths are relative to `sites/agent-web/`.

## Task 1: Catalog, Selection Validation and Anonymous Discovery

**Files:** Create `src/gaia_agent/interactive/catalog.py`, `tests/test_model_catalog.py`; modify `interactive/api.py`, frontend `app/lib/proxy.ts`, `tests/proxy.test.ts`, `tests/test_interactive_api.py`.

**Interfaces:**
- `ProviderId = Literal["openai", "deepseek"]`; frozen `ModelSelection(provider: ProviderId, model: str)`.
- Frozen `ModelProfile`: selection, display_name, endpoint, allowed_reasoning_efforts, default_reasoning_effort, capabilities and per-capability verification status/date.
- `resolve_profile(selection: ModelSelection) -> ModelProfile`, `public_catalog() -> dict`; unknown provider/model produces safe `model_unavailable` without a network call.
- Capabilities are explicit booleans for text, image, structured output, function tools, hosted search, hosted Python, audio. Initial enabled capabilities follow this application's actual adapter, not just a model's marketing list.
- GET `/v1/model-catalog` requires service token but not owner. Browser GET `/api/agent/model-catalog` allows anonymous reads of only that static catalog. All other routes retain identity enforcement.

- [ ] Write `test_unknown_pair_rejected_without_network`, `test_catalog_has_only_two_official_hosts`, `test_anonymous_catalog_does_not_read_credentials`, and proxy tests. Assert no `key`, `handle` or account-specific status in the catalog; anonymous credentials/run requests remain 401; POST catalog is rejected.
- [ ] Run `uv run pytest tests/test_model_catalog.py tests/test_interactive_api.py -q` and frontend Node proxy tests; observe missing catalog behavior fail.
- [ ] Implement the catalog and the narrow GET exception in both authentication boundaries. Maintain a safe distinction between documented/contract-tested/live-verified capabilities; no account-permission or price guarantee.
- [ ] Rerun both tests; confirm all previous proxy auth, origin, redirect and body-boundary tests still pass.
- [ ] Commit only this task's explicit files: `feat: expose capability-aware official model catalog`.

## Task 2: Provider-Bound Credentials and Explicit Legacy Migration

**Files:** Modify `interactive/credential_vault.py`, `credential_sessions.py`, `credential_api.py`, relevant credential tests and frontend proxy allowlist/test.

**Interfaces:**
- Writable credential providers: `openai`, `deepseek`, `serper`, `serpapi`; legacy `model` is readable only for status/migration, never a new execution credential.
- `CredentialMaterial(provider: ProviderId, model: SecretStr, serper: SecretStr | None = None, serpapi: SecretStr | None = None)` and `CredentialRef(owner: str, provider: ProviderId, generation: int, handle: str | None = None)`; update every internal caller/test explicitly.
- `CredentialSessions.reference(owner, provider, handle) -> CredentialRef`; `resolve(ref)` enforces owner/provider/generation/expiry before retrieving a credential.
- `CredentialVault.migrate_legacy_openai(owner: str, *, consent: bool) -> CredentialStatus`: transactionally decrypt old AAD and re-encrypt under `openai`; old and new rows cannot overwrite each other. Non-official legacy endpoint refuses migration.
- POST `/v1/credentials/migrate-model` accepts exactly `{provider:"openai", consent:true}`. Status returns endpoint mapping, masked slots and whether legacy migration is available. Temporary-session requests require provider plus secret fields; no secret is returned.

- [ ] Add tests for wrong-provider handle, wrong-owner handle, expiry at 1800 seconds, deleted/replaced Key dispatch, migration rollback, tampered AAD, existing openai+legacy conflict, literal consent and nested-error redaction. Assert both records survive a conflict; missing provider is rejected before execution.
- [ ] Run `uv run pytest tests/test_credential_crypto.py tests/test_credential_vault.py tests/test_credential_api.py tests/test_credential_runtime.py -q`; observe failures from the new binding/migration requirements.
- [ ] Implement provider binding and atomic migration. Keep the existing mutation barrier, owner-wide generation invalidation/cancellation, one temporary material per owner and 100-owner global limit. Do not relax consent or automatically migrate on startup.
- [ ] Rerun the suite, checking synthetic secret absence in response/error/log/DB plaintext checks. Existing legacy run/conversation/upload rows are not modified here.
- [ ] Commit explicit task files: `feat: bind credentials to providers and migrate legacy keys safely`.

## Task 3: DeepSeek Responses Contract Without Breaking Legacy CLI

**Files:** Create `src/gaia_agent/providers/deepseek_responses.py`, `tests/test_deepseek_responses.py`; modify `providers/openai.py` narrowly and `providers/__init__.py`; keep `providers/deepseek.py` Chat Completions behavior unchanged.

**Interfaces:**
- `DeepSeekResponsesProvider(client, model, *, retry_policy=None)` implements `ModelProvider.complete`.
- Add identity hook `OpenAIProvider._prepare_response_kwargs(kwargs: dict) -> dict`; call it immediately before a Responses transport request. DeepSeek overrides only transport compatibility and uses stateless continuation (`use_previous_response_id=False`).
- DeepSeek projects requests to its supported fields: model, instructions, input, function tools, tool_choice, text.format, max_output_tokens and a supported reasoning.effort. Do not send OpenAI-only include/store/previous_response_id/max_tool_calls or hosted tool types. Local budgets and role allowlists remain enforced.
- Native image content is admitted only when catalog+adapter declares support; unknown content types cannot be silently replaced by a text marker. Old Chat Completions vision stays false.

- [ ] Add fixture tests for tool-free routing JSON, two-turn function calls and results, incomplete/refused/malformed responses, strict schema handling, usage aggregation and partial usage, unsupported parameters, no previous_response_id, parallel calls exceeding local budget, unknown tool, real image payload and no persistence of reasoning blocks. Assert an ignored hosted-tool request can never be interpreted as an execution result.
- [ ] Run `uv run pytest tests/test_deepseek_responses.py tests/test_openai_provider.py tests/test_deepseek_provider.py -q`; observe failing new behavior and keep existing adapter characterization intact.
- [ ] Implement the dedicated projection and stateless transport. Reuse existing parsing, retry and tool-loop machinery only where it preserves each provider's contract; never replace API errors with fake successes. Refresh official DeepSeek compatibility docs before setting reasoning/image support.
- [ ] Rerun all three provider suites and inspect request fixtures; OpenAI request shape and legacy CLI responses must remain unchanged.
- [ ] Commit explicit task files: `feat: add bounded DeepSeek Responses provider`.

## Task 4: Run/Conversation Isolation and Capability-Safe Execution

**Files:** Modify `interactive/{models,store,service,api,credential_runtime,composition,failures}.py`, `mcp_client.py`, `mcp_server.py`, `deploy/configure_runtime.py` and corresponding tests; create `interactive/tool_policy.py`, `tests/test_interactive_provider_isolation.py`.

**Interfaces:**
- `InteractiveRequest` gains required provider/model with registry validation; `InteractiveConfig` keeps an OpenAI-compatible default for internal test/CLI composition but allows registry-selected model/effort for public runtime.
- `ConversationStore.create(owner, provider)` binds a new conversation; additive provider/model columns on runs and provider on conversations. Historical rows return nullable metadata and are read-only legacy history, not assigned invented providers.
- `RunService.start` enforces authentication upstream, profile validation, same-payload idempotency, conversation binding, then credential reference and dispatch. Provider/model are part of the fingerprint and the immutable run snapshot.
- `UserRuntime.execute` resolves the selected profile and matching reference, creates only its selected client, wraps it with existing secret sanitization, closes it in finally, and supplies a role/capability-filtered tool factory.
- `ToolPolicy` exposes `allowed_tools(profile, configured_search: set[str]) -> frozenset[str]`; a requested missing capability returns safe `capability_unavailable` before execution rather than allowing a fake call.
- Explicit child mode `AGENT_TOOL_MODE=openai|portable`: OpenAI mode retains existing hosted tools; portable mode creates no OpenAI client and needs no model Key in the subprocess. Portable supports registered local reads, bounded HTTP page reads, explicit independent search and local answer submission; no arbitrary Python/audio execution.
- Missing/unknown public mode fails closed. No explicit mode preserves old CLI behavior. Public tool envs whitelist selected mode, official settings and required user secrets; they never inherit another provider Key or process proxies.

- [ ] Write isolation tests: concurrent Alice/OpenAI and Bob/DeepSeek with operator Keys set; altered provider/model under the same idempotency key; expired-Key completed replay; wrong-provider conversation history; queued deletion; false tool success; disallowed compute/audio; unsupported image; empty scanned PDF. Assert wrong-provider admission occurs before any history read/client creation and portable subprocesses have no model Key.
- [ ] Add migration/startup tests: old rows remain readable with null model metadata; model/base_url fields cannot be forged; only exact official production endpoints are accepted; infrastructure helper never accepts a provider Key or transmits it during tests.
- [ ] Run `uv run pytest tests/test_interactive_provider_isolation.py tests/test_interactive_service.py tests/test_interactive_api.py tests/test_credential_runtime.py tests/test_public_runtime_configuration.py -q` and relevant MCP suites; observe RED on the added contracts.
- [ ] Implement additive schema changes, profile snapshot, idempotent replay, provider binding, explicit modes, portable backend and safe failure mapping. Preserve 500-second outer timeout, 24-call local budget and existing cancellation behavior. Same provider/model replay may return its terminal result after Key expiry without another paid request.
- [ ] Update existing fake-executor tests to specify provider/model rather than adding public request defaults. Rerun all focused suites, legacy MCP/CLI tests and the full Python suite.
- [ ] Commit explicit task files: `feat: isolate provider execution and gate unsupported tools`.

## Task 5: Model Picker, Key Guidance and Recoverable Sign-In

**Files:** Create frontend `app/lib/models.ts`, `app/lib/onboarding.ts`, `app/components/ModelSelector.tsx`, `tests/onboarding.test.ts`, `tests/models.test.ts`; modify AgentChat, ApiKeySettings, credentials/proxy/history/failures types and tests, and existing stylesheet.

**Interfaces:**
- `Selection = {provider:"openai"|"deepseek";model:string}` and server catalog client types mirror Task 1.
- `nextGate({signedIn,selection,credentialStatus,busy}): "sign_in"|"key"|"ready"|"busy"`; no helper triggers network execution.
- `saveDraft(storage, draft, now)`, `consumeDraft(storage, now)`, `clearDraft(storage)` use only one same-origin sessionStorage record: question, selection, createdAt, ownerHint (nullable). Exclude Key/handle/file/history IDs; expire after 900000 ms, consume once and handle storage exceptions visibly.
- ApiKeySettings accepts controlled open/provider/onClose/onCredentialChange props, focuses the matching provider input, and supports legacy migration confirmation. Credential statuses and temporary handles remain provider-tagged.
- ModelSelector is disabled while a task is active. Different-provider choice opens a new conversation and preserves only unsent text/file held in memory; selecting it does not send history or call a model.

- [ ] Add pure behavior tests: missing login/Key leads to the correct gate; save/return does not submit; draft expiration/single consumption/storage disabled/identity mismatch; no URL secret or prompt content; new provider creates new conversation; stale handle from another provider is never forwarded; per-message model attribution survives reopening.
- [ ] Run frontend `node --experimental-strip-types --test tests/*.test.ts`; observe RED. Add proxy tests for catalog GET, migration POST, provider fields and existing hostile-origin/identity protections.
- [ ] Implement the picker, controlled settings, explicit continue action and safe draft lifecycle. Anonymous text remains editable. Before auth navigation show temporary draft notice and an option not to keep it; attachments are not persisted and require re-selection after full navigation. Sign-out clears the draft. Storage failure shows copy-before-login guidance.
- [ ] Implement safe API-error actions: expired auth → sign-in; missing/expired/revoked/unauthorized Key → matching settings; quota → provider usage guidance; capability → supported-model choice. Keep the original question and idempotency state until an accepted response; never automatically send on save or polling recovery.
- [ ] Run Node tests, TypeScript noEmit and production build. Personally check local browser desktop/mobile/keyboard flows using synthetic keys and mocked auth where needed, clearly labeling mocks. Verify a real sign-in separately only on a real authorized hosting environment; do not mistake local mock headers for real authentication.
- [ ] Commit explicit task files: `feat: guide sign-in and provider credentials from the composer`.

## Task 6: Actual Product Evaluation, Regression and Handoff

**Files:** Create `tests/fixtures/product_acceptance.json`, `scripts/interactive_smoke.py`, `tests/test_interactive_smoke.py`, `docs/superpowers/reports/2026-10-18-provider-onboarding-verification.md`; update `docs/web-deployment.md` and relevant readme text.

**Interfaces:**
- Acceptance fixture has exactly the spec's 15 case IDs, literal expected behaviors, capability expectations and forbidden behaviors; never GAIA gated data. Inputs include tiny synthetic TXT/CSV/PDF/XLSX files generated only in temporary test directories.
- Smoke CLI accepts non-secret provider/model/case/owner options. Key enters via non-echoed stdin only (or existing owner-bound saved OpenAI credentials). It uses a local temporary credential session, outputs only task status, safe errors, usage/timing/tool counts and approved synthetic-case answers; it neither prints the handle/Key nor saves credentials or raw provider payloads.
- Dry-run mode validates cases, selection and upper bounds without reading secrets or making network requests. Six-run ledger prevents accidental repetition; resume reads only non-secret task IDs/results and does not resubmit accepted idempotent tasks.

- [ ] Write dry-run tests: no network/Key read; unknown provider rejected; seventh total task or fourth for a provider rejected; secret-containing nested failure redacted; accepted-but-disconnected request resumes without another submission; fixture has 15 distinct cases and no secret fields.
- [ ] Run `uv run pytest tests/test_interactive_smoke.py -q`, observe RED, then implement the case harness and dry-run validation.
- [ ] Run the full Python suite, Ruff (`src tests deploy scripts/interactive_smoke.py`), frontend Node suite, TypeScript noEmit, production build and git diff checks. Baseline before this feature should still include the previously verified 756 tests; report actual counts rather than requiring a hardcoded total.
- [ ] Ensure no active user tasks before restarting the local backend with existing infrastructure configuration. Never dump runtime env files or credentials tables. Migrate the existing legacy OpenAI Key only after the user's explicit migration confirmation; do not read it into tool output.
- [ ] Run exactly these three accepted tasks/provider in the authorized first round: (1) translate a meeting notice without inventing a date; (2) follow up in the same conversation asking for a formal rewrite; (3) in a new conversation, read a tiny registered TXT naming Python and its official definition URL, then extract the file's project name and check the definition against that URL with a source. Each accepted question counts as one task, not one conversation. Clarification and unsupported calculation/audio stay in offline coverage for this round; do not claim them live-tested.
- [ ] For each actual run inspect the webpage or terminal and persisted safe progress/result: route, answer quality, source relevance, file-derived values, tool execution, latency and tokens. Mark unsupported-capability outcomes separately from successful analyses. Fix reproducible failures with a regression test, then rerun offline; seek an increased real-call budget before exceeding six tasks.
- [ ] Verify cancellation/reopen and anonymous/no-Key guidance with synthetic fixtures; capture final screenshots without settings secrets. Check browser/dev/backend error text for synthetic Key absence, and ensure no test-only runtime/mock changes remain in shipped configuration.
- [ ] Write the verification report with exact commands, outcomes, actual provider/model IDs, real vs mock coverage, unresolved issues and public-release gates. Do not claim all 15 cases passed live from six smoke runs or claim mainstream-product equivalence.
- [ ] Commit explicit completed task files after fresh verification; do not push, merge or deploy without authorization. Keep the local app running if safe. Present remaining release blockers and the next concrete decision rather than claiming production readiness.

## Self-Review and Execution Handoff

- Spec coverage: catalog/selection and discovery → Task 1; encrypted binding/migration → Task 2; protocol → Task 3; runtime/tools/metadata/budgets → Task 4; auth/drafts/UX → Task 5; real observation and evidence → Task 6.
- Review Focus cases are explicitly assigned to their owning task's tests. Old `DeepSeekProvider` remains distinct from the new `DeepSeekResponsesProvider`; no model ID is silently rewritten to satisfy old defaults.
- Product source changes begin only after this plan is reviewed. Preserve the user's inline/no-subagent execution method; no further provider/scope selection question is needed.
- Official references: the approved spec's OpenAI Docs and DeepSeek Responses links. Refresh model capability details before coding provider parameter mappings; documentation alone does not prove account access.
