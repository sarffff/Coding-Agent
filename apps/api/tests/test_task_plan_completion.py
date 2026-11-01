import json

import pytest
from pydantic import ValidationError

from app.approval_service import ApprovalService, plan_scope_hash
from app.config import Settings
from app.models import (
    ApprovalDecisionRequest,
    ApprovalRequestCreate,
    CompletionConfirmationRequest,
    CompletionCriterion,
    CompletionEvidence,
    TaskCreateRequest,
    TaskPlan,
    TestRunResponse as ValidationResult,
    ValidationCheck,
)
from app.repository_service import RepositoryError, RepositoryService
from app.store import StateStore
from app.task_service import TaskService, TaskStateError


HASH = "a" * 64


@pytest.fixture
def services(tmp_path):
    directory = tmp_path / "local"
    directory.mkdir()
    (directory / "app.py").write_text("value = 1\n", encoding="utf-8")
    settings = Settings(_env_file=None, workspace_root=tmp_path)
    repositories = RepositoryService(settings)
    repository = repositories.register(str(directory))
    return TaskService(repositories), repository


def plan(*step_ids):
    return TaskPlan(objective="Verify the value behavior", repository_summary="Local source",
                    steps=[{"id": step_id, "title": f"Confirm {step_id}",
                            "description": f"Check the observable result for {step_id}."} for step_id in step_ids])


def validated_task(service, repository, *, edited=False, checks=None, changed=False):
    task = service.create("Verify the value behavior", repository.id, validation_checks=checks)
    if edited:
        service.set_plan(task.id, plan("behavior", "scope"))
    iteration = service.start_iteration(task.id)
    if changed:
        service.mark_patch_applied(task.run_id, "patch", "checkpoint", ["app.py"])
    service.begin_test_run(task.run_id)
    service.record_test_result(task.run_id, ValidationResult(
        run_id=task.run_id, kind="pytest", command=["pytest"], status="passed",
        check_id=checks[0].id if checks else None, exit_code=0, duration_ms=1,
        stdout="1 passed", stderr="", validation_hash=HASH,
    ))
    return task, iteration


def evidence_payload(task, iteration):
    ids = [criterion.id for criterion in task.completion_criteria] or ["task-goal"]
    return CompletionConfirmationRequest(
        iteration_id=iteration.id, scope_hash=plan_scope_hash(task), validation_hash=HASH,
        evidence=[{"criterion_id": criterion_id, "evidence": f"Verified {criterion_id} with current tests and file review."}
                  for criterion_id in ids],
    )


def test_numbered_requirements_preserve_header_and_full_completion_text(services):
    service, repository = services
    long_requirement = ("Keep existing behavior " + "including observable boundary cases " * 5).strip()
    goal = f"Complete these requirements:\n1. {long_requirement}\n2) Verify the result"
    task = service.create(goal, repository.id)
    assert task.goal == task.plan.objective == goal
    assert [step.description for step in task.plan.steps] == [long_requirement, "Verify the result"]
    assert task.plan.steps[0].title == long_requirement[:120]
    service.set_plan(task.id, task.plan)
    assert task.completion_criteria[0].text == long_requirement
    revised = task.plan.model_copy(deep=True)
    revised.steps[0].description += " and document the findings"
    service.set_plan(task.id, revised)
    assert task.completion_criteria[0].text == revised.steps[0].description
    conditions = [item.model_copy(deep=True) for item in task.completion_criteria]
    conditions[0].text = "The user customized this requirement"
    service.set_completion_criteria(task.id, conditions)
    revised.steps[0].description += " before handoff"
    service.set_plan(task.id, revised)
    assert task.completion_criteria[0].text == conditions[0].text


def test_chinese_bulleted_requirements_are_independent_steps(services):
    service, repository = services
    task = service.create("补齐以下功能：\n- 支持空输入并显示提示\n• 保留已有文件\n* 运行完整验证", repository.id)
    assert [step.description for step in task.plan.steps] == ["支持空输入并显示提示", "保留已有文件", "运行完整验证"]
    assert [step.id for step in task.plan.steps] == ["step-1", "step-2", "step-3"]


@pytest.mark.parametrize("goal,expected", [
    ("Keep original behavior; Verify existing tests; Document results", ["Keep original behavior", "Verify existing tests", "Document results"]),
    ("保留已有行为；验证现有测试；记录结果", ["保留已有行为", "验证现有测试", "记录结果"]),
])
def test_semicolon_requirements_are_independent_steps(services, goal, expected):
    service, repository = services
    task = service.create(goal, repository.id)
    assert [step.description for step in task.plan.steps] == expected
    assert task.plan.objective == goal


@pytest.mark.parametrize("goal", ["Improve the observable behavior", "Requirements:\n1. Improve the observable behavior", "Improve the observable behavior;"])
def test_unstructured_or_single_requirement_keeps_original_template(services, goal):
    service, repository = services
    task = service.create(goal, repository.id)
    assert task.plan.objective == goal
    assert len(task.plan.steps) == 3
    assert task.plan.steps[0].title == "理解仓库上下文"


@pytest.mark.parametrize("goal,code", [
    ("\n".join(f"{index + 1}. Requirement {index + 1}" for index in range(13)), "PLAN_TOO_MANY_STEPS"),
    ("1. " + "x" * 501 + "\n2. Verify results", "PLAN_STEP_TOO_LONG"),
])
def test_invalid_explicit_requirements_do_not_create_partial_task(services, tmp_path, goal, code):
    service, repository = services
    service.store = StateStore(tmp_path / "state")
    existing = service.create("Keep existing task", repository.id)
    before = service.store.items("tasks")
    with pytest.raises(RepositoryError) as blocked:
        service.create(goal, repository.id)
    assert blocked.value.code == code
    assert [task.id for task in service.list()] == [existing.id]
    assert service.store.items("tasks") == before


def test_editable_plan_covers_every_step_and_invalidates_approval(services):
    service, repository = services
    task = service.create("Verify behavior", repository.id)
    approvals = ApprovalService()
    approval = approvals.create(task.id, task.run_id, ApprovalRequestCreate(
        type="plan", summary="Approved original plan", scope_hash=plan_scope_hash(task),
    ))
    approvals.decide(approval.id, ApprovalDecisionRequest(decision="approve"))
    edited = service.set_plan(task.id, plan("behavior", "scope"))
    assert {item.step_id for item in edited.completion_criteria} == {"behavior", "scope"}
    assert not approvals.has_approved(task.id, "plan", plan_scope_hash(edited))
    approved_scope = plan_scope_hash(edited)
    service.set_plan(task.id, plan("behavior", "scope").model_copy(update={"repository_summary": "Changed scope"}))
    assert plan_scope_hash(task) != approved_scope
    with pytest.raises(RepositoryError, match="every plan step"):
        service.set_completion_criteria(task.id, [])


def test_plan_replaces_removed_step_conditions_and_preserves_manual_conditions(services):
    service, repository = services
    task = service.create("Verify behavior", repository.id,
                          completion_criteria=[CompletionCriterion(id="manual", text="Keep unrelated data")])
    service.set_plan(task.id, plan("behavior", "scope"))
    service.set_plan(task.id, plan("behavior", "docs"))
    assert {item.step_id for item in task.completion_criteria if item.step_id} == {"behavior", "docs"}
    assert any(item.id == "manual" and item.step_id is None for item in task.completion_criteria)


def test_step_title_updates_generated_condition_and_feedback_preserves_custom_steps(services):
    service, repository = services
    task = service.create("Verify behavior", repository.id)
    service.set_plan(task.id, plan("behavior", "scope"))
    revised = plan("behavior", "scope")
    revised.steps[0].title = "Verify observable behavior"
    service.set_plan(task.id, revised)
    assert next(item.text for item in task.completion_criteria if item.step_id == "behavior") == revised.steps[0].title
    conditions = [item.model_copy(deep=True) for item in task.completion_criteria]
    conditions[0].text = "The user customized this completion condition"
    service.set_completion_criteria(task.id, conditions)
    revised.steps[0].title = "A new title"
    service.set_plan(task.id, revised)
    assert task.completion_criteria[0].text == conditions[0].text
    service.revise_plan_from_feedback(task.id, "Cover empty inputs too")
    assert [step.id for step in task.plan.steps] == ["behavior", "scope"]
    assert "Cover empty inputs too" in task.plan.objective


@pytest.mark.parametrize("criteria", [
    [CompletionCriterion(id="one", text="Wrong step", step_id="missing")],
    [CompletionCriterion(id="one", text="One"), CompletionCriterion(id="one", text="Duplicate")],
    [CompletionCriterion(id=str(index), text="Condition") for index in range(12)],
])
def test_invalid_step_conditions_do_not_partially_replace_plan(services, criteria):
    service, repository = services
    task = service.create("Verify behavior", repository.id)
    original = task.model_copy(deep=True)
    with pytest.raises(RepositoryError):
        service.set_plan(task.id, plan("behavior", "scope"), criteria)
    assert task.plan == original.plan
    assert task.completion_criteria == original.completion_criteria


def test_plan_editing_locks_after_start_and_requires_recovery(services):
    service, repository = services
    task = service.create("Verify behavior", repository.id)
    task.requires_recovery = True
    with pytest.raises(RepositoryError) as blocked:
        service.set_plan(task.id, plan("behavior"))
    assert blocked.value.code == "RECOVERY_CONFIRMATION_REQUIRED"
    service.recover(task.id)
    service.start_iteration(task.id)
    with pytest.raises(TaskStateError):
        service.set_plan(task.id, plan("behavior"))


@pytest.mark.parametrize("changes", [
    {"steps": []},
    {"steps": [{"id": "same", "title": "A", "description": "A"}] * 2},
    {"objective": " "},
    {"steps": [{"id": "bad id", "title": "A", "description": "A"}]},
    {"steps": [{"id": "valid", "title": " ", "description": "A"}]},
    {"steps": [{"id": "valid", "title": "A", "description": " "}]},
    {"steps": [{"id": str(index), "title": "A", "description": "A"} for index in range(13)]},
])
def test_plan_input_rejects_empty_or_ambiguous_steps(changes):
    with pytest.raises(ValidationError):
        TaskPlan.model_validate({**plan("behavior").model_dump(), **changes})


@pytest.mark.parametrize("edited,changed", [(False, False), (True, False), (True, True)])
def test_non_git_and_no_change_tasks_complete_only_after_explicit_evidence(services, edited, changed):
    service, repository = services
    assert not repository.has_git
    task, iteration = validated_task(service, repository, edited=edited, changed=changed)
    assert service.completion_status(task.id, HASH) == ("pending" if edited else "not_required")
    with pytest.raises(RepositoryError) as blocked:
        service.complete_local(task.id, HASH)
    assert blocked.value.code == "COMPLETION_REQUIRED"
    service.confirm_completion(task.id, evidence_payload(task, iteration), HASH)
    assert service.completion_status(task.id, HASH) == "confirmed"
    assert service.complete_local(task.id, HASH).status == "completed"
    assert iteration.changed_files == (["app.py"] if changed else [])


def test_git_task_can_finish_locally_without_commit(services, demo_repository):
    service, _ = services
    repository = service.repositories.register(str(demo_repository))
    task, iteration = validated_task(service, repository, edited=True)
    service.confirm_completion(task.id, evidence_payload(task, iteration), HASH)
    assert service.complete_local(task.id, HASH).status == "completed"


@pytest.mark.parametrize("evidence", [[], [{"criterion_id": "behavior", "evidence": "Test passes"}]])
def test_each_step_requires_evidence(services, evidence):
    service, repository = services
    task, iteration = validated_task(service, repository, edited=True)
    payload = evidence_payload(task, iteration).model_copy(update={"evidence": [
        CompletionEvidence.model_validate(item) for item in evidence
    ]})
    with pytest.raises(RepositoryError) as blocked:
        service.confirm_completion(task.id, payload, HASH)
    assert blocked.value.code == "INVALID_COMPLETION_EVIDENCE"


@pytest.mark.parametrize("change", ["hash", "plan", "evidence", "rollback", "recovery", "cancel", "paused", "blocked", "testing"])
def test_local_completion_cannot_use_stale_or_ineligible_evidence(services, change):
    service, repository = services
    task, iteration = validated_task(service, repository, edited=True, changed=True)
    service.confirm_completion(task.id, evidence_payload(task, iteration), HASH)
    current_hash = HASH
    if change == "hash":
        current_hash = "b" * 64
    elif change == "plan":
        task.plan.steps[0].description += " Additional behavior."
    elif change == "evidence":
        task.completion_confirmation.evidence.pop()
    elif change == "rollback":
        service.record_rollback(task.run_id, ["checkpoint"])
    elif change == "recovery":
        task.requires_recovery = True
    elif change == "cancel":
        service.cancel(task.id)
    elif change == "paused":
        service.begin_test_run(task.run_id)
        service.pause(task.id)
    elif change == "blocked":
        service.block_agent(task.id, "VALIDATION_REVIEW_REQUIRED", "Review check changes")
    elif change == "testing":
        service.begin_test_run(task.run_id)
    with pytest.raises(RepositoryError):
        service.complete_local(task.id, current_hash)
    assert task.status != "completed"


def test_local_completion_requires_every_current_validation_check(services):
    service, repository = services
    checks = [ValidationCheck(id="one", label="First tests"), ValidationCheck(id="two", label="Second tests")]
    task, iteration = validated_task(service, repository, checks=checks)
    assert task.status == "testing"
    with pytest.raises(RepositoryError):
        service.confirm_completion(task.id, evidence_payload(task, iteration), HASH)
    with pytest.raises(RepositoryError):
        service.complete_local(task.id, HASH)


def test_no_change_result_requires_tests_and_explicit_evidence(services):
    service, repository = services
    task = service.create("Verify existing behavior", repository.id)
    iteration = service.start_iteration(task.id)
    service.mark_no_changes(task.run_id, "The requested behavior already exists")
    assert task.status == "testing"
    assert iteration.no_changes is True
    assert iteration.test_result is None
    with pytest.raises(RepositoryError):
        service.complete_local(task.id, HASH)
    service.begin_test_run(task.run_id)
    service.record_test_result(task.run_id, ValidationResult(
        run_id=task.run_id, kind="pytest", command=["pytest"], status="passed", exit_code=0,
        duration_ms=1, stdout="1 passed", stderr="", validation_hash=HASH,
    ))
    with pytest.raises(RepositoryError) as unconfirmed:
        service.complete_local(task.id, HASH)
    assert unconfirmed.value.code == "COMPLETION_REQUIRED"
    service.confirm_completion(task.id, evidence_payload(task, iteration), HASH)
    assert service.complete_local(task.id, HASH).status == "completed"


@pytest.mark.parametrize("state", ["patch", "paused", "cancelled", "rolled_back", "recovery"])
def test_no_change_result_rejects_ineligible_iterations(services, state):
    service, repository = services
    task = service.create("Verify existing behavior", repository.id)
    iteration = service.start_iteration(task.id)
    if state == "patch":
        service.mark_patch_applied(task.run_id, "patch", "checkpoint", ["app.py"])
    elif state == "paused":
        service.pause(task.id)
    elif state == "cancelled":
        service.cancel(task.id)
    elif state == "rolled_back":
        iteration.rolled_back = True
    elif state == "recovery":
        task.requires_recovery = True
    with pytest.raises(RepositoryError):
        service.mark_no_changes(task.run_id, "No changes needed")
    assert iteration.no_changes is False


def test_local_completion_and_evidence_persist_without_restart_recovery(services, tmp_path):
    service, repository = services
    store = StateStore(tmp_path / "completed.sqlite3")
    try:
        service = TaskService(service.repositories, store)
        task, iteration = validated_task(service, repository, edited=True)
        service.confirm_completion(task.id, evidence_payload(task, iteration), HASH)
        service.complete_local(task.id, HASH)
        loaded = TaskService(service.repositories, store).get(task.id)
        assert loaded.status == "completed"
        assert loaded.requires_recovery is False
        assert loaded.completion_confirmation == task.completion_confirmation
        assert {item.criterion_id for item in loaded.completion_confirmation.evidence} == {"behavior", "scope"}
    finally:
        store.close()


def test_plan_progress_attributes_only_matching_files_across_iterations(services):
    service, repository = services
    task = service.create("Verify behavior", repository.id)
    scoped = plan("behavior", "docs", "review")
    scoped.steps[0].files = ["src/"]
    scoped.steps[1].files = ["docs/readme.md"]
    service.set_plan(task.id, scoped)
    first = service.start_iteration(task.id)
    service.mark_patch_applied(task.run_id, "first", "first-checkpoint", ["src/app.py", "src_other/app.py"])
    failure = ValidationResult(run_id=task.run_id, kind="pytest", command=["pytest"], status="failed", exit_code=1,
                               duration_ms=1, stdout="", stderr="Failed", validation_hash=HASH)
    service.record_test_result(task.run_id, failure)
    second = service.start_repair(task.id)
    service.mark_patch_applied(task.run_id, "second", "second-checkpoint", ["docs/readme.md"])
    service.record_test_result(task.run_id, failure.model_copy(update={"status": "passed", "exit_code": 0, "stderr": ""}))
    progress = service.plan_progress(task.id, HASH)
    assert [item.status for item in progress] == ["validated", "validated", "pending"]
    assert progress[0].changed_files == ["src/app.py"]
    assert progress[0].iteration_ids == [first.id]
    assert progress[1].changed_files == ["docs/readme.md"]
    assert progress[1].iteration_ids == [second.id]
    assert progress[2].changed_files == progress[2].iteration_ids == []


def test_plan_progress_requires_current_validation_and_explicit_step_evidence(services):
    service, repository = services
    task, iteration = validated_task(service, repository, edited=True, changed=True)
    task.plan.steps[0].files = ["app.py"]
    before = service.plan_progress(task.id, HASH)
    assert [item.status for item in before] == ["validated", "pending"]
    service.confirm_completion(task.id, evidence_payload(task, iteration), HASH)
    confirmed = service.plan_progress(task.id, HASH)
    assert [item.status for item in confirmed] == ["confirmed", "confirmed"]
    assert [item.criterion_id for item in confirmed[0].evidence] == ["behavior"]
    assert [item.criterion_id for item in confirmed[1].evidence] == ["scope"]
    assert confirmed[1].changed_files == confirmed[1].iteration_ids == []
    stale = service.plan_progress(task.id, "b" * 64)
    assert [item.status for item in stale] == ["changed", "pending"]
    assert all(not item.evidence for item in stale)


def test_plan_progress_excludes_rolled_back_iterations_and_their_confirmation(services):
    service, repository = services
    task, iteration = validated_task(service, repository, edited=True, changed=True)
    task.plan.steps[0].files = ["app.py"]
    service.confirm_completion(task.id, evidence_payload(task, iteration), HASH)
    service.record_rollback(task.run_id, ["checkpoint"])
    progress = service.plan_progress(task.id, HASH)
    assert all(item.status == "pending" and not item.changed_files and not item.iteration_ids and not item.evidence for item in progress)


def test_bound_workspace_and_step_evidence_persist_and_legacy_tasks_load(services, tmp_path):
    service, source = services
    directory = tmp_path / "isolated"
    directory.mkdir()
    (directory / "app.py").write_text("value = 1\n", encoding="utf-8")
    repository = service.repositories.register(str(directory))
    store = StateStore(tmp_path / "tasks.sqlite3")
    try:
        service = TaskService(service.repositories, store)
        task = service.create("Verify behavior", source.id)
        service.set_plan(task.id, plan("behavior", "scope"))
        service.bind_workspace(task.id, repository.id, source.id, str(directory), "local")
        restored = TaskService(service.repositories, store)
        loaded = restored.get(task.id)
        assert loaded.repository_id == repository.id
        assert loaded.source_repository_id == source.id
        assert loaded.workspace_path == str(directory)
        assert loaded.workspace_kind == "local"
        assert loaded.plan == task.plan
        assert loaded.completion_criteria == task.completion_criteria
        restored.recover(task.id)
        restored.start_iteration(task.id)
        with pytest.raises(TaskStateError):
            restored.bind_workspace(task.id, source.id, source.id, str(directory), "local")
        legacy = json.loads(TaskService._stored(service._record(task.id)).model_dump_json())
        for name in ("source_repository_id", "workspace_path", "workspace_kind"):
            legacy["summary"].pop(name)
        legacy["summary"]["completion_criteria"] = []
        store.put("tasks", service._keys[task.id], json.dumps(legacy), task.updated_at.isoformat())
        legacy_task = TaskService(service.repositories, store).get(task.id)
        assert legacy_task.source_repository_id is None
        assert legacy_task.workspace_path is None
        assert legacy_task.workspace_kind is None
    finally:
        store.close()


def test_isolated_workspace_request_defaults_to_existing_behavior():
    assert TaskCreateRequest(goal="Verify behavior").isolated_workspace is False
    assert TaskCreateRequest(goal="Verify behavior", isolated_workspace=True).isolated_workspace is True
