import time

from test_api_workflow import post


def _plain_folder(workspace_root):
    folder = workspace_root / "plain-notes"
    folder.mkdir()
    (folder / "readme.md").write_text("# notes\nsome text\n", encoding="utf-8")
    (folder / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    return folder


def _wait_for_loop(client, task_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get(f"/api/v1/tasks/{task_id}/agent").json()
        if not state["loop_running"]:
            return state
        time.sleep(0.1)
    raise AssertionError("agent loop did not finish in time")


def test_validate_accepts_a_non_git_folder(api_client, tmp_path):
    folder = _plain_folder(tmp_path)
    result = api_client.post("/api/v1/repositories/validate", json={"path": str(folder)}).json()
    assert result["valid"] is True
    assert result["git_root"] is None  # not a git repo, but still importable


def test_register_marks_git_and_non_git_folders(api_client, demo_repository, tmp_path):
    folder = _plain_folder(tmp_path)
    plain = post(api_client, "/api/v1/repositories", {"path": str(folder)})
    assert plain["has_git"] is False
    assert plain["branch"] == ""
    assert plain["languages"]  # language scan does not depend on git

    git_repo = post(api_client, "/api/v1/repositories", {"path": str(demo_repository)})
    assert git_repo["has_git"] is True
    assert git_repo["branch"] == "main"


def test_non_git_conservative_task_waits_for_patch_review(api_client, tmp_path):
    folder = _plain_folder(tmp_path)
    repository = post(api_client, "/api/v1/repositories", {"path": str(folder)})
    task = post(api_client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Explain the value function", "mode": "conservative"})

    # Loop pauses at the plan gate first (conservative auto-grants nothing).
    post(api_client, f"/api/v1/runs/{task['run_id']}/loop", {"mode": "conservative"})
    s1 = _wait_for_loop(api_client, task["id"])
    assert s1["action"] == "await_plan_approval"
    post(api_client, f"/api/v1/approvals/{s1['pending_approval']['id']}/decision", {"decision": "approve"})

    # A local folder can code without Git, but conservative mode still waits for patch review.
    post(api_client, f"/api/v1/runs/{task['run_id']}/loop", {})
    s2 = _wait_for_loop(api_client, task["id"])
    assert s2["action"] == "review_patch"
    assert s2["task"]["current_iteration"] == 1
    assert not (folder / ".git").exists()
    assert api_client.get(f"/api/v1/tasks/{task['id']}/commits").json()["total"] == 0


def test_opening_git_subfolder_preserves_the_selected_scope(api_client, demo_repository):
    folder = demo_repository / "notes"
    folder.mkdir()
    (folder / "readme.md").write_text("Only this folder\n", encoding="utf-8")
    opened = post(api_client, "/api/v1/repositories", {"path": str(folder)})
    assert opened["path"] == str(folder)
    assert opened["has_git"] is False
    tree = api_client.get(f"/api/v1/repositories/{opened['id']}/tree").json()
    assert [item["path"] for item in tree["items"]] == ["readme.md"]
    assert api_client.get(f"/api/v1/repositories/{opened['id']}/files", params={"path": "../app.py"}).status_code == 400


def test_opening_git_folder_without_commits_allows_local_work(api_client, tmp_path):
    from conftest import git_output

    folder = _plain_folder(tmp_path)
    git_output(folder, "init", "-q", "-b", "main")
    opened = post(api_client, "/api/v1/repositories", {"path": str(folder)})
    assert opened["path"] == str(folder)
    assert opened["has_git"] is False
    assert opened["head"] is None
    assert post(api_client, "/api/v1/tasks", {"repository_id": opened["id"], "goal": "Fix value"})["repository_id"] == opened["id"]


def test_local_folder_rejects_git_operations(api_client, tmp_path):
    folder = _plain_folder(tmp_path)
    opened = post(api_client, "/api/v1/repositories", {"path": str(folder)})
    task = post(api_client, "/api/v1/tasks", {"repository_id": opened["id"], "goal": "Fix value"})
    for suffix, body in (("branch", {"name": "codex/local"}), ("commits/preview", {"message": "Fix value"})):
        response = api_client.post(f"/api/v1/tasks/{task['id']}/{suffix}", json=body)
        assert response.status_code == 400
        assert response.json()["code"] == "GIT_NOT_AVAILABLE"
    assert not (folder / ".git").exists()
