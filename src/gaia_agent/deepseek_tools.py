"""Independent, bounded DeepSeek Search/Fetch API. Inputs are never history dumps."""

import ipaddress
import json
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit

import httpx

from gaia_agent.search import SearchProviderError


class _DeepSeekHTTP:
    provider = "deepseek"

    def __init__(self, api_key: str, client: httpx.AsyncClient, *, timeout_seconds: float):
        self._key, self._client, self._timeout = api_key, client, timeout_seconds

    def _error(self, category, retryable=False, status=None):
        return SearchProviderError(
            provider=self.provider, category=category, retryable=retryable, http_status=status
        )

    def _guard(self, value):
        if self._key and self._key in unquote(value):
            raise self._error("policy_rejection")

    def _clean(self, value, limit):
        return value.replace(self._key, "[REDACTED]")[:limit] if isinstance(value, str) else ""

    async def _post(self, operation, payload):
        try:
            async with self._client.stream(
                "POST",
                f"https://api.deepseek.com/v1/tools/{operation}",
                headers={"Authorization": "Bearer " + self._key},
                json=payload,
                timeout=self._timeout,
                follow_redirects=False,
            ) as response:
                status = response.status_code
                if not 200 <= status < 300:
                    category = {
                        400: "invalid_input",
                        401: "authentication",
                        402: "balance",
                        403: "forbidden",
                        408: "timeout",
                        429: "rate_limit",
                    }.get(status, "upstream" if status >= 500 else "http_status")
                    raise self._error(category, status in {408, 429} or status >= 500, status)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 2_000_000:
                        raise self._error("response_too_large")
                    body.extend(chunk)
            result = json.loads(body)
            if not isinstance(result, dict):
                raise self._error("parse_failure")
            return result
        except httpx.TimeoutException:
            raise self._error("timeout", True) from None
        except httpx.TransportError:
            raise self._error("transport", True) from None
        except (ValueError, UnicodeError):
            raise self._error("parse_failure") from None


def _public_url(url):
    try:
        parsed = urlsplit(url)
        if (
            len(url) > 8192
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return False
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return "." in host
    except ValueError:
        return False


class DeepSeekSearchProvider(_DeepSeekHTTP):
    async def search(self, query: str) -> dict:
        self._guard(query)
        if not query.strip() or not 1 <= len(query) <= 128:
            raise self._error("invalid_input")
        payload = await self._post("search", {"query": query, "count": 5})
        results = payload.get("results")
        if not isinstance(results, list):
            raise self._error("parse_failure")
        sources = []
        for item in results[:5]:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                raise self._error("parse_failure")
            url = item["url"]
            if not _public_url(url) or (self._key and self._key in unquote(url)):
                continue
            sources.append(
                {
                    "url": url,
                    "title": self._clean(item.get("title"), 500),
                    "snippet": self._clean(item.get("snippet"), 2000),
                    "summary": self._clean(item.get("summary"), 3000),
                    "retrieved_at": datetime.now(UTC).isoformat(),
                }
            )
        return {
            "answer": "\n\n".join(s["summary"] or s["snippet"] for s in sources),
            "sources": sources,
        }


class DeepSeekWebReader(_DeepSeekHTTP):
    """Only mount behind the application public URL/redirect policy wrapper."""

    async def read(self, url: str) -> dict:
        self._guard(url)
        if not _public_url(url):
            raise self._error("policy_rejection")
        result = await self._post("fetch", {"url": url})
        if not isinstance(result.get("content"), str) or not isinstance(result.get("title"), str):
            raise self._error("parse_failure")
        return {
            "url": url,
            "title": self._clean(result["title"], 500),
            "text": self._clean(result["content"], 20_000),
        }
