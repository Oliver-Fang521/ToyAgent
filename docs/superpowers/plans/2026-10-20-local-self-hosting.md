# ToyAgent Local Self-Hosting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable a fresh clone to run a persistent, single-user ToyAgent locally through Docker Compose without Sites authentication.

**Architecture:** Explicit local authentication supplements, never replaces implicitly, the existing Sites mode. An interactive init container creates separate least-privilege frontend/backend secrets; Compose starts the production Node frontend and one Python backend with persistent storage.

**Tech Stack:** Python 3.12, Node 22, existing Vinext/React and FastAPI, Node crypto scrypt/HMAC, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-20-local-self-hosting-design.md`

## Global Constraints

- 单用户、单后端实例；只映射 `127.0.0.1:3000`，backend 不发布宿主端口。
- 默认 Sites；只有服务端显式 `AGENT_AUTH_MODE=local` 启用本地认证。
- Cookie: HttpOnly, SameSite=Strict, Path=/, host-only, 12 小时固定过期；本机 HTTP 不设 Secure，不支持公网发布。
- 密码不经命令行、环境变量或日志；秘密不进入镜像、Git、浏览器或构建上下文。
- 前端不获数据加密 keyring，后端不获访问密码摘要/会话签名密钥；运行时秘密只读挂载。
- owner 与加密主密钥在重启/密码重置时不变；旧会话在密码重置后全部失效。
- 保留现有 Key/记忆/代理接口与 Sites 行为；不修改真实用户数据、不做真实付费调用、不 push/merge/deploy。

## Review Focus

1. Partial initialization or repeated init must not silently replace decryption keys (Task 1).
2. Forged Sites headers/local cookies cannot cross authentication modes; hostile Host/Origin cannot unlock localhost (Tasks 2–3).
3. Docker build context/volume layout must not expose operator credentials or frontend access to the keyring (Task 5).
4. Password reset and backup restore preserve owner, encrypted records and forgetting barriers while invalidating old sessions (Tasks 4, 6).
5. Clean-clone commands, non-root permissions and restart readiness must work in actual containers, not only mocks (Tasks 5–6).

## Task 1: Versioned initialization and secret separation

**Files:** Create `deploy/local_runtime.py`, `tests/test_local_runtime.py`.

**Interfaces:** `initialize(root: Path, password: str) -> None`, `reset_password(root: Path, password: str) -> None`; frontend JSON v1 contains owner_id, password `{algorithm,salt,hash,n,r,p}`, session_key and service_token; backend JSON v1 contains service_token, credential_keyring and active_version. CLI `python deploy/local_runtime.py init|reset-password --root PATH` uses getpass twice, requires TTY. Store under `root/frontend/runtime.json`, `root/backend/runtime.json`; a root completion marker gates service startup. Never return secrets to stdout.

- [ ] Add failing tests: two random initialized roots differ; matching service tokens, 32-byte keys, stable opaque owner; no keyring in frontend/no password in backend; existing or partial root refused without changing bytes; non-TTY/unequal password refusal. Password policy minimum 12 characters, maximum 1024 UTF-8 bytes. Pin scrypt N=16384/r=8/p=1, 16-byte salt, 32-byte derived key for Python/Node interoperability.
- [ ] Run `uv run --no-sync pytest -o addopts='' -q tests/test_local_runtime.py`; observe RED.
- [ ] Implement validated versioned schema, atomic per-file writes and completion marker last; directory permissions and ownership compatible with fixed runtime UID/GID 10001. Existing incomplete state requires explicit operator recovery, never automatic regeneration.
- [ ] Run same tests GREEN; inspect generated synthetic files only through assertions, never print their contents.
- [ ] Commit explicit task files as `feat: initialize isolated local deployment secrets`.

## Task 2: Local authentication core and request policy

**Files:** Create `sites/agent-web/app/lib/local-auth.ts`, `sites/agent-web/tests/local-auth.test.ts`.

**Interfaces:** `LocalAuthConfig` mirrors Task 1 frontend v1; `verifyPassword(password: string, config: LocalAuthConfig): Promise<boolean>`; `issueSession(config, nowSeconds): string`; `verifySession(value, config, nowSeconds): {userId:string}|null`; `validateLocalRequest(request: Request, configuredOrigin: string): boolean`; bounded `LoginLimiter` with `allow(nowMs)`/`failed(nowMs)`/`clear()`. Configuration loaded server-side from explicit mounted path, validated and cached only in process.

- [ ] Add RED Node tests using Python-compatible known synthetic scrypt vector; correct/incorrect/oversize password, constant-length digest comparison, tampered/truncated/expired/future tokens, invalid parameters/version. Assert 12-hour expiry and random nonce, no password/service token in token payload. Accept only configured loopback origin; reject missing/foreign/null Origin, hostile Host, forwarded headers as authority. Limiter: five failures in 15 minutes, sixth denied with bounded Retry-After, bounded state independent of attacker IP headers.
- [ ] Run `node --experimental-strip-types --test tests/local-auth.test.ts`; observe RED.
- [ ] Implement using Node crypto async scrypt and HMAC-SHA256/timingSafeEqual; versioned payload, explicit owner match, canonical encoding, size caps. Pure dependency-injected core stays importable by Node tests; no next/headers dependency here.
- [ ] Run same tests GREEN plus TypeScript noEmit.
- [ ] Commit explicit files as `feat: add bounded local password and session validation`.

## Task 3: Identity adapter and login/logout UI

**Files:** Create `app/auth.ts`, `app/local-login/page.tsx`, `app/api/local-auth/login/route.ts`, `app/api/local-auth/logout/route.ts`, `tests/local-auth-routes.test.ts` under `sites/agent-web`; Modify `app/page.tsx`, `app/api/agent/[...path]/route.ts`, `app/components/AgentChat.tsx`, `app/lib/ui-language.ts`. Add narrowly scoped styles to `app/workspace.css`.

**Interfaces:** `getAppUser(): Promise<ChatGPTUser|null>`, `appSignInPath(returnTo: string): string`, `appSignOutPath(): string`, `getAuthMode(): 'sites'|'local'`. Existing getChatGPTUser remains Sites-only. Use cookie `toyagent_local_session`. Login POST form consumes at most 4096 bytes; returns safe same-site 303 on success. Local logout POST deletes cookie; Sites retains existing signout link.

- [ ] Add failing route/adapter tests: local ignores forged oai headers, Sites ignores valid local cookie, unknown mode fails closed; missing/invalid config 503; anonymous agent requests remain 401. Correct login sets specified cookie flags and no-store, wrong login generic failure, rate limit 429/Retry-After. Reject cross-origin login/logout, unsafe return_to, GET logout, multipart/oversize body.
- [ ] Run new tests plus `tests/proxy.test.ts` (verify actual existing test filename before invocation); observe RED for new behavior.
- [ ] Implement identity boundary and local routes, maintaining server-only secret access. Login page bilingual password form with accessible labels, generic failures, no password echo; local UI labels never claim ChatGPT login. Logout form POST clears frontend credential handles/draft through existing cleanup before navigation. Do not auto-submit questions after login.
- [ ] Run all frontend tests and TypeScript; browser-test keyboard submit, failed login, correct login, logout and draft recovery using synthetic config later in Task 6.
- [ ] Commit explicit task files as `feat: support standalone local login without Sites`.

## Task 4: Runtime loaders and password maintenance

**Files:** Create `deploy/local_backend.py`, `deploy/local_frontend.mjs`, `tests/test_local_entrypoints.py`; Modify `deploy/local_runtime.py`, `tests/test_local_runtime.py`.

**Interfaces:** Backend entrypoint validates completed mounted config and exports only allowed infrastructure env to uvicorn factory; frontend launcher validates its own config and starts built Vinext on `0.0.0.0:3000`, exposes local config path to server code, never serializes config into public settings. Both handle shutdown signals. Password reset uses Task 1 API under stopped services.

- [ ] Add failing tests for missing marker/wrong version/incomplete fields, no shell sourcing, no secret prints, exact exported field allowlists. Reset tests preserve owner/service token/keyring, change password hash and session key, reject invalid input before writes. Exercise old token rejection against new session key.
- [ ] Run targeted Python/Node tests RED.
- [ ] Implement strict loaders, bounded reads, exec-style process startup and atomic password reset. Never read `.env` or author runtime.env; production app starts only with persisted validated keys.
- [ ] Run targeted tests GREEN and existing credential-runtime tests.
- [ ] Commit as `feat: load persistent local runtime and reset access password safely`.

## Task 5: Compose and production images

**Files:** Create root `compose.yaml`, `.dockerignore`, `.env.local.example`, `deploy/Dockerfile.backend`, `deploy/Dockerfile.frontend`, `tests/test_local_compose.py`; Modify `.gitignore` for self-host override files only if needed.

**Interfaces:** Compose services init/frontend/backend; named volumes `local-secrets` and `local-data`. Each app mounts only corresponding secrets subdirectory read-only plus readiness marker; init owns full secrets volume. Backend gets only data writable volume. Frontend maps configured loopback port default 3000, backend internal port 8000. Healthcheck backend `/health`; frontend readiness must verify startup, not require login.

- [ ] Write failing config tests for loopback-only mapping, no backend host mapping, single backend, read-only scoped mounts, no default passwords/model Keys, no Docker socket or privileged runtime. Build-context ignore assertions cover `.env*` except safe templates, `*.private`, SQLite, uploads, `.git`, node_modules, caches and tests/private artifacts while retaining lockfiles and required source.
- [ ] Run targeted tests RED, implement Compose and multi-stage lockfile builds with fixed UID/GID 10001. Use init profile so ordinary `up` never starts an interactive password prompt; init command explicitly selects the profile. Compose commands in docs must match this decision.
- [ ] Run tests GREEN and `docker compose --env-file .env.local.example config --quiet`; build both images with no host runtime files included. Startup fails closed before initialization, but build does not require secrets.
- [ ] Commit as `build: package standalone ToyAgent with Docker Compose`.

## Task 6: Fresh-volume acceptance, recovery and documentation

**Files:** Create `docs/local-self-hosting.md`, `scripts/local_self_host_smoke.py`, `docs/superpowers/reports/2026-10-20-local-self-hosting-verification.md`; Modify `README.md`, `docs/web-deployment.md`.

**Interfaces:** Smoke script requires explicit disposable Compose project name, never uses default user's volumes, does not read real Keys. Test setup injects synthetic initialization password via test-only subprocess input, not CLI arguments/logs; normal init remains TTY-only. Keep all fake execution wiring in test fixture, not production default.

- [ ] Write failing smoke assertions for uninitialized start, unauthorized API, forged header/cookie, successful login, settings/no-Key guidance, restart persistence and reset invalidation. Run RED against isolated test project.
- [ ] Build/start actual containers using unique test project and temporary generated inputs. Browser-check bilingual login and signed-in model/memory UI; create synthetic manual memory, pause/delete and check forgetting barrier persists after restart. Never invoke paid models. Validate frontend cannot read backend keyring, app processes non-root, backend host port absent, health and clean shutdown.
- [ ] Stop test services; backup secrets and data using an isolated one-off container with no printed content, restore into separate empty disposable volumes, start and assert same owner/records/settings/barriers and new-session behavior. Explicitly name test volumes before cleanup; never use unscoped `down -v` against user setup.
- [ ] Document fresh-clone commands, prerequisites, expected URL, init profile, logs `docker compose logs -f frontend backend`, restart/down, upgrade backup-first, stopped-service reset, consistent backup/restore and lost-key warning. Note single-user/loopback scope, API charges, supported tool limitations and no high-availability claim. Link from README; distinguish local Docker from Sites/Modal instructions.
- [ ] Run full Python suite/Ruff, all Node tests/TypeScript/production build, Compose smoke and `git diff --check`; record actual evidence and any blocked checks. Retain existing unrelated dirty changes unstaged. Commit explicit test/docs as `test: verify clean local startup and recovery`.

## Self-review and handoff

Spec coverage: architecture/commands/images → Task 5; authentication/CSRF/mode isolation → Tasks 2–3; secret creation, separation and password reset → Tasks 1/4; data, backup and upgrades → Tasks 5–6; all five Review Focus conditions have owning tests above. No hosted deployment, real Key use or live paid acceptance is implied.

Dependencies: 1 defines persisted schemas; 2 consumes frontend schema; 3 consumes auth core; 4 consumes persisted schema; 5 packages entrypoints; 6 exercises all interfaces. Preserve in-session native implementation method selected earlier. User must review this written plan before implementation.
