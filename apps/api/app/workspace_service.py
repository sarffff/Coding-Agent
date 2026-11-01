from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from .config import Settings
from .file_policy import is_protected_path
from .models import PatchFile
from .patch_service import PatchService
from .repository_service import RepositoryError, RepositoryService
from .store import StateStore
from .test_service import _generated


class WorkspaceRecord(BaseModel):
    task_id: str
    source_repository_id: str
    repository_id: str
    source_path: Path
    path: Path
    kind: Literal["git", "local"]
    branch: str | None = None
    token: str
    created_at: datetime


class CommandSnapshotManifest(BaseModel):
    task_id: str
    repository_id: str
    token: str
    snapshot_id: str
    files: dict[str, str]
    directories: list[str]
    created_at: datetime


class CommandDraftRecord(BaseModel):
    task_id: str
    repository_id: str
    token: str
    files: list[PatchFile]
    updated_at: datetime


@dataclass
class WorkspaceSnapshot:
    task_id: str
    repository_id: str
    token: str
    files: dict[str, bytes]
    directories: set[str] = field(default_factory=set)
    observed: tuple[dict[str, bytes], set[str]] | None = None
    snapshot_id: str = ""


class WorkspaceService:
    """Own task copies; commands still execute with the local API user's rights."""

    def __init__(self, repositories: RepositoryService, settings: Settings, store: StateStore | None = None):
        self.repositories = repositories
        self.settings = settings
        self.store = store
        self.workspace_dir = Path(settings.state_dir).expanduser().absolute() / "workspaces"
        self._records: dict[str, WorkspaceRecord] = {}
        self._command_snapshots: dict[str, CommandSnapshotManifest] = {}
        self._command_drafts: dict[str, CommandDraftRecord] = {}
        # ponytail: creation/deletion share one process lock; shared leases are needed for multiple workers.
        self._lock = RLock()
        if store:
            self._records = {record.task_id: record for _, record in store.models("workspaces", WorkspaceRecord)}
            self._command_snapshots = {record.task_id: record for _, record in store.models("command_snapshots", CommandSnapshotManifest)}
            self._command_drafts = {record.task_id: record for _, record in store.models("command_drafts", CommandDraftRecord)}

    @staticmethod
    def _is_link(path: Path) -> bool:
        try:
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
            return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
        except FileNotFoundError:
            return False

    def _check_root(self) -> Path:
        if any(self._is_link(path) for path in (self.workspace_dir, *self.workspace_dir.parents)):
            raise RepositoryError("WORKSPACE_PATH_UNSAFE", "The managed workspace directory must not contain symlinks or junctions.")
        return self.workspace_dir.resolve()

    def _owner(self, record: WorkspaceRecord) -> Path:
        root = self._check_root()
        if len(record.token) != 32 or any(character not in "0123456789abcdef" for character in record.token):
            raise RepositoryError("WORKSPACE_OWNERSHIP_INVALID", "The task workspace ownership record is invalid.")
        owner = root / record.token
        if record.path != owner / "repository" or self._is_link(owner) or self._is_link(record.path):
            raise RepositoryError("WORKSPACE_PATH_UNSAFE", "The task workspace path is no longer the directory created for this task.")
        marker = owner / ".owner.json"
        try:
            if self._is_link(marker):
                raise ValueError("linked ownership marker")
            saved = json.loads(marker.read_text(encoding="utf-8"))
            if saved != {"task_id": record.task_id, "token": record.token, "source_repository_id": record.source_repository_id}:
                raise ValueError("ownership mismatch")
        except (OSError, ValueError) as exc:
            raise RepositoryError("WORKSPACE_OWNERSHIP_INVALID", "The task workspace ownership marker is missing or changed.") from exc
        return owner

    def get(self, task_id: str) -> WorkspaceRecord | None:
        record = self._records.get(task_id)
        if record:
            self._owner(record)
            if not record.path.is_dir():
                raise RepositoryError("WORKSPACE_UNAVAILABLE", "The task workspace is missing; restore it before continuing.")
        return record

    def source_repository_id_for(self, repository_id: str) -> str | None:
        return next((record.source_repository_id for record in self._records.values() if record.repository_id == repository_id), None)

    def save_command_draft(self, task_id: str, files: list[PatchFile]) -> None:
        record = self.get(task_id)
        if not record:
            raise RepositoryError("WORKSPACE_NOT_FOUND", "This task does not have an isolated workspace.")
        if not files:
            self.clear_command_draft(task_id)
            return
        repository = self.repositories.get(record.repository_id)
        for item in files:
            self.repositories._safe_repo_path(repository, item.path)
        draft = CommandDraftRecord(task_id=task_id, repository_id=record.repository_id, token=record.token,
                                   files=files, updated_at=datetime.now(timezone.utc))
        if self.store:
            self.store.put("command_drafts", task_id, draft, draft.updated_at.isoformat())
        self._command_drafts[task_id] = draft

    def command_draft(self, task_id: str) -> list[PatchFile]:
        record = self.get(task_id)
        draft = self._command_drafts.get(task_id)
        if not record or not draft:
            return []
        if draft.repository_id != record.repository_id or draft.token != record.token:
            raise RepositoryError("WORKSPACE_SNAPSHOT_STALE", "The saved command draft belongs to another task workspace.")
        return draft.files

    def clear_command_draft(self, task_id: str) -> None:
        self.get(task_id)
        if self.store:
            self.store.delete("command_drafts", task_id)
        self._command_drafts.pop(task_id, None)

    def create(self, task_id: str, source_repository_id: str) -> WorkspaceRecord:
        with self._lock:
            existing = self.get(task_id)
            if existing:
                if existing.source_repository_id != source_repository_id:
                    raise RepositoryError("WORKSPACE_SOURCE_MISMATCH", "This task already has a workspace for another repository.")
                return existing
            source = self.repositories.get(source_repository_id)
            root = self._check_root()
            if source.path.is_relative_to(root):
                raise RepositoryError("WORKSPACE_SOURCE_INVALID", "Choose the original project as the task source.")
            files, directories = self._scan(source.path, read=False)
            token = uuid4().hex
            owner = root / token
            path = owner / "repository"
            owner.mkdir(parents=True)
            (owner / ".owner.json").write_text(json.dumps({"task_id": task_id, "token": token, "source_repository_id": source_repository_id}), encoding="utf-8")
            branch = f"codex/task-{hashlib.sha256(task_id.encode()).hexdigest()[:12]}-{token[:8]}" if source.has_git else None
            worktree_created = False
            repository_id = None
            try:
                if source.has_git:
                    # Do not check out protected tracked files or run repository checkout filters.
                    self.repositories._run_git(source.path, "worktree", "add", "--no-checkout", "-b", branch, str(path), "HEAD")
                    worktree_created = True
                    actual_root = self.repositories._run_git(path, "rev-parse", "--show-toplevel") if path.is_dir() else ""
                    if not actual_root or Path(actual_root).resolve() != path or not (path / ".git").is_file():
                        raise RepositoryError("WORKSPACE_CREATE_FAILED", "Git did not create the isolated task worktree.")
                    self.repositories._run_git(path, "read-tree", "HEAD")
                    index_name = self.repositories._run_git(path, "rev-parse", "--git-path", "index")
                    if (self.repositories._run_git(path, "branch", "--show-current") != branch
                            or not index_name or not (path / index_name).is_file()):
                        raise RepositoryError("WORKSPACE_CREATE_FAILED", "Git did not select the isolated task branch.")
                else:
                    path.mkdir()
                for relative in sorted(directories):
                    (path / relative).mkdir(parents=True, exist_ok=True)
                for relative in files:
                    original = self.repositories._safe_repo_path(source, relative)
                    target = path / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, target)
                registered = self.repositories.register(str(path), name=f"{source.name} · {task_id}")
                repository_id = registered.id
                record = WorkspaceRecord(task_id=task_id, source_repository_id=source_repository_id,
                                         repository_id=repository_id, source_path=source.path, path=path,
                                         kind="git" if source.has_git else "local", branch=branch,
                                         token=token, created_at=datetime.now(timezone.utc))
                if self.store:
                    self.store.put("workspaces", task_id, record, record.created_at.isoformat())
                self._records[task_id] = record
                return record
            except BaseException:
                if repository_id:
                    self.repositories.delete(repository_id)
                if worktree_created:
                    try:
                        self.repositories._run_git(source.path, "worktree", "remove", "--force", str(path))
                        self.repositories._run_git(source.path, "branch", "-D", branch)
                    except RepositoryError:
                        pass
                if owner.resolve() == root / token and not self._is_link(owner):
                    shutil.rmtree(owner)
                raise

    def delete(self, task_id: str) -> bool:
        with self._lock:
            record = self._records.get(task_id)
            if not record:
                return False
            owner = self._owner(record)
            if record.kind == "git":
                registered = self.repositories._run_git(record.source_path, "worktree", "list", "--porcelain", "-z")
                if record.path.exists() or any(
                    entry.startswith("worktree ") and Path(entry[9:]).resolve() == record.path
                    for entry in registered.split("\0")
                ):
                    self.repositories._run_git(record.source_path, "worktree", "remove", "--force", str(record.path))
                if record.path.exists():
                    raise RepositoryError("WORKSPACE_CLEANUP_FAILED", "Git did not remove the isolated task worktree.")
            shutil.rmtree(owner)
            try:
                self.repositories.delete(record.repository_id)
            except RepositoryError as exc:
                if exc.code != "REPOSITORY_NOT_FOUND":
                    raise
            if self.store:
                self.store.delete("workspaces", task_id)
                self.store.delete("command_snapshots", task_id)
                self.store.delete("command_drafts", task_id)
            self._command_snapshots.pop(task_id, None)
            self._command_drafts.pop(task_id, None)
            del self._records[task_id]
            return True

    def _scan(self, root: Path, *, read: bool = True) -> tuple[dict[str, bytes], set[str]]:
        """Exclude protected paths, links and the existing validation-generated-file policy."""
        files: dict[str, bytes] = {}
        directories: set[str] = set()
        state = Path(self.settings.state_dir).expanduser().resolve()
        exclude_state = state != root and state.is_relative_to(root)
        if self._is_link(root) or root.resolve() != root or not root.is_dir():
            raise RepositoryError("WORKSPACE_PATH_UNSAFE", "The workspace source must remain an accessible regular directory.")

        def failed(error: OSError) -> None:
            raise error

        try:
            for current, names, filenames in os.walk(root, onerror=failed):
                base = Path(current)
                relative_base = base.relative_to(root)

                def allowed(name: str) -> bool:
                    path = base / name
                    return not (is_protected_path(relative_base / name) or _generated(name)
                                or self._is_link(path) or (exclude_state and path.resolve().is_relative_to(state)))

                names[:] = sorted(name for name in names if allowed(name))
                directories.update((relative_base / name).as_posix() for name in names)
                for name in sorted(filenames):
                    if not allowed(name):
                        continue
                    path = base / name
                    if not path.is_file():
                        raise RepositoryError("WORKSPACE_INPUT_UNAVAILABLE", "Task workspace inputs must be regular files.")
                    files[(relative_base / name).as_posix()] = path.read_bytes() if read else b""
                if len(files) + len(directories) > self.settings.max_tree_entries:
                    raise RepositoryError("WORKSPACE_SCOPE_TOO_LARGE", "The task workspace exceeds the configured repository entry limit.")
        except OSError as exc:
            raise RepositoryError("WORKSPACE_INPUT_UNAVAILABLE", "The task workspace files could not be read.") from exc
        return files, directories

    def snapshot(self, task_id: str) -> WorkspaceSnapshot:
        record = self.get(task_id)
        if not record:
            raise RepositoryError("WORKSPACE_NOT_FOUND", "This task does not have an isolated workspace.")
        if self.pending_snapshot(task_id):
            raise RepositoryError("COMMAND_SNAPSHOT_PENDING", "Recover the previous command snapshot before running another command.")
        files, directories = self._scan(record.path)
        snapshot_id = uuid4().hex
        manifest = CommandSnapshotManifest(task_id=task_id, repository_id=record.repository_id, token=record.token,
                                           snapshot_id=snapshot_id,
                                           files={name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                                           directories=sorted(directories), created_at=datetime.now(timezone.utc))
        owner = self._owner(record)
        temporary = owner / f"snapshot-{snapshot_id}"
        backup_dir = owner / "command-snapshot"
        temporary.mkdir()
        try:
            (temporary / "files").mkdir()
            for name, data in files.items():
                (temporary / "files" / hashlib.sha256(name.encode()).hexdigest()).write_bytes(data)
            (temporary / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
            os.replace(temporary, backup_dir)
            if self.store:
                self.store.put("command_snapshots", task_id, manifest, manifest.created_at.isoformat())
            self._command_snapshots[task_id] = manifest
        finally:
            if temporary.exists() and not self._is_link(temporary):
                shutil.rmtree(temporary)
        return WorkspaceSnapshot(task_id, record.repository_id, record.token, files, directories, snapshot_id=snapshot_id)

    def pending_snapshot(self, task_id: str) -> WorkspaceSnapshot | None:
        record = self.get(task_id)
        if not record:
            return None
        owner = self._owner(record)
        backup_dir = owner / "command-snapshot"
        expected = self._command_snapshots.get(task_id)
        if not backup_dir.exists() and expected is None and not self._is_link(backup_dir):
            return None
        try:
            if self._is_link(backup_dir) or self._is_link(backup_dir / "manifest.json") or self._is_link(backup_dir / "files"):
                raise ValueError("linked command backup")
            saved = CommandSnapshotManifest.model_validate_json((backup_dir / "manifest.json").read_text(encoding="utf-8"))
            if expected is not None and expected != saved:
                raise ValueError("command manifest changed")
            if saved.task_id != task_id or saved.repository_id != record.repository_id or saved.token != record.token:
                raise ValueError("command snapshot ownership mismatch")
            for name in [*saved.files, *saved.directories]:
                relative = Path(name)
                if not name or relative.is_absolute() or ".." in relative.parts or is_protected_path(relative):
                    raise ValueError("invalid backup path")
            files: dict[str, bytes] = {}
            for name, digest in saved.files.items():
                backup = backup_dir / "files" / hashlib.sha256(name.encode()).hexdigest()
                if self._is_link(backup):
                    raise ValueError("linked command file backup")
                data = backup.read_bytes()
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("command backup digest mismatch")
                files[name] = data
        except (OSError, ValueError) as exc:
            raise RepositoryError("COMMAND_SNAPSHOT_UNAVAILABLE", "Command backups are missing or damaged; restore the original backups before recovering this command.",
                                  {"task_id": task_id}) from exc
        self._command_snapshots[task_id] = saved
        return WorkspaceSnapshot(task_id, record.repository_id, record.token, files, set(saved.directories), snapshot_id=saved.snapshot_id)

    def _snapshot_workspace(self, snapshot: WorkspaceSnapshot) -> WorkspaceRecord:
        record = self.get(snapshot.task_id)
        if not record or record.repository_id != snapshot.repository_id or record.token != snapshot.token:
            raise RepositoryError("WORKSPACE_SNAPSHOT_STALE", "The command snapshot belongs to a different task workspace.")
        saved = self.pending_snapshot(snapshot.task_id)
        if saved is None or saved.snapshot_id != snapshot.snapshot_id:
            raise RepositoryError("WORKSPACE_SNAPSHOT_STALE", "The command snapshot is no longer pending for this task workspace.")
        snapshot.files, snapshot.directories = saved.files, saved.directories
        return record

    def diff(self, snapshot: WorkspaceSnapshot) -> list[PatchFile]:
        record = self._snapshot_workspace(snapshot)
        current, directories = self._scan(record.path)
        snapshot.observed = current, directories
        files: list[PatchFile] = []
        for name in sorted(snapshot.files.keys() | current.keys()):
            previous, content = snapshot.files.get(name), current.get(name)
            if previous == content:
                continue
            for data in (previous, content):
                if data is None:
                    continue
                if len(data) > self.settings.max_file_size_bytes:
                    raise RepositoryError("FILE_TOO_LARGE", f"Command change exceeds the patch file limit: {name}")
                if b"\x00" in data:
                    raise RepositoryError("BINARY_FILE_NOT_ALLOWED", f"Command binary changes cannot be previewed: {name}")
                try:
                    data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise RepositoryError("BINARY_FILE_NOT_ALLOWED", f"Command changes require UTF-8 text: {name}") from exc
            files.append(PatchFile(path=name, operation="delete" if content is None else "create" if previous is None else "update",
                                   content=content.decode("utf-8") if content is not None else None,
                                   expected_hash=PatchService._content_hash(previous)))
        return files

    def restore(self, snapshot: WorkspaceSnapshot) -> list[str]:
        record = self._snapshot_workspace(snapshot)
        current, directories = self._scan(record.path)
        if snapshot.observed is not None and snapshot.observed != (current, directories):
            raise RepositoryError("WORKSPACE_RESTORE_CONFLICT", "The workspace changed after command inspection; preserve these external edits before restoring.")
        names = sorted(name for name in snapshot.files.keys() | current.keys() if snapshot.files.get(name) != current.get(name))
        repository = self.repositories.get(record.repository_id)
        targets = {name: self.repositories._safe_repo_path(repository, name) for name in names}
        if any(target.exists() and not target.is_file() for target in targets.values()):
            raise RepositoryError("WORKSPACE_RESTORE_CONFLICT", "A command file path is now a directory; inspect it before restoring.")
        try:
            for name in sorted(snapshot.directories):
                self.repositories._safe_repo_path(repository, name).mkdir(parents=True, exist_ok=True)
            for name, target in targets.items():
                content = snapshot.files.get(name)
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    PatchService._atomic_write(target, content)
            for name in sorted(directories - snapshot.directories, key=lambda item: len(Path(item).parts), reverse=True):
                directory = self.repositories._safe_repo_path(repository, name)
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
        except OSError as exc:
            raise RepositoryError("WORKSPACE_RESTORE_FAILED", "The command snapshot could not be fully restored; retry after checking workspace access.") from exc
        backup_dir = self._owner(record) / "command-snapshot"
        if self._is_link(backup_dir):
            raise RepositoryError("COMMAND_SNAPSHOT_UNAVAILABLE", "The command snapshot directory changed while restoring.")
        shutil.rmtree(backup_dir)
        if self.store:
            self.store.delete("command_snapshots", snapshot.task_id)
        self._command_snapshots.pop(snapshot.task_id, None)
        return names
