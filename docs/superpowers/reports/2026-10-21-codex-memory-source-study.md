# Codex memory 源码研究与 ToyAgent 设计映射

研究日期：2026-10-21。用户指定来源：https://github.com/openai/codex 。通过 `git ls-remote` 核对当时 main 并 fetch 固定提交 **430a16799d90e296fb27b1e94f206023902b7e56**；读取该提交源码，不运行 Codex 写记忆流程，不读取或改动用户的 Codex 记忆库。

## 已读取的主要来源

- [官方 Memories 文档](https://learn.chatgpt.com/docs/customization/memories)：后台更新、跳过活跃/短暂会话、读写控制、额度保护；不是每次结束对话立即完成。
- [流水线说明](https://github.com/openai/codex/blob/430a16799d90e296fb27b1e94f206023902b7e56/codex-rs/memories/README.md)：解释提取、整合、任务领取/租约、增量差异和读取分层；部分路径及输出描述属于 v1，不能只读 README 就推断所有版本。
- [实际启动逻辑](https://github.com/openai/codex/blob/430a16799d90e296fb27b1e94f206023902b7e56/codex-rs/memories/write/src/start.rs)：feature/ephemeral/non-root gates、额度判断、Phase1→Phase2，以及按配置选择 v1/v2/dual-write。
- [版本化提取输出](https://github.com/openai/codex/blob/430a16799d90e296fb27b1e94f206023902b7e56/codex-rs/memories/write/src/phase1_output.rs)：v1 raw_memory+rollout_summary；v2 summary-only，严格解析并脱敏/限长。未推断所有安装默认使用哪个版本。
- [v1 提取指令](https://github.com/openai/codex/blob/430a16799d90e296fb27b1e94f206023902b7e56/codex-rs/memories/write/templates/memories/stage_one_system.md)：有价值才保存、用户纠正优先、验证证据、区分成功/失败/不确定。
- [v2 提取指令](https://github.com/openai/codex/blob/430a16799d90e296fb27b1e94f206023902b7e56/codex-rs/memories/write/templates/memories/stage_one_system_v2.md)：按任务保留范围/时间线/来源，不把一次要求夸大为永久偏好，不把助手提议当用户决策。
- [v1 整合指令](https://github.com/openai/codex/blob/430a16799d90e296fb27b1e94f206023902b7e56/codex-rs/memories/write/templates/memories/consolidation.md)与[v2 整合指令](https://github.com/openai/codex/blob/430a16799d90e296fb27b1e94f206023902b7e56/codex-rs/memories/write/templates/memories/consolidation_v2.md)：前者维护 handbook/index/可选skills，后者从摘要维护紧凑总览；都要求正确处理来源删除和用户纠正。
- [v2 读取指令](https://github.com/openai/codex/blob/430a16799d90e296fb27b1e94f206023902b7e56/codex-rs/ext/memories/templates/memories/read_path_v2.md)：先用摘要，只有额外证据可能改变答案时才追溯；历史不是当前事实证明。
- [明确用户编辑通道](https://github.com/openai/codex/blob/430a16799d90e296fb27b1e94f206023902b7e56/codex-rs/memories/write/templates/extensions/ad_hoc/instructions.md)：用户编辑记为 notes 再整合，note内容是数据而不是可执行指令。
- [Phase2 实现](https://github.com/openai/codex/blob/430a16799d90e296fb27b1e94f206023902b7e56/codex-rs/memories/write/src/phase2.rs)：整合前领取锁，检查工作集差异并验证输出；普通受限配置不开放网络。这里是本地 coding-agent 实现，不是多租户数据库模板。

## 可借鉴与不能直接复制

| Codex 的实际机制 | ToyAgent 的设计映射 |
| --- | --- |
| 对话提取与全局整合分开 | 新消息/完成轮次→候选事件；账户内单写者做去重、修正和发布 |
| 任务摘要、稳定经验、紧凑总览 | 来源证据→规范记录→带版本的概要/索引，按需追溯 |
| 用户明确编辑是独立输入 | 识别任意明确保存/更正/遗忘语义，写入可靠事件队列，不假装已完成 |
| 无信号可以不更新 | 普通闲聊不硬造画像；明确保存不被自动价值评分静默丢弃 |
| 用户纠正与实际验证高于助手说法 | 区分用户事实、任务决定、验证过的项目经验与未采纳提案 |
| 租约、幂等、退避、增量差异 | SQLite 持久任务、generation屏障、CAS、失败码和有限重试 |
| 来源删除传播到摘要 | 普通删除/永久遗忘都立即使派生摘要失效，其他主题自动学习继续 |

以下是 ToyAgent 的产品设计，**不是声称 Codex 已实现同样功能**：逐账户/逐服务商密钥隔离、加密数据库、普通删除与永久遗忘的区别、永久遗忘语义屏障、开放主题 CRUD UI、BYOK后台任务的费用归属和会话失效处理。

不照搬明文共享记忆目录、把记忆当高优先级指令注入、让整合 Agent 任意写文件/生成可执行 skill，也不把未经验证的工具/网页内容当用户画像。Codex 不以必须 embedding 为前提；本研究未发现依据可宣称应整体迁移向量数据库。

## 用户最新修正与推荐

用户明确不要求“保存后马上得到可靠确认”。因此采用**异步优先的双阶段记忆**，取消前一版“回答前完成明确记忆事务”的硬要求：前台只持久化输入事件，不等待整理；后台读取同账户、同provider的有限证据并整合。界面不每轮报告更新。明确保存事件优先、不被静默丢弃；尚未完成只能表示已收到/处理中，不能表示已记住。

与 Codex 不同，ToyAgent 是用户期待连续交互的网页产品。新对话若遇到仍待处理的记忆，只能用已经发布的版本；如影响“你是否记得”等直接问题，应如实说明整理未完成，不编造身份。需实测后台完成延迟，不能以异步为理由长期不保存。

保留既有数据、账户隔离、用户开关及普通删除/永久遗忘政策。后台付费工作需要明确模型、Key generation、token/时限和任务预算；本轮研究没有发起任何付费调用，也没有重新运行旧聊天提取。

结论：学习 Codex 的**证据分层、异步提取、增量整合与按需读取**，而不是复制其具体目录、版本格式或将聊天全文直接变成永久画像。
