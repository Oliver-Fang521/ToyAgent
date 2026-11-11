# ToyAgent Memory, Models and Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付简洁的记忆管理、真实模型/推理选择和使用账户自有 Key 的 DeepSeek 搜索工具。

**Architecture:** 保留现有 LangGraph、账户隔离、加密记忆及执行预算。官方能力注册表与账户发现分离；提交时冻结运行选择；搜索/抓取走有界工具适配器。UI 使用同一可访问菜单和现有双语上下文。

**Tech Stack:** Python/Pydantic/FastAPI/SQLite/httpx、现有 Responses provider/MCP、React/TypeScript/vinext、pytest/Node tests。

**Spec:** `docs/superpowers/specs/2026-10-19-memory-models-search-design.md`

## Global Constraints

- ToyAgent 品牌和已确认的暖深灰聊天布局。
- 不自动部署、合并 PR 或使用站长 Key。
- 账户发现缓存按 owner/provider/credential generation 隔离，TTL 15 分钟。
- GPT-6.1 Sol 默认保持 low；合法档位 low/medium/high/xhigh/max。
- DeepSeek Responses 规范档位 none/low/high/max；none 关闭推理。
- 500 秒总时限、工具预算和撤销屏障保留。
- 搜索 query 为 1–128 个 Unicode 字符，默认返回 5 条结果，count 为 1–50。
- Search 固定 `https://api.deepseek.com/v1/tools/search`；Fetch 固定 `/v1/tools/fetch`。
- 记忆快照同锁/事务读取 generation 与最多 200 条 MemoryView。
- 打开约 160–180ms、4–6px 位移；关闭约 120ms；主要触控区域至少 44px。
- 窄屏验收 390px 和 320px，记录真实 innerWidth/scrollWidth。
- 中英切换只改界面，不改正文、文件名、模型 ID、工具参数或回答语言。
- 不改用户真实记忆/Key；真实烟测另行约定次数和费用上限。
- 不新增每条回答的记忆更新提示，不自动跨服务商传历史或借搜索 Key。

## Review Focus

1. 账户发现返回前 Key 被轮换：丢弃旧 generation 结果；Task 2 `test_discovery_rotation_during_refresh`。
2. 无推理模型、旧客户端、旧历史：分别省略参数、解析默认、显示未知；Task 3 `test_reasoning_omission_and_legacy_history`。
3. 搜索含 astral Unicode、secret 或私网 URL：按字符计数且发送前拒绝；Task 4 `test_unicode_secret_and_private_url_guards`。
4. 嵌套菜单 Esc、隐藏菜单焦点、搜索 Enter：不关错弹窗或提交聊天；Task 7 `test_nested_menu_keyboard_contract` 与 Task 10 浏览器步骤。
5. 暂停/并发编辑/撤销期间的旧快照：保留 409 草稿、不恢复已删除内容、不展示其他账户；Task 8 `test_memory_editor_conflict_and_owner_reset`。

## Execution and file map

沿用当前任务内逐步实施方式，不重新分配子代理。三个交付单元分别是模型契约（1–3）、DeepSeek 工具（4–5）、记忆与交互（6–9），最后统一验收（10）；可分别审查，不进行全框架重写。

工作目录固定为 `/Users/HPOMEN/.codex/worktrees/public-agent-web/agent`。现有脏文件是先前集成成果，不回滚、不整仓暂存。执行前重新核对 diff；每次提交只包含本任务明确改动，重叠文件先审查既有变更来源。

- `interactive/catalog.py`：官方 profile 与验证；新增 `model_discovery.py`：账户缓存/发现。
- `interactive/models.py, service.py, store.py, credential_runtime.py`：冻结选择、存储、调用。
- `providers/deepseek_responses.py, providers/openai.py`：按 profile 发送真实 SDK 参数。
- 新增 `deepseek_tools.py`：独立 HTTP Search/Fetch；`search.py`：搜索链归一化。
- `mcp_server.py, mcp_client.py, interactive/tool_policy.py`：工具装配、预算和安全事件。
- `interactive/memory/models.py, repository.py, api.py`：一致列表快照；保留现有写入服务。
- 新增 `AnimatedSelect.tsx, MemoryEditor.tsx`：菜单行为与记忆编辑；既有组件负责组合。
- `app/lib/models.ts, memory.ts, history.ts, onboarding.ts, ui-language.ts`：前端契约与 UI 文案。
- 新增 `app/lib/select-state.ts, memory-editor.ts`：纯状态逻辑，供 Node 单测。
- `app/api/agent/[...path]/route.ts`：只扩展受信代理路由白名单，不透传凭据。
- 每项 Task 的 Test 行指定对应测试文件；新增测试不接真实外网。

## Commands

后文 `PYTEST` 指 `UV_CACHE_DIR=/tmp/uv-cache /Users/HPOMEN/.local/bin/uv run --no-sync pytest -o addopts='' -q -p no:cacheprovider`（仓库根运行）。`NODE` 指 `/Users/HPOMEN/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`（前端目录运行）。RED 是未来实施步骤，本文不声称已经执行这些新测试。

### Task 1: Official capability registry and coverage

**Files:** Modify `src/gaia_agent/interactive/catalog.py`; Test `tests/test_model_catalog.py`; Create `docs/superpowers/reports/2026-10-19-model-coverage.md`。

**Interfaces:** `ModelProfile` 增加 `protocol: str, family: str, legacy: bool, evidence_url: str, capability_version: str, adapter_status: Literal['supported','unsupported'], disabled_reason: str | None`；`default_reasoning_effort: str | None`，无推理支持为 `allowed_reasoning_efforts=()`。保留 `resolve_profile(selection: ModelSelection) -> ModelProfile` 和 `public_catalog() -> dict`。

- [ ] 写 `test_registry_capabilities_are_per_model`：GPT-6.1 档位恰为五项、默认 low；DS 恰为四项；Flash ID 为 `deepseek-flash`；unknown ID 和 unsupported profile 不允许执行；专用模型不进入文本菜单。
- [ ] 运行 `PYTEST tests/test_model_catalog.py`，确认新断言失败而非环境错误。
- [ ] 从 spec 链接逐 ID 核对当前 6/6.1、5.6/5.5/5.4、兼容 5.x/4.1/4o/o 系列，覆盖表逐项记录官方链接、检查时间、启用/禁用原因；禁止从展示名推导 API ID。兼容性必须包含结构化输出、function tools、附件和实际协议；未知项禁用而非猜测。实施前若文档已变化更新覆盖表，不照搬本计划旧时间。
- [ ] 运行同一命令，预期全部 PASS；检查公共目录无账户字段或 Key。
- [ ] 审查后仅提交该三文件：`git add src/gaia_agent/interactive/catalog.py tests/test_model_catalog.py docs/superpowers/reports/2026-10-19-model-coverage.md`；`git commit -m "feat: define documented model capability registry"`。

### Task 2: Owner-scoped model discovery

**Files:** Create `src/gaia_agent/interactive/model_discovery.py`, `tests/test_model_discovery.py`; Modify `interactive/api.py`, `credential_sessions.py`, `sites/agent-web/app/api/agent/[...path]/route.ts`。

**Interfaces:** `ModelDiscovery(sessions, client, *, clock=time.monotonic)`；`async discover(owner: str, provider: ProviderId, handle: str | None = None) -> dict`；`invalidate(owner: str) -> None`。新增鉴权 `POST /v1/model-catalog/refresh`，body 仅 `{provider}`，临时 handle 沿用现有安全 header，不在 body/URL。返回 `{models, discovery:{status,checked_at,expires_at}}`，model availability 为 `visible|unavailable|unknown`；公共 GET 不变。

- [ ] 写 `test_discovery_rotation_during_refresh`、`test_owner_provider_cache_and_static_fallback`：不同 owner/provider 不共享；TTL 900 秒；换 Key/删除/退出立即失效；异步返回前再次校验 ref；401/网络错仅安全状态并保留静态目录；unknown 发现 ID 不自动启用。
- [ ] 运行 `PYTEST tests/test_model_discovery.py tests/test_credential_api.py`，观察新断言 RED。
- [ ] 实现固定官方 `/models` 请求、无 redirects/trust_env、仅返回审核过 ID。缓存 key 使用 owner/provider/ref.generation，缓存不持久化 Key；所有读取先校验 ref；invalidate 清除 owner 条目。服务端生命周期关闭清理；退出后的接口必须重新鉴权，不能读旧缓存。代理仅增加准确路径/方法。
- [ ] 同命令 GREEN；加入重复刷新并发合并、无 Key、禁止自定义 URL/owner 请求测试。
- [ ] 仅暂存本任务五文件并提交 `feat: discover account models with generation-scoped cache`（使用显式完整相对路径，不用目录通配符）。

### Task 3: Reasoning selection frozen end-to-end

**Files:** Modify `src/gaia_agent/interactive/{models.py,service.py,store.py,credential_runtime.py,composition.py}`, `src/gaia_agent/config.py`, `src/gaia_agent/providers/{openai.py,deepseek_responses.py}`；Test `tests/{test_model_catalog.py,test_interactive_service.py,test_interactive_api.py,test_credential_runtime.py,test_interactive_composition.py,test_deepseek_provider.py}`。

**Interfaces:** `InteractiveRequest.reasoning_effort: str | None = None`；新增不可变 `ResolvedSelection(provider,model,reasoning_effort,capability_version)` 与 `resolve_selection(request: InteractiveRequest) -> ResolvedSelection`（定义于 models.py）。`InteractiveConfig.reasoning_effort` 与 RunConfig 兼容 null。RunService 在新 run admission 时 resolve 一次；store reserve 保存解析结果并传 runtime，幂等比较解析后的规范值。

- [ ] 写 `test_reasoning_omission_and_legacy_history`：不支持推理的型号 SDK kwargs 无 reasoning；旧客户端 GPT-6.1→low；历史缺失字段→null 而不是 low。写 `test_effort_changes_idempotency`：同 idem 不同 effort→409；缺省与等效显式默认一致；非法组合拒绝。
- [ ] 运行上述六测试文件的 `PYTEST`，确认新断言 RED。
- [ ] 新增 nullable run columns `reasoning_effort, capability_version`，保留旧记录且可供旧版本读取，不改正文；执行时只读冻结选择，不读取后续 UI 值。已排队/运行状态重启保持现有 interrupted 处理，不自动重发。idempotent replay 先找已存选择，避免注册表默认值变化导致同任务重解析漂移。
- [ ] provider 边界对支持型号发送 `reasoning={effort:...}`，DeepSeek 不再剥离；无推理则整个字段省略。覆盖路由、直接回答、研究/验证、可选记忆提取；其他未知参数依旧拒绝。保留 500 秒、当前 retry/budget，不静默换模型或降档。
- [ ] 运行六文件 GREEN；mock SDK 捕获每条路径实际 model/effort，测试重启读历史、alias metadata 不变及附件能力拒绝。
- [ ] 只暂存本任务显式文件清单并提交 `feat: persist and execute model-specific reasoning selections`。

### Task 4: Bounded DeepSeek Search and Fetch HTTP adapters

**Files:** Create `src/gaia_agent/deepseek_tools.py`, `tests/test_deepseek_tools.py`; Modify `src/gaia_agent/search.py`; Test `tests/test_search.py`。

**Interfaces:** `DeepSeekSearchProvider(api_key: str, client: httpx.AsyncClient, *, timeout_seconds: float)` 实现 `provider='deepseek'`、`async search(query: str) -> dict[str,Any]`。`DeepSeekWebReader` 同构造参数、`async read(url: str) -> dict[str,Any]`。公共 URL 校验由 Task 5 注入的 reader wrapper 完成；adapter 自身再拒绝协议/凭据/明显私网，禁止独立绕开 wrapper 使用。

- [ ] 写 `test_unicode_secret_and_private_url_guards`：128 emoji 接受、129 拒绝且 transport 调用 0；已知 Key 在 query/URL 中拒绝；userinfo/loopback/link-local/private targets 拒绝。写 transport 契约：固定 host/path、Bearer、count=5，无 ori_question/IP/history，结果有 URL/title/snippet/retrieved_at。
- [ ] 运行 `PYTEST tests/test_deepseek_tools.py tests/test_search.py`，确认 RED。
- [ ] 实现 normalized SearchProvider 输出与有界 Fetch Markdown；沿用现有页面/工具输出长度上限，不额外开无限缓冲。错误码安全映射 authentication/balance/forbidden/input/rate_limit/upstream；401/402/403 不重试，408/429/5xx 交现有链有界重试。合法空结果不报错；绝不返回原始响应体/headers。
- [ ] 同命令 GREEN；增加 malformed JSON、空结果、超大结果、secret 嵌套输出脱敏、429 后成功、截止时间/取消断言。测试使用 httpx.MockTransport，无真实账单。
- [ ] `git add src/gaia_agent/deepseek_tools.py src/gaia_agent/search.py tests/test_deepseek_tools.py tests/test_search.py`；提交 `feat: add bounded DeepSeek search and fetch adapters`。

### Task 5: Wire tools through credentials, budgets and diagnostics

**Files:** Modify `src/gaia_agent/{mcp_server.py,mcp_client.py}`, `src/gaia_agent/interactive/{credential_runtime.py,tool_policy.py,failures.py,public_results.py}`；Test `tests/{test_mcp.py,test_interactive_provider_isolation.py,test_interactive_failures.py,test_credential_runtime.py}`。

**Interfaces:** 保留 `allowed_tools(profile, configured_search)`，加入 `deepseek` search capability。MCP 显式 env 新增 `DEEPSEEK_API_KEY` 及已有 search-provider 配置枚举值 deepseek；只在 DS run 解析本账户 DS ref 后传入。新增 `DeepSeekPublicWebReader.read(url: str) -> dict` wrapper，复用 mcp_server 公网 DNS/URL 策略后才调用 Task 4 reader。

- [ ] 写 `test_deepseek_tools_use_only_owner_key`：DS 无 Serper 仍开放搜索，OA 不读取 DS Key；模型和工具错误区分；unknown env 被丢弃。写 `test_fetch_policy_cancel_and_budget`：DNS 私网拒绝、取消/撤销中断、工具预算和 deadline 不增长、401 不 fallback 到未授权服务。
- [ ] 运行上述四文件 `PYTEST`，确认 RED。
- [ ] 装配 DS 搜索链和 public-reader，更新 MCP client/server 全部 safe provider 枚举、secret_values 与 redactor。无 redirect/trust_env；远程 Fetch 无法证明目标最终跳转符合现有安全政策时失败关闭，不能假定第三方抓取等同 DNS pinning；直接读取既有安全 reader 可保留为合规路径。网页只作不可信 evidence，不进用户记忆候选。
- [ ] 为工具事件加入安全 provider/tool/code；usage 增加 `search_requests,fetch_requests`，仅实际成功非空调用计数，模型 token 统计不变。只有已配置且允许的 fallback；空结果终止该次搜索链。
- [ ] 同命令 GREEN；测试 secret 不进入参数/日志/notebook/error，模型鉴权失败不误标 search，暂停记忆撤销活动搜索。
- [ ] 仅暂存本任务显式文件并提交 `feat: integrate isolated DeepSeek tools and safe diagnostics`。

### Task 6: Consistent account memory list snapshot

**Files:** Modify `src/gaia_agent/interactive/memory/{models.py,repository.py,api.py}`, `sites/agent-web/app/api/agent/[...path]/route.ts`；Test `tests/{test_memory_repository.py,test_memory_api.py}`。

**Interfaces:** 新增 `MemoryListSnapshot(settings: MemorySettings, items: tuple[MemoryView,...])`（不复用执行用 MemorySnapshot）。`MemoryRepository.list_snapshot(owner: str) -> MemoryListSnapshot`；鉴权 `GET /v1/memories/snapshot` 返回 `{settings,items}`，在动态 identifier 路由前注册；旧分页不改。

- [ ] 写 `test_list_snapshot_generation_and_owner_are_atomic`：同锁读取 settings/items，≤200；并发写前后只能出现一致组合；owner B 内容不泄露；不返回 source_fragment/canonical_key/ciphertext。API 测试 401/no-store/旧分页兼容。
- [ ] `PYTEST tests/test_memory_repository.py tests/test_memory_api.py`，确认 RED。
- [ ] 在现有 RLock 和 SQLite 一致读边界内实现，不在锁外拼 settings 和 list；按原列表排序，不调用模型。代理仅新增固定 GET。
- [ ] 同命令 GREEN；零条、200 条、暂停、删除后 generation 和列表一致。
- [ ] 仅暂存本任务五文件并提交 `feat: expose consistent owner-scoped memory snapshots`。

### Task 7: Shared animated accessible select

**Files:** Create `sites/agent-web/app/components/AnimatedSelect.tsx`, `app/lib/select-state.ts`, `tests/select-state.test.ts`；Modify `app/components/LanguageMenu.tsx`, `app/workspace.css`（以上相对于 sites/agent-web）。

**Interfaces:** `AnimatedSelect<T extends string>({value,options,onChange,label,disabled?})`，options 为 `{value:T,label:string,group?:string,disabled?:boolean}[]`；逻辑 `selectTransition(state: SelectState, event: SelectEvent) -> SelectState`。独立菜单使用 listbox/option，父 dialog 管理模态 focus trap。

- [ ] 写 `test_nested_menu_keyboard_contract`：disabled 项不可选、Esc 关闭最内层并 stopPropagation、Enter 只选不 submit、关闭恢复 trigger。状态测试覆盖 home/end/arrows；DOM 行为留 Task 10 真实浏览器，不把纯函数测试当焦点实测。
- [ ] 前端运行 `NODE --experimental-strip-types --test tests/select-state.test.ts`，确认 RED。
- [ ] 实现 portal/定位限宽与内部滚动，enter 170ms/translate5px，exit120ms；reduced-motion=0；主要目标44px。关闭/卸载清理监听器；隐藏内容不参与 Tab。迁移 LanguageMenu 验证与旧 locale 持久化兼容。
- [ ] 同命令 GREEN；运行 `NODE node_modules/typescript/bin/tsc --noEmit --incremental false` 通过。
- [ ] 仅暂存该五文件并提交 `feat: unify animated keyboard-accessible selects`。

### Task 8: Content-first memory management

**Files:** Create `sites/agent-web/app/components/MemoryEditor.tsx`, `app/lib/memory-editor.ts`, `tests/memory-editor.test.ts`；Modify `app/components/{MemorySettings.tsx,AgentChat.tsx}`, `app/lib/{memory.ts,ui-language.ts}`, `app/workspace.css`；Test `tests/memory.test.ts`（前端相对路径）。

**Interfaces:** `loadMemorySnapshot(): Promise<MemoryListSnapshot>`（TS shape 对齐 Task 6）；`MemoryEditor({record,open,onClose,onSaved,hasActiveRun})`，record 为 MemoryView|null；`MemoryTypeOption` 的 value 唯一映射现有 category/facet。`resetMemoryEditor()` 清除 owner-scoped draft，不持久化正文。

- [ ] 写 `test_memory_editor_conflict_and_owner_reset`：409 保留输入/显式重载，关闭/换账户清空；paused 不禁止人工编辑；取消 active-run 确认不发 mutation。测试 grouped type 映射、project/decision entity、snapshot 全量搜索和零网络搜索。
- [ ] `NODE --experimental-strip-types --test tests/memory-editor.test.ts tests/memory.test.ts`，确认 RED。
- [ ] 主列表仅正文/搜索/新增/开关/行菜单，长文展开；元数据/provider/expiry 收 Advanced，clear 在更多菜单。使用 Task 7 select；保留 CAS、manual priority、suppressed facet restoration 与现有 cancel barrier。取消确认不丢 draft；删除/clear JIT 提醒，开启共享 JIT 提醒；无 Key 保存同意删改。加载状态 unknown，错误短句+retry。
- [ ] 同命令 GREEN；locale 测试正文恰为 UI 文案也不翻译；无 localStorage/sessionStorage memory content/query；搜索词不入 URL/log。补跑 `PYTEST tests/test_memory_acceptance.py` 确认删除不自动学回。
- [ ] 仅暂存本任务显式文件并提交 `feat: simplify memory management with safe editable snapshots`。

### Task 9: Real model and effort picker integration

**Files:** Modify `sites/agent-web/app/components/{ModelSelector.tsx,AgentChat.tsx}`, `app/lib/{models.ts,model-picker.ts,history.ts,onboarding.ts,ui-language.ts}`, `app/workspace.css`；Test `tests/{models.test.ts,onboarding.test.ts,workspace-ui.test.ts}`（前端相对路径）。

**Interfaces:** `Selection={provider,model,reasoning_effort?:string}`；ModelSelector 消费 Task 1/2 profiles 和 availability，提交 Task 3 契约；历史额外保留 nullable capability_version/effort，不用当前 selection 回填。draft 保存合法选择，不保存 memory bodies/credentials。

- [ ] 写 `test_picker_respects_capabilities_and_preserves_payload`：无推理隐藏、非法 effort 拒绝；型号切换重置为合法 default；历史未记录显示未知；新档位进入请求与幂等尝试指纹。provider 切换新对话、不跨传旧历史，保留当前未提交输入。
- [ ] `NODE --experimental-strip-types --test tests/models.test.ts tests/onboarding.test.ts tests/workspace-ui.test.ts`，确认 RED。
- [ ] 用 Task 7 菜单实现服务商/系列搜索、历史型号 toggle、effort；unknown/unsupported disabled reason，发现失败静态 fallback 标识且可重试。未登录/无 Key 仍可浏览后走既有 onboarding；刷新不生成模型调用。高计算档位只一次短提示；工具 progress 显示实际 DS 搜索/抓取；费用详情可展开，独立计数不冒充 token。
- [ ] 同命令 GREEN 与 TypeScript 通过；检查中文/英文全部新增 UI 字符串，保留 user content/filename/answer 原样。
- [ ] 仅暂存本任务显式文件并提交 `feat: connect model discovery and reasoning picker to runs`。

### Task 10: Regression, synthetic browser QA and release evidence

**Files:** Modify `tests/manual_memory_app.py`, `tests/manual_memory_proxy.py`；Create `docs/superpowers/reports/2026-10-19-memory-models-search-verification.md`。

**Interfaces:** 合成 fixture 只在 localhost + TemporaryDirectory + 假执行器运行；加入模拟模型目录、search/fetch 事件及安全错误，不读取真实 vault/runtime.env。

- [ ] 先跑全部 Python：`PYTEST`；Ruff：`UV_CACHE_DIR=/tmp/uv-cache /Users/HPOMEN/.local/bin/uv run --no-sync ruff check --no-cache src tests deploy scripts`。修复回归前按 systematic-debugging/TDD，记录真实输出，不降低断言绕过失败。
- [ ] 前端运行 `NODE --experimental-strip-types --test tests/*.test.ts`、`NODE node_modules/typescript/bin/tsc --noEmit --incremental false`；将 bundled node bin 加 PATH 后运行 `NODE scripts/run-framework.mjs build`，全部需退出0。
- [ ] 浏览器用实际构建与 synthetic owner 在桌面/390/320 测量 innerWidth/scrollWidth；逐项测试长模型名、长记忆、空/加载/错误、完整搜索、CRUD409、暂停/clear、active-run确认、ownerchange、双语、登录/Key引导。真实用户记忆不操作。
- [ ] 键盘验收 Tab/方向键/Enter、nested Esc、focus restore、点击外部、reduced motion；特别检查旧 CSS 不隐藏搜索 label，聊天表单内菜单 Enter 不提交。录截图与结果；纯 Node 测试不能替代这些浏览器检查。
- [ ] 对 mock transport 日志、notebook 与 public errors 做 synthetic secret 扫描；工具重试/取消/余额错误和 deadline 验证。文档明确列出 documented/contract_tested/live_verified，各自证据独立；未批预算的真实烟测标记未执行，不阻塞离线成果交付，也不宣称全部型号可用或生产认证。
- [ ] 停止本轮 fixture 进程、恢复原 UI 语言/选择，不停止原真实服务；`git diff --check`。显式暂存本任务三个文件，提交 `test: verify memory models and search integration`；不 push/merge/deploy。

## Self-review / approval gate

- Spec 1–2 → Tasks 1–3/10；Spec 3 → 6/8；Spec 4 → 7/9/10；Spec 5 → 1–3/9；Spec 6 → 4–5/9；Spec 7 → 全部测试与10；Spec 8 → 上述依赖顺序。
- 五项 Review Focus 均指定测试；实现时仍需核对真实 transport schema 和目标页面，而非只测辅助函数。
- 契约统一：ResolvedSelection 冻结执行参数；MemoryListSnapshot 与执行 MemorySnapshot 不混用；可空 effort 与旧记录不伪造默认。
- 本计划不记录测试成功数；现有脏文件保留，真实账单测试另行批准。
- 用户审阅本计划后，沿用当前任务内执行方式，按 Task 1 开始；在此之前不改应用实现。
