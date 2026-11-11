import json
import runpy
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_compose_keeps_backend_private_and_secrets_scoped():
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "tools",
            "--env-file",
            ".env.local.example",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    front, back = config["services"]["frontend"], config["services"]["backend"]
    assert front["ports"][0]["host_ip"] == "127.0.0.1"
    assert not back.get("ports")
    for service, name in [(front, "frontend"), (back, "backend")]:
        mount = next(v for v in service["volumes"] if v["target"] == "/run/toyagent")
        assert mount["read_only"] and mount["volume"]["subpath"] == name
        assert service["user"] == "10001:10001"
        assert not service.get("privileged")
        assert not any("docker.sock" in str(v) for v in service["volumes"])
    assert config["services"]["backend"]["depends_on"]["init"]["condition"] == "service_completed_successfully"


@pytest.mark.parametrize(
    "file", ["deploy/Dockerfile.backend", "deploy/Dockerfile.frontend", ".dockerignore"]
)
def test_build_inputs_exist(file):
    assert (ROOT / file).is_file()


def test_smoke_normal_up_configuration_uses_automatic_init():
    helpers = runpy.run_path(str(ROOT / "scripts/local_self_host_smoke.py"))
    result = helpers["compose"]("toyagent-qa-profile-check", 3020, "config", "--format", "json")
    assert json.loads(result.stdout)["services"]["init"]["command"][0] == "auto-init"
