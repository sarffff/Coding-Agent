from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from .models import (
    TaskCheckpoint,
    TaskIteration,
    TaskPlan,
    TaskStatus,
    TaskSummary,
    TestRunResponse,
)
from .repository_service import RepositoryError, RepositoryRecord, RepositoryService


class TaskStateError(RepositoryError):
    def __init__(self, message: str, details: dict[str, object] | None = None):
        super().__init__("INVALID_TASK_STATE", message, details)


@dataclass(slots=True)
class TaskRecord:
    summary: TaskSummary
    iterations: list[TaskIteration] = field(default_factory=list)
    checkpoints: list[TaskCheckpoint] = field(default_factory=list)


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
        "paused": {"coding", "testing", "repairing", "cancelled"},
        "cancelled": set(),
        "running": {"testing", "paused", "failed", "cancelled"},
    }

    def __init__(self, repositories: RepositoryService):
        self.repositories = repositories
        self._tasks: dict[str, TaskRecord] = {}

    def create(self, goal: str, repository_id: str | None = None) -> TaskSummary:
        repository: RepositoryRecord | None = None
        if repository_id:
            repository = self.repositories.get(repository_id)
        now = datetime.now(timezone.utc)
        task_id = f"task-{uuid4().hex[:10]}"
        run_id = f"run-{uuid4().hex[:10]}"
        summary = TaskSummary(
            id=task_id,
            run_id=run_id,
            goal=goal.strip(),
            repository_id=repository_id,
            status="awaiting_approval",
            plan=self._build_plan(goal, repository),
            created_at=now,
            updated_at=now,
            next_action="Review and approve the task plan before the first coding iteration.",
        )
        self._tasks[task_id] = TaskRecord(summary=summary)
        return summary

    def list(self) -> list[TaskSummary]:
        return [record.summary for record in reversed(list(self._tasks.values()))]

    def get(self, task_id: str) -> TaskSummary:
        return self._record(task_id).summary

    def get_by_run(self, run_id: str) -> TaskSummary:
        return self._record_by_run(run_id).summary

    def update_git_snapshot(self, task_id: str, branch: str, head: str, changed_files: list[str]) -> TaskSummary:
        record = self._record(task_id)
        record.summary.branch = branch
        record.summary.head = head
        record.summary.dirty_files = changed_files
        record.summary.base_branch = record.summary.base_branch or branch
        record.summary.updated_at = datetime.now(timezone.utc)
        return record.summary

    def list_iterations(self, task_id: str) -> list[TaskIteration]:
        return list(self._record(task_id).iterations)

    def list_checkpoints(self, task_id: str) -> list[TaskCheckpoint]:
        return list(self._record(task_id).checkpoints)

    def start_iteration(self, task_id: str, goal: str | None = None) -> TaskIteration:
        record = self._record(task_id)
        if record.summary.status == "awaiting_approval":
            self._transition(record, "coding")
        elif record.summary.status in {"repairing", "paused", "failed"}:
            self._transition(record, "coding")
        elif record.summary.status != "coding":
            raise TaskStateError("A coding iteration cannot start in the current task state.", {"status": record.summary.status})
        now = datetime.now(timezone.utc)
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
        record.summary.next_action = "Prepare and review a patch for this iteration."
        record.summary.updated_at = now
        return iteration

    def mark_patch_applied(self, run_id: str, patch_id: str, checkpoint_id: str, files: list[str]) -> TaskIteration:
        record = self._record_by_run(run_id)
        iteration = self._current_iteration(record)
        if record.summary.status not in {"coding", "repairing", "paused", "failed"}:
            raise TaskStateError("A patch can only be applied during a coding iteration.", {"status": record.summary.status})
        iteration.patch_id = patch_id
        iteration.checkpoint_id = checkpoint_id
        iteration.changed_files = files
        iteration.updated_at = datetime.now(timezone.utc)
        self._transition(record, "testing")
        record.summary.next_action = "Run the selected tests for this iteration."
        return iteration

    def assert_patch_allowed(self, run_id: str) -> None:
        record = self._record_by_run(run_id)
        if not record.iterations:
            raise TaskStateError("Start a coding iteration before applying a patch.", {"status": record.summary.status})
        if record.summary.status not in {"coding", "repairing", "paused", "failed"}:
            raise TaskStateError("A patch can only be applied during a coding iteration.", {"status": record.summary.status})

    def record_test_result(self, run_id: str, result: TestRunResponse) -> TaskIteration:
        record = self._record_by_run(run_id)
        iteration = self._current_iteration(record)
        iteration.test_result = result
        iteration.updated_at = datetime.now(timezone.utc)
        if result.status == "passed":
            iteration.status = "passed"
            self._transition(record, "ready_for_pr")
            record.summary.next_action = "Review the final diff and approve commit or PR creation."
        else:
            iteration.status = "failed"
            iteration.failure_summary = result.failed_tests[0].message if result.failed_tests else result.stderr or f"Test run {result.status}."
            record.summary.retry_count += 1
            self._transition(record, "failed")
            record.summary.next_action = "Inspect the failure and start a repair iteration."
        return iteration

    def begin_test_run(self, run_id: str) -> TaskIteration | None:
        record = self._record_by_run(run_id)
        if not record.iterations:
            raise TaskStateError("Start a coding iteration before running tests.", {"status": record.summary.status})
        iteration = self._current_iteration(record)
        if record.summary.status not in {"coding", "testing", "failed", "ready_for_pr", "paused"}:
            raise TaskStateError("Tests cannot run in the current task state.", {"status": record.summary.status})
        if record.summary.status != "testing":
            self._transition(record, "testing")
        iteration.status = "testing"
        iteration.updated_at = datetime.now(timezone.utc)
        record.summary.next_action = "Wait for the selected tests to finish."
        return iteration

    def start_repair(self, task_id: str, feedback: str | None = None) -> TaskIteration:
        record = self._record(task_id)
        if record.summary.status not in {"failed", "paused", "repairing"}:
            raise TaskStateError("A repair iteration requires a failed or paused task.", {"status": record.summary.status})
        self._transition(record, "repairing")
        return self.start_iteration(task_id, feedback or "Repair the latest test failure and rerun validation.")

    def pause(self, task_id: str) -> TaskSummary:
        record = self._record(task_id)
        self._transition(record, "paused")
        record.summary.next_action = "Resume the task after reviewing the latest checkpoint."
        self._save_checkpoint(record)
        return record.summary

    def resume(self, task_id: str) -> TaskSummary:
        record = self._record(task_id)
        if record.summary.status != "paused":
            raise TaskStateError("Only paused tasks can be resumed.", {"status": record.summary.status})
        self._transition(record, "coding")
        record.summary.next_action = "Continue the current coding iteration."
        return record.summary

    def cancel(self, task_id: str) -> TaskSummary:
        record = self._record(task_id)
        self._transition(record, "cancelled")
        record.summary.next_action = None
        return record.summary

    def _save_checkpoint(self, record: TaskRecord) -> TaskCheckpoint:
        checkpoint = TaskCheckpoint(
            id=f"task-checkpoint-{uuid4().hex[:10]}",
            task_id=record.summary.id,
            run_id=record.summary.run_id,
            state=record.summary.status,
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
            verification = ["运行相关单元测试", "检查 Git diff"]
        steps = [
            {"id": "step-1", "title": first_title, "description": first_description, "risk": "low", "verification": ["确认文件范围", "记录当前分支"]},
            {"id": "step-2", "title": second_title, "description": second_description, "risk": "low", "verification": verification},
            {"id": "step-3", "title": "验证并等待人工确认", "description": "展示 diff、测试结果和审计轨迹，应用修改前保留检查点。", "risk": "low", "verification": ["检查工作区状态", "等待确认"]},
        ]
        return TaskPlan(objective=goal.strip(), repository_summary=f"{repository_name} · 可恢复编码循环", steps=steps)
