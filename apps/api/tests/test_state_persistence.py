from conftest import git_output
from test_api_workflow import apply_value, create_task, post


def test_task_repository_approval_and_audit_state_survive_restart(api_stack, demo_repository):
    client = api_stack.restart()
    task = create_task(client, demo_repository)
    apply_value(client, task, 2)
    post(client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest", "timeout_seconds": 15})
    before = [event["action"] for event in client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]]
    assert before

    restarted = api_stack.restart()
    assert restarted.get("/api/v1/repositories").json()["total"] == 1
    loaded = restarted.get(f"/api/v1/tasks/{task['id']}").json()
    assert loaded["repository_id"] == task["repository_id"]
    assert loaded["task_branch"] == task["task_branch"] or loaded["branch"]
    iterations = restarted.get(f"/api/v1/tasks/{task['id']}/iterations").json()["items"]
    assert len(iterations) == 1
    assert iterations[0]["checkpoint_id"]
    assert [run["kind"] for run in iterations[0]["test_runs"]] == ["pytest"]
    approvals = restarted.get(f"/api/v1/tasks/{task['id']}/approvals").json()["items"]
    assert {item["type"] for item in approvals} == {"plan"}
    assert all(item["status"] == "approved" for item in approvals)
    after = [event["action"] for event in restarted.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]]
    assert after == before


def test_checkpoint_files_can_be_restored_after_restart(api_stack, demo_repository):
    client = api_stack.restart()
    task = create_task(client, demo_repository)
    applied = apply_value(client, task, 2)
    assert "return 2" in (demo_repository / "app.py").read_text(encoding="utf-8")

    restarted = api_stack.restart()
    checkpoints = restarted.get(f"/api/v1/runs/{task['run_id']}/checkpoints").json()["items"]
    assert [item["id"] for item in checkpoints] == [applied["checkpoint"]["id"]]
    result = post(restarted, f"/api/v1/runs/{task['run_id']}/rollback", {"checkpoint_id": applied["checkpoint"]["id"]})
    assert result["restored_files"] == ["app.py"]
    assert "return 1" in (demo_repository / "app.py").read_text(encoding="utf-8")
    assert git_output(demo_repository, "status", "--porcelain").strip() == ""


def test_in_flight_task_requires_explicit_recovery(api_stack, demo_repository):
    client = api_stack.restart()
    task = create_task(client, demo_repository)

    restarted = api_stack.restart()
    loaded = restarted.get(f"/api/v1/tasks/{task['id']}").json()
    assert loaded["requires_recovery"] is True
    assert loaded["next_action"]

    blocked = restarted.post(
        f"/api/v1/runs/{task['run_id']}/patches/preview",
        json={"files": [{"path": "app.py", "content": "def value():\n    return 3\n"}]},
    )
    assert blocked.status_code == 400
    assert blocked.json()["code"] == "RECOVERY_CONFIRMATION_REQUIRED"
    blocked_commit = restarted.post(f"/api/v1/tasks/{task['id']}/branch", json={"name": "codex/after-restart"})
    assert blocked_commit.json()["code"] == "RECOVERY_CONFIRMATION_REQUIRED"

    recovered = post(restarted, f"/api/v1/tasks/{task['id']}/recover")
    assert recovered["action"] == "recovered"
    assert recovered["task"]["requires_recovery"] is False
    allowed = post(
        restarted,
        f"/api/v1/runs/{task['run_id']}/patches/preview",
        {"files": [{"path": "app.py", "content": "def value():\n    return 3\n"}]},
    )
    assert allowed["files"] == ["app.py"]
    assert "task.recover" in [event["action"] for event in restarted.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]]


def test_closed_and_paused_tasks_do_not_require_recovery(api_stack, demo_repository):
    client = api_stack.restart()
    running = create_task(client, demo_repository)
    cancelled = create_task(client, demo_repository)
    post(client, f"/api/v1/tasks/{cancelled['id']}/cancel")

    restarted = api_stack.restart()
    assert restarted.get(f"/api/v1/tasks/{cancelled['id']}").json()["requires_recovery"] is False
    assert restarted.get(f"/api/v1/tasks/{running['id']}").json()["requires_recovery"] is True
    assert restarted.get("/api/v1/tasks").json()["total"] == 2
