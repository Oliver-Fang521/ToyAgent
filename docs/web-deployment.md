# Interactive website deployment

For **single-user local Docker deployment**, use [local-self-hosting.md](local-self-hosting.md).
It has independent password login and does not require Sites/Modal. The hosted authentication
and cloud deployment instructions below are a separate deployment mode, not prerequisites for Docker.

Status: OpenAI/DeepSeek BYOK and default-on account memory implemented locally. The previous
six-task paid acceptance allowance is exhausted; new memory-model tests need a separate budget.
See the dated verification reports for actual results and unverified release gates.
Cloud deployment is not authorized by this local implementation workflow.
The website uses native Sites sign-in and a server-only proxy; Python runs separately on Modal.
GPT-6.1 Sol / low is a configured preference, not guaranteed API availability. Unsupported accounts
fail without model/provider/owner-key fallback. Historical GAIA scores and CLI remain unchanged.

## Runtime configuration

- Python: `AGENT_SERVICE_TOKEN`, `AGENT_CREDENTIAL_KEYRING`,
  `AGENT_CREDENTIAL_ACTIVE_VERSION`, `AGENT_MODEL_BASE_URL`.
- Sites: `AGENT_BACKEND_URL` and secret `AGENT_SERVICE_TOKEN`.
- Never put secrets in browser code, hosting.json, Git, command-line literals or tracked env files.

Keyring is JSON mapping version names to base64-encoded 32-byte random AES keys. The active version
must exist. Missing/malformed infrastructure configuration fails closed: no automatic ephemeral
master key. Master keys stay separate from SQLite and are never given to the frontend Site.
Public model endpoints are server-owned constants: `https://api.openai.com/v1` and
`https://api.deepseek.com`. `AGENT_MODEL_BASE_URL` must equal the official OpenAI URL;
it does not override the selected provider. Keys cannot be redirected by browser endpoint fields.
The matching hostname is displayed before key entry.

Install with `uv sync --extra dev`. Configure a named Modal secret `toy-agent-web-byok-runtime`,
then deploy using `modal deploy deploy/modal_agent.py`.
The Modal CLI is separate deployment tooling. `deploy/configure_runtime.py` accepts exactly the four
Python infrastructure values as JSON on hidden stdin. Invoking it transmits secrets and requires
separate authorization. It never reads `.env` or copies owner model/search keys; extra fields and
env file paths are rejected. Do not invoke it or deploy this local mock configuration without
authorization. Public startup requires no owner OpenAI/search key and ignores deliberately set ones.
The API uses a single backend container and SQLite on the `toy-agent-web-data` Volume. Do not
increase container replicas: the current storage and queue implementation requires one writer.
Store updates checkpoint every 15 seconds and at clean shutdown; abrupt infrastructure failure
can lose recent updates, including a deletion. Revoke with the provider when urgent.
Unfinished runs become interrupted after restart. Deployment should occur
while no runs are active. This first release is suitable for low-volume use, not high-availability
multi-replica service.

## Account memory and forgetting

Logged-in accounts default to memory enabled without an opt-in popup. The input area exposes
status, temporary mode and “我的记忆”; users can edit, delete, clear, pause and restore ordinary
profile/project/decision records without a model Key. Anonymous requests have no memory access.
Every memory API is behind the existing trusted identity/service-token and same-origin proxy.
Manual revisions are in JSON; stale writes return 409 without silently replacing a draft.

Only newly submitted current-user text can be extracted. No old-chat backfill, attachments,
web/tool/assistant statements or inferred sensitive dossiers. Exact source spans, a closed facet
taxonomy and deterministic safety gates check model proposals. These conservative gates can
skip valid facts; fake tests do not prove perfect semantic provenance or personalization quality.
Each profile facet currently has one canonical self record; it is not a complete biographical
knowledge graph or guaranteed additive skill-history merger. User-edited values take priority.

Same-account/same-provider records share across conversations. Cross-provider access is off by
default; an explicit per-entry allowed-provider setting changes it. Pausing stops both reads and
writes, not storage; resuming never fills its gap. Temporary requests disable memory reads/writes
only. Ordinary conversation history remains saved and may still contain the original self-report.
Deleting memory does not erase a conversation or retract already-sent provider requests.

Memory bodies, source fragments and readable semantic keys use independent memory-domain
AES-GCM AAD (owner/id/version). The infrastructure keyring format is reused, not credential AAD.
Only opaque owner-scoped HMAC keys/provider masks/times/revisions are indexed in SQLite.
Pin the historical HMAC key version per account: AES active-key rotation **must retain old hash
keys while tombstones exist**. Missing a pinned key makes memory unavailable; do not recreate
an empty index or reset tombstones to get it running. Never print the keyring during diagnostics.

The bounded layer admits 200 active entries/account and 500 characters/body. Retrieval uses
lexical/CJK-bigram overlap, recency and stable ordering, with at most 8 entries/6000 UTF-8 bytes.
Memory is untrusted user context, not system policy or research evidence. Planner/specialists may
receive it separately; verifier/linker providers remain unwrapped and task.question is not expanded
with the profile. Progress and memory reports contain safe statuses/counts/opaque IDs only.

One optional extraction attempt runs after an otherwise successful answer, using the current
visitor's selected model/client, no tools, no retries, at most 800 output tokens/8 seconds within
the original 500-second deadline. Its observed usage (including partial failure usage) is added once.
No detached paid job, replay extraction, provider switch or operator-Key fallback is permitted.

Manual changes increment a separate memory generation, close old leases and await owner task
cancellation before replying. Subsequent provider retries/tool continuations check the old lease
and cannot reuse it. A cancelled task is never automatically resubmitted. Failed mutations do not
claim success, and a failed CAS does not change content/generation (old leases may remain closed).

Online delete removes the encrypted body/source and keeps opaque forgetting barriers. It also
conservatively suppresses **all automatic additions for that category/facet**, including new aliases,
until explicit manual re-add or current “重新记住”. The management page displays suppressed fields.
This trades recall for reliable forgetting; it is not perfect semantic alias recognition. Clear
records a durable source sequence watermark. Neither clear nor pause deletes ordinary chat history.

Physical WAL/process-memory/backup erasure is not guaranteed. A backup restore must preserve
forgetting barriers and settings; actual Modal/Sites retention and restore behavior remain release
gates. Synthetic SQLite/WAL scans only establish that this memory layer does not write its plaintext
payloads/indexes; they do not claim existing chat-question columns are encrypted.

Credential-free memory acceptance planning:
`uv run python scripts/interactive_smoke.py --provider openai --model gpt-6.1-sol --memory-plan`.
It never reads credentials or starts live calls, and rejects combination with `--live`.
Do not reset the previously exhausted six-task ledger. Browser QA fixtures in `tests/manual_memory_*`
are localhost-only, disposable, use a synthetic identity/fake executor, and must never be deployed.
See [the full verification report](superpowers/reports/2026-10-18-account-memory-verification.md).

## Limits and privacy

One active run per user, no hourly task-submission cap, two executing runs globally, ten queued runs.
Each run has 500 seconds and 24 logical tool calls. One supported attachment per question,
up to 32 MiB; three successful uploads per user per hour. Upload records expire after seven days
and are removed during service startup. Questions, results and uploads are private to their owner.
Temporary run workspaces are removed after execution. Conversations persist on the backend.

These limits bound work, not a dollar spending ceiling. Configure provider and hosting budgets
separately. Model/tool charges go to the visitor's API account; the owner still pays backend,
storage and hosting. Membership subscriptions and API balances are separate concepts, whose terms
depend on the provider. Accepting key syntax never proves model access, balance or billing terms.

## Conversational routing

Text-only requests first use the selected visitor-owned model to choose
direct response, research, or clarification using bounded conversation history. Translation,
rewriting and stable general explanations can be answered without the GAIA research graph.
Direct responses are not evidence-verified research and must not be advertised as such.
Source requests, external/current facts and tool-verified computations use the strict research
graph; attachments conservatively always use that graph. Invalid, incomplete, contradictory or
uncertain routing falls back to research, not an unverified answer. Transport/authentication
failures instead stop with a safe failure; they do not trigger another paid research run.
Routing is a model judgment, not a guarantee of perfect intent detection or factual accuracy.
Its usage is included in the returned token totals, and normal model charges apply even for chat.
Progress/error storage contains only allowlisted semantic events, never classifier reasoning.
Validation rejection exposes only a safe category (insufficient evidence or revision still needed),
not raw critic feedback. Historical errors cannot acquire explanations retroactively.

## Consent, private credentials and deletion

Save consent is unchecked by default. Keys go to this backend and the displayed model provider;
optional search keys go to their respective Serper/SerpAPI APIs. The operator/hosting infrastructure
can access runtime plaintext: this is not end-to-end encryption or a claim the operator cannot read
keys. Independently assess operator, hosting and provider trust before entering any real key.

Unchecked consent creates a private, owner-bound in-memory session for 30 minutes. One session per
owner, 100 globally. Refresh or restart can require re-entry. Queued runs recheck expiry/generation
at dispatch; active runs retain their private snapshot only through completion/cancellation or the
500-second deadline. There is no browser key/handle persistence or process environment mutation.
Each parent/visual/audio/hosted-tool client and MCP child uses only its user's credentials.
Missing user search keys disable independent search providers. OpenAI hosted search uses its model
Key. DeepSeek runs portable tools with no model Key or OpenAI client in the child: registered
file reads, bounded public webpage reads, optional independent search and answer submission.
DeepSeek cannot run hosted Python or audio transcription; Flash admits native image input,
while V4 Pro image input is rejected. Empty/scanned PDFs require OCR before use. Unsupported
capabilities fail explicitly, without another provider fallback. DeepSeek reasoning parameters
are omitted until an exact supported mapping is verified; the UI says provider default, not low.

## Model selection and onboarding

The authenticated public run body requires both `provider` and `model`; only the server catalog's
exact pairs are admitted. Catalog GET is the only anonymous proxy exception and contains no keys.
Conversations are provider-bound. Switching providers starts a fresh conversation and retains only
unsent input; old histories remain readable, with null provider/model metadata for legacy rows.
Every new completed answer keeps its actual provider/model, independently of the current picker.

Missing login opens a top-level native sign-in link. Optional tab-local drafts contain only the
question, selection, timestamp and owner hint, expire after 15 minutes, and are consumed once.
No Key, handle, attachment or conversation ID is stored there or in the sign-in URL. Missing or
expired credentials guide to the selected provider settings. Saving, migrating, signing in or
returning never automatically sends a task. Real hosted authentication remains unverified locally.

New credentials are stored separately as `openai` or `deepseek`. Historical `model` credentials
are not auto-renamed or used as an implicit fallback. Explicit OpenAI migration requires consent
and the known official hostname, re-encrypts owner/provider AAD atomically, and preserves the old
row on conflict/failure. DeepSeek test credentials supplied through hidden stdin stay temporary.

Saving uses AES-256-GCM, fresh 12-byte nonce and owner/provider/version-bound associated data.
SQLite holds ciphertext, nonce, version, last four characters and update/consent timestamps only.
There is no plaintext read/export endpoint or credential backup/export feature.
Replacing/deleting revokes queued references and awaits cancellation of that user's tasks before
returning. Online ciphertext and temporary references are removed; already submitted requests
cannot be recalled and may still cost money. For urgent revocation, revoke at the provider.
Deletion is not guaranteed physical erasure of Python memory, SQLite free pages, WAL or hosting
snapshots. A restart/restore must not silently restore usable deleted credentials; actual platform
snapshot behavior and retention are unverified release gates, not a fabricated fixed promise.

## Master-key rotation

Stop new admissions and wait for active runs to finish. Add a new random 32-byte version to the
platform Secret, retaining the old versions; switch active version and restart. In an administrator-
only maintenance session inside the trusted backend environment, construct the vault with that
keyring and call `rotate_to_active(owner, provider)` for each row. Inspect version counts only;
all rows must be active before removing old versions. Verify decryption/restart, then retire old
versions and restart. Never export plaintext or put key literals in shell arguments. Lost master
keys require affected users to re-enter keys. Historical snapshots may still need older versions:
decide recovery/retention policy explicitly rather than promising immediate total erasure.

## Verification

Run `uv run pytest -o addopts='' -q`, `uv run ruff check src tests deploy`, frontend Node tests,
TypeScript noEmit and production build. Local fake-key UI checks are not real-provider verification.
Before public release, ALL of the following must be independently verified:

- [ ] Independent BYOK security review (no paid client or child inherits owner credentials).
- [ ] Native real sign-in, HTTPS and proxy; anonymous/cross-user requests denied.
- [ ] Operator-approved provider endpoint/trust and model access with a consenting tester.
- [ ] Separate authorization for infrastructure Secret transfer and cloud deployment.
- [ ] Separately authorized paid smoke test; cancellation, expiry, retry and recovery checked.
- [ ] Real platform body/header/error/trace collection audited to exclude credentials.
- [ ] Actual Modal/Sites snapshot retention configured and verified; publish its actual duration
      in privacy copy. Duration is currently UNVERIFIED: public release remains blocked.
- [ ] Real deletion/restart/restore behavior and residual exposure accurately documented.

No cloud deployment or real hosted sign-in checks have been performed. Local provider smoke results
are recorded separately; they do not establish snapshot deletion, platform logging or public readiness.

For credential-free acceptance planning: `uv run python scripts/interactive_smoke.py --provider
deepseek --model deepseek-flash`. Live mode additionally requires explicit approval, a six-task
ledger, the server-only service token in the process environment, and hidden TTY input for DeepSeek.
Never paste a Key into argv, shell history or a tracked file. Reusing a completed ledger only reads
its non-secret summaries; uncertain accepted submissions are recovered by GET, not resubmitted.
## Asynchronous account memory

Run the backend's lifespan so its two bounded memory consumers start and shut down cleanly. Account-level serialization and dispatch journaling are SQLite-backed; keep the database on durable storage, including its WAL during live operation. Use an offline backup of data plus encryption/authentication secrets before migration. Do not copy a live SQLite file alone.

Model maintenance is separately accounted and uses the user's frozen provider/model selection and credential epoch. It is not an operator-funded fallback. One job makes one bounded call; uncertain or failed calls need explicit, cost-confirmed retry. A deleted/rotated Key or disabled memory fences pending work. Do not run automatic chat backfill on upgrade.

The semantic-effect results and deployment scope are documented in [the verification report](superpowers/reports/2026-10-21-semantic-memory-verification.md). Passing a finite synthetic corpus is not a guarantee of perfect semantic forgetting or model availability.
