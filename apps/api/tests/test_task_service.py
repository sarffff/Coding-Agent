import pytest

from app.models import TestRunResponse as RunResult
from app.repository_service import RepositoryService
from app.task_service import TaskService, TaskStateError


def test_task_can_run_two_coding_iterations(repository_service: RepositoryService) -> None:
    service = TaskService(repository_service)
    task = service.create("Fix the flaky parser test")

    first = service.start_iteration(task.id)
    assert first.number == 1
    assert service.get(task.id).status == "coding"

    service.mark_patch_applied(task.run_id, "patch-one", "checkpoint-one", ["parser.py"])
    failed = RunResult(
        run_id=task.run_id,
        kind="pytest",
        command=["pytest", "-q"],
        status="failed",
        exit_code=1,
        duration_ms=12,
        stdout="parser.py:18",
        stderr="assertion failed",
    )
    service.record_test_result(task.run_id, failed)
    assert service.get(task.id).status == "failed"
    assert service.get(task.id).retry_count == 1
    assert service.list_iterations(task.id)[0].failure_summary == "assertion failed"

    second = service.start_repair(task.id, "Handle the empty input case")
    assert second.number == 2
    assert service.get(task.id).current_iteration == 2

    service.mark_patch_applied(task.run_id, "patch-two", "checkpoint-two", ["parser.py", "test_parser.py"])
    passed = failed.model_copy(update={"status": "passed", "exit_code": 0, "stderr": "", "stdout": "2 passed"})
    service.record_test_result(task.run_id, passed)
    assert service.get(task.id).status == "ready_for_pr"
    assert service.list_iterations(task.id)[1].status == "passed"


def test_pause_resume_and_invalid_transition(repository_service: RepositoryService) -> None:
    service = TaskService(repository_service)
    task = service.create("Update the README")
    service.start_iteration(task.id)
    paused = service.pause(task.id)
    assert paused.status == "paused"
    assert len(service.list_checkpoints(task.id)) == 1
    assert service.resume(task.id).status == "coding"

    assert service.pause(task.id).status == "paused"
    assert len(service.list_checkpoints(task.id)) == 2
    assert service.pause(task.id).status == "paused"
    assert len(service.list_checkpoints(task.id)) == 2
    service.cancel(task.id)
    with pytest.raises(TaskStateError):
        service.resume(task.id)
    with pytest.raises(TaskStateError):
        service.start_iteration(task.id)


@pytest.fixture
def repository_service(tmp_path):
    from app.config import Settings
    return RepositoryService(Settings(workspace_root=tmp_path))


def test_paused_task_cannot_write_or_test_and_restores_step(repository_service):
    service = TaskService(repository_service)
    task = service.create("Repair a validation failure")
    service.start_iteration(task.id)
    service.mark_patch_applied(task.run_id, "patch", "checkpoint", ["app.py"])
    service.pause(task.id)
    assert service.list_checkpoints(task.id)[-1].state == "testing"
    with pytest.raises(TaskStateError):
        service.assert_patch_allowed(task.run_id)
    with pytest.raises(TaskStateError):
        service.begin_test_run(task.run_id)
    assert service.resume(task.id).status == "testing"
