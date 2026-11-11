import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
from local_backend import backend_environment
from local_runtime import initialize


def test_backend_loader_allows_only_infrastructure(tmp_path):
    initialize(tmp_path / "runtime", "synthetic-local-password")
    path = tmp_path / "runtime/backend/runtime.json"
    result = backend_environment(path)
    assert set(result) == {
        "AGENT_SERVICE_TOKEN",
        "AGENT_CREDENTIAL_KEYRING",
        "AGENT_CREDENTIAL_ACTIVE_VERSION",
        "AGENT_MODEL_BASE_URL",
    }
    assert result["AGENT_MODEL_BASE_URL"] == "https://api.openai.com/v1"
    assert "password" not in json.dumps(result)
    (path.parent / ".complete").unlink()
    with pytest.raises(ValueError, match="local_configuration"):
        backend_environment(path)


@pytest.mark.parametrize(
    "value", [{"version": 2}, {"version": 1}, {"version": 1, "service_token": "bad"}]
)
def test_backend_rejects_bad_config_without_secret_output(tmp_path, value, capsys):
    (tmp_path / ".complete").write_text("1\n")
    (tmp_path / "runtime.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="local_configuration"):
        backend_environment(tmp_path / "runtime.json")
    assert capsys.readouterr().out == ""
