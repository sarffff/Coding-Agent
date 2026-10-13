from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from starlette.concurrency import run_in_threadpool

from .approval_service import ApprovalService, plan_scope_hash
from .audit_service import AuditService
from .git_service import GitService
from .model_provider import ModelProvider
from .models import (
    AgentAction,
    AgentAdvanceRequest,
    AgentAdvanceResponse,
    AgentLoopRequest,
    AgentMode,
    AgentStateResponse,
    ApprovalDecisionRequest,
    ApprovalRequestCreate,
    ApprovalSummary,
    ApprovalType,
    DraftPatchRequest,
    DraftPatchResponse,
    PullRequestCreateRequest,
    SearchRequest,
    TaskIteration,
    TaskSummary,
    TestRunRequest,
    TestRunResponse,
)
from .patch_service import PatchService
from .remote_service import RemoteService
from .repository_service import RepositoryError
from .task_service import TaskService
from .test_service import TestService


@dataclass(frozen=True, slots=True)
class AgentDecision:
    action: AgentAction
    message: str
    can_advance: bool = False
    requires_approval: bool = False
    approval_type: ApprovalType | None = None


@dataclass(slots=True)
class _LoopState:
    task: asyncio.Task
    stop: asyncio.Event
    draft: asyncio.Task | None = None


# ponytail: one asyncio loop per task, tracked module-level (mirrors test_service's reservation).
# A single process serves one desktop user, so a dict is enough; shard by process if multi-tenant.
_ACTIVE_LOOPS: dict[str, _LoopState] = {}
# Hard backstop; the task budget (iterations/remote-calls/failures) is the real limiter.
_LOOP_MAX_STEPS = 60

# Autonomy posture → which approval types the loop auto-grants. Anything NOT listed pauses the
# loop for an inline human approval in chat. push/pr leave the machine, so only "autonomous"
# (an explicit per-task choice) auto-grants them; "standard" still asks a human before publishing.
_STANDARD_GRANT: frozenset[ApprovalType] = frozenset({"plan", "write"})
AUTO_GRANT: dict[AgentMode, frozenset[ApprovalType]] = {
    "conservative": frozenset(),
    "standard": _STANDARD_GRANT,
    "autonomous": frozenset({"plan", "write", "push", "pr"}),
}


class AgentService:
    """Advance one local step, yielding to the existing patch and approval UI."""

    def __init__(
        self,
        tasks: TaskService,
        approvals: ApprovalService,
        tests: TestService,
        git: GitService,
        audit: AuditService,
        remote: RemoteService,
        patch: PatchService,
        models: ModelProvider,
    ) -> None:
        self.tasks = tasks
        self.approvals = approvals
        self.tests = tests
        self.git = git
        self.audit = audit
        self.remote = remote
        self.patch = patch
        self.models = models

    def inspect(self, task_id: str) -> AgentStateResponse:
        task = self.tasks.get(task_id)
        iterations = self.tasks.list_iterations(task_id)
        iteration = iterations[-1] if iterations else None
        decision = self._decide(task, iteration)
        scope = plan_scope_hash(task) if decision.approval_type == "plan" else None
        approval = self._latest_approval(task_id, decision.approval_type, scope)
        return AgentStateResponse(
            task=task,
            action=decision.action,
            can_advance=decision.can_advance,
            requires_approval=decision.requires_approval,
            approval_type=decision.approval_type,
            message=decision.message,
            iteration=iteration,
            pending_approval=approval if approval and approval.status == "pending" else None,
            last_test=iteration.test_result if iteration else None,
            loop_running=self._loop_running(task_id),
        )

    async def advance(self, task_id: str, request: AgentAdvanceRequest) -> AgentAdvanceResponse:
        state = self.inspect(task_id)
        if request.action == "auto" and not state.can_advance:
            return AgentAdvanceResponse(**state.model_dump(), performed_action="noop")
        if not state.can_advance or request.action not in {"auto", state.action}:
            raise RepositoryError(
                "AGENT_ACTION_NOT_AVAILABLE",
                "The requested action is not the task's available next step.",
                {"available_action": state.action, "task_id": task_id},
            )
        self.tasks.assert_recovered(task_id)
        task = state.task
        if state.action == "request_plan_approval":
            approval = self.approvals.create(task.id, task.run_id, ApprovalRequestCreate(
                type="plan", summary=f"Approve the plan for: {task.goal}"[:500], scope_hash=plan_scope_hash(task),
            ))
            self.audit.record("approval.request", "succeeded", "Requested plan approval", task_id=task_id, run_id=task.run_id,
                              details={"approval_id": approval.id, "type": "plan", "source": "agent"})
        elif state.action in {"start_iteration", "start_repair"}:
            if state.action == "start_repair":
                failure = state.iteration.failure_summary if state.iteration else None
                feedback = request.feedback or f"{task.goal}\n\nRepair the latest test failure:\n{failure or 'Review the test output.'}"
                iteration = self.tasks.start_repair(task_id, feedback[:4_000])
                event = "iteration.repair"
            else:
                iteration = self.tasks.start_iteration(task_id, request.goal)
                event = "iteration.start"
            self.audit.record(event, "succeeded", f"Started coding iteration {iteration.number}", task_id=task_id, run_id=task.run_id,
                              details={"iteration_id": iteration.id, "number": iteration.number, "source": "agent"})
        elif state.action == "run_tests":
            await self.run_tests(task.run_id, request)
        else:
            raise RepositoryError("AGENT_ACTION_NOT_AVAILABLE", "This action requires manual review.")

        result = AgentAdvanceResponse(**self.inspect(task_id).model_dump(), performed_action=state.action)
        self.audit.record("agent.advance", "succeeded", f"Agent performed {state.action}", task_id=task_id, run_id=task.run_id,
                          details={"action": state.action, "next_action": result.action})
        return result

    # --- Autonomous loop (posture-gated) ---------------------------------------------------
    # Drives _decide in the background. The task's mode (conservative/standard/autonomous)
    # decides which approval gates auto-grant via _gate(); anything not auto-granted pauses the
    # loop with a pending approval for the human to decide inline in chat. Every write still
    # goes through the same preview gate + checkpoint; the task budget breaker is the hard stop;
    # protected paths stay blocked by patch.preview. Only "autonomous" auto-grants push/pr.

    def _loop_running(self, task_id: str) -> bool:
        state = _ACTIVE_LOOPS.get(task_id)
        return bool(state and not state.task.done())

    def start_loop(self, run_id: str, request: AgentLoopRequest) -> AgentStateResponse:
        task = self.tasks.get_by_run(run_id)
        if task.track == "cowork":
            raise RepositoryError("COWORK_NO_LOOP", "Cowork conversations are chat-only; they do not run the coding loop.", {"task_id": task.id})
        self.tasks.assert_recovered(task.id)
        if self._loop_running(task.id):
            raise RepositoryError("LOOP_ALREADY_RUNNING", "An agent loop is already running for this task.", {"task_id": task.id})
        if request.mode is not None:
            self.tasks.set_mode(task.id, request.mode)  # first start sets the posture; resumes (mode=None) keep it
        stop = asyncio.Event()
        loop_task = asyncio.create_task(self._run_loop(task.id, task.run_id, request.feedback, stop))
        _ACTIVE_LOOPS[task.id] = _LoopState(task=loop_task, stop=stop)
        loop_task.add_done_callback(lambda _done, tid=task.id: _ACTIVE_LOOPS.pop(tid, None))
        self.audit.record("agent.loop", "started", "Agent loop started", task_id=task.id, run_id=task.run_id, details={"source": "chat", "mode": self.tasks.get(task.id).mode})
        return self.inspect(task.id)

    def stop_loop(self, run_id: str) -> AgentStateResponse:
        task = self.tasks.get_by_run(run_id)
        state = _ACTIVE_LOOPS.get(task.id)
        if state and not state.task.done():
            state.stop.set()
            if state.draft and not state.draft.done():
                state.draft.cancel()
            self.tests.cancel(task.run_id)  # interrupt any in-flight validation so the loop can break promptly
        return self.inspect(task.id)

    async def _run_loop(self, task_id: str, run_id: str, feedback: str | None, stop: asyncio.Event) -> None:
        outcome, detail = "succeeded", "Agent loop finished"
        try:
            for _ in range(_LOOP_MAX_STEPS):
                if stop.is_set():
                    detail = "Agent loop stopped by request"
                    break
                self.tasks.assert_recovered(task_id)
                state = self.inspect(task_id)
                action = state.action
                if action in {"request_plan_approval", "await_plan_approval"}:
                    if not self._gate(state.task, "plan", plan_scope_hash(state.task)):
                        detail = f"Agent loop paused for plan approval: {state.message}"
                        break
                elif action == "create_branch":
                    self._auto_create_branch(state.task)
                elif action in {"start_iteration", "start_repair"}:
                    await self.advance(task_id, AgentAdvanceRequest(action=action, feedback=feedback))
                    feedback = None
                elif action == "review_patch":
                    if state.task.mode == "conservative":
                        detail = f"Agent loop paused for patch review: {state.message}"
                        break
                    await self._draft_and_apply(run_id, feedback, stop)
                    feedback = None
                elif action == "run_tests":
                    await self.advance(task_id, AgentAdvanceRequest(action="run_tests"))
                elif action == "review_commit":
                    if not self._auto_commit(state.task):
                        detail = f"Agent loop paused for commit approval: {state.message}"
                        break
                elif action == "review_push":
                    if not self._gate(state.task, "push", None):
                        detail = f"Agent loop paused for push approval: {state.message}"
                        break
                    await self._perform_push(state.task)
                elif action == "review_pr":
                    if not self._gate(state.task, "pr", None):
                        detail = f"Agent loop paused for PR approval: {state.message}"
                        break
                    await self._perform_pr(state.task)
                else:
                    # await_pr / completed / manual_review / paused / … → hand back to the human.
                    detail = f"Agent loop handed off at {action}: {state.message}"
                    break
            else:
                outcome, detail = "failed", "Agent loop reached its step limit"
        except RepositoryError as exc:
            outcome, detail = "failed", f"Agent loop stopped: {exc.message}"
        except Exception:  # never let a background loop die silently and leave loop_running stuck
            outcome, detail = "failed", "Agent loop stopped on an unexpected error"
        finally:
            status = self.tasks.get(task_id).status
            self.audit.record("agent.loop", outcome, detail, task_id=task_id, run_id=run_id, details={"status": status})

    def _decide(self, task: TaskSummary, iteration: TaskIteration | None) -> AgentDecision:
        if task.track == "cowork":
            return AgentDecision("manual_review", "This is a Cowork chat; it does not run the coding loop.")
        if task.status in {"completed", "done"}:
            return AgentDecision("completed", "The task is complete.")
        if task.status == "cancelled":
            return AgentDecision("cancelled", "The task was cancelled.")
        budget = self.tasks.get_budget(task.id)
        if budget.exhausted_reason:
            return AgentDecision("manual_review", f"Task budget exhausted: {budget.exhausted_reason}. Review or cancel this task before starting a new one.")
        if task.requires_recovery:
            return AgentDecision("recover", "Confirm recovery before continuing this task.")
        if task.status == "paused":
            return AgentDecision("paused", "Resume the task after reviewing the latest checkpoint.")
        if self.tests.is_running(task.run_id):
            return AgentDecision("wait_for_tests", "Wait for validation to finish; pause or cancel to stop it.")
        if task.status == "pr_created":
            pull_request = self.remote.get_pull_request(task.id)
            if pull_request and pull_request.state == "merged":
                return AgentDecision("completed", "The Pull Request was merged.")
            if pull_request and pull_request.state == "closed":
                return AgentDecision("manual_review", "The Pull Request is closed. Review the remote result before continuing.")
            return AgentDecision("await_pr", "Refresh the pull request in the publishing panel to follow review and merge.")
        if task.status not in {"awaiting_approval", "coding", "testing", "failed", "repairing", "ready_for_pr"}:
            return AgentDecision("manual_review", task.next_action or "Review the current task state.")

        if task.status == "awaiting_approval" or (task.status == "coding" and not iteration):
            scope = plan_scope_hash(task)
            approval = self._latest_approval(task.id, "plan", scope)
            if approval and approval.status in {"rejected", "request_changes", "cancelled"}:
                return AgentDecision("manual_review", f"Plan approval is {approval.status}. Review the feedback before requesting approval again.")
            if not self.approvals.has_approved(task.id, "plan", scope):
                if approval and approval.status == "pending":
                    return AgentDecision("await_plan_approval", "Review and approve the task plan before coding.", approval_type="plan", requires_approval=True)
                return AgentDecision("request_plan_approval", "Request approval for the current task plan.", can_advance=True, approval_type="plan", requires_approval=True)

        if not task.repository_id:
            return AgentDecision("connect_repository", "Choose a registered repository when creating a coding task.")
        has_git = self.tasks.repositories.get(task.repository_id).has_git
        if has_git:
            if not task.task_branch:
                if iteration:
                    return AgentDecision("manual_review", "This task started without a task branch. Review the changes and start a new task on a clean branch.")
                return AgentDecision("create_branch", "Create a task branch in the Git panel before starting the coding loop.")
            snapshot = self.git.snapshot(task.repository_id)
            if snapshot.branch != task.task_branch:
                return AgentDecision("manual_review", "Switch back to the task branch before continuing.")

        if task.status == "awaiting_approval" or task.status == "coding" and (not iteration or iteration.rolled_back):
            return AgentDecision("start_iteration", "Start a coding iteration and prepare a patch for review.", can_advance=True)
        if not iteration or iteration.rolled_back:
            return AgentDecision("manual_review", "Review the current iteration and checkpoint before continuing.")
        if task.status == "coding":
            if iteration.patch_id:
                return AgentDecision("run_tests", "Run validation for the applied patch.", can_advance=True)
            return AgentDecision("review_patch", "Prepare a patch, review its diff, then confirm application in the Patch panel.")
        if task.status == "testing":
            return AgentDecision("run_tests", "Run validation for the current iteration.", can_advance=True)
        if task.status in {"failed", "repairing"}:
            result = iteration.test_result
            if not result or result.status != "failed":
                return AgentDecision("manual_review", "Resolve the validation environment, timeout or cancellation before retrying tests.")
            if result.validation_hash is None:
                return AgentDecision("run_tests", "Task files changed during validation. Rerun tests before repairing.", can_advance=True)
            current_hash = self.git.validation_hash(task.repository_id, self.tasks.changed_files(task.id))
            if current_hash != result.validation_hash:
                return AgentDecision("run_tests", "Task files changed after the failure. Rerun tests before creating a repair iteration.", can_advance=True)
            return AgentDecision("start_repair", "Start a repair iteration using the latest test failure.", can_advance=True)

        # A commit changes HEAD, so its persisted record supersedes the pre-commit validation hash.
        if has_git:
            commits = self.remote.list_commits(task.id)
            prepared = self.git.preview(task.repository_id, self.tasks.changed_files(task.id), "")
            if commits and snapshot.head == commits[-1].commit_sha and not prepared.ready:
                branches = self.remote.list_remote_branches(task.id)
                pushed = any(item.branch == task.task_branch and item.pushed_sha == snapshot.head for item in branches)
                kind: ApprovalType = "pr" if pushed else "push"
                return AgentDecision(
                    "review_pr" if pushed else "review_push",
                    "Review and create a Draft PR in the publishing panel." if pushed else "Review and push the approved commit in the publishing panel.",
                    requires_approval=not self.approvals.has_approved(task.id, kind), approval_type=kind,
                )
        result = iteration.test_result
        validation_hash = self.git.validation_hash(task.repository_id, self.tasks.changed_files(task.id))
        verified = any(run.kind != "static" and run.status == "passed" and run.validation_hash == validation_hash for run in iteration.test_runs)
        if not result or result.status != "passed" or result.validation_hash != validation_hash or not verified:
            return AgentDecision("run_tests", "Run project or selected tests for the current changes before final review.", can_advance=True)
        if not has_git:
            return AgentDecision("manual_review", "Local validation passed. Review the changed files or restore a checkpoint in the Patch panel; Git publishing is unavailable for this folder.")
        if not prepared.ready:
            return AgentDecision("manual_review", "Validation passed, but there are no task changes to commit. Review the diff.")
        return AgentDecision("review_commit", "Preview the commit and approve its exact scope in the Git panel.", requires_approval=True, approval_type="write")

    async def run_tests(self, run_id: str, request: TestRunRequest) -> TestRunResponse:
        """Shared validation path for the manual test API and Agent advancement."""
        task = self.tasks.get_by_run(run_id)
        self.tasks.assert_recovered(task.id)
        self.tasks.assert_budget_available(task.id)
        if not task.repository_id:
            raise RepositoryError("TASK_REPOSITORY_REQUIRED", "Task must be connected to a repository before running tests.")
        files = self.tasks.changed_files(task.id)
        before = self.git.validation_hash(task.repository_id, files)
        self.tests.reserve(run_id)
        try:
            self.tasks.begin_test_run(run_id)
        except Exception:
            self.tests.release(run_id)
            raise
        result = await run_in_threadpool(self.tests.run, run_id, task.repository_id, request.kind, request.timeout_seconds, request.target)
        try:
            after = self.git.validation_hash(task.repository_id, files)
        except RepositoryError:
            after = None
        if before == after:
            result.validation_hash = before
        elif result.status == "passed":
            result.status = "failed"
            result.stderr += "\nTask files changed during testing. Run validation again."
        self.tasks.record_test_result(run_id, result)
        self.audit.record("tests.run", "succeeded" if result.status == "passed" else "failed", f"Test run {result.status}", task_id=task.id, run_id=run_id,
                          details={"test_id": result.id, "kind": result.kind, "command": result.command, "exit_code": result.exit_code,
                                   "failed_tests": [failure.model_dump() for failure in result.failed_tests]})
        return result

    async def draft_patch(self, run_id: str, request: DraftPatchRequest) -> DraftPatchResponse:
        """Draft whole-file edits with the model, then preview them through the SAME gate a hand-typed patch uses.

        The model never writes to disk: its output flows into patch.preview (protected-path,
        size and binary checks) and the user still reviews the diff and confirms apply.
        """
        task = self.tasks.get_by_run(run_id)
        self.tasks.assert_recovered(task.id)
        if not task.repository_id:
            raise RepositoryError("TASK_REPOSITORY_REQUIRED", "Connect a repository before drafting a patch.")
        self.tasks.assert_patch_allowed(run_id)
        iterations = self.tasks.list_iterations(task.id)
        iteration = iterations[-1]
        failed_iteration = next((item for item in reversed(iterations) if not item.rolled_back and item.test_result and item.test_result.status == "failed"), None)
        failed_test = failed_iteration.test_result if failed_iteration else None
        failure = "\n".join(part for part in (failed_iteration.failure_summary, failed_test.stdout, failed_test.stderr) if part)[:12_000] if failed_iteration else None
        goal = request.feedback or iteration.goal
        context_files = self._gather_context(task.repository_id, request.context_paths, task.id, goal, failed_test)
        repository = self.tasks.repositories.get(task.repository_id)
        baselines = {repository.path / path: self.patch._content_hash(content) for path, content in context_files}
        # ponytail: model calls share the task's remote-call budget (one counter with git-host calls); split if it ever matters.
        self.tasks.consume_remote_call(task.id)
        succeeded = False
        try:
            proposal = await self.models.propose_patch(goal, context_files, failure)
            succeeded = True
        finally:
            self.tasks.record_remote_result(task.id, succeeded)
        files = []
        for proposed in proposal.files:
            target = self.tasks.repositories._safe_repo_path(repository, proposed.path.replace("\\", "/"))
            baseline = baselines.get(target)
            if baseline is None and proposed.operation != "create":
                raise RepositoryError("MODEL_CONTEXT_REQUIRED", "Include the file in context_paths and draft again before modifying it.", {"path": proposed.path})
            files.append(proposed.model_copy(update={
                "path": target.relative_to(repository.path).as_posix(),
                "expected_hash": baseline if baseline is not None else self.patch._content_hash(None),
            }))
        preview = self.patch.preview(task.repository_id, files, run_id=run_id)
        self.audit.record(
            "patch.draft", "succeeded", f"Model drafted a patch for {len(preview.files)} file(s)",
            task_id=task.id, run_id=run_id,
            # rationale persisted here (capped) so a chat transcript can show the model's reasoning after the fact.
            details={"patch_id": preview.patch_id, "files": preview.files, "model": self.models.settings.llm_model,
                     "rationale": proposal.rationale[:2_000]},
        )
        return DraftPatchResponse(**preview.model_dump(), rationale=proposal.rationale)

    async def chat(self, task_id: str, message: str) -> str:
        """One Cowork conversational turn: record the user message, ask the model, record the reply.

        Cowork never touches git, patches or the loop — it is a plain chat that can be
        given a short read-only summary of an attached folder for context.
        """
        task = self.tasks.get(task_id)
        if task.track != "cowork":
            raise RepositoryError("CHAT_NOT_ALLOWED", "Chat is only available for Cowork conversations.")
        self.tasks.consume_remote_call(task.id)
        self.audit.record("chat.user", "succeeded", message, task_id=task.id, run_id=task.run_id)
        history: list[tuple[str, str]] = []
        for event in self.audit.list_for_run(task.run_id):
            if event.action == "chat.user":
                history.append(("user", event.summary))
            elif event.action == "chat.assistant":
                history.append(("assistant", event.summary))
        succeeded = False
        try:
            reply = (await self.models.chat(history, self._cowork_system(task))).strip() or "(no reply)"
            succeeded = True
        finally:
            self.tasks.record_remote_result(task.id, succeeded)
        self.audit.record("chat.assistant", "succeeded", reply, task_id=task.id, run_id=task.run_id)
        return reply

    def _cowork_system(self, task: TaskSummary) -> str | None:
        """A compact, read-only summary of the attached folder, or None when no folder is attached."""
        if not task.repository_id:
            return None
        try:
            ctx = self.tasks.repositories.context(task.repository_id)
        except RepositoryError:
            return None
        languages = ", ".join(f"{name}×{count}" for name, count in list(ctx.languages.items())[:6]) or "unknown"
        entries = ", ".join(ctx.entry_files[:10]) or "none"
        return (
            "The user attached a local folder for context (read-only):\n"
            f"- languages: {languages}\n- package manager: {ctx.package_manager or 'none'}\n"
            f"- entry files: {entries}\n- file count: {ctx.file_count}"
        )

    def _gather_context(self, repository_id: str, context_paths: list[str], task_id: str, goal: str, failure: TestRunResponse | None) -> list[tuple[str, str]]:
        repositories = self.tasks.repositories
        explicit = [path.replace("\\", "/") for path in re.findall(r"[\w./\\-]+\.[\w.-]+", goal)]
        failing = [item.path for item in (failure.failed_tests if failure else []) if item.path]
        ordered = [*context_paths, *explicit, *failing, *self.tasks.changed_files(task_id)]
        # ponytail: bounded keyword search plus entry/test fallbacks; no index or model lookup needed.
        ignored = {"fix", "add", "update", "change", "test", "tests", "file", "files", "return", "with", "that", "from", "this", "code", "local", "the", "for", "and"}
        terms = dict.fromkeys(word for word in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}|[\u4e00-\u9fff]{2,}", goal.lower()) if word not in ignored)
        for term in list(terms)[:3]:
            matches, _ = repositories.search(repository_id, SearchRequest(query=term[:100], max_results=10))
            ordered.extend(match.path for match in matches)
        context = repositories.context(repository_id)
        entries, _ = repositories.tree(repository_id, max_depth=6)
        tests = [entry.path for entry in entries if entry.kind == "file" and (
            entry.name.startswith("test_") or any(token in entry.path for token in (".test.", ".spec.", "/tests/", "/__tests__/"))
        )]
        ordered.extend([*context.entry_files[:3], *tests[:3], *context.config_files[:2]])
        ordered.extend(entry.path for entry in entries if entry.kind == "file" and entry.language and PurePosixPath(entry.path).suffix not in {".md", ".json"})
        files: list[tuple[str, str]] = []
        budget = self.tasks.settings.llm_max_context_bytes
        for path in dict.fromkeys(ordered):
            try:
                source = repositories.read_file(repository_id, path)
            except RepositoryError:
                continue  # missing, protected or binary — skip; the model still gets the goal
            size = len(source.content.encode("utf-8"))
            # Whole-file edits must see complete files; truncation could silently remove unseen code.
            if size > budget or any(name == source.path for name, _ in files):
                continue
            files.append((source.path, source.content))
            budget -= size
            if len(files) >= self.tasks.settings.llm_max_context_files or budget <= 0:
                break
        return files

    def _gate(self, task: TaskSummary, approval_type: ApprovalType, scope: str | None) -> bool:
        """Posture gate: True if the loop may proceed through this approval, else pause for a human.

        Auto-grants the types in AUTO_GRANT[task.mode]; otherwise proceeds only if a human has
        already approved. When neither holds, it ensures a pending approval exists (so chat can
        show it inline) and returns False so the loop stops and hands off.
        """
        if approval_type in AUTO_GRANT.get(task.mode, _STANDARD_GRANT):
            self._auto_approve(task, approval_type, scope)
            return True
        if self.approvals.has_approved(task.id, approval_type, scope):
            return True
        self._ensure_pending(task, approval_type, scope)
        return False

    def _ensure_pending(self, task: TaskSummary, approval_type: ApprovalType, scope: str | None) -> None:
        """Create a pending approval (if one isn't already open) so the human can decide it inline."""
        existing = self._latest_approval(task.id, approval_type, scope)
        if existing and existing.status == "pending":
            return
        pending = self.approvals.create(task.id, task.run_id, ApprovalRequestCreate(
            type=approval_type, summary=f"Approve {approval_type} for: {task.goal}"[:500], scope_hash=scope,
        ))
        self.audit.record("approval.request", "succeeded", f"Agent requested {approval_type} approval",
                          task_id=task.id, run_id=task.run_id, details={"approval_id": pending.id, "type": approval_type, "source": "agent"})

    def _auto_approve(self, task: TaskSummary, approval_type: ApprovalType, scope: str | None) -> None:
        """Grant an approval on the agent's behalf when the posture auto-grants it.

        Recorded in the audit log as decided_by="agent-loop" so the auto-grant is never invisible.
        Which types reach here is governed by AUTO_GRANT[task.mode]; push/pr only in "autonomous".
        """
        pending = self._latest_approval(task.id, approval_type, scope)
        if not pending or pending.status != "pending":
            pending = self.approvals.create(task.id, task.run_id, ApprovalRequestCreate(
                type=approval_type, summary=f"Agent requested {approval_type} approval ({task.mode})."[:500], scope_hash=scope,
            ))
            self.audit.record("approval.request", "succeeded", f"Agent requested {approval_type} approval",
                              task_id=task.id, run_id=task.run_id, details={"approval_id": pending.id, "type": approval_type, "source": "agent"})
        approved = self.approvals.decide(
            pending.id, ApprovalDecisionRequest(decision="approve", reason=f"Auto-approved by the '{task.mode}' autonomy posture."),
            decided_by="agent-loop",
        )
        self.audit.record("approval.decision", "succeeded", f"Agent auto-approved {approval_type}",
                          task_id=task.id, run_id=task.run_id, details={"approval_id": approved.id, "type": approval_type, "source": "agent"})

    def _auto_create_branch(self, task: TaskSummary) -> None:
        summary = self.tasks.get(task.id)  # live summary object; setting task_branch here is persisted by update_git_snapshot
        snapshot = self.git.create_task_branch(task.repository_id, f"forge/{task.id}")
        summary.task_branch = snapshot.branch
        self.tasks.update_git_snapshot(task.id, snapshot.branch, snapshot.head, snapshot.changed_files)
        self.audit.record("git.branch.create", "succeeded", f"Agent created task branch {snapshot.branch}",
                          task_id=task.id, run_id=task.run_id, details={"branch": snapshot.branch, "head": snapshot.head, "source": "agent"})

    async def _draft_and_apply(self, run_id: str, feedback: str | None, stop: asyncio.Event) -> None:
        """The review_patch seam the manual flow leaves to a human: draft → preview → apply, same gate + checkpoint."""
        task = self.tasks.get_by_run(run_id)
        loop = _ACTIVE_LOOPS[task.id]
        loop.draft = asyncio.create_task(self.draft_patch(run_id, DraftPatchRequest(feedback=feedback) if feedback else DraftPatchRequest()))
        try:
            preview = await loop.draft
        except asyncio.CancelledError:
            if stop.is_set():
                return
            raise
        finally:
            loop.draft = None
        if stop.is_set():
            return
        self.tasks.assert_patch_allowed(run_id)
        result = self.patch.apply(preview.patch_id, True)  # the model's output already passed patch.preview's protected-path/size gate
        self.tasks.mark_patch_applied(run_id, result.patch_id, result.checkpoint.id, result.applied_files)
        self.audit.record("patch.apply", "succeeded", f"Agent applied patch to {len(result.applied_files)} file(s)",
                          task_id=task.id, run_id=run_id, details={"patch_id": result.patch_id, "checkpoint_id": result.checkpoint.id, "source": "agent"})

    def _auto_commit(self, task: TaskSummary) -> bool:
        """Commit the validated changes once the write gate clears. Mirrors the /commits endpoint.

        Returns False (without committing) when the posture needs a human to approve the commit,
        leaving a pending 'write' approval for the chat to show inline. _decide already verified tests.
        """
        repository_id = task.repository_id
        files = self.tasks.changed_files(task.id)
        body = (task.goal.strip().splitlines()[0] if task.goal.strip() else "agent change")[:180]
        message = f"{task.id}: {body}"  # ponytail: templated from the goal; a model-drafted commit message can replace this later
        prepared = self.git.preview(repository_id, files, message)
        if not self._gate(task, "write", prepared.scope_hash):
            return False
        head = self.git.commit(repository_id, files, message, prepared.scope_hash, task.task_branch or "")
        snapshot = self.git.snapshot(repository_id)
        self.tasks.update_git_snapshot(task.id, snapshot.branch, head, snapshot.changed_files)
        self.remote.record_commit(task.id, task.run_id, snapshot.branch, head, message, prepared.scope_hash)
        self.audit.record("git.commit.create", "succeeded", "Agent created approved task commit", task_id=task.id, run_id=task.run_id,
                          details={"branch": snapshot.branch, "head": head, "files": prepared.changed_files, "scope_hash": prepared.scope_hash, "source": "agent"})
        return True

    async def _perform_push(self, task: TaskSummary) -> None:
        """Push the approved commit (autonomous posture, or after an inline push approval)."""
        record = await self.remote.push_branch(task.id)
        self.audit.record("git.push", "succeeded", f"Agent pushed {record.branch}", task_id=task.id, run_id=task.run_id,
                          details={"branch": record.branch, "pushed_sha": record.pushed_sha, "source": "agent"})

    async def _perform_pr(self, task: TaskSummary) -> None:
        """Open a Draft PR with the default preview title/body (autonomous, or after an inline PR approval)."""
        preview = self.remote.preview_pull_request(task.id)
        pr = await self.remote.create_pull_request(task.id, PullRequestCreateRequest(
            title=preview.title, body=preview.body, target_branch=preview.target_branch or "main", draft=True,
        ))
        self.audit.record("git.pr.create", "succeeded", f"Agent opened draft PR #{pr.number}", task_id=task.id, run_id=task.run_id,
                          details={"pr_number": pr.number, "url": pr.html_url, "source": "agent"})

    def _latest_approval(self, task_id: str, kind: ApprovalType | None, scope: str | None = None) -> ApprovalSummary | None:
        if kind is None:
            return None
        return next((item for item in self.approvals.list_for_task(task_id) if item.type == kind and (scope is None or item.scope_hash == scope)), None)
