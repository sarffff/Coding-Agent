from datetime import datetime, timedelta, timezone

from conftest import git_output


def post(client, url, payload=None):
    response = client.post(url, json=payload or {})
    assert response.status_code in {200, 201}, response.text
    return response.json()


def approve(client, task, kind, scope_hash=None):
    payload = {"type": kind, "summary": "Review the task changes"}
    if scope_hash:
        payload["scope_hash"] = scope_hash
    approval = post(client, f"/api/v1/tasks/{task['id']}/approvals", payload)
    return post(client, f"/api/v1/approvals/{approval['id']}/decision", {"decision": "approve"})


def create_task(client, directory):
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Fix value and verify the local test"})
    post(client, f"/api/v1/tasks/{task['id']}/branch", {"name": "codex/" + task["id"]})
    approve(client, task, "plan")
    post(client, f"/api/v1/tasks/{task['id']}/iterations")
    return task


def apply_value(client, task, value):
    preview = post(client, f"/api/v1/runs/{task['run_id']}/patches/preview", {"files": [{"path": "app.py", "content": f"def value():\n    return {value}\n"}]})
    return post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {"patch_id": preview["patch_id"], "confirm": True})


def passing_task(client, directory):
    task = create_task(client, directory)
    apply_value(client, task, 2)
    result = post(client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest", "timeout_seconds": 15})
    assert result["status"] == "passed", result
    return task


def test_health_and_structured_validation(api_client):
    response = api_client.get("/health")
    assert response.json()["status"] == "ok"
    assert response.headers["x-trace-id"]
    response = api_client.post("/api/v1/tasks", json={"goal": ""})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["trace_id"]


def test_real_repair_test_approval_and_commit_workflow(api_client, demo_repository):
    task = create_task(api_client, demo_repository)
    apply_value(api_client, task, 0)
    failed = post(api_client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest", "timeout_seconds": 15})
    assert failed["status"] == "failed"
    assert failed["failed_tests"]
    post(api_client, f"/api/v1/tasks/{task['id']}/repairs", {"feedback": "Return 2 as expected by test_value"})
    apply_value(api_client, task, 2)
    passed = post(api_client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest", "timeout_seconds": 15})
    assert passed["status"] == "passed", passed
    iterations = api_client.get(f"/api/v1/tasks/{task['id']}/iterations").json()["items"]
    assert [item["status"] for item in iterations] == ["failed", "passed"]
    assert len(iterations[0]["test_runs"]) == 1
    preview = post(api_client, f"/api/v1/tasks/{task['id']}/commits/preview", {"message": "Fix value"})
    assert preview["changed_files"] == ["app.py"]
    blocked = api_client.post(f"/api/v1/tasks/{task['id']}/commits", json={"message": "Fix value", "scope_hash": preview["scope_hash"]})
    assert blocked.json()["code"] == "APPROVAL_REQUIRED"
    approve(api_client, task, "write", preview["scope_hash"])
    (demo_repository / "notes.txt").write_text("keep my staging\n", encoding="utf-8")
    git_output(demo_repository, "add", "notes.txt")
    committed = post(api_client, f"/api/v1/tasks/{task['id']}/commits", {"message": "Fix value", "scope_hash": preview["scope_hash"]})
    assert committed["head"] == git_output(demo_repository, "rev-parse", "HEAD").strip()
    assert git_output(demo_repository, "diff", "--cached", "--name-only").strip() == "notes.txt"
    duplicate = api_client.post(f"/api/v1/tasks/{task['id']}/commits", json={"message": "Fix value", "scope_hash": preview["scope_hash"]})
    assert duplicate.status_code == 400
    assert git_output(demo_repository, "rev-list", "--count", "HEAD").strip() == "2"
    actions = {item["action"] for item in api_client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]}
    assert {"tests.run", "iteration.repair", "approval.decision", "git.commit.create"} <= actions


def test_commit_rejects_changed_content_and_stale_tests(api_client, demo_repository):
    task = passing_task(api_client, demo_repository)
    url = f"/api/v1/tasks/{task['id']}/commits"
    preview = post(api_client, url + "/preview", {"message": "Fix value"})
    approve(api_client, task, "write", preview["scope_hash"])
    (demo_repository / "app.py").write_text("def value():\n    return 2  # edited after tests\n", encoding="utf-8")
    response = api_client.post(url, json={"message": "Fix value", "scope_hash": preview["scope_hash"]})
    assert response.json()["code"] == "COMMIT_PREVIEW_STALE"
    updated = post(api_client, url + "/preview", {"message": "Fix value"})
    approve(api_client, task, "write", updated["scope_hash"])
    response = api_client.post(url, json={"message": "Fix value", "scope_hash": updated["scope_hash"]})
    assert response.json()["code"] == "TEST_RESULTS_STALE"
    assert git_output(demo_repository, "rev-list", "--count", "HEAD").strip() == "1"


def test_expired_approval_cannot_authorize_a_commit(api_client, demo_repository):
    from app import main
    task = passing_task(api_client, demo_repository)
    url = f"/api/v1/tasks/{task['id']}/commits"
    preview = post(api_client, url + "/preview", {"message": "Fix value"})
    approval = approve(api_client, task, "write", preview["scope_hash"])
    main.approval_service.get(approval["id"]).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    response = api_client.post(url, json={"message": "Fix value", "scope_hash": preview["scope_hash"]})
    assert response.json()["code"] == "APPROVAL_REQUIRED"


def test_cancelled_task_cannot_apply_patch(api_client, demo_repository):
    task = create_task(api_client, demo_repository)
    run_url = f"/api/v1/runs/{task['run_id']}"
    preview = post(api_client, run_url + "/patches/preview", {"files": [{"path": "app.py", "content": "value = 3\n"}]})
    post(api_client, f"/api/v1/tasks/{task['id']}/cancel")
    response = api_client.post(run_url + "/patches/apply", json={"patch_id": preview["patch_id"], "confirm": True})
    assert response.json()["code"] == "INVALID_TASK_STATE"
    assert (demo_repository / "app.py").read_text(encoding="utf-8") == "def value():\n    return 1\n"


def test_source_history_and_rollback_endpoints(api_client, demo_repository):
    task = passing_task(api_client, demo_repository)
    source = api_client.get(f"/api/v1/repositories/{task['repository_id']}/files", params={"path": "app.py"})
    assert source.status_code == 200
    assert source.json()["symbols"][0]["name"] == "value"
    history = api_client.get(f"/api/v1/runs/{task['run_id']}/tests").json()
    assert history["items"][0]["result"]["status"] == "passed"
    checkpoints = api_client.get(f"/api/v1/runs/{task['run_id']}/checkpoints").json()["items"]
    assert len(checkpoints) == 1
    restored = post(api_client, f"/api/v1/runs/{task['run_id']}/rollback", {"checkpoint_id": checkpoints[0]["id"]})
    assert restored["restored_files"] == ["app.py"]
    assert (demo_repository / "app.py").read_text(encoding="utf-8") == "def value():\n    return 1\n"
    iteration = api_client.get(f"/api/v1/tasks/{task['id']}/iterations").json()["items"][0]
    assert iteration["rolled_back"]
    response = api_client.post(f"/api/v1/runs/{task['run_id']}/tests", json={"kind": "pytest"})
    assert response.json()["code"] == "INVALID_TASK_STATE"


def test_patch_cannot_cross_run_boundary(api_client, demo_repository):
    task = create_task(api_client, demo_repository)
    preview = post(api_client, f"/api/v1/runs/{task['run_id']}/patches/preview", {"files": [{"path": "app.py", "content": "value = 3\n"}]})
    other = post(api_client, "/api/v1/tasks", {"repository_id": task["repository_id"], "goal": "Another independent task"})
    approve(api_client, other, "plan")
    post(api_client, f"/api/v1/tasks/{other['id']}/iterations")
    response = api_client.post(f"/api/v1/runs/{other['run_id']}/patches/apply", json={"patch_id": preview["patch_id"], "confirm": True})
    assert response.json()["code"] == "PATCH_NOT_FOUND"


def test_running_validation_can_be_cancelled(api_client, demo_repository):
    import time
    from concurrent.futures import ThreadPoolExecutor
    task = create_task(api_client, demo_repository)
    apply_value(api_client, task, 2)
    (demo_repository / "test_app.py").write_text("import time\nfrom pathlib import Path\n\ndef test_slow():\n    Path('.test-started').write_text('started')\n    time.sleep(20)\n", encoding="utf-8")
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(api_client.post, f"/api/v1/runs/{task['run_id']}/tests", json={"kind": "pytest", "timeout_seconds": 15})
        deadline = time.monotonic() + 10
        while not (demo_repository / ".test-started").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (demo_repository / ".test-started").exists()
        assert api_client.get("/health").status_code == 200
        post(api_client, f"/api/v1/tasks/{task['id']}/pause")
        response = future.result(timeout=10)
        assert response.json()["status"] == "cancelled"
    assert api_client.get(f"/api/v1/tasks/{task['id']}").json()["status"] == "paused"
    resumed = post(api_client, f"/api/v1/tasks/{task['id']}/resume")
    assert resumed["task"]["status"] == "testing"


def test_selected_test_and_static_validation(api_client, demo_repository):
    task = create_task(api_client, demo_repository)
    apply_value(api_client, task, 2)
    result = post(api_client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest", "target": "test_app.py::test_value", "timeout_seconds": 15})
    assert result["status"] == "passed", result
    assert result["command"][-1] == "test_app.py::test_value"
    result = post(api_client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "static"})
    assert result["status"] == "passed", result
    assert api_client.get(f"/api/v1/runs/{task['run_id']}/tests").json()["total"] == 2
