import asyncio

import pytest
from conftest import build_settings, git_output, mount_services
from test_api_workflow import approve, passing_task, post

from app.repository_service import RepositoryError


REMOTE_URL = "https://github.com/forge-demo/sandbox.git"


class RemoteApi:
    """Test client over one state directory, with an in-memory fake provider."""

    def __init__(self, repository, tmp_path, monkeypatch):
        self.repository = repository
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.services = None
        self.client = None

    def mount(self):
        from app import main
        from fastapi.testclient import TestClient

        if self.services is not None:
            self.services.store.close()
        settings = build_settings(self.repository, self.tmp_path, remote_provider="fake")
        self.services = mount_services(self.monkeypatch, settings)
        self.client = TestClient(main.app)
        return self.client

    @property
    def provider(self):
        return self.services.remote_service.provider


@pytest.fixture
def remote_repository(demo_repository):
    git_output(demo_repository, "remote", "add", "origin", REMOTE_URL)
    return demo_repository


@pytest.fixture(autouse=True)
def clean_fake_remote():
    from app.remote_provider import FakeRemoteProvider

    FakeRemoteProvider.reset()
    yield
    FakeRemoteProvider.reset()


@pytest.fixture
def remote_api(remote_repository, tmp_path, monkeypatch):
    api = RemoteApi(remote_repository, tmp_path, monkeypatch)
    api.mount()
    return api


def commit_passing_task(client, task):
    preview = post(client, f"/api/v1/tasks/{task['id']}/commits/preview", {"message": "Fix value"})
    approve(client, task, "write", preview["scope_hash"])
    return post(client, f"/api/v1/tasks/{task['id']}/commits", {"message": "Fix value", "scope_hash": preview["scope_hash"]})


def test_push_requires_approval_then_publishes_the_task_branch(remote_api, remote_repository):
    client = remote_api.client
    task = passing_task(client, remote_repository)
    committed = commit_passing_task(client, task)

    blocked = client.post(f"/api/v1/tasks/{task['id']}/push", json={"remote": "origin"})
    assert blocked.status_code == 400
    assert blocked.json()["code"] == "PUSH_APPROVAL_REQUIRED"
    assert remote_api.provider.pushes == []

    approve(client, task, "push")
    pushed = post(client, f"/api/v1/tasks/{task['id']}/push", {"remote": "origin"})
    head = git_output(remote_repository, "rev-parse", "HEAD").strip()
    assert pushed["pushed_sha"] == head == committed["head"]
    assert pushed["branch"] == git_output(remote_repository, "branch", "--show-current").strip()
    assert pushed["remote_url"] == REMOTE_URL
    assert remote_api.provider.pushes == [("github.com/forge-demo/sandbox", pushed["branch"], head)]

    commits = client.get(f"/api/v1/tasks/{task['id']}/commits").json()["items"]
    assert [item["commit_sha"] for item in commits] == [head]
    state = client.get(f"/api/v1/tasks/{task['id']}/publish-state").json()
    assert [item["id"] for item in state["remote_branches"]] == [pushed["id"]]
    assert state["pull_request"] is None
    actions = {item["action"] for item in client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]}
    assert {"commit.record", "branch.push"} <= actions


def test_idempotency_key_replays_the_first_push_without_repeating_it(remote_api):
    client = remote_api.client
    task = passing_task(client, remote_api.repository)
    commit_passing_task(client, task)
    approve(client, task, "push")

    headers = {"Idempotency-Key": "push-key-0001"}
    first = client.post(f"/api/v1/tasks/{task['id']}/push", json={"remote": "origin"}, headers=headers)
    assert first.status_code == 201, first.text
    replay = client.post(f"/api/v1/tasks/{task['id']}/push", json={"remote": "origin"}, headers=headers)
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert len(remote_api.provider.pushes) == 1

    fresh = client.post(f"/api/v1/tasks/{task['id']}/push", json={"remote": "origin"}, headers={"Idempotency-Key": "push-key-0002"})
    assert fresh.status_code == 201
    assert len(remote_api.provider.pushes) == 2
    assert client.get(f"/api/v1/tasks/{task['id']}/remote-branches").json()["total"] == 2


def test_idempotency_key_is_bound_to_one_endpoint_and_body(remote_api):
    client = remote_api.client
    task = passing_task(client, remote_api.repository)
    commit_passing_task(client, task)
    approve(client, task, "push")
    headers = {"Idempotency-Key": "shared-key-00001"}
    assert client.post(f"/api/v1/tasks/{task['id']}/push", json={"remote": "origin"}, headers=headers).status_code == 201

    reused = client.post(
        f"/api/v1/tasks/{task['id']}/pull-request",
        json={"title": "Fix value", "body": "Publishes the reviewed change.", "target_branch": "main"},
        headers=headers,
    )
    assert reused.status_code == 409
    assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

    malformed = client.post(f"/api/v1/tasks/{task['id']}/push", json={"remote": "origin"}, headers={"Idempotency-Key": "short"})
    assert malformed.status_code == 400
    assert malformed.json()["code"] == "INVALID_IDEMPOTENCY_KEY"


def test_draft_pull_request_requires_push_and_pr_approval(remote_api):
    client = remote_api.client
    task = passing_task(client, remote_api.repository)
    commit_passing_task(client, task)

    preview = client.get(f"/api/v1/tasks/{task['id']}/pull-request/preview").json()
    assert preview["ready"] is False
    assert preview["validation_passed"] is True
    assert preview["files"] == ["app.py"]

    approve(client, task, "push")
    post(client, f"/api/v1/tasks/{task['id']}/push", {"remote": "origin"})
    assert client.get(f"/api/v1/tasks/{task['id']}/pull-request/preview").json()["ready"] is True

    body = {"title": "[Forge] Fix value", "body": "Return 2 so the local test passes.", "target_branch": "main"}
    blocked = client.post(f"/api/v1/tasks/{task['id']}/pull-request", json=body)
    assert blocked.status_code == 400
    assert blocked.json()["code"] == "PR_APPROVAL_REQUIRED"

    approve(client, task, "pr")
    created = post(client, f"/api/v1/tasks/{task['id']}/pull-request", body)
    assert created["number"] == 1
    assert created["draft"] is True
    assert created["state"] == "draft"
    assert created["html_url"] == f"https://github.com/forge-demo/sandbox/pull/1"
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["status"] == "pr_created"
    assert client.get(f"/api/v1/tasks/{task['id']}/pull-request").json()["id"] == created["id"]

    refreshed = post(client, f"/api/v1/tasks/{task['id']}/pull-request/refresh")
    assert refreshed["number"] == 1

    same_branch = client.post(
        f"/api/v1/tasks/{task['id']}/pull-request",
        json={"title": "Self target", "body": "Targets its own branch.", "target_branch": created["source_branch"]},
    )
    assert same_branch.json()["code"] == "INVALID_TARGET_BRANCH"


def test_pull_request_creation_stops_when_the_remote_branch_disappears(remote_api):
    client = remote_api.client
    task = passing_task(client, remote_api.repository)
    commit_passing_task(client, task)
    approve(client, task, "push")
    approve(client, task, "pr")
    pushed = post(client, f"/api/v1/tasks/{task['id']}/push", {"remote": "origin"})

    slug = remote_api.provider.repo_slug(pushed["remote_url"])
    del remote_api.provider.branches[slug][pushed["branch"]]
    gone = client.post(
        f"/api/v1/tasks/{task['id']}/pull-request",
        json={"title": "[Forge] Fix value", "body": "Return 2 so the local test passes.", "target_branch": "main"},
    )
    assert gone.status_code == 400
    assert gone.json()["code"] == "REMOTE_SOURCE_BRANCH_MISSING"
    assert remote_api.provider.pull_requests.get(slug, []) == []


def test_disallowed_or_missing_remote_never_falls_back_to_a_guess(remote_api, remote_repository):
    client = remote_api.client
    task = passing_task(client, remote_repository)
    commit_passing_task(client, task)
    approve(client, task, "push")

    git_output(remote_repository, "remote", "set-url", "origin", "https://gitlab.com/forge-demo/sandbox.git")
    refused = client.post(f"/api/v1/tasks/{task['id']}/push", json={"remote": "origin"})
    assert refused.status_code == 400
    assert refused.json()["code"] == "DISALLOWED_REMOTE_HOST"
    assert remote_api.provider.pushes == []

    git_output(remote_repository, "remote", "remove", "origin")
    missing = client.post(f"/api/v1/tasks/{task['id']}/push", json={"remote": "origin"})
    assert missing.status_code == 400
    assert missing.json()["code"] == "REMOTE_NOT_CONFIGURED"
    assert remote_api.provider.pushes == []


def test_push_refuses_when_head_moved_after_the_approved_commit(remote_api, remote_repository):
    client = remote_api.client
    task = passing_task(client, remote_repository)
    commit_passing_task(client, task)
    (remote_repository / "notes.txt").write_text("hand committed later\n", encoding="utf-8")
    git_output(remote_repository, "commit", "-aqm", "Manual commit outside the task")
    approve(client, task, "push")

    moved = client.post(f"/api/v1/tasks/{task['id']}/push", json={"remote": "origin"})
    assert moved.status_code == 409
    assert moved.json()["code"] == "REMOTE_HEAD_MOVED"
    assert remote_api.provider.pushes == []


def test_remote_records_survive_restart_and_gate_the_next_write(remote_api, remote_repository):
    client = remote_api.client
    task = passing_task(client, remote_repository)
    commit_passing_task(client, task)
    approve(client, task, "push")
    pushed = post(client, f"/api/v1/tasks/{task['id']}/push", {"remote": "origin"})

    restarted = remote_api.mount()
    commits = restarted.get(f"/api/v1/tasks/{task['id']}/commits").json()["items"]
    branches = restarted.get(f"/api/v1/tasks/{task['id']}/remote-branches").json()["items"]
    assert [item["commit_sha"] for item in commits] == [pushed["pushed_sha"]]
    assert [item["id"] for item in branches] == [pushed["id"]]

    loaded = restarted.get(f"/api/v1/tasks/{task['id']}").json()
    assert loaded["requires_recovery"] is True
    assert loaded["status"] == "ready_for_pr"

    approve(restarted, task, "pr")
    gated = restarted.post(
        f"/api/v1/tasks/{task['id']}/pull-request",
        json={"title": "[Forge] Fix value", "body": "Return 2 so the local test passes.", "target_branch": "main"},
    )
    assert gated.status_code == 400
    assert gated.json()["code"] == "RECOVERY_CONFIRMATION_REQUIRED"

    post(restarted, f"/api/v1/tasks/{task['id']}/recover")
    created = post(
        restarted,
        f"/api/v1/tasks/{task['id']}/pull-request",
        {"title": "[Forge] Fix value", "body": "Return 2 so the local test passes.", "target_branch": "main"},
    )
    assert created["number"] == 1

    after_pr = remote_api.mount()
    assert after_pr.get(f"/api/v1/tasks/{task['id']}").json()["status"] == "pr_created"
    post(after_pr, f"/api/v1/tasks/{task['id']}/pull-request/refresh")
    post(after_pr, f"/api/v1/tasks/{task['id']}/pull-request/refresh")
    assert len(remote_api.services.store.items("pull_requests")) == 1


def test_provider_refuses_to_publish_protected_branches(remote_api, remote_repository):
    provider = remote_api.provider
    with pytest.raises(RepositoryError) as error:
        asyncio.run(provider.push_task_branch(remote_repository, REMOTE_URL, "main"))
    assert error.value.code == "PROTECTED_BRANCH_PUSH_PROHIBITED"


def test_github_provider_url_policy_and_allowlist():
    from app.config import Settings
    from app.remote_provider import GitHubRemoteProvider

    provider = GitHubRemoteProvider(Settings(allowed_remote_hosts=["github.com", "git.example.com"]))
    assert provider.parse_remote_url("git@github.com:owner/repo.git") == ("github.com", "owner", "repo")
    assert provider.validate_remote_url("https://git.example.com/owner/repo") == ("git.example.com", "owner", "repo")

    with pytest.raises(RepositoryError) as off_host:
        provider.validate_remote_url("https://gitlab.com/owner/repo.git")
    assert off_host.value.code == "DISALLOWED_REMOTE_HOST"

    with pytest.raises(RepositoryError) as unparsable:
        provider.parse_remote_url("not a remote url")
    assert unparsable.value.code == "INVALID_REMOTE_URL"

    scoped = GitHubRemoteProvider(Settings(allowed_repositories=["forge-demo/sandbox"]))
    assert scoped.validate_remote_url("https://github.com/forge-demo/sandbox.git")[1:] == ("forge-demo", "sandbox")
    with pytest.raises(RepositoryError) as other_repository:
        scoped.validate_remote_url("https://github.com/forge-demo/production.git")
    assert other_repository.value.code == "DISALLOWED_REPOSITORY"


def test_remote_calls_are_observed_without_token_or_body(remote_api, remote_repository):
    client = remote_api.client
    task = passing_task(client, remote_repository)
    task_url = f"/api/v1/tasks/{task['id']}"
    commit_passing_task(client, task)

    client.get(task_url + "/remote-target")  # one validate call
    approve(client, task, "push")
    post(client, task_url + "/push", {"remote": "origin"})
    approve(client, task, "pr")
    post(client, task_url + "/pull-request", {"title": "[Forge] Fix value", "body": "Return 2.", "target_branch": "main"})
    post(client, task_url + "/pull-request/refresh")

    calls = client.get(task_url + "/remote-calls").json()
    assert calls["total"] == 7  # target + push + PR repository/two branches/create + refresh
    budget = client.get(task_url + "/budget").json()
    assert budget["remote_calls"] == calls["total"]
    by_op = {}
    for item in calls["items"]:
        by_op.setdefault(item["operation"], item)
        assert item["duration_ms"] >= 0 and item["attempts"] >= 1
        # Only the status line is observed: no token, headers or response body leak into the record.
        assert set(item) == {"id", "task_id", "run_id", "operation", "status_code", "duration_ms", "attempts", "outcome", "error_code", "created_at"}
    assert by_op["validate"]["status_code"] == 200 and by_op["validate"]["outcome"] == "ok"
    assert by_op["push"]["status_code"] is None and by_op["push"]["outcome"] == "ok"  # git push is not an HTTP call
    assert by_op["create_pr"]["status_code"] == 201 and by_op["create_pr"]["outcome"] == "ok"
    assert by_op["refresh_pr"]["status_code"] == 200 and by_op["refresh_pr"]["outcome"] == "ok"

    # The desktop panel reads observability off publish-state, and records survive a restart.
    assert len(client.get(task_url + "/publish-state").json()["remote_calls"]) == calls["total"]
    restarted = remote_api.mount()
    assert restarted.get(task_url + "/remote-calls").json()["total"] == calls["total"]


def test_remote_call_records_a_failed_provider_call(remote_api, monkeypatch):
    client = remote_api.client
    task = passing_task(client, remote_api.repository)
    task_url = f"/api/v1/tasks/{task['id']}"
    commit_passing_task(client, task)
    approve(client, task, "push")
    post(client, task_url + "/push", {"remote": "origin"})
    approve(client, task, "pr")
    post(client, task_url + "/pull-request", {"title": "[Forge] Fix value", "body": "Return 2.", "target_branch": "main"})

    async def timeout(*args, **kwargs):
        raise RepositoryError("REMOTE_TIMEOUT", "simulated remote timeout")

    monkeypatch.setattr(remote_api.provider, "get_pull_request", timeout)
    assert client.post(task_url + "/pull-request/refresh").status_code == 504

    failed = [item for item in client.get(task_url + "/remote-calls").json()["items"] if item["operation"] == "refresh_pr"][-1]
    assert failed["outcome"] == "error"
    assert failed["error_code"] == "REMOTE_TIMEOUT"
    assert failed["status_code"] is None

    budget = client.get(task_url + "/budget").json()
    assert budget["remote_calls"] == len(client.get(task_url + "/remote-calls").json()["items"])
    assert budget["consecutive_failures"] == 1


@pytest.mark.parametrize("succeeds", [True, False])
def test_remote_budget_counts_attempts_and_blocks_the_provider_at_the_limit(remote_api, monkeypatch, succeeds):
    client = remote_api.client
    repository = post(client, "/api/v1/repositories", {"path": str(remote_api.repository)})
    task = post(client, "/api/v1/tasks", {"goal": "Inspect remote", "repository_id": repository["id"]})
    remote_api.services.task_service.get(task["id"]).budget.max_remote_calls = 1
    original = remote_api.provider.validate_repository
    attempts = []

    async def validate(remote_url):
        attempts.append(remote_url)
        if not succeeds:
            raise RepositoryError("REMOTE_TIMEOUT", "simulated remote timeout")
        return await original(remote_url)

    monkeypatch.setattr(remote_api.provider, "validate_repository", validate)
    task_url = f"/api/v1/tasks/{task['id']}"
    first = client.get(task_url + "/remote-target")
    assert first.status_code == (200 if succeeds else 504)
    blocked = client.get(task_url + "/remote-target")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "TASK_BUDGET_EXHAUSTED"
    assert attempts == [REMOTE_URL]
    budget = client.get(task_url + "/budget").json()
    assert budget["remote_calls"] == 1
    assert budget["consecutive_failures"] == (0 if succeeds else 1)
    assert client.get(task_url + "/remote-calls").json()["total"] == 1


def test_pr_preflight_calls_cannot_bypass_the_remaining_budget(remote_api):
    client = remote_api.client
    task = passing_task(client, remote_api.repository)
    task_url = f"/api/v1/tasks/{task['id']}"
    commit_passing_task(client, task)
    approve(client, task, "push")
    post(client, task_url + "/push", {"remote": "origin"})
    approve(client, task, "pr")
    remote_api.services.task_service.get(task["id"]).budget.max_remote_calls = 4

    response = client.post(task_url + "/pull-request", json={
        "title": "[Forge] Fix value", "body": "Return 2.", "target_branch": "main",
    })
    assert response.status_code == 409
    assert response.json()["code"] == "TASK_BUDGET_EXHAUSTED"
    assert remote_api.provider.pull_requests == {}
    calls = client.get(task_url + "/remote-calls").json()["items"]
    assert [call["operation"] for call in calls] == ["push", "validate", "validate", "validate"]
    assert client.get(task_url + "/budget").json()["remote_calls"] == 4


def test_remote_target_never_uses_the_parent_git_repository_of_a_local_folder(remote_api, monkeypatch):
    from app import remote_service

    folder = remote_api.repository / "local-folder"
    folder.mkdir()
    (folder / "notes.txt").write_text("Local scope only.\n", encoding="utf-8")
    client = remote_api.client
    repository = post(client, "/api/v1/repositories", {"path": str(folder)})
    assert repository["has_git"] is False
    task = post(client, "/api/v1/tasks", {"goal": "Edit local notes", "repository_id": repository["id"]})

    def unexpected_git(*args, **kwargs):
        raise AssertionError("A local folder must not read its parent's Git remote.")

    monkeypatch.setattr(remote_service.subprocess, "run", unexpected_git)
    task_url = f"/api/v1/tasks/{task['id']}"
    response = client.get(task_url + "/remote-target")
    assert response.status_code == 400
    assert response.json()["code"] == "GIT_NOT_AVAILABLE"
    assert client.get(task_url + "/budget").json()["remote_calls"] == 0
    assert client.get(task_url + "/remote-calls").json()["total"] == 0
    assert client.get(task_url + "/publish-state").json()["remote_url"] is None


@pytest.mark.parametrize("merged", [False, True])
def test_refreshed_pr_terminal_state_updates_agent_without_assuming_closed_means_merged(remote_api, merged):
    client = remote_api.client
    task = passing_task(client, remote_api.repository)
    task_url = f"/api/v1/tasks/{task['id']}"
    commit_passing_task(client, task)
    assert client.get(task_url + "/agent").json()["action"] == "review_push"
    approve(client, task, "push")
    post(client, task_url + "/push", {"remote": "origin"})
    assert client.get(task_url + "/agent").json()["action"] == "review_pr"
    approve(client, task, "pr")
    created = post(client, task_url + "/pull-request", {
        "title": "[Forge] Fix value", "body": "Return 2 so the local test passes.", "target_branch": "main",
    })
    assert client.get(task_url + "/agent").json()["action"] == "await_pr"
    remote = asyncio.run(remote_api.provider.get_pull_request(REMOTE_URL, created["number"]))
    remote.state = "closed"
    remote.merged = merged
    # A closed Draft PR retains draft=True; terminal state must take precedence.
    assert remote.draft is True
    for _ in range(2):
        refreshed = post(client, task_url + "/pull-request/refresh")
        assert refreshed["state"] == ("merged" if merged else "closed")
        state = client.get(task_url + "/agent").json()
        assert state["action"] == ("completed" if merged else "manual_review")
        assert state["task"]["status"] == ("completed" if merged else "pr_created")
        assert state["can_advance"] is False
