import json

import pytest

from app import main
from app.models import TestRunResponse as ValidationResult
from test_api_workflow import post
from test_agent_workflow import approve_plan, create_branch, start_agent_iteration, apply_value, agent_state
from test_agent_loop import model_api


CHECKS = [
    {"id": "api", "label": "API tests", "kind": "pytest", "target": "test_app.py"},
    {"id": "types", "label": "Type check", "kind": "static"},
    {"id": "build", "label": "Build", "kind": "script", "script": "build"},
]


def prepared_task(client, directory, checks=CHECKS):
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Fix value and validate every project", "validation_checks": checks})
    approve_plan(client, task)
    create_branch(client, task)
    start_agent_iteration(client, task)
    apply_value(client, task, 2)
    return task


def stub_runner(monkeypatch, statuses):
    calls = []

    def run(run_id, repository_id, kind, timeout_seconds, target=None, **kwargs):
        calls.append((kind, target, kwargs))
        main.test_service.release(run_id)
        return ValidationResult(run_id=run_id, kind=kind, command=["fixture", kind], status=next(statuses), duration_ms=1, stdout="", stderr="")

    monkeypatch.setattr(main.test_service, "run", run)
    return calls


def test_required_checks_run_in_order_and_selected_test_cannot_complete_task(api_client, demo_repository, monkeypatch):
    task = prepared_task(api_client, demo_repository)
    calls = stub_runner(monkeypatch, iter(["passed"] * 5))
    endpoint = f"/api/v1/runs/{task['run_id']}/tests"
    selected = post(api_client, endpoint, {"kind": "pytest", "target": "test_app.py::test_value"})
    assert selected.get("check_id") is None
    assert agent_state(api_client, task["id"])["task"]["status"] == "testing"

    for index, check in enumerate(CHECKS):
        result = post(api_client, endpoint)
        assert result["check_id"] == check["id"]
        state = agent_state(api_client, task["id"])
        assert state["action"] == ("review_commit" if index == 2 else "run_tests")
        assert [item["status"] for item in state["validation_status"]] == ["passed"] * (index + 1) + ["pending"] * (2 - index)
    assert [call[0] for call in calls] == ["pytest", "pytest", "static", "script"]

    (demo_repository / "app.py").write_text("def value():\n    return 2  # changed\n", encoding="utf-8")
    state = agent_state(api_client, task["id"])
    assert state["action"] == "run_tests"
    assert {item["status"] for item in state["validation_status"]} == {"stale"}
    assert post(api_client, endpoint)["check_id"] == "api"


def test_failed_required_check_cannot_be_hidden_by_a_manual_pass(api_client, demo_repository, monkeypatch):
    task = prepared_task(api_client, demo_repository)
    stub_runner(monkeypatch, iter(["passed", "failed", "passed"]))
    endpoint = f"/api/v1/runs/{task['run_id']}/tests"
    post(api_client, endpoint)
    post(api_client, endpoint)
    state = agent_state(api_client, task["id"])
    assert state["action"] == "start_repair"
    post(api_client, endpoint, {"kind": "pytest", "target": "test_app.py::test_value"})
    state = agent_state(api_client, task["id"])
    assert state["action"] != "review_commit"
    assert state["task"]["status"] != "ready_for_pr"
    response = api_client.post(f"/api/v1/tasks/{task['id']}/commits", json={"message": "Fix", "scope_hash": "0" * 64})
    assert response.status_code == 400
    assert response.json()["code"] == "COMMIT_NOT_READY"


def test_checklist_changes_require_new_plan_approval_and_lock_after_start(api_client, demo_repository):
    repository = post(api_client, "/api/v1/repositories", {"path": str(demo_repository)})
    task = post(api_client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Fix value"})
    approve_plan(api_client, task)
    endpoint = f"/api/v1/tasks/{task['id']}/validation-checks"
    response = api_client.put(endpoint, json={"checks": CHECKS})
    assert response.status_code == 200, response.text
    assert [check["id"] for check in response.json()["validation_checks"]] == ["api", "types", "build"]
    assert agent_state(api_client, task["id"])["action"] == "request_plan_approval"
    # Approve the new scope (the helper assumes only one approval exists).
    requested = post(api_client, f"/api/v1/tasks/{task['id']}/agent/advance")
    post(api_client, f"/api/v1/approvals/{requested['pending_approval']['id']}/decision", {"decision": "approve"})
    create_branch(api_client, task)
    start_agent_iteration(api_client, task)
    locked = api_client.put(endpoint, json={"checks": []})
    assert locked.status_code == 400
    assert locked.json()["code"] == "INVALID_TASK_STATE"


def test_duplicate_check_ids_are_rejected(api_client, demo_repository):
    repository = post(api_client, "/api/v1/repositories", {"path": str(demo_repository)})
    response = api_client.post("/api/v1/tasks", json={"repository_id": repository["id"], "goal": "Fix value", "validation_checks": [CHECKS[0], CHECKS[0]]})
    assert response.status_code == 422


def test_checklist_and_partial_results_survive_restart(api_stack, demo_repository, monkeypatch):
    client = api_stack.restart()
    task = prepared_task(client, demo_repository)
    stub_runner(monkeypatch, iter(["passed"]))
    post(client, f"/api/v1/runs/{task['run_id']}/tests")
    restarted = api_stack.restart()
    state = agent_state(restarted, task["id"])
    assert state["action"] == "recover"
    assert [item["status"] for item in state["validation_status"]] == ["passed", "pending", "pending"]
    post(restarted, f"/api/v1/tasks/{task['id']}/recover")
    stub_runner(monkeypatch, iter(["passed"]))
    assert post(restarted, f"/api/v1/runs/{task['run_id']}/tests")["check_id"] == "types"


@pytest.mark.parametrize("changed", ["notes.txt", "package.json"])
def test_changes_outside_task_patch_invalidate_required_checks(api_client, demo_repository, monkeypatch, changed):
    task = prepared_task(api_client, demo_repository)
    stub_runner(monkeypatch, iter(["passed"]))
    post(api_client, f"/api/v1/runs/{task['run_id']}/tests")
    (demo_repository / changed).write_text("{}", encoding="utf-8")
    assert agent_state(api_client, task["id"])["validation_status"][0]["status"] == "stale"


def test_loop_executes_actual_api_check_and_package_scripts(model_api):
    from test_agent_loop import _wait_for_loop
    from app.model_provider import FakeModelProvider
    from app.models import PatchFile
    from conftest import git_output

    client, directory = model_api
    (directory / "package.json").write_text(json.dumps({"scripts": {
        "check": "node -e \"require('node:assert/strict').equal(2 + 2, 4); console.log('types-ok')\"",
        "build": "node -e \"console.log('build-ok')\"",
    }}), encoding="utf-8")
    git_output(directory, "add", "package.json")
    git_output(directory, "commit", "-qm", "Add validation scripts")
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    checks = [CHECKS[0], {**CHECKS[1], "kind": "script", "script": "check"}, CHECKS[2]]
    task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Fix value", "validation_checks": checks})
    FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])
    post(client, f"/api/v1/runs/{task['run_id']}/loop")
    state = _wait_for_loop(client, task["id"], timeout=60)
    assert state["action"] == "review_push", state
    history = client.get(f"/api/v1/runs/{task['run_id']}/tests").json()["items"]
    assert [item["result"]["check_id"] for item in history] == ["api", "types", "build"]
    assert all(item["result"]["status"] == "passed" for item in history)
    assert "types-ok" in history[1]["result"]["stdout"]
    assert "build-ok" in history[2]["result"]["stdout"]
