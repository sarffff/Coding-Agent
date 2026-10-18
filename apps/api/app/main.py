from datetime import datetime, timezone
import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
    DraftPatchRequest,
    DraftPatchResponse,
    RollbackRequest,
    RollbackResponse,
    TestRunRequest,
    TestRunResponse,
    ValidationChecksRequest,
    SearchRequest,
    SearchResponse,
    TreeResponse,
    RepositoryContext,
    FileContentResponse,
    TaskCreateRequest,
    ChatMessageRequest,
    ChatMessageResponse,
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
    ApprovalStatus,
    ApprovalType,
    CommitListResponse,
    CommitRecord,
    PullRequestCreateRequest,
    PullRequestListResponse,
    PullRequestPreviewResponse,
    PullRequestRecord,
    RemoteBranchListResponse,
    RemoteBranchRecord,
    RemoteCallListResponse,
    RemoteTargetResponse,
    TaskPublishStateResponse,
    TaskPushRequest,
    TaskListResponse,
    TaskSummary,
    TaskBudget,
    AgentAdvanceRequest,
    AgentAdvanceResponse,
    AgentLoopRequest,
    AgentStateResponse,
    WorkspaceSummary,
)
from .approval_service import plan_scope_hash
from .repository_service import RepositoryError
from .idempotency import run_idempotent
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
remote_service = services.remote_service
agent_service = services.agent_service
idempotency_service = services.idempotency_service

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


ERROR_STATUS_BY_CODE = {
    "GIT_UNAVAILABLE": 503,
    "GIT_BUSY": 503,
    "AGENT_ACTION_NOT_AVAILABLE": 409,
    "LOOP_ALREADY_RUNNING": 409,
    "TASK_BUDGET_EXHAUSTED": 409,
    "APPROVAL_NOT_FOUND": 404,
    "APPROVAL_REASON_REQUIRED": 422,
    "APPROVAL_CONTINUATION_UNAVAILABLE": 409,
    "REMOTE_TIMEOUT": 504,
    "REMOTE_NETWORK_ERROR": 502,
    "PUSH_REJECTED": 502,
    "PR_CREATION_FAILED": 502,
    "REMOTE_ERROR": 502,
    "PUSHED_COMMIT_UNRESOLVED": 502,
    "AUTHENTICATION_FAILED": 401,
    "RATE_LIMITED_OR_FORBIDDEN": 429,
    "MODEL_INVALID_OUTPUT": 400,
    "MODEL_NO_RESPONSE": 400,
    "MODEL_AUTH_FAILED": 401,
    "MODEL_RATE_LIMITED": 429,
    "MODEL_NETWORK_ERROR": 502,
    "MODEL_ERROR": 502,
    "MODEL_TIMEOUT": 504,
    "REPOSITORY_NOT_FOUND": 404,
    "TASK_NOT_FOUND": 404,
    "RUN_NOT_FOUND": 404,
    "PATCH_NOT_FOUND": 404,
    "CHECKPOINT_NOT_FOUND": 404,
    "PATH_NOT_FOUND": 404,
    "TREE_PATH_NOT_FOUND": 404,
    "REMOTE_NOT_FOUND": 404,
    "PULL_REQUEST_NOT_FOUND": 404,
    "IDEMPOTENCY_IN_PROGRESS": 409,
    "IDEMPOTENCY_KEY_REUSED": 409,
    "BRANCH_NOT_PUSHED": 409,
    "REMOTE_HEAD_MOVED": 409,
    "NO_APPROVED_COMMIT": 409,
    "TASK_BRANCH_NOT_CHECKED_OUT": 409,
}


@app.exception_handler(RepositoryError)
async def repository_error_handler(request: Request, exc: RepositoryError) -> JSONResponse:
    return error_response(request, ERROR_STATUS_BY_CODE.get(exc.code, 400), exc.code, exc.message, exc.details)


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
    checks = payload.validation_checks
    if "validation_checks" not in payload.model_fields_set and payload.track == "code" and payload.repository_id:
        checks = test_service.default_checks(payload.repository_id)
    task = task_service.create(payload.goal, payload.repository_id, payload.mode, payload.track, checks)
    if payload.track == "code" and payload.repository_id and repository_service.get(payload.repository_id).has_git:
        snapshot = git_service.snapshot(payload.repository_id)
        task_service.update_git_snapshot(task.id, snapshot.branch, snapshot.head, snapshot.changed_files)
    audit_service.record("task.create", "succeeded", "Created task", task_id=task.id, run_id=task.run_id, details={"repository_id": task.repository_id, "track": task.track})
    return task


@app.post("/api/v1/tasks/{task_id}/chat", response_model=ChatMessageResponse)
async def cowork_chat(task_id: str, payload: ChatMessageRequest) -> ChatMessageResponse:
    reply = await agent_service.chat(task_id, payload.message.strip())
    return ChatMessageResponse(reply=reply)


@app.get("/api/v1/tasks", response_model=TaskListResponse)
async def list_tasks() -> TaskListResponse:
    items = task_service.list()
    return TaskListResponse(items=items, total=len(items))


@app.get("/api/v1/tasks/{task_id}", response_model=TaskSummary)
async def get_task(task_id: str) -> TaskSummary:
    return task_service.get(task_id)


@app.put("/api/v1/tasks/{task_id}/validation-checks", response_model=TaskSummary)
async def set_validation_checks(task_id: str, payload: ValidationChecksRequest) -> TaskSummary:
    agent_service.assert_workspace_available(task_id)
    if agent_service._loop_running(task_id) or test_service.is_running(task_service.get(task_id).run_id):
        raise RepositoryError("WORKSPACE_BUSY", "Stop execution before configuring validation checks.")
    task = task_service.set_validation_checks(task_id, payload.checks)
    audit_service.record("validation.configure", "succeeded", "Updated required validation checks", task_id=task.id, run_id=task.run_id,
                         details={"checks": [check.model_dump() for check in task.validation_checks]})
    return task


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


@app.get("/api/v1/tasks/{task_id}/agent", response_model=AgentStateResponse)
async def inspect_agent(task_id: str) -> AgentStateResponse:
    return agent_service.inspect(task_id)


@app.get("/api/v1/tasks/{task_id}/budget", response_model=TaskBudget)
async def get_task_budget(task_id: str) -> TaskBudget:
    return task_service.get_budget(task_id)


@app.post("/api/v1/tasks/{task_id}/agent/advance", response_model=AgentAdvanceResponse)
async def advance_agent(
    task_id: str, payload: AgentAdvanceRequest, idempotency_key: str | None = Header(default=None),
) -> AgentAdvanceResponse | JSONResponse:
    async def action() -> AgentAdvanceResponse:
        return await agent_service.advance(task_id, payload)

    return await run_idempotent(idempotency_service, idempotency_key, f"tasks/{task_id}/agent/advance", payload, action, status_code=200)


@app.get("/api/v1/approvals", response_model=ApprovalListResponse)
async def list_approvals(status: ApprovalStatus | None = None, type: ApprovalType | None = None, task_id: str | None = None) -> ApprovalListResponse:
    if task_id is not None:
        task_service.get(task_id)
    items = approval_service.list(task_id=task_id, status=status, approval_type=type)
    return ApprovalListResponse(items=items, total=len(items))


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


@app.post("/api/v1/approvals/{approval_id}/continue", response_model=TaskSummary)
async def continue_approval(approval_id: str) -> TaskSummary:
    approval = approval_service.continuation(approval_id)
    task = task_service.get(approval.task_id)
    if approval.continued_at is not None:
        return task
    task_service.assert_recovered(task.id)
    if test_service.is_running(task.run_id):
        raise RepositoryError("APPROVAL_CONTINUATION_UNAVAILABLE", "Wait for validation to stop before continuing a review.")
    if approval.type == "plan":
        task = task_service.revise_plan_from_feedback(task.id, approval.reason or "")
        replacement = approval_service.create(task.id, task.run_id, ApprovalRequestCreate(
            type="plan", summary="Review the revised task plan.", scope_hash=plan_scope_hash(task),
        ))
        audit_service.record("approval.request", "succeeded", "Requested revised plan approval", task_id=task.id, run_id=task.run_id, details={"approval_id": replacement.id, "type": "plan"})
    else:
        if not approval_service.has_approved(task.id, "plan", plan_scope_hash(task)):
            raise RepositoryError("APPROVAL_REQUIRED", "Approve the current task plan before starting a review revision.", {"type": "plan"})
        iteration = task_service.start_review_revision(task.id, approval.reason or "")
        audit_service.record("iteration.start", "succeeded", f"Started review revision {iteration.number}", task_id=task.id, run_id=task.run_id, details={"iteration_id": iteration.id, "approval_id": approval.id})
    approval_service.invalidate_publish_approvals(task.id)
    approval_service.mark_continued(approval.id)
    audit_service.record("approval.continue", "succeeded", "Continued requested changes", task_id=task.id, run_id=task.run_id, details={"approval_id": approval.id, "type": approval.type})
    return task


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
    task.task_branch = snapshot.branch
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
        validation_hash = agent_service.validation_hash(task)
        if not task_service.validation_passed(task_id, validation_hash):
            raise RepositoryError("TEST_RESULTS_STALE", "Run all required validation checks for the current task files before committing.")
        if validation_hash != iterations[-1].test_result.validation_hash:
            raise RepositoryError("TEST_RESULTS_STALE", "Task files or HEAD changed since validation. Run tests again.")
        if not approval_service.has_approved(task_id, "write", payload.scope_hash):
            raise RepositoryError("APPROVAL_REQUIRED", "A current write approval is required for this exact commit.", {"type": "write"})
        head = git_service.commit(repository_id, files, message, payload.scope_hash, task.task_branch or "")
        snapshot = git_service.snapshot(repository_id)
        task_service.update_git_snapshot(task_id, snapshot.branch, head, snapshot.changed_files)
        remote_service.record_commit(task_id, task.run_id, snapshot.branch, head, message, payload.scope_hash)
        audit_service.record("git.commit.create", "succeeded", "Created approved task commit", task_id=task_id, run_id=task.run_id, details={"branch": snapshot.branch, "head": head, "files": prepared.changed_files, "scope_hash": payload.scope_hash})
        return GitSnapshotResponse(branch=snapshot.branch, head=head, changed_files=snapshot.changed_files, clean=snapshot.clean)


@app.get("/api/v1/tasks/{task_id}/remote-target", response_model=RemoteTargetResponse)
async def task_remote_target(task_id: str, remote: str = Query(default="origin", max_length=40)) -> RemoteTargetResponse:
    repository_id_for_task(task_id)
    return await remote_service.remote_target(task_id, remote)


@app.get("/api/v1/tasks/{task_id}/publish-state", response_model=TaskPublishStateResponse)
async def task_publish_state(task_id: str) -> TaskPublishStateResponse:
    task_service.get(task_id)
    return remote_service.publish_state(task_id)


@app.get("/api/v1/tasks/{task_id}/commits", response_model=CommitListResponse)
async def list_task_commits(task_id: str) -> CommitListResponse:
    task_service.get(task_id)
    items = remote_service.list_commits(task_id)
    return CommitListResponse(items=items, total=len(items))


@app.get("/api/v1/tasks/{task_id}/remote-branches", response_model=RemoteBranchListResponse)
async def list_task_remote_branches(task_id: str) -> RemoteBranchListResponse:
    task_service.get(task_id)
    items = remote_service.list_remote_branches(task_id)
    return RemoteBranchListResponse(items=items, total=len(items))


@app.get("/api/v1/tasks/{task_id}/remote-calls", response_model=RemoteCallListResponse)
async def list_task_remote_calls(task_id: str) -> RemoteCallListResponse:
    task_service.get(task_id)
    items = remote_service.list_remote_calls(task_id)
    return RemoteCallListResponse(items=items, total=len(items))


@app.post("/api/v1/tasks/{task_id}/push", response_model=RemoteBranchRecord, status_code=201)
async def push_task_branch(
    task_id: str,
    payload: TaskPushRequest,
    idempotency_key: str | None = Header(default=None),
) -> RemoteBranchRecord | JSONResponse:
    repository_id_for_task(task_id)

    async def action() -> RemoteBranchRecord:
        return await remote_service.push_branch(task_id, payload.remote)

    return await run_idempotent(idempotency_service, idempotency_key, f"tasks/{task_id}/push", payload, action)


@app.get("/api/v1/tasks/{task_id}/pull-request/preview", response_model=PullRequestPreviewResponse)
async def preview_task_pull_request(
    task_id: str, target_branch: str = Query(default="main", max_length=100)
) -> PullRequestPreviewResponse:
    task_service.get(task_id)
    return remote_service.preview_pull_request(task_id, target_branch)


@app.post("/api/v1/tasks/{task_id}/pull-request", response_model=PullRequestRecord, status_code=201)
async def create_task_pull_request(
    task_id: str,
    payload: PullRequestCreateRequest,
    idempotency_key: str | None = Header(default=None),
) -> PullRequestRecord | JSONResponse:
    repository_id_for_task(task_id)

    async def action() -> PullRequestRecord:
        return await remote_service.create_pull_request(task_id, payload)

    return await run_idempotent(idempotency_service, idempotency_key, f"tasks/{task_id}/pull-request", payload, action)


@app.get("/api/v1/tasks/{task_id}/pull-request", response_model=PullRequestRecord)
async def get_task_pull_request(task_id: str) -> PullRequestRecord:
    record = remote_service.get_pull_request(task_id)
    if record is None:
        raise RepositoryError("PULL_REQUEST_NOT_FOUND", "No Pull Request has been created for this task.", {"task_id": task_id})
    return record


@app.post("/api/v1/tasks/{task_id}/pull-request/refresh", response_model=PullRequestRecord)
async def refresh_task_pull_request(task_id: str) -> PullRequestRecord:
    task_service.assert_recovered(task_id)
    record = await remote_service.refresh_pull_request(task_id)
    if record is None:
        raise RepositoryError("PULL_REQUEST_NOT_FOUND", "No Pull Request has been created for this task.", {"task_id": task_id})
    audit_service.record("pr.refresh", "succeeded", f"Reconciled Pull Request #{record.number} as {record.state}", task_id=task_id, run_id=record.run_id, details={"pr_number": record.number, "state": record.state})
    return record


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


@app.post("/api/v1/runs/{run_id}/patches/draft", response_model=DraftPatchResponse)
async def draft_patch(run_id: str, payload: DraftPatchRequest) -> DraftPatchResponse:
    return await agent_service.draft_patch(run_id, payload)


@app.post("/api/v1/runs/{run_id}/loop", response_model=AgentStateResponse)
async def start_agent_loop(run_id: str, payload: AgentLoopRequest) -> AgentStateResponse:
    """Start the autonomous auto-to-commit loop in the background; returns the current agent state."""
    return agent_service.start_loop(run_id, payload)


@app.post("/api/v1/runs/{run_id}/loop/stop", response_model=AgentStateResponse)
async def stop_agent_loop(run_id: str) -> AgentStateResponse:
    return agent_service.stop_loop(run_id)


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
    return await agent_service.run_tests(run_id, payload)


@app.get("/api/v1/runs/{run_id}/events", response_model=AuditEventListResponse)
async def list_run_events(run_id: str) -> AuditEventListResponse:
    task_service.get_by_run(run_id)
    items = audit_service.list_for_run(run_id)
    return AuditEventListResponse(items=items, total=len(items))
