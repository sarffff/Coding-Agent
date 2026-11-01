import pytest

from app import main
from app.model_provider import ContextRequest, PatchProposal
from app.models import PatchFile, TestRunResponse as RunResult
from conftest import git_output
from test_api_workflow import create_task, post
from test_model_draft import model_api


@pytest.mark.parametrize("status", ["passed", "failed", "timed_out"])
@pytest.mark.parametrize("mutation", ["create", "update", "delete", "stage", "branch"])
def test_model_command_workspace_change_stops_before_next_reply(model_api, monkeypatch, status, mutation):
    client, directory = model_api
    task = create_task(client, directory)
    unrelated = directory / "extra.py"
    unrelated.write_text("EXTRA = 1\n", encoding="utf-8")
    calls = []

    async def model(goal, context, failure):
        calls.append(goal)
        if len(calls) == 1:
            return PatchProposal(context_requests=[ContextRequest(action="run", command=["python", "probe.py"])])
        return PatchProposal(files=[PatchFile(path="app.py", content="def value():\n    return 2\n")])

    def command(run_id, repository_id, argv, *, reserved):
        if mutation == "create":
            (directory / "new.py").write_text("NEW = 1\n", encoding="utf-8")
        elif mutation == "update":
            unrelated.write_text("EXTRA = 99\n", encoding="utf-8")
        elif mutation == "delete":
            unrelated.unlink()
        elif mutation == "stage":
            git_output(directory, "add", "extra.py")
        else:
            git_output(directory, "checkout", "-b", "codex/probe")
        return RunResult(run_id=run_id, kind="auto", command=argv, status=status,
                               exit_code=0 if status == "passed" else 1, duration_ms=1, stdout="probe", stderr="")

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    monkeypatch.setattr(main.test_service, "run_command", command)
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={"context_paths": ["app.py"]})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "COMMAND_WORKSPACE_CHANGED"
    assert len(calls) == 1
    assert (directory / "app.py").read_text(encoding="utf-8").endswith("return 1\n")
    assert client.get(f"/api/v1/runs/{task['run_id']}/checkpoints").json()["total"] == 0
    assert main.task_service.get(task["id"]).agent_blocked_reason == "COMMAND_WORKSPACE_CHANGED"
    assert not main.test_service.is_running(task["run_id"] + main.test_service.COMMAND_SUFFIX)
    events = main.audit_service.list_for_run(task["run_id"])
    assert any(event.action == "context.request" and event.details.get("workspace_changed") for event in events)
    # Detection preserves the changed files and Git state for review.
    if mutation == "update":
        assert unrelated.read_text(encoding="utf-8") == "EXTRA = 99\n"
    elif mutation == "stage":
        assert git_output(directory, "diff", "--cached", "--name-only").strip() == "extra.py"


def test_readonly_command_and_generated_outputs_can_continue_drafting(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    calls = []

    async def model(goal, context, failure):
        calls.append(goal)
        if len(calls) == 1:
            return PatchProposal(context_requests=[ContextRequest(action="run", command=["python", "-c", "print('probe')"])])
        assert "probe" in goal
        return PatchProposal(files=[PatchFile(path="app.py", content="def value():\n    return 2\n")])

    original = main.test_service.run_command

    def command(*args, **kwargs):
        (directory / ".coverage").write_text("generated", encoding="utf-8")
        return original(*args, **kwargs)

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    monkeypatch.setattr(main.test_service, "run_command", command)
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
    assert "+    return 2" in draft["diff"] and len(calls) == 2
    assert main.task_service.get(task["id"]).agent_blocked_reason is None
