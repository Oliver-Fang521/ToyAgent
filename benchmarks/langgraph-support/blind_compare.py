"""Compare a question-only, tool-free baseline with normal ToyAgent on public cases.

Secrets are read from the environment, never written to results. Runs require
explicit --execute. Both arms use the same model. Only ToyAgent uses tools;
reference answers are not read by either evaluated system.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

from openai import APIError, AsyncOpenAI
from pydantic import SecretStr

from gaia_agent.interactive.catalog import ModelSelection, resolve_profile
from gaia_agent.interactive.composition import execute_interactive
from gaia_agent.interactive.models import InteractiveConfig, InteractiveRequest
from gaia_agent.interactive.composition import InteractiveFailure
from gaia_agent.interactive.failures import EVENT_STAGES, KNOWN_CODES, current_stage, safe_diagnostic
from gaia_agent.interactive.routing import combined_usage
from gaia_agent.interactive.tool_policy import allowed_tools
from gaia_agent.mcp_client import MCPToolClient
from gaia_agent.providers import OpenAIProvider
from gaia_agent.providers.base import ModelRequest
from gaia_agent.models import TerminalErrorCategory
from gaia_agent.reliability import ProviderTransientExhaustedError, TRANSIENT_PROVIDER_ERROR_NAMES

ROOT = Path(__file__).resolve().parent
MODEL = "gpt-6.1-terra"
EFFORT = "medium"
SAFE_TOOLS = {"web_search", "read_webpage", "execute_python", "read_attachment", "transcribe_audio", "submit_answer"}
SAFE_PROGRESS = set(EVENT_STAGES) | {"routing_completed", "tool_call_started", "tool_call_completed", "tool_call_failed"}


def failure_record(case, arm, error, elapsed, diagnostic):
    progress = []
    for event in diagnostic['progress']:
        name = event.get('event')
        if name not in SAFE_PROGRESS:
            continue
        clean = {'event': name}
        if event.get('tool') in SAFE_TOOLS:
            clean['tool'] = event['tool']
        if event.get('route') in {'direct', 'research', 'clarify'}:
            clean['route'] = event['route']
        progress.append(clean)
    code = str(error) if isinstance(error, InteractiveFailure) else None
    allowed_codes = KNOWN_CODES | {item.value for item in TerminalErrorCategory} | {'capability_unavailable'}
    category = code if code in allowed_codes else 'execution_failed'
    if isinstance(error, TimeoutError):
        category = 'timeout'
    elif isinstance(error, ProviderTransientExhaustedError):
        category = 'retry_exhausted'
    elif isinstance(error, APIError):
        category = 'model_api_error'
    info = safe_diagnostic(error)
    info.pop('request_id', None)
    row = {'case_id': case['id'], 'split': case['split'], 'arm': arm,
           'status': 'failed', 'error_type': info.pop('exception_type'),
           'error_category': category, 'stage': current_stage(progress),
           'progress': progress, 'model': MODEL, 'reasoning_effort': EFFORT,
           'duration_seconds': round(elapsed, 3), 'cost_usd': None, **info,
           'tool_trace': [{'tool': t['tool'], 'status': t.get('status', 'started')}
                          for client in diagnostic['clients'] for t in client.trace
                          if t.get('tool') in SAFE_TOOLS]}
    if isinstance(error, ProviderTransientExhaustedError):
        if error.retry_category in TRANSIENT_PROVIDER_ERROR_NAMES | {'provider_transient'}:
            row['retry_category'] = error.retry_category
        if type(error.attempt_count) is int and 1 <= error.attempt_count <= 100:
            row['attempt_count'] = error.attempt_count
    return row


class TracedClient(MCPToolClient):
    async def call(self, name, arguments):
        entry = {"tool": name, "arguments": arguments, "status": "started"}
        self.trace.append(entry)
        try:
            result = await super().call(name, arguments)
            entry["result"] = json.dumps(result, ensure_ascii=False)[:16000]
            entry["status"] = "completed"
            return result
        except Exception as exc:
            entry["error_type"] = type(exc).__name__
            entry["status"] = "failed"
            raise


async def run_arm(case, arm, material, diagnostic=None):
    started = time.monotonic()
    model_key = material["OPENAI_API_KEY"]
    config = InteractiveConfig(openai_api_key=SecretStr(model_key), model=MODEL,
                               reasoning_effort=EFFORT)
    runtime_env = {"AGENT_TOOL_MODE": "openai", "OPENAI_API_KEY": model_key,
                   "OPENAI_BASE_URL": "https://api.openai.com/v1"}
    runtime_env.update({k: v for k, v in material.items() if k != "OPENAI_API_KEY" and v})
    profile = resolve_profile(ModelSelection(provider="openai", model=MODEL))
    configured = {name for name, env in (("serper", "SERPER_API_KEY"),
                                        ("serpapi", "SERPAPI_API_KEY")) if material.get(env)}
    allowed = allowed_tools(profile, configured)
    diagnostic = diagnostic if diagnostic is not None else {'clients': [], 'progress': []}
    clients = diagnostic['clients']
    progress = diagnostic['progress']
    with tempfile.TemporaryDirectory(prefix="toyagent-public-eval-", dir=os.path.realpath(tempfile.gettempdir())) as directory:
        workspace = Path(directory)

        def tools_factory(task=None):
            client = TracedClient(config=config, task_root=workspace, runtime_env=runtime_env,
                                   allowed=allowed)
            client.trace = []
            clients.append(client)
            return client

        async with AsyncOpenAI(api_key=model_key, max_retries=0) as client:
            provider = OpenAIProvider(client, model=MODEL)
            if arm == "baseline":
                request = ModelRequest(
                    instructions="Answer the user's question directly. Be accurate and state uncertainty when needed. Reply in the user's language.",
                    input_items=({"role": "user", "content": case["question"]},),
                    output_schema={"type": "object", "properties": {"answer": {"type": "string"}},
                                   "required": ["answer"], "additionalProperties": False},
                    allowed_tools=(), max_tool_calls=0, reasoning_effort=EFFORT,
                )
                response = await provider.complete(request)
                answer = (response.parsed or {}).get("answer")
                usage = response.usage
                events = []
                status = "completed" if response.response_status == "completed" and answer else "failed"
                evidence = []
            else:
                request = InteractiveRequest(question=case["question"], provider="openai", model=MODEL,
                                             reasoning_effort=EFFORT, memory_mode="temporary")
                result = await execute_interactive(request, config, workspace, history=[],
                    on_progress=lambda event, payload: progress.append({"event": event, **payload}),
                    provider=provider, tools_factory=tools_factory, available_tools=allowed)
                answer, usage, status = result.answer, result.usage, "completed"
                evidence = [item.model_dump(mode="json") for item in result.evidence]
                events = [event.get("tool") for event in progress if event["event"] == "tool_call_started"]
            # The orchestrator already includes MCP usage; a direct baseline does not.
            if arm == "baseline":
                for tool in clients:
                    usage = combined_usage(usage, tool.usage)
            return {"case_id": case["id"], "split": case["split"], "arm": arm,
                    "status": status, "answer": answer, "evidence": evidence,
                    "model": MODEL, "reasoning_effort": EFFORT,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "route": next((p.get("route") for p in progress if p["event"] == "routing_completed"), None),
                    "tool_trace": [entry for tool in clients for entry in tool.trace],
                    "usage": usage.model_dump(), "tool_calls": events,
                    "tool_usage": {k: sum(t.tool_usage[k] for t in clients)
                                   for k in ("search_requests", "fetch_requests")},
                    "cost_usd": None}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--parallel", type=int, default=1, choices=(1, 2, 3, 4))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    cases = json.loads((ROOT / "public/cases.json").read_text())
    selected = [case for case in cases if not args.case or case["id"] in args.case]
    if not selected or (args.case and {c["id"] for c in selected} != set(args.case)):
        raise ValueError("unknown or empty case selection")
    if not args.execute:
        print(json.dumps({"mode": "dry-run", "model": MODEL, "effort": EFFORT,
                          "cases": [case["id"] for case in selected]}))
        return
    if args.output is None or (args.output.exists() and not args.resume):
        raise ValueError("provide a new output file, or explicitly resume")
    completed = set()
    if args.output.exists():
        for line in args.output.read_text().splitlines():
            row = json.loads(line)
            key = (row["case_id"], row["arm"])
            if key in completed:
                raise ValueError("duplicate existing result")
            completed.add(key)
    material = {key: os.environ[key] for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "SERPER_API_KEY", "SERPAPI_API_KEY") if os.environ.get(key)}
    if "OPENAI_API_KEY" not in material:
        raise ValueError("OPENAI_API_KEY required")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(args.parallel)
    async def run_case(case):
      async with semaphore:
        for arm in ("baseline", "toyagent"):
            if (case["id"], arm) in completed:
                continue
            started = time.monotonic()
            diagnostic = {'clients': [], 'progress': []}
            try:
                async with asyncio.timeout(500):
                    row = await run_arm(case, arm, material, diagnostic)
            except Exception as exc:
                row = failure_record(case, arm, exc, time.monotonic()-started, diagnostic)
            with args.output.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(case["id"], arm, row["status"], row["duration_seconds"], flush=True)
    await asyncio.gather(*(run_case(case) for case in selected))


if __name__ == "__main__":
    asyncio.run(main())
