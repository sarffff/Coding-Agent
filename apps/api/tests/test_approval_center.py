from datetime import datetime, timedelta, timezone

import pytest

from test_api_workflow import approve, create_task, passing_task, post


def request(client, task, kind="plan", scope_hash=None):
    return post(client, f"/api/v1/tasks/{task['id']}/approvals", {
        "type": kind, "summary": "Review the proposed task change", "scope_hash": scope_hash,
    })


@pytest.mark.parametrize("decision", ["reject", "request_changes"])
@pytest.mark.parametrize("reason", [None, "", " \n\t "])
def test_negative_decisions_require_a_nonblank_reason(api_client, decision, reason):
    task = post(api_client, "/api/v1/tasks", {"goal": "Review the local task plan"})
    approval = request(api_client, task)
    response = api_client.post(f"/api/v1/approvals/{approval['id']}/decision", json={"decision": decision, "reason": reason})
    assert response.status_code == 422
    assert response.json()["code"] == "APPROVAL_REASON_REQUIRED"
    assert api_client.get("/api/v1/approvals").json()["items"][0]["status"] == "pending"


def test_approval_center_filters_expiration_and_latest_decision(api_client):
    from app import main

    task = post(api_client, "/api/v1/tasks", {"goal": "Inspect the approval history"})
    approved = approve(api_client, task, "plan")
    assert main.approval_service.has_approved(task["id"], "plan", approved["scope_hash"])
    for decision in ("reject", "request_changes", "cancel"):
        approval = request(api_client, task)
        saved = post(api_client, f"/api/v1/approvals/{approval['id']}/decision", {"decision": decision, "reason": "  Explain the changed requirement  "})
        assert saved["reason"] == "Explain the changed requirement"
        assert not main.approval_service.has_approved(task["id"], "plan", approved["scope_hash"])
    expired = request(api_client, task, "write", "a" * 64)
    main.approval_service.get(expired["id"]).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    pending = request(api_client, task, "push")
    other = post(api_client, "/api/v1/tasks", {"goal": "Another independent review"})
    request(api_client, other, "pr")
    history = api_client.get("/api/v1/approvals", params={"task_id": task["id"]}).json()
    assert history["total"] == 6
    assert {item["status"] for item in history["items"]} == {"approved", "rejected", "request_changes", "cancelled", "expired", "pending"}
    filtered = api_client.get("/api/v1/approvals", params={"task_id": task["id"], "status": "pending", "type": "push"}).json()
    assert [item["id"] for item in filtered["items"]] == [pending["id"]]
    assert api_client.get("/api/v1/approvals", params={"status": "invalid"}).status_code == 422


def test_plan_feedback_reopens_approval_before_any_iteration(api_client):
    task = post(api_client, "/api/v1/tasks", {"goal": "Improve local task planning"})
    original = request(api_client, task)
    feedback = "Keep all existing user changes and explain the affected files"
    post(api_client, f"/api/v1/approvals/{original['id']}/decision", {"decision": "request_changes", "reason": feedback})
    continued = post(api_client, f"/api/v1/approvals/{original['id']}/continue")
    assert continued["status"] == "awaiting_approval"
    assert continued["current_iteration"] == 0
    assert feedback in continued["goal"]
    assert feedback in continued["plan"]["objective"]
    approvals = api_client.get(f"/api/v1/tasks/{task['id']}/approvals").json()["items"]
    assert [item["status"] for item in approvals] == ["pending", "request_changes"]
    assert approvals[0]["scope_hash"] != original["scope_hash"]
    assert approvals[1]["continued_at"]
    blocked = api_client.post(f"/api/v1/tasks/{task['id']}/iterations", json={})
    assert blocked.json()["code"] == "APPROVAL_REQUIRED"
    duplicate = post(api_client, f"/api/v1/approvals/{original['id']}/continue")
    assert duplicate["goal"] == continued["goal"]
    assert api_client.get(f"/api/v1/tasks/{task['id']}/approvals").json()["total"] == 2
    post(api_client, f"/api/v1/approvals/{approvals[0]['id']}/decision", {"decision": "approve"})
    assert post(api_client, f"/api/v1/tasks/{task['id']}/iterations")["number"] == 1


def test_commit_feedback_starts_revision_and_revokes_publish_approvals(api_client, demo_repository):
    from app import main

    task = passing_task(api_client, demo_repository)
    preview = post(api_client, f"/api/v1/tasks/{task['id']}/commits/preview", {"message": "Fix local value"})
    push_approval = approve(api_client, task, "push")
    approval = request(api_client, task, "write", preview["scope_hash"])
    feedback = "Document why this value is required"
    post(api_client, f"/api/v1/approvals/{approval['id']}/decision", {"decision": "request_changes", "reason": feedback})
    continued = post(api_client, f"/api/v1/approvals/{approval['id']}/continue")
    assert continued["status"] == "coding"
    assert continued["current_iteration"] == 2
    assert (demo_repository / "app.py").read_text() == "def value():\n    return 2\n"
    iterations = api_client.get(f"/api/v1/tasks/{task['id']}/iterations").json()["items"]
    assert feedback in iterations[-1]["goal"]
    assert iterations[-1]["test_result"] is None
    assert main.approval_service.get(push_approval["id"]).status == "cancelled"
    assert not main.approval_service.has_approved(task["id"], "write", preview["scope_hash"])
    assert not main.approval_service.has_approved(task["id"], "push")
    assert post(api_client, f"/api/v1/approvals/{approval['id']}/continue")["current_iteration"] == 2
    blocked = api_client.post(f"/api/v1/tasks/{task['id']}/commits", json={"message": "Fix local value", "scope_hash": preview["scope_hash"]})
    assert blocked.status_code == 400
    assert "approval.continue" in {item["action"] for item in api_client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]}


def test_continuation_rejects_superseded_approval_and_terminal_task(api_client):
    task = post(api_client, "/api/v1/tasks", {"goal": "Review superseded task plans"})
    approval = request(api_client, task)
    post(api_client, f"/api/v1/approvals/{approval['id']}/decision", {"decision": "request_changes", "reason": "Clarify the change"})
    replacement = request(api_client, task)
    response = api_client.post(f"/api/v1/approvals/{approval['id']}/continue")
    assert response.status_code == 409
    assert response.json()["code"] == "APPROVAL_CONTINUATION_UNAVAILABLE"
    post(api_client, f"/api/v1/approvals/{replacement['id']}/decision", {"decision": "request_changes", "reason": "Clarify the scope"})
    post(api_client, f"/api/v1/tasks/{task['id']}/cancel")
    response = api_client.post(f"/api/v1/approvals/{replacement['id']}/continue")
    assert response.json()["code"] == "INVALID_TASK_STATE"
    assert api_client.get(f"/api/v1/tasks/{task['id']}").json()["current_iteration"] == 0


def test_review_continuation_requires_current_plan_and_stopped_validation(api_client, demo_repository, monkeypatch):
    from app import main

    task = create_task(api_client, demo_repository)
    approval = request(api_client, task, "write", "b" * 64)
    post(api_client, f"/api/v1/approvals/{approval['id']}/decision", {"decision": "request_changes", "reason": "Keep changes minimal"})
    plan = main.approval_service.list(task_id=task["id"], approval_type="plan")[0]
    plan.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    response = api_client.post(f"/api/v1/approvals/{approval['id']}/continue")
    assert response.json()["code"] == "APPROVAL_REQUIRED"
    approve(api_client, task, "plan")
    monkeypatch.setattr(main.test_service, "is_running", lambda run_id: True)
    response = api_client.post(f"/api/v1/approvals/{approval['id']}/continue")
    assert response.status_code == 409
    assert response.json()["code"] == "APPROVAL_CONTINUATION_UNAVAILABLE"
    assert main.task_service.get(task["id"]).current_iteration == 1
    assert main.approval_service.get(approval["id"]).continued_at is None
    monkeypatch.setattr(main.test_service, "is_running", lambda run_id: False)
    assert post(api_client, f"/api/v1/approvals/{approval['id']}/continue")["current_iteration"] == 2


def test_feedback_continuation_survives_restart_and_requires_recovery(api_stack):
    with api_stack.restart() as client:
        task = post(client, "/api/v1/tasks", {"goal": "Persist review continuation"})
        approval = request(client, task)
        post(client, f"/api/v1/approvals/{approval['id']}/decision", {"decision": "request_changes", "reason": "Keep history across restarts"})
    with api_stack.restart() as client:
        response = client.post(f"/api/v1/approvals/{approval['id']}/continue")
        assert response.json()["code"] == "RECOVERY_CONFIRMATION_REQUIRED"
        post(client, f"/api/v1/tasks/{task['id']}/recover")
        continued = post(client, f"/api/v1/approvals/{approval['id']}/continue")
    with api_stack.restart() as client:
        history = client.get(f"/api/v1/tasks/{task['id']}/approvals").json()
        assert history["total"] == 2
        assert history["items"][1]["continued_at"]
        duplicate = post(client, f"/api/v1/approvals/{approval['id']}/continue")
        assert duplicate["goal"] == continued["goal"]
        assert duplicate["requires_recovery"] is True
    api_stack.services.store.close()
