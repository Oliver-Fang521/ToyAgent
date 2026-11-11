import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
from local_runtime import initialize

PASSWORD = "synthetic-local-password"


def read(root, service):
    return json.loads((root / service / "runtime.json").read_text())


def test_initialization_is_random_and_separates_secrets(tmp_path):
    root = tmp_path / "first"
    initialize(root, PASSWORD)
    initialize(tmp_path / "second", PASSWORD)
    front, back = read(root, "frontend"), read(root, "backend")
    assert front["service_token"] == back["service_token"]
    assert front["owner_id"] != read(tmp_path / "second", "frontend")["owner_id"]
    assert front["session_key"] != read(tmp_path / "second", "frontend")["session_key"]
    assert "credential_keyring" not in front and "password" not in back
    assert len(base64.b64decode(front["session_key"])) == 32
    assert len(base64.b64decode(back["credential_keyring"]["v1"])) == 32
    digest = front["password"]
    assert hashlib.scrypt(
        PASSWORD.encode(), salt=base64.b64decode(digest["salt"]), n=16384, r=8, p=1, dklen=32
    ) == base64.b64decode(digest["hash"])
    for folder in (root, root / "frontend", root / "backend"):
        assert (folder / ".complete").read_text() == "1\n"
    assert (root / "frontend/runtime.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("partial", [False, True])
def test_existing_state_is_never_overwritten(tmp_path, partial):
    root = tmp_path / "state"
    if partial:
        root.mkdir()
        (root / "important").write_bytes(b"keep")
    else:
        initialize(root, PASSWORD)
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="existing_state"):
        initialize(root, PASSWORD)
    assert before == {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize("password", ["short", "x" * 1025])
def test_invalid_password_writes_nothing(tmp_path, password):
    with pytest.raises(ValueError, match="password_policy"):
        initialize(tmp_path / "state", password)
    assert not (tmp_path / "state").exists()


def test_cli_refuses_non_tty_without_printing_password(tmp_path):
    result = subprocess.run(
        [sys.executable, "deploy/local_runtime.py", "init", "--root", str(tmp_path / "state")],
        input=PASSWORD,
        text=True,
        capture_output=True,
        check=False,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode != 0
    assert "tty_required" in result.stderr
    assert PASSWORD not in result.stdout + result.stderr
    assert not (tmp_path / "state").exists()


def test_confirmation_mismatch_writes_nothing(tmp_path, monkeypatch, capsys):
    import local_runtime

    responses = iter([PASSWORD, "different-password"])
    monkeypatch.setattr(sys, "argv", ["init", "init", "--root", str(tmp_path / "state")])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(local_runtime.getpass, "getpass", lambda _: next(responses))
    assert local_runtime.main() == 1
    assert "password_mismatch" in capsys.readouterr().err
    assert not (tmp_path / "state").exists()


def test_password_reset_preserves_encryption_and_owner(tmp_path):
    from local_runtime import reset_password

    root = tmp_path / "runtime"
    initialize(root, PASSWORD)
    front, back = read(root, "frontend"), read(root, "backend")
    reset_password(root, "replacement-local-password")
    after = read(root, "frontend")
    assert read(root, "backend") == back
    assert after["owner_id"] == front["owner_id"]
    assert after["service_token"] == front["service_token"]
    assert after["session_key"] != front["session_key"]
    assert after["password"] != front["password"]
    with pytest.raises(ValueError):
        reset_password(root, "bad")
    assert read(root, "frontend") == after
