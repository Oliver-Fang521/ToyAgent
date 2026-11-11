import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_offline_cli_never_requires_credentials_and_reports_old_fact_recall():
    result = subprocess.run([sys.executable, "scripts/layered_memory_acceptance.py", "--offline", "--count", "20"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["paid_calls"] == 0 and data["saved_count"] == 20
    assert data["oldest_recalled"] is True
    assert data["decrypted_for_queries"] == 20


def test_budget_authorization_is_not_reset_on_restart(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    module = importlib.import_module("layered_memory_acceptance")
    ledger = tmp_path / "budget.json"
    ledger.write_text(json.dumps([{"provider": "openai", "phase": "prior", "estimated_usd": 0.01}]))
    budget = module.LayeredBudget(ledger, max_calls=1, authorize=True)
    budget.reserve("openai", "summary")
    reopened = module.LayeredBudget(ledger, max_calls=1, authorize=False)
    with pytest.raises(ValueError, match="call_budget_exhausted"):
        reopened.reserve("openai", "recall")
    assert len(json.loads(ledger.read_text())) == 2
