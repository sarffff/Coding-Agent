import asyncio

import pytest

from app import main
from app.model_provider import FakeModelProvider
from app.models import PatchFile, PullRequestCreateRequest, TestRunResponse as ValidationResult
from app.repository_service import RepositoryError
from app.remote_provider import FakeRemoteProvider
from conftest import git_output
from test_agent_loop import model_api, _wait_for_loop
from test_agent_workflow import approve_plan, create_branch, start_agent_iteration, apply_value, agent_state
from test_api_workflow import approve, post


CRITERIA = [{"id": "value", "text": "value() returns 2 without changing the test"}]


def passing_completion_task(client, directory, monkeypatch):
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Fix value", "completion_criteria": CRITERIA})
    approve_plan(client, task)
    create_branch(client, task)
    start_agent_iteration(client, task)
    apply_value(client, task, 2)

    def run(run_id, repository_id, kind, timeout_seconds, target=None, **kwargs):
        main.test_service.release(run_id)
        return ValidationResult(run_id=run_id, kind="pytest", command=["fixture"], status="passed", duration_ms=1, stdout="", stderr="")

    monkeypatch.setattr(main.test_service, "run", run)
    post(client, f"/api/v1/runs/{task['run_id']}/tests")
    return task


def confirm(client, task):
    state = agent_state(client, task["id"])
    return post(client, f"/api/v1/tasks/{task['id']}/completion-confirmation", {
        "iteration_id": state["iteration"]["id"], "scope_hash": state["completion_scope_hash"],
        "validation_hash": state["completion_validation_hash"],
        "evidence": [{"criterion_id": "value", "evidence": "Reviewed app.py; unchanged test_value passed."}],
    })


def test_confirmation_survives_commit_but_external_changes_block_direct_publish(api_client, demo_repository, monkeypatch):
    task = passing_completion_task(api_client, demo_repository, monkeypatch)
    state = agent_state(api_client, task["id"])
    assert state["action"] == "review_completion"
    assert state["can_confirm_completion"] is True
    confirm(api_client, task)
    before = agent_state(api_client, task["id"])["completion_validation_hash"]
    preview = post(api_client, f"/api/v1/tasks/{task['id']}/commits/preview", {"message": "Fix value"})
    approve(api_client, task, "write", preview["scope_hash"])
    post(api_client, f"/api/v1/tasks/{task['id']}/commits", {"message": "Fix value", "scope_hash": preview["scope_hash"]})
    state = agent_state(api_client, task["id"])
    assert state["completion_status"] == "confirmed"
    assert state["completion_validation_hash"] == before
    assert state["action"] == "review_push"

    (demo_repository / "notes.txt").write_text("external change\n", encoding="utf-8")
    state = agent_state(api_client, task["id"])
    assert state["completion_status"] == "stale"
    assert state["action"] == "run_tests"
    for operation in (main.remote_service.push_branch(task["id"]),
                      main.remote_service.create_pull_request(task["id"], PullRequestCreateRequest(title="Fix value", body="Reviewed", target_branch="main"))):
        with pytest.raises(RepositoryError) as caught:
            asyncio.run(operation)
        assert caught.value.code == "COMPLETION_REQUIRED"
    assert not main.remote_service.list_remote_calls(task["id"])


@pytest.mark.parametrize("operation", ["commit", "push", "pr"])
def test_direct_automatic_publish_requires_completion_evidence(api_client, demo_repository, monkeypatch, operation):
    task = passing_completion_task(api_client, demo_repository, monkeypatch)
    with pytest.raises(RepositoryError) as caught:
        current = main.task_service.get(task["id"])
        if operation == "commit":
            main.agent_service._auto_commit(current)
        elif operation == "push":
            asyncio.run(main.agent_service._perform_push(current))
        else:
            asyncio.run(main.agent_service._perform_pr(current))
    assert caught.value.code == "COMPLETION_REQUIRED"
    assert git_output(demo_repository, "log", "--oneline").count("\n") == 1


def test_autonomous_loop_stops_for_completion_without_committing(model_api):
    client, directory = model_api
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "mode": "autonomous", "goal": "Fix value", "completion_criteria": CRITERIA})
    FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])
    post(client, f"/api/v1/runs/{task['run_id']}/loop")
    state = _wait_for_loop(client, task["id"])
    assert state["action"] == "review_completion", state
    assert state["completion_status"] == "pending"
    assert client.get(f"/api/v1/tasks/{task['id']}/commits").json()["total"] == 0
    confirm(client, task)
    post(client, f"/api/v1/runs/{task['run_id']}/loop")
    state = _wait_for_loop(client, task["id"])
    # No configured remote, but the confirmed work can now reach the local commit.
    assert client.get(f"/api/v1/tasks/{task['id']}/commits").json()["total"] == 1
    assert state["completion_status"] == "confirmed"


def committed_completion_task(client, directory, monkeypatch):
    FakeRemoteProvider.reset()
    main.remote_service.provider = FakeRemoteProvider(main.settings)
    git_output(directory, "remote", "add", "origin", "https://github.com/forge-demo/sandbox.git")
    task = passing_completion_task(client, directory, monkeypatch)
    confirm(client, task)
    commit_completion_task(client, task)
    approve(client, task, "push")
    asyncio.run(main.remote_service.push_branch(task["id"]))
    approve(client, task, "pr")
    return task


def commit_completion_task(client, task):
    preview = post(client, f"/api/v1/tasks/{task['id']}/commits/preview", {"message": "Fix value"})
    approve(client, task, "write", preview["scope_hash"])
    return post(client, f"/api/v1/tasks/{task['id']}/commits", {"message": "Fix value", "scope_hash": preview["scope_hash"]})


@pytest.mark.parametrize("new_commit", [False, True])
def test_reconfirmed_changes_cannot_publish_an_older_source_commit(api_client, demo_repository, monkeypatch, new_commit):
    task = committed_completion_task(api_client, demo_repository, monkeypatch)
    (demo_repository / "app.py").write_text("def value():\n    return 2  # reviewed revision\n", encoding="utf-8")
    post(api_client, f"/api/v1/runs/{task['run_id']}/tests")
    confirm(api_client, task)
    if new_commit:
        commit_completion_task(api_client, task)
    expected = "REMOTE_HEAD_MOVED" if new_commit else "UNCOMMITTED_TASK_CHANGES"
    preview = main.remote_service.preview_pull_request(task["id"])
    assert preview.ready is False
    with pytest.raises(RepositoryError) as caught:
        asyncio.run(main.remote_service.create_pull_request(task["id"], PullRequestCreateRequest(
            title="Fix value", body="Reviewed current revision", target_branch="main",
        )))
    assert caught.value.code == expected
    assert not main.remote_service.provider.pull_requests
    if not new_commit:
        with pytest.raises(RepositoryError) as caught:
            asyncio.run(main.remote_service.push_branch(task["id"]))
        assert caught.value.code == expected


def test_publish_allows_revalidated_changes_excluded_from_task_commit(api_client, demo_repository, monkeypatch):
    task = committed_completion_task(api_client, demo_repository, monkeypatch)
    (demo_repository / "notes.txt").write_text("user staging\n", encoding="utf-8")
    git_output(demo_repository, "add", "notes.txt")
    post(api_client, f"/api/v1/runs/{task['run_id']}/tests")
    confirm(api_client, task)
    pushed = asyncio.run(main.remote_service.push_branch(task["id"]))
    assert pushed.pushed_sha == git_output(demo_repository, "rev-parse", "HEAD").strip()
    assert git_output(demo_repository, "diff", "--cached", "--name-only").strip() == "notes.txt"


def test_pr_preview_includes_seed_commits_relative_to_remote_target(api_client, demo_repository, monkeypatch):
    git_output(demo_repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    (demo_repository / "test_seed.py").write_text("def test_seed():\n    assert True\n", encoding="utf-8")
    git_output(demo_repository, "add", "test_seed.py")
    git_output(demo_repository, "commit", "-qm", "Seed additional test")
    task = committed_completion_task(api_client, demo_repository, monkeypatch)
    preview = main.remote_service.preview_pull_request(task["id"])
    assert preview.ready is True
    assert preview.files == ["app.py", "test_seed.py"]
    assert "- `test_seed.py`" in preview.body


def test_pr_preview_marks_unknown_local_target_scope_as_incomplete(api_client, demo_repository, monkeypatch):
    task = committed_completion_task(api_client, demo_repository, monkeypatch)
    preview = main.remote_service.preview_pull_request(task["id"], "remote-only-target")
    assert preview.ready is False
    assert "complete PR file scope" in preview.body
    assert "unavailable" in preview.body


def test_direct_pr_requires_known_local_target_scope(api_client, demo_repository, monkeypatch):
    task = committed_completion_task(api_client, demo_repository, monkeypatch)
    remote = "https://github.com/forge-demo/sandbox.git"
    provider = main.remote_service.provider
    provider.branches[provider.repo_slug(remote)]["remote-only-target"] = git_output(demo_repository, "rev-parse", "main").strip()
    before = len(main.remote_service.list_remote_calls(task["id"]))
    response = api_client.post(f"/api/v1/tasks/{task['id']}/pull-request", json={
        "title": "Fix value", "body": "Reviewed", "target_branch": "remote-only-target", "draft": True,
    })
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "PR_SCOPE_UNAVAILABLE"
    assert len(main.remote_service.list_remote_calls(task["id"])) == before
    assert not provider.pull_requests
