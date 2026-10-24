import pytest

from app import main
from app.approval_service import plan_scope_hash
from app.models import TaskSummary, TestRunResponse as ValidationResult
from app.repository_service import RepositoryError
from test_api_workflow import apply_value, approve, post


CRITERIA = [
    {"id": "value", "text": "Return 2 from value()."},
    {"id": "scope", "text": "Keep unrelated notes unchanged."},
]
EVIDENCE = [
    {"criterion_id": "value", "evidence": "app.py returns 2; test_value passes."},
    {"criterion_id": "scope", "evidence": "Reviewed the diff: only app.py changes."},
]
CHECKS = [{"id": "api", "label": "API tests", "kind": "pytest", "target": "test_app.py"}]


def create_completion_task(client, directory, criteria=CRITERIA):
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    return post(client, "/api/v1/tasks", {
        "repository_id": repository["id"], "goal": "Fix value and preserve unrelated notes",
        "validation_checks": CHECKS, "completion_criteria": criteria,
    })


def prepare_completion_task(client, directory, *, passed=True):
    task = create_completion_task(client, directory)
    approve(client, task, "plan")
    post(client, f"/api/v1/tasks/{task['id']}/branch", {"name": "codex/" + task["id"]})
    post(client, f"/api/v1/tasks/{task['id']}/iterations")
    apply_value(client, task, 2)
    if passed:
        record_validation(task)
    return task


def record_validation(task, status="passed", check_id="api"):
    summary = main.task_service.get(task["id"])
    main.task_service.begin_test_run(task["run_id"])
    main.task_service.record_test_result(task["run_id"], ValidationResult(
        run_id=task["run_id"], kind="pytest", command=["pytest", "test_app.py"],
        check_id=check_id, status=status, exit_code=0 if status == "passed" else 1,
        duration_ms=1, stdout="1 passed" if status == "passed" else "", stderr="",
        validation_hash=main.agent_service.validation_hash(summary),
    ))


def confirmation_payload(task):
    summary = main.task_service.get(task["id"])
    return {
        "iteration_id": main.task_service.list_iterations(task["id"])[-1].id,
        "scope_hash": plan_scope_hash(summary),
        "validation_hash": main.agent_service.validation_hash(summary),
        "evidence": EVIDENCE,
    }


def confirm(client, task, payload=None):
    return client.post(f"/api/v1/tasks/{task['id']}/completion-confirmation", json=payload or confirmation_payload(task))


def test_criteria_are_saved_and_bound_to_the_original_task_goal(api_client, demo_repository):
    task = create_completion_task(api_client, demo_repository)
    assert task["completion_criteria"] == CRITERIA
    assert task["completion_confirmation"] is None
    summary = main.task_service.get(task["id"])
    original_scope = plan_scope_hash(summary)
    summary.completion_criteria[0].text = "Return 3 from value()."
    assert plan_scope_hash(summary) != original_scope
    summary.completion_criteria[0].text = CRITERIA[0]["text"]
    summary.goal += " and document the change"
    assert plan_scope_hash(summary) != original_scope


@pytest.mark.parametrize("criteria", [
    [CRITERIA[0], CRITERIA[0]],
    [{"id": "value", "text": "   "}],
    [{"id": "value", "text": "x" * 501}],
    [{"id": str(index), "text": "A condition"} for index in range(13)],
])
def test_invalid_criteria_are_rejected(api_client, demo_repository, criteria):
    repository = post(api_client, "/api/v1/repositories", {"path": str(demo_repository)})
    response = api_client.post("/api/v1/tasks", json={
        "repository_id": repository["id"], "goal": "Fix value", "completion_criteria": criteria,
    })
    assert response.status_code == 422


def test_criteria_changes_require_new_plan_approval_and_lock_after_start(api_client, demo_repository):
    task = create_completion_task(api_client, demo_repository, criteria=[])
    approve(api_client, task, "plan")
    endpoint = f"/api/v1/tasks/{task['id']}/completion-criteria"
    updated = api_client.put(endpoint, json={"criteria": CRITERIA})
    assert updated.status_code == 200, updated.text
    assert updated.json()["completion_criteria"] == CRITERIA
    blocked = api_client.post(f"/api/v1/tasks/{task['id']}/iterations", json={})
    assert blocked.json()["code"] == "APPROVAL_REQUIRED"
    approve(api_client, task, "plan")
    post(api_client, f"/api/v1/tasks/{task['id']}/branch", {"name": "codex/" + task["id"]})
    post(api_client, f"/api/v1/tasks/{task['id']}/iterations")
    locked = api_client.put(endpoint, json={"criteria": []})
    assert locked.status_code == 400
    assert locked.json()["code"] == "INVALID_TASK_STATE"


@pytest.mark.parametrize("field", ["iteration_id", "scope_hash", "validation_hash"])
def test_confirmation_requires_explicit_snapshot_fields(api_client, demo_repository, field):
    task = prepare_completion_task(api_client, demo_repository)
    payload = confirmation_payload(task)
    payload.pop(field)
    response = confirm(api_client, task, payload)
    assert response.status_code == 422
    assert main.task_service.get(task["id"]).completion_confirmation is None


@pytest.mark.parametrize("field,value", [
    ("iteration_id", "iteration-old"), ("scope_hash", "0" * 64), ("validation_hash", "0" * 64),
])
def test_stale_confirmation_is_not_rebound_to_current_state(api_client, demo_repository, field, value):
    task = prepare_completion_task(api_client, demo_repository)
    payload = {**confirmation_payload(task), field: value}
    response = confirm(api_client, task, payload)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "COMPLETION_CONFIRMATION_STALE"
    assert main.task_service.get(task["id"]).completion_confirmation is None


def test_confirmation_requires_current_passing_validation(api_client, demo_repository):
    task = prepare_completion_task(api_client, demo_repository, passed=False)
    response = confirm(api_client, task)
    assert response.status_code == 409
    assert response.json()["code"] == "COMPLETION_VALIDATION_REQUIRED"


@pytest.mark.parametrize("evidence", [
    [], EVIDENCE[:1], EVIDENCE + [EVIDENCE[0]],
    [EVIDENCE[0], {"criterion_id": "other", "evidence": "An unrelated check passes."}],
])
def test_evidence_must_cover_every_criterion_exactly_once(api_client, demo_repository, evidence):
    task = prepare_completion_task(api_client, demo_repository)
    response = confirm(api_client, task, {**confirmation_payload(task), "evidence": evidence})
    assert response.status_code in {400, 422}
    assert main.task_service.get(task["id"]).completion_confirmation is None


def test_whitespace_evidence_is_rejected(api_client, demo_repository):
    task = prepare_completion_task(api_client, demo_repository)
    response = confirm(api_client, task, {
        **confirmation_payload(task), "evidence": [EVIDENCE[0], {"criterion_id": "scope", "evidence": "  "}],
    })
    assert response.status_code == 422


def test_green_validation_cannot_commit_until_completion_is_confirmed(api_client, demo_repository):
    task = prepare_completion_task(api_client, demo_repository, passed=False)
    validation = post(api_client, f"/api/v1/runs/{task['run_id']}/tests", {"check_id": "api"})
    assert validation["status"] == "passed", validation
    endpoint = f"/api/v1/tasks/{task['id']}/commits"
    preview = post(api_client, endpoint + "/preview", {"message": "Fix value"})
    assert preview["changed_files"] == ["app.py"]
    assert preview["ready"] is False
    approve(api_client, task, "write", preview["scope_hash"])
    blocked = api_client.post(endpoint, json={"message": "Fix value", "scope_hash": preview["scope_hash"]})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "COMPLETION_REQUIRED"
    payload = confirmation_payload(task)
    response = confirm(api_client, task, {**payload, "confirmed_by": "agent-loop"})
    assert response.status_code == 200, response.text
    summary = main.task_service.get(task["id"])
    saved = summary.completion_confirmation
    assert saved.iteration_id == payload["iteration_id"]
    assert saved.scope_hash == payload["scope_hash"]
    assert saved.validation_hash == payload["validation_hash"]
    assert saved.confirmed_by == "local-user"
    assert [item.model_dump() for item in saved.evidence] == EVIDENCE
    assert main.task_service.completion_status(task["id"], payload["validation_hash"]) == "confirmed"
    assert post(api_client, endpoint + "/preview", {"message": "Fix value"})["ready"] is True
    post(api_client, endpoint, {"message": "Fix value", "scope_hash": preview["scope_hash"]})
    assert main.task_service.completion_status(task["id"], main.agent_service.validation_hash(summary)) == "confirmed"


def test_changed_code_or_goal_invalidates_completion(api_client, demo_repository):
    task = prepare_completion_task(api_client, demo_repository)
    payload = confirmation_payload(task)
    assert confirm(api_client, task, payload).status_code == 200
    summary = main.task_service.get(task["id"])
    summary.goal += " and update the documentation"
    assert main.task_service.completion_status(task["id"], payload["validation_hash"]) == "stale"
    summary.goal = task["goal"]
    (demo_repository / "app.py").write_text("def value():\n    return 2  # changed after confirmation\n", encoding="utf-8")
    current_hash = main.agent_service.validation_hash(summary)
    assert main.task_service.completion_status(task["id"], current_hash) == "stale"
    with pytest.raises(RepositoryError, match="completion"):
        main.task_service.assert_completion_ready(task["id"], current_hash)
    response = confirm(api_client, task, payload)
    assert response.status_code == 409
    assert response.json()["code"] == "COMPLETION_CONFIRMATION_STALE"


@pytest.mark.parametrize("check_id", ["api", None])
def test_failed_rerun_invalidates_confirmation_even_with_the_same_hash(api_client, demo_repository, check_id):
    task = prepare_completion_task(api_client, demo_repository)
    payload = confirmation_payload(task)
    assert confirm(api_client, task, payload).status_code == 200
    record_validation(task, "failed", check_id)
    assert main.task_service.completion_status(task["id"], payload["validation_hash"]) == "stale"


def test_confirmation_refuses_busy_workspace_and_changed_validation_inputs(api_client, demo_repository):
    task = prepare_completion_task(api_client, demo_repository)
    owner = main.agent_service.workspaces.acquire(demo_repository, "other-task")
    try:
        response = confirm(api_client, task)
        assert response.status_code == 409
        assert response.json()["code"] == "WORKSPACE_BUSY"
    finally:
        main.agent_service.workspaces.release(owner)
    (demo_repository / "test_app.py").write_text("def test_empty():\n    pass\n", encoding="utf-8")
    response = confirm(api_client, task)
    assert response.status_code == 409
    assert response.json()["code"] == "VALIDATION_REVIEW_REQUIRED"


def test_legacy_empty_criteria_require_no_completion_confirmation(api_client, demo_repository):
    task = create_completion_task(api_client, demo_repository, criteria=[])
    assert main.task_service.completion_status(task["id"], None) == "not_required"
    main.task_service.assert_completion_ready(task["id"], None)
    legacy = {key: value for key, value in task.items() if not key.startswith("completion_")}
    parsed = TaskSummary.model_validate(legacy)
    assert parsed.completion_criteria == []
    assert parsed.completion_confirmation is None


def test_completion_confirmation_survives_task_storage_restart(api_stack, demo_repository):
    client = api_stack.restart()
    task = prepare_completion_task(client, demo_repository)
    payload = confirmation_payload(task)
    assert confirm(client, task, payload).status_code == 200
    restarted = api_stack.restart()
    restored = restarted.get(f"/api/v1/tasks/{task['id']}").json()
    assert restored["completion_criteria"] == CRITERIA
    assert restored["completion_confirmation"]["validation_hash"] == payload["validation_hash"]
    assert restored["completion_confirmation"]["evidence"] == EVIDENCE
    assert main.task_service.completion_status(task["id"], payload["validation_hash"]) == "confirmed"
