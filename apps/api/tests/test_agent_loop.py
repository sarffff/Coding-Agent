import asyncio
import time
from threading import Event

import pytest
from conftest import build_settings, mount_services
from fastapi.testclient import TestClient
from test_api_workflow import create_task, post

from app import main
from app.model_provider import FakeModelProvider, PatchProposal
from app.models import PatchFile


@pytest.fixture(autouse=True)
def clean_fake_model():
    FakeModelProvider.reset()
    yield
    FakeModelProvider.reset()


@pytest.fixture
def model_api(demo_repository, tmp_path, monkeypatch):
    settings = build_settings(demo_repository, tmp_path, llm_provider="fake")
    services = mount_services(monkeypatch, settings)
    with TestClient(main.app) as client:
        yield client, demo_repository
    services.store.close()


def _wait_for_loop(client, task_id, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get(f"/api/v1/tasks/{task_id}/agent").json()
        if not state["loop_running"]:
            return state
        time.sleep(0.1)
    raise AssertionError("agent loop did not finish in time")


def _wait_for_file(path, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"{path} did not appear in time")


def test_agent_loop_drives_to_commit_and_stops_at_push(model_api):
    client, directory = model_api
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Fix value so the local test passes"})
    FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])

    started = post(client, f"/api/v1/runs/{task['run_id']}/loop", {})
    assert started["loop_running"] is True

    final = _wait_for_loop(client, task["id"])
    assert final["loop_running"] is False
    assert final["task"]["status"] == "ready_for_pr"
    assert final["action"] == "review_push"  # stopped at the publish gate — nothing leaves the machine without a human

    assert client.get(f"/api/v1/tasks/{task['id']}/commits").json()["total"] == 1
    assert client.get(f"/api/v1/tasks/{task['id']}/remote-branches").json()["total"] == 0  # not pushed
    assert (directory / "app.py").read_text(encoding="utf-8") == "def value():\n    return 2\n"
    actions = {event["action"] for event in client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]}
    assert {"agent.loop", "git.branch.create", "patch.draft", "patch.apply", "tests.run", "git.commit.create"} <= actions
    context = dict(FakeModelProvider._calls[0][1])
    assert "return 1" in context["app.py"]
    assert "assert value() == 2" in context["test_app.py"]


def test_agent_loop_stops_when_the_budget_trips(demo_repository, tmp_path, monkeypatch):
    settings = build_settings(demo_repository, tmp_path, llm_provider="fake", task_max_consecutive_failures=1)
    services = mount_services(monkeypatch, settings)
    with TestClient(main.app) as client:
        repository = post(client, "/api/v1/repositories", {"path": str(demo_repository)})
        task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Fix the failing test"})
        FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 0\n")])  # keeps the test red
        post(client, f"/api/v1/runs/{task['run_id']}/loop", {})
        final = _wait_for_loop(client, task["id"])
        assert final["action"] == "manual_review"
        assert final["task"]["budget"]["exhausted_reason"] == "consecutive_failures"
        assert client.get(f"/api/v1/tasks/{task['id']}/commits").json()["total"] == 0
    services.store.close()


def test_agent_loop_surfaces_a_protected_path(model_api):
    client, directory = model_api
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Add a config file"})
    FakeModelProvider.seed([PatchFile(path=".env", content="SECRET=1\n", operation="create")])
    post(client, f"/api/v1/runs/{task['run_id']}/loop", {})
    final = _wait_for_loop(client, task["id"])
    assert final["task"]["status"] == "coding"  # the write was blocked by the preview gate; never reached testing
    assert client.get(f"/api/v1/tasks/{task['id']}/commits").json()["total"] == 0
    events = client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]
    assert any(event["action"] == "agent.loop" and event["status"] == "failed" for event in events)


SLOW_TEST = "import time\nfrom pathlib import Path\n\ndef test_slow():\n    Path('.loop-test-started').write_text('x')\n    time.sleep(20)\n"


def test_agent_loop_reports_running_and_can_be_stopped(model_api):
    client, directory = model_api
    task = create_task(client, directory)  # repo+task+branch+plan+iteration while the worktree is still clean
    (directory / "test_app.py").write_text(SLOW_TEST, encoding="utf-8")
    FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])

    post(client, f"/api/v1/runs/{task['run_id']}/loop", {})
    _wait_for_file(directory / ".loop-test-started")  # loop reached run_tests and the slow test is executing

    assert client.get(f"/api/v1/tasks/{task['id']}/agent").json()["loop_running"] is True
    second = client.post(f"/api/v1/runs/{task['run_id']}/loop", json={})
    assert second.status_code == 409
    assert second.json()["code"] == "LOOP_ALREADY_RUNNING"

    post(client, f"/api/v1/runs/{task['run_id']}/loop/stop")
    final = _wait_for_loop(client, task["id"])
    assert final["loop_running"] is False


@pytest.mark.parametrize("ignore_cancellation", [False, True])
@pytest.mark.parametrize("action", ["stop", "pause", "cancel"])
def test_stop_cancels_waiting_model_and_never_applies_its_reply(model_api, monkeypatch, ignore_cancellation, action):
    client, directory = model_api
    task = create_task(client, directory)
    original = (directory / "app.py").read_text(encoding="utf-8")
    entered, cancelled = Event(), Event()

    async def delayed_model(goal, context_files, failure):
        entered.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            if not ignore_cancellation:
                raise
        return PatchProposal(files=[PatchFile(path="app.py", content="def value():\n    return 2\n")])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", delayed_model)
    post(client, f"/api/v1/runs/{task['run_id']}/loop", {})
    assert entered.wait(5)
    endpoint = f"/api/v1/runs/{task['run_id']}/loop/stop" if action == "stop" else f"/api/v1/tasks/{task['id']}/{action}"
    post(client, endpoint)
    final = _wait_for_loop(client, task["id"], timeout=5)
    assert cancelled.is_set()
    assert final["task"]["status"] == {"stop": "coding", "pause": "paused", "cancel": "cancelled"}[action]
    assert (directory / "app.py").read_text(encoding="utf-8") == original
    assert client.get(f"/api/v1/runs/{task['run_id']}/checkpoints").json()["total"] == 0
    events = client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]
    assert not any(event["action"] in {"patch.apply", "tests.run"} for event in events)
    assert events[-1]["summary"] == "Agent loop stopped by request"


@pytest.mark.parametrize("overlap", ["same", "child", "separate"])
def test_running_agent_reserves_overlapping_folders(model_api, tmp_path, monkeypatch, overlap):
    client, _ = model_api
    directory = tmp_path / "plain"
    directory.mkdir()
    (directory / "app.py").write_text("def value(): return 1", encoding="utf-8")
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    first = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Fix app.py"})
    entered = Event()
    async def waiting(*args):
        entered.set()
        await asyncio.sleep(60)
    monkeypatch.setattr(main.agent_service.models, "propose_patch", waiting)
    post(client, f"/api/v1/runs/{first['run_id']}/loop")
    assert entered.wait(5)
    try:
        other = directory if overlap == "same" else directory / "child" if overlap == "child" else tmp_path / "separate"
        other.mkdir(exist_ok=True)
        registered = post(client, "/api/v1/repositories", {"path": str(other)})
        second = post(client, "/api/v1/tasks", {"repository_id": registered["id"], "goal": "Fix files", "mode": "conservative"})
        response = client.post(f"/api/v1/runs/{second['run_id']}/loop", json={})
        if overlap == "separate":
            assert response.status_code == 200
            _wait_for_loop(client, second["id"])
        else:
            assert response.status_code == 409
            assert response.json()["code"] == "WORKSPACE_BUSY"
    finally:
        post(client, f"/api/v1/runs/{first['run_id']}/loop/stop")
        _wait_for_loop(client, first["id"], timeout=5)
    post(client, f"/api/v1/runs/{second['run_id']}/loop")
    _wait_for_loop(client, second["id"])


def test_plain_folder_loop_validates_and_rolls_back_without_git(model_api, tmp_path):
    client, _ = model_api
    directory = tmp_path / "plain-folder"
    directory.mkdir()
    original = "def value():\n    return 1\n"
    (directory / "app.py").write_text(original, encoding="utf-8")
    (directory / "test_app.py").write_text("from app import value\n\ndef test_value():\n    assert value() == 2\n", encoding="utf-8")
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    assert repository["has_git"] is False
    task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Fix value so the local test passes", "mode": "autonomous"})
    FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])

    post(client, f"/api/v1/runs/{task['run_id']}/loop", {})
    final = _wait_for_loop(client, task["id"])
    assert final["action"] == "manual_review"
    assert "Local validation passed" in final["message"]
    assert final["last_test"]["status"] == "passed"
    assert final["last_test"]["validation_hash"]
    assert final["task"]["task_branch"] is None
    assert (directory / "app.py").read_text(encoding="utf-8") == "def value():\n    return 2\n"
    assert client.get(f"/api/v1/tasks/{task['id']}/commits").json()["total"] == 0
    assert client.get(f"/api/v1/tasks/{task['id']}/remote-branches").json()["total"] == 0
    events = client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]
    assert not any(event["action"].startswith("git.") for event in events)

    (directory / "app.py").write_text("def value():\n    return 2  # external edit\n", encoding="utf-8")
    assert client.get(f"/api/v1/tasks/{task['id']}/agent").json()["action"] == "run_tests"
    checkpoint = client.get(f"/api/v1/runs/{task['run_id']}/checkpoints").json()["items"][0]
    assert "-    return 1" in checkpoint["diff"]
    assert "+    return 2" in checkpoint["diff"]
    conflict = client.post(f"/api/v1/runs/{task['run_id']}/rollback", json={"checkpoint_id": checkpoint["id"]})
    assert conflict.json()["code"] == "ROLLBACK_CONFLICT"
    (directory / "app.py").write_bytes(b"def value():\n    return 2\n")
    post(client, f"/api/v1/runs/{task['run_id']}/rollback", {"checkpoint_id": checkpoint["id"]})
    assert (directory / "app.py").read_text(encoding="utf-8") == original

