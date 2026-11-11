"""Strict infrastructure loader; no user or author model credentials are inherited."""

import base64
import json
import os
import sys
from pathlib import Path


def backend_environment(path: Path) -> dict[str, str]:
    try:
        if (path.parent / ".complete").read_text() != "1\n" or path.stat().st_size > 8192:
            raise ValueError
        value = json.loads(path.read_text())
        if (
            value["version"] != 1
            or not isinstance(value["service_token"], str)
            or len(value["service_token"]) < 32
        ):
            raise ValueError
        keyring = value["credential_keyring"]
        if not isinstance(keyring, dict) or not keyring or value["active_version"] not in keyring:
            raise ValueError
        for key in keyring.values():
            if len(base64.b64decode(key, validate=True)) != 32:
                raise ValueError
        return {
            "AGENT_SERVICE_TOKEN": value["service_token"],
            "AGENT_CREDENTIAL_KEYRING": json.dumps(keyring),
            "AGENT_CREDENTIAL_ACTIVE_VERSION": value["active_version"],
            "AGENT_MODEL_BASE_URL": "https://api.openai.com/v1",
        }
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("local_configuration") from None


def main() -> None:
    try:
        values = backend_environment(
            Path(os.environ.get("AGENT_LOCAL_CONFIG", "/run/toyagent/runtime.json"))
        )
    except ValueError:
        print(
            "local_configuration: run initialization; do not replace existing keys", file=sys.stderr
        )
        raise SystemExit(1) from None
    env = {
        name: value
        for name, value in os.environ.items()
        if name in {"PATH", "HOME", "LANG", "PYTHONPATH", "AGENT_DATA_DIR"}
    }
    env.update(values)
    os.execve(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "gaia_agent.interactive.api:create_app",
            "--factory",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
        env,
    )


if __name__ == "__main__":
    main()
