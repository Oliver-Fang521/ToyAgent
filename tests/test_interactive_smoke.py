import importlib.util
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location(
        "interactive_smoke", Path(__file__).parents[1] / "scripts/interactive_smoke.py"
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_dry_run_no_network_or_secret_read(monkeypatch):
    smoke = module()
    monkeypatch.setattr(smoke, "read_key", lambda: pytest.fail("dry-run read a key"))
    monkeypatch.setattr(
        smoke.httpx, "Client", lambda **kwargs: pytest.fail("dry-run created a client")
    )
    cases = smoke.dry_run("deepseek", "deepseek-flash")
    assert len(cases) == 15 and len({c["id"] for c in cases}) == 15
    assert all("expected" in c and "forbidden" in c for c in cases)
    with pytest.raises(ValueError):
        smoke.dry_run("unknown", "bad")


def test_ledger_bounds_and_resume(tmp_path):
    smoke = module()
    ledger = smoke.RunLedger(tmp_path / "ledger.json")
    for provider in ("openai", "deepseek"):
        for index in range(3):
            entry = ledger.reserve(provider, "model", str(index), "cid")
            ledger.accept(entry, "run-" + str(index))
        with pytest.raises(ValueError, match="budget_exhausted"):
            ledger.reserve(provider, "model", "fourth", "cid")
    restored = smoke.RunLedger(tmp_path / "ledger.json")
    entry = restored.reserve("deepseek", "model", "0", "cid")
    assert entry["run_id"] == "run-0" and len(restored.entries) == 6


def test_safe_nested_output_and_disconnected_resume(tmp_path):
    smoke = module()
    assert "private" not in str(smoke.sanitize({"private": ["private"]}, ["private"]))
    ledger = smoke.RunLedger(tmp_path / "ledger.json")
    entry = ledger.reserve("openai", "model", "translation", "cid")

    class Client:
        def get(self, path):
            return type(
                "Response",
                (),
                {
                    "raise_for_status": lambda self: None,
                    "json": lambda self: {
                        "latest_run": {
                            "id": "accepted",
                            "question": "q",
                            "provider": "openai",
                            "model": "model",
                        }
                    },
                },
            )()

        def post(self, *args, **kwargs):
            pytest.fail("accepted task was submitted twice")

    assert smoke.recover_accepted(Client(), ledger, entry, "q") == "accepted"
