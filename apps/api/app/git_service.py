from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from .config import Settings
from .file_policy import is_protected_path
from .process_env import child_env, is_process_startup_failure
from .repository_service import RepositoryError, RepositoryRecord, RepositoryService


@dataclass(slots=True)
class GitSnapshot:
    branch: str
    head: str
    changed_files: list[str]
    clean: bool


@dataclass(slots=True)
class PreparedCommit:
    branch: str
    head: str
    tree: str
    diff: str
    changed_files: list[str]
    excluded_files: list[str]
    message: str
    scope_hash: str
    validation_hash: str

    @property
    def ready(self) -> bool:
        return bool(self.changed_files)


class GitService:
    """Build and publish exactly the reviewed tree, preserving the user's index."""

    BRANCH_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,80}$")
    PROTECTED_BRANCHES = {"main", "master", "develop", "production"}

    def __init__(self, repositories: RepositoryService, settings: Settings):
        self.repositories = repositories
        self.settings = settings
        self._locks: dict[str, RLock] = {}

    def lock(self, repository_id: str) -> RLock:
        return self._locks.setdefault(repository_id, RLock())

    def snapshot(self, repository_id: str) -> GitSnapshot:
        repository = self.repositories.get(repository_id)
        branch = self._run(repository, ["branch", "--show-current"]).strip()
        head = self._run(repository, ["rev-parse", "HEAD"]).strip()
        status = self._run(repository, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
        fields = iter(status.split("\0"))
        changed: set[str] = set()
        for field in fields:
            if len(field) < 4:
                continue
            changed.add(field[3:])
            if "R" in field[:2] or "C" in field[:2]:
                changed.add(next(fields, ""))
        changed.discard("")
        return GitSnapshot(branch=branch, head=head, changed_files=sorted(changed), clean=not changed)

    def create_task_branch(self, repository_id: str, branch: str) -> GitSnapshot:
        repository = self.repositories.get(repository_id)
        with self.lock(repository_id):
            if not self.BRANCH_RE.fullmatch(branch) or ".." in branch or branch in self.PROTECTED_BRANCHES:
                raise RepositoryError("INVALID_BRANCH_NAME", "Choose a valid, unprotected task branch name.")
            self._run(repository, ["check-ref-format", "--branch", branch])
            snapshot = self.snapshot(repository_id)
            if not snapshot.clean:
                raise RepositoryError("WORKTREE_NOT_CLEAN", "Create the task branch before editing files. Existing changes must be preserved.", {"changed_files": snapshot.changed_files})
            self._run(repository, ["switch", "-c", branch])
            return self.snapshot(repository_id)

    def preview(self, repository_id: str, files: list[str], message: str) -> PreparedCommit:
        repository = self.repositories.get(repository_id)
        with self.lock(repository_id), self._temporary_directory("git-") as directory:
            env = {"GIT_INDEX_FILE": str(Path(directory) / "preview.index")}
            snapshot = self.snapshot(repository_id)
            selected = self._safe_files(repository, files)
            self._run(repository, ["read-tree", snapshot.head], env=env)
            tracked = set(self._run(repository, ["ls-tree", "-r", "--name-only", "-z", snapshot.head]).split("\0"))
            existing = [name for name in selected if (repository.path / name).exists() or name in tracked]
            if existing:
                self._run(repository, ["add", "--all", "--", *existing], env=env)
            tree = self._run(repository, ["write-tree"], env=env).strip()
            diff = self._run(repository, ["diff", "--cached", "--no-ext-diff", "--no-textconv", "--no-renames", "--full-index", snapshot.head, "--"], env=env)
            if len(diff.encode("utf-8")) > self.settings.max_file_size_bytes * 2:
                raise RepositoryError("PATCH_TOO_LARGE", "The commit diff exceeds the review limit.")
            changed = [name for name in self._run(repository, ["diff", "--cached", "--name-only", "--no-renames", "-z", snapshot.head, "--"], env=env).split("\0") if name]
            scope = {"repository_id": repository_id, "branch": snapshot.branch, "head": snapshot.head, "tree": tree}
            validation_hash = self._hash(scope)
            return PreparedCommit(
                branch=snapshot.branch, head=snapshot.head, tree=tree, diff=diff,
                changed_files=changed, excluded_files=sorted(set(snapshot.changed_files) - set(changed)),
                message=message.strip(), scope_hash=self._hash({**scope, "message": message.strip()}),
                validation_hash=validation_hash,
            )

    def commit(self, repository_id: str, files: list[str], message: str, scope_hash: str, branch: str) -> str:
        repository = self.repositories.get(repository_id)
        if not message.strip() or len(message) > 256:
            raise RepositoryError("INVALID_COMMIT_MESSAGE", "Commit message must be between 1 and 256 characters.")
        with self.lock(repository_id):
            prepared = self.preview(repository_id, files, message)
            if prepared.scope_hash != scope_hash:
                raise RepositoryError("COMMIT_PREVIEW_STALE", "The branch, HEAD, files or message changed. Preview and approve the commit again.")
            if not branch or prepared.branch != branch or branch in self.PROTECTED_BRANCHES:
                raise RepositoryError("TASK_BRANCH_REQUIRED", "Commit only on the branch created for this task.")
            if not prepared.ready:
                raise RepositoryError("NOTHING_TO_COMMIT", "No task changes remain to commit.")
            with self._locked_index(repository) as (index_path, lock_path, env, lock_state):
                staged = [name for name in self._run(repository, ["diff", "--cached", "--name-only", "--no-renames", "-z", prepared.head, "--", *prepared.changed_files], env=env).split("\0") if name]
                if staged and self._run(repository, ["diff", "--cached", "--name-only", "-z", prepared.tree, "--", *staged], env=env):
                    raise RepositoryError("TASK_INDEX_CONFLICT", "A task file has independently staged changes. Resolve its staging before committing.", {"files": staged})
                # Update only task entries in a copy of the real index; keep other staging intact.
                removals = "".join("0 " + "0" * len(prepared.head) + "\t" + name + "\0" for name in prepared.changed_files)
                entries = self._run(repository, ["ls-tree", "-r", "-z", prepared.tree, "--", *prepared.changed_files])
                additions = "".join(entry.split(" ", 1)[0] + " " + entry.split(" ", 2)[2] + "\0" for entry in entries.split("\0") if entry)
                self._run(repository, ["update-index", "-z", "--index-info"], env=env, input_text=removals + additions)
                lock_path.write_bytes(Path(env["GIT_INDEX_FILE"]).read_bytes())
                # Plumbing commits have no hooks that could replace the approved tree.
                head = self._run(repository, ["commit-tree", prepared.tree, "-p", prepared.head], input_text=message.strip() + "\n").strip()
                reference = "refs/heads/" + branch
                if self.snapshot(repository_id).branch != branch:
                    raise RepositoryError("COMMIT_PREVIEW_STALE", "The checked-out branch changed during commit.")
                self._run(repository, ["update-ref", "-m", "forge: task commit", reference, head, prepared.head])
                try:
                    os.replace(lock_path, index_path)
                    lock_state["published"] = True
                except OSError as exc:
                    self._run(repository, ["update-ref", reference, prepared.head, head])
                    raise RepositoryError("GIT_INDEX_WRITE_FAILED", "The index could not be updated; the branch update was reverted.") from exc
                return head

    @contextmanager
    def _temporary_directory(self, prefix: str):
        # Scratch lives in the state directory: an inherited TMP may point at a
        # sandbox-owned path this process cannot write to.
        self.settings.state_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"forge-{prefix}-", dir=self.settings.state_dir) as directory:
            yield directory

    @contextmanager
    def _locked_index(self, repository: RepositoryRecord):
        raw_path = self._run(repository, ["rev-parse", "--git-path", "index"]).strip()
        index_path = (repository.path / raw_path).resolve()
        lock_path = index_path.with_name(index_path.name + ".lock")
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RepositoryError("GIT_BUSY", "Another Git operation owns the index. Retry after it finishes.") from exc
        os.close(descriptor)
        lock_state = {"published": False}
        try:
            with self._temporary_directory("index") as directory:
                copied_index = Path(directory) / "index"
                env = {"GIT_INDEX_FILE": str(copied_index)}
                if index_path.exists():
                    shutil.copyfile(index_path, copied_index)
                else:
                    self._run(repository, ["read-tree", "HEAD"], env=env)
                yield index_path, lock_path, env, lock_state
        finally:
            if not lock_state["published"]:
                lock_path.unlink(missing_ok=True)

    def _safe_files(self, repository: RepositoryRecord, files: list[str]) -> list[str]:
        selected: set[str] = set()
        for name in files:
            relative = Path(name)
            if not name or relative.is_absolute() or ".." in relative.parts or any(ord(char) < 32 for char in name) or is_protected_path(relative):
                raise RepositoryError("PROTECTED_PATH", "A task commit contains an unsupported or protected path.")
            target = repository.path / relative
            if not target.resolve().is_relative_to(repository.path):
                raise RepositoryError("PATH_OUTSIDE_REPOSITORY", "A task file escapes its repository.")
            if any(parent.is_symlink() for parent in [target, *target.parents] if parent != repository.path and parent.is_relative_to(repository.path)):
                raise RepositoryError("SYMLINK_NOT_ALLOWED", "Symlink task files cannot be committed.")
            if target.exists():
                if not target.is_file() or target.stat().st_size > self.settings.max_file_size_bytes:
                    raise RepositoryError("FILE_TOO_LARGE", "Only bounded regular task files can be committed.")
                content = target.read_bytes()
                if b"\0" in content:
                    raise RepositoryError("BINARY_FILE_NOT_ALLOWED", "Binary task files cannot be committed.")
            selected.add(relative.as_posix())
        if len(selected) > 20:
            raise RepositoryError("PATCH_TOO_LARGE", "A task commit may contain at most 20 files.")
        return sorted(selected)

    @staticmethod
    def _hash(value: dict[str, str]) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()

    def _run(self, repository: RepositoryRecord, args: list[str], *, env: dict[str, str] | None = None, input_text: str | None = None) -> str:
        environment = child_env({"GIT_LITERAL_PATHSPECS": "1", **(env or {})})
        try:
            result = subprocess.run(
                ["git", *args], cwd=repository.path, capture_output=True, input=input_text,
                text=True, encoding="utf-8", errors="replace", env=environment,
                timeout=self.settings.git_timeout_seconds, check=False, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RepositoryError("GIT_COMMAND_FAILED", "The Git command could not be completed.") from exc
        if is_process_startup_failure(result.returncode):
            raise RepositoryError("GIT_UNAVAILABLE", "The API process could not start Git. Restart the API from a normal shell.", {"command": args, "exit_code": result.returncode})
        if result.returncode != 0:
            raise RepositoryError("GIT_COMMAND_FAILED", "The Git command was rejected.", {"command": args, "stderr": result.stderr[-2_000:]})
        return result.stdout
