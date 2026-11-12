"""Small offline retriever shared by both LangGraph support evaluation arms.

Only explicitly supplied documentation snapshots are loaded.  Reference answers and
GitHub issues are intentionally unavailable to the evaluated agent.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path


DEFAULT_SNAPSHOT = Path(__file__).parent / "public" / "docs-snapshot.json"
ALIASES = {
    "中断": "interrupt", "恢复": "resume", "暂停": "interrupt pause",
    "检查点": "checkpoint", "持久化": "persistence checkpoint",
    "子图": "subgraph", "并行": "parallel", "工具": "tool",
    "状态": "state", "版本": "version", "线程": "thread",
    "存储": "storage", "数据库": "database", "异步": "async",
}


def terms(text: str) -> list[str]:
    text = text.lower()
    expansions = " ".join(value for key, value in ALIASES.items() if key in text)
    return re.findall(r"[a-z][a-z0-9_]{1,63}", text + " " + expansions)


class DocumentationIndex:
    def __init__(self, snapshot: Path = DEFAULT_SNAPSHOT):
        data = json.loads(Path(snapshot).read_text(encoding="utf-8"))
        self.chunks = []
        self.documents = {}
        for document in data["documents"]:
            if not document["source_url"].startswith("https://github.com/langchain-ai/docs/blob/main/"):
                raise ValueError("documentation source is not allowed")
            self.documents[document["source_url"]] = document["content"]
            lines = document["content"].splitlines()
            for start in range(0, len(lines), 40):
                excerpt = "\n".join(lines[start:start + 48]).strip()
                if excerpt:
                    self.chunks.append({"url": document["source_url"], "blob_sha": document["blob_sha"],
                                        "start_line": start + 1, "text": excerpt})
        self.counts = [Counter(terms(chunk["text"])) for chunk in self.chunks]
        self.document_frequencies = Counter(key for count in self.counts for key in count)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        if not 1 <= limit <= 10 or not isinstance(query, str) or len(query) > 1000:
            raise ValueError("invalid query or limit")
        query_terms = set(terms(query))
        total = len(self.chunks)
        ranked = []
        for index, count in enumerate(self.counts):
            score = sum((1 + math.log(count[term])) * math.log(1 + total / self.document_frequencies[term])
                        for term in query_terms if count[term])
            if score > 0:
                ranked.append((score, index))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [dict(self.chunks[index], score=round(score, 4)) for score, index in ranked[:limit]]

    def read(self, url: str) -> str:
        if url not in self.documents:
            raise ValueError("URL is outside the evaluation corpus")
        return self.documents[url]
