import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
from local_backup import backup, restore
from local_runtime import initialize


def test_backup_restore_preserves_secrets_and_forgetting_data(tmp_path):
    source = tmp_path / "source"
    initialize(source / "secrets", "synthetic-local-password")
    (source / "data").mkdir()
    (source / "data/state.sqlite").write_bytes(b"synthetic-tombstone")
    archive = tmp_path / "archive.tar"
    backup(source / "secrets", source / "data", archive)
    assert archive.stat().st_mode & 0o777 == 0o600
    target = tmp_path / "target"
    restore(target / "secrets", target / "data", archive)
    for path in source.rglob("*"):
        if path.is_file():
            assert (target / path.relative_to(source)).read_bytes() == path.read_bytes()
    with pytest.raises(ValueError, match="nonempty_restore"):
        restore(target / "secrets", target / "data", archive)


def test_interrupted_data_restore_never_publishes_ready_secrets(tmp_path, monkeypatch):
    from local_backend import backend_environment

    source = tmp_path / "source"
    initialize(source / "secrets", "synthetic-local-password")
    (source / "data").mkdir()
    (source / "data/state.sqlite").write_bytes(b"synthetic-forgetting-barrier")
    archive = tmp_path / "archive.tar"
    backup(source / "secrets", source / "data", archive)
    original = tarfile.TarFile.extractfile

    def interrupted(tar, member):
        if member.name == "data/state.sqlite":
            raise OSError("synthetic_disk_failure")
        return original(tar, member)

    monkeypatch.setattr(tarfile.TarFile, "extractfile", interrupted)
    target = tmp_path / "target"
    with pytest.raises(OSError, match="synthetic_disk_failure"):
        restore(target / "secrets", target / "data", archive)
    for folder in (target / "secrets", target / "secrets/frontend", target / "secrets/backend"):
        assert not (folder / ".complete").exists()
    with pytest.raises(ValueError, match="local_configuration"):
        backend_environment(target / "secrets/backend/runtime.json")
