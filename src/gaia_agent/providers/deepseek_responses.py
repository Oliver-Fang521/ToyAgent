"""Stateless DeepSeek Responses transport with explicit capability checks."""

from typing import Any

from gaia_agent.providers.base import ModelRequest, ModelResponse, ToolExecutor, UsageObserver
from gaia_agent.providers.openai import OpenAIProvider


class DeepSeekResponsesProvider(OpenAIProvider):
    # High-effort responses share the output allowance with reasoning tokens.
    structured_high_output_tokens = 20_000
    def __init__(self, client: Any, model: str = "deepseek-flash", **kwargs: Any) -> None:
        if model not in {"deepseek-flash", "deepseek-v4-pro"}:
            raise ValueError("model_unavailable")
        self.supports_vision = model == "deepseek-flash"
        super().__init__(client, model, use_previous_response_id=False, **kwargs)

    async def complete(
        self,
        request: ModelRequest,
        executor: ToolExecutor | None = None,
        *,
        usage_observer: UsageObserver | None = None,
    ) -> ModelResponse:
        if not self.supports_vision:
            for item in request.input_items:
                content = item.get("content", ())
                if isinstance(content, (list, tuple)) and any(
                    part.get("type") == "input_image" for part in content
                ):
                    raise ValueError("capability_unavailable")
        return await super().complete(request, executor, usage_observer=usage_observer)

    def _prepare_response_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if any(tool.get("type") != "function" for tool in kwargs.get("tools", ())):
            raise ValueError("capability_unavailable")
        # DeepSeek ignores max_tool_calls; the inherited local loop enforces it.
        effort = kwargs.get("reasoning", {}).get("effort")
        if effort is not None and effort not in {"none", "low", "high", "max"}:
            raise ValueError("reasoning_unavailable")
        return {
            key: value
            for key, value in kwargs.items()
            if key
            in {
                "model",
                "instructions",
                "input",
                "tools",
                "tool_choice",
                "text",
                "max_output_tokens",
                "reasoning",
            }
        }

    async def _create_with_retry(self, **kwargs: Any) -> Any:
        response = await super()._create_with_retry(**kwargs)
        if any(
            item.type not in {"message", "reasoning", "function_call"} for item in response.output
        ):
            raise ValueError("capability_unavailable")
        return response
