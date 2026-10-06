from datetime import datetime, timezone
import logging
import time
import hashlib
from uuid import uuid4

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

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
    RepositoryContext,
    FileContentResponse,
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
from .repository_service import RepositoryError
from .services import build_services


services = build_services()
settings = services.settings
repository_service = services.repository_service
task_service = services.task_service
patch_service = services.patch_service
test_service = services.test_service
audit_service = services.audit_service
git_service = services.git_service
approval_service = services.approval_service

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


@app.get("/api/v1/repositories/{repository_id}/files", response_model=FileContentResponse)
async def read_repository_file(repository_id: str, path: str = Query(min_length=1, max_length=4096)) -> FileContentResponse:
    result = repository_service.read_file(repository_id, path)
    audit_service.record("repository.read", "succeeded", "Read source file", details={"repository_id": repository_id, "path": result.path})
    return result


@app.get("/api/v1/repositories/{repository_id}/context", response_model=RepositoryContext)
async def repository_context(repository_id: str) -> RepositoryContext:
    return repository_service.context(repository_id)


@app.post("/api/v1/repositories/{repository_id}/search", response_model=SearchResponse)
async def search_repository(repository_id: str, payload: SearchRequest) -> SearchResponse:
    matches, truncated = repository_service.search(repository_id, payload)
    return SearchResponse(repository_id=repository_id, query=payload.query, matches=matches, truncated=truncated)


@app.post("/api/v1/tasks", response_model=TaskSummary, status_code=201)
async def create_task(payload: TaskCreateRequest) -> TaskSummary:
    task = task_service.create(payload.goal, payload.repository_id)
    if payload.repository_id:
        snapshot = git_service.snapshot(payload.repository_id)
        task_service.update_git_snapshot(task.id, snapshot.branch, snapshot.head, snapshot.changed_files)
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
    task_service.assert_recovered(task_id)
    task = task_service.get(task_id)
    if task.status == "awaiting_approval" and not approval_service.has_approved(task_id, "plan", plan_scope_hash(task)):
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
    task_service.assert_recovered(task_id)
    iteration = task_service.start_repair(task_id, payload.feedback)
    task = task_service.get(task_id)
    audit_service.record("iteration.repair", "succeeded", f"Started repair iteration {iteration.number}", task_id=task_id, run_id=task.run_id, details={"iteration_id": iteration.id, "number": iteration.number})
    return iteration


@app.post("/api/v1/tasks/{task_id}/pause", response_model=TaskActionResponse)
async def pause_task(task_id: str) -> TaskActionResponse:
    task = task_service.pause(task_id)
    test_service.cancel(task.run_id)
    audit_service.record("task.pause", "succeeded", "Paused task", task_id=task_id, run_id=task.run_id)
    return TaskActionResponse(task=task, action="paused")


@app.post("/api/v1/tasks/{task_id}/resume", response_model=TaskActionResponse)
async def resume_task(task_id: str) -> TaskActionResponse:
    if test_service.is_running(task_service.get(task_id).run_id):
        raise RepositoryError("TEST_STOPPING", "Wait for the validation process to stop before resuming.")
    task = task_service.resume(task_id)
    audit_service.record("task.resume", "succeeded", "Resumed task", task_id=task_id, run_id=task.run_id)
    return TaskActionResponse(task=task, action="resumed")


@app.post("/api/v1/tasks/{task_id}/cancel", response_model=TaskActionResponse)
async def cancel_task(task_id: str) -> TaskActionResponse:
    task = task_service.cancel(task_id)
    test_service.cancel(task.run_id)
    audit_service.record("task.cancel", "succeeded", "Cancelled task", task_id=task_id, run_id=task.run_id)
    return TaskActionResponse(task=task, action="cancelled")


@app.post("/api/v1/tasks/{task_id}/recover", response_model=TaskActionResponse)
async def recover_task(task_id: str) -> TaskActionResponse:
    task = task_service.recover(task_id)
    audit_service.record("task.recover", "succeeded", "Confirmed task recovery after restart", task_id=task.id, run_id=task.run_id)
    return TaskActionResponse(task=task, action="recovered")


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


def repository_id_for_task(task_id: str, require_recovered: bool = True) -> str:
    task = task_service.get(task_id)
    if require_recovered:
        task_service.assert_recovered(task_id)
    if not task.repository_id:
        raise RepositoryError("TASK_REPOSITORY_REQUIRED", "Task must be connected to a repository before Git operations.")
    return task.repository_id


@app.post("/api/v1/tasks/{task_id}/branch", response_model=GitSnapshotResponse)
async def create_task_branch(task_id: str, payload: BranchCreateRequest) -> GitSnapshotResponse:
    repository_id = repository_id_for_task(task_id)
    task = task_service.get(task_id)
    if task.current_iteration or task.task_branch:
        raise RepositoryError("TASK_BRANCH_ALREADY_STARTED", "Create one task branch before starting the first iteration.")
    snapshot = git_service.create_task_branch(repository_id, payload.name)
    task = task_service.update_git_snapshot(task_id, snapshot.branch, snapshot.head, snapshot.changed_files)
    task.task_branch = snapshot.branch
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
    task = task_service.get(task_id)
    prepared = git_service.preview(repository_id, task_service.changed_files(task_id), f"{task.id}: {payload.message.strip()}")
    audit_service.record("git.commit.preview", "succeeded", "Prepared task-scoped commit preview", task_id=task_id, run_id=task.run_id, details={"branch": prepared.branch, "changed_files": prepared.changed_files, "scope_hash": prepared.scope_hash})
    return CommitPreviewResponse(branch=prepared.branch, head=prepared.head, diff=prepared.diff, changed_files=prepared.changed_files, excluded_files=prepared.excluded_files, commit_message=payload.message.strip(), scope_hash=prepared.scope_hash, ready=prepared.ready)


@app.post("/api/v1/tasks/{task_id}/commits", response_model=GitSnapshotResponse)
async def create_task_commit(task_id: str, payload: CommitCreateRequest) -> GitSnapshotResponse:
    repository_id = repository_id_for_task(task_id)
    task = task_service.get(task_id)
    with git_service.lock(repository_id):
        iterations = task_service.list_iterations(task_id)
        if task.status != "ready_for_pr" or not iterations or not iterations[-1].test_result or iterations[-1].test_result.status != "passed":
            raise RepositoryError("COMMIT_NOT_READY", "A task commit requires passing tests for the latest iteration.", {"status": task.status})
        files = task_service.changed_files(task_id)
        message = f"{task.id}: {payload.message.strip()}"
        prepared = git_service.preview(repository_id, files, message)
        if prepared.scope_hash != payload.scope_hash:
            raise RepositoryError("COMMIT_PREVIEW_STALE", "Changes or commit message no longer match the preview. Preview and approve again.")
        verified_tests = [result for result in iterations[-1].test_runs if result.kind != "static" and result.status == "passed" and result.validation_hash == prepared.validation_hash]
        if not verified_tests:
            raise RepositoryError("TEST_RESULTS_STALE", "Run project or selected tests for the current task files before committing.")
        if prepared.validation_hash != iterations[-1].test_result.validation_hash:
            raise RepositoryError("TEST_RESULTS_STALE", "Task files or HEAD changed since validation. Run tests again.")
        if not approval_service.has_approved(task_id, "write", payload.scope_hash):
            raise RepositoryError("APPROVAL_REQUIRED", "A current write approval is required for this exact commit.", {"type": "write"})
        head = git_service.commit(repository_id, files, message, payload.scope_hash, task.task_branch or "")
        snapshot = git_service.snapshot(repository_id)
        task_service.update_git_snapshot(task_id, snapshot.branch, head, snapshot.changed_files)
        audit_service.record("git.commit.create", "succeeded", "Created approved task commit", task_id=task_id, run_id=task.run_id, details={"branch": snapshot.branch, "head": head, "files": prepared.changed_files, "scope_hash": payload.scope_hash})
        return GitSnapshotResponse(branch=snapshot.branch, head=head, changed_files=snapshot.changed_files, clean=snapshot.clean)


def repository_id_for_run(run_id: str, require_recovered: bool = True) -> str:
    task = task_service.get_by_run(run_id)
    if require_recovered:
        task_service.assert_recovered(task.id)
    if not task.repository_id:
        raise RepositoryError("TASK_REPOSITORY_REQUIRED", "Task must be connected to a repository before editing files.")
    return task.repository_id


@app.post("/api/v1/runs/{run_id}/patches/preview", response_model=PatchPreviewResponse)
async def preview_patch(run_id: str, payload: PatchPreviewRequest) -> PatchPreviewResponse:
    repository_id = repository_id_for_run(run_id)
    preview = patch_service.preview(repository_id, payload.files, payload.confirm_delete, run_id=run_id)
    audit_service.record("patch.preview", "succeeded", f"Prepared patch for {len(preview.files)} file(s)", run_id=run_id, details={"patch_id": preview.patch_id, "files": preview.files})
    return preview


@app.post("/api/v1/runs/{run_id}/patches/apply", response_model=PatchApplyResponse)
async def apply_patch(run_id: str, payload: PatchApplyRequest) -> PatchApplyResponse:
    repository_id = repository_id_for_run(run_id)
    task_service.assert_patch_allowed(run_id)
    if not payload.confirm:
        raise RepositoryError("PATCH_CONFIRMATION_REQUIRED", "Confirm the reviewed patch before applying it.")
    if patch_service.patch_repository_id(payload.patch_id) != repository_id or patch_service.patch_run_id(payload.patch_id) != run_id:
        raise RepositoryError("PATCH_NOT_FOUND", "Patch preview was not found for this run.")
    result = patch_service.apply(payload.patch_id, payload.confirm)
    task = task_service.get_by_run(run_id)
    task_service.mark_patch_applied(run_id, result.patch_id, result.checkpoint.id, result.applied_files)
    audit_service.record("patch.apply", "succeeded", f"Applied patch to {len(result.applied_files)} file(s)", task_id=task.id, run_id=run_id, details={"patch_id": result.patch_id, "checkpoint_id": result.checkpoint.id})
    return result


@app.post("/api/v1/runs/{run_id}/rollback", response_model=RollbackResponse)
async def rollback_run(run_id: str, payload: RollbackRequest) -> RollbackResponse:
    repository_id = repository_id_for_run(run_id, require_recovered=False)
    if test_service.is_running(run_id):
        raise RepositoryError("TEST_ALREADY_RUNNING", "Stop validation before restoring files.")
    if task_service.get_by_run(run_id).status in {"cancelled", "completed", "done", "pr_created"}:
        raise RepositoryError("INVALID_TASK_STATE", "A closed task cannot restore files.")
    if patch_service.checkpoint_repository_id(payload.checkpoint_id) != repository_id or patch_service.checkpoint_run_id(payload.checkpoint_id) != run_id:
        raise RepositoryError("CHECKPOINT_NOT_FOUND", "Checkpoint was not found for this run.")
    result = patch_service.rollback(payload.checkpoint_id)
    task_service.record_rollback(run_id, result.restored_checkpoint_ids)
    audit_service.record("checkpoint.rollback", "succeeded", f"Restored {len(result.restored_files)} file(s)", run_id=run_id, details={"checkpoint_id": payload.checkpoint_id})
    return result


@app.get("/api/v1/runs/{run_id}/checkpoints")
async def list_run_checkpoints(run_id: str):
    task = task_service.get_by_run(run_id)
    ids = [iteration.checkpoint_id for iteration in task_service.list_iterations(task.id) if iteration.checkpoint_id]
    items = patch_service.list_checkpoints(ids)
    return {"items": items, "total": len(items)}


@app.get("/api/v1/runs/{run_id}/tests")
async def list_run_tests(run_id: str):
    task = task_service.get_by_run(run_id)
    items = [{"iteration_id": iteration.id, "iteration_number": iteration.number, "result": result} for iteration in task_service.list_iterations(task.id) for result in iteration.test_runs]
    return {"items": items, "total": len(items)}


@app.post("/api/v1/runs/{run_id}/tests", response_model=TestRunResponse)
async def run_tests(run_id: str, payload: TestRunRequest) -> TestRunResponse:
    repository_id = repository_id_for_run(run_id)
    task = task_service.get_by_run(run_id)
    files = task_service.changed_files(task.id)
    before = git_service.preview(repository_id, files, "").validation_hash
    test_service.reserve(run_id)
    try:
        task_service.begin_test_run(run_id)
    except Exception:
        test_service.release(run_id)
        raise
    result = await run_in_threadpool(test_service.run, run_id, repository_id, payload.kind, payload.timeout_seconds, payload.target)
    try:
        after = git_service.preview(repository_id, files, "").validation_hash
    except RepositoryError:
        after = None
    if before == after:
        result.validation_hash = before
    elif result.status == "passed":
        result.status = "failed"
        result.stderr += "\nTask files changed during testing. Run validation again."
    task_service.record_test_result(run_id, result)
    audit_service.record("tests.run", "succeeded" if result.status == "passed" else "failed", f"Test run {result.status}", task_id=task.id, run_id=run_id, details={"test_id": result.id, "kind": result.kind, "command": result.command, "exit_code": result.exit_code, "failed_tests": [failure.model_dump() for failure in result.failed_tests]})
    return result


@app.get("/api/v1/runs/{run_id}/events", response_model=AuditEventListResponse)
async def list_run_events(run_id: str) -> AuditEventListResponse:
    task_service.get_by_run(run_id)
    items = audit_service.list_for_run(run_id)
    return AuditEventListResponse(items=items, total=len(items))
