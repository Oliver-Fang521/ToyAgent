# Local self-hosting verification — 2026-10-20

Scope: approved single-user loopback Docker Compose design. Not a public deployment, multi-user security certification or paid-provider acceptance.

## Implemented

- Interactive local password initialization, stable opaque owner and separate frontend/backend secret files, persistent data and encryption keyring.
- Explicit local/Sites identity modes, scrypt verification, bounded login attempts, signed 12-hour HttpOnly/SameSite=Strict cookies, exact Host/Origin and safe redirect validation, POST logout and offline password reset.
- Node 22 and Python 3.12 non-root images, loopback-only frontend port, no published backend port, read-only scoped secret mounts and healthchecks.
- Offline private backup/empty-volume restore, setup template and complete startup/log/upgrade/reset/recovery documentation.

## Observed verification

- Baseline: 912 Python tests passed before changes.
- Final post-review suite: `uv run --no-sync pytest -o addopts='' -q -p no:cacheprovider`: **931 passed in 57.75s**. Ruff passed across src/tests/deploy/scripts.
- Actual final Node 22 frontend image: **49 tests passed**, TypeScript noEmit passed. Docker frontend build completed all five build phases; backend/init images built with frozen uv lockfile.
- `docker compose --profile tools --env-file .env.local.example config --format json` confirmed loopback-only mapping, private backend, scoped readonly secrets and UID/GID 10001.
- Fresh uninitialized project refused startup at missing secret subpath; init never silently generated ephemeral secrets. Test-only direct initializer used synthetic password over stdin; public CLI TTY/mismatch checks covered by Python tests.
- Actual isolated `toyagent-qa-20261020` at port 3020: anonymous and forged Sites headers denied; correct local login; memory creation, restart persistence, deletion barrier, logout, password reset and old-session invalidation passed. No paid calls.
- Offline backup after stopping services restored to a second empty project `toyagent-qa-restored-20261020`, port 3021: login and forgetting barriers preserved. Backup helper unit test also checks exact synthetic bytes and rejects nonempty restore targets.
- Browser: wrong password displays safe error; correct password plus Enter opens local workspace; memory dialog opens; POST logout returns to login; language menu switches login to English. Screenshot: `/private/tmp/toyagent-local-login-2026-10-20.png`.
- Actual image checks: both app users non-root; frontend cannot access backend secret directory; author private runtime and tests absent from backend image. Original user services at 3000/8000 not stopped or reconfigured.
- Final image restored the offline archive in a disposable container; SQLite integrity check passed and forgetting barriers were present. Synthetic password/full-Key log scan passed.
- Smoke helper initially enabled the tools profile for ordinary up; a real Compose-config regression reproduced it, then the helper was fixed and the actual cycle rerun without starting interactive init.

## Debugging and decisions

1. Per-service completion markers accompany the root maintenance marker so each scoped mount can validate initialization without exposing sibling secrets.
2. HTTP auth logic is extracted to a pure Request/Response module for direct behavioral tests; Next-specific adapter remains thin, mode wiring additionally exercised by real containers.
3. First Docker context included local dependencies because of directory exceptions; explicit final exclusions now omit node_modules, generated outputs and runtime caches. A new build completed with the corrected context.
4. Recovery uses a small tested Python helper rather than shell tar extraction: refuses nonempty targets and unsafe archive paths. Only trusted operator backups are supported.
5. Actual Python 3.12 startup revealed `ConversationStore.list` shadowing builtin annotations. Future annotations fixed import compatibility; failed import was observed before correction, then the actual image imported the API and became healthy.

## Limits

Only local HTTP on the configured localhost port is supported. Cookies are host-scoped, not port-scoped; separate instances should use different generated signing keys. Restoring a backup also restores its access password/session signing key; reset before startup when old sessions must be revoked. Rate limiting is in-memory and resets with frontend restart. Backups contain sensitive decryption material; volume deletion or losing keys can make data unrecoverable.

No real Key, model generation or search request was used. Container testing was performed on this host's Linux Docker architecture, not every OS/CPU combination. Public cloud authentication/HTTPS/retention remain separate release gates. Latest implementation is local only; no push/merge/deploy performed.

## Independent review and single repair pass

Fresh-context reviewer found no Critical issues, two Important issues and no Minor findings. Both were independently reproduced, then reproduced again by new regressions before fixes:

- Concurrent login could pass the limiter before awaiting request body, then all enter password hashing. RED: 20 simultaneous wrong passwords returned 20x401. GREEN: 5x401 and 15x429; recheck/reserve is synchronous after body read. This same bound was subsequently confirmed over actual HTTP against the final container.
- Interrupted backup restore could expose completion markers before all business data was restored. Fault-injection test observed active markers after data-copy failure, then passed after markers were withheld until extraction finished. Failed restore now leaves backend startup blocked.

The complete suites and image builds above were rerun after both fixes; no second reviewer substituted for these tests. No finding is deferred.

Final full synthetic cycle passed against the rebuilt containers, including synthetic saved-Key status after restart. One overlapping concurrency probe intentionally exhausted the limiter during another cycle, causing its later login to return 429; after restarting the disposable frontend, the complete cycle was rerun serially and passed. This was test interference, not bypassed by changing assertions or limits.

Cleanup targets are only the two explicitly named QA Compose projects, their four synthetic volumes and the generated temporary backup; original local services/user data remain untouched. Docker build caches and source checkout are retained.

Review limitations/rulings: restore intentionally preserves backup signing keys; explicit offline password reset is required to revoke old sessions, as documented. The reviewer could not access Docker in its sandbox, so actual container evidence is implementer-provided rather than independently rerun. Paid-provider behavior, public hosting/multi-user certification and other OS/CPU combinations remain unverified and outside this local scope.
