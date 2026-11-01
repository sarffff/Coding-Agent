from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from .approval_service import plan_scope_hash
from .config import Settings
from .models import (
    MAX_MESSAGE_CHARS,
    AgentMode,
    CompletionConfirmation,
    CompletionConfirmationRequest,
    CompletionCriterion,
    CompletionStatus,
    PlanStepProgress,
    TaskBudget,
    TaskCheckpoint,
    TaskIteration,
    TaskPlan,
    TaskStatus,
    TaskSummary,
    TaskTrack,
    TestRunResponse,
    TestRunRequest,
    ValidationCheck,
    ValidationCheckStatus,
)
from .repository_service import RepositoryError, RepositoryRecord, RepositoryService
from .retention import prune_test_outputs
from .store import StateStore
from .validation_guard import capture_validation_guard


class TaskStateError(RepositoryError):
    def __init__(self, message: str, details: dict[str, object] | None = None):
        super().__init__("INVALID_TASK_STATE", message, details)


@dataclass(slots=True)
class TaskRecord:
    summary: TaskSummary
    iterations: list[TaskIteration] = field(default_factory=list)
    checkpoints: list[TaskCheckpoint] = field(default_factory=list)
    validation_guard: dict[str, str] | None = None
    validation_baseline: list[TestRunResponse] = field(default_factory=list)
    validation_baseline_hash: str | None = None
    validation_review_patch_id: str | None = None
    validation_runners: dict[str, str] = field(default_factory=dict)


class PersistedTask(BaseModel):
    """Storage shape for one task: its summary plus the iterations and checkpoints it owns."""

    summary: TaskSummary
    iterations: list[TaskIteration] = []
    checkpoints: list[TaskCheckpoint] = []
    validation_guard: dict[str, str] | None = None
    validation_baseline: list[TestRunResponse] = []
    validation_baseline_hash: str | None = None
    validation_review_patch_id: str | None = None
    validation_runners: dict[str, str] = {}


class TaskService:
    """Task lifecycle and iteration state for the Phase 2 coding loop."""

    ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
        "queued": {"planning", "cancelled"},
        "planning": {"awaiting_approval", "coding", "failed", "cancelled"},
        "review": {"coding", "cancelled"},
        "awaiting_approval": {"coding", "cancelled"},
        "coding": {"testing", "paused", "failed", "cancelled"},
        "testing": {"ready_for_pr", "repairing", "failed", "paused", "cancelled"},
        "repairing": {"coding", "failed", "paused", "cancelled"},
        "ready_for_pr": {"testing", "awaiting_approval", "pr_created", "completed", "cancelled"},
        "pr_created": {"completed", "failed", "cancelled"},
        "completed": set(),
        "done": set(),
        "failed": {"testing", "repairing", "coding", "paused", "cancelled"},
        "paused": {"coding", "testing", "repairing", "failed", "running", "cancelled"},
        "cancelled": set(),
        "running": {"testing", "paused", "failed", "cancelled"},
    }

    IN_FLIGHT_STATES: set[TaskStatus] = {"queued", "planning", "running", "coding", "testing", "repairing", "awaiting_approval", "ready_for_pr"}
    # The clock stops here: review, pause and merge wait are the human's time, not the agent's.
    HUMAN_WAIT_STATES: set[TaskStatus] = {"awaiting_approval", "ready_for_pr", "paused", "pr_created"}

    def __init__(self, repositories: RepositoryService, store: StateStore | None = None, settings: Settings | None = None):
        self.repositories = repositories
        self.store = store
        self.settings = settings or repositories.settings
        self._budget_lock = RLock()
        self._tasks: dict[str, TaskRecord] = {}
        self._keys: dict[str, str] = {}
        if store:
            for key, stored in store.models("tasks", PersistedTask):
                record = TaskRecord(summary=stored.summary, iterations=stored.iterations, checkpoints=stored.checkpoints,
                                    validation_guard=stored.validation_guard, validation_baseline=stored.validation_baseline,
                                    validation_baseline_hash=stored.validation_baseline_hash,
                                    validation_review_patch_id=stored.validation_review_patch_id,
                                    validation_runners=stored.validation_runners)
                if "budget" not in stored.summary.model_fields_set:
                    record.summary.budget = self._new_budget()
                    if record.iterations:
                        record.summary.budget.started_at = record.iterations[0].created_at
                    for iteration in record.iterations:
                        for result in iteration.test_runs:
                            if result.status == "passed":
                                record.summary.budget.consecutive_failures = 0
                            elif result.status != "cancelled":
                                record.summary.budget.consecutive_failures += 1
                if record.summary.status in self.IN_FLIGHT_STATES:
                    record.summary.requires_recovery = True
                    budget = record.summary.budget
                    if budget.started_at and budget.waiting_since is None:
                        # The API was not working after the last persisted activity.
                        budget.waiting_since = max(record.summary.updated_at, budget.started_at + timedelta(seconds=budget.elapsed_seconds))
                    record.summary.next_action = "服务已重启。请确认要继续该任务，或选择回滚与取消。"
                    store.put("tasks", key, self._stored(record), record.summary.updated_at.isoformat())
                self._tasks[record.summary.id] = record
                self._keys[record.summary.id] = key
                self._refresh_budget(record)
                self._persist(record)

    def _persist(self, record: TaskRecord) -> None:
        prune_test_outputs(record.iterations, self.settings)
        if not self.store:
            return
        task_id = record.summary.id
        key = self._keys.setdefault(task_id, self.store.next_key("tasks"))
        self.store.put(
            "tasks",
            key,
            self._stored(record),
            record.summary.updated_at.isoformat(),
        )

    @staticmethod
    def _stored(record: TaskRecord) -> PersistedTask:
        return PersistedTask(summary=record.summary, iterations=record.iterations, checkpoints=record.checkpoints,
                             validation_guard=record.validation_guard, validation_baseline=record.validation_baseline,
                             validation_baseline_hash=record.validation_baseline_hash,
                             validation_review_patch_id=record.validation_review_patch_id,
                             validation_runners=record.validation_runners)

    def prepare_validation_guard(self, task_id: str) -> dict[str, str]:
        record = self._record(task_id)
        if record.validation_guard is None:
            repository = self.repositories.get(record.summary.repository_id)
            record.validation_guard = capture_validation_guard(repository, record.summary.validation_checks,
                                                               max_entries=self.settings.max_tree_entries)
            self._persist(record)
        return record.validation_guard

    def replace_validation_guard(self, task_id: str, paths: list[str] | None = None) -> None:
        record = self._record(task_id)
        repository = self.repositories.get(record.summary.repository_id)
        captured = capture_validation_guard(repository, record.summary.validation_checks,
                                            max_entries=self.settings.max_tree_entries)
        if paths is not None:
            restored = set(paths)
            captured = {**{key: value for key, value in (record.validation_guard or {}).items()
                           if key.split(":", 2)[1] not in restored},
                        **{key: value for key, value in captured.items() if key.split(":", 2)[1] in restored}}
        record.validation_guard = captured
        # A human-approved change to the checks starts a new comparison baseline.
        record.validation_baseline = []
        record.validation_baseline_hash = None
        self._persist(record)

    def validation_baseline(self, task_id: str) -> list[TestRunResponse]:
        return self._record(task_id).validation_baseline

    def validation_baseline_hash(self, task_id: str) -> str | None:
        return self._record(task_id).validation_baseline_hash

    @staticmethod
    def _validation_runner_key(request: TestRunRequest | TestRunResponse) -> str:
        return json.dumps([request.check_id, request.workdir, request.target, request.script])

    def validation_runner(self, task_id: str, request: TestRunRequest) -> str | None:
        return self._record(task_id).validation_runners.get(self._validation_runner_key(request))

    def record_validation_baseline(self, task_id: str, results: list[TestRunResponse], input_hash: str | None) -> None:
        record = self._record(task_id)
        record.validation_baseline = [item.model_copy(deep=True) for item in results]
        for item in record.validation_baseline:
            item.stdout, item.stderr = item.stdout[-4_000:], item.stderr[-4_000:]
        record.validation_baseline_hash = input_hash
        if input_hash:
            record.validation_runners.update({self._validation_runner_key(item): item.kind for item in results
                                              if item.kind in {"pytest", "frontend"}})
        self._persist(record)

    def validation_review_patch_id(self, task_id: str) -> str | None:
        return self._record(task_id).validation_review_patch_id

    def block_agent(self, task_id: str, code: str, message: str, details: dict | None = None) -> None:
        record = self._record(task_id)
        if record.summary.status in {"paused", "cancelled", "completed", "done"}:
            return
        record.summary.agent_blocked_reason = code
        record.validation_review_patch_id = (details or {}).get("patch_id") if code == "VALIDATION_REVIEW_REQUIRED" else None
        record.summary.error = message
        record.summary.next_action = message + " Review the files and retry, or restore a checkpoint."
        record.summary.updated_at = datetime.now(timezone.utc)
        self._persist(record)

    def clear_agent_block(self, task_id: str) -> None:
        record = self._record(task_id)
        if record.summary.agent_blocked_reason:
            record.summary.agent_blocked_reason = None
            record.summary.error = None
            record.validation_review_patch_id = None
            self._persist(record)

    def _new_budget(self) -> TaskBudget:
        return TaskBudget(
            max_iterations=self.settings.task_max_iterations,
            max_wall_time_seconds=self.settings.task_max_wall_time_seconds,
            max_remote_calls=self.settings.task_max_remote_calls,
            max_consecutive_failures=self.settings.task_max_consecutive_failures,
        )

    def _update_budget_wait(self, record: TaskRecord, waiting: bool, now: datetime) -> None:
        budget = record.summary.budget
        if not budget.started_at:
            return
        if waiting and budget.waiting_since is None:
            budget.waiting_since = now
        elif not waiting and budget.waiting_since is not None:
            waited = max(0, (now - budget.waiting_since).total_seconds())
            budget.started_at += timedelta(seconds=waited)
            budget.waiting_since = None

    def _refresh_budget(self, record: TaskRecord, *, new_iteration: bool = False) -> TaskBudget:
        budget = record.summary.budget
        if record.summary.track == "cowork":
            # Chat is bounded per request, not by the lifetime of a conversation.
            budget.started_at = budget.waiting_since = None
            budget.elapsed_seconds = 0
            budget.exhausted_reason = None
            return budget
        budget.iterations = len(record.iterations)
        if budget.started_at:
            if (record.summary.status in self.HUMAN_WAIT_STATES or record.summary.requires_recovery) and budget.waiting_since is None:
                # A task loaded mid-review (or parked by an older build) starts the pause here.
                budget.waiting_since = datetime.now(timezone.utc)
            if budget.waiting_since is not None:
                end = budget.waiting_since
            elif record.summary.status in {"completed", "done", "cancelled"}:
                end = record.summary.updated_at
            else:
                end = datetime.now(timezone.utc)
            budget.elapsed_seconds = round(max(0, (end - budget.started_at).total_seconds()), 3)
        if budget.exhausted_reason is None:
            if budget.elapsed_seconds >= budget.max_wall_time_seconds:
                budget.exhausted_reason = "wall_time"
            elif budget.consecutive_failures >= budget.max_consecutive_failures:
                budget.exhausted_reason = "consecutive_failures"
            elif budget.iterations >= budget.max_iterations and (
                new_iteration or record.summary.status in {"failed", "repairing"}
                or bool(record.iterations and record.iterations[-1].rolled_back)
            ):
                budget.exhausted_reason = "iterations"
        return budget

    def get_budget(self, task_id: str) -> TaskBudget:
        record = self._record(task_id)
        previous_reason = record.summary.budget.exhausted_reason
        budget = self._refresh_budget(record)
        if previous_reason != budget.exhausted_reason:
            self._persist(record)
        return budget

    def assert_budget_available(self, task_id: str, *, new_iteration: bool = False) -> None:
        record = self._record(task_id)
        budget = self._refresh_budget(record, new_iteration=new_iteration)
        if budget.exhausted_reason:
            self._persist(record)
            raise RepositoryError(
                "TASK_BUDGET_EXHAUSTED", "The task budget is exhausted. Review or cancel this task before starting a new one.",
                {"task_id": task_id, "reason": budget.exhausted_reason, "budget": budget.model_dump(mode="json")},
            )

    def consume_remote_call(self, task_id: str) -> None:
        with self._budget_lock:
            self.assert_budget_available(task_id)
            record = self._record(task_id)
            if record.summary.track != "cowork":
                if record.summary.budget.remote_calls >= record.summary.budget.max_remote_calls:
                    record.summary.budget.exhausted_reason = "remote_calls"
                    self.assert_budget_available(task_id)
                record.summary.budget.started_at = record.summary.budget.started_at or datetime.now(timezone.utc)
            record.summary.budget.remote_calls += 1
            self._persist(record)

    def record_remote_result(self, task_id: str, succeeded: bool) -> None:
        with self._budget_lock:
            record = self._record(task_id)
            budget = record.summary.budget
            # For code, only a passing test proves the repair worked; chat retries reset on success.
            if not succeeded:
                budget.consecutive_failures += 1
            elif record.summary.track == "cowork":
                budget.consecutive_failures = 0
            self._refresh_budget(record)
            self._persist(record)

    def assert_recovered(self, task_id: str) -> None:
        summary = self._record(task_id).summary
        if summary.requires_recovery:
            raise RepositoryError(
                "RECOVERY_CONFIRMATION_REQUIRED",
                "The task was in flight when the API restarted. Confirm recovery before any further write.",
                {"task_id": task_id, "status": summary.status},
            )

    def recover(self, task_id: str) -> TaskSummary:
        record = self._record(task_id)
        if not record.summary.requires_recovery:
            raise TaskStateError("Only tasks restored from a restart need recovery.", {"status": record.summary.status})
        record.summary.requires_recovery = False
        record.summary.updated_at = datetime.now(timezone.utc)
        self._update_budget_wait(record, record.summary.status in self.HUMAN_WAIT_STATES, record.summary.updated_at)
        record.summary.next_action = "Continue the task from the last checkpoint."
        self._persist(record)
        return record.summary

    def transition(self, task_id: str, target: TaskStatus, next_action: str | None = None) -> TaskSummary:
        """Move a task to `target` through the same state machine the loop uses."""
        record = self._record(task_id)
        self._transition(record, target)
        if next_action is not None:
            record.summary.next_action = next_action
        self._persist(record)
        return record.summary

    def create(self, goal: str, repository_id: str | None = None, mode: AgentMode = "standard", track: TaskTrack = "code", validation_checks: list[ValidationCheck] | None = None, completion_criteria: list[CompletionCriterion] | None = None) -> TaskSummary:
        repository: RepositoryRecord | None = None
        if repository_id:
            repository = self.repositories.get(repository_id)
        if validation_checks:
            if track != "code" or not repository:
                raise TaskStateError("Validation checks require a coding task with a repository.")
            self._validate_check_paths(repository, validation_checks)
        if completion_criteria and track != "code":
            raise TaskStateError("Completion criteria require a coding task.")
        now = datetime.now(timezone.utc)
        task_id = f"task-{uuid4().hex[:10]}"
        run_id = f"run-{uuid4().hex[:10]}"
        if track == "cowork":
            # Cowork is a plain chat: no plan, no coding loop, no git. "review" keeps it out of
            # the in-flight set so a restart never flags it for recovery.
            summary = TaskSummary(
                id=task_id, run_id=run_id, goal=goal.strip(), repository_id=repository_id,
                status="review", plan=None, created_at=now, updated_at=now,
                next_action=None, budget=self._new_budget(), mode=mode, track="cowork",
            )
        else:
            summary = TaskSummary(
                id=task_id, run_id=run_id, goal=goal.strip(), repository_id=repository_id,
                status="awaiting_approval", plan=self._build_plan(goal, repository), created_at=now, updated_at=now,
                next_action="Review and approve the task plan before the first coding iteration.",
                budget=self._new_budget(), mode=mode, track="code",
                validation_checks=validation_checks or [],
                completion_criteria=completion_criteria or [],
            )
            self._validate_completion_steps(summary.plan, summary.completion_criteria)
        self._tasks[task_id] = TaskRecord(summary=summary)
        self._persist(self._tasks[task_id])
        return summary

    def list(self) -> list[TaskSummary]:
        return [self.get(record.summary.id) for record in reversed(list(self._tasks.values()))]

    def get(self, task_id: str) -> TaskSummary:
        self.get_budget(task_id)
        return self._record(task_id).summary

    def get_by_run(self, run_id: str) -> TaskSummary:
        return self.get(self._record_by_run(run_id).summary.id)

    def update_git_snapshot(self, task_id: str, branch: str, head: str, changed_files: list[str]) -> TaskSummary:
        record = self._record(task_id)
        record.summary.branch = branch
        record.summary.head = head
        record.summary.dirty_files = changed_files
        record.summary.base_branch = record.summary.base_branch or branch
        record.summary.updated_at = datetime.now(timezone.utc)
        self._persist(record)
        return record.summary

    def bind_workspace(self, task_id: str, repository_id: str, source_repository_id: str,
                       workspace_path: str, workspace_kind: Literal["git", "local"]) -> TaskSummary:
        self.assert_recovered(task_id)
        record = self._record(task_id)
        if record.summary.track != "code" or record.iterations or record.summary.status != "awaiting_approval":
            raise TaskStateError("Bind an isolated workspace before the first coding iteration.")
        repository = self.repositories.get(repository_id)
        self.repositories.get(source_repository_id)
        self._validate_check_paths(repository, record.summary.validation_checks)
        record.summary.repository_id = repository_id
        record.summary.source_repository_id = source_repository_id
        record.summary.workspace_path = workspace_path
        record.summary.workspace_kind = workspace_kind
        if record.summary.plan:
            record.summary.plan.repository_summary = f"{repository.name} · 可恢复编码循环"
        record.validation_guard = None
        record.validation_baseline = []
        record.validation_baseline_hash = None
        record.validation_runners = {}
        record.summary.completion_confirmation = None
        record.summary.updated_at = datetime.now(timezone.utc)
        self._persist(record)
        return record.summary

    def set_mode(self, task_id: str, mode: AgentMode) -> TaskSummary:
        record = self._record(task_id)
        record.summary.mode = mode
        record.summary.updated_at = datetime.now(timezone.utc)
        self._persist(record)
        return record.summary

    def rename(self, task_id: str, name: str) -> TaskSummary:
        record = self._record(task_id)
        if record.summary.display_name != name:
            record.summary.display_name = name
            record.summary.updated_at = datetime.now(timezone.utc)
            self._persist(record)
        return record.summary

    def changed_files(self, task_id: str) -> list[str]:
        return sorted({name for iteration in self._record(task_id).iterations if not iteration.rolled_back for name in iteration.changed_files})

    def set_validation_checks(self, task_id: str, checks: list[ValidationCheck]) -> TaskSummary:
        self.assert_recovered(task_id)
        record = self._record(task_id)
        if record.summary.track != "code" or record.iterations or record.summary.status != "awaiting_approval":
            raise TaskStateError("Configure validation checks before the first coding iteration.")
        if not record.summary.repository_id:
            raise TaskStateError("Validation checks require a repository.")
        self._validate_check_paths(self.repositories.get(record.summary.repository_id), checks)
        record.summary.validation_checks = checks
        record.validation_guard = None
        record.validation_baseline = []
        record.validation_baseline_hash = None
        record.validation_runners = {}
        record.summary.updated_at = datetime.now(timezone.utc)
        record.summary.next_action = "Review and approve the plan and required validation checks."
        self._persist(record)
        return record.summary

    def _validate_check_paths(self, repository: RepositoryRecord, checks: list[ValidationCheck]) -> None:
        for check in checks:
            directory = self.repositories._safe_repo_path(repository, check.workdir)
            if not directory.is_dir():
                raise RepositoryError("INVALID_TEST_TARGET", "Validation working directory must exist inside the repository.")
            if check.target:
                target = self.repositories._safe_repo_path(repository, check.workdir + "/" + check.target.split("::", 1)[0])
                if not target.is_relative_to(directory):
                    raise RepositoryError("INVALID_TEST_TARGET", "The test target must stay inside its working directory.")

    def set_plan(self, task_id: str, plan: TaskPlan, criteria: list[CompletionCriterion] | None = None) -> TaskSummary:
        self.assert_recovered(task_id)
        record = self._record(task_id)
        if record.summary.track != "code" or record.iterations or record.summary.status != "awaiting_approval":
            raise TaskStateError("Configure the task plan before the first coding iteration.")
        step_ids = {step.id for step in plan.steps}
        conditions = list(criteria) if criteria is not None else [
            criterion for criterion in record.summary.completion_criteria
            if criterion.step_id is None or criterion.step_id in step_ids
        ]
        current_texts = {step.id: step.description if step.description.startswith(step.title) else step.title
                         for step in plan.steps}
        if criteria is None:
            previous_texts = {step.id: {step.title, step.description if step.description.startswith(step.title) else step.title}
                              for step in record.summary.plan.steps} if record.summary.plan else {}
            conditions = [criterion.model_copy(update={"text": current_texts[criterion.step_id]})
                          if criterion.step_id and criterion.text in previous_texts.get(criterion.step_id, set())
                          else criterion for criterion in conditions]
        bound = {criterion.step_id for criterion in conditions if criterion.step_id}
        used_ids = {criterion.id for criterion in conditions}
        for step in plan.steps:
            if step.id not in bound:
                criterion_id = step.id if step.id not in used_ids else f"criterion-{uuid4().hex[:10]}"
                conditions.append(CompletionCriterion(id=criterion_id, text=current_texts[step.id], step_id=step.id))
                used_ids.add(criterion_id)
        self._validate_completion_steps(plan, conditions, require_steps=True)
        record.summary.plan = plan.model_copy(deep=True)
        record.summary.completion_criteria = [criterion.model_copy(deep=True) for criterion in conditions]
        record.summary.completion_confirmation = None
        record.summary.updated_at = datetime.now(timezone.utc)
        record.summary.next_action = "Review and approve the updated plan and each step's completion conditions."
        self._persist(record)
        return record.summary

    @staticmethod
    def _validate_completion_steps(plan: TaskPlan | None, criteria: list[CompletionCriterion], *, require_steps: bool = False) -> None:
        if len(criteria) > 12 or len({criterion.id for criterion in criteria}) != len(criteria):
            raise RepositoryError("INVALID_COMPLETION_CRITERIA", "Provide at most 12 completion conditions with unique ids.")
        step_ids = {step.id for step in plan.steps} if plan else set()
        bound = {criterion.step_id for criterion in criteria if criterion.step_id}
        if not bound.issubset(step_ids) or ((require_steps or bound) and bound != step_ids):
            raise RepositoryError("INVALID_COMPLETION_STEPS", "Bind a completion condition to every plan step and use only current step ids.")

    def set_completion_criteria(self, task_id: str, criteria: list[CompletionCriterion]) -> TaskSummary:
        self.assert_recovered(task_id)
        record = self._record(task_id)
        if record.summary.track != "code" or record.iterations or record.summary.status != "awaiting_approval":
            raise TaskStateError("Configure completion criteria before the first coding iteration.")
        self._validate_completion_steps(record.summary.plan, criteria,
                                        require_steps=any(criterion.step_id for criterion in record.summary.completion_criteria))
        record.summary.completion_criteria = criteria
        record.summary.completion_confirmation = None
        record.summary.updated_at = datetime.now(timezone.utc)
        record.summary.next_action = "Review and approve the plan and completion criteria."
        self._persist(record)
        return record.summary

    def _completion_validation_passed(self, task_id: str, validation_hash: str | None) -> bool:
        iterations = self._record(task_id).iterations
        latest = iterations[-1].test_result if iterations and not iterations[-1].rolled_back else None
        return bool(latest and latest.status == "passed" and latest.validation_hash == validation_hash
                    and self.validation_passed(task_id, validation_hash))

    @staticmethod
    def _completion_evidence_valid(summary: TaskSummary, payload: CompletionConfirmationRequest) -> bool:
        expected = {criterion.id for criterion in summary.completion_criteria} or {"task-goal"}
        supplied = [item.criterion_id for item in payload.evidence]
        return (len(supplied) == len(expected) and set(supplied) == expected
                and all(item.evidence.strip() for item in payload.evidence))

    def completion_status(self, task_id: str, validation_hash: str | None) -> CompletionStatus:
        record = self._record(task_id)
        confirmation = record.summary.completion_confirmation
        if not record.summary.completion_criteria and confirmation is None:
            return "not_required"
        if confirmation is None:
            return "pending"
        if (not record.iterations or confirmation.iteration_id != record.iterations[-1].id
                or confirmation.scope_hash != plan_scope_hash(record.summary)
                or confirmation.validation_hash != validation_hash
                or not self._completion_evidence_valid(record.summary, confirmation)
                or not self._completion_validation_passed(task_id, validation_hash)):
            return "stale"
        return "confirmed"

    def confirm_completion(self, task_id: str, payload: CompletionConfirmationRequest, validation_hash: str | None) -> TaskSummary:
        self.assert_recovered(task_id)
        record = self._record(task_id)
        if record.summary.track != "code":
            raise TaskStateError("Completion confirmation requires a coding task.")
        if record.summary.status != "ready_for_pr" or record.summary.agent_blocked_reason:
            raise RepositoryError("COMPLETION_VALIDATION_REQUIRED", "Passing current validation is required before confirming completion.")
        if (not record.iterations or payload.iteration_id != record.iterations[-1].id
                or payload.scope_hash != plan_scope_hash(record.summary)
                or payload.validation_hash != validation_hash):
            raise RepositoryError("COMPLETION_CONFIRMATION_STALE", "The task scope, iteration or validation changed. Review completion again.")
        if not self._completion_validation_passed(task_id, validation_hash):
            raise RepositoryError("COMPLETION_VALIDATION_REQUIRED", "Passing current validation is required before confirming completion.")
        self._validate_completion_steps(record.summary.plan, record.summary.completion_criteria)
        if not self._completion_evidence_valid(record.summary, payload):
            raise RepositoryError("INVALID_COMPLETION_EVIDENCE", "Provide nonempty evidence for every completion criterion exactly once.")
        now = datetime.now(timezone.utc)
        record.summary.completion_confirmation = CompletionConfirmation(
            **payload.model_dump(), confirmed_at=now, confirmed_by="local-user",
        )
        record.summary.updated_at = now
        record.summary.next_action = "Review the final diff and approve commit or PR creation."
        self._persist(record)
        return record.summary

    def complete_local(self, task_id: str, validation_hash: str | None) -> TaskSummary:
        self.assert_recovered(task_id)
        record = self._record(task_id)
        if record.summary.track != "code" or record.summary.status != "ready_for_pr" or record.summary.agent_blocked_reason:
            raise RepositoryError("LOCAL_COMPLETION_NOT_READY", "A local task can finish only after its current iteration passes validation.")
        if not self._completion_validation_passed(task_id, validation_hash):
            raise RepositoryError("COMPLETION_VALIDATION_REQUIRED", "Passing current validation is required before completing the local task.")
        if self.completion_status(task_id, validation_hash) != "confirmed":
            raise RepositoryError("COMPLETION_REQUIRED", "Confirm current completion evidence before completing the local task.")
        self._transition(record, "completed")
        record.summary.next_action = "Local task completed. Review the saved evidence and changed files."
        self._persist(record)
        return record.summary

    def plan_progress(self, task_id: str, validation_hash: str | None) -> list[PlanStepProgress]:
        record = self._record(task_id)
        if not record.summary.plan:
            return []
        validated = self._completion_validation_passed(task_id, validation_hash)
        confirmation = record.summary.completion_confirmation if self.completion_status(task_id, validation_hash) == "confirmed" else None
        progress = []
        for step in record.summary.plan.steps:
            prefixes = [path.replace("\\", "/").removeprefix("./").rstrip("/") for path in step.files if path.strip()]
            matches = []
            for iteration in record.iterations:
                if iteration.rolled_back:
                    continue
                files = [path for path in iteration.changed_files
                         if any(prefix == "." or path.replace("\\", "/") == prefix
                                or path.replace("\\", "/").startswith(prefix + "/") for prefix in prefixes)]
                if files:
                    matches.append((iteration.id, files))
            criterion_ids = {criterion.id for criterion in record.summary.completion_criteria if criterion.step_id == step.id}
            evidence = [item for item in confirmation.evidence if item.criterion_id in criterion_ids] if confirmation else []
            status = "confirmed" if criterion_ids and len(evidence) == len(criterion_ids) else "validated" if matches and validated else "changed" if matches else "pending"
            progress.append(PlanStepProgress(step_id=step.id, status=status,
                                             changed_files=sorted({path for _, files in matches for path in files}),
                                             iteration_ids=[iteration_id for iteration_id, _ in matches], evidence=evidence))
        return progress

    def assert_completion_ready(self, task_id: str, validation_hash: str | None) -> None:
        status = self.completion_status(task_id, validation_hash)
        if status not in {"not_required", "confirmed"}:
            raise RepositoryError("COMPLETION_REQUIRED", "Review and confirm completion evidence for the current task before committing or publishing.", {"status": status})

    def validation_status(self, task_id: str, validation_hash: str | None) -> list[ValidationCheckStatus]:
        record = self._record(task_id)
        iteration = record.iterations[-1] if record.iterations else None
        runs = iteration.test_runs if iteration and not iteration.rolled_back else []
        statuses = []
        for check in record.summary.validation_checks:
            result = next((run for run in reversed(runs) if run.check_id == check.id), None)
            status = "pending" if result is None else "stale" if not validation_hash or result.validation_hash != validation_hash else result.status
            statuses.append(ValidationCheckStatus(check=check, status=status, result=result))
        return statuses

    def validation_passed(self, task_id: str, validation_hash: str | None) -> bool:
        record = self._record(task_id)
        if not validation_hash or not record.iterations or record.iterations[-1].rolled_back:
            return False
        statuses = self.validation_status(task_id, validation_hash)
        if statuses:
            return all(item.status == "passed" for item in statuses)
        iteration = record.iterations[-1]
        latest = iteration.test_result
        return bool(latest and latest.status == "passed" and latest.validation_hash == validation_hash
                    and any(run.kind != "static" and run.status == "passed" and run.validation_hash == validation_hash for run in iteration.test_runs))

    def list_iterations(self, task_id: str) -> list[TaskIteration]:
        record = self._record(task_id)
        if prune_test_outputs(record.iterations, self.settings):
            self._persist(record)
        return list(record.iterations)

    def list_checkpoints(self, task_id: str) -> list[TaskCheckpoint]:
        return list(self._record(task_id).checkpoints)

    def start_iteration(self, task_id: str, goal: str | None = None) -> TaskIteration:
        self.assert_recovered(task_id)
        self.assert_budget_available(task_id, new_iteration=True)
        record = self._record(task_id)
        if record.summary.repository_id and record.summary.status in {"awaiting_approval", "repairing", "failed", "coding"}:
            self.prepare_validation_guard(task_id)
        self.clear_agent_block(task_id)
        if record.summary.status == "awaiting_approval":
            self._transition(record, "coding")
        elif record.summary.status in {"repairing", "failed"}:
            self._transition(record, "coding")
        elif record.summary.status != "coding":
            raise TaskStateError("A coding iteration cannot start in the current task state.", {"status": record.summary.status})
        now = datetime.now(timezone.utc)
        record.summary.budget.started_at = record.summary.budget.started_at or now
        number = len(record.iterations) + 1
        iteration = TaskIteration(
            id=f"iteration-{uuid4().hex[:10]}",
            task_id=task_id,
            run_id=record.summary.run_id,
            number=number,
            goal=(goal or record.summary.goal).strip(),
            status="coding",
            created_at=now,
            updated_at=now,
        )
        record.iterations.append(iteration)
        record.summary.current_iteration = number
        record.summary.budget.iterations = number
        record.summary.next_action = "Prepare and review a patch for this iteration."
        record.summary.updated_at = now
        self._persist(record)
        return iteration

    def mark_patch_applied(self, run_id: str, patch_id: str, checkpoint_id: str, files: list[str]) -> TaskIteration:
        record = self._record_by_run(run_id)
        iteration = self._current_iteration(record)
        if record.summary.status not in {"coding", "repairing", "failed"}:
            raise TaskStateError("A patch can only be applied during a coding iteration.", {"status": record.summary.status})
        iteration.patch_id = patch_id
        iteration.no_changes = False
        iteration.checkpoint_id = checkpoint_id
        iteration.changed_files = files
        iteration.updated_at = datetime.now(timezone.utc)
        self._transition(record, "testing")
        record.summary.next_action = "Run the selected tests for this iteration."
        self.clear_agent_block(record.summary.id)
        self._persist(record)
        return iteration

    def mark_no_changes(self, run_id: str, rationale: str) -> TaskIteration:
        record = self._record_by_run(run_id)
        self.assert_recovered(record.summary.id)
        iteration = self._current_iteration(record)
        if record.summary.status not in {"coding", "repairing"} or iteration.patch_id or iteration.rolled_back:
            raise TaskStateError("A no-change result requires a current coding iteration without an applied patch.")
        iteration.no_changes = True
        iteration.status = "testing"
        iteration.updated_at = datetime.now(timezone.utc)
        if record.summary.status == "repairing":
            self._transition(record, "coding")
        self._transition(record, "testing")
        record.summary.next_action = f"No source changes proposed: {rationale.strip()}. Run all required validation before reviewing completion evidence."
        self.clear_agent_block(record.summary.id)
        self._persist(record)
        return iteration

    def assert_patch_allowed(self, run_id: str) -> None:
        record = self._record_by_run(run_id)
        self.assert_budget_available(record.summary.id)
        if not record.iterations or record.iterations[-1].rolled_back:
            raise TaskStateError("Start a coding iteration before applying a patch.", {"status": record.summary.status})
        if record.summary.status not in {"coding", "repairing", "failed"}:
            raise TaskStateError("A patch can only be applied during a coding iteration.", {"status": record.summary.status})

    def record_test_result(self, run_id: str, result: TestRunResponse) -> TaskIteration:
        record = self._record_by_run(run_id)
        iteration = self._current_iteration(record)
        iteration.test_result = result
        iteration.test_runs.append(result)
        iteration.failure_summary = None
        iteration.updated_at = datetime.now(timezone.utc)
        if record.summary.status in {"paused", "cancelled"} or result.status == "cancelled":
            if record.summary.status != "cancelled":
                iteration.status = "coding"
            self._persist(record)
            return iteration
        if result.status == "passed":
            record.summary.budget.consecutive_failures = 0
            if record.summary.validation_checks and not self.validation_passed(record.summary.id, result.validation_hash):
                iteration.status = "testing"
                record.summary.next_action = "Run the remaining required validation checks."
            else:
                iteration.status = "passed"
                self._transition(record, "ready_for_pr")
                record.summary.next_action = "Review completion evidence against the task goal." if record.summary.completion_criteria else "Review the final diff and approve commit or PR creation."
        else:
            record.summary.budget.consecutive_failures += 1
            iteration.status = "failed"
            iteration.failure_summary = result.failed_tests[0].message if result.failed_tests else result.stderr or f"Test run {result.status}."
            record.summary.retry_count += 1
            self._transition(record, "failed")
            record.summary.next_action = "Inspect the failure and start a repair iteration."
        self._refresh_budget(record)
        self._persist(record)
        return iteration

    def begin_test_run(self, run_id: str) -> TaskIteration | None:
        record = self._record_by_run(run_id)
        self.assert_budget_available(record.summary.id)
        if not record.iterations or record.iterations[-1].rolled_back:
            raise TaskStateError("Start a coding iteration before running tests.", {"status": record.summary.status})
        iteration = self._current_iteration(record)
        if record.summary.status not in {"coding", "testing", "failed", "ready_for_pr"}:
            raise TaskStateError("Tests cannot run in the current task state.", {"status": record.summary.status})
        if record.summary.status != "testing":
            self._transition(record, "testing")
        iteration.status = "testing"
        iteration.updated_at = datetime.now(timezone.utc)
        record.summary.next_action = "Wait for the selected tests to finish."
        self._persist(record)
        return iteration

    def start_repair(self, task_id: str, feedback: str | None = None) -> TaskIteration:
        self.assert_recovered(task_id)
        self.assert_budget_available(task_id, new_iteration=True)
        record = self._record(task_id)
        if record.summary.status not in {"failed", "repairing"}:
            raise TaskStateError("A repair iteration requires a failed or paused task.", {"status": record.summary.status})
        self._transition(record, "repairing")
        return self.start_iteration(task_id, feedback or "Repair the latest test failure and rerun validation.")

    def revise_plan_from_feedback(self, task_id: str, feedback: str) -> TaskSummary:
        self.assert_recovered(task_id)
        self.assert_budget_available(task_id)
        record = self._record(task_id)
        if record.summary.status != "awaiting_approval" or record.iterations:
            raise TaskStateError("Only an unstarted task plan can be revised.", {"status": record.summary.status})
        goal = f"{record.summary.goal}\n\nReview feedback:\n{feedback}"
        if len(goal) > MAX_MESSAGE_CHARS:
            raise RepositoryError("PLAN_GOAL_TOO_LONG", f"The revised goal exceeds {MAX_MESSAGE_CHARS} characters. Shorten the review feedback.")
        repository = self.repositories.get(record.summary.repository_id) if record.summary.repository_id else None
        record.summary.goal = goal
        if record.summary.plan and any(criterion.step_id for criterion in record.summary.completion_criteria):
            record.summary.plan = record.summary.plan.model_copy(update={"objective": goal})
        else:
            record.summary.plan = self._build_plan(goal, repository)
        record.summary.completion_confirmation = None
        record.summary.updated_at = datetime.now(timezone.utc)
        record.summary.next_action = "Review and approve the revised task plan."
        self._persist(record)
        return record.summary

    def start_review_revision(self, task_id: str, feedback: str) -> TaskIteration:
        self.assert_recovered(task_id)
        self.assert_budget_available(task_id, new_iteration=True)
        record = self._record(task_id)
        if record.summary.status not in {"coding", "failed", "ready_for_pr"}:
            raise TaskStateError("Review revisions require a coding, failed or validated task.", {"status": record.summary.status})
        if record.summary.status == "ready_for_pr":
            self._transition(record, "awaiting_approval")
        return self.start_iteration(task_id, f"{record.summary.goal}\n\nReview feedback:\n{feedback}")

    def pause(self, task_id: str) -> TaskSummary:
        record = self._record(task_id)
        if record.summary.status == "paused":
            return record.summary
        previous_state = record.summary.status
        self._transition(record, "paused")
        record.summary.resume_state = previous_state
        record.summary.next_action = "Resume the task after reviewing the latest checkpoint."
        self._save_checkpoint(record, previous_state)
        self._persist(record)
        return record.summary

    def resume(self, task_id: str) -> TaskSummary:
        self.assert_recovered(task_id)
        self.assert_budget_available(task_id)
        record = self._record(task_id)
        if record.summary.status != "paused":
            raise TaskStateError("Only paused tasks can be resumed.", {"status": record.summary.status})
        target = record.summary.resume_state or "coding"
        self._transition(record, target)
        record.summary.resume_state = None
        record.summary.next_action = "Rerun validation for the current iteration." if target == "testing" else "Continue the current coding iteration."
        self.clear_agent_block(task_id)
        self._persist(record)
        return record.summary

    def cancel(self, task_id: str) -> TaskSummary:
        record = self._record(task_id)
        self._transition(record, "cancelled")
        record.summary.next_action = None
        record.summary.resume_state = None
        if record.iterations and record.iterations[-1].status in {"coding", "testing"}:
            record.iterations[-1].status = "cancelled"
        self._persist(record)
        return record.summary

    def delete(self, task_id: str) -> None:
        """Drop the task record. Checkpoints and approvals are removed by the caller."""
        self._record(task_id)
        key = self._keys.pop(task_id, None)
        self._tasks.pop(task_id, None)
        if self.store and key:
            self.store.delete("tasks", key)

    def record_rollback(self, run_id: str, checkpoint_ids: list[str]) -> TaskSummary:
        record = self._record_by_run(run_id)
        for iteration in record.iterations:
            if iteration.checkpoint_id in checkpoint_ids:
                iteration.rolled_back = True
                iteration.status = "cancelled"
        record.summary.status = "coding"
        record.summary.resume_state = None
        record.summary.next_action = "Start a new coding iteration after reviewing the restored files."
        record.summary.updated_at = datetime.now(timezone.utc)
        self._update_budget_wait(record, record.summary.requires_recovery, record.summary.updated_at)
        self.clear_agent_block(record.summary.id)
        self._persist(record)
        return record.summary

    def _save_checkpoint(self, record: TaskRecord, state: TaskStatus) -> TaskCheckpoint:
        checkpoint = TaskCheckpoint(
            id=f"task-checkpoint-{uuid4().hex[:10]}",
            task_id=record.summary.id,
            run_id=record.summary.run_id,
            state=state,
            iteration=record.summary.current_iteration,
            created_at=datetime.now(timezone.utc),
        )
        record.checkpoints.append(checkpoint)
        return checkpoint

    def _transition(self, record: TaskRecord, target: TaskStatus) -> None:
        current = record.summary.status
        if current == target:
            return
        if target not in self.ALLOWED_TRANSITIONS.get(current, set()):
            raise TaskStateError(f"Cannot transition task from {current} to {target}.", {"from": current, "to": target})
        now = datetime.now(timezone.utc)
        self._update_budget_wait(record, target in self.HUMAN_WAIT_STATES or record.summary.requires_recovery, now)
        record.summary.status = target
        record.summary.updated_at = now

    def _current_iteration(self, record: TaskRecord) -> TaskIteration:
        if not record.iterations:
            raise TaskStateError("The task has no active coding iteration.")
        return record.iterations[-1]

    def _record(self, task_id: str) -> TaskRecord:
        record = self._tasks.get(task_id)
        if record is None:
            raise RepositoryError("TASK_NOT_FOUND", "Task is not registered.", {"task_id": task_id})
        return record

    def _record_by_run(self, run_id: str) -> TaskRecord:
        for record in self._tasks.values():
            if record.summary.run_id == run_id:
                return record
        raise RepositoryError("RUN_NOT_FOUND", "Run is not registered.", {"run_id": run_id})

    def _build_plan(self, goal: str, repository: RepositoryRecord | None) -> TaskPlan:
        repository_name = repository.name if repository else "当前工作区"
        has_git = bool(repository and repository.has_git)
        listed = re.findall(r"^\s*(?:[-*•]\s+|\d+[.)、]\s*|[一二三四五六七八九十]+[、.)]\s*)(.+?)\s*$", goal, re.MULTILINE)
        if len(listed) < 2:
            listed = [item.strip() for item in re.split(r"[;；]", goal) if item.strip()]
        if len(listed) >= 2:
            if len(listed) > 12:
                raise RepositoryError("PLAN_TOO_MANY_STEPS", "A task can contain at most 12 explicit subgoals. Split this request into separate tasks.")
            if any(len(item) > 500 for item in listed):
                raise RepositoryError("PLAN_STEP_TOO_LONG", "Each explicit subgoal must contain at most 500 characters. Shorten the listed requirements.")
            return TaskPlan(objective=goal.strip(), repository_summary=f"{repository_name} · 可恢复编码循环", steps=[
                {"id": f"step-{index + 1}", "title": item[:120], "description": item}
                for index, item in enumerate(listed)
            ])
        lower_goal = goal.lower()
        if any(keyword in lower_goal for keyword in ("test", "测试", "bug", "修复", "fix")):
            first_title = "定位相关代码与失败路径"
            first_description = "搜索任务关键词，确认入口、调用链和现有测试覆盖。"
            second_title = "实现最小范围修复"
            second_description = "只修改与目标直接相关的文件，并保留现有行为。"
            verification = ["运行相关单元测试", "运行静态检查"]
        else:
            first_title = "理解仓库上下文"
            first_description = "读取项目结构、配置和相关符号，确认目标代码位置。"
            second_title = "设计并实现低风险变更"
            second_description = "生成可审阅的最小 diff，避免修改无关文件。"
            verification = ["运行相关单元测试", "检查 Git diff" if has_git else "检查文件 diff"]
        steps = [
            {"id": "step-1", "title": first_title, "description": first_description, "risk": "low", "verification": ["确认文件范围", "记录当前分支" if has_git else "确认文件快照"]},
            {"id": "step-2", "title": second_title, "description": second_description, "risk": "low", "verification": verification},
            {"id": "step-3", "title": "验证并等待人工确认", "description": "展示 diff、测试结果和审计轨迹，应用修改前保留检查点。", "risk": "low", "verification": ["检查工作区状态", "等待确认"]},
        ]
        return TaskPlan(objective=goal.strip(), repository_summary=f"{repository_name} · 可恢复编码循环", steps=steps)
