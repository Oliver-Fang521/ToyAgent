"""Small public failure contract: never includes exception messages or bodies."""

import re

from openai import APIConnectionError, APIStatusError

from gaia_agent.interactive.credential_vault import SAFE_CODES

EVENT_STAGES = {
    "routing_started": "routing",
    "plan_started": "plan",
    "act_started": "research",
    "correction_started": "correction",
    "verify_started": "verify",
    "submit_started": "submit",
    "evidence_repair_started": "verify",
}
KNOWN_CODES = SAFE_CODES | {
    "timeout",
    "retry_exhausted",
    "malformed_response",
    "critic_rejection",
    "tool_budget_exhausted",
    "loop_detected",
    "attachment_error",
    "no_answer",
    "service_restarted",
}
UPSTREAM_CODES = {
    "invalid_api_key",
    "insufficient_quota",
    "model_not_found",
    "rate_limit_exceeded",
    "unsupported_country_region_territory",
    "context_length_exceeded",
}


def current_stage(events):
    stage = "prepare"
    for item in events:
        stage = EVENT_STAGES.get(item.get("event"), stage)
    return stage


def safe_diagnostic(error, secrets=()):
    name = type(error).__name__
    # Only a short identifier is exposed; never exception text, arguments or traceback.
    if (
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name)
        or any(secret and secret in name for secret in secrets)
    ):
        name = "UnexpectedError"
    result = {"exception_type": name}
    if isinstance(error, APIStatusError):
        if isinstance(error.status_code, int) and 100 <= error.status_code <= 599:
            result["http_status"] = error.status_code
        if isinstance(error.code, str) and error.code in UPSTREAM_CODES:
            result["upstream_code"] = error.code
        request_id = getattr(error, "request_id", None)
        if (
            isinstance(request_id, str)
            and re.fullmatch(r"req_[A-Za-z0-9_-]{1,96}", request_id)
            and not any(s and s in request_id for s in secrets)
        ):
            result["request_id"] = request_id
    elif isinstance(error, APIConnectionError):
        result["exception_type"] = (
            "APITimeoutError" if type(error).__name__ == "APITimeoutError" else "APIConnectionError"
        )
    return result


def failure_details(code, events, diagnostic=None):
    code = code if code in KNOWN_CODES else "execution_failed"
    tool = None
    allowed_tools = {
        "web_search",
        "read_webpage",
        "execute_python",
        "read_attachment",
        "transcribe_audio",
        "submit_answer",
    }
    for event in events:
        if event.get("event") in EVENT_STAGES or event.get("event") == "tool_call_completed":
            tool = None
        elif (
            event.get("event") in {"tool_call_failed", "tool_call_started"}
            and event.get("tool") in allowed_tools
        ):
            tool = event["tool"]
    component = (
        "model"
        if code.startswith("provider_")
        else "credentials"
        if code.startswith("credential_")
        else "validation"
        if code == "critic_rejection"
        else "execution"
    )
    if code == "retry_exhausted":
        for entry in reversed(events):
            if entry.get("event") == "provider_retry_exhausted":
                if entry.get("category") in {"APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError", "provider_transient"} and type(entry.get("attempt_count")) is int and 1 <= entry["attempt_count"] <= 100:
                    component = "model"
                    diagnostic = {"retry_category": entry["category"], "attempt_count": entry["attempt_count"]}
                    tool = None
                break
            if entry.get("event") in EVENT_STAGES:
                break
    if code == "provider_client_error":
        component = "model_client"
    if component == "model" and (diagnostic or {}).get("exception_type") in {
        "TypeError",
        "ValueError",
        "AttributeError",
        "RuntimeError",
        "KeyError",
    }:
        component = "model_client"
    if code in {"malformed_response", "no_answer"}:
        component = "model_output"
    elif code == "attachment_error":
        component = "attachment"
    if tool and code in {"retry_exhausted", "timeout", "loop_detected", "tool_budget_exhausted"}:
        component = "tool"
    if code == "malformed_response" and not diagnostic:
        for item in reversed(events):
            if item.get("event") == "stage_response_diagnostic":
                fields = {
                    "phase": {"initial", "repair"},
                    "code": {"role_mismatch", "evidence_shape", "source_url_missing", "candidate_format", "checked_evidence", "schema"},
                    "response_status": {"completed", "incomplete", "failed", "unknown"},
                    "incomplete_reason": {"none", "max_output_tokens", "content_filter", "other"},
                }
                if all(item.get(key) in values for key, values in fields.items()):
                    diagnostic = {"phase": item["phase"], "validation_code": item["code"], "response_status": item["response_status"], "incomplete_reason": item["incomplete_reason"]}
                    from gaia_agent.diagnostics import safe_model_metrics
                    diagnostic.update(safe_model_metrics(item))
                break
            if item.get("event") in {"act_started", "plan_started", "verify_started", "correction_started"}:
                break
    result = {
        "stage": current_stage(events),
        "code": code,
        "component": component,
        "tool": tool if component == "tool" else None,
        "diagnostic": diagnostic or {},
    }
    if code == "critic_rejection":
        for event in reversed(events):
            if event.get("event") == "validation_rejected" and event.get("reason") in {
                "insufficient_evidence",
                "revise",
            }:
                result["reason"] = event["reason"]
                break
    if component == "tool":
        for event in reversed(events):
            if event.get("event") == "tool_call_failed" and event.get("tool") == tool:
                if event.get("provider") in {"openai", "deepseek", "serper", "serpapi"}:
                    result["provider"] = event["provider"]
                if event.get("category") in {
                    "authentication",
                    "balance",
                    "forbidden",
                    "invalid_input",
                    "rate_limit",
                    "upstream",
                    "timeout",
                    "policy_rejection",
                    "repeated_call",
                    "tool_budget_exhausted",
                }:
                    result["tool_category"] = event["category"]
                break
    return result
