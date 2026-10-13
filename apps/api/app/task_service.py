from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel

from .config import Settings
from .models import (
    AgentMode,
    TaskBudget,
    TaskCheckpoint,
    TaskIteration,
    TaskPlan,
    TaskStatus,
    TaskSummary,
    TaskTrack,
    TestRunResponse,
)
from .repository_service import RepositoryError, RepositoryRecord, RepositoryService
from .retention import prune_test_outputs
from .store import StateStore


class TaskStateError(RepositoryError):
    def __init__(self, message: str, details: dict[str, object] | None = None):
        super().__init__("INVALID_TASK_STATE", message, details)


@dataclass(slots=True)
class TaskRecord:
    summary: TaskSummary
    iterations: list[TaskIteration] = field(default_factory=list)
    checkpoints: list[TaskCheckpoint] = field(default_factory=list)


class PersistedTask(BaseModel):
    """Storage shape for one task: its summary plus the iterations and checkpoints it owns."""

    summary: TaskSummary
    iterations: list[TaskIteration] = []
    checkpoints: list[TaskCheckpoint] = []


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

    def __init__(self, repositories: RepositoryService, store: StateStore | None = None, settings: Settings | None = None):
        self.repositories = repositories
        self.store = store
        self.settings = settings or repositories.settings
        self._budget_lock = RLock()
        self._tasks: dict[str, TaskRecord] = {}
        self._keys: dict[str, str] = {}
        if store:
            for key, stored in store.models("tasks", PersistedTask):
                record = TaskRecord(summary=stored.summary, iterations=stored.iterations, checkpoints=stored.checkpoints)
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
                    record.summary.next_action = "服务已重启。请确认要继续该任务，或选择回滚与取消。"
                    store.put("tasks", key, PersistedTask(summary=record.summary, iterations=record.iterations, checkpoints=record.checkpoints), record.summary.updated_at.isoformat())
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
            PersistedTask(summary=record.summary, iterations=record.iterations, checkpoints=record.checkpoints),
            record.summary.updated_at.isoformat(),
        )

    def _new_budget(self) -> TaskBudget:
        return TaskBudget(
            max_iterations=self.settings.task_max_iterations,
            max_wall_time_seconds=self.settings.task_max_wall_time_seconds,
            max_remote_calls=self.settings.task_max_remote_calls,
            max_consecutive_failures=self.settings.task_max_consecutive_failures,
        )

    def _refresh_budget(self, record: TaskRecord, *, new_iteration: bool = False) -> TaskBudget:
        budget = record.summary.budget
        budget.iterations = len(record.iterations)
        if budget.started_at:
            end = record.summary.updated_at if record.summary.status in {"completed", "done", "cancelled"} else datetime.now(timezone.utc)
            budget.elapsed_seconds = round(max(0, (end - budget.started_at).total_seconds()), 3)
        if budget.exhausted_reason is None:
            if budget.elapsed_seconds >= budget.max_wall_time_seconds:
                budget.exhausted_reason = "wall_time"
            elif budget.consecutive_failures >= budget.max_consecutive_failures:
                budget.exhausted_reason = "consecutive_failures"
            elif budget.remote_calls >= budget.max_remote_calls:
                budget.exhausted_reason = "remote_calls"
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
            record.summary.budget.started_at = record.summary.budget.started_at or datetime.now(timezone.utc)
            record.summary.budget.remote_calls += 1
            self._persist(record)

    def record_remote_result(self, task_id: str, succeeded: bool) -> None:
        with self._budget_lock:
            record = self._record(task_id)
            budget = record.summary.budget
            # Only a passing test proves the repair worked; a successful API call cannot reset failures.
            if not succeeded:
                budget.consecutive_failures += 1
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

    def create(self, goal: str, repository_id: str | None = None, mode: AgentMode = "standard", track: TaskTrack = "code") -> TaskSummary:
        repository: RepositoryRecord | None = None
        if repository_id:
            repository = self.repositories.get(repository_id)
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
            )
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

    def set_mode(self, task_id: str, mode: AgentMode) -> TaskSummary:
        record = self._record(task_id)
        record.summary.mode = mode
        record.summary.updated_at = datetime.now(timezone.utc)
        self._persist(record)
        return record.summary

    def changed_files(self, task_id: str) -> list[str]:
        return sorted({name for iteration in self._record(task_id).iterations if not iteration.rolled_back for name in iteration.changed_files})

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
        iteration.checkpoint_id = checkpoint_id
        iteration.changed_files = files
        iteration.updated_at = datetime.now(timezone.utc)
        self._transition(record, "testing")
        record.summary.next_action = "Run the selected tests for this iteration."
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
            iteration.status = "passed"
            self._transition(record, "ready_for_pr")
            record.summary.next_action = "Review the final diff and approve commit or PR creation."
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
        if len(goal) > 4_000:
            raise RepositoryError("PLAN_GOAL_TOO_LONG", "The revised goal exceeds 4000 characters. Shorten the review feedback.")
        repository = self.repositories.get(record.summary.repository_id) if record.summary.repository_id else None
        record.summary.goal = goal
        record.summary.plan = self._build_plan(goal, repository)
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
        return self.start_iteration(task_id, f"{record.summary.goal}\n\nReview feedback:\n{feedback}"[:4_000])

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
        record.summary.status = target
        record.summary.updated_at = datetime.now(timezone.utc)

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
