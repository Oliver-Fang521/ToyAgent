"""Final public-result projection, after evidence validation and tool accounting."""

from pathlib import Path
from urllib.parse import urlsplit


def public_result(value, workspace: Path):
    prefix = str(workspace.resolve())

    def project(item):
        if isinstance(item, str):
            return item.replace(prefix, "[task-workspace]")
        if isinstance(item, list):
            return [project(part) for part in item]
        if isinstance(item, dict):
            result = {key: project(part) for key, part in item.items()}
            source = result.get("source_url")
            if source is not None:
                try:
                    url = urlsplit(source)
                    safe = (
                        url.scheme in {"https", "http"}
                        and url.hostname
                        and not (url.username or url.password)
                    )
                except (TypeError, ValueError):
                    safe = False
                if not safe:
                    result["source_url"] = None
            return result
        return item

    return project(value)
