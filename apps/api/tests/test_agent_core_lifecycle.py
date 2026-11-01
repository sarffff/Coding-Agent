from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.models import TestRunResponse as RunResult
from app.repository_service import RepositoryError, RepositoryService
from app.store import StateStore
from app.task_service import TaskService


@pytest.fixture
def clock(monkeypatch):
    import app.task_service as task_module

    class Clock(datetime):
        current = datetime(2026, 10, 31, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current

        @classmethod
        def advance(cls, seconds):
            cls.current += timedelta(seconds=seconds)

    monkeypatch.setattr(task_module, "datetime", Clock)
    return Clock


@pytest.fixture
def repositories(tmp_path):
    return RepositoryService(Settings(
        _env_file=None, workspace_root=tmp_path,
        state_dir=tmp_path / "state", task_max_wall_time_seconds=60,
    ))


@pytest.mark.parametrize("waiting_state", ["ready_for_pr", "paused"])
def test_rollback_restarts_active_budget_clock(repositories, clock, waiting_state):
    tasks = TaskService(repositories)
    task = tasks.create("Repair the implementation")
    tasks.start_iteration(task.id)
    tasks.mark_patch_applied(task.run_id, "patch", "checkpoint", ["app.py"])
    clock.advance(10)
    if waiting_state == "paused":
        tasks.pause(task.id)
    else:
        tasks.record_test_result(task.run_id, RunResult(
            run_id=task.run_id, kind="pytest", command=["pytest"], status="passed",
            exit_code=0, duration_ms=1, stdout="", stderr="", validation_hash="current",
        ))
    clock.advance(600)

    tasks.record_rollback(task.run_id, ["checkpoint"])
    assert tasks.get(task.id).status == "coding"
    assert tasks.get_budget(task.id).waiting_since is None
    clock.advance(5)
    assert tasks.get_budget(task.id).elapsed_seconds == 15


def test_restart_and_recovery_wait_do_not_exhaust_active_budget(repositories, tmp_path, clock):
    store = StateStore(tmp_path / "state" / "lifecycle.sqlite3")
    try:
        tasks = TaskService(repositories, store)
        task = tasks.create("Continue a coding task after restarting")
        tasks.start_iteration(task.id)
        clock.advance(20)
        tasks.consume_remote_call(task.id)
        clock.advance(7_200)

        restarted = TaskService(repositories, store)
        assert restarted.get(task.id).requires_recovery is True
        budget = restarted.get_budget(task.id)
        assert budget.exhausted_reason is None
        assert budget.elapsed_seconds == 20
        clock.advance(600)
        assert restarted.get_budget(task.id).elapsed_seconds == 20
        restarted = TaskService(repositories, store)
        assert restarted.get_budget(task.id).elapsed_seconds == 20

        restarted.recover(task.id)
        clock.advance(5)
        restarted.assert_budget_available(task.id)
        assert restarted.get_budget(task.id).elapsed_seconds == 25
        assert restarted.get_budget(task.id).waiting_since is None
    finally:
        store.close()


@pytest.mark.parametrize("reason", ["wall_time", "remote_calls"])
def test_recovery_does_not_reset_an_exhausted_budget(repositories, tmp_path, clock, reason):
    store = StateStore(tmp_path / "state" / "exhausted.sqlite3")
    try:
        tasks = TaskService(repositories, store)
        task = tasks.create("Keep the wall time circuit breaker")
        tasks.start_iteration(task.id)
        if reason == "wall_time":
            clock.advance(60)
        else:
            task.budget.max_remote_calls = 1
            tasks.consume_remote_call(task.id)
            with pytest.raises(RepositoryError, match="task budget is exhausted"):
                tasks.consume_remote_call(task.id)
        assert tasks.get_budget(task.id).exhausted_reason == reason
        clock.advance(600)

        restarted = TaskService(repositories, store)
        restarted.recover(task.id)
        assert restarted.get_budget(task.id).exhausted_reason == reason
        assert restarted.get_budget(task.id).elapsed_seconds == (60 if reason == "wall_time" else 0)
    finally:
        store.close()


def test_last_remote_reply_can_apply_validate_and_commit_before_next_call_is_rejected(demo_repository, tmp_path, monkeypatch):
    from conftest import build_settings, mount_services
    from fastapi.testclient import TestClient
    from test_api_workflow import approve, create_task, post
    from app import main
    from app.model_provider import FakeModelProvider
    from app.models import PatchFile

    services = mount_services(monkeypatch, build_settings(
        demo_repository, tmp_path, llm_provider="fake", task_max_remote_calls=1,
    ))
    FakeModelProvider.reset()
    try:
        with TestClient(main.app) as client:
            task = create_task(client, demo_repository)
            run_url = f"/api/v1/runs/{task['run_id']}"
            task_url = f"/api/v1/tasks/{task['id']}"
            FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])
            drafted = post(client, run_url + "/patches/draft")
            budget = client.get(task_url + "/budget").json()
            assert budget["remote_calls"] == budget["max_remote_calls"] == 1
            assert budget["exhausted_reason"] is None
            post(client, run_url + "/patches/apply", {"patch_id": drafted["patch_id"], "confirm": True})
            assert post(client, run_url + "/tests", {"kind": "pytest", "timeout_seconds": 15})["status"] == "passed"
            preview = post(client, task_url + "/commits/preview", {"message": "Fix value"})
            approve(client, task, "write", preview["scope_hash"])
            post(client, task_url + "/commits", {"message": "Fix value", "scope_hash": preview["scope_hash"]})

            approval = post(client, task_url + "/approvals", {"type": "write", "summary": "Review a follow-up change"})
            post(client, f"/api/v1/approvals/{approval['id']}/decision", {"decision": "request_changes", "reason": "Handle the next input case"})
            post(client, f"/api/v1/approvals/{approval['id']}/continue")
            FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 3\n")])
            rejected = client.post(run_url + "/patches/draft", json={})
            assert rejected.status_code == 409, rejected.text
            assert rejected.json()["code"] == "TASK_BUDGET_EXHAUSTED"
            assert rejected.json()["details"]["reason"] == "remote_calls"
            budget = client.get(task_url + "/budget").json()
            assert budget["remote_calls"] == 1
            assert budget["exhausted_reason"] == "remote_calls"
            assert len(FakeModelProvider._calls) == 1
            assert (demo_repository / "app.py").read_text(encoding="utf-8") == "def value():\n    return 2\n"
    finally:
        FakeModelProvider.reset()
        services.store.close()
