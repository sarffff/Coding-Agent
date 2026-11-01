from pathlib import Path

import pytest

from app import main
from app.model_provider import ContextRequest, PatchProposal
from app.models import TestRunResponse as RunResult
from conftest import git_output
from test_api_workflow import post
from test_agent_workflow import approve_plan, start_agent_iteration, agent_state


def isolated_task(client, directory):
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    return post(client, "/api/v1/tasks", {"goal": "Verify value behavior", "repository_id": repository["id"], "isolated_workspace": True})


def test_isolated_tasks_preserve_dirty_source_and_group_under_source(api_client, demo_repository):
    (demo_repository / "app.py").write_bytes(b"def value():\r\n    return 9\r\n")
    git_output(demo_repository, "add", "app.py")
    (demo_repository / "notes.txt").write_bytes(b"unstaged notes\r\n")
    (demo_repository / "new.py").write_bytes(b"new = 7\r\n")
    before = (git_output(demo_repository, "status", "--porcelain"), (demo_repository / ".git" / "index").read_bytes())
    first, second = isolated_task(api_client, demo_repository), isolated_task(api_client, demo_repository)
    assert first["repository_id"] != second["repository_id"] != first["source_repository_id"]
    assert first["source_repository_id"] == second["source_repository_id"]
    assert first["task_branch"].startswith("codex/")
    assert first["base_branch"] == "main"
    for task in (first, second):
        workspace = Path(task["workspace_path"])
        for name in ("app.py", "notes.txt", "new.py"):
            assert (workspace / name).read_bytes() == (demo_repository / name).read_bytes()
        assert api_client.get(f"/api/v1/repositories/{task['repository_id']}").status_code == 200
    assert [item["id"] for item in api_client.get("/api/v1/repositories").json()["items"]] == [first["source_repository_id"]]
    assert api_client.get("/api/v1/workspace/summary").json()["repositories"] == 1
    assert before == (git_output(demo_repository, "status", "--porcelain"), (demo_repository / ".git" / "index").read_bytes())
    assert api_client.delete(f"/api/v1/repositories/{first['source_repository_id']}").status_code == 409
    assert api_client.delete(f"/api/v1/repositories/{first['source_repository_id']}?cascade=true").status_code == 200
    assert demo_repository.exists()
    assert not Path(first["workspace_path"]).exists() and not Path(second["workspace_path"]).exists()
    assert before == (git_output(demo_repository, "status", "--porcelain"), (demo_repository / ".git" / "index").read_bytes())


@pytest.mark.parametrize("mutation", ["create", "update", "delete", "binary", "stage"])
def test_isolated_command_changes_use_review_apply_and_rollback(api_client, demo_repository, monkeypatch, mutation):
    task = isolated_task(api_client, demo_repository)
    workspace = Path(task["workspace_path"])
    original = (workspace / "app.py").read_bytes()
    approve_plan(api_client, task)
    start_agent_iteration(api_client, task)
    calls = []

    async def model(goal, context, failure):
        calls.append(goal)
        return PatchProposal(context_requests=[ContextRequest(action="run", command=["python", "probe.py"])])

    def run_command(run_id, repository_id, argv, *, reserved):
        assert repository_id == task["repository_id"]
        if mutation == "create":
            (workspace / "new.py").write_bytes(b"new = 7\r\n")
        elif mutation == "delete":
            (workspace / "app.py").unlink()
        elif mutation == "binary":
            (workspace / "app.py").write_bytes(b"\x00binary")
        elif mutation == "stage":
            git_output(workspace, "add", "app.py")
            git_output(workspace, "update-index", "--assume-unchanged", "app.py")
        else:
            (workspace / "app.py").write_bytes(b"def value():\r\n    return 2\r\n")
        return RunResult(run_id=run_id, kind="auto", command=argv, status="passed", exit_code=0, duration_ms=1, stdout="probe", stderr="")

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    monkeypatch.setattr(main.test_service, "run_command", run_command)
    response = api_client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={"context_paths": ["app.py"]})
    assert len(calls) == 1
    assert (workspace / "app.py").read_bytes() == original
    assert (demo_repository / "app.py").read_bytes() == original
    assert main.agent_service.isolated_workspaces.pending_snapshot(task["id"]) is None
    if mutation in {"binary", "stage"}:
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "COMMAND_WORKSPACE_CHANGED"
        return
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["files"] == (["new.py"] if mutation == "create" else ["app.py"])
    assert api_client.get(f"/api/v1/runs/{task['run_id']}/checkpoints").json()["total"] == 0
    assert main.agent_service.isolated_workspaces.command_draft(task["id"])
    applied = post(api_client, f"/api/v1/runs/{task['run_id']}/patches/apply", {"patch_id": draft["patch_id"], "confirm": True})
    assert main.agent_service.isolated_workspaces.command_draft(task["id"]) == []
    if mutation == "update":
        assert (workspace / "app.py").read_bytes() == b"def value():\r\n    return 2\r\n"
    elif mutation == "delete":
        assert not (workspace / "app.py").exists()
    else:
        assert (workspace / "new.py").exists()
    post(api_client, f"/api/v1/runs/{task['run_id']}/rollback", {"checkpoint_id": applied["checkpoint"]["id"]})
    assert (workspace / "app.py").read_bytes() == original
    assert not (workspace / "new.py").exists()


@pytest.mark.parametrize("git", [False, True])
def test_no_change_goal_requires_real_tests_and_explicit_local_completion(api_client, demo_repository, tmp_path, monkeypatch, git):
    directory = demo_repository if git else tmp_path / "ordinary"
    directory.mkdir(exist_ok=True)
    (directory / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8", newline="")
    (directory / "test_app.py").write_text("from app import value\n\ndef test_value():\n    assert value() == 2\n", encoding="utf-8", newline="")
    task = isolated_task(api_client, directory)
    approve_plan(api_client, task)
    start_agent_iteration(api_client, task)

    async def model(goal, context, failure):
        assert "return 2" in dict(context)["app.py"]
        return PatchProposal(no_changes=True, rationale="value already returns 2; verify the existing test")

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    draft = post(api_client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": ["app.py"]})
    assert draft["no_changes"] is True and not draft["patch_id"] and not draft["files"]
    assert api_client.post(f"/api/v1/tasks/{task['id']}/complete-local").status_code == 409
    result = post(api_client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest", "timeout_seconds": 15})
    assert result["status"] == "passed", result
    state = agent_state(api_client, task["id"])
    expected_action = "review_local_completion" if git else "review_completion"
    assert state["action"] == expected_action and state["can_confirm_completion"]
    assert not state["can_complete_local"]
    post(api_client, f"/api/v1/tasks/{task['id']}/completion-confirmation", {
        "iteration_id": state["iteration"]["id"], "scope_hash": state["completion_scope_hash"],
        "validation_hash": state["completion_validation_hash"],
        "evidence": [{"criterion_id": "task-goal", "evidence": "Existing value implementation and unchanged test_value were verified."}],
    })
    assert agent_state(api_client, task["id"])["can_complete_local"] is True
    completed = post(api_client, f"/api/v1/tasks/{task['id']}/complete-local")
    assert completed["status"] == "completed"
    assert Path(completed["workspace_path"]).exists()
    assert agent_state(api_client, task["id"])["action"] == "completed"


def test_plan_edit_invalidates_approved_scope_before_start(api_client, demo_repository):
    task = isolated_task(api_client, demo_repository)
    approve_plan(api_client, task)
    plan = task["plan"]
    plan["steps"] = [dict(plan["steps"][1], id="behavior", title="Verify value returns 2")]
    response = api_client.put(f"/api/v1/tasks/{task['id']}/plan", json=plan)
    assert response.status_code == 200, response.text
    task = response.json()
    assert task["completion_criteria"][0]["step_id"] == "behavior"
    assert agent_state(api_client, task["id"])["action"] == "request_plan_approval"
    blocked = api_client.post(f"/api/v1/tasks/{task['id']}/iterations", json={})
    assert blocked.status_code == 400 and blocked.json()["code"] == "APPROVAL_REQUIRED"
