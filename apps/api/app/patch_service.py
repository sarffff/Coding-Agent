from __future__ import annotations

import difflib
import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import Settings
from .models import (
    CheckpointSummary,
    PatchApplyResponse,
    PatchFile,
    PatchPreviewResponse,
    RollbackResponse,
)
from .repository_service import IGNORED_DIRECTORIES, RepositoryError, RepositoryRecord, RepositoryService


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


@dataclass(slots=True)
class Checkpoint:
    summary: CheckpointSummary
    backup_dir: Path
    snapshots: dict[str, bytes | None]


class PatchService:
    def __init__(self, repositories: RepositoryService, settings: Settings):
        self.repositories = repositories
        self.settings = settings
        self._patches: dict[str, PendingPatch] = {}
        self._checkpoints: dict[str, Checkpoint] = {}

    def patch_repository_id(self, patch_id: str) -> str:
        patch = self._patches.get(patch_id)
        if patch is None:
            raise RepositoryError("PATCH_NOT_FOUND", "Patch preview was not found or has expired.")
        return patch.repository_id

    def checkpoint_repository_id(self, checkpoint_id: str) -> str:
        checkpoint = self._checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise RepositoryError("CHECKPOINT_NOT_FOUND", "Checkpoint was not found.")
        return checkpoint.summary.repository_id

    def preview(self, repository_id: str, files: list[PatchFile], confirm_delete: bool = False) -> PatchPreviewResponse:
        repository = self.repositories.get(repository_id)
        prepared: list[PatchFile] = []
        diff_parts: list[str] = []
        additions = 0
        deletions = 0
        bytes_changed = 0
        requires_delete_confirmation = False
        original_hashes: dict[str, str] = {}

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
        self._restore_checkpoint(checkpoint)
        return RollbackResponse(checkpoint=checkpoint.summary, restored_files=checkpoint.summary.files)

    def _create_checkpoint(self, repository: RepositoryRecord, files: list[PatchFile]) -> Checkpoint:
        checkpoint_id = f"checkpoint-{uuid4().hex[:12]}"
        backup_dir = Path(tempfile.mkdtemp(prefix=f"forge-{checkpoint_id}-"))
        snapshots: dict[str, bytes | None] = {}
        for patch_file in files:
            target = self._safe_target(repository, patch_file.path)
            content = target.read_bytes() if target.exists() else None
            snapshots[patch_file.path] = content
            if content is not None:
                backup = backup_dir / patch_file.path
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
        if not relative_path or Path(relative_path).is_absolute():
            raise RepositoryError("INVALID_PATCH_PATH", "Patch paths must be relative to the repository.")
        target = (repository.path / relative_path).resolve(strict=False)
        try:
            target.relative_to(repository.path)
        except ValueError as exc:
            raise RepositoryError("PATH_OUTSIDE_REPOSITORY", "Patch path is outside the registered repository.") from exc
        if any(part in IGNORED_DIRECTORIES or part == ".git" for part in Path(relative_path).parts):
            raise RepositoryError("PROTECTED_PATH", "Protected directories cannot be modified.")
        if target.is_symlink():
            raise RepositoryError("SYMLINK_NOT_ALLOWED", "Symlink targets cannot be modified.")
        return target

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
        return hashlib.sha256((content or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _atomic_write(path: Path, content: str | bytes) -> None:
        data = content.encode("utf-8") if isinstance(content, str) else content
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(data)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
