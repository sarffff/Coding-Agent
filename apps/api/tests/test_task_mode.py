import pytest

from app import main
from app.approval_service import plan_scope_hash
from test_api_workflow import create_task, post
from test_workspace_ownership import folder_task, held_loop, workspace_api


def test_mode_change_preserves_approvals_and_persists_without_starting(api_stack, demo_repository, monkeypatch):
    client = api_stack.restart()
    repository = post(client, "/api/v1/repositories", {"path": str(demo_repository)})
    task = post(client, "/api/v1/tasks", {
        "repository_id": repository["id"], "goal": "Fix the reported bug", "mode": "conservative",
    })
    pending = post(client, f"/api/v1/tasks/{task['id']}/approvals", {"type": "plan", "summary": "Review this plan"})
    before_scope = plan_scope_hash(main.task_service.get(task["id"]))
    before_budget = client.get(f"/api/v1/tasks/{task['id']}/budget").json()

    def unexpected_start(*args):
        raise AssertionError("Changing mode must not start the agent loop")

    monkeypatch.setattr(main.agent_service, "start_loop", unexpected_start)
    for mode in ["standard", "autonomous", "conservative"]:
        changed = client.put(f"/api/v1/tasks/{task['id']}/mode", json={"mode": mode})
        assert changed.status_code == 200, changed.text
        assert changed.json()["mode"] == mode
        assert changed.json()["current_iteration"] == 0
        assert plan_scope_hash(main.task_service.get(task["id"])) == before_scope
        assert main.approval_service.get(pending["id"]).status == "pending"
        assert client.get(f"/api/v1/tasks/{task['id']}/budget").json() == before_budget
        assert not main.agent_service._loop_running(task["id"])
    events = client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]
    assert [event["details"]["mode"] for event in events if event["action"] == "task.mode"] == ["standard", "autonomous", "conservative"]
    assert not any(event["action"] in {"agent.loop", "approval.decision"} for event in events)

    assert client.put(f"/api/v1/tasks/{task['id']}/mode", json={"mode": "standard"}).status_code == 200
    restarted = api_stack.restart()
    assert restarted.get(f"/api/v1/tasks/{task['id']}").json()["mode"] == "standard"
    approvals = restarted.get(f"/api/v1/tasks/{task['id']}/approvals").json()["items"]
    assert next(item for item in approvals if item["id"] == pending["id"])["status"] == "pending"


def test_mode_change_retains_explicit_approval_scope(api_client, demo_repository):
    task = folder_task(api_client, demo_repository)
    approved = [item.model_dump() for item in main.approval_service.list_for_task(task["id"])]
    changed = api_client.put(f"/api/v1/tasks/{task['id']}/mode", json={"mode": "conservative"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["current_iteration"] == 1
    assert [item.model_dump() for item in main.approval_service.list_for_task(task["id"])] == approved


def test_recovery_task_can_change_mode_without_recovering_or_executing(api_stack, demo_repository):
    client = api_stack.restart()
    task = create_task(client, demo_repository)
    restarted = api_stack.restart()
    task_url = f"/api/v1/tasks/{task['id']}"
    before = restarted.get(task_url).json()
    assert before["requires_recovery"] is True
    approvals = restarted.get(task_url + "/approvals").json()["items"]
    original = (demo_repository / "app.py").read_bytes()

    changed = restarted.put(task_url + "/mode", json={"mode": "autonomous"})
    assert changed.status_code == 200, changed.text
    after = changed.json()
    assert after["mode"] == "autonomous"
    assert {key: value for key, value in after.items() if key not in {"mode", "updated_at", "budget"}} == {
        key: value for key, value in before.items() if key not in {"mode", "updated_at", "budget"}
    }
    before_budget = {key: value for key, value in before["budget"].items() if key != "elapsed_seconds"}
    assert {key: value for key, value in after["budget"].items() if key != "elapsed_seconds"} == before_budget
    assert restarted.get(task_url + "/approvals").json()["items"] == approvals
    assert not main.agent_service._loop_running(task["id"])

    for endpoint in [f"/api/v1/runs/{task['run_id']}/loop", f"/api/v1/runs/{task['run_id']}/patches/draft", task_url + "/iterations"]:
        execution = restarted.post(endpoint, json={})
        assert execution.status_code == 400, execution.text
        assert execution.json()["code"] == "RECOVERY_CONFIRMATION_REQUIRED"
    final = restarted.get(task_url).json()
    assert final["requires_recovery"] is True
    assert final["status"] == before["status"]
    assert {key: value for key, value in final["budget"].items() if key != "elapsed_seconds"} == before_budget
    assert (demo_repository / "app.py").read_bytes() == original
    persisted = api_stack.restart().get(task_url).json()
    assert persisted["mode"] == "autonomous"
    assert persisted["requires_recovery"] is True


@pytest.mark.parametrize("state", ["done", "completed", "cancelled", "pr_created", "cowork"])
def test_unavailable_task_mode_does_not_change_state(api_client, monkeypatch, state):
    task = post(api_client, "/api/v1/tasks", {"goal": "Unchanged permission", "track": "cowork" if state == "cowork" else "code"})
    current = main.task_service.get(task["id"])
    if state != "cowork":
        monkeypatch.setattr(current, "status", state)
    before = current.model_dump()
    result = api_client.put(f"/api/v1/tasks/{task['id']}/mode", json={"mode": "autonomous"})
    assert result.status_code == 400, result.text
    assert result.json()["code"] == "INVALID_TASK_STATE"
    assert current.model_dump() == before


@pytest.mark.parametrize("running", ["loop", "tests"])
def test_running_task_mode_rejected_before_mutation(api_client, monkeypatch, running):
    task = post(api_client, "/api/v1/tasks", {"goal": "Busy task", "mode": "conservative"})
    if running == "loop":
        monkeypatch.setattr(main.agent_service, "_loop_running", lambda task_id: task_id == task["id"])
    else:
        monkeypatch.setattr(main.test_service, "is_running", lambda run_id: run_id == task["run_id"])
    result = api_client.put(f"/api/v1/tasks/{task['id']}/mode", json={"mode": "autonomous"})
    assert result.status_code == 409, result.text
    assert result.json()["code"] == "WORKSPACE_BUSY"
    assert main.task_service.get(task["id"]).mode == "conservative"


@pytest.mark.parametrize("same_task", [True, False])
def test_live_loop_reserves_permissions_for_its_workspace(workspace_api, tmp_path, monkeypatch, same_task):
    owner = folder_task(workspace_api, tmp_path / "project")
    other = owner if same_task else folder_task(workspace_api, tmp_path / "project")
    with held_loop(workspace_api, owner, monkeypatch):
        response = workspace_api.put(f"/api/v1/tasks/{other['id']}/mode", json={"mode": "autonomous"})
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "WORKSPACE_BUSY"
        assert main.task_service.get(other["id"]).mode == "standard"
    assert workspace_api.put(f"/api/v1/tasks/{other['id']}/mode", json={"mode": "conservative"}).status_code == 200


def test_invalid_mode_is_rejected_at_the_request_boundary(api_client):
    task = post(api_client, "/api/v1/tasks", {"goal": "Retain mode"})
    result = api_client.put(f"/api/v1/tasks/{task['id']}/mode", json={"mode": "invalid"})
    assert result.status_code == 422, result.text
    assert main.task_service.get(task["id"]).mode == "standard"
