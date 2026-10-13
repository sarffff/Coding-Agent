import time

import pytest
from conftest import build_settings, git_output, mount_services
from fastapi.testclient import TestClient
from test_api_workflow import post

from app import main
from app.model_provider import FakeModelProvider
from app.models import PatchFile

REMOTE_URL = "https://github.com/forge-demo/sandbox.git"
FIX = [PatchFile(path="app.py", content="def value():\n    return 2\n")]


@pytest.fixture(autouse=True)
def clean_fakes():
    from app.remote_provider import FakeRemoteProvider

    FakeModelProvider.reset()
    FakeRemoteProvider.reset()
    yield
    FakeModelProvider.reset()
    FakeRemoteProvider.reset()


@pytest.fixture
def modes_api(demo_repository, tmp_path, monkeypatch):
    git_output(demo_repository, "remote", "add", "origin", REMOTE_URL)
    settings = build_settings(demo_repository, tmp_path, llm_provider="fake", remote_provider="fake")
    services = mount_services(monkeypatch, settings)
    with TestClient(main.app) as client:
        yield client, demo_repository
    services.store.close()


def _wait_for_loop(client, task_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get(f"/api/v1/tasks/{task_id}/agent").json()
        if not state["loop_running"]:
            return state
        time.sleep(0.1)
    raise AssertionError("agent loop did not finish in time")


def _seed_task(client, directory, mode):
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Fix value so the local test passes", "mode": mode})
    FakeModelProvider.seed(FIX)
    return task


def _approve(client, approval_id):
    return post(client, f"/api/v1/approvals/{approval_id}/decision", {"decision": "approve"})


# <!-- TESTS -->
def test_conservative_pauses_at_plan_each_patch_then_commit(modes_api):
    client, directory = modes_api
    task = _seed_task(client, directory, "conservative")

    # Nothing is auto-granted: the loop stops at the plan gate with a pending approval to show inline.
    post(client, f"/api/v1/runs/{task['run_id']}/loop", {"mode": "conservative"})
    s1 = _wait_for_loop(client, task["id"])
    assert s1["action"] == "await_plan_approval"
    assert s1["pending_approval"]["type"] == "plan"
    assert client.get(f"/api/v1/tasks/{task['id']}/commits").json()["total"] == 0

    # Plan approval starts the iteration, but every patch still needs manual preview and confirmation.
    _approve(client, s1["pending_approval"]["id"])
    post(client, f"/api/v1/runs/{task['run_id']}/loop", {})
    s2 = _wait_for_loop(client, task["id"])
    assert s2["action"] == "review_patch"
    assert "Patch panel" in s2["message"]
    assert FakeModelProvider._calls == []
    assert (directory / "app.py").read_text(encoding="utf-8") == "def value():\n    return 1\n"
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {})
    assert (directory / "app.py").read_text(encoding="utf-8") == "def value():\n    return 1\n"
    post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {"patch_id": draft["patch_id"], "confirm": True})
    post(client, f"/api/v1/runs/{task['run_id']}/loop", {})
    s2 = _wait_for_loop(client, task["id"])
    assert s2["action"] == "review_commit"
    assert s2["pending_approval"]["type"] == "write"
    assert client.get(f"/api/v1/tasks/{task['id']}/commits").json()["total"] == 0  # patch applied, not committed
    assert (directory / "app.py").read_text(encoding="utf-8") == "def value():\n    return 2\n"

    # Approve commit inline, resume: it commits, then stops at the push gate (publishing still human).
    _approve(client, s2["pending_approval"]["id"])
    post(client, f"/api/v1/runs/{task['run_id']}/loop", {})
    s3 = _wait_for_loop(client, task["id"])
    assert s3["action"] == "review_push"
    assert s3["pending_approval"]["type"] == "push"
    assert client.get(f"/api/v1/tasks/{task['id']}/commits").json()["total"] == 1
    assert client.get(f"/api/v1/tasks/{task['id']}/remote-branches").json()["total"] == 0


def test_conservative_repair_stops_before_drafting_a_second_patch(modes_api):
    client, directory = modes_api
    task = _seed_task(client, directory, "conservative")
    post(client, f"/api/v1/runs/{task['run_id']}/loop", {})
    plan = _wait_for_loop(client, task["id"])
    _approve(client, plan["pending_approval"]["id"])
    post(client, f"/api/v1/runs/{task['run_id']}/loop", {})
    assert _wait_for_loop(client, task["id"])["action"] == "review_patch"
    preview = post(client, f"/api/v1/runs/{task['run_id']}/patches/preview", {
        "files": [{"path": "app.py", "content": "def value():\n    return 0\n"}],
    })
    post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {"patch_id": preview["patch_id"], "confirm": True})
    post(client, f"/api/v1/runs/{task['run_id']}/loop", {})
    repair = _wait_for_loop(client, task["id"])
    assert repair["action"] == "review_patch"
    assert repair["iteration"]["number"] == 2
    assert FakeModelProvider._calls == []
    assert (directory / "app.py").read_text(encoding="utf-8") == "def value():\n    return 0\n"


def test_standard_surfaces_a_pending_push_approval(modes_api):
    client, directory = modes_api
    task = _seed_task(client, directory, "standard")

    post(client, f"/api/v1/runs/{task['run_id']}/loop", {"mode": "standard"})
    final = _wait_for_loop(client, task["id"])
    # Auto-to-commit, then an inline push approval the human can decide in chat (nothing pushed yet).
    assert final["action"] == "review_push"
    assert final["pending_approval"]["type"] == "push"
    assert client.get(f"/api/v1/tasks/{task['id']}/commits").json()["total"] == 1
    assert client.get(f"/api/v1/tasks/{task['id']}/remote-branches").json()["total"] == 0


def test_autonomous_pushes_and_opens_draft_pr(modes_api):
    client, directory = modes_api
    task = _seed_task(client, directory, "autonomous")

    post(client, f"/api/v1/runs/{task['run_id']}/loop", {"mode": "autonomous"})
    final = _wait_for_loop(client, task["id"])
    # Explicit per-task consent to cross the machine boundary: it pushes and opens a draft PR, then waits for review.
    assert final["task"]["status"] == "pr_created"
    assert final["action"] == "await_pr"
    assert client.get(f"/api/v1/tasks/{task['id']}/commits").json()["total"] == 1
    assert client.get(f"/api/v1/tasks/{task['id']}/remote-branches").json()["total"] == 1
    pr = client.get(f"/api/v1/tasks/{task['id']}/pull-request").json()
    assert pr["draft"] is True and pr["number"] == 1
    actions = {event["action"] for event in client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]}
    assert {"branch.push", "git.pr.create"} <= actions
