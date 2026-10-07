from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel

from .approval_service import ApprovalService
from .audit_service import AuditService
from .config import Settings
from .git_service import PROTECTED_BRANCHES, GitService
from .models import (
    CommitRecord,
    PullRequestCreateRequest,
    PullRequestPreviewResponse,
    PullRequestRecord,
    RemoteBranchRecord,
    RemoteTargetResponse,
    TaskPublishStateResponse,
    TaskSummary,
)
from .process_env import child_env
from .remote_provider import FakeRemoteProvider, GitHubRemoteProvider, REMOTE_NAME_RE, RemoteProvider
from .repository_service import RepositoryError, RepositoryService
from .store import StateStore
from .task_service import TaskService


class RemoteService:
    """Publishes approved work outward: local commits, task branch push, Draft PR."""

    def __init__(
        self,
        repositories: RepositoryService,
        tasks: TaskService,
        git: GitService,
        approvals: ApprovalService,
        audit: AuditService,
        settings: Settings,
        store: StateStore,
        provider: RemoteProvider | None = None,
    ) -> None:
        self.repositories = repositories
        self.tasks = tasks
        self.git = git
        self.approvals = approvals
        self.audit = audit
        self.settings = settings
        self.store = store
        if provider is not None:
            self.provider = provider
        elif settings.remote_provider == "fake":
            self.provider = FakeRemoteProvider(settings)
        else:
            self.provider = GitHubRemoteProvider(settings)

        self._commits: dict[str, CommitRecord] = {}
        self._remote_branches: dict[str, RemoteBranchRecord] = {}
        self._pull_requests: dict[str, PullRequestRecord] = {}
        self._keys: dict[str, str] = {}
        for key, record in store.models("commits", CommitRecord):
            self._commits[record.id] = record
            self._keys[f"commits:{record.id}"] = key
        for key, record in store.models("remote_branches", RemoteBranchRecord):
            self._remote_branches[record.id] = record
            self._keys[f"remote_branches:{record.id}"] = key
        for key, record in store.models("pull_requests", PullRequestRecord):
            self._pull_requests[record.id] = record
            self._keys[f"pull_requests:{record.id}"] = key

    def _put(self, scope: str, record_id: str, record: BaseModel, updated_at: datetime) -> None:
        key = self._keys.setdefault(f"{scope}:{record_id}", self.store.next_key(scope))
        self.store.put(scope, key, record, updated_at.isoformat())

    def _persist_commit(self, record: CommitRecord) -> None:
        self._commits[record.id] = record
        self._put("commits", record.id, record, record.created_at)

    def _persist_branch(self, record: RemoteBranchRecord) -> None:
        self._remote_branches[record.id] = record
        self._put("remote_branches", record.id, record, record.created_at)

    def _persist_pull_request(self, record: PullRequestRecord) -> None:
        self._pull_requests[record.id] = record
        self._put("pull_requests", record.id, record, record.updated_at)

    def record_commit(
        self,
        task_id: str,
        run_id: str,
        branch: str,
        commit_sha: str,
        commit_message: str,
        scope_hash: str,
    ) -> CommitRecord:
        record = CommitRecord(
            id=f"commit-{uuid4().hex[:12]}",
            task_id=task_id,
            run_id=run_id,
            branch=branch,
            commit_sha=commit_sha,
            commit_message=commit_message,
            scope_hash=scope_hash,
            created_at=datetime.now(timezone.utc),
        )
        self._persist_commit(record)
        self.audit.record(
            "commit.record",
            "succeeded",
            f"Recorded approved commit {commit_sha[:8]} on {branch}",
            task_id=task_id,
            run_id=run_id,
            details={"commit_sha": commit_sha, "branch": branch, "message": commit_message},
        )
        return record

    def list_commits(self, task_id: str) -> list[CommitRecord]:
        return [record for record in self._commits.values() if record.task_id == task_id]

    def list_remote_branches(self, task_id: str) -> list[RemoteBranchRecord]:
        return [record for record in self._remote_branches.values() if record.task_id == task_id]

    def get_pull_request(self, task_id: str) -> PullRequestRecord | None:
        records = [record for record in self._pull_requests.values() if record.task_id == task_id]
        return records[-1] if records else None

    def publish_state(self, task_id: str) -> TaskPublishStateResponse:
        task = self.tasks.get(task_id)
        return TaskPublishStateResponse(
            task_id=task_id,
            branch=task.task_branch,
            head=task.head,
            remote_url=self.peek_remote_url(task.repository_id),
            commits=self.list_commits(task_id),
            remote_branches=self.list_remote_branches(task_id),
            pull_request=self.get_pull_request(task_id),
        )

    def peek_remote_url(self, repository_id: str | None, remote_name: str = "origin") -> str | None:
        if not repository_id:
            return None
        try:
            return self.get_remote_url(repository_id, remote_name)
        except RepositoryError:
            return None

    def get_remote_url(self, repository_id: str, remote_name: str = "origin") -> str:
        if not REMOTE_NAME_RE.fullmatch(remote_name):
            raise RepositoryError("INVALID_REMOTE_NAME", "Choose a valid Git remote name.")
        repository = self.repositories.get(repository_id)
        result = subprocess.run(
            ["git", "remote", "get-url", remote_name],
            cwd=str(repository.path),
            env=child_env(),
            capture_output=True,
            text=True,
        )
        url = result.stdout.strip()
        if not url:
            raise RepositoryError(
                "REMOTE_NOT_CONFIGURED",
                f"Repository '{repository.name}' has no '{remote_name}' remote. Configure it with `git remote add {remote_name} <url>` first.",
            )
        return url

    async def remote_target(self, task_id: str, remote_name: str = "origin") -> RemoteTargetResponse:
        task = self.tasks.get(task_id)
        repository_id = self._require_repository(task)
        remote_url = self.get_remote_url(repository_id, remote_name)
        info = await self.provider.validate_repository(remote_url)
        return RemoteTargetResponse(
            provider=self.settings.remote_provider,
            remote_url=remote_url,
            host=info.host,
            owner=info.owner,
            repo=info.repo,
            default_branch=info.default_branch,
            allowed_hosts=self.settings.allowed_remote_hosts,
            allowed_repositories=self.settings.allowed_repositories,
        )

    async def push_branch(self, task_id: str, remote_name: str = "origin") -> RemoteBranchRecord:
        task = self.tasks.get(task_id)
        repository_id = self._require_repository(task)
        branch = task.task_branch
        if not branch:
            raise RepositoryError("NO_TASK_BRANCH", "Create the task branch before pushing it.")
        if branch in PROTECTED_BRANCHES:
            raise RepositoryError("PROTECTED_BRANCH_PUSH_PROHIBITED", f"Pushing to protected branch '{branch}' is prohibited.")
        if not self.approvals.has_approved(task_id, "push"):
            raise RepositoryError("PUSH_APPROVAL_REQUIRED", "Pushing requires a current 'push' approval.", {"type": "push"})

        commits = self.list_commits(task_id)
        if not commits:
            raise RepositoryError("NO_APPROVED_COMMIT", "Publish an approved local commit before pushing.")
        snapshot = self.git.snapshot(repository_id)
        if snapshot.branch != branch:
            raise RepositoryError("TASK_BRANCH_NOT_CHECKED_OUT", f"Repository is on '{snapshot.branch}', not the task branch '{branch}'.")
        if snapshot.head != commits[-1].commit_sha:
            raise RepositoryError("REMOTE_HEAD_MOVED", "HEAD moved after the approved commit. Preview and approve the commit again.")

        remote_url = self.get_remote_url(repository_id, remote_name)
        self.provider.validate_remote_url(remote_url)
        pushed_sha = await self.provider.push_task_branch(self.repositories.get(repository_id).path, remote_url, branch, remote=remote_name)

        record = RemoteBranchRecord(
            id=f"rbranch-{uuid4().hex[:12]}",
            task_id=task_id,
            run_id=task.run_id,
            repository_id=repository_id,
            branch=branch,
            remote_url=remote_url,
            pushed_sha=pushed_sha,
            created_at=datetime.now(timezone.utc),
        )
        self._persist_branch(record)
        self.audit.record(
            "branch.push",
            "succeeded",
            f"Pushed {branch} to {record.remote_url}",
            task_id=task_id,
            run_id=task.run_id,
            details={"branch": branch, "pushed_sha": pushed_sha, "remote_url": remote_url},
        )
        self.tasks.transition(task_id, "ready_for_pr", next_action="Review the pushed branch, then create a Draft Pull Request.")
        return record

    def preview_pull_request(self, task_id: str, target_branch: str = "main") -> PullRequestPreviewResponse:
        task = self.tasks.get(task_id)
        commits = self.list_commits(task_id)
        pushed = [record for record in self.list_remote_branches(task_id) if record.branch == task.task_branch]
        iterations = self.tasks.list_iterations(task_id)
        files = sorted(self.tasks.changed_files(task_id))
        validation_passed = bool(iterations and iterations[-1].test_result and iterations[-1].test_result.status == "passed")
        latest_commit = commits[-1] if commits else None
        file_lines = [f"- `{name}`" for name in files] or ["- None"]
        body = "\n".join(
            [
                f"**Task goal**: {task.goal}",
                "",
                "### Modified files",
                *file_lines,
                "",
                "### Validation",
                f"- Tests passed: **{'yes' if validation_passed else 'not yet'}**",
                f"- Iterations: {len(iterations)}",
                "",
                "### Audit trail",
                f"- Branch: `{task.task_branch or 'none'}`",
                f"- Commit: `{latest_commit.commit_sha if latest_commit else 'none'}`",
                f"- Rollback: restore the iteration checkpoint for `{latest_commit.commit_sha[:10] if latest_commit else 'n/a'}` with `POST /api/v1/runs/{task.run_id}/rollback`.",
                "- Published by the Forge coding agent through approval-gated Git plumbing.",
            ]
        )
        return PullRequestPreviewResponse(
            title=f"[Forge] {task.goal}"[:200],
            body=body,
            source_branch=task.task_branch or "",
            target_branch=target_branch,
            draft=True,
            files=files,
            risk_level="low" if len(files) <= 5 else "medium",
            ready=bool(pushed and latest_commit and validation_passed and task.task_branch),
            validation_passed=validation_passed,
            commit_sha=latest_commit.commit_sha if latest_commit else None,
        )

    async def create_pull_request(self, task_id: str, payload: PullRequestCreateRequest) -> PullRequestRecord:
        task = self.tasks.get(task_id)
        branch = task.task_branch
        if not branch:
            raise RepositoryError("NO_TASK_BRANCH", "Create the task branch before opening a Pull Request.")
        if payload.target_branch == branch:
            raise RepositoryError("INVALID_TARGET_BRANCH", "A Pull Request cannot target its own source branch.")
        if not self.approvals.has_approved(task_id, "pr"):
            raise RepositoryError("PR_APPROVAL_REQUIRED", "Creating a Pull Request requires a current 'pr' approval.", {"type": "pr"})

        pushed = [record for record in self.list_remote_branches(task_id) if record.branch == branch]
        if not pushed:
            raise RepositoryError("BRANCH_NOT_PUSHED", "Push the task branch before creating a Pull Request.")
        remote_url = pushed[-1].remote_url

        await self.provider.validate_repository(remote_url)
        if not await self.provider.check_branch_exists(remote_url, branch):
            raise RepositoryError("REMOTE_SOURCE_BRANCH_MISSING", f"'{branch}' is no longer present on the remote.")
        if not await self.provider.check_branch_exists(remote_url, payload.target_branch):
            raise RepositoryError("REMOTE_TARGET_BRANCH_MISSING", f"Target branch '{payload.target_branch}' is not present on the remote.")

        pr_data = await self.provider.create_pull_request(
            remote_url=remote_url,
            source_branch=branch,
            target_branch=payload.target_branch,
            title=payload.title,
            body=payload.body,
            draft=payload.draft,
        )
        now = datetime.now(timezone.utc)
        record = PullRequestRecord(
            id=f"pr-{uuid4().hex[:12]}",
            task_id=task_id,
            run_id=task.run_id,
            repository_id=task.repository_id or "",
            provider=self.settings.remote_provider,
            number=pr_data.number,
            title=pr_data.title,
            body=pr_data.body,
            html_url=pr_data.html_url,
            state="draft" if pr_data.draft else "open",
            source_branch=pr_data.source_branch,
            target_branch=pr_data.target_branch,
            draft=pr_data.draft,
            created_at=now,
            updated_at=now,
        )
        self._persist_pull_request(record)
        self.audit.record(
            "pr.create",
            "succeeded",
            f"Created {'draft ' if pr_data.draft else ''}Pull Request #{pr_data.number}",
            task_id=task_id,
            run_id=task.run_id,
            details={"pr_number": pr_data.number, "html_url": pr_data.html_url, "draft": pr_data.draft},
        )
        self.tasks.transition(task_id, "pr_created", next_action=f"Pull Request #{pr_data.number} is open for review.")
        return record

    async def refresh_pull_request(self, task_id: str) -> PullRequestRecord | None:
        """Reconcile a lost or stale response by reading the Pull Request back from the remote."""
        record = self.get_pull_request(task_id)
        if record is None:
            return None
        pushed = [item for item in self.list_remote_branches(task_id) if item.branch == record.source_branch]
        if not pushed:
            raise RepositoryError("BRANCH_NOT_PUSHED", "No push record to reconcile this Pull Request against.")
        remote = await self.provider.get_pull_request(pushed[-1].remote_url, record.number)
        if remote is None:
            record.state = "closed"
        else:
            record.state = "draft" if remote.draft else remote.state
            record.title = remote.title
            record.body = remote.body
            record.target_branch = remote.target_branch
        record.updated_at = datetime.now(timezone.utc)
        self._persist_pull_request(record)
        return record

    def _require_repository(self, task: TaskSummary) -> str:
        if not task.repository_id:
            raise RepositoryError("TASK_REPOSITORY_REQUIRED", "Task must be connected to a repository before remote operations.")
        return task.repository_id
