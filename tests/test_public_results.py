from pathlib import Path


def test_public_result_removes_private_file_urls_and_task_paths():
    from gaia_agent.interactive.public_results import public_result

    workspace = Path("/private/task-root/run-1")
    raw = {
        "answer": "Read /private/task-root/run-1/attachment.txt",
        "evidence": [
            {
                "summary": "Read /private/task-root/run-1/attachment.txt",
                "source_url": "file:///private/task-root/run-1/attachment.txt",
            },
            {"summary": "official", "source_url": "https://www.python.org/doc/essays/blurb/"},
            {"summary": "unsafe", "source_url": "https://secret@www.example.com/"},
        ],
    }
    result = public_result(raw, workspace)
    assert str(workspace) not in str(result)
    assert result["evidence"][0]["source_url"] is None
    assert result["evidence"][1]["source_url"] == raw["evidence"][1]["source_url"]
    assert result["evidence"][2]["source_url"] is None
    assert raw["evidence"][0]["source_url"].startswith("file:")


def test_reopening_old_results_applies_projection_without_rewriting_db(tmp_path):
    from gaia_agent.interactive.models import InteractiveRequest
    from gaia_agent.interactive.store import ConversationStore

    store = ConversationStore(tmp_path / "state.sqlite")
    cid = store.create("alice", "deepseek")["id"]
    run = store.reserve(
        "alice",
        cid,
        InteractiveRequest(provider="deepseek", model="deepseek-flash", question="q"),
        "once",
    )
    workspace = tmp_path / "runs" / run["id"]
    store.update(
        run["id"],
        status="completed",
        result={
            "answer": str(workspace / "attachment.txt"),
            "evidence": [{"source_url": f"file://{workspace}/attachment.txt", "summary": "file"}],
        },
    )
    assert store.run("alice", run["id"])["result"]["evidence"][0]["source_url"] is None
    messages = store.messages("alice", cid)["messages"]
    assert str(workspace) not in str(messages) and messages[1]["evidence"][0]["source_url"] is None
    assert "file://" in store.db.execute("SELECT result FROM runs").fetchone()[0]
