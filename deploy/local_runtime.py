"""Offline local-runtime initialization. Never accepts passwords in argv or environment."""

import argparse
import base64
import getpass
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path
from uuid import uuid4


def encoded(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def password_record(password: str) -> dict:
    if len(password) < 12 or len(password.encode("utf-8")) > 1024:
        raise ValueError("password_policy")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return {
        "algorithm": "scrypt",
        "salt": encoded(salt),
        "hash": encoded(digest),
        "n": 16384,
        "r": 8,
        "p": 1,
    }


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".pending")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def initialize(root: Path, password: str) -> None:
    digest = password_record(password)
    if root.exists() and any(root.iterdir()):
        raise ValueError("existing_state")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    front = {
        "version": 1,
        "owner_id": str(uuid4()),
        "password": digest,
        "session_key": encoded(secrets.token_bytes(32)),
        "service_token": token,
    }
    back = {
        "version": 1,
        "service_token": token,
        "active_version": "v1",
        "credential_keyring": {"v1": encoded(secrets.token_bytes(32))},
    }
    for service, config in (("frontend", front), ("backend", back)):
        directory = root / service
        directory.mkdir(mode=0o700)
        atomic_write(directory / "runtime.json", json.dumps(config))
    for directory in (root / "frontend", root / "backend", root):
        atomic_write(directory / ".complete", "1\n")
    if os.geteuid() == 0:
        for path in [*root.rglob("*"), root]:
            os.chown(path, 10001, 10001)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize a private single-user ToyAgent runtime"
    )
    parser.add_argument("action", choices=["init", "reset-password", "auto-init"])
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "auto-init":
        try:
            if args.root.exists() and any(args.root.iterdir()):
                if any((args.root / part / ".complete").read_text() != "1\n" for part in ("frontend", "backend")) or (args.root / ".complete").read_text() != "1\n":
                    raise ValueError("local_configuration")
                front = json.loads((args.root / "frontend/runtime.json").read_text())
                back = json.loads((args.root / "backend/runtime.json").read_text())
                if front.get("version") != 1 or back.get("version") != 1 or not front.get("service_token") or front["service_token"] != back.get("service_token"):
                    raise ValueError("local_configuration")
            else:
                initialize(args.root, secrets.token_urlsafe(32))
            data = os.environ.get("AGENT_DATA_DIR")
            if data:
                Path(data).mkdir(parents=True, exist_ok=True)
                if os.geteuid() == 0:
                    os.chown(data, 10001, 10001)
            return 0
        except (ValueError, OSError, json.JSONDecodeError):
            print("local_configuration: preserve existing secrets", file=sys.stderr)
            return 1
    if not sys.stdin.isatty():
        print("tty_required", file=sys.stderr)
        return 1
    try:
        password = getpass.getpass("Local access password (12+ characters): ")
        if password != getpass.getpass("Confirm password: "):
            raise ValueError("password_mismatch")
        if args.action == "init":
            initialize(args.root, password)
            data = os.environ.get("AGENT_DATA_DIR")
            if data:
                Path(data).mkdir(parents=True, exist_ok=True)
                if os.geteuid() == 0:
                    os.chown(data, 10001, 10001)
        else:
            reset_password(args.root, password)
    except (ValueError, OSError) as error:
        code = str(error) if isinstance(error, ValueError) else "runtime_write_failed"
        print(code, file=sys.stderr)
        return 1
    print("Local runtime initialized. Keep a private backup of secrets and data together.")
    return 0


def reset_password(root: Path, password: str) -> None:
    digest = password_record(password)
    try:
        if (root / ".complete").read_text() != "1\n":
            raise ValueError("local_configuration")
        path = root / "frontend/runtime.json"
        front = json.loads(path.read_text())
        back = json.loads((root / "backend/runtime.json").read_text())
        if (
            front["version"] != 1
            or back["version"] != 1
            or front["service_token"] != back["service_token"]
        ):
            raise ValueError("local_configuration")
        front["password"] = digest
        front["session_key"] = encoded(secrets.token_bytes(32))
        atomic_write(path, json.dumps(front))
        if os.geteuid() == 0:
            os.chown(path, 10001, 10001)
    except (KeyError, OSError, json.JSONDecodeError):
        raise ValueError("local_configuration") from None


if __name__ == "__main__":
    raise SystemExit(main())
