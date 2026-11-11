# ToyAgent workspace UI integration

Date: 2026-10-19. Local application integration in the existing managed worktree.
No push, merge or deployment. Existing unrelated/uncommitted memory fixes were preserved.

## Delivered

- ToyAgent branding, page title and original monogram favicon.
- Warm dark chat workspace based on the approved prototype: compact sidebar,
  centered welcome, integrated composer, lightweight prompt shortcuts and mobile drawer.
- Chinese/English interface preference, browser persistence and cross-tab updates.
  UI translation is explicit at render sites, not a DOM-wide translation pass.
  User questions, model answers, filenames, identifiers and memory bodies are not translated.
- Minimal settings hub: language, provider keys and memory. Safety/consent details remain
  inside the actual key/memory management flows; the hub does not repeat them.
- Animated language menu and searchable, provider-filtered model dialog with keyboard
  handling, focus restoration, reduced-motion handling and viewport bounds.
- Real server model catalog only. No demo reasoning levels carried into the application.
  Existing backend defaults remain: OpenAI low and DeepSeek provider default.
  Expanding the model catalog or enabling per-request reasoning overrides is not part
  of this UI integration; the reasoning section reports the actual server configuration.
- Existing auth, BYOK consent, owner/provider isolation, memory management, idempotency,
  task polling/cancellation and safe failure diagnostics retained.
- Per-answer memory update reports remain hidden.

## Verification evidence

| Check | Result |
| --- | --- |
| Frontend baseline Node suite | 33 passed |
| New locale/filter tests before implementation | 2 expected assertion failures |
| Final full Node suite | 35 passed, 0 failed |
| TypeScript `tsc --noEmit --incremental false` | Exit 0 |
| Final production build | All 5 stages passed |
| Full Python regression | 886 passed in 51.16s |
| Ruff `check --no-cache src tests deploy scripts` | Passed |
| `git diff --check` | Passed |

Node commands use the bundled Node runtime in `sites/agent-web`. Python uses the existing
environment with `UV_CACHE_DIR=/tmp/uv-cache .../uv run --no-sync` and
`pytest -o addopts='' -q -p no:cacheprovider`.

## Browser checks

Used CUA against the real local application on port 3000 and a separate production-build
fixture on ports 8001/3001/3002. Fixture identity, execution and data are synthetic, with a
process-owned temporary database and no real model clients/keys.

Verified on the real application: new layout, actual catalog/search, provider switching
preserving an unsent synthetic draft, English key/memory management, Chinese restoration,
new page title, and existing history loading. No question was submitted to the real executor,
and no real memories or keys were created, edited or deleted. The test draft was removed and
the original OpenAI selection restored. Final real-page screenshot:
`/private/tmp/toyagent-live-integrated.png`.

Verified in the disposable fixture:

- Manual memory creation; a body exactly equal to the UI label `新建对话` stayed unchanged
  when the interface switched to English.
- Synthetic task submission → queued/polled → completed response, using one authorized
  memory. No model request was sent. User text stayed unchanged across interface switches.
- No per-answer memory report. The fake answer is not evidence of real-model quality.
- Anonymous draft → login guidance → return to editing retained the draft and did not
  automatically submit or navigate through a real login flow.
- Language-menu switching and Escape collapse retained the settings dialog and focus.
- 320px production-build viewport: document scroll width 320; model search visible,
  search results correct, and dialog within viewport. Search Enter did not submit the
  nonempty draft or open login. Screenshot: `/private/tmp/toyagent-live-mobile-model.png`.

Found and fixed an actual mobile regression before handoff: the old mobile rule
`.model-selector label { display:none }` also hid the new search label. Added a specific
search-container display rule and verified the rebuilt artifact, not merely desktop CSS.

The preview CLI ignored `--host`; its first wildcard-bound synthetic preview was immediately
stopped and restarted with the supported `--hostname 127.0.0.1` option before fixture use.
All disposable QA services and agent-created QA tabs were stopped/closed after verification.
Original application services and user tabs were preserved; viewport override reset.

## Limits

This is local UI delivery, not public production certification. No new paid provider calls,
real hosted sign-in, cloud deployment, backup/log audit, load testing or independent security
review occurred. Review was inline, consistent with the existing execution arrangement.
Missing-key recovery has existing unit coverage; no real saved key was removed to exercise it.
Mobile verification is browser viewport testing, not physical-device keyboard certification.
