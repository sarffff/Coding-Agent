import pytest
from conftest import build_settings, git_output, mount_services
from test_api_workflow import create_task, post


def test_budget_circuit_breaker_latches_and_the_agent_stops_for_review(demo_repository, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    settings = build_settings(demo_repository, tmp_path, task_max_iterations=1)
    services = mount_services(monkeypatch, settings)
    with TestClient(main.app) as client:
        task = create_task(client, demo_repository)  # starts iteration #1 (the ceiling)

        budget = client.get(f"/api/v1/tasks/{task['id']}/budget").json()
        assert budget["iterations"] == 1 and budget["max_iterations"] == 1
        assert budget["exhausted_reason"] is None  # one iteration is still within budget

        blocked = client.post(f"/api/v1/tasks/{task['id']}/iterations", json={})
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "TASK_BUDGET_EXHAUSTED"
        assert blocked.json()["details"]["reason"] == "iterations"

        # The breaker latches: the agent now reports the budget the panel renders and
        # refuses to advance until the task is reviewed or cancelled.
        state = client.get(f"/api/v1/tasks/{task['id']}/agent").json()
        assert state["action"] == "manual_review"
        assert state["can_advance"] is False
        assert state["task"]["budget"]["exhausted_reason"] == "iterations"
    services.store.close()


@pytest.mark.parametrize("final_status", ["failed", "passed"])
def test_only_a_passing_test_resets_failures_between_successful_model_and_remote_calls(demo_repository, tmp_path, monkeypatch, final_status):
    from fastapi.testclient import TestClient
    from app import main
    from app.model_provider import FakeModelProvider
    from app.models import PatchFile, TestRunResponse

    git_output(demo_repository, "remote", "add", "origin", "https://github.com/forge-demo/budget.git")
    settings = build_settings(demo_repository, tmp_path, llm_provider="fake", remote_provider="fake", task_max_consecutive_failures=2)
    services = mount_services(monkeypatch, settings)
    FakeModelProvider.reset()
    try:
        with TestClient(main.app) as client:
            task = create_task(client, demo_repository)
            task_url = f"/api/v1/tasks/{task['id']}"
            tasks = services.task_service

            def record_test(status):
                tasks.begin_test_run(task["run_id"])
                tasks.record_test_result(task["run_id"], TestRunResponse(
                    run_id=task["run_id"], kind="pytest", command=["pytest"], status=status,
                    exit_code=0 if status == "passed" else 1, duration_ms=1, stdout="", stderr="",
                ))

            record_test("failed")
            post(client, task_url + "/repairs")
            FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])
            post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
            assert client.get(task_url + "/remote-target").status_code == 200
            budget = client.get(task_url + "/budget").json()
            assert budget["remote_calls"] == 2
            assert budget["consecutive_failures"] == 1

            record_test(final_status)
            budget = client.get(task_url + "/budget").json()
            assert budget["consecutive_failures"] == (2 if final_status == "failed" else 0)
            assert budget["exhausted_reason"] == ("consecutive_failures" if final_status == "failed" else None)
            if final_status == "failed":
                assert client.get(task_url + "/agent").json()["action"] == "manual_review"
                assert client.post(task_url + "/repairs", json={}).json()["code"] == "TASK_BUDGET_EXHAUSTED"
    finally:
        FakeModelProvider.reset()
        services.store.close()
