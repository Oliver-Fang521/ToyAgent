"""Disposable browser fixture: synthetic memories, no credentials or model requests."""

import argparse
import base64
import json
from pathlib import Path

import uvicorn
from test_memory_repository import write

from deploy.local_runtime import initialize
from gaia_agent.interactive.api import create_app
from gaia_agent.interactive.memory.crypto import MemoryCrypto
from gaia_agent.interactive.models import InteractiveResult


def fixture(root):
    secrets = root / "secrets"
    if not (secrets / ".complete").exists():
        initialize(secrets, "synthetic-local-password")
    back = json.loads((secrets / "backend/runtime.json").read_text())
    front = json.loads((secrets / "frontend/runtime.json").read_text())

    async def execute(*args, **kwargs):
        return InteractiveResult(answer="Synthetic fixture. No model request was sent.", evidence=[])

    app = create_app(root / "data", back["service_token"], execute=execute,
        memory_crypto=MemoryCrypto({k: base64.b64decode(v) for k, v in back["credential_keyring"].items()}, back["active_version"]))
    count = app.state.memory_service.repository.db.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
    for i in range(210 if count == 0 else 0):
        app.state.memory_service.repository.save_manual(front["owner_id"],
            write(f"合成背景 sentinel-{i}：用于分页验收。", entity=f"fixture-{i}"), ("openai",))
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8016)
    args = parser.parse_args()
    uvicorn.run(fixture(args.root), host="127.0.0.1", port=args.port)
