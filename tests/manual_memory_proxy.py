"""LOCAL TEST ONLY: fixed synthetic identity, fixed disposable QA upstream.

Never use this proxy in deployment. Both upstreams use synthetic credentials/data.
"""

import httpx
from fastapi import FastAPI, Request, Response

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.api_route("/{path:path}", methods=["GET", "POST"])
async def proxy(path: str, request: Request):
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"connection", "content-length", "accept-encoding"}
    }
    headers.update(
        {
            "oai-authenticated-user-id": "synthetic_memory_qa",
            "oai-authenticated-user-email": "memory-qa@example.invalid",
            "accept-encoding": "identity",
        }
    )
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
        response = await client.request(
            request.method,
            f"http://127.0.0.1:3001/{path}"
            + (f"?{request.url.query}" if request.url.query else ""),
            headers=headers,
            content=await request.body(),
        )
    return Response(
        response.content,
        status_code=response.status_code,
        headers={
            key: value
            for key, value in response.headers.items()
            if key.lower()
            not in {"content-length", "transfer-encoding", "content-encoding", "connection"}
        },
    )
