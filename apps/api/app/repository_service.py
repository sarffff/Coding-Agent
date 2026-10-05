from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from .config import Settings
from .models import (
    RepositorySummary,
    RepositoryValidateResponse,
    SearchMatch,
    SearchRequest,
    TreeEntry,
)


IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "vendor",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".venv",
    "__pycache__",
    ".pytest_cache",
}

LANGUAGE_BY_EXTENSION = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".json": "JSON",
    ".css": "CSS",
    ".scss": "SCSS",
    ".md": "Markdown",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
}


class RepositoryError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(slots=True)
class RepositoryRecord:
    id: str
    name: str
    path: Path
    registered_at: datetime


class RepositoryService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._repositories: dict[str, RepositoryRecord] = {}

    def _workspace_root(self) -> Path:
        return self.settings.workspace_root.expanduser().resolve()

    def _resolve_allowed_path(self, raw_path: str | Path) -> Path:
        path = Path(raw_path).expanduser().resolve(strict=False)
        root = self._workspace_root()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise RepositoryError(
                "PATH_OUTSIDE_WORKSPACE",
                "Repository path must be inside the configured workspace root.",
                {"workspace_root": str(root)},
            ) from exc
        return path

    def _run_git(self, repo_path: Path, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=repo_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.git_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RepositoryError("GIT_NOT_FOUND", "Git is not installed or not available on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise RepositoryError("GIT_TIMEOUT", "Git command timed out.") from exc

        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RepositoryError("GIT_COMMAND_FAILED", detail or "Git command failed.")
        return result.stdout.strip()

    def _git_root(self, path: Path) -> Path:
        if not path.exists():
            raise RepositoryError("PATH_NOT_FOUND", "The repository path does not exist.")
        if not path.is_dir():
            raise RepositoryError("PATH_NOT_DIRECTORY", "The repository path must be a directory.")
        raw_root = self._run_git(path, "rev-parse", "--show-toplevel")
        git_root = Path(raw_root).expanduser().resolve()
        try:
            git_root.relative_to(self._workspace_root())
        except ValueError as exc:
            raise RepositoryError(
                "GIT_ROOT_OUTSIDE_WORKSPACE",
                "The resolved Git root is outside the configured workspace root.",
            ) from exc
        return git_root

    def validate(self, raw_path: str) -> RepositoryValidateResponse:
        try:
            path = self._resolve_allowed_path(raw_path)
            git_root = self._git_root(path)
            return RepositoryValidateResponse(valid=True, path=str(path), git_root=str(git_root))
        except RepositoryError as exc:
            return RepositoryValidateResponse(valid=False, path=str(Path(raw_path).expanduser()), reason=exc.message)

    def register(self, raw_path: str, name: str | None = None) -> RepositorySummary:
        path = self._resolve_allowed_path(raw_path)
        git_root = self._git_root(path)
        repository_id = hashlib.sha256(os.fspath(git_root).encode("utf-8")).hexdigest()[:12]
        record = RepositoryRecord(
            id=repository_id,
            name=name or git_root.name,
            path=git_root,
            registered_at=datetime.now(timezone.utc),
        )
        self._repositories[repository_id] = record
        return self.summary(record)

    def list(self) -> list[RepositorySummary]:
        return [self.summary(record) for record in self._repositories.values()]

    def get(self, repository_id: str) -> RepositoryRecord:
        record = self._repositories.get(repository_id)
        if record is None:
            raise RepositoryError("REPOSITORY_NOT_FOUND", "Repository is not registered.", {"repository_id": repository_id})
        return record

    def summary(self, record: RepositoryRecord) -> RepositorySummary:
        branch = self._run_git(record.path, "branch", "--show-current") or "(detached HEAD)"
        head = self._run_git(record.path, "rev-parse", "--short", "HEAD") or None
        last_commit = self._run_git(record.path, "log", "-1", "--pretty=%s") or None
        status = self._run_git(record.path, "status", "--porcelain=v1")
        changed_files = len([line for line in status.splitlines() if line.strip()])
        return RepositorySummary(
            id=record.id,
            name=record.name,
            path=str(record.path),
            branch=branch,
            head=head,
            last_commit=last_commit,
            changed_files=changed_files,
            languages=self._language_counts(record.path),
            package_manager=self._package_manager(record.path),
            registered_at=record.registered_at,
        )

    def tree(self, repository_id: str, relative_path: str = "", max_depth: int = 4) -> tuple[list[TreeEntry], bool]:
        record = self.get(repository_id)
        base_path = self._safe_repo_path(record, relative_path)
        if not base_path.exists() or not base_path.is_dir():
            raise RepositoryError("TREE_PATH_NOT_FOUND", "The requested tree path does not exist or is not a directory.")

        entries: list[TreeEntry] = []
        truncated = False
        for current, directories, files in os.walk(base_path):
            current_path = Path(current)
            depth = len(current_path.relative_to(base_path).parts)
            directories[:] = sorted(directory for directory in directories if directory not in IGNORED_DIRECTORIES)
            if depth >= max_depth:
                directories[:] = []
            for directory in directories:
                relative = (current_path / directory).relative_to(record.path).as_posix()
                entries.append(TreeEntry(path=relative, name=directory, kind="directory"))
                if len(entries) >= self.settings.max_tree_entries:
                    truncated = True
                    return entries[: self.settings.max_tree_entries], truncated
            for filename in sorted(files):
                file_path = current_path / filename
                if self._skip_file(file_path):
                    continue
                relative = file_path.relative_to(record.path).as_posix()
                size = file_path.stat().st_size
                entries.append(
                    TreeEntry(
                        path=relative,
                        name=filename,
                        kind="file",
                        size=size,
                        language=LANGUAGE_BY_EXTENSION.get(file_path.suffix.lower()),
                    )
                )
                if len(entries) >= self.settings.max_tree_entries:
                    truncated = True
                    return entries[: self.settings.max_tree_entries], truncated
        return entries, truncated

    def search(self, repository_id: str, request: SearchRequest) -> tuple[list[SearchMatch], bool]:
        record = self.get(repository_id)
        query = request.query if request.case_sensitive else request.query.lower()
        extensions = {self._normalize_extension(extension) for extension in request.extensions}
        matches: list[SearchMatch] = []
        truncated = False
        for current, directories, files in os.walk(record.path):
            directories[:] = sorted(directory for directory in directories if directory not in IGNORED_DIRECTORIES)
            for filename in sorted(files):
                file_path = Path(current) / filename
                if self._skip_file(file_path) or (extensions and file_path.suffix.lower() not in extensions):
                    continue
                relative = file_path.relative_to(record.path).as_posix()
                if request.glob and not fnmatch(relative, request.glob):
                    continue
                try:
                    content = file_path.read_bytes()
                except OSError:
                    continue
                if b"\x00" in content:
                    continue
                text = content.decode("utf-8", errors="replace")
                for line_number, line in enumerate(text.splitlines(), start=1):
                    haystack = line if request.case_sensitive else line.lower()
                    column = haystack.find(query)
                    if column < 0:
                        continue
                    matches.append(SearchMatch(path=relative, line=line_number, column=column + 1, text=line.strip()[:500]))
                    if len(matches) >= request.max_results:
                        return matches, True
        return matches, truncated

    def _safe_repo_path(self, record: RepositoryRecord, relative_path: str) -> Path:
        requested = (record.path / relative_path).resolve()
        try:
            requested.relative_to(record.path)
        except ValueError as exc:
            raise RepositoryError("PATH_OUTSIDE_REPOSITORY", "Requested path is outside the registered repository.") from exc
        return requested

    def _skip_file(self, path: Path) -> bool:
        try:
            if path.is_symlink() or path.stat().st_size > self.settings.max_file_size_bytes:
                return True
        except OSError:
            return True
        return any(part in IGNORED_DIRECTORIES for part in path.parts)

    @staticmethod
    def _normalize_extension(extension: str) -> str:
        return extension.lower() if extension.startswith(".") else f".{extension.lower()}"

    def _language_counts(self, repo_path: Path) -> dict[str, int]:
        counts: dict[str, int] = {}
        for current, directories, files in os.walk(repo_path):
            directories[:] = [directory for directory in directories if directory not in IGNORED_DIRECTORIES]
            for filename in files:
                language = LANGUAGE_BY_EXTENSION.get(Path(filename).suffix.lower())
                if language:
                    counts[language] = counts.get(language, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    @staticmethod
    def _package_manager(repo_path: Path) -> str | None:
        markers = {
            "pnpm-lock.yaml": "pnpm",
            "yarn.lock": "yarn",
            "package-lock.json": "npm",
            "poetry.lock": "poetry",
            "uv.lock": "uv",
            "Pipfile.lock": "pipenv",
        }
        for filename, manager in markers.items():
            if (repo_path / filename).exists():
                return manager
        if (repo_path / "requirements.txt").exists():
            return "pip"
        return None
