from app import main
from test_api_workflow import post
from test_agent_workflow import approve_plan, create_branch, start_agent_iteration, apply_value, agent_advance


def prepared(client, directory):
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Bump the value"})
    approve_plan(client, task)
    create_branch(client, task)
    start_agent_iteration(client, task)
    return task


def apply_and_repair(client, task, value):
    """Apply one patch, fail the test, and open the next coding iteration (one checkpoint per round)."""
    apply_value(client, task, value)
    agent_advance(client, task["id"], {"action": "run_tests", "kind": "pytest", "target": "test_app.py::test_value", "timeout_seconds": 15})
    agent_advance(client, task["id"])


def checkpoint_dirs() -> list[str]:
    root = main.settings.state_dir / "checkpoints"
    return sorted(item.name for item in root.iterdir()) if root.is_dir() else []


def test_oldest_checkpoints_are_pruned_but_stay_in_history(api_client, demo_repository, monkeypatch):
    monkeypatch.setattr(main.settings, "checkpoint_max_per_task", 2)
    task = prepared(api_client, demo_repository)
    apply_and_repair(api_client, task, 0)
    apply_and_repair(api_client, task, 1)
    apply_value(api_client, task, 3)

    items = api_client.get(f"/api/v1/runs/{task['run_id']}/checkpoints").json()["items"]
    assert len(items) == 3, "pruning must keep the record, not delete it from history"

    oldest = min(items, key=lambda item: item["created_at"])
    assert oldest["pruned"] is True
    assert oldest["diff"] == ""
    assert oldest["files"], "the file list stays so the history row still says what changed"
    assert oldest["id"] not in checkpoint_dirs()

    for kept in (item for item in items if not item["pruned"]):
        assert kept["id"] in checkpoint_dirs()

    blocked = api_client.post(f"/api/v1/runs/{task['run_id']}/rollback", json={"checkpoint_id": oldest["id"]})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "CHECKPOINT_PRUNED"

    newest = max(items, key=lambda item: item["created_at"])
    allowed = api_client.post(f"/api/v1/runs/{task['run_id']}/rollback", json={"checkpoint_id": newest["id"]})
    assert allowed.status_code == 200, allowed.text


def test_pruned_checkpoints_come_back_after_a_restart(api_stack, demo_repository, monkeypatch):
    client = api_stack.restart()
    monkeypatch.setattr(main.settings, "checkpoint_max_per_task", 1)
    task = prepared(client, demo_repository)
    apply_and_repair(client, task, 0)
    apply_value(client, task, 1)

    restarted = api_stack.restart()
    items = restarted.get(f"/api/v1/runs/{task['run_id']}/checkpoints").json()["items"]
    # A pruned checkpoint has no backup directory; it must not be dropped on load.
    assert len(items) == 2
    assert [item["pruned"] for item in items].count(True) == 1
