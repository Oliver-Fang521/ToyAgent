"""Offline backup/restore. Stop both application services before running."""

import argparse
import os
import tarfile
from pathlib import Path, PurePosixPath

from local_runtime import atomic_write


def backup(secrets: Path, data: Path, archive: Path) -> None:
    if not (secrets / ".complete").is_file() or not data.is_dir():
        raise ValueError("incomplete_runtime")
    fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output, tarfile.open(fileobj=output, mode="w") as tar:
        tar.add(secrets, arcname="secrets", recursive=True)
        tar.add(data, arcname="data", recursive=True)


def restore(secrets: Path, data: Path, archive: Path) -> None:
    for folder in (secrets, data):
        if folder.exists() and any(folder.iterdir()):
            raise ValueError("nonempty_restore")
    with tarfile.open(archive, "r") as tar:
        members = tar.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or path.parts[0] not in {"secrets", "data"}
                or not (member.isfile() or member.isdir())
            ):
                raise ValueError("unsafe_archive")
        ready_names = {
            "secrets/.complete",
            "secrets/frontend/.complete",
            "secrets/backend/.complete",
        }
        ready = [m for m in members if PurePosixPath(m.name).as_posix() in ready_names]
        if len(ready) != 3 or {PurePosixPath(m.name).as_posix() for m in ready} != ready_names:
            raise ValueError("incomplete_runtime")
        for marker in ready:
            if not marker.isfile() or marker.size != 2:
                raise ValueError("incomplete_runtime")
            with tar.extractfile(marker) as content:
                if content.read(3) != b"1\n":
                    raise ValueError("incomplete_runtime")
        for folder in (secrets, data):
            folder.mkdir(parents=True, exist_ok=True)
        for member in members:
            path = PurePosixPath(member.name)
            if path.as_posix() in ready_names:
                continue
            target = secrets if path.parts[0] == "secrets" else data
            relative = Path(*path.parts[1:])
            destination = target / relative
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source:
                    fd = os.open(
                        destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, member.mode & 0o700
                    )
                    with os.fdopen(fd, "wb") as output:
                        import shutil

                        shutil.copyfileobj(source, output)
            os.chmod(destination, member.mode & 0o700)
            if os.geteuid() == 0:
                os.chown(destination, 10001, 10001)
        # Publish readiness only after all business data (including tombstones) is present.
        directories = (secrets / "frontend", secrets / "backend", secrets)
        try:
            for directory in directories:
                atomic_write(directory / ".complete", "1\n")
                if os.geteuid() == 0:
                    os.chown(directory / ".complete", 10001, 10001)
        except OSError:
            for directory in directories:
                (directory / ".complete").unlink(missing_ok=True)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    try:
        (backup if args.action == "backup" else restore)(
            Path("/secrets"), Path("/data"), args.archive
        )
    except (ValueError, OSError, tarfile.TarError):
        parser.exit(1, "backup_or_restore_failed: check empty target, archive and permissions\n")
    print("Offline operation completed. Keep backups private.")


if __name__ == "__main__":
    main()
