import asyncio
from threading import Event

import pytest

from app import main
from app.models import TestRunResponse as RunResult
from app.repository_service import RepositoryError
from test_api_workflow import create_task
from test_model_draft import model_api


def test_budget_rejection_after_command_reservation_releases_the_slot(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    slot = task["run_id"] + main.test_service.COMMAND_SUFFIX

    def reject(task_id):
        assert task_id == task["id"]
        assert main.test_service.is_running(slot)
        raise RepositoryError("TASK_BUDGET_EXHAUSTED", "The task budget expired before execution.")

    def unexpected_command(*args, **kwargs):
        raise AssertionError("A refused command must never start.")

    monkeypatch.setattr(main.task_service, "consume_remote_call", reject)
    monkeypatch.setattr(main.test_service, "run_command", unexpected_command)
    with pytest.raises(RepositoryError) as error:
        asyncio.run(main.agent_service._run_for_model(main.task_service.get(task["id"]), ["python", "probe.py"]))
    assert error.value.code == "TASK_BUDGET_EXHAUSTED"
    assert not main.test_service.is_running(slot)
    main.test_service.reserve(slot)
    main.test_service.release(slot)


def test_cancelled_command_waits_for_late_write_and_records_drift_without_success(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    slot = task["run_id"] + main.test_service.COMMAND_SUFFIX
    started, release, finished = Event(), Event(), Event()
    late_file = directory / "late.py"

    def command(run_id, repository_id, argv, *, reserved):
        assert reserved
        started.set()
        assert release.wait(5), "Command worker was not released."
        late_file.write_text("LATE_WRITE = 1\n", encoding="utf-8")
        finished.set()
        return RunResult(run_id=run_id, kind="auto", command=argv, status="passed",
                         exit_code=0, duration_ms=1, stdout="probe", stderr="")

    monkeypatch.setattr(main.test_service, "run_command", command)

    async def cancel():
        running = asyncio.create_task(main.agent_service._run_for_model(
            main.task_service.get(task["id"]), ["python", "probe.py"],
        ))
        try:
            assert await asyncio.to_thread(started.wait, 5), "Command worker did not start."
            main.task_service.pause(task["id"])
            running.cancel()
            await asyncio.sleep(0)
            assert not running.done()
            assert main.test_service.is_running(slot)
            assert not late_file.exists()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 5)

    asyncio.run(cancel())
    assert finished.is_set()
    assert late_file.read_text(encoding="utf-8") == "LATE_WRITE = 1\n"
    assert not main.test_service.is_running(slot)
    assert main.task_service.get(task["id"]).status == "paused"
    events = [event for event in main.audit_service.list_for_run(task["run_id"]) if event.action == "context.request"]
    assert len(events) == 1
    assert events[0].status == "failed"
    assert events[0].details["workspace_changed"] is True
    assert events[0].details["status"] == "interrupted"
