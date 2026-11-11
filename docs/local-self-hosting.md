# 在自己电脑运行 ToyAgent

单用户、本机 Docker 部署，无需 Sites 或 ChatGPT 登录。支持 macOS/Linux；Windows 使用 Docker Desktop 的 Linux containers，下面命令在 WSL2 中运行。需要 Git、运行中的 Docker Desktop/Engine 与 Docker Compose v2（支持 volume subpath；建议使用当前 Docker Desktop）。首次构建需要网络与可用磁盘空间。

## 首次启动

```sh
git clone https://github.com/Oliver-Fang521/ToyAgent.git
cd ToyAgent
cp .env.local.example .env.local
docker compose --env-file .env.local up -d --build --wait
```

首次启动自动初始化本地密钥，无需设置密码。已有密钥和数据会保留；遇到不完整的秘密目录会拒绝覆盖，**不要为解决启动错误而删除秘密卷**。

打开 **http://localhost:3000**，直接在设置中填写你自己的 OpenAI 或 DeepSeek Key，再选择模型。模型和搜索可能产生服务商费用。此安装不会提供作者的 Key、额度或数据。

如端口占用，在 `.env.local` 中修改 `TOYAGENT_PORT=3020` 后使用对应的 `http://localhost:3020`。请使用 `localhost`，不要换成 `127.0.0.1`：登录严格检查配置的 Host/Origin。只绑定宿主机 loopback，**不要改为公网监听、添加隧道或把它当作多人共享服务**。当前 HTTP Cookie 策略仅为本机设计。

## 日志、停止和升级

```sh
docker compose --env-file .env.local ps
docker compose --env-file .env.local logs -f frontend backend
docker compose --env-file .env.local restart
docker compose --env-file .env.local down
```

`down` 保留数据。**`down -v` 会删除密钥和聊天/记忆数据卷，不要用于日常停止或升级。** 退出网页账户不会删除数据。本机模式不设访问密码，任何能使用这台电脑及其本地浏览器的人都可能访问数据。

升级前先按下面流程停止并备份，再运行 `git pull` 和 `docker compose --env-file .env.local up -d --build --wait`。不保证任意历史版本可以直接降级或读取新版数据库。后端必须保持单实例。

## 一致性离线备份

等待所有任务结束，停止服务。备份包含聊天、附件、记忆、遗忘屏障及解密所需的秘密，**必须作为敏感文件保管**；不向他人发送，也不要加入 Git。以下命令创建新文件，不覆盖旧备份。

```sh
docker compose --env-file .env.local stop frontend backend
mkdir -p backups
docker compose --env-file .env.local --profile tools run --rm -T -v "$PWD/backups:/backup" --entrypoint python init /app/deploy/local_backup.py backup --archive /backup/toyagent.tar
docker compose --env-file .env.local up -d --wait
```

如已有 `toyagent.tar`，换新文件名。仅备份业务卷而不保留秘密卷会失去解密能力；丢失主密钥无法从访问密码恢复。

## 恢复到全新实例

使用同版本代码与空卷，**先恢复，不要先执行 init**。下例使用新的 Compose project 名，避免覆盖原实例；恢复期间原实例保持停止。恢复工具拒绝非空目标，不合并旧聊天或重置遗忘屏障。

```sh
docker compose --env-file .env.local -p toyagent-restored --profile tools build init
docker compose --env-file .env.local -p toyagent-restored --profile tools run --rm -T -v "$PWD/backups:/backup:ro" --entrypoint python init /app/deploy/local_backup.py restore --archive /backup/toyagent.tar
docker compose --env-file .env.local -p toyagent-restored up -d --build --wait
```

恢复会保留备份时的本地密钥。不要解压来源不可信的备份。

## 故障排查与边界

- Docker 连接失败：先启动 Docker Desktop/Engine，确认 `docker info` 与 `docker compose version` 可用。
- 提示 subpath 不存在或 local_configuration：先运行 init；已有数据则检查卷/配置或从完整备份恢复，不自动生成替代主密钥。
- 无法登录/403：使用 `.env.local` 端口的 `http://localhost:端口`；不要从其他域名、代理或 iframe 访问。
- 任务失败：网页诊断会指出阶段和工具；使用上方 logs 看服务日志，不分享任何 Key、秘密 JSON 或完整用户数据。
- 模型不可用/余额不足：由自己的服务商账户权限和余额决定，不会借用作者或其他账户 Key。
- DeepSeek Search 已支持；远程 Fetch 暂未挂载，网页读取保留现有安全读取器。Docker 不增加原本没有的工具能力。

这是本地单用户方案，不是生产多租户、高可用或公网安全认证。实际测试范围以日期对应的验证报告为准。
## Semantic memory maintenance

Memory maintenance runs after a successful answer and can incur a separate model charge. Each job uses one bounded, tool-free call (6400 output tokens,30-second planner timeout); an explicitly confirmed retry allows at most one additional dispatch. Unknown outcomes after dispatch are marked uncertain and are not automatically replayed. Temporary credentials expire with their in-memory reference; restart never substitutes a saved Key for them.

Use **My memory → About memory** for pending/failure status and maintenance usage. An invalid Key is corrected in provider settings before retry. Ordinary deletion does not disable future learning; permanent forgetting blocks the selected topic until explicitly restored. Old chat content is not automatically backfilled on upgrade.

Before upgrading an existing installation, stop both application services and make the documented offline backup of both secrets and data. Restore tests must use empty, separate volumes. Never remove existing volumes to apply a schema upgrade. The migration is additive and preserves old encrypted rows and tombstones.

## Layered memory (2026-10-22)

There is no account-wide 200-fact limit. Facts remain encrypted and independently searchable; the management page searches the whole account and loads more results on demand. This is not unlimited disk or context: each search/read and model request has a bounded processing budget.

After upgrade, a deterministic background worker rebuilds missing fact indexes in batches of 100 without reading old chats or charging a model. During rebuilding, searches can use bounded legacy scans and continuation. Existing fact IDs, revisions, keyrings and forgetting barriers are preserved. Do not initialize new secrets to apply this migration.

The active conversation model decides whether to search memory and may search/read repeatedly, up to six tool rounds or eight actions. Small account overviews are partial navigation, not the complete memory corpus. Facts and retained sources remain the authority; current external facts still require current verification.

Changed topics may be summarized into shards, topics and a small account overview using the original selected model and user credential. Summary maintenance is separate from extraction: two workers globally, one active request per account/provider, at most four summary dispatches per account/provider per rolling hour. This processing budget does not limit saved facts. Failed or uncertain paid work is not automatically replayed; an explicitly confirmed retry is separately metered. Editing, deleting, pausing or permanently forgetting invalidates dependent material before further use. Old facts are not evicted simply for being numerous or unused.

Offline synthetic verification (no Key required):

```sh
uv run python scripts/layered_memory_acceptance.py --offline --count 1000
```

Use `--count 10000` for the larger storage/index case. Results measure lexical retrieval and pagination, not general model understanding. Real-model tests require explicit read-only credential source paths and a persistent authorized budget ledger; never put Keys into commands or commit them. See the dated verification report for tested cases and remaining limitations.
