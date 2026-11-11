"""Read explicitly registered local skill instructions without executing them."""

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path


class SkillError(ValueError):
    """Skill is unavailable or fails the catalog's safety checks."""


@dataclass(frozen=True)
class Skill:
    name: str
    instructions: str


class SkillCatalog:
    def __init__(self, root: Path, allowed: set[str], *, max_bytes: int = 16_384):
        self.root = root
        self.allowed = frozenset(allowed)
        self.max_bytes = max_bytes

    def load(self, name: str) -> Skill:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name) or name not in self.allowed:
            raise SkillError("skill unavailable")
        try:
            root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                dir_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                try:
                    fd = os.open("SKILL.md", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
                    try:
                        if not stat.S_ISREG(os.fstat(fd).st_mode):
                            raise SkillError("invalid skill")
                        with os.fdopen(fd, "rb", closefd=False) as file:
                            raw = file.read(self.max_bytes + 1)
                    finally:
                        os.close(fd)
                finally:
                    os.close(dir_fd)
            finally:
                os.close(root_fd)
            if len(raw) > self.max_bytes:
                raise SkillError("skill too large")
            content = raw.decode("utf-8")
            match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
            if not match or not re.search(rf"(?m)^name:\s*['\"]?{re.escape(name)}['\"]?\s*$", match.group(1)):
                raise SkillError("invalid skill metadata")
            return Skill(name, content)
        except (OSError, UnicodeError) as error:
            raise SkillError("skill unavailable") from error
