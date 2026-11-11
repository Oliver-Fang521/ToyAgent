import time

from gaia_agent.interactive.routing import combined_usage
from gaia_agent.models import Usage
from gaia_agent.providers import complete_with_usage_observer
from gaia_agent.reliability import ensure_current_invocation_allowed

from .models import MemoryReport


class MemoryContextProvider:
    def __init__(self, provider, context, *, validator=None, max_bytes=6000):
        self.provider = provider
        self.context = context if len(context.encode()) <= max_bytes else ""
        self.validator = validator
        self.supports_vision = getattr(provider, "supports_vision", False)
        self.supports_pdf_input = getattr(provider, "supports_pdf_input", False)
        self.supports_pdf_images = getattr(provider, "supports_pdf_images", False)
        self.structured_high_output_tokens = getattr(provider, "structured_high_output_tokens", 0)

    async def complete(self, request, executor=None, *, usage_observer=None):
        ensure_current_invocation_allowed()
        if self.validator:
            self.validator()
        if self.context:
            request = request.model_copy(
                update={
                    "input_items": ({"role": "user", "content": self.context}, *request.input_items)
                }
            )
        return await complete_with_usage_observer(
            self.provider, request, executor, usage_observer=usage_observer
        )


async def run_with_memory(
    execute, snapshot, extractor, current_text, run_id, deadline, *, memory_service
):
    result = await execute()
    if snapshot is None or memory_service is None:
        return result.model_copy(update={"memory": MemoryReport(status="unavailable")})
    extra_usage = Usage()

    def observe(usage):
        nonlocal extra_usage
        extra_usage = combined_usage(extra_usage, usage)

    used = ()
    try:
        used = memory_service.retriever.select(snapshot.records, current_text, time.time())
        ensure_current_invocation_allowed()
        report = await memory_service.remember(
            snapshot, run_id, current_text, extractor, deadline=deadline, usage_observer=observe
        )
    except Exception:  # noqa: BLE001 - account maintenance is optional, safe status only
        report = MemoryReport(status="unavailable")
    return result.model_copy(
        update={
            "usage": combined_usage(result.usage, extra_usage),
            "memory": report.model_copy(update={"used_ids": tuple(r.id for r in used)}),
        }
    )
