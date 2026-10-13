from __future__ import annotations

from typing import Any

import pytest

from app.models import TestRunResponse as ValidationResult


def post(client, url: str, payload: dict[str, Any] | None = None, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
    response = client.post(url, json=payload or {}, headers=headers or {})
    assert response.status_code in {200, 201}, response.text
    return response.json()


def create_agent_task(client, directory):
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(
        client,
        "/api/v1/tasks",
        {"repository_id": repository["id"], "goal": "Fix value and verify the local test"},
    )
    return task


def agent_state(client, task_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/tasks/{task_id}/agent")
    assert response.status_code == 200, response.text
    return response.json()


def agent_advance(client, task_id: str, payload: dict[str, Any] | None = None, *, key: str | None = None) -> dict[str, Any]:
    headers = {"Idempotency-Key": key} if key else None
    return post(client, f"/api/v1/tasks/{task_id}/agent/advance", payload, headers=headers)


def approve_plan(client, task: dict[str, Any]) -> dict[str, Any]:
    requested = agent_advance(client, task["id"])
    assert requested["performed_action"] == "request_plan_approval"
    assert requested["pending_approval"]["type"] == "plan"
    approval_id = requested["pending_approval"]["id"]
    duplicate = agent_advance(client, task["id"])
    assert duplicate["performed_action"] == "noop"
    assert duplicate["pending_approval"]["id"] == approval_id
    assert client.get(f"/api/v1/tasks/{task['id']}/approvals").json()["total"] == 1
    return post(client, f"/api/v1/approvals/{approval_id}/decision", {"decision": "approve"})


def create_branch(client, task: dict[str, Any]) -> dict[str, Any]:
    return post(client, f"/api/v1/tasks/{task['id']}/branch", {"name": f"codex/{task['id']}"})


def apply_value(client, task: dict[str, Any], value: int) -> dict[str, Any]:
    preview = post(
        client,
        f"/api/v1/runs/{task['run_id']}/patches/preview",
        {"files": [{"path": "app.py", "content": f"def value():\n    return {value}\n"}]},
    )
    return post(
        client,
        f"/api/v1/runs/{task['run_id']}/patches/apply",
        {"patch_id": preview["patch_id"], "confirm": True},
    )


def start_agent_iteration(client, task: dict[str, Any], *, key: str | None = None) -> dict[str, Any]:
    result = agent_advance(client, task["id"], key=key)
    assert result["performed_action"] == "start_iteration", result
    assert result["action"] == "review_patch", result
    return result


def test_agent_local_loop_approval_patch_test_repair_and_idempotency(api_client, demo_repository):
    task = create_agent_task(api_client, demo_repository)

    initial = agent_state(api_client, task["id"])
    assert initial["action"] == "request_plan_approval"
    assert initial["can_advance"] is True
    assert initial["requires_approval"] is True
    assert initial["approval_type"] == "plan"
    denied = api_client.post(f"/api/v1/tasks/{task['id']}/agent/advance", json={"action": "start_iteration"})
    assert denied.status_code == 409
    assert denied.json()["code"] == "AGENT_ACTION_NOT_AVAILABLE"
    assert api_client.get(f"/api/v1/tasks/{task['id']}/iterations").json()["total"] == 0

    approve_plan(api_client, task)
    after_plan = agent_state(api_client, task["id"])
    assert after_plan["action"] == "create_branch"
    assert after_plan["can_advance"] is False
    blocked = agent_advance(api_client, task["id"])
    assert blocked["performed_action"] == "noop"
    assert blocked["action"] == "create_branch"

    create_branch(api_client, task)
    start_agent_iteration(api_client, task, key="agent-start-iteration-1")
    replay = agent_advance(api_client, task["id"], key="agent-start-iteration-1")
    assert replay["performed_action"] == "start_iteration"
    repeated = agent_advance(api_client, task["id"])
    assert repeated["performed_action"] == "noop"
    assert repeated["action"] == "review_patch"
    assert api_client.get(f"/api/v1/tasks/{task['id']}/iterations").json()["total"] == 1

    apply_value(api_client, task, 0)
    failed = agent_advance(
        api_client,
        task["id"],
        {"action": "run_tests", "kind": "pytest", "target": "test_app.py::test_value", "timeout_seconds": 15},
    )
    assert failed["performed_action"] == "run_tests"
    assert failed["last_test"]["status"] == "failed"
    assert failed["action"] == "start_repair"

    (demo_repository / "app.py").write_text("def value():\n    return 0  # changed after failure\n", encoding="utf-8")
    assert agent_state(api_client, task["id"])["action"] == "run_tests"
    rerun = agent_advance(api_client, task["id"], {"kind": "pytest", "timeout_seconds": 15})
    assert rerun["performed_action"] == "run_tests"
    assert rerun["last_test"]["status"] == "failed"
    assert rerun["action"] == "start_repair"

    repaired = agent_advance(api_client, task["id"])
    assert repaired["performed_action"] == "start_repair"
    assert repaired["iteration"]["number"] == 2
    assert rerun["iteration"]["failure_summary"] in repaired["iteration"]["goal"]
    assert repaired["action"] == "review_patch"

    apply_value(api_client, task, 2)
    passed = agent_advance(
        api_client,
        task["id"],
        {"action": "run_tests", "kind": "pytest", "target": "test_app.py::test_value", "timeout_seconds": 15},
    )
    assert passed["performed_action"] == "run_tests"
    assert passed["last_test"]["status"] == "passed"
    assert passed["action"] == "review_commit"
    assert passed["task"]["status"] == "ready_for_pr"

    (demo_repository / "app.py").write_text("def value():\n    return 2  # changed after validation\n", encoding="utf-8")
    stale = agent_state(api_client, task["id"])
    assert stale["action"] == "run_tests"
    assert stale["can_advance"] is True


def test_agent_rollback_pause_and_cancel_are_gated(api_client, demo_repository):
    task = create_agent_task(api_client, demo_repository)
    approve_plan(api_client, task)
    create_branch(api_client, task)
    start_agent_iteration(api_client, task)
    applied = apply_value(api_client, task, 2)

    rolled_back = post(
        api_client,
        f"/api/v1/runs/{task['run_id']}/rollback",
        {"checkpoint_id": applied["checkpoint"]["id"]},
    )
    assert rolled_back["restored_files"] == ["app.py"]
    after_rollback = agent_state(api_client, task["id"])
    assert after_rollback["action"] == "start_iteration"
    assert after_rollback["can_advance"] is True
    started_again = agent_advance(api_client, task["id"])
    assert started_again["performed_action"] == "start_iteration"
    assert started_again["iteration"]["number"] == 2

    paused = post(api_client, f"/api/v1/tasks/{task['id']}/pause")
    assert paused["task"]["status"] == "paused"
    paused_state = agent_state(api_client, task["id"])
    assert paused_state["action"] == "paused"
    assert paused_state["can_advance"] is False
    assert agent_advance(api_client, task["id"])["performed_action"] == "noop"

    cancelled = post(api_client, f"/api/v1/tasks/{task['id']}/cancel")
    assert cancelled["task"]["status"] == "cancelled"
    cancelled_state = agent_state(api_client, task["id"])
    assert cancelled_state["action"] == "cancelled"
    assert cancelled_state["can_advance"] is False
    assert agent_advance(api_client, task["id"])["performed_action"] == "noop"


@pytest.mark.parametrize("result_status", ["not_found", "blocked", "timed_out"])
def test_agent_routes_nonrepairable_test_results_to_manual_review(api_client, demo_repository, result_status, monkeypatch):
    from app import main

    task = create_agent_task(api_client, demo_repository)
    approve_plan(api_client, task)
    create_branch(api_client, task)
    start_agent_iteration(api_client, task)
    apply_value(api_client, task, 2)

    def fake_run(run_id, repository_id, kind, timeout_seconds, target=None):
        try:
            return ValidationResult(
                run_id=run_id,
                kind=kind,
                command=["fake-test"],
                status=result_status,
                duration_ms=1,
                stdout="",
                stderr="",
            )
        finally:
            main.test_service.release(run_id)

    monkeypatch.setattr(main.agent_service.tests, "run", fake_run)
    result = agent_advance(api_client, task["id"], {"action": "run_tests", "kind": "pytest"})
    assert result["last_test"]["status"] == result_status
    state = agent_state(api_client, task["id"])
    assert state["action"] == "manual_review"
    assert state["can_advance"] is False
    assert agent_advance(api_client, task["id"])["performed_action"] == "noop"


def test_agent_static_only_validation_requires_project_test(api_client, demo_repository):
    task = create_agent_task(api_client, demo_repository)
    approve_plan(api_client, task)
    create_branch(api_client, task)
    start_agent_iteration(api_client, task)
    apply_value(api_client, task, 2)

    result = agent_advance(api_client, task["id"], {"action": "run_tests", "kind": "static"})
    assert result["last_test"]["status"] == "passed"
    state = agent_state(api_client, task["id"])
    assert state["action"] == "run_tests"
    assert state["can_advance"] is True


def test_agent_recovery_blocks_auto_write(api_stack, demo_repository):
    client = api_stack.restart()
    task = create_agent_task(client, demo_repository)
    approve_plan(client, task)
    branch = create_branch(client, task)

    restarted = api_stack.restart()
    state = agent_state(restarted, task["id"])
    assert state["action"] == "recover"
    assert state["can_advance"] is False
    assert state["task"]["task_branch"] == branch["branch"]
    advanced = agent_advance(restarted, task["id"])
    assert advanced["performed_action"] == "noop"
    assert restarted.get(f"/api/v1/tasks/{task['id']}/iterations").json()["total"] == 0

    post(restarted, f"/api/v1/tasks/{task['id']}/recover")
    recovered = agent_state(restarted, task["id"])
    assert recovered["action"] == "start_iteration"
    assert recovered["task"]["task_branch"] == branch["branch"]
    assert start_agent_iteration(restarted, task)["iteration"]["number"] == 1


def test_agent_running_validation_is_not_repeated_and_can_be_paused(api_client, demo_repository):
    import time
    from concurrent.futures import ThreadPoolExecutor

    task = create_agent_task(api_client, demo_repository)
    approve_plan(api_client, task)
    create_branch(api_client, task)
    start_agent_iteration(api_client, task)
    apply_value(api_client, task, 2)
    (demo_repository / "test_app.py").write_text(
        "import time\nfrom pathlib import Path\n\ndef test_slow():\n"
        "    Path('.test-started').write_text('started')\n    time.sleep(20)\n",
        encoding="utf-8",
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            agent_advance,
            api_client,
            task["id"],
            {"action": "run_tests", "kind": "pytest", "timeout_seconds": 15},
        )
        deadline = time.monotonic() + 10
        while not (demo_repository / ".test-started").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        try:
            assert (demo_repository / ".test-started").exists()
            running = agent_advance(api_client, task["id"])
            assert running["action"] == "wait_for_tests"
            assert running["can_advance"] is False
            assert running["performed_action"] == "noop"
        finally:
            post(api_client, f"/api/v1/tasks/{task['id']}/pause")
        cancelled = pending.result(timeout=10)
    assert cancelled["last_test"]["status"] == "cancelled"
    assert cancelled["task"]["status"] == "paused"
    assert agent_state(api_client, task["id"])["action"] == "paused"
    assert api_client.get(f"/api/v1/runs/{task['run_id']}/tests").json()["total"] == 1
