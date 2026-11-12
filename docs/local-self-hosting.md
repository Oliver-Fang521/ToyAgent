# Run ToyAgent on Your Computer

This is a single-user, local Docker deployment. It requires neither Sites nor ChatGPT sign-in. It supports macOS and Linux; on Windows, use Docker Desktop with Linux containers and run the commands below in WSL2. You need Git, a running Docker Desktop or Engine, and Docker Compose v2 with volume subpath support; a recent Docker Desktop is recommended. The first build requires internet access and available disk space.

## First launch

```sh
git clone https://github.com/Oliver-Fang521/ToyAgent.git
cd ToyAgent
cp .env.local.example .env.local
docker compose --env-file .env.local up -d --build --wait
```

The first launch automatically initializes local secrets; no password is required. Existing secrets and data are preserved. Initialization refuses to overwrite an incomplete secrets directory. **Do not delete the secrets volume to resolve a startup error.**

Open **http://localhost:3000**, enter your own OpenAI or DeepSeek API key in Settings, and select a model. Model and search calls may incur provider charges. This installation does not provide the author's API keys, credits, or data.

If the port is already in use, set `TOYAGENT_PORT=3020` in `.env.local` and use `http://localhost:3020`. Use `localhost`, not `127.0.0.1`: the application checks the configured Host and Origin. The service binds only to the host's loopback interface. **Do not expose it publicly, add a tunnel, or treat it as a shared multi-user service.**

## Logs, stopping, and upgrades

```sh
docker compose --env-file .env.local ps
docker compose --env-file .env.local logs -f frontend backend
docker compose --env-file .env.local restart
docker compose --env-file .env.local down
```

`down` preserves data. **`down -v` deletes the secrets and chat/memory data volumes; do not use it for routine stops or upgrades.** Local mode has no access password, so anyone who can use this computer and access its local browser may be able to access the data.

Before upgrading, stop and back up the installation using the procedure below, then run `git pull` and `docker compose --env-file .env.local up -d --build --wait`. Downgrading between arbitrary versions or reading a newer database with an older version is not guaranteed. Keep the backend to a single instance.

## Consistent offline backup

Wait for all tasks to finish and stop the services. The backup contains chats, attachments, memories, forgetting barriers, and the secrets needed to decrypt them. **Treat it as sensitive:** do not share it or commit it to Git. These commands create a new archive without overwriting an existing one.

```sh
docker compose --env-file .env.local stop frontend backend
mkdir -p backups
docker compose --env-file .env.local --profile tools run --rm -T -v "$PWD/backups:/backup" --entrypoint python init /app/deploy/local_backup.py backup --archive /backup/toyagent.tar
docker compose --env-file .env.local up -d --wait
```

If `toyagent.tar` already exists, choose a new filename. Backing up only the application data without the secrets volume will leave you unable to decrypt it. A lost master key cannot be recovered from an access password.

## Restore to a new instance

Use the same code version and empty volumes. **Restore before starting the instance.** The example uses a new Compose project name to avoid overwriting the original instance; keep the original stopped during restoration. The restore tool refuses non-empty targets and does not merge old conversations or reset forgetting barriers.

```sh
docker compose --env-file .env.local -p toyagent-restored --profile tools build init
docker compose --env-file .env.local -p toyagent-restored --profile tools run --rm -T -v "$PWD/backups:/backup:ro" --entrypoint python init /app/deploy/local_backup.py restore --archive /backup/toyagent.tar
docker compose --env-file .env.local -p toyagent-restored up -d --build --wait
```

Restoration preserves the local secrets from the backup. Do not extract backups from untrusted sources.

## Troubleshooting and scope

- **Docker connection failure:** Start Docker Desktop or Engine, then check `docker info` and `docker compose version`.
- **Missing subpath or `local_configuration`:** Check initialization, volumes, and configuration. If data already exists, do not generate replacement master keys; inspect the volumes or restore a complete backup.
- **403 or inaccessible page:** Use `http://localhost:<port>` with the port configured in `.env.local`; do not use another domain, proxy, or iframe.
- **Task failure:** The interface reports the stage and tool when available. Inspect the service logs above; do not share API keys, secrets JSON, or complete user data.
- **Model unavailable or insufficient balance:** Availability depends on your provider account's permissions and balance; the installation does not use the author's or another user's API key.
- **DeepSeek Search:** Search is supported; remote Fetch is not mounted, and page reading uses the existing safe reader. Docker does not add tool capabilities that are absent from the application.

This is a local single-user setup, not production multi-tenancy, high availability, or public-facing authentication. Refer to dated verification reports for the scope actually tested.

## Semantic memory maintenance

Memory maintenance runs after a successful answer and can incur a separate model charge. Each job uses one bounded, tool-free call (6400 output tokens,30-second planner timeout); an explicitly confirmed retry allows at most one additional dispatch. Unknown outcomes after dispatch are marked uncertain and are not automatically replayed. Temporary credentials expire with their in-memory reference; restart never substitutes a saved Key for them.

Correct an invalid key in provider settings before retrying. Ordinary deletion does not disable future learning; permanent forgetting blocks the selected topic until explicitly restored. Old chat content is not automatically backfilled on upgrade.

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
