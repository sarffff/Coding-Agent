from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)
    trace_id: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "coding-agent-api"
    timestamp: datetime


class WorkspaceSummary(BaseModel):
    repositories: int = 0
    active_tasks: int = 0
    pending_approvals: int = 0
    sandbox_status: Literal["ready", "degraded"] = "ready"


class RepositoryValidateRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4_096)


class RepositoryValidateResponse(BaseModel):
    valid: bool
    path: str
    git_root: str | None = None
    reason: str | None = None


class RepositoryCreateRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4_096)
    name: str | None = Field(default=None, max_length=120)


class RepositorySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    path: str
    branch: str
    head: str | None = None
    last_commit: str | None = None
    changed_files: int = 0
    languages: dict[str, int] = Field(default_factory=dict)
    package_manager: str | None = None
    registered_at: datetime


class RepositoryListResponse(BaseModel):
    items: list[RepositorySummary]
    total: int


class TreeEntry(BaseModel):
    path: str
    name: str
    kind: Literal["file", "directory"]
    size: int | None = None
    language: str | None = None


class TreeResponse(BaseModel):
    repository_id: str
    base_path: str
    items: list[TreeEntry]
    truncated: bool = False


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    glob: str | None = Field(default=None, max_length=200)
    extensions: list[str] = Field(default_factory=list, max_length=20)
    case_sensitive: bool = False
    max_results: int = Field(default=50, ge=1, le=200)


class SearchMatch(BaseModel):
    path: str
    line: int
    column: int
    text: str


class SearchResponse(BaseModel):
    repository_id: str
    query: str
    matches: list[SearchMatch]
    truncated: bool = False


TaskStatus = Literal[
    "queued",
    "planning",
    "coding",
    "testing",
    "repairing",
    "running",
    "review",
    "awaiting_approval",
    "ready_for_pr",
    "pr_created",
    "completed",
    "done",
    "failed",
    "cancelled",
    "paused",
]
RiskLevel = Literal["low", "medium", "high"]


class TaskCreateRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=4_000)
    repository_id: str | None = None


class PlanStep(BaseModel):
    id: str
    title: str
    description: str
    files: list[str] = Field(default_factory=list)
    risk: RiskLevel = "low"
    verification: list[str] = Field(default_factory=list)


class TaskPlan(BaseModel):
    objective: str
    repository_summary: str
    steps: list[PlanStep]


class TaskSummary(BaseModel):
    id: str
    run_id: str
    goal: str
    repository_id: str | None = None
    status: TaskStatus
    plan: TaskPlan | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    current_iteration: int = 0
    retry_count: int = 0
    next_action: str | None = None
    branch: str | None = None
    base_branch: str | None = None
    head: str | None = None
    dirty_files: list[str] = Field(default_factory=list)


IterationStatus = Literal["coding", "testing", "passed", "failed", "cancelled"]


class TaskIteration(BaseModel):
    id: str
    task_id: str
    run_id: str
    number: int
    goal: str
    status: IterationStatus
    changed_files: list[str] = Field(default_factory=list)
    patch_id: str | None = None
    checkpoint_id: str | None = None
    test_result: "TestRunResponse | None" = None
    failure_summary: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskIterationListResponse(BaseModel):
    items: list[TaskIteration]
    total: int


class IterationCreateRequest(BaseModel):
    goal: str | None = Field(default=None, min_length=3, max_length=4_000)


class RepairCreateRequest(BaseModel):
    feedback: str | None = Field(default=None, max_length=4_000)


class TaskActionResponse(BaseModel):
    task: TaskSummary
    action: Literal["paused", "resumed", "cancelled"]


class TaskCheckpoint(BaseModel):
    id: str
    task_id: str
    run_id: str
    state: TaskStatus
    iteration: int
    created_at: datetime


class TaskCheckpointListResponse(BaseModel):
    items: list[TaskCheckpoint]
    total: int


class BranchCreateRequest(BaseModel):
    name: str = Field(min_length=3, max_length=100)
    allow_dirty: bool = False


class GitSnapshotResponse(BaseModel):
    branch: str
    head: str
    changed_files: list[str] = Field(default_factory=list)
    clean: bool


class CommitPreviewRequest(BaseModel):
    message: str = Field(min_length=1, max_length=200)


class CommitPreviewResponse(BaseModel):
    branch: str
    head: str
    diff: str
    changed_files: list[str] = Field(default_factory=list)
    commit_message: str
    scope_hash: str
    ready: bool


class CommitCreateRequest(BaseModel):
    message: str = Field(min_length=1, max_length=200)


ApprovalType = Literal["plan", "write", "push", "pr"]
ApprovalStatus = Literal["pending", "approved", "rejected", "request_changes", "cancelled", "expired"]


class ApprovalRequestCreate(BaseModel):
    type: ApprovalType
    summary: str = Field(min_length=3, max_length=500)
    expires_in_minutes: int = Field(default=60, ge=5, le=7_200)
    scope_hash: str | None = Field(default=None, min_length=64, max_length=64)


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approve", "reject", "request_changes", "cancel"]
    reason: str | None = Field(default=None, max_length=4_000)


class ApprovalSummary(BaseModel):
    id: str
    task_id: str
    run_id: str
    type: ApprovalType
    status: ApprovalStatus
    summary: str
    scope_hash: str
    reason: str | None = None
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None


class ApprovalListResponse(BaseModel):
    items: list[ApprovalSummary]
    total: int


class TaskListResponse(BaseModel):
    items: list[TaskSummary]
    total: int


PatchOperation = Literal["create", "update", "delete"]


class PatchFile(BaseModel):
    path: str = Field(min_length=1, max_length=4_096)
    content: str | None = None
    operation: PatchOperation = "update"
    expected_hash: str | None = Field(default=None, max_length=128)


class PatchPreviewRequest(BaseModel):
    files: list[PatchFile] = Field(min_length=1, max_length=20)
    confirm_delete: bool = False


class PatchPreviewResponse(BaseModel):
    patch_id: str
    repository_id: str
    files: list[str]
    diff: str
    additions: int
    deletions: int
    bytes_changed: int
    requires_delete_confirmation: bool = False


class PatchApplyRequest(BaseModel):
    patch_id: str = Field(min_length=1, max_length=100)
    confirm: bool = False


class CheckpointSummary(BaseModel):
    id: str
    repository_id: str
    files: list[str]
    created_at: datetime


class PatchApplyResponse(BaseModel):
    patch_id: str
    checkpoint: CheckpointSummary
    applied_files: list[str]


class RollbackRequest(BaseModel):
    checkpoint_id: str = Field(min_length=1, max_length=100)


class RollbackResponse(BaseModel):
    checkpoint: CheckpointSummary
    restored_files: list[str]


TestKind = Literal["auto", "pytest", "frontend"]


class TestRunRequest(BaseModel):
    kind: TestKind = "auto"
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class TestFailure(BaseModel):
    path: str | None = None
    line: int | None = None
    test_name: str | None = None
    message: str


class TestRunResponse(BaseModel):
    run_id: str
    kind: TestKind
    command: list[str]
    status: Literal["passed", "failed", "timed_out", "not_found", "blocked"]
    exit_code: int | None = None
    duration_ms: int
    stdout: str
    stderr: str
    output_truncated: bool = False
    failed_tests: list[TestFailure] = Field(default_factory=list)


TaskIteration.model_rebuild()


class AuditEvent(BaseModel):
    id: str
    action: str
    status: Literal["started", "succeeded", "failed"]
    run_id: str | None = None
    task_id: str | None = None
    trace_id: str | None = None
    summary: str
    details: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class AuditEventListResponse(BaseModel):
    items: list[AuditEvent]
    total: int


