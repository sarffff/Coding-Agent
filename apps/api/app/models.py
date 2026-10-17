from datetime import datetime, timezone
from uuid import uuid4
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
    reason_code: str | None = None


class RepositoryCreateRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4_096)
    name: str | None = Field(default=None, max_length=120)


class RepositorySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    path: str
    branch: str
    has_git: bool = True
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


class SymbolInfo(BaseModel):
    name: str
    kind: str
    line: int


class FileContentResponse(BaseModel):
    repository_id: str
    path: str
    content: str
    content_hash: str
    size: int
    language: str | None = None
    line_count: int
    symbols: list[SymbolInfo] = Field(default_factory=list)


class RepositoryContext(BaseModel):
    repository_id: str
    languages: dict[str, int]
    package_manager: str | None = None
    entry_files: list[str]
    test_directories: list[str]
    config_files: list[str]
    file_count: int
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
AgentMode = Literal["conservative", "standard", "autonomous"]
TaskTrack = Literal["cowork", "code"]


class TaskCreateRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=4_000)
    repository_id: str | None = None
    mode: AgentMode = "standard"
    track: TaskTrack = "code"


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)


class ChatMessageResponse(BaseModel):
    reply: str


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


class TaskBudget(BaseModel):
    max_iterations: int = Field(default=8, ge=1)
    max_wall_time_seconds: int = Field(default=3_600, ge=1)
    max_remote_calls: int = Field(default=50, ge=1)
    max_consecutive_failures: int = Field(default=3, ge=1)
    iterations: int = 0
    elapsed_seconds: float = 0
    remote_calls: int = 0
    consecutive_failures: int = 0
    started_at: datetime | None = None
    exhausted_reason: Literal["iterations", "wall_time", "remote_calls", "consecutive_failures"] | None = None


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
    task_branch: str | None = None
    resume_state: TaskStatus | None = None
    requires_recovery: bool = False
    budget: TaskBudget = Field(default_factory=TaskBudget)
    mode: AgentMode = "standard"
    track: TaskTrack = "code"


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
    test_runs: list["TestRunResponse"] = Field(default_factory=list)
    failure_summary: str | None = None
    rolled_back: bool = False
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
    action: Literal["paused", "resumed", "cancelled", "recovered"]


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
    excluded_files: list[str] = Field(default_factory=list)
    commit_message: str
    scope_hash: str
    ready: bool


class CommitCreateRequest(BaseModel):
    message: str = Field(min_length=1, max_length=200)
    scope_hash: str = Field(min_length=64, max_length=64)


class CommitRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"commit-{uuid4().hex[:12]}")
    task_id: str
    run_id: str
    branch: str
    commit_sha: str
    commit_message: str
    scope_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CommitListResponse(BaseModel):
    items: list[CommitRecord]
    total: int


class RemoteBranchRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"rbranch-{uuid4().hex[:12]}")
    task_id: str
    run_id: str
    repository_id: str
    branch: str
    remote_url: str
    pushed_sha: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RemoteBranchListResponse(BaseModel):
    items: list[RemoteBranchRecord]
    total: int


class CheckRunRecord(BaseModel):
    """One CI check as read from the provider, persisted for replay and budget."""

    id: str = Field(default_factory=lambda: f"check-{uuid4().hex[:12]}")
    task_id: str
    run_id: str
    repository_id: str
    provider: str = "github"
    provider_id: str
    name: str
    status: str
    conclusion: str | None = None
    html_url: str = ""
    ref: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CheckRunListResponse(BaseModel):
    items: list[CheckRunRecord]
    total: int


RemoteCallOperation = Literal["validate", "push", "create_pr", "refresh_pr", "checks"]


class RemoteCallRecord(BaseModel):
    """Observability for one outward provider call: timing, HTTP status, attempts.

    Never stores the token or the full response body — only the status line.
    """

    id: str = Field(default_factory=lambda: f"rcall-{uuid4().hex[:12]}")
    task_id: str
    run_id: str
    operation: RemoteCallOperation
    status_code: int | None = None
    duration_ms: int = 0
    attempts: int = 1
    outcome: Literal["ok", "error"] = "ok"
    error_code: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RemoteCallListResponse(BaseModel):
    items: list[RemoteCallRecord]
    total: int


class TaskPushRequest(BaseModel):
    remote: str = Field(default="origin", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")


class RemoteTargetResponse(BaseModel):
    provider: str
    remote_url: str
    host: str
    owner: str
    repo: str
    default_branch: str
    allowed_hosts: list[str]
    allowed_repositories: list[str]


class PullRequestRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"pr-{uuid4().hex[:12]}")
    task_id: str
    run_id: str
    repository_id: str
    provider: str = "github"
    number: int
    title: str
    body: str
    html_url: str
    state: Literal["open", "closed", "merged", "draft"] = "open"
    source_branch: str
    target_branch: str
    draft: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PullRequestListResponse(BaseModel):
    items: list[PullRequestRecord]
    total: int


class PullRequestCreateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=3, max_length=10_000)
    target_branch: str = "main"
    draft: bool = True


class PullRequestPreviewResponse(BaseModel):
    title: str
    body: str
    source_branch: str
    target_branch: str
    draft: bool
    files: list[str]
    risk_level: RiskLevel
    ready: bool
    validation_passed: bool
    commit_sha: str | None = None


class TaskPublishStateResponse(BaseModel):
    task_id: str
    branch: str | None = None
    head: str | None = None
    remote_url: str | None = None
    commits: list[CommitRecord] = Field(default_factory=list)
    remote_branches: list[RemoteBranchRecord] = Field(default_factory=list)
    pull_request: PullRequestRecord | None = None
    checks: list[CheckRunRecord] = Field(default_factory=list)
    remote_calls: list[RemoteCallRecord] = Field(default_factory=list)


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
    continued_at: datetime | None = None


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
    run_id: str | None = None
    restored: bool = False
    repository_id: str
    files: list[str]
    diff: str = ""
    created_at: datetime


class PatchApplyResponse(BaseModel):
    patch_id: str
    checkpoint: CheckpointSummary
    applied_files: list[str]


class DraftPatchRequest(BaseModel):
    context_paths: list[str] = Field(default_factory=list, max_length=20)
    feedback: str | None = Field(default=None, max_length=4_000)


class DraftPatchResponse(PatchPreviewResponse):
    """A model-drafted patch preview: the same shape the manual flow produces, plus the model's rationale."""

    rationale: str = ""


class RollbackRequest(BaseModel):
    checkpoint_id: str = Field(min_length=1, max_length=100)


class RollbackResponse(BaseModel):
    checkpoint: CheckpointSummary
    restored_files: list[str]
    restored_checkpoint_ids: list[str] = Field(default_factory=list)


TestKind = Literal["auto", "pytest", "frontend", "static"]


class TestRunRequest(BaseModel):
    kind: TestKind = "auto"
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    target: str | None = Field(default=None, max_length=500)


class TestFailure(BaseModel):
    path: str | None = None
    line: int | None = None
    test_name: str | None = None
    message: str


class TestRunResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"test-{uuid4().hex[:12]}")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str
    kind: TestKind
    command: list[str]
    status: Literal["passed", "failed", "timed_out", "not_found", "blocked", "cancelled"]
    exit_code: int | None = None
    duration_ms: int
    stdout: str
    stderr: str
    output_truncated: bool = False
    output_pruned: bool = False
    validation_hash: str | None = None
    failed_tests: list[TestFailure] = Field(default_factory=list)


TaskIteration.model_rebuild()


AgentAction = Literal[
    "recover",
    "request_plan_approval",
    "await_plan_approval",
    "connect_repository",
    "create_branch",
    "start_iteration",
    "review_patch",
    "run_tests",
    "wait_for_tests",
    "start_repair",
    "review_commit",
    "review_push",
    "review_pr",
    "await_pr",
    "completed",
    "paused",
    "cancelled",
    "manual_review",
]


class AgentAdvanceRequest(TestRunRequest):
    action: Literal["auto", "request_plan_approval", "start_iteration", "start_repair", "run_tests"] = "auto"
    goal: str | None = Field(default=None, min_length=3, max_length=4_000)
    feedback: str | None = Field(default=None, max_length=4_000)


class AgentLoopRequest(BaseModel):
    """Kick the autonomous agent loop. `mode` sets the posture on the first start (None keeps the task's current posture on resume); `feedback` steers the next coding/repair iteration."""

    feedback: str | None = Field(default=None, max_length=4_000)
    mode: AgentMode | None = None


class AgentStateResponse(BaseModel):
    task: TaskSummary
    action: AgentAction
    can_advance: bool
    requires_approval: bool = False
    approval_type: ApprovalType | None = None
    message: str
    iteration: TaskIteration | None = None
    pending_approval: ApprovalSummary | None = None
    last_test: TestRunResponse | None = None
    loop_running: bool = False


class AgentAdvanceResponse(AgentStateResponse):
    performed_action: Literal["noop", "request_plan_approval", "start_iteration", "start_repair", "run_tests"] = "noop"


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
