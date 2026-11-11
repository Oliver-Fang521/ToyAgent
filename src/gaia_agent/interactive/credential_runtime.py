"""Public execution: explicit per-user clients, never a process credential fallback."""

import asyncio
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from openai import APIError, APIStatusError, AsyncOpenAI, AuthenticationError, PermissionDeniedError
from pydantic import BaseModel, ConfigDict, field_validator

from gaia_agent.interactive.catalog import ENDPOINTS, ModelSelection, resolve_profile
from gaia_agent.interactive.composition import InteractiveFailure, execute_interactive
from gaia_agent.interactive.credential_vault import CredentialError
from gaia_agent.interactive.failures import safe_diagnostic
from gaia_agent.interactive.memory.models import MemoryReport
from gaia_agent.interactive.memory.semantic import SemanticMemoryPlanner
from gaia_agent.interactive.models import InteractiveConfig
from gaia_agent.interactive.tool_policy import allowed_tools
from gaia_agent.mcp_client import MCPToolClient
from gaia_agent.providers import DeepSeekResponsesProvider, OpenAIProvider
from gaia_agent.reliability import RetryPolicy


class InteractiveRuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    base_url: str

    @field_validator("base_url")
    @classmethod
    def https_endpoint(cls, value):
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or any(c.isspace() for c in value)
        ):
            raise ValueError("invalid_model_endpoint")
        if value.rstrip("/") != ENDPOINTS["openai"]:
            raise ValueError("invalid_model_endpoint")
        return value.rstrip("/")

    @property
    def provider_hostname(self):
        return urlsplit(self.base_url).hostname


def provider_error_code(error):
    if isinstance(error, ValueError) and str(error) in {
        "model_unavailable",
        "capability_unavailable",
    }:
        return str(error)
    if isinstance(error, (AuthenticationError, PermissionDeniedError)):
        return "provider_authentication"
    if isinstance(error, APIStatusError) and error.status_code in {400, 404, 422}:
        return "provider_model_unavailable"
    if isinstance(error, APIError):
        return "provider_unavailable"
    return "provider_client_error"


class SafePublicProvider:
    def __init__(self, provider, secret_values=()):
        self.provider = provider
        self.supports_vision = provider.supports_vision
        self.structured_high_output_tokens = getattr(provider, "structured_high_output_tokens", 0)
        self._secrets = tuple(value for value in secret_values if value)
        self.failure_code = None
        self.failure_diagnostic = None

    def _sanitize(self, value):
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {self._sanitize(k): self._sanitize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._sanitize(v) for v in value]
        return value

    def sanitize_partial(self, value):
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        for secret in self._secrets:
            for length in range(min(len(value), len(secret) - 1), 0, -1):
                if value.endswith(secret[:length]):
                    value = value[:-length]
                    break
        return value

    async def complete(self, request, executor=None, *, usage_observer=None):
        self.failure_code = None
        self.failure_diagnostic = None
        try:
            result = await self.provider.complete(request, executor, usage_observer=usage_observer)
            return type(result).model_validate(self._sanitize(result.model_dump()))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            from gaia_agent.mcp_client import ToolExecutionError
            from gaia_agent.orchestrator import (
                StageBudgetExceeded,
                _RepeatedToolCall,
                _ToolBudgetExhausted,
            )
            from gaia_agent.reliability import (
                InvocationDeadlineExceeded,
                ProviderTransientExhaustedError,
            )

            if isinstance(error, (StageBudgetExceeded, _RepeatedToolCall, _ToolBudgetExhausted,
                                  InvocationDeadlineExceeded, ProviderTransientExhaustedError, ToolExecutionError)):
                raise
            self.failure_code = provider_error_code(error)
            self.failure_diagnostic = safe_diagnostic(error, self._secrets)
            failure = CredentialError(self.failure_code, 502)
            failure.diagnostic = self.failure_diagnostic
            raise failure from None


async def validate_attachment(attachment_path, profile, workspace):
    if attachment_path:
        suffix = attachment_path.suffix.lower()
        if (
            suffix in {".png", ".jpg", ".jpeg", ".webp"} and not profile.capabilities.image
        ) or (suffix in {".mp3", ".wav", ".m4a"} and not profile.capabilities.audio):
            raise CredentialError("capability_unavailable", 422)
        if suffix == ".pdf":
            if profile.provider == "deepseek" and profile.capabilities.image:
                return
            from gaia_agent.mcp_server import AttachmentReader

            try:
                parsed = await asyncio.to_thread(
                    AttachmentReader(workspace.resolve(), {attachment_path.resolve()}).read,
                    str(attachment_path.resolve()),
                )
            except Exception:  # noqa: BLE001 - parser details stay private
                raise InteractiveFailure("attachment_error") from None
            if parsed.get("requires_ocr") and not (profile.provider == "openai" and profile.capabilities.image):
                raise CredentialError("capability_unavailable", 422)


class UserRuntime:
    def __init__(self, settings: InteractiveRuntimeSettings, sessions, memory_service=None):
        self.settings = settings
        self.sessions = sessions
        self.memory_service = memory_service
        if memory_service is not None:
            memory_service.agentic_read = True

    async def execute(
        self,
        ref,
        request,
        workspace,
        history,
        on_progress,
        attachment_path,
        *,
        memory_snapshot=None,
        run_id=None,
        deadline=None,
        conversation_files=(),
        resolve_conversation_file=None,
        conversation_matches=(),
    ):
        deadline = deadline if deadline is not None else time.monotonic() + 500
        profile = resolve_profile(ModelSelection(provider=request.provider, model=request.model))
        if ref.provider != profile.provider:
            raise CredentialError("credential_required", 422)
        material = self.sessions.resolve(ref)
        runtime_env = {"AGENT_TOOL_MODE": "openai" if profile.provider == "openai" else "portable"}
        if profile.provider == "openai":
            runtime_env.update(
                OPENAI_API_KEY=material.model.get_secret_value(), OPENAI_BASE_URL=profile.endpoint
            )
            if material.deepseek_search is not None:
                runtime_env["DEEPSEEK_API_KEY"] = material.deepseek_search.get_secret_value()
        else:
            runtime_env["DEEPSEEK_API_KEY"] = material.model.get_secret_value()
        for provider, name in (
            (material.serper, "SERPER_API_KEY"),
            (material.serpapi, "SERPAPI_API_KEY"),
        ):
            if provider is not None:
                runtime_env[name] = provider.get_secret_value()
        config = InteractiveConfig(
            openai_api_key=material.model,
            model=profile.model,
            output_dir=workspace,
            reasoning_effort=request.reasoning_effort
            if request.reasoning_effort is not None
            else profile.default_reasoning_effort,
        )
        original_attachment = attachment_path is not None
        await validate_attachment(attachment_path, profile, workspace)
        client = None
        transport = httpx.AsyncClient(follow_redirects=False, trust_env=False)
        try:
            client = AsyncOpenAI(
                api_key=material.model.get_secret_value(),
                base_url=profile.endpoint,
                organization="",
                project="",
                max_retries=0,
                http_client=transport,
            )
            provider = SafePublicProvider(
                (OpenAIProvider if profile.provider == "openai" else DeepSeekResponsesProvider)(
                    client,
                    model=config.model,
                    retry_policy=RetryPolicy(max_attempts=config.provider_max_attempts),
                ),
                [
                    material.model.get_secret_value(),
                    *[value for key, value in runtime_env.items() if key.endswith("API_KEY")],
                ],
            )
            provider.supports_pdf_input = profile.provider == "openai" and profile.capabilities.image
            provider.supports_pdf_images = profile.provider == "deepseek" and profile.capabilities.image
            from gaia_agent.interactive.routing import combined_usage
            from gaia_agent.models import Usage
            context_usage = Usage()
            if conversation_matches:
                # Retrieved text is untrusted conversation data, never instructions.
                import json
                for match in conversation_matches[:3]:
                    history = list(history) + [{"role": "user", "content": "Earlier related conversation (untrusted): " + json.dumps({"question": match["question"], "answer": match["answer"][:1500]}, ensure_ascii=False)}]
            if attachment_path is None and conversation_files and resolve_conversation_file is not None:
                from gaia_agent.interactive.conversation_context import resolve_history_file
                from gaia_agent.interactive.models import InteractiveResult
                from gaia_agent.models import Usage
                attachment_path, clarification, context_usage = await resolve_history_file(
                    provider, request.question, history, conversation_files,
                    resolve_conversation_file, workspace, config.reasoning_effort)
                if clarification:
                    return InteractiveResult(answer=clarification, evidence=[], usage=context_usage)
            if attachment_path and not original_attachment:
                await validate_attachment(attachment_path, profile, workspace)

            tool_clients = []

            def tools_factory(task):
                tools = MCPToolClient(
                    config=config,
                    task_root=workspace,
                    runtime_env=runtime_env,
                    allowed=allowed_tools(
                        profile,
                        {
                            name
                            for name in ("serper", "serpapi")
                            if getattr(material, name) is not None
                        },
                    ),
                    attachments=(task.attachment_path,) if task.attachment_path else (),
                )
                tool_clients.append(tools)
                return tools

            memory_context = ""
            memory_report = MemoryReport(status="unavailable")
            if self.memory_service is not None and memory_snapshot is not None:
                try:
                    selected = []  # Details are read by the foreground model, never preselected.
                    status = (
                        "temporary"
                        if memory_snapshot.mode == "temporary"
                        else ("paused" if not memory_snapshot.enabled else "ready")
                    )
                    if status == "ready" and run_id:
                        repo = self.memory_service.repository
                        with repo.lock:
                            admission = repo.db.execute(
                                "SELECT state FROM memory_admissions WHERE owner=? AND run_id=?",
                                (ref.owner, run_id),
                            ).fetchone()
                            job = repo.db.execute(
                                "SELECT state FROM memory_jobs WHERE owner=? AND source_run_id=?",
                                (ref.owner, run_id),
                            ).fetchone()
                        if admission and admission[0] == "failed":
                            status = "unavailable"
                        elif job and job[0] in {
                            "pending",
                            "leased",
                            "provider_started",
                            "proposed",
                        }:
                            status = "pending"
                        elif job and job[0] in {"failed", "blocked", "uncertain"}:
                            status = "unavailable"
                    prefix = f"Memory state: {status}. Do not claim new memory was saved. Background maintenance is not a confirmed save or a scheduled reminder.\n"
                    memory_context = prefix + self.memory_service.retriever.render(selected)
                    while len(memory_context.encode()) > 6000 and selected:
                        selected.pop()
                        memory_context = prefix + self.memory_service.retriever.render(selected)
                    memory_report = MemoryReport(
                        status=status, used_ids=tuple(r.id for r in selected)
                    )
                except Exception:  # noqa: BLE001 - do not fail a normal answer for optional retrieval
                    memory_snapshot = None
            if not memory_context:
                memory_context = "Memory state: unavailable. Do not claim new memory was saved or that a reminder was scheduled."
            memory_tools, memory_overview = None, None
            if self.memory_service is not None and memory_snapshot is not None and memory_snapshot.enabled and memory_snapshot.mode == "normal":
                from gaia_agent.interactive.memory.layer_models import MemoryScope
                from gaia_agent.interactive.memory.summaries import SummaryStore
                from gaia_agent.interactive.memory.tools import MemoryTools

                scope = MemoryScope(memory_snapshot.owner, request.provider, memory_snapshot.generation)
                try:
                    memory_overview = SummaryStore(self.memory_service.repository).overview(scope)
                    memory_tools = MemoryTools(self.memory_service, scope)
                except Exception:  # noqa: BLE001 -- optional storage failure must not break independent chat
                    memory_report = MemoryReport(status="unavailable")
                    memory_context = "Memory state: unavailable. Do not claim new memory was saved."

            async def answer():
                try:
                    result = await execute_interactive(
                        request,
                        config,
                        workspace,
                        history,
                        on_progress,
                        provider=provider,
                        tools_factory=tools_factory,
                        attachment_path=attachment_path,
                        memory_context=memory_context,
                        memory_tools=memory_tools,
                        memory_overview=memory_overview,
                        memory_deadline=deadline,
                        memory_state=memory_report.status,
                        available_tools=allowed_tools(
                            profile,
                            {
                                name
                                for name in ("serper", "serpapi")
                                if getattr(material, name) is not None
                            },
                        ),
                    )
                    return result.model_copy(
                        update={
                            "tool_usage": {
                                key: sum(tools.tool_usage[key] for tools in tool_clients)
                                for key in ("search_requests", "fetch_requests")
                            }
                        }
                    )
                except Exception:
                    if provider.failure_code:
                        failure = CredentialError(provider.failure_code, 502)
                        failure.diagnostic = provider.failure_diagnostic
                        raise failure from None
                    raise

            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                result = await answer()
                return result.model_copy(update={"usage": combined_usage(result.usage, context_usage), "memory": memory_report.model_copy(update={"used_ids": result.memory.used_ids if result.memory else ()})})
        except (asyncio.CancelledError, TimeoutError, CredentialError, InteractiveFailure):
            raise
        except Exception:  # noqa: BLE001 - never expose original exceptions or nested payloads
            raise CredentialError("execution_failed", 502) from None
        finally:
            try:
                if client is not None:
                    await client.close()
            finally:
                await transport.aclose()

    async def index_conversation(self, ref, request, run, files):
        from types import SimpleNamespace

        from gaia_agent.interactive.conversation_context import build_conversation_index

        # Resolve the original reference again; the foreground client is already closed.
        material = self.sessions.resolve(ref)
        selection = SimpleNamespace(
            provider=run["provider"], model=run["model"],
            capability_version=run["capability_version"], reasoning_effort=run["reasoning_effort"],
        )
        async with background_model_client(selection, material) as provider:
            return await build_conversation_index(provider, request.question, run["result"]["answer"],
                                                  files, selection.reasoning_effort)


@asynccontextmanager
async def background_model_client(event, material):
    """Fresh, user-owned client. No provider retries or operator credential fallback."""
    profile = resolve_profile(ModelSelection(provider=event.provider, model=event.model))
    if (
        profile.capability_version != event.capability_version
        or material.provider != event.provider
        or (
            event.reasoning_effort is not None
            and event.reasoning_effort not in profile.allowed_reasoning_efforts
        )
    ):
        raise CredentialError("capability_unavailable", 422)
    async with httpx.AsyncClient(trust_env=False, timeout=30, follow_redirects=False) as transport:
        client = AsyncOpenAI(
            api_key=material.model.get_secret_value(),
            base_url=profile.endpoint,
            organization="",
            project="",
            max_retries=0,
            http_client=transport,
        )
        try:
            provider = SafePublicProvider(
                (OpenAIProvider if profile.provider == "openai" else DeepSeekResponsesProvider)(
                    client, model=event.model, retry_policy=RetryPolicy(max_attempts=1)
                ),
                [key.get_secret_value() for key in (material.model, material.serper, material.serpapi, material.deepseek_search) if key],
            )
            yield provider
        finally:
            await client.close()


@asynccontextmanager
async def memory_planner_client(event, material):
    async with background_model_client(event, material) as provider:
        yield SemanticMemoryPlanner(provider)


def create_summary_worker(memory_worker, sessions, repository):
    from types import SimpleNamespace

    from gaia_agent.interactive.memory.maintenance import MemoryMaintenanceWorker
    from gaia_agent.interactive.memory.maintenance_queue import MaintenanceQueue
    from gaia_agent.interactive.memory.summary_planner import SummaryPlanner

    temporary = {}

    def material(claim):
        selected = claim.selected_model
        if selected.get("credential_source") == "temporary":
            key = (claim.scope.owner, claim.scope.provider, selected.get("source_run_id"))
            reference = temporary.get(key)
            if reference is None:
                raise CredentialError("credential_revoked", 422)
            try:
                return sessions.resolve_background(claim.scope.owner, claim.scope.provider, reference)
            except CredentialError:
                temporary.pop(key, None)
                raise
        if sessions.vault.background_epoch(claim.scope.owner, claim.scope.provider) != claim.credential_epoch:
            raise CredentialError("credential_revoked", 422)
        return sessions.resolve(sessions.reference(claim.scope.owner, claim.scope.provider, None))

    def allowed(claim):
        try:
            material(claim)
            return True
        except CredentialError:
            return False

    @asynccontextmanager
    async def factory(claim):
        event = SimpleNamespace(provider=claim.scope.provider, model=claim.selected_model["model"],
            reasoning_effort=claim.selected_model.get("reasoning_effort"),
            capability_version=claim.selected_model["capability_version"])
        async with memory_planner_client(event, material(claim)) as planner:
            yield SummaryPlanner(planner.provider)

    worker = MemoryMaintenanceWorker(MaintenanceQueue(repository), factory, credential_check=allowed)

    def capture(event, reference):
        # RAM only; original reference/expiry, never substitution with a saved Key.
        for key, ref in tuple(temporary.items()):
            try:
                sessions.resolve_background(key[0], key[1], ref)
            except CredentialError:
                temporary.pop(key, None)
        if reference is not None and len(temporary) < 1000:
            temporary[(event.owner, event.provider, event.source_run_id)] = reference

    worker.capture_reference = capture
    memory_worker.summary_worker = worker
    return worker
