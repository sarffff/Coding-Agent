from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .repository_service import RepositoryError, RepositoryRecord, RepositoryService


@dataclass(slots=True)
class GitSnapshot:
    branch: str
    head: str
    changed_files: list[str]
    clean: bool


class GitService:
    """Controlled local Git operations for task branches and commits."""

    BRANCH_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,80}$")

    def __init__(self, repositories: RepositoryService, settings: Settings):
        self.repositories = repositories
        self.settings = settings

    def snapshot(self, repository_id: str) -> GitSnapshot:
        repository = self.repositories.get(repository_id)
        branch = self._run(repository, ["branch", "--show-current"])[0].strip()
        head = self._run(repository, ["rev-parse", "HEAD"])[0].strip()
        status = self._run(repository, ["status", "--porcelain"])[0]
        changed = [line[3:].strip() for line in status.splitlines() if len(line) >= 4]
        return GitSnapshot(branch=branch, head=head, changed_files=changed, clean=not changed)

    def create_task_branch(self, repository_id: str, branch: str, allow_dirty: bool = False) -> GitSnapshot:
        repository = self.repositories.get(repository_id)
        if not self.BRANCH_RE.fullmatch(branch) or branch.startswith("-") or ".." in branch:
            raise RepositoryError("INVALID_BRANCH_NAME", "Task branch name is invalid.")
        snapshot = self.snapshot(repository_id)
        if not allow_dirty and not snapshot.clean:
            raise RepositoryError("WORKTREE_NOT_CLEAN", "The repository has uncommitted changes; task branch creation was blocked.", {"changed_files": snapshot.changed_files})
        self._run(repository, ["switch", "-c", branch])
        return self.snapshot(repository_id)

    def diff(self, repository_id: str) -> str:
        repository = self.repositories.get(repository_id)
        return self._run(repository, ["diff", "--no-ext-diff", "--"])[0]

    def commit(self, repository_id: str, message: str) -> str:
        repository = self.repositories.get(repository_id)
        if not message.strip() or len(message) > 200:
            raise RepositoryError("INVALID_COMMIT_MESSAGE", "Commit message must be between 1 and 200 characters.")
        snapshot = self.snapshot(repository_id)
        if snapshot.clean:
            raise RepositoryError("NOTHING_TO_COMMIT", "The repository has no changes to commit.")
        self._run(repository, ["add", "--all"])
        self._run(repository, ["commit", "-m", message.strip()])
        return self._run(repository, ["rev-parse", "HEAD"])[0].strip()

    def _run(self, repository: RepositoryRecord, args: list[str]) -> tuple[str, str]:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=repository.path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.git_timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RepositoryError("GIT_COMMAND_FAILED", "The Git command could not be completed.") from exc
        if result.returncode != 0:
            raise RepositoryError("GIT_COMMAND_FAILED", "The Git command was rejected.", {"command": args, "stderr": result.stderr[-2_000:]})
        return result.stdout, result.stderr

