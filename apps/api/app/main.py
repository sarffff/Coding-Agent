from datetime import datetime, timezone
import logging
import time
import hashlib
from uuid import uuid4

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .models import (
    ApiError,
    AuditEventListResponse,
    HealthResponse,
    RepositoryCreateRequest,
    RepositoryListResponse,
    RepositorySummary,
    RepositoryValidateRequest,
    RepositoryValidateResponse,
    PatchApplyRequest,
    PatchApplyResponse,
    PatchPreviewRequest,
    PatchPreviewResponse,
    RollbackRequest,
    RollbackResponse,
    TestRunRequest,
    TestRunResponse,
    SearchRequest,
    SearchResponse,
    TreeResponse,
    TaskCreateRequest,
    IterationCreateRequest,
    RepairCreateRequest,
    TaskActionResponse,
    TaskIterationListResponse,
    TaskIteration,
    TaskCheckpointListResponse,
    BranchCreateRequest,
    GitSnapshotResponse,
    CommitPreviewRequest,
    CommitPreviewResponse,
    CommitCreateRequest,
    ApprovalRequestCreate,
    ApprovalDecisionRequest,
    ApprovalListResponse,
    ApprovalSummary,
    TaskListResponse,
    TaskSummary,
    WorkspaceSummary,
)
from .repository_service import RepositoryError, RepositoryService
from .task_service import TaskService
from .patch_service import PatchService
from .test_service import TestService
from .audit_service import AuditService
from .git_service import GitService
from .approval_service import ApprovalService


settings = get_settings()
repository_service = RepositoryService(settings)
task_service = TaskService(repository_service)
patch_service = PatchService(repository_service, settings)
test_service = TestService(repository_service, settings)
audit_service = AuditService()
git_service = GitService(repository_service, settings)
approval_service = ApprovalService()

app = FastAPI(
    title="Coding Agent API",
    version="0.1.0",
    description="Foundation API for the enterprise coding and DevOps agent.",
)

logger = logging.getLogger("forge.api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_trace_id(request: Request, call_next):
    trace_id = request.headers.get("x-trace-id") or uuid4().hex
    request.state.trace_id = trace_id
    started = time.perf_counter()
    response = await call_next(request)
    logger.info(
        "request method=%s path=%s status=%s duration_ms=%s trace_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        round((time.perf_counter() - started) * 1000),
        trace_id,
    )
    response.headers["x-trace-id"] = trace_id
    return response


def error_response(request: Request, status_code: int, code: str, message: str, details: dict[str, object] | None = None) -> JSONResponse:
    payload = ApiError(
        code=code,
        message=message,
        details=details or {},
        trace_id=getattr(request.state, "trace_id", uuid4().hex),
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def plan_scope_hash(task: TaskSummary) -> str:
    return hashlib.sha256((task.id + "\n" + task.goal + "\n" + (task.plan.objective if task.plan else "")).encode("utf-8")).hexdigest()


@app.exception_handler(RepositoryError)
async def repository_error_handler(request: Request, exc: RepositoryError) -> JSONResponse:
    status_code = 404 if exc.code in {"REPOSITORY_NOT_FOUND", "TASK_NOT_FOUND", "RUN_NOT_FOUND", "PATCH_NOT_FOUND", "CHECKPOINT_NOT_FOUND", "PATH_NOT_FOUND", "TREE_PATH_NOT_FOUND"} else 400
    return error_response(request, status_code, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(request, 422, "VALIDATION_ERROR", "Request validation failed.", {"errors": exc.errors()})


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Do not expose local paths or command output in the normal API response.
    return error_response(request, 500, "INTERNAL_ERROR", "The API could not complete the request.")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(timestamp=datetime.now(timezone.utc))


@app.get("/api/v1/workspace/summary", response_model=WorkspaceSummary)
async def workspace_summary() -> WorkspaceSummary:
    repositories = repository_service.list()
    tasks = task_service.list()
    return WorkspaceSummary(
        repositories=len(repositories),
        active_tasks=sum(task.status in {"queued", "planning", "coding", "testing", "repairing", "running"} for task in tasks),
        pending_approvals=sum(task.status in {"review", "awaiting_approval", "ready_for_pr"} for task in tasks),
    )


@app.post("/api/v1/repositories/validate", response_model=RepositoryValidateResponse)
async def validate_repository(payload: RepositoryValidateRequest) -> RepositoryValidateResponse:
    return repository_service.validate(payload.path)


@app.post("/api/v1/repositories", response_model=RepositorySummary, status_code=201)
async def create_repository(payload: RepositoryCreateRequest) -> RepositorySummary:
    repository = repository_service.register(payload.path, payload.name)
    audit_service.record("repository.register", "succeeded", f"Registered repository {repository.name}", details={"repository_id": repository.id})
    return repository


@app.get("/api/v1/repositories", response_model=RepositoryListResponse)
async def list_repositories() -> RepositoryListResponse:
    items = repository_service.list()
    return RepositoryListResponse(items=items, total=len(items))


@app.get("/api/v1/repositories/{repository_id}", response_model=RepositorySummary)
async def get_repository(repository_id: str) -> RepositorySummary:
    return repository_service.summary(repository_service.get(repository_id))


@app.get("/api/v1/repositories/{repository_id}/tree", response_model=TreeResponse)
async def repository_tree(
    repository_id: str,
    path: str = Query(default="", max_length=4_096),
    max_depth: int = Query(default=4, ge=0, le=20),
) -> TreeResponse:
    items, truncated = repository_service.tree(repository_id, path, max_depth)
    base_path = path.replace("\\", "/").strip("/") or "."
    return TreeResponse(repository_id=repository_id, base_path=base_path, items=items, truncated=truncated)


@app.post("/api/v1/repositories/{repository_id}/search", response_model=SearchResponse)
async def search_repository(repository_id: str, payload: SearchRequest) -> SearchResponse:
    matches, truncated = repository_service.search(repository_id, payload)
    return SearchResponse(repository_id=repository_id, query=payload.query, matches=matches, truncated=truncated)


@app.post("/api/v1/tasks", response_model=TaskSummary, status_code=201)
async def create_task(payload: TaskCreateRequest) -> TaskSummary:
    task = task_service.create(payload.goal, payload.repository_id)
    audit_service.record("task.create", "succeeded", "Created task", task_id=task.id, run_id=task.run_id, details={"repository_id": task.repository_id})
    return task


@app.get("/api/v1/tasks", response_model=TaskListResponse)
async def list_tasks() -> TaskListResponse:
    items = task_service.list()
    return TaskListResponse(items=items, total=len(items))


@app.get("/api/v1/tasks/{task_id}", response_model=TaskSummary)
async def get_task(task_id: str) -> TaskSummary:
    return task_service.get(task_id)


@app.post("/api/v1/tasks/{task_id}/iterations", response_model=TaskIteration, status_code=201)
async def start_iteration(task_id: str, payload: IterationCreateRequest):
    task = task_service.get(task_id)
    if task.status == "awaiting_approval" and not approval_service.has_approved(task_id, "plan"):
        raise RepositoryError("APPROVAL_REQUIRED", "Plan approval is required before the first coding iteration.", {"type": "plan"})
    iteration = task_service.start_iteration(task_id, payload.goal)
    task = task_service.get(task_id)
    audit_service.record("iteration.start", "succeeded", f"Started coding iteration {iteration.number}", task_id=task_id, run_id=task.run_id, details={"iteration_id": iteration.id, "number": iteration.number})
    return iteration


@app.get("/api/v1/tasks/{task_id}/iterations", response_model=TaskIterationListResponse)
async def list_iterations(task_id: str) -> TaskIterationListResponse:
    items = task_service.list_iterations(task_id)
    return TaskIterationListResponse(items=items, total=len(items))


@app.get("/api/v1/tasks/{task_id}/checkpoints", response_model=TaskCheckpointListResponse)
async def list_task_checkpoints(task_id: str) -> TaskCheckpointListResponse:
    items = task_service.list_checkpoints(task_id)
    return TaskCheckpointListResponse(items=items, total=len(items))


@app.post("/api/v1/tasks/{task_id}/repairs", response_model=TaskIteration, status_code=201)
async def start_repair(task_id: str, payload: RepairCreateRequest):
    iteration = task_service.start_repair(task_id, payload.feedback)
    task = task_service.get(task_id)
    audit_service.record("iteration.repair", "succeeded", f"Started repair iteration {iteration.number}", task_id=task_id, run_id=task.run_id, details={"iteration_id": iteration.id, "number": iteration.number})
    return iteration


@app.post("/api/v1/tasks/{task_id}/pause", response_model=TaskActionResponse)
async def pause_task(task_id: str) -> TaskActionResponse:
    task = task_service.pause(task_id)
    audit_service.record("task.pause", "succeeded", "Paused task", task_id=task_id, run_id=task.run_id)
    return TaskActionResponse(task=task, action="paused")


@app.post("/api/v1/tasks/{task_id}/resume", response_model=TaskActionResponse)
async def resume_task(task_id: str) -> TaskActionResponse:
    task = task_service.resume(task_id)
    audit_service.record("task.resume", "succeeded", "Resumed task", task_id=task_id, run_id=task.run_id)
    return TaskActionResponse(task=task, action="resumed")


@app.post("/api/v1/tasks/{task_id}/cancel", response_model=TaskActionResponse)
async def cancel_task(task_id: str) -> TaskActionResponse:
    task = task_service.cancel(task_id)
    audit_service.record("task.cancel", "succeeded", "Cancelled task", task_id=task_id, run_id=task.run_id)
    return TaskActionResponse(task=task, action="cancelled")


@app.get("/api/v1/tasks/{task_id}/approvals", response_model=ApprovalListResponse)
async def list_task_approvals(task_id: str) -> ApprovalListResponse:
    task = task_service.get(task_id)
    items = approval_service.list_for_task(task.id)
    return ApprovalListResponse(items=items, total=len(items))


@app.post("/api/v1/tasks/{task_id}/approvals", response_model=ApprovalSummary, status_code=201)
async def create_task_approval(task_id: str, payload: ApprovalRequestCreate) -> ApprovalSummary:
    task = task_service.get(task_id)
    if payload.type == "plan" and payload.scope_hash is None:
        payload.scope_hash = plan_scope_hash(task)
    approval = approval_service.create(task.id, task.run_id, payload)
    audit_service.record("approval.request", "succeeded", f"Requested {approval.type} approval", task_id=task.id, run_id=task.run_id, details={"approval_id": approval.id, "type": approval.type})
    return approval


@app.post("/api/v1/approvals/{approval_id}/decision", response_model=ApprovalSummary)
async def decide_approval(approval_id: str, payload: ApprovalDecisionRequest) -> ApprovalSummary:
    approval = approval_service.decide(approval_id, payload)
    audit_service.record("approval.decision", "succeeded", f"Approval {approval.status}", task_id=approval.task_id, run_id=approval.run_id, details={"approval_id": approval.id, "type": approval.type, "reason": approval.reason})
    return approval


def repository_id_for_task(task_id: str) -> str:
    task = task_service.get(task_id)
    if not task.repository_id:
        raise RepositoryError("TASK_REPOSITORY_REQUIRED", "Task must be connected to a repository before Git operations.")
    return task.repository_id


@app.post("/api/v1/tasks/{task_id}/branch", response_model=GitSnapshotResponse)
async def create_task_branch(task_id: str, payload: BranchCreateRequest) -> GitSnapshotResponse:
    repository_id = repository_id_for_task(task_id)
    snapshot = git_service.create_task_branch(repository_id, payload.name, payload.allow_dirty)
    task = task_service.update_git_snapshot(task_id, snapshot.branch, snapshot.head, snapshot.changed_files)
    audit_service.record("git.branch.create", "succeeded", f"Created task branch {snapshot.branch}", task_id=task_id, run_id=task.run_id, details={"branch": snapshot.branch, "head": snapshot.head})
    return GitSnapshotResponse(branch=snapshot.branch, head=snapshot.head, changed_files=snapshot.changed_files, clean=snapshot.clean)


@app.get("/api/v1/tasks/{task_id}/git", response_model=GitSnapshotResponse)
async def task_git_snapshot(task_id: str) -> GitSnapshotResponse:
    repository_id = repository_id_for_task(task_id)
    snapshot = git_service.snapshot(repository_id)
    task_service.update_git_snapshot(task_id, snapshot.branch, snapshot.head, snapshot.changed_files)
    return GitSnapshotResponse(branch=snapshot.branch, head=snapshot.head, changed_files=snapshot.changed_files, clean=snapshot.clean)


@app.post("/api/v1/tasks/{task_id}/commits/preview", response_model=CommitPreviewResponse)
async def preview_task_commit(task_id: str, payload: CommitPreviewRequest) -> CommitPreviewResponse:
    repository_id = repository_id_for_task(task_id)
    snapshot = git_service.snapshot(repository_id)
    diff = git_service.diff(repository_id)
    task = task_service.update_git_snapshot(task_id, snapshot.branch, snapshot.head, snapshot.changed_files)
    audit_service.record("git.commit.preview", "succeeded", "Prepared commit preview", task_id=task_id, run_id=task.run_id, details={"branch": snapshot.branch, "changed_files": snapshot.changed_files})
    return CommitPreviewResponse(branch=snapshot.branch, head=snapshot.head, diff=diff, changed_files=snapshot.changed_files, commit_message=payload.message.strip(), scope_hash=hashlib.sha256(diff.encode("utf-8")).hexdigest(), ready=bool(diff and not snapshot.clean))


@app.post("/api/v1/tasks/{task_id}/commits", response_model=GitSnapshotResponse)
async def create_task_commit(task_id: str, payload: CommitCreateRequest) -> GitSnapshotResponse:
    repository_id = repository_id_for_task(task_id)
    task = task_service.get(task_id)
    if task.status not in {"ready_for_pr", "awaiting_approval"}:
        raise RepositoryError("COMMIT_NOT_READY", "A task commit requires completed tests and reviewable changes.", {"status": task.status})
    diff = git_service.diff(repository_id)
    scope_hash = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    if not approval_service.has_approved(task_id, "write", scope_hash):
        raise RepositoryError("APPROVAL_REQUIRED", "A write approval is required before creating a task commit.", {"type": "write"})
    head = git_service.commit(repository_id, f"{task.id}: {payload.message.strip()}")
    snapshot = git_service.snapshot(repository_id)
    task_service.update_git_snapshot(task_id, snapshot.branch, head, snapshot.changed_files)
    audit_service.record("git.commit.create", "succeeded", "Created task commit", task_id=task_id, run_id=task.run_id, details={"branch": snapshot.branch, "head": head})
    return GitSnapshotResponse(branch=snapshot.branch, head=head, changed_files=snapshot.changed_files, clean=snapshot.clean)


def repository_id_for_run(run_id: str) -> str:
    task = task_service.get_by_run(run_id)
    if not task.repository_id:
        raise RepositoryError("TASK_REPOSITORY_REQUIRED", "Task must be connected to a repository before editing files.")
    return task.repository_id


@app.post("/api/v1/runs/{run_id}/patches/preview", response_model=PatchPreviewResponse)
async def preview_patch(run_id: str, payload: PatchPreviewRequest) -> PatchPreviewResponse:
    repository_id = repository_id_for_run(run_id)
    preview = patch_service.preview(repository_id, payload.files, payload.confirm_delete)
    audit_service.record("patch.preview", "succeeded", f"Prepared patch for {len(preview.files)} file(s)", run_id=run_id, details={"patch_id": preview.patch_id, "files": preview.files})
    return preview


@app.post("/api/v1/runs/{run_id}/patches/apply", response_model=PatchApplyResponse)
async def apply_patch(run_id: str, payload: PatchApplyRequest) -> PatchApplyResponse:
    repository_id = repository_id_for_run(run_id)
    task_service.assert_patch_allowed(run_id)
    if patch_service.patch_repository_id(payload.patch_id) != repository_id:
        raise RepositoryError("PATCH_NOT_FOUND", "Patch preview was not found for this run.")
    result = patch_service.apply(payload.patch_id, payload.confirm)
    task = task_service.get_by_run(run_id)
    task_service.mark_patch_applied(run_id, result.patch_id, result.checkpoint.id, result.applied_files)
    audit_service.record("patch.apply", "succeeded", f"Applied patch to {len(result.applied_files)} file(s)", task_id=task.id, run_id=run_id, details={"patch_id": result.patch_id, "checkpoint_id": result.checkpoint.id})
    return result


@app.post("/api/v1/runs/{run_id}/rollback", response_model=RollbackResponse)
async def rollback_run(run_id: str, payload: RollbackRequest) -> RollbackResponse:
    repository_id = repository_id_for_run(run_id)
    if patch_service.checkpoint_repository_id(payload.checkpoint_id) != repository_id:
        raise RepositoryError("CHECKPOINT_NOT_FOUND", "Checkpoint was not found for this run.")
    result = patch_service.rollback(payload.checkpoint_id)
    audit_service.record("checkpoint.rollback", "succeeded", f"Restored {len(result.restored_files)} file(s)", run_id=run_id, details={"checkpoint_id": payload.checkpoint_id})
    return result


@app.post("/api/v1/runs/{run_id}/tests", response_model=TestRunResponse)
async def run_tests(run_id: str, payload: TestRunRequest) -> TestRunResponse:
    repository_id = repository_id_for_run(run_id)
    task_service.begin_test_run(run_id)
    result = test_service.run(run_id, repository_id, payload.kind, payload.timeout_seconds)
    task = task_service.get_by_run(run_id)
    task_service.record_test_result(run_id, result)
    audit_service.record("tests.run", "succeeded" if result.status == "passed" else "failed", f"Test run {result.status}", task_id=task.id, run_id=run_id, details={"kind": result.kind, "command": result.command, "exit_code": result.exit_code, "failed_tests": [failure.model_dump() for failure in result.failed_tests]})
    return result


@app.get("/api/v1/runs/{run_id}/events", response_model=AuditEventListResponse)
async def list_run_events(run_id: str) -> AuditEventListResponse:
    task_service.get_by_run(run_id)
    items = audit_service.list_for_run(run_id)
    return AuditEventListResponse(items=items, total=len(items))
