from __future__ import annotations

import difflib
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from .config import Settings
from .models import (
    CheckpointSummary,
    PatchApplyResponse,
    PatchFile,
    PatchPreviewResponse,
    RollbackResponse,
)
from .repository_service import RepositoryError, RepositoryRecord, RepositoryService
from .store import StateStore


class PersistedCheckpoint(BaseModel):
    """Checkpoint metadata; file contents live next to it under the state directory."""

    summary: CheckpointSummary
    applied_hashes: dict[str, str] = {}
    missing: list[str] = []


@dataclass(slots=True)
class PendingPatch:
    id: str
    repository_id: str
    files: list[PatchFile]
    diff: str
    additions: int
    deletions: int
    bytes_changed: int
    requires_delete_confirmation: bool
    original_hashes: dict[str, str]
    run_id: str | None = None


@dataclass(slots=True)
class Checkpoint:
    summary: CheckpointSummary
    backup_dir: Path
    snapshots: dict[str, bytes | None]
    applied_hashes: dict[str, str] = field(default_factory=dict)


class PatchService:
    def __init__(self, repositories: RepositoryService, settings: Settings, store: StateStore | None = None):
        self.repositories = repositories
        self.settings = settings
        self.store = store
        self.checkpoint_dir = (settings.state_dir / "checkpoints").resolve()
        self._patches: dict[str, PendingPatch] = {}
        self._checkpoints: dict[str, Checkpoint] = {}
        self._keys: dict[str, str] = {}
        if store:
            for key, persisted in store.models("checkpoints", PersistedCheckpoint):
                backup_dir = self.checkpoint_dir / persisted.summary.id
                if not backup_dir.is_dir():
                    continue
                snapshots: dict[str, bytes | None] = {}
                for name in persisted.summary.files:
                    if name in persisted.missing:
                        snapshots[name] = None
                    else:
                        snapshot = self._backup_path(backup_dir, name)
                        snapshots[name] = snapshot.read_bytes() if snapshot.is_file() else b""
                checkpoint = Checkpoint(
                    summary=persisted.summary,
                    backup_dir=backup_dir,
                    snapshots=snapshots,
                    applied_hashes=persisted.applied_hashes,
                )
                self._checkpoints[persisted.summary.id] = checkpoint
                self._keys[persisted.summary.id] = key

    def _persist(self, checkpoint: Checkpoint) -> None:
        if not self.store:
            return
        key = self._keys.setdefault(checkpoint.summary.id, self.store.next_key("checkpoints"))
        self.store.put(
            "checkpoints",
            key,
            PersistedCheckpoint(
                summary=checkpoint.summary,
                applied_hashes=checkpoint.applied_hashes,
                missing=[name for name, content in checkpoint.snapshots.items() if content is None],
            ),
            checkpoint.summary.created_at.isoformat(),
        )

    def _backup_path(self, backup_dir: Path, relative_path: str) -> Path:
        target = (backup_dir / relative_path).resolve()
        if not target.is_relative_to(backup_dir):
            raise RepositoryError("INVALID_CHECKPOINT_PATH", "A checkpoint file escapes its backup directory.")
        return target

    def list_checkpoints(self, checkpoint_ids: list[str]) -> list[CheckpointSummary]:
        return [self._checkpoints[item].summary for item in checkpoint_ids if item in self._checkpoints]

    def patch_repository_id(self, patch_id: str) -> str:
        patch = self._patches.get(patch_id)
        if patch is None:
            raise RepositoryError("PATCH_NOT_FOUND", "Patch preview was not found or has expired.")
        return patch.repository_id

    def patch_run_id(self, patch_id: str) -> str | None:
        self.patch_repository_id(patch_id)
        return self._patches[patch_id].run_id

    def checkpoint_run_id(self, checkpoint_id: str) -> str | None:
        self.checkpoint_repository_id(checkpoint_id)
        return self._checkpoints[checkpoint_id].summary.run_id

    def checkpoint_repository_id(self, checkpoint_id: str) -> str:
        checkpoint = self._checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise RepositoryError("CHECKPOINT_NOT_FOUND", "Checkpoint was not found.")
        return checkpoint.summary.repository_id

    def preview(self, repository_id: str, files: list[PatchFile], confirm_delete: bool = False, run_id: str | None = None) -> PatchPreviewResponse:
        repository = self.repositories.get(repository_id)
        prepared: list[PatchFile] = []
        diff_parts: list[str] = []
        additions = 0
        deletions = 0
        bytes_changed = 0
        requires_delete_confirmation = False
        original_hashes: dict[str, str] = {}

        if len({item.path for item in files}) != len(files):
            raise RepositoryError("DUPLICATE_PATCH_PATH", "A patch may modify each file only once.")
        for patch_file in files:
            target = self._safe_target(repository, patch_file.path)
            current = self._read_text(target) if target.exists() else None
            original_hashes[patch_file.path] = self._content_hash(current)
            self._validate_operation(patch_file, current, target)
            if patch_file.expected_hash and self._content_hash(current) != patch_file.expected_hash:
                raise RepositoryError("PATCH_CONFLICT", f"File changed since the patch was prepared: {patch_file.path}")
            if patch_file.operation == "delete" and not confirm_delete:
                requires_delete_confirmation = True
            next_content = None if patch_file.operation == "delete" else patch_file.content or ""
            old_lines = (current or "").splitlines(keepends=True)
            new_lines = (next_content or "").splitlines(keepends=True)
            diff = list(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=f"a/{patch_file.path}",
                    tofile=f"b/{patch_file.path}",
                    lineterm="",
                )
            )
            diff_text = "\n".join(diff)
            if diff_text:
                diff_parts.append(diff_text)
                additions += sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
                deletions += sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
                bytes_changed += len((next_content or "").encode("utf-8"))
            prepared.append(patch_file)

        patch_id = f"patch-{uuid4().hex[:12]}"
        diff_text = "\n".join(diff_parts)
        if len(prepared) > 20 or len(diff_text.encode("utf-8")) > self.settings.max_file_size_bytes * 2:
            raise RepositoryError("PATCH_TOO_LARGE", "Patch exceeds the Phase 1 safety limit.")
        pending = PendingPatch(
            id=patch_id,
            repository_id=repository_id,
            files=prepared,
            diff=diff_text,
            additions=additions,
            deletions=deletions,
            bytes_changed=bytes_changed,
            requires_delete_confirmation=requires_delete_confirmation,
            original_hashes=original_hashes,
            run_id=run_id,
        )
        self._patches[patch_id] = pending
        return PatchPreviewResponse(
            patch_id=patch_id,
            repository_id=repository_id,
            files=[item.path for item in prepared],
            diff=diff_text,
            additions=additions,
            deletions=deletions,
            bytes_changed=bytes_changed,
            requires_delete_confirmation=requires_delete_confirmation,
        )

    def apply(self, patch_id: str, confirm: bool = False) -> PatchApplyResponse:
        patch = self._patches.get(patch_id)
        if patch is None:
            raise RepositoryError("PATCH_NOT_FOUND", "Patch preview was not found or has expired.")
        if patch.requires_delete_confirmation and not confirm:
            raise RepositoryError("DELETE_CONFIRMATION_REQUIRED", "Deleting files requires explicit confirmation.")
        repository = self.repositories.get(patch.repository_id)
        for relative_path, original_hash in patch.original_hashes.items():
            current = self._read_text(self._safe_target(repository, relative_path))
            if self._content_hash(current) != original_hash:
                raise RepositoryError("PATCH_CONFLICT", f"File changed after preview: {relative_path}")
        checkpoint = self._create_checkpoint(repository, patch.files)
        checkpoint.summary.run_id = patch.run_id
        checkpoint.applied_hashes = {item.path: self._content_hash(None if item.operation == "delete" else item.content) for item in patch.files}
        self._persist(checkpoint)
        try:
            for patch_file in patch.files:
                target = self._safe_target(repository, patch_file.path)
                if patch_file.operation == "delete":
                    target.unlink(missing_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                self._atomic_write(target, patch_file.content or "")
        except OSError as exc:
            self._restore_checkpoint(checkpoint)
            raise RepositoryError("PATCH_APPLY_FAILED", "Patch application failed and was rolled back.") from exc
        return PatchApplyResponse(
            patch_id=patch.id,
            checkpoint=checkpoint.summary,
            applied_files=[item.path for item in patch.files],
        )

    def rollback(self, checkpoint_id: str) -> RollbackResponse:
        checkpoint = self._checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise RepositoryError("CHECKPOINT_NOT_FOUND", "Checkpoint was not found.")
        if checkpoint.summary.restored:
            raise RepositoryError("CHECKPOINT_ALREADY_RESTORED", "This checkpoint has already been restored.")
        ordered = list(self._checkpoints.values())
        start = next(index for index, item in enumerate(ordered) if item is checkpoint)
        chain = [item for item in reversed(ordered[start:]) if not item.summary.restored and (item is checkpoint or (checkpoint.summary.run_id and item.summary.run_id == checkpoint.summary.run_id))]
        repository = self.repositories.get(checkpoint.summary.repository_id)
        simulated: dict[str, bytes | None] = {}
        original: dict[str, bytes | None] = {}
        for item in chain:
            for name, saved in item.snapshots.items():
                target = self._safe_target(repository, name)
                if name not in simulated:
                    simulated[name] = target.read_bytes() if target.exists() else None
                    original[name] = simulated[name]
                current = simulated[name]
                if self._content_hash(current.decode("utf-8") if current is not None else None) != item.applied_hashes[name]:
                    raise RepositoryError("ROLLBACK_CONFLICT", "A file changed outside this task. Preserve the external edits before rolling back.", {"path": name})
                simulated[name] = saved
        try:
            for item in chain:
                self._restore_checkpoint(item)
        except OSError as exc:
            for name, content in original.items():
                target = self._safe_target(repository, name)
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    self._atomic_write(target, content)
            raise RepositoryError("ROLLBACK_FAILED", "Rollback failed; original file contents were restored.") from exc
        for item in chain:
            item.summary.restored = True
            self._persist(item)
        return RollbackResponse(checkpoint=checkpoint.summary, restored_files=sorted(original), restored_checkpoint_ids=[item.summary.id for item in chain])

    def _create_checkpoint(self, repository: RepositoryRecord, files: list[PatchFile]) -> Checkpoint:
        checkpoint_id = f"checkpoint-{uuid4().hex[:12]}"
        backup_dir = self.checkpoint_dir / checkpoint_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        snapshots: dict[str, bytes | None] = {}
        for patch_file in files:
            target = self._safe_target(repository, patch_file.path)
            content = target.read_bytes() if target.exists() else None
            snapshots[patch_file.path] = content
            if content is not None:
                backup = self._backup_path(backup_dir, patch_file.path)
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(content)
        summary = CheckpointSummary(
            id=checkpoint_id,
            repository_id=repository.id,
            files=list(snapshots),
            created_at=datetime.now(timezone.utc),
        )
        checkpoint = Checkpoint(summary=summary, backup_dir=backup_dir, snapshots=snapshots)
        self._checkpoints[checkpoint_id] = checkpoint
        self._persist(checkpoint)
        return checkpoint

    def _restore_checkpoint(self, checkpoint: Checkpoint) -> None:
        repository = self.repositories.get(checkpoint.summary.repository_id)
        for relative_path, content in checkpoint.snapshots.items():
            target = self._safe_target(repository, relative_path)
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                self._atomic_write(target, content)

    def _safe_target(self, repository: RepositoryRecord, relative_path: str) -> Path:
        if not relative_path:
            raise RepositoryError("INVALID_PATCH_PATH", "Patch paths must identify a repository file.")
        return self.repositories._safe_repo_path(repository, relative_path)

    def _validate_operation(self, patch_file: PatchFile, current: str | None, target: Path) -> None:
        if patch_file.operation == "create" and current is not None:
            raise RepositoryError("FILE_ALREADY_EXISTS", f"File already exists: {patch_file.path}")
        if patch_file.operation in {"create", "update"} and patch_file.content is None:
            raise RepositoryError("PATCH_CONTENT_REQUIRED", f"Content is required for {patch_file.operation}: {patch_file.path}")
        if patch_file.operation == "update" and current is None and target.exists() is False:
            raise RepositoryError("FILE_NOT_FOUND", f"File does not exist: {patch_file.path}")
        if patch_file.operation == "delete" and current is None:
            raise RepositoryError("FILE_NOT_FOUND", f"File does not exist: {patch_file.path}")
        if patch_file.content is not None and "\x00" in patch_file.content:
            raise RepositoryError("BINARY_FILE_NOT_ALLOWED", "Binary file changes are not supported in Phase 1.")
        if patch_file.content is not None and len(patch_file.content.encode("utf-8")) > self.settings.max_file_size_bytes:
            raise RepositoryError("FILE_TOO_LARGE", f"File exceeds the configured size limit: {patch_file.path}")

    @staticmethod
    def _read_text(path: Path) -> str | None:
        if not path.exists():
            return None
        content = path.read_bytes()
        if b"\x00" in content:
            raise RepositoryError("BINARY_FILE_NOT_ALLOWED", f"Binary file changes are not supported: {path.name}")
        return content.decode("utf-8", errors="replace")

    @staticmethod
    def _content_hash(content: str | None) -> str:
        return hashlib.sha256(content.encode("utf-8") if content is not None else b"\xffmissing").hexdigest()

    @staticmethod
    def _atomic_write(path: Path, content: str | bytes) -> None:
        data = content.encode("utf-8") if isinstance(content, str) else content
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(data)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
