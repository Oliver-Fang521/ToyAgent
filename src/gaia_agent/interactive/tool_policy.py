"""Expose only tools whose backend can actually perform the operation."""

from gaia_agent.interactive.catalog import ModelProfile


def allowed_tools(profile: ModelProfile, configured_search: set[str]) -> frozenset[str]:
    tools = {"read_webpage", "read_attachment", "submit_answer"}
    if (
        profile.provider == "deepseek"
        or profile.capabilities.hosted_search
        or configured_search & {"serper", "serpapi"}
    ):
        tools.add("web_search")
    if profile.capabilities.hosted_python:
        tools.add("execute_python")
    if profile.capabilities.audio:
        tools.add("transcribe_audio")
    return frozenset(tools)
