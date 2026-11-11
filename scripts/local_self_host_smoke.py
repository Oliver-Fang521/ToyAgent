"""Credential-free acceptance against an explicitly named disposable Compose project."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "synthetic-local-password"


def compose(project, port, *args, input=None):
    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.local.example",
            "-p",
            project,
            *(["--profile", "tools"] if args and args[0] == "run" else []),
            *args,
        ],
        cwd=ROOT,
        env={**os.environ, "TOYAGENT_PORT": str(port)},
        input=input,
        text=True,
        capture_output=True,
        check=True,
    )


def initialize(project, port, reset=False):
    action = "reset_password" if reset else "initialize"
    code = (
        "import sys,json,os;from pathlib import Path;sys.path.insert(0,'/app/deploy');"
        f"from local_runtime import {action};"
        f"{action}(Path('/secrets'),json.load(sys.stdin)['password']);"
        "os.chown('/data',10001,10001)"
    )
    compose(
        project,
        port,
        "run",
        "--rm",
        "-T",
        "--no-deps",
        "--entrypoint",
        "python",
        "init",
        "-c",
        code,
        input=json.dumps({"password": PASSWORD}),
    )


def login(client):
    response = client.post("/api/local-auth/login", data={"password": PASSWORD, "return_to": "/"})
    assert response.status_code == 303, f"login status {response.status_code}"
    assert "HttpOnly" in response.headers["set-cookie"]


def probe(project, port, cycle=False):
    base = f"http://localhost:{port}"
    with httpx.Client(base_url=base, headers={"origin": base}, trust_env=False) as client:
        assert client.get("/api/agent/memories/snapshot").status_code == 401
        assert (
            client.get(
                "/api/agent/memories/snapshot",
                headers={
                    "oai-authenticated-user-id": "spoof",
                    "oai-authenticated-user-email": "spoof@example.invalid",
                },
            ).status_code
            == 401
        )
        login(client)
        assert client.get("/api/agent/memories/snapshot").status_code == 200
        status = client.get("/api/agent/credentials").json()
        assert status is not None
        assert (
            client.post(
                "/api/agent/credentials",
                json={
                    "provider": "openai",
                    "key": "sk-synthetic-not-a-real-provider-key-7891",
                    "consent": True,
                },
            ).status_code
            == 200
        )
        memory = client.post(
            "/api/agent/memories",
            json={
                "category": "profile",
                "facet": "learning_subjects",
                "entity": "self",
                "content": "I study synthetic testing",
                "allowed_providers": ["openai"],
            },
        )
        assert memory.status_code == 200, f"memory status {memory.status_code}"
        item = memory.json()
        page = client.get('/api/agent/memories/search', params={'q': 'synthetic testing', 'limit': 1})
        assert page.status_code == 200
        assert any(v['id'] == item['id'] for v in page.json()['items'])
        maintenance = client.get('/api/agent/memories/maintenance')
        assert maintenance.status_code == 200
        if cycle:
            compose(project, port, "restart", "backend", "frontend")
            for _ in range(40):
                try:
                    if client.get("/api/agent/memories/snapshot").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(1)
            assert any(
                v["id"] == item["id"]
                for v in client.get("/api/agent/memories/snapshot").json()["items"]
            )
            statuses = client.get("/api/agent/credentials").json()["statuses"]
            assert any(
                v["provider"] == "openai" and v["configured"] and v["last_four"] == "7891"
                for v in statuses
            )
        assert (
            client.post(
                f"/api/agent/memories/{item['id']}/delete", json={"revision": item["revision"]}
            ).status_code
            == 200
        )
        assert client.get('/api/agent/memory/settings').json()['suppressed_facets'] == []
        permanent = client.post('/api/agent/memories',json={
            'category':'profile','facet':'custom_topic','entity':'self',
            'content':'Synthetic permanent-forget scope','allowed_providers':['openai']}).json()
        assert client.post(f"/api/agent/memories/{permanent['id']}/delete",
            json={'revision':permanent['revision'],'permanent':True}).status_code == 200
        barriers = client.get('/api/agent/memories/snapshot').json()['barriers']
        assert any(b['topic']=='custom_topic' for b in barriers)
        old_cookie = client.cookies.get("toyagent_local_session")
        assert client.post("/api/local-auth/logout").status_code == 303
        assert client.get("/api/agent/memories/snapshot").status_code == 401
        if cycle:
            compose(project, port, "stop", "frontend", "backend")
            initialize(project, port, reset=True)
            compose(project, port, "up", "-d", "--wait", "--no-build")
            client.cookies.set(
                "toyagent_local_session", old_cookie, domain="localhost.local", path="/"
            )
            assert client.get("/api/agent/memories/snapshot").status_code == 401
            client.cookies.clear()
            login(client)
            assert any(b['topic']=='custom_topic' for b in client.get('/api/agent/memories/snapshot').json()['barriers'])
    print("Synthetic Compose acceptance passed; no paid calls made.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["init", "probe", "cycle"])
    parser.add_argument("--project", required=True)
    parser.add_argument("--port", type=int, default=3020)
    args = parser.parse_args()
    if not args.project.startswith("toyagent-qa-") or not args.project.replace("-", "").isalnum():
        parser.error("only an explicitly disposable toyagent-qa-* project is allowed")
    if args.phase == "init":
        initialize(args.project, args.port)
    else:
        probe(args.project, args.port, args.phase == "cycle")


if __name__ == "__main__":
    main()
