import asyncio
import time
from contextlib import suppress
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app import main
from app import agent_service as agent_module
from app.model_provider import ContextRequest, PatchProposal
from app.models import PatchFile, TestRunRequest as ValidationRequest, TestRunResponse as ValidationResult
from app.repository_service import RepositoryError
from test_agent_loop import _wait_for_loop
from test_api_workflow import post
from test_workspace_ownership import assert_busy, folder_task, workspace_api


def tasks_in_one_folder(client, tmp_path):
    directory = tmp_path / "project"
    directory.mkdir()
    (directory / "test_app.py").write_text("assert True\n", encoding="utf-8")
    return folder_task(client, directory), folder_task(client, directory), directory


def controlled_validation(monkeypatch):
    entered, released, finished = Event(), Event(), Event()

    def run(run_id, repository_id, kind, timeout_seconds, target=None, **options):
        cancel = main.test_service._active[run_id]
        entered.set()
        try:
            assert released.wait(10), "validation worker was not released"
            return ValidationResult(run_id=run_id, kind=kind, command=["python", "-m", "pytest"],
                                    status="cancelled" if cancel.is_set() else "passed", duration_ms=1, stdout="", stderr="")
        finally:
            main.test_service.release(run_id)
            finished.set()

    monkeypatch.setattr(main.test_service, "run", run)
    return entered, released, finished


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_baseline_stop_keeps_workspace_reserved_until_worker_exits(workspace_api, tmp_path, monkeypatch, action):
    client = workspace_api
    owner, other, directory = tasks_in_one_folder(client, tmp_path)
    original = (directory / "app.py").read_bytes()
    entered, released, finished = controlled_validation(monkeypatch)
    post(client, f"/api/v1/runs/{owner['run_id']}/loop")
    try:
        assert entered.wait(5), "baseline validation did not start"
        post(client, f"/api/v1/tasks/{owner['id']}/{action}")
        # Repeated stops must not cancel the protected worker or release its lease.
        post(client, f"/api/v1/runs/{owner['run_id']}/loop/stop")
        post(client, f"/api/v1/runs/{owner['run_id']}/loop/stop")
        assert_busy(client.post(f"/api/v1/runs/{other['run_id']}/patches/preview", json={
            "files": [{"path": "app.py", "content": "def value():\n    return 2\n"}],
        }))
        assert main.test_service.is_running(owner["run_id"])
        assert not finished.is_set()
    finally:
        released.set()
        final = _wait_for_loop(client, owner["id"], timeout=5)
    assert finished.is_set()
    assert not main.test_service.is_running(owner["run_id"])
    assert final["task"]["status"] == ("paused" if action == "pause" else "cancelled")
    assert final["task"]["budget"]["consecutive_failures"] == 0
    assert main.task_service.validation_baseline(owner["id"]) == []
    assert (directory / "app.py").read_bytes() == original
    assert client.post(f"/api/v1/runs/{other['run_id']}/patches/preview", json={
        "files": [{"path": "app.py", "content": "def value():\n    return 2\n"}],
    }).status_code == 200


def test_cancelled_manual_validation_waits_through_repeated_cancellation(workspace_api, tmp_path, monkeypatch):
    client = workspace_api
    owner, other, _ = tasks_in_one_folder(client, tmp_path)
    entered, released, finished = controlled_validation(monkeypatch)

    async def scenario():
        pending = asyncio.create_task(main.agent_service.run_tests(owner["run_id"], ValidationRequest(kind="pytest")))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            main.task_service.pause(owner["id"])
            pending.cancel()
            await asyncio.sleep(0)
            pending.cancel()
            await asyncio.sleep(0)
            assert not pending.done()
            assert main.test_service.is_running(owner["run_id"])
            assert not finished.is_set()
            with pytest.raises(RepositoryError) as error:
                main.agent_service.assert_workspace_available(other["id"])
            assert error.value.code == "WORKSPACE_BUSY"
            released.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
        finally:
            released.set()
            with suppress(asyncio.CancelledError):
                await pending

    asyncio.run(scenario())
    assert finished.is_set()
    assert not main.test_service.is_running(owner["run_id"])
    assert main.task_service.get(owner["id"]).budget.consecutive_failures == 0
    assert main.task_service.list_iterations(owner["id"])[0].test_runs == []
    main.agent_service.assert_workspace_available(other["id"])


@pytest.mark.parametrize("stage", ["baseline", "tests", "command"])
@pytest.mark.parametrize("fault", ["missing", "exception"])
def test_runner_fault_is_reported_and_releases_validation_reservation(workspace_api, tmp_path, monkeypatch, stage, fault):
    owner, other, _ = tasks_in_one_folder(workspace_api, tmp_path)

    def broken_run(*args, **kwargs):
        if fault == "exception":
            raise RuntimeError("runner failed before cleanup")
        return None

    monkeypatch.setattr(main.test_service, "run_command" if stage == "command" else "run", broken_run)

    async def scenario():
        if stage == "baseline":
            with main.agent_service.workspace_operation(owner["id"]):
                await main.agent_service._ensure_validation_baseline(owner["id"])
        elif stage == "tests":
            await main.agent_service.run_tests(owner["run_id"], ValidationRequest(kind="pytest"))
        else:
            with main.agent_service.workspace_operation(owner["id"]):
                await main.agent_service._run_for_model(main.task_service.get(owner["id"]), ["python", "-c", "print(1)"])

    if fault == "exception":
        with pytest.raises(RuntimeError, match="runner failed before cleanup"):
            asyncio.run(scenario())
    else:
        with pytest.raises(RepositoryError) as error:
            asyncio.run(scenario())
        assert error.value.code == "VALIDATION_RESULT_UNAVAILABLE"
    assert not main.test_service.is_running(owner["run_id"])
    assert not main.test_service.is_running(owner["run_id"] + main.test_service.COMMAND_SUFFIX)
    assert main.task_service.get(owner["id"]).budget.consecutive_failures == 0
    main.agent_service.assert_workspace_available(other["id"])


@pytest.mark.parametrize("action", ["stop", "pause", "cancel"])
def test_model_command_stop_keeps_workspace_until_worker_exits(workspace_api, tmp_path, monkeypatch, action):
    client = workspace_api
    owner, other, directory = tasks_in_one_folder(client, tmp_path)
    original = (directory / "app.py").read_bytes()
    entered, released, finished = Event(), Event(), Event()
    calls = []

    def execute(run_id, kind, argv, command, workdir, cancel, limit, started, details):
        entered.set()
        try:
            assert released.wait(10), "command worker was not released"
            return ValidationResult(run_id=run_id, kind=kind, command=command, status="cancelled" if cancel.is_set() else "passed",
                                    duration_ms=1, stdout="", stderr="")
        finally:
            finished.set()

    async def model(goal, context, failure):
        calls.append(goal)
        return PatchProposal(context_requests=[ContextRequest(action="run", command=["python", "-c", "print(1)"])])

    monkeypatch.setattr(main.test_service, "_execute", execute)
    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    # The command probe, rather than baseline validation, owns the controlled worker.
    main.task_service.record_validation_baseline(owner["id"], [], "already-captured")
    post(client, f"/api/v1/runs/{owner['run_id']}/loop")
    try:
        assert entered.wait(5), "model command did not start"
        path = f"/api/v1/runs/{owner['run_id']}/loop/stop" if action == "stop" else f"/api/v1/tasks/{owner['id']}/{action}"
        post(client, path)
        post(client, f"/api/v1/runs/{owner['run_id']}/loop/stop")
        post(client, f"/api/v1/runs/{owner['run_id']}/loop/stop")
        # Let cancellation settle: a command still cleaning up must retain the directory.
        deadline = time.perf_counter() + 0.2
        while time.perf_counter() < deadline and main.agent_service._loop_running(owner["id"]):
            time.sleep(0.01)
        assert not finished.is_set()
        assert_busy(client.post(f"/api/v1/runs/{other['run_id']}/patches/preview", json={
            "files": [{"path": "app.py", "content": "def value():\n    return 2\n"}],
        }))
        assert main.agent_service._loop_running(owner["id"])
    finally:
        released.set()
        final = _wait_for_loop(client, owner["id"], timeout=5)
    assert finished.is_set()
    assert len(calls) == 1
    assert not main.test_service.is_running(owner["run_id"] + main.test_service.COMMAND_SUFFIX)
    assert final["task"]["status"] == {"stop": "coding", "pause": "paused", "cancel": "cancelled"}[action]
    assert final["task"]["budget"]["consecutive_failures"] == 0
    assert (directory / "app.py").read_bytes() == original
    assert client.get(f"/api/v1/runs/{owner['run_id']}/checkpoints").json()["total"] == 0
    main.agent_service.assert_workspace_available(other["id"])


def test_model_command_cancelled_before_worker_start_never_spawns_process(workspace_api, tmp_path, monkeypatch):
    owner, other, _ = tasks_in_one_folder(workspace_api, tmp_path)
    original_threadpool = agent_module.run_in_threadpool
    executions = []

    def execute(*args, **kwargs):
        executions.append(args)
        return ValidationResult(run_id=owner["run_id"], kind="auto", command=["python"], status="passed", duration_ms=1, stdout="", stderr="")

    monkeypatch.setattr(main.test_service, "_execute", execute)

    async def scenario():
        entered, released = asyncio.Event(), asyncio.Event()

        async def delayed_threadpool(function, *args, **kwargs):
            entered.set()
            await released.wait()
            return await original_threadpool(function, *args, **kwargs)

        monkeypatch.setattr(agent_module, "run_in_threadpool", delayed_threadpool)

        async def run():
            with main.agent_service.workspace_operation(owner["id"]):
                return await main.agent_service._run_for_model(main.task_service.get(owner["id"]), ["python", "-c", "print(1)"])

        pending = asyncio.create_task(run())
        try:
            await entered.wait()
            pending.cancel()
            await asyncio.sleep(0)
            pending.cancel()
            await asyncio.sleep(0)
            assert not pending.done()
            with pytest.raises(RepositoryError) as error:
                main.agent_service.assert_workspace_available(other["id"])
            assert error.value.code == "WORKSPACE_BUSY"
            released.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
        finally:
            released.set()
            with suppress(asyncio.CancelledError):
                await pending

    asyncio.run(scenario())
    assert executions == []
    assert not main.test_service.is_running(owner["run_id"] + main.test_service.COMMAND_SUFFIX)
    main.agent_service.assert_workspace_available(other["id"])


def test_stopping_manual_draft_during_model_command_discards_the_draft(workspace_api, tmp_path, monkeypatch):
    client = workspace_api
    owner, other, directory = tasks_in_one_folder(client, tmp_path)
    original = (directory / "app.py").read_bytes()
    entered, released = Event(), Event()
    calls = []

    def execute(run_id, kind, argv, command, workdir, cancel, limit, started, details):
        entered.set()
        assert released.wait(10), "command worker was not released"
        return ValidationResult(run_id=run_id, kind=kind, command=command, status="cancelled" if cancel.is_set() else "passed",
                                duration_ms=1, stdout="", stderr="")

    async def model(goal, context, failure):
        calls.append(goal)
        if len(calls) == 1:
            return PatchProposal(context_requests=[ContextRequest(action="run", command=["python", "-c", "print(1)"])])
        return PatchProposal(files=[PatchFile(path="app.py", content="def value():\n    return 2\n")])

    monkeypatch.setattr(main.test_service, "_execute", execute)
    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    main.task_service.record_validation_baseline(owner["id"], [], "already-captured")
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.post, f"/api/v1/runs/{owner['run_id']}/patches/draft", json={})
        try:
            assert entered.wait(5)
            post(client, f"/api/v1/runs/{owner['run_id']}/loop/stop")
            released.set()
            response = future.result(timeout=5)
        finally:
            released.set()
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "INVALID_TASK_STATE"
    assert len(calls) == 1
    assert (directory / "app.py").read_bytes() == original
    assert client.get(f"/api/v1/runs/{owner['run_id']}/checkpoints").json()["total"] == 0
    main.agent_service.assert_workspace_available(other["id"])
