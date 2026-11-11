# Agentic Layered Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 取消记忆条数硬上限，让当前主模型循环检索全库，并由后台模型增量生成有来源、可失效的主题摘要与账户概要。

**Architecture:** 保留加密规范事实和原有提取队列；新增可分页 HMAC 索引、带版本依赖的派生摘要及独立整理日志。扩展原路由模型为有界动作循环，读取结果连续进入同一上下文；研究分支复用该记忆包，不再创建第二个循环。

**Tech Stack:** Python 3.12+、SQLite、Pydantic、现有 OpenAI/DeepSeek provider、FastAPI、React/TypeScript/Vinext；无新增外部记忆服务或 embedding 依赖。

**Spec:** `docs/superpowers/specs/2026-10-22-agentic-layered-memory-design.md`（用户已批准）。

## Global Constraints

- 使用现有独立 worktree `/Users/HPOMEN/.codex/worktrees/public-agent-web/agent`；基线 `953207b`，执行前重新确认 HEAD、AGENTS 和运行实例。不切换用户主 checkout、不创建重复 worktree；不纳入 `tests/test_orchestrator.py` 和其他既有脏文件。
- 无固定账户事实条数上限；不因年龄、少用或摘要压缩自动淘汰有效事实；不是无限物理磁盘承诺。保持原来源载荷退休政策和隐私界限，不回扫真实聊天。
- 无额外记忆分类模型。前台用当前用户模型；后台用产生相关变更时记录的用户 provider/model/reasoning，凭据撤销立即阻断；不得使用作者 Key、隐式跨 provider 或失败后换模型。
- 最多六轮工具请求、累计八次工具执行，包含最后一次决策在内最多七次主模型调用；连续两轮无新增证据终止。
- 账户概要最多 1200 UTF-8 bytes；单次工具结果最多 4000 bytes；累计记忆载荷最多 16000 bytes，同时服从实际模型输入 token 和既有任务截止时间的更小预算。
- 整理变更至少合并等待 10 秒；全局最多两个后台消费者；单账户/provider 每小时最多四次整理调用（主题分片、主题合并、概要均计入），每次最多 30 秒、1000 输出 tokens。超额仅排队，不拒绝事实写入。
- 摘要派发后不自动重试；显式重试最多一次并受上述预算约束。token 用量计账，价格未知不宣称美元硬限；真实付费验收先确定额外调用/费用范围。
- 用户删改、权限变化、暂停、永久遗忘和清空须先失效派生内容；旧建议不能复活事实。临时/暂停无内容注入、无记忆读取工具、无写入。
- 不逐轮显示记忆更新，不关闭研究验证；所有新 UI 文案中英双语。任何模型文本不得决定账户授权。
- 本计划阶段不改产品代码、不运行付费测试、不更新 3000/3025 实例；实施仍需用户审阅计划并选择执行方式。

## Review Focus

- 概要未列出的老主题、索引迁移未完成：仍能分页发现旧事实，而非误报不存在（Task 1/5）。
- 摘要生成与永久删除同时发生：旧输出不能发布，活动任务不得重新注入缓存（Task 2/6）。
- 用户切换模型、撤销 Key 或后台崩溃：不能默默换服务商或重复付费（Task 3）。
- 重复工具参数、畸形模型动作和多字节中文：不得绕过次数、字节/token 预算或使循环无限进行（Task 4/5）。
- 多页管理时修改/删除引起排序变化：游标不得串账户、漏报权限或继续显示失效摘要（Task 1/7）。

## Files and Interfaces Map

新增 `memory/layer_models.py` 定义以下不可变、隐藏正文 repr 的数据类型；owner/provider 由服务端构造，模型参数不含 owner：

- `MemoryScope(owner, provider, generation)`、`FactRef(id, revision)`。
- `SearchPage(items, next_cursor, total, coverage)`、`MemoryReadResult(items, coverage)`：items 为现有记录或含 kind/id/revision/content/source_refs 的有界结果，coverage 为 `complete/index_pending/truncated/not_found_in_search/unavailable`。
- `SummaryDraft(kind, topic_id, content, source_refs, child_refs)`、`SummaryView(id, revision, content, source_refs, state)`：kind 为 shard/topic/overview；主题/overview 通过 child_refs 形成依赖 DAG。
- `MaintenanceClaim(id, scope, input_revision, selected_model, credential_epoch, cursor, lease_id)`。
- `MemoryToolCall(name, arguments)`：name 只允许 search_memory/read_memory，arguments 为严格判别联合。
- `MemoryEvidence(context_text, refs, coverage, generation, usage)`、`MemoryLoopResult(decision, evidence, usage, termination)`；decision 沿用 `RouteDecision`。

新增 `memory/index.py`、`summaries.py`、`summary_planner.py`、`maintenance_queue.py`、`maintenance.py`、`tools.py`、`read_loop.py`，分别负责索引、存储/失效、模型归纳、整理账本/调度、工具契约和前台循环。扩展已有 schema/crypto/repository/service/retention/barriers 及 routing/credential_runtime/composition；不把新逻辑都塞进 worker。

统一测试命令前缀：`UV_CACHE_DIR=/tmp/uv-cache /Users/HPOMEN/.local/bin/uv run --no-sync pytest -o addopts='' -q --tb=short -p no:cacheprovider`，下文用 `PYTEST` 代表完整前缀。以选择性的文件 `git add -- <task files>` 提交；禁止 `git add .`。

---

### Task 1: 全库分页与索引，取消容量限制

**Files:** 新增 `memory/layer_models.py`、`memory/index.py`、`tests/test_memory_index.py`；修改 `memory/schema.py`、`crypto.py`、`repository.py`、`service.py`、`semantic.py`、`consolidation.py` 和原容量/候选测试。

**Interfaces:** `MemoryIndex.search(scope: MemoryScope, query: str, cursor: str | None = None, limit: int = 20) -> SearchPage`；`backfill(scope, cursor=None, batch_size=100) -> SearchPage`；repository 新增 `page_records(owner, cursor=None, limit=50) -> SearchPage` 和 `state_snapshot(owner, provider)`（仅状态/版本，不含全库正文）。搜索游标绑定 scope/query/索引版本，不接受客户端自由拼接 offset/SQL。

- [ ] 写 RED 测试 `test_1000_facts_preserved_and_pageable`：保存 1000 个独立事实，逐页 ID 集合恰等于 1000，最早事实可查；`test_partial_backfill_falls_back_to_old_rows`：coverage=index_pending 仍返回旧记录；`test_cursor_owner_query_and_mutation`：异账户/换查询拒绝游标、数据变化给明确游标重启状态，无静默遗漏。
- [ ] 运行 `PYTEST tests/test_memory_index.py tests/test_memory_repository.py`，记录缺接口/原容量拒绝导致的预期失败，不接受依赖安装失败为 RED。
- [ ] 实现索引表、HMAC 词项、确定性回填与按 ID 分批解密；所有相关写入同事务更新索引。去除写入 count>=200 和候选最近200限制；替换整库快照调用为小状态及有界候选。保留管理旧 offset 接口但每页最多50。提取器走全库匹配候选；更新原“第201条失败”测试为保存成功，不删除来源/CAS断言。
- [ ] 运行上述测试加 `tests/test_memory_schema.py tests/test_memory_semantic.py tests/test_memory_consolidation.py`，全部 PASS；检索超大命中集合返回可续页结果而不全库解密。
- [ ] 选择本任务文件提交 `feat(memory): index all facts and remove account count cap`。

### Task 2: 加密派生摘要与同步失效

**Files:** 新增 `memory/summaries.py`、`tests/test_memory_summaries.py`；修改 schema/crypto/repository/retention/barriers/service。

**Interfaces:** `SummaryStore(repository).publish(scope: MemoryScope, draft: SummaryDraft, input_revision: int) -> SummaryView`；`overview(scope) -> SummaryView | None`；`invalidate(owner: str, fact_ids: tuple[str, ...], *, all_scopes: bool = False) -> None`。发布事务验证 FactRef/child_refs、scope、版本和 DAG 无环；不变更原子事实。

- [ ] 写 RED 测试 `test_summary_never_overwrites_facts`、`test_delete_during_generation_rejects_publish`、`test_provider_shrink_invalidates_parent_overview`：事实保持原值，旧版本发布冲突，父概要不可读；测试只有部分来源失效也先隐藏整个受影响摘要。
- [ ] 运行 `PYTEST tests/test_memory_summaries.py` 验证预期 RED。
- [ ] 增加摘要/依赖/脏范围的加法迁移与加密对象种类；在事实删改、永久屏障、clear、provider 权限和暂停事务内使派生层失效；读取再次验证依赖、过期及状态。无概要返回 None，不回落八条预选事实。状态与 API 错误日志不得带正文。
- [ ] 运行新测试加 `tests/test_memory_forgetting.py tests/test_memory_review_regressions.py tests/test_memory_crypto.py` 全部 PASS，断言没有恢复被删除正文。
- [ ] 提交 `feat(memory): add revision-bound encrypted summary layers`。

### Task 3: 模型增量整理与有界后台账本

**Files:** 新增 `memory/summary_planner.py`、`maintenance_queue.py`、`maintenance.py`、`tests/test_memory_maintenance.py`；修改 api/credential_runtime/schema 及事实发布后的标脏接入。

**Interfaces:** `SummaryPlanner(provider).summarize(claim: MaintenanceClaim, inputs: tuple, *, usage_observer) -> SummaryDraft`；`MaintenanceQueue.mark_dirty(scope, selection, credential_epoch, topic_ids)`、`claim(now, worker_id) -> MaintenanceClaim | None`、`reserve_dispatch(claim)`、`retry(owner, job_id, confirm_cost: bool)`；`MemoryMaintenanceWorker.start()/close()`，通过用户工厂创建模型，无环境凭据兜底。

- [ ] 写 RED 测试：fake clock 下9秒不派发、10秒可派发；单域第5次调用被排队；30秒超时记录安全码；1000输出tokens参数；两消费者同域只领取一个；重启后 provider_started 不自动再发；重试只一次且需费用确认；撤销Key后调用计数为0。
- [ ] 运行 `PYTEST tests/test_memory_maintenance.py`，确认 RED 来自未实现行为。
- [ ] 实现独立整理日志/分片游标，提取完成触发模型主题归类及有界shard→topic→overview整理，全部模型请求计入四次预算。脏范围合并保留最新已授权选择，租约中的模型选择不可修改。大输入分片，不静默裁掉源事实；来源退休不受摘要任务拖延。CAS冲突只排未派发的新版本，不自动重发旧请求。
- [ ] 测试重复表达、不同实体、独立偏好、纠正及注入输出；`PYTEST tests/test_memory_maintenance.py tests/test_memory_worker.py tests/test_memory_credential_lifetime.py` 全部 PASS。脚本模型验证结构边界，语义忠实度留Task8真实测试，不把引用检查当语义证明。
- [ ] 提交 `feat(memory): consolidate dirty topics with bounded user-model jobs`。

### Task 4: 只读记忆工具与覆盖状态

**Files:** 新增 `memory/tools.py`、`tests/test_memory_tools.py`；补全 layer_models；修改 service/guard。

**Interfaces:** `MemoryTools(service, scope).execute(call: MemoryToolCall) -> SearchPage | MemoryReadResult`；`read_memory(kind, ids)` 每次最多8个 opaque ID，kind 为 topic/fact/source，结果超过4000 bytes 返回显式截断/游标；`validate_refs(scope, refs)` 返回仍有效引用。来源只用仍合法保留的证据，不打开任意聊天/路径。

- [ ] 写 RED 测试 `test_search_read_old_fact_with_no_overview`、`test_scope_cannot_be_model_selected`、`test_unknown_id_path_and_sql_rejected`、`test_4000_byte_result_is_utf8_valid_and_marked`、`test_paused_temporary_return_no_content`；跨账户/provider均无正文。
- [ ] 运行 `PYTEST tests/test_memory_tools.py` 验证 RED。
- [ ] 实现严格工具参数、scope绑定、全库搜索/主题分页、规范事实读取和来源缺失状态；权限和generation变化立即拒绝旧scope。对有界结果做安全渲染，禁止模型字符串选择SQL/文件。没有命中明确标 not_found_in_search。
- [ ] 同上测试加 `tests/test_memory_service.py` 全部 PASS；确保只有数据读取，没有任何模型调用。
- [ ] 提交 `feat(memory): expose scoped search and read tools`。

### Task 5: 主模型循环路由，不新增记忆分类器

**Files:** 新增 `memory/read_loop.py`、`tests/test_memory_read_loop.py`；修改 routing/layer_models，保持既有 RouteDecision 兼容。

**Interfaces:** `async route_with_memory(provider, *, question: str, history: str, reasoning_effort: str, tools: MemoryTools | None, overview: SummaryView | None, deadline: float, usage_observer=None) -> MemoryLoopResult`。使用一个严格结构化动作联合 `tool_calls | RouteDecision`，模型既不能自报owner，也不能把工具参数当代码执行；通过现有 provider.complete 调用，无额外分类模型。

- [ ] 写脚本模型 RED 测试：零工具1次调用；搜索→读事实→direct共3次；中文改写找早期事实；无概要仍可查；连续两轮无新增终止；同参重复不重执行；六轮后第七次禁工具；八工具总限；overview1200/单结果4000/累计16000 UTF-8 bytes；工具定义/历史及摘要同时纳入实际模型token预算，不足不派发。
- [ ] 运行 `PYTEST tests/test_memory_read_loop.py` 验证 RED，保留provider记录的请求次数/模型/输入历史断言。
- [ ] 实现连续上下文动作循环、工具结果引用与usage累计；多工具并行一轮最多剩余工具额度。只保存安全动作，不保存隐藏推理。第七次仍请求工具/畸形结构/最终失败返回安全错误，不再发修复模型。预算/空结果等coverage在最终决策输入明确标注，提示承认缺口，research验证要求保持不变。
- [ ] 上述测试加原 routing 测试全部 PASS；通过工具结果中恶意“忽略验证”反例验证信任边界。禁止fake模型仅匹配硬编码关键词来伪造真实语义效果。
- [ ] 提交 `feat(memory): let the active model iteratively retrieve memory`。

### Task 6: 运行时整合与撤销安全

**Files:** 修改 credential_runtime/composition/api、memory/runtime.py、guard.py、service.py；新增 `tests/test_agentic_memory_runtime.py`，扩展 tests/test_credential_runtime.py。

**Interfaces:** UserRuntime调用Task5；`MemoryEvidence` 的已读合法引用与文本传入direct/research，MemoryContextProvider扩为按任务读取有效证据且支持16000预算，不继续沿用6000吞掉上下文。每次模型派发前 `validate_refs`，权限/删除变化按既有cancel_owner取消任务；无记忆模式不创建tools。

- [ ] 写 RED 集成测试：direct和research收到同一引用包；research不重新跑读循环；无数据ready与pending/paused/temporary/unavailable不混淆；读取后、最终模型派发前删除/收缩权限，旧文本不发送；取消后不复活后台建议；独立翻译在记忆服务坏掉时仍可完成但状态真实。
- [ ] 运行 `PYTEST tests/test_agentic_memory_runtime.py tests/test_credential_runtime.py` 验证 RED。
- [ ] 用新循环替换前台select_context八条预选，沿用UserRuntime用户模型和deadline；状态小快照替代整库snapshot；usage合并包含每次循环、研究调用，后台单列整理用量。旧provider包装器不得重复注入概要或把 memory 当外部证据。
- [ ] 新测试及 `tests/test_memory_runtime.py tests/test_memory_end_to_end.py tests/test_memory_smoke.py` 全部 PASS，原聊天/附件/研究验证测试不回归。
- [ ] 提交 `feat(memory): integrate iterative recall into direct and research routes`。

### Task 7: 管理页分页搜索与简短双语状态

**Files:** 修改 `src/gaia_agent/interactive/memory/api.py`、`sites/agent-web/app/lib/memory.ts`、`sites/agent-web/app/lib/memory-editor.ts`、`sites/agent-web/app/components/MemorySettings.tsx`、`sites/agent-web/app/components/MemoryEditor.tsx`；仅在分页布局需要时改 `sites/agent-web/app/workspace.css`。双语状态沿用上述组件与lib现有文案位置；扩展 `tests/test_memory_api.py`、`sites/agent-web/tests/memory.test.ts`、`sites/agent-web/tests/memory-status.test.ts`。

**Interfaces:** GET `/v1/memory/entries` 保持旧offset/limit契约；新增 GET `/v1/memory/search?q=&cursor=&limit=`（管理页账户权限视图，不允许客户端owner）、GET `/v1/memory/maintenance`、POST `/v1/memory/maintenance/{id}/retry`（confirm_cost=true）。搜索响应为items/total/next_cursor/coverage，旧接口不返回无界列表。

- [ ] 写 RED 测试：第201条可新增、1000条翻页/搜索、跨账户游标拒绝；翻页期间删改后明确刷新，不残留旧摘要；暂停/临时准确；费用确认false不重试；未登录401，API错误无正文。前端断言中英均无“200条满了”、普通删除不变永久删除、不逐轮toast。
- [ ] Python和Node目标测试运行记录RED，执行前确认项目现有Node测试运行器，沿用Node22而非系统旧Node。
- [ ] 接入服务端搜索分页、取消旧容量提示、保持简洁动画和键盘操作；失败/部分索引/整理排队只在管理状态或确实影响问题时显示。普通用户不看到内部敏感日志/全文派发数据。
- [ ] API/Node目标测试、TypeScript noEmit及生产build通过；在独立合成账户桌面和窄屏检查实际分页和双语，截图对照。不得以测试名义改真实记忆。
- [ ] 提交 `feat(memory): paginate memory management and expose safe maintenance state`。

### Task 8: 升级、性能与真实模型验收

**Files:** 新增 `tests/test_layered_memory_migration.py`、`scripts/layered_memory_acceptance.py`、合成案例fixtures及 `docs/superpowers/reports/2026-10-22-layered-memory-verification.md`；扩展部署文档与 scripts/local_self_host_smoke.py，不混入用户真实凭据。

**Interfaces:** 新验收脚本默认 `--offline`，显式 `--live --provider --max-calls --budget-ledger` 才收费；ledger跨重启累计所有前台/整理用量，预算检查在派发前，账本不确定则停止。费用范围在live前与用户确认，不能从旧149次测试账本假装重新开始。

- [ ] 写 RED 升级测试：旧库含201+合成事实、现有ID/revision/keyring/barrier不变；迁移跑两次相同；索引构建中重启仍可找旧事实；旧未完成付费任务不再次派发；restore后读取一致。
- [ ] 运行 `PYTEST tests/test_layered_memory_migration.py` 验证RED，修正迁移/回填实现后重复运行到PASS，不重初始化数据库。
- [ ] 跑1000条保存/召回和10000条性能测试，输出命中/漏召回、分页次数、解密数量、构建耗时、内存、读取p50/p95以及模型调用数；真实OpenAI/DeepSeek在批准范围内测试同义改写、多轮缺口、摘要忠实度、冲突与遗忘。保留失败样本，失败则回相应任务RED→GREEN，不降低标准。
- [ ] 全量Python、Node测试、TS、生产build、Ruff与diff检查通过；独立Docker Python3.12/Node22旧库升级、健康检查、重启、备份恢复通过。报告分别列离线/真实模型/容器证据与未测项，不把功能测试写成生产安全认证。
- [ ] 选择本任务文件提交 `test(memory): verify layered recall migration and model behavior`。执行方法如包含独立复核，按获选流程复核并修复后复跑；推送/PR/真实实例更新需用户另行授权。

## Self-review and Handoff

Spec映射：目标与基线→Task1/6；三层数据→Task1/2；后台压缩→Task3；工具循环→Task4/5/6；全库检索→Task1/7；删除故障→Task2/3/6；迁移验收→Task8。保留输入事件五项提取上限的范围说明，不用此次读取改造冒称解决全部提取完整性。

类型统一来自layer_models，原RouteDecision不重定义。所有任务有可验证RED→GREEN和独立提交点；没有新增不受控付费重试、静默跨provider或旧事实自动淘汰。计划未执行，任何PASS均为预期结果而非已取得证据。

请用户审阅并选择：当前会话由主代理逐项实现，或逐任务子代理实现与复核。推荐前者，因schema、依赖失效与循环上下文紧密耦合且可减少重复上下文成本；最终独立复核方式在执行交接时明确，不提前派发子代理。
