import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import pytest
from fastapi.testclient import TestClient

from conftest import mount_services
from test_agent_loop import _wait_for_loop
from test_api_workflow import create_task, post

from app import main
from app.config import Settings
from app.model_provider import PatchProposal
from app.models import PatchFile, TestRunResponse as ValidationResult


@pytest.fixture
def workspace_api(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        workspace_root=tmp_path,
        state_dir=tmp_path / "state",
        llm_provider="fake",
        command_timeout_seconds=5,
    )
    services = mount_services(monkeypatch, settings)
    with TestClient(main.app) as client:
        yield client
    services.store.close()


def folder_task(client, directory, goal="Fix value"):
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / "app.py").exists():
        (directory / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": goal})
    approval = post(client, f"/api/v1/tasks/{task['id']}/approvals", {"type": "plan", "summary": "Review plan"})
    post(client, f"/api/v1/approvals/{approval['id']}/decision", {"decision": "approve"})
    post(client, f"/api/v1/tasks/{task['id']}/iterations")
    return task


@contextmanager
def held_loop(client, task, monkeypatch, ignore_cancellation=False):
    entered, released, cancelled = Event(), Event(), Event()

    async def model(goal, context_files, failure):
        if not entered.is_set():
            entered.set()
            try:
                while not released.is_set():
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                cancelled.set()
                if not ignore_cancellation:
                    raise
        return PatchProposal(files=[PatchFile(path="app.py", content="def value():\n    return 2\n")])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    post(client, f"/api/v1/runs/{task['run_id']}/loop", {})
    try:
        assert entered.wait(5), "loop did not reach the model"
        yield cancelled
    finally:
        client.post(f"/api/v1/runs/{task['run_id']}/loop/stop", json={})
        released.set()
        _wait_for_loop(client, task["id"], timeout=5)


def assert_busy(response):
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "WORKSPACE_BUSY", response.text


@pytest.mark.parametrize("relation", ["same", "child", "parent"])
def test_overlapping_folders_cannot_start_another_loop(workspace_api, tmp_path, monkeypatch, relation):
    client = workspace_api
    root, nested = tmp_path / "project", tmp_path / "project" / "nested"
    owner_path, other_path = (nested, root) if relation == "parent" else (root, nested if relation == "child" else root)
    owner = folder_task(client, owner_path)
    other = folder_task(client, other_path)
    with held_loop(client, owner, monkeypatch):
        assert_busy(client.post(f"/api/v1/runs/{other['run_id']}/loop", json={}))
    # A stopped task must not keep its directory reserved.
    response = client.post(f"/api/v1/runs/{other['run_id']}/patches/draft", json={})
    assert response.status_code == 200, response.text


def test_independent_folder_loop_can_apply_and_validate(workspace_api, tmp_path, monkeypatch):
    client = workspace_api
    owner = folder_task(client, tmp_path / "owner")
    other_path = tmp_path / "independent"
    other = folder_task(client, other_path)
    (other_path / "test_app.py").write_text("from app import value\n\ndef test_value():\n    assert value() == 2\n", encoding="utf-8")
    with held_loop(client, owner, monkeypatch):
        started = post(client, f"/api/v1/runs/{other['run_id']}/loop", {})
        assert started["loop_running"] is True
        final = _wait_for_loop(client, other["id"], timeout=15)
        assert final["last_test"]["status"] == "passed", final
        assert (other_path / "app.py").read_text(encoding="utf-8") == "def value():\n    return 2\n"


def test_manual_validation_reserves_the_folder_against_another_loop(workspace_api, tmp_path, monkeypatch):
    client = workspace_api
    directory = tmp_path / "project"
    owner = folder_task(client, directory)
    other = folder_task(client, directory)
    entered, released = Event(), Event()

    def delayed_validation(run_id, repository_id, kind, timeout_seconds, target=None):
        entered.set()
        try:
            assert released.wait(5), "validation was not released"
            return ValidationResult(run_id=run_id, kind=kind, command=["fake-validation"], status="passed", duration_ms=1, stdout="", stderr="")
        finally:
            main.test_service.release(run_id)

    monkeypatch.setattr(main.test_service, "run", delayed_validation)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.post, f"/api/v1/runs/{owner['run_id']}/tests", json={"kind": "static"})
        try:
            assert entered.wait(5)
            assert_busy(client.post(f"/api/v1/runs/{other['run_id']}/loop", json={}))
        finally:
            released.set()
        assert future.result(timeout=5).status_code == 200


@pytest.mark.parametrize("operation", ["draft", "tests", "apply", "rollback"])
def test_foreign_loop_blocks_manual_workspace_operations(workspace_api, tmp_path, monkeypatch, operation):
    client = workspace_api
    directory = tmp_path / "project"
    owner = folder_task(client, directory)
    other = folder_task(client, directory)
    preview = post(client, f"/api/v1/runs/{other['run_id']}/patches/preview", {
        "files": [{"path": "app.py", "content": "def value():\n    return 3\n"}],
    })
    if operation == "rollback":
        applied = post(client, f"/api/v1/runs/{other['run_id']}/patches/apply", {"patch_id": preview["patch_id"], "confirm": True})
        endpoint, payload = "rollback", {"checkpoint_id": applied["checkpoint"]["id"]}
    elif operation == "apply":
        endpoint, payload = "patches/apply", {"patch_id": preview["patch_id"], "confirm": True}
    elif operation == "tests":
        endpoint, payload = "tests", {"kind": "static"}
    else:
        endpoint, payload = "patches/draft", {}
    original = (directory / "app.py").read_bytes()
    with held_loop(client, owner, monkeypatch):
        assert_busy(client.post(f"/api/v1/runs/{other['run_id']}/{endpoint}", json=payload))
        assert (directory / "app.py").read_bytes() == original


@pytest.mark.parametrize("endpoint", ["patches/draft", "tests"])
def test_running_loop_blocks_its_own_manual_operations(workspace_api, tmp_path, monkeypatch, endpoint):
    client = workspace_api
    task = folder_task(client, tmp_path / "project")
    payload = {"kind": "static"} if endpoint == "tests" else {}
    with held_loop(client, task, monkeypatch):
        assert_busy(client.post(f"/api/v1/runs/{task['run_id']}/{endpoint}", json=payload))


@pytest.mark.parametrize("endpoint", ["branch", "commits"])
def test_foreign_loop_blocks_git_mutations(workspace_api, demo_repository, monkeypatch, endpoint):
    client = workspace_api
    owner = create_task(client, demo_repository)
    other = post(client, "/api/v1/tasks", {"repository_id": owner["repository_id"], "goal": "Another local change"})
    payload = {"name": "codex/another-task"} if endpoint == "branch" else {"message": "Another change", "scope_hash": "0" * 64}
    with held_loop(client, owner, monkeypatch):
        assert_busy(client.post(f"/api/v1/tasks/{other['id']}/{endpoint}", json=payload))


@pytest.mark.parametrize("action", ["pause", "cancel"])
@pytest.mark.parametrize("ignore_cancellation", [False, True])
def test_task_action_cancels_model_without_late_writes_and_releases_workspace(workspace_api, tmp_path, monkeypatch, action, ignore_cancellation):
    client = workspace_api
    directory = tmp_path / "project"
    owner = folder_task(client, directory)
    other = folder_task(client, directory)
    original = (directory / "app.py").read_bytes()
    with held_loop(client, owner, monkeypatch, ignore_cancellation) as cancelled:
        post(client, f"/api/v1/tasks/{owner['id']}/{action}")
        assert cancelled.wait(1), f"{action} left the model request running"
        final = _wait_for_loop(client, owner["id"], timeout=5)
        assert final["task"]["status"] == ("paused" if action == "pause" else "cancelled")
        assert final["task"]["budget"]["consecutive_failures"] == 0
        assert (directory / "app.py").read_bytes() == original
        assert client.get(f"/api/v1/runs/{owner['run_id']}/checkpoints").json()["total"] == 0
    assert client.post(f"/api/v1/runs/{other['run_id']}/patches/draft", json={}).status_code == 200


@pytest.mark.parametrize("action", ["pause", "cancel"])
@pytest.mark.parametrize("ignore_cancellation", [False, True])
def test_task_action_cancels_manual_draft_and_releases_workspace(workspace_api, tmp_path, monkeypatch, action, ignore_cancellation):
    client = workspace_api
    directory = tmp_path / "project"
    owner = folder_task(client, directory)
    other = folder_task(client, directory)
    entered, released, cancelled = Event(), Event(), Event()
    original = (directory / "app.py").read_bytes()

    async def model(goal, context_files, failure):
        if not entered.is_set():
            entered.set()
            try:
                while not released.is_set():
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                cancelled.set()
                if not ignore_cancellation:
                    raise
        return PatchProposal(files=[PatchFile(path="app.py", content="def value():\n    return 2\n")])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.post, f"/api/v1/runs/{owner['run_id']}/patches/draft", json={})
        try:
            assert entered.wait(5)
            post(client, f"/api/v1/tasks/{owner['id']}/{action}")
            assert cancelled.wait(1), f"{action} left the manual draft request running"
            response = future.result(timeout=5)
            assert response.status_code == 400, response.text
            assert response.json()["code"] == "INVALID_TASK_STATE"
        finally:
            released.set()
    assert (directory / "app.py").read_bytes() == original
    assert main.task_service.get(owner["id"]).budget.consecutive_failures == 0
    assert client.post(f"/api/v1/runs/{other['run_id']}/patches/draft", json={}).status_code == 200
