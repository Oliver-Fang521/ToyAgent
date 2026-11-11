"""Bounded local acceptance CLI. Dry-run is the default; no credentials on argv."""

import argparse
import getpass
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx

from gaia_agent.interactive.catalog import ModelSelection, resolve_profile

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/product_acceptance.json"
LIVE_CASES = ("translation", "follow_up", "txt")


def dry_run(provider, model):
    resolve_profile(ModelSelection(provider=provider, model=model))
    cases = json.loads(FIXTURE.read_text())
    if len(cases) != 15 or len({case["id"] for case in cases}) != 15:
        raise ValueError("invalid_acceptance_fixture")
    return cases


def read_key():
    if not sys.stdin.isatty():
        raise ValueError("hidden_tty_required")
    return getpass.getpass("DeepSeek test Key (hidden, temporary only): ")


def sanitize(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {sanitize(k, secrets): sanitize(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, secrets) for v in value]
    return value


class RunLedger:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError("unsafe_ledger_path")
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else []
        if not isinstance(self.entries, list) or len(self.entries) > 6:
            raise ValueError("invalid_ledger")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as stream:
            json.dump(self.entries, stream, ensure_ascii=False)
            temporary = stream.name
        os.replace(temporary, self.path)

    def reserve(self, provider, model, case, cid):
        for entry in self.entries:
            if (entry["provider"], entry["model"], entry["case"]) == (provider, model, case):
                return entry
        if len(self.entries) >= 6 or sum(e["provider"] == provider for e in self.entries) >= 3:
            raise ValueError("budget_exhausted")
        entry = {
            "provider": provider,
            "model": model,
            "case": case,
            "conversation": cid,
            "idem": str(uuid4()),
            "run_id": None,
            "submission_started": False,
        }
        self.entries.append(entry)
        self.save()
        return entry

    def accept(self, entry, identifier):
        entry["run_id"] = identifier
        self.save()


def checked(response):
    if response.is_error:
        # Never print a raw upstream/local response or request headers.
        code = response.json().get("error", "request_failed")
        from gaia_agent.interactive.credential_vault import SAFE_CODES

        raise ValueError(code if code in SAFE_CODES else "request_failed")
    return response.json()


def recover_accepted(client, ledger, entry, question):
    if entry["run_id"]:
        return entry["run_id"]
    response = client.get(f"conversations/{entry['conversation']}")
    response.raise_for_status()
    run = response.json().get("latest_run")
    if run and (run["question"], run["provider"], run["model"]) == (
        question,
        entry["provider"],
        entry["model"],
    ):
        ledger.accept(entry, run["id"])
        return run["id"]
    raise ValueError("submission_uncertain_do_not_repeat")


def run_provider(client, ledger, provider, model, cases, handle=None, secrets=()):
    results = []
    conversation = None
    for case_id in LIVE_CASES:
        case = next(case for case in cases if case["id"] == case_id)
        existing = next(
            (
                e
                for e in ledger.entries
                if (e["provider"], e["model"], e["case"]) == (provider, model, case_id)
            ),
            None,
        )
        if existing:
            conversation = existing["conversation"]
        elif case_id != "follow_up" or conversation is None:
            conversation = checked(client.post("conversations", json={"provider": provider}))["id"]
        entry = ledger.reserve(provider, model, case_id, conversation)
        question = case["input"]
        started = time.monotonic()
        if entry.get("submission_started") or entry["run_id"]:
            identifier = recover_accepted(client, ledger, entry, question)
        else:
            attachment = None
            if case_id == "txt":
                content = b"Project name: Orion\nLanguage: Python\nDefinition URL: https://www.python.org/doc/essays/blurb/\n"
                attachment = checked(
                    client.post(
                        f"conversations/{conversation}/uploads",
                        files={"file": ("project.txt", content, "text/plain")},
                    )
                )["id"]
            entry["submission_started"] = True
            ledger.save()
            run = checked(
                client.post(
                    f"conversations/{conversation}/runs",
                    json={
                        "question": question,
                        "provider": provider,
                        "model": model,
                        "attachment_id": attachment,
                    },
                    headers={
                        "idempotency-key": entry["idem"],
                        **({"x-agent-credential-handle": handle} if handle else {}),
                    },
                )
            )
            identifier = run["id"]
            ledger.accept(entry, identifier)
        deadline = time.monotonic() + 510
        while True:
            run = checked(client.get(f"runs/{identifier}"))
            if run["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                break
            if time.monotonic() >= deadline:
                raise ValueError("poll_timeout_resume_ledger")
            time.sleep(1)
        progress = run.get("progress", [])
        result = run.get("result") or {}
        summary = {
            "provider": provider,
            "model": model,
            "case": case_id,
            "run_id": identifier,
            "status": run["status"],
            "seconds": round(time.monotonic() - started, 2),
            "route": "direct"
            if any(e["event"] == "chat_completed" for e in progress)
            else "research"
            if any(e["event"] == "plan_started" for e in progress)
            else "failed_before_route",
            "answer": result.get("answer"),
            "usage": result.get("usage"),
            "sources": [e.get("source_url") for e in result.get("evidence", [])],
            "tool_count": sum(e["event"] == "tool_call_started" for e in progress),
            "failure": run.get("failure"),
        }
        summary = sanitize(summary, secrets)
        entry["summary"] = summary
        ledger.save()
        results.append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("openai", "deepseek"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--owner", default="local_seedy")
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--memory-plan",
        action="store_true",
        help="Print credential-free memory acceptance plan only",
    )
    options = parser.parse_args()
    if options.memory_plan:
        if options.live:
            parser.error("memory plan does not authorize paid tests")
        fixture = FIXTURE.with_name("memory_acceptance.json")
        memory_cases = json.loads(fixture.read_text())
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "cases": len(memory_cases),
                    "paid_requests": 0,
                    "new_budget_required": True,
                }
            )
        )
        return
    cases = dry_run(options.provider, options.model)
    if not options.live:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "cases": len(cases),
                    "live_cases": LIVE_CASES,
                    "max_total": 6,
                    "max_provider": 3,
                }
            )
        )
        return
    if options.ledger is None:
        raise ValueError("ledger_required")
    token = os.environ.get("AGENT_SERVICE_TOKEN", "")
    if len(token) < 16:
        raise ValueError("service_credential_missing")
    ledger = RunLedger(options.ledger)
    previous = [
        e
        for e in ledger.entries
        if e["provider"] == options.provider and e["model"] == options.model
    ]
    if len(previous) == 3 and all(
        e.get("summary", {}).get("status") in {"completed", "failed", "cancelled", "interrupted"}
        for e in previous
    ):
        for entry in previous:
            print(json.dumps(entry["summary"], ensure_ascii=False))
        return
    if len(ledger.entries) >= 6 and not previous:
        raise ValueError("budget_exhausted")
    secrets = [token]
    # Local API only; real model credentials never go to arbitrary CLI URLs.
    with httpx.Client(
        base_url="http://127.0.0.1:8000/v1/",
        timeout=30,
        follow_redirects=False,
        trust_env=False,
        headers={"authorization": "Bearer " + token, "x-agent-user": options.owner},
    ) as client:
        handle = None
        if options.provider == "deepseek":
            key = read_key()
            secrets.append(key)
            handle = checked(
                client.post("credential-sessions", json={"provider": "deepseek", "model_key": key})
            )["handle"]
        # Sanitization covers nested answers/failures, including dictionary keys.
        # run_provider prints only already server-sanitized public synthetic answers.
        try:
            run_provider(client, ledger, options.provider, options.model, cases, handle, secrets)
        finally:
            for entry in ledger.entries:
                if "summary" in entry:
                    entry["summary"] = sanitize(entry["summary"], secrets)
            ledger.save()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - never print exception payloads containing credentials
        from gaia_agent.interactive.credential_vault import SAFE_CODES

        safe = (
            str(error)
            if isinstance(error, ValueError)
            and str(error)
            in SAFE_CODES
            | {
                "budget_exhausted",
                "submission_uncertain_do_not_repeat",
                "poll_timeout_resume_ledger",
            }
            else type(error).__name__
        )
        raise SystemExit(safe) from None
