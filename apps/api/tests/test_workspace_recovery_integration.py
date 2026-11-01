from pathlib import Path

import pytest

from app import main
from app.model_provider import ContextRequest, PatchProposal
from app.models import TestRunResponse as RunResult
from app.repository_service import RepositoryError
from conftest import git_output
from test_api_workflow import approve, post


def task_for(client, directory, *, isolated=True, start=True):
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"goal": "Fix value behavior", "repository_id": repository["id"], "isolated_workspace": isolated})
    if start:
        approve(client, task, "plan")
        post(client, f"/api/v1/tasks/{task['id']}/iterations")
    return task


@pytest.mark.parametrize("has_git", [False, True])
def test_recovered_command_draft_survives_two_restarts_and_applies_with_rollback(api_stack, demo_repository, tmp_path, monkeypatch, has_git):
    source = demo_repository if has_git else tmp_path / "ordinary"
    source.mkdir(exist_ok=True)
    (source / "app.py").write_bytes(b"def value():\r\n    return 1\r\n")
    (source / "notes.txt").write_bytes(b"original notes\r\n")
    client = api_stack.restart()
    try:
        task = task_for(client, source)
        directory = Path(task["workspace_path"])
        original = {name: (source / name).read_bytes() for name in ("app.py", "notes.txt")}
        original_index = (source / ".git" / "index").read_bytes() if has_git else None
        services = api_stack.services
        services.workspace_service.snapshot(task["id"])
        (directory / "app.py").write_bytes(b"def value():\n    return 2\n")
        (directory / "notes.txt").unlink()
        (directory / "new.py").write_bytes(b"NEW = True\n")

        client = api_stack.restart()
        loaded = client.get(f"/api/v1/tasks/{task['id']}").json()
        assert loaded["requires_recovery"] is True
        assert loaded["source_repository_id"] == task["source_repository_id"]
        assert loaded["repository_id"] == task["repository_id"]
        assert loaded["workspace_path"] == task["workspace_path"]
        assert loaded["workspace_kind"] == ("git" if has_git else "local")
        recovered = post(client, f"/api/v1/tasks/{task['id']}/recover")
        assert recovered["task"]["requires_recovery"] is False
        assert {name: (directory / name).read_bytes() for name in original} == original
        assert not (directory / "new.py").exists()
        assert api_stack.services.workspace_service.pending_snapshot(task["id"]) is None
        saved = api_stack.services.workspace_service.command_draft(task["id"])
        assert {item.path for item in saved} == {"app.py", "notes.txt", "new.py"}

        client = api_stack.restart()
        assert api_stack.services.workspace_service.command_draft(task["id"]) == saved
        blocked = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={})
        assert blocked.status_code == 400, blocked.text
        assert blocked.json()["code"] == "RECOVERY_CONFIRMATION_REQUIRED"
        post(client, f"/api/v1/tasks/{task['id']}/recover")

        async def no_model(*args, **kwargs):
            raise AssertionError("A saved command draft must not call the model")

        monkeypatch.setattr(api_stack.services.agent_service.models, "propose_patch", no_model)
        draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
        assert set(draft["files"]) == {"app.py", "notes.txt", "new.py"}
        assert draft["repository_id"] == task["repository_id"]
        assert "+    return 2" in draft["diff"]
        applied = post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {"patch_id": draft["patch_id"], "confirm": True})
        assert applied["checkpoint"]["repository_id"] == task["repository_id"]
        assert applied["checkpoint"]["run_id"] == task["run_id"]
        assert (directory / "app.py").read_bytes() == b"def value():\n    return 2\n"
        assert not (directory / "notes.txt").exists()
        assert (directory / "new.py").read_bytes() == b"NEW = True\n"
        assert api_stack.services.workspace_service.command_draft(task["id"]) == []
        rolled_back = post(client, f"/api/v1/runs/{task['run_id']}/rollback", {"checkpoint_id": applied["checkpoint"]["id"]})
        assert set(rolled_back["restored_files"]) == {"app.py", "notes.txt", "new.py"}
        assert {name: (directory / name).read_bytes() for name in original} == original
        assert not (directory / "new.py").exists()
        assert {name: (source / name).read_bytes() for name in original} == original
        if has_git:
            assert (source / ".git" / "index").read_bytes() == original_index
            assert git_output(source, "branch", "--show-current").strip() == "main"
    finally:
        client.close()
        api_stack.services.store.close()


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_recovery_rejects_damaged_command_backup_and_keeps_recovery_required(api_stack, demo_repository, damage):
    client = api_stack.restart()
    try:
        task = task_for(client, demo_repository)
        directory = Path(task["workspace_path"])
        original_source = (demo_repository / "app.py").read_bytes()
        api_stack.services.workspace_service.snapshot(task["id"])
        (directory / "app.py").write_bytes(b"def value():\n    return 2\n")
        backup = next((directory.parent / "command-snapshot" / "files").iterdir())
        if damage == "missing":
            backup.unlink()
        else:
            backup.write_bytes(b"corrupt backup")
        client = api_stack.restart()
        response = client.post(f"/api/v1/tasks/{task['id']}/recover", json={})
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "COMMAND_SNAPSHOT_UNAVAILABLE"
        assert client.get(f"/api/v1/tasks/{task['id']}").json()["requires_recovery"] is True
        assert (directory / "app.py").read_bytes() == b"def value():\n    return 2\n"
        assert api_stack.services.workspace_service.command_draft(task["id"]) == []
        assert client.post(f"/api/v1/runs/{task['run_id']}/patches/preview", json={"files": [{"path": "app.py", "content": "VALUE = 3\n"}]}).json()["code"] == "RECOVERY_CONFIRMATION_REQUIRED"
        assert (demo_repository / "app.py").read_bytes() == original_source
    finally:
        client.close()
        api_stack.services.store.close()


def test_same_source_isolated_tasks_have_independent_reservations_and_legacy_tasks_stay_exclusive(api_client, demo_repository):
    first, second = task_for(api_client, demo_repository), task_for(api_client, demo_repository)
    old_first = task_for(api_client, demo_repository, isolated=False)
    old_second = task_for(api_client, demo_repository, isolated=False)
    assert first["source_repository_id"] == second["source_repository_id"] == old_first["repository_id"]
    with main.agent_service.workspace_operation(first["id"]):
        with main.agent_service.workspace_operation(second["id"]):
            assert main.repository_service.get(first["repository_id"]).path != main.repository_service.get(second["repository_id"]).path
        with main.agent_service.workspace_operation(old_first["id"]):
            pass
    with main.agent_service.workspace_operation(old_first["id"]):
        with pytest.raises(RepositoryError) as error:
            with main.agent_service.workspace_operation(old_second["id"]):
                pass
        assert error.value.code == "WORKSPACE_BUSY"

    first_directory = main.repository_service.get(first["repository_id"]).path
    owner = main.agent_service.workspaces.acquire(first_directory, first["id"])
    try:
        allowed = api_client.post(f"/api/v1/runs/{second['run_id']}/patches/preview", json={"files": [{"path": "app.py", "content": "VALUE = 2\n"}]})
        assert allowed.status_code == 200, allowed.text
    finally:
        main.agent_service.workspaces.release(owner)
    owner = main.agent_service.workspaces.acquire(demo_repository, old_first["id"])
    try:
        blocked = api_client.post(f"/api/v1/runs/{old_second['run_id']}/patches/preview", json={"files": [{"path": "app.py", "content": "VALUE = 2\n"}]})
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["code"] == "WORKSPACE_BUSY"
    finally:
        main.agent_service.workspaces.release(owner)


def test_recovery_waits_for_existing_workspace_reservation(api_stack, demo_repository):
    client = api_stack.restart()
    try:
        task = task_for(client, demo_repository)
        directory = Path(task["workspace_path"])
        original = (directory / "app.py").read_bytes()
        api_stack.services.workspace_service.snapshot(task["id"])
        (directory / "app.py").write_bytes(b"VALUE = 2\n")
        client = api_stack.restart()
        agent = api_stack.services.agent_service
        owner = agent.workspaces.acquire(directory, task["id"])
        try:
            response = client.post(f"/api/v1/tasks/{task['id']}/recover", json={})
            assert response.status_code == 409, response.text
            assert response.json()["code"] == "WORKSPACE_BUSY"
            assert (directory / "app.py").read_bytes() == b"VALUE = 2\n"
            assert client.get(f"/api/v1/tasks/{task['id']}").json()["requires_recovery"] is True
        finally:
            agent.workspaces.release(owner)
        post(client, f"/api/v1/tasks/{task['id']}/recover")
        assert (directory / "app.py").read_bytes() == original
    finally:
        client.close()
        api_stack.services.store.close()


@pytest.mark.parametrize("has_git", [False, True])
def test_task_delete_and_source_cascade_only_remove_owned_workspaces(api_client, demo_repository, tmp_path, has_git):
    source = demo_repository if has_git else tmp_path / "ordinary"
    source.mkdir(exist_ok=True)
    (source / "app.py").write_bytes(b"VALUE = 1\r\n")
    first, second = task_for(api_client, source, start=False), task_for(api_client, source, start=False)
    first_directory, second_directory = Path(first["workspace_path"]), Path(second["workspace_path"])
    service = main.agent_service.isolated_workspaces
    before = service.snapshot(first["id"])
    (first_directory / "app.py").write_bytes(b"VALUE = 2\n")
    service.save_command_draft(first["id"], service.diff(before))
    service.restore(before)
    service.snapshot(second["id"])
    (source / "app.py").write_bytes(b"VALUE = 'user source edit'\r\n")
    if has_git:
        git_output(source, "add", "app.py")
    original_index = (source / ".git" / "index").read_bytes() if has_git else None
    assert api_client.delete(f"/api/v1/tasks/{first['id']}").status_code == 200
    assert not first_directory.exists() and second_directory.exists()
    assert service.command_draft(first["id"]) == []
    assert api_client.get(f"/api/v1/repositories/{first['repository_id']}").status_code == 404
    blocked = api_client.delete(f"/api/v1/repositories/{second['source_repository_id']}")
    assert blocked.status_code == 409 and blocked.json()["code"] == "REPOSITORY_IN_USE"
    deleted = api_client.delete(f"/api/v1/repositories/{second['source_repository_id']}?cascade=true")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["tasks"] == 1
    assert not second_directory.exists()
    assert api_client.get(f"/api/v1/tasks/{second['id']}").status_code == 404
    assert api_client.get("/api/v1/repositories").json()["total"] == 0
    assert (source / "app.py").read_bytes() == b"VALUE = 'user source edit'\r\n"
    if has_git:
        assert (source / ".git" / "index").read_bytes() == original_index
        assert git_output(source, "branch", "--show-current").strip() == "main"


def test_model_command_detects_source_drift_without_overwriting_source(api_client, demo_repository, monkeypatch):
    task = task_for(api_client, demo_repository)
    directory = Path(task["workspace_path"])
    original = (directory / "app.py").read_bytes()
    calls = []

    async def baseline(task_id):
        pass

    async def model(goal, context, failure):
        calls.append(goal)
        return PatchProposal(context_requests=[ContextRequest(action="run", command=["python", "probe.py"])])

    def command(run_id, repository_id, argv, *, reserved):
        assert repository_id == task["repository_id"]
        (directory / "app.py").write_bytes(b"VALUE = 2\n")
        (demo_repository / "notes.txt").write_bytes(b"user changed source during command\r\n")
        return RunResult(run_id=run_id, kind="auto", command=argv, status="passed", exit_code=0, duration_ms=1, stdout="probe", stderr="")

    monkeypatch.setattr(main.agent_service, "_ensure_validation_baseline", baseline)
    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    monkeypatch.setattr(main.test_service, "run_command", command)
    response = api_client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={"context_paths": ["app.py"]})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "COMMAND_WORKSPACE_CHANGED"
    assert len(calls) == 1
    assert (directory / "app.py").read_bytes() == original
    assert (demo_repository / "app.py").read_bytes() == original
    assert (demo_repository / "notes.txt").read_bytes() == b"user changed source during command\r\n"
    assert main.task_service.get(task["id"]).agent_blocked_reason == "COMMAND_WORKSPACE_CHANGED"
    assert main.agent_service.isolated_workspaces.pending_snapshot(task["id"]) is None
    assert api_client.get(f"/api/v1/runs/{task['run_id']}/checkpoints").json()["total"] == 0
    assert not main.test_service.is_running(task["run_id"] + main.test_service.COMMAND_SUFFIX)


@pytest.mark.parametrize("started", [False, True])
def test_restart_reconciles_unbound_initial_workspace_and_blocks_incompatible_active_binding(api_stack, demo_repository, started):
    client = api_stack.restart()
    try:
        task = task_for(client, demo_repository, isolated=False, start=started)
        services = api_stack.services
        stored = services.task_service._record(task["id"])
        stored.summary.source_repository_id = task["repository_id"]
        stored.summary.base_branch = "main"
        services.task_service._persist(stored)
        # Simulate interruption after WorkspaceService persisted its record and before main bound the task.
        workspace = services.workspace_service.create(task["id"], task["repository_id"])
        client = api_stack.restart()
        loaded = client.get(f"/api/v1/tasks/{task['id']}").json()
        assert loaded["requires_recovery"] is True
        if started:
            response = client.post(f"/api/v1/tasks/{task['id']}/recover", json={})
            assert response.status_code == 409, response.text
            assert response.json()["code"] == "WORKSPACE_UNAVAILABLE"
            blocked = client.post(f"/api/v1/runs/{task['run_id']}/patches/preview", json={"files": [{"path": "app.py", "content": "VALUE = 2\n"}]})
            assert blocked.status_code == 409 and blocked.json()["code"] == "WORKSPACE_UNAVAILABLE"
            assert client.get(f"/api/v1/tasks/{task['id']}").json()["requires_recovery"] is True
        else:
            assert loaded["repository_id"] == workspace.repository_id
            assert loaded["source_repository_id"] == task["repository_id"]
            assert loaded["workspace_path"] == str(workspace.path)
            assert loaded["workspace_kind"] == "git"
            assert loaded["task_branch"] == workspace.branch
            assert loaded["base_branch"] == "main"
            recovered = post(client, f"/api/v1/tasks/{task['id']}/recover")
            assert recovered["task"]["requires_recovery"] is False
        assert (demo_repository / "app.py").read_bytes() == (workspace.path / "app.py").read_bytes()
        assert git_output(demo_repository, "branch", "--show-current").strip() == "main"
    finally:
        client.close()
        api_stack.services.store.close()


def test_restart_before_workspace_creation_keeps_source_visible_and_task_deletable(api_stack, demo_repository):
    client = api_stack.restart()
    try:
        task = task_for(client, demo_repository, isolated=False, start=False)
        stored = api_stack.services.task_service._record(task["id"])
        stored.summary.source_repository_id = task["repository_id"]
        stored.summary.base_branch = "main"
        api_stack.services.task_service._persist(stored)
        original = (demo_repository / "app.py").read_bytes()
        client = api_stack.restart()
        sources = client.get("/api/v1/repositories").json()
        assert [item["id"] for item in sources["items"]] == [task["repository_id"]]
        assert client.get("/api/v1/workspace/summary").json()["repositories"] == 1
        response = client.post(f"/api/v1/tasks/{task['id']}/recover", json={})
        assert response.status_code == 409 and response.json()["code"] == "WORKSPACE_UNAVAILABLE"
        assert client.get(f"/api/v1/tasks/{task['id']}").json()["requires_recovery"] is True
        deleted = client.delete(f"/api/v1/tasks/{task['id']}")
        assert deleted.status_code == 200, deleted.text
        assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 404
        assert (demo_repository / "app.py").read_bytes() == original
        assert git_output(demo_repository, "branch", "--show-current").strip() == "main"
    finally:
        client.close()
        api_stack.services.store.close()


def test_missing_managed_checkout_does_not_break_source_repository_list(api_client, demo_repository):
    task = task_for(api_client, demo_repository, start=False)
    git_output(demo_repository, "worktree", "remove", "--force", task["workspace_path"])
    response = api_client.get("/api/v1/repositories")
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [task["source_repository_id"]]
    assert api_client.get("/api/v1/workspace/summary").json()["repositories"] == 1
    recovery = api_client.post(f"/api/v1/tasks/{task['id']}/recover", json={})
    assert recovery.status_code == 409, recovery.text
    assert recovery.json()["code"] == "WORKSPACE_UNAVAILABLE"
    assert demo_repository.exists() and not Path(task["workspace_path"]).exists()
    agent = api_client.get(f"/api/v1/tasks/{task['id']}/agent")
    assert agent.status_code == 200, agent.text
    assert agent.json()["can_advance"] is False
    deleted = api_client.delete(f"/api/v1/tasks/{task['id']}")
    assert deleted.status_code == 200, deleted.text
    assert demo_repository.exists()
