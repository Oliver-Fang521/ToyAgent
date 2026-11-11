import asyncio
import json
import logging
import shutil
import time
from contextlib import nullcontext
from pathlib import Path

from gaia_agent.interactive.composition import InteractiveFailure
from gaia_agent.interactive.credential_vault import CredentialError
from gaia_agent.interactive.failures import failure_details, safe_diagnostic
from gaia_agent.interactive.memory.models import MemoryFailure
from gaia_agent.interactive.models import InteractiveRequest
from gaia_agent.interactive.public_results import public_result
from gaia_agent.interactive.store import ConversationStore
from gaia_agent.interactive.streaming import observe_first_model_text
from gaia_agent.reliability import invocation_deadline_scope


class RunService:
    def __init__(
        self,
        store: ConversationStore,
        root: Path,
        execute,
        *,
        credential_sessions=None,
        memory_service=None,
        memory_worker=None,
        conversation_indexer=None,
    ):
        self.store = store
        self.root = root
        self.execute = execute
        self.tasks: dict[str, asyncio.Task] = {}
        self.answer_previews: dict[str, str] = {}
        self.slots = asyncio.Semaphore(2)
        self.conversation_indexer = conversation_indexer
        self.index_slots = asyncio.Semaphore(2)
        self.index_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self.credential_sessions = credential_sessions
        self.memory_service = memory_service
        self.memory_worker = memory_worker

    def start(
        self,
        owner: str,
        cid: str,
        request: InteractiveRequest,
        idem: str,
        *,
        credential_ref=None,
        credential_handle=None,
    ) -> dict:
        existing = self.store.lookup(owner, cid, request, idem)
        if existing is not None:
            return existing
        if self.memory_service is not None:
            self.memory_service.check_admission(owner)
        self.store.check_provider(owner, cid, request.provider)
        if self.credential_sessions is not None:
            credential_ref = credential_ref or self.credential_sessions.reference(
                owner, request.provider, credential_handle
            )
            if credential_ref.owner != owner or credential_ref.provider != request.provider:
                raise CredentialError("credential_required", 422)
            self.credential_sessions.resolve(credential_ref)
        attachment = (
            self.store.attachment(owner, cid, request.attachment_id)
            if request.attachment_id
            else None
        )
        run = self.store.reserve(owner, cid, request, idem)
        request = request.model_copy(update={"reasoning_effort": run["reasoning_effort"]})
        if run["status"] == "queued" and run["id"] not in self.tasks:
            if self.memory_service is not None and request.memory_mode == "normal":
                try:
                    if self.memory_service.repository.settings(owner).enabled:
                        self.memory_service.repository.register_turn(owner, run["id"])
                except Exception:  # noqa: BLE001 - optional memory must not block a submitted answer
                    logging.getLogger("gaia_agent.interactive").warning("agent.memory_unavailable")
            if self.memory_worker is not None:
                self.memory_worker.admit(owner, run, request, credential_ref)
            self.tasks[run["id"]] = asyncio.create_task(
                self._execute(owner, cid, run["id"], request, attachment, credential_ref)
            )
            self.tasks[run["id"]].add_done_callback(lambda _: self.tasks.pop(run["id"], None))
        return run

    async def _execute(self, owner, cid, identifier, request, attachment, credential_ref):
        workspace = self.root / identifier
        milestones = set()

        def mark_once(name):
            if name not in milestones:
                milestones.add(name)
                self.store.progress(identifier, name, {})

        def progress(event, payload):
            if event == "answer_preview":
                value = payload.get("text")
                if isinstance(value, str) and len(value) <= 12000:
                    self.answer_previews[identifier] = value
                    if value.strip():
                        mark_once("first_answer_ready")
            else:
                self.store.progress(identifier, event, payload)
        try:
            async with self.slots:
                if self.credential_sessions is not None:
                    self.credential_sessions.resolve(credential_ref)
                self.store.update(identifier, status="running")
                workspace.mkdir(parents=True, exist_ok=True)
                registered = None
                if attachment:
                    registered = workspace / ("attachment" + attachment.suffix)
                    await asyncio.to_thread(shutil.copyfile, attachment, registered)
                deadline = time.monotonic() + 500
                snapshot = None
                if self.memory_service is not None:
                    try:
                        snapshot = self.memory_service.snapshot(
                            owner, request.provider, request.memory_mode
                        )
                    except MemoryFailure as error:
                        if error.code == "memory_busy":
                            raise
                    except Exception:  # noqa: BLE001 - storage outage degrades personalization only
                        logging.getLogger("gaia_agent.interactive").warning(
                            "agent.memory_unavailable"
                        )
                lease_context = (
                    self.memory_service.lease(snapshot, deadline)
                    if snapshot is not None
                    else nullcontext(None)
                )
                with lease_context as lease, invocation_deadline_scope(lease), observe_first_model_text(
                    lambda: mark_once("first_model_text")
                ):
                    async with asyncio.timeout(max(0, deadline - time.monotonic())):
                        args = (
                            request,
                            workspace,
                            self.store.history(owner, cid),
                            progress,
                            registered,
                        )
                        options = (
                            {
                                "memory_snapshot": snapshot,
                                "run_id": identifier,
                                "deadline": deadline,
                            }
                            if self.memory_service is not None
                            else {}
                        )
                        import inspect
                        accepts_context = "conversation_files" in inspect.signature(self.execute).parameters
                        if self.credential_sessions is not None and accepts_context:
                            options["conversation_files"] = self.store.conversation_attachments(owner, cid)
                            options["resolve_conversation_file"] = lambda file_id: self.store.bind_run_attachment(owner, cid, identifier, file_id)
                            options["conversation_matches"] = self.store.search_conversation(owner, cid, request.question)
                        result = (
                            await self.execute(credential_ref, *args, **options)
                            if self.credential_sessions is not None
                            else await self.execute(*args, **options)
                        )
                        if lease is not None:
                            lease.ensure_open()
                if result.answer.strip():
                    mark_once("first_answer_ready")
                self.store.update(
                    identifier,
                    status="completed",
                    result=public_result(result.model_dump(mode="json"), workspace),
                )
                if self.conversation_indexer is not None and request.memory_mode == "normal":
                    files = [{"name": item["name"], "type": item["type"]}
                             for item in options.get("conversation_files", ())[:20]]
                    key = (owner, identifier)
                    task = asyncio.create_task(self._index_completed(owner, cid, identifier, request,
                                                                     credential_ref, files, snapshot))
                    self.index_tasks[key] = task
                    task.add_done_callback(lambda _: self.index_tasks.pop(key, None))
        except asyncio.CancelledError:
            self.store.update(identifier, status="cancelled", error="cancelled")
            raise
        except TimeoutError:
            self._fail(owner, identifier, "timeout")
        except InteractiveFailure as error:
            self._fail(owner, identifier, str(error))
        except CredentialError as error:
            self._fail(owner, identifier, error.code, getattr(error, "diagnostic", None))
        except Exception as error:  # noqa: BLE001 - private execution errors are opaque at the API boundary
            self._fail(owner, identifier, "execution_failed", safe_diagnostic(error))
        finally:
            self.answer_previews.pop(identifier, None)
            if self.memory_worker is not None:
                try:
                    self.memory_worker.release(
                        owner,
                        identifier,
                        self.store.run(owner, identifier)["status"] == "completed",
                    )
                except Exception:  # noqa: BLE001 - maintenance never invalidates completed answer
                    logging.getLogger("gaia_agent.interactive").warning("agent.memory_unavailable")
            if workspace.exists():
                await asyncio.to_thread(shutil.rmtree, workspace)

    async def _index_completed(self, owner, cid, identifier, request, credential_ref, files, snapshot):
        """Optional post-processing. Foreground slots and completed results are independent."""
        try:
            async with self.index_slots:
                run = self.store.run(owner, identifier)
                if run["status"] != "completed":
                    return
                deadline = time.monotonic() + 10
                scope = self.memory_service.lease(snapshot, deadline) if snapshot else nullcontext(None)
                with scope as lease, invocation_deadline_scope(lease):
                    async with asyncio.timeout(10):
                        try:
                            index = await self.conversation_indexer(credential_ref, request, run, files)
                        except CredentialError as error:
                            # Only retry an explicit rate rejection. A timeout may already have incurred usage.
                            if (getattr(error, "diagnostic", None) or {}).get("http_status") != 429:
                                raise
                            await asyncio.sleep(1)
                            if lease is not None:
                                lease.ensure_open()
                            index = await self.conversation_indexer(credential_ref, request, run, files)
                        if lease is not None:
                            lease.ensure_open()
                        self.credential_sessions.resolve(credential_ref)
                        self.store.save_conversation_index(owner, cid, identifier, index.summary,
                                                           index.keywords, usage=index.usage)
        except Exception:  # noqa: BLE001 - optional index failure cannot invalidate a delivered answer
            logging.getLogger("gaia_agent.interactive").warning("agent.conversation_index_unavailable")

    def get(self, owner: str, identifier: str) -> dict:
        result = self.store.run(owner, identifier)
        if result["status"] == "running" and identifier in self.answer_previews:
            result["partial_answer"] = self.answer_previews[identifier]
        if self.memory_worker is not None:
            result["memory_maintenance"] = self.memory_worker.status(owner, identifier)
        return result

    def _fail(self, owner, identifier, code, diagnostic=None):
        failure = failure_details(code, self.get(owner, identifier)["progress"], diagnostic)
        self.store.update(identifier, status="failed", error=failure["code"], failure=failure)
        logging.getLogger("gaia_agent.interactive").warning(
            "agent.run_failed %s", json.dumps({"task_id": identifier, **failure})
        )

    async def cancel(self, owner: str, identifier: str) -> dict:
        run = self.get(owner, identifier)
        if run["status"] in {"queued", "running"}:
            self.store.update(identifier, status="cancelled", error="cancelled")
            task = self.tasks.get(identifier)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        return self.get(owner, identifier)

    async def shutdown(self):
        tasks = [*self.tasks.values(), *self.index_tasks.values()]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel_owner(self, owner: str):
        jobs = [task for (job_owner, _), task in self.index_tasks.items() if job_owner == owner]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        identifiers = [
            row[0]
            for row in self.store.db.execute(
                "SELECT id FROM runs WHERE owner=? AND status IN ('queued','running')", (owner,)
            )
        ]
        for identifier in identifiers:
            await self.cancel(owner, identifier)
