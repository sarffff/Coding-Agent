from __future__ import annotations

import ast
import re
import hashlib
import os
import subprocess
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from pydantic import BaseModel

from .config import Settings
from .store import StateStore
from .models import (
    RepositorySummary,
    RepositoryContext,
    FileContentResponse,
    SymbolInfo,
    RepositoryValidateResponse,
    SearchMatch,
    SearchRequest,
    TreeEntry,
)


from .file_policy import IGNORED_DIRECTORIES, is_protected_path
from .process_env import child_env, is_process_startup_failure


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


class RepositoryRecord(BaseModel):
    id: str
    name: str
    path: Path
    registered_at: datetime


class RepositoryService:
    def __init__(self, settings: Settings, store: StateStore | None = None):
        self.settings = settings
        self.store = store
        self._repositories: dict[str, RepositoryRecord] = {}
        self._keys: dict[str, str] = {}
        if store:
            for key, record in store.models("repositories", RepositoryRecord):
                self._repositories[record.id] = record
                self._keys[record.id] = key

    def _persist(self, record: RepositoryRecord) -> None:
        if not self.store:
            return
        key = self._keys.setdefault(record.id, self.store.next_key("repositories"))
        self.store.put("repositories", key, record, record.registered_at.isoformat())

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
                env=child_env(),
                check=False,
            )
        except FileNotFoundError as exc:
            raise RepositoryError("GIT_NOT_FOUND", "Git is not installed or not available on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise RepositoryError("GIT_TIMEOUT", "Git command timed out.") from exc

        if is_process_startup_failure(result.returncode):
            raise RepositoryError("GIT_UNAVAILABLE", "The API process could not start Git. Restart the API from a normal shell.", {"command": ["git", *args], "exit_code": result.returncode})
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RepositoryError("GIT_COMMAND_FAILED", detail or "Git command failed.", {"command": ["git", *args], "exit_code": result.returncode})
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
        self._persist(record)
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
            directories[:] = sorted(directory for directory in directories if directory.lower() not in IGNORED_DIRECTORIES and not (Path(current) / directory).is_symlink())
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
            directories[:] = sorted(directory for directory in directories if directory.lower() not in IGNORED_DIRECTORIES and not (Path(current) / directory).is_symlink())
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
                    if len(matches) > min(request.max_results, self.settings.max_search_results):
                        return matches[:min(request.max_results, self.settings.max_search_results)], True
        return matches, truncated

    def read_file(self, repository_id: str, relative_path: str) -> FileContentResponse:
        record = self.get(repository_id)
        target = self._safe_repo_path(record, relative_path)
        if not target.is_file():
            raise RepositoryError("FILE_NOT_FOUND", "The requested code file does not exist.")
        if self._skip_file(target):
            raise RepositoryError("FILE_NOT_READABLE", "This file is protected or exceeds the size limit.")
        content = target.read_bytes()
        if b"\0" in content:
            raise RepositoryError("BINARY_FILE_NOT_ALLOWED", "Binary files cannot be displayed as source code.")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RepositoryError("FILE_ENCODING_UNSUPPORTED", "Only UTF-8 source files are supported.") from exc
        return FileContentResponse(
            repository_id=repository_id, path=target.relative_to(record.path).as_posix(),
            content=text, content_hash=hashlib.sha256(content).hexdigest(), size=len(content),
            language=LANGUAGE_BY_EXTENSION.get(target.suffix.lower()),
            line_count=len(text.splitlines()), symbols=self._symbols(target.suffix.lower(), text),
        )

    def context(self, repository_id: str) -> RepositoryContext:
        record = self.get(repository_id)
        entries, truncated = self.tree(repository_id, max_depth=6)
        files = [entry.path for entry in entries if entry.kind == "file"]
        entry_names = {"main.py", "app.py", "__main__.py", "main.tsx", "main.ts", "main.js", "index.ts", "index.tsx", "index.js", "server.ts", "server.js"}
        config_names = {"pyproject.toml", "requirements.txt", "package.json", "pnpm-workspace.yaml", "tsconfig.json", "vite.config.ts", "pytest.ini", "ruff.toml", "eslint.config.js", "vitest.config.ts"}
        test_paths = sorted({str(Path(name).parent).replace("\\", "/") for name in files if Path(name).name.startswith("test_") or any(token in name for token in (".test.", ".spec.", "/tests/", "/__tests__/"))})
        return RepositoryContext(
            repository_id=repository_id, languages=self._language_counts(record.path),
            package_manager=self._package_manager(record.path),
            entry_files=[name for name in files if Path(name).name in entry_names][:30],
            test_directories=test_paths[:30], config_files=[name for name in files if Path(name).name in config_names][:30],
            file_count=len(files), truncated=truncated,
        )

    @staticmethod
    def _symbols(extension: str, content: str) -> list[SymbolInfo]:
        symbols: list[SymbolInfo] = []
        if extension == ".py":
            try:
                tree = ast.parse(content)
            except SyntaxError:
                return []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    symbols.append(SymbolInfo(name=node.name, kind="class" if isinstance(node, ast.ClassDef) else "function", line=node.lineno))
        elif extension in {".ts", ".tsx", ".js", ".jsx"}:
            pattern = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?(function|class|interface|type|const|let|var)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
            for match in pattern.finditer(content):
                kind = "variable" if match.group(1) in {"const", "let", "var"} else match.group(1)
                symbols.append(SymbolInfo(name=match.group(2), kind=kind, line=content.count("\n", 0, match.start()) + 1 + match.group(0).count("\n")))
        return sorted(symbols, key=lambda symbol: symbol.line)[:200]

    def _safe_repo_path(self, record: RepositoryRecord, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts or (relative_path and is_protected_path(relative)):
            raise RepositoryError("PROTECTED_PATH", "This path is outside the readable code area.")
        candidate = record.path / relative
        for part in [candidate, *candidate.parents]:
            if part == record.path:
                break
            if part.is_symlink():
                raise RepositoryError("SYMLINK_NOT_ALLOWED", "Symlink paths cannot be read or edited.")
        requested = candidate.resolve()
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
        return is_protected_path(path.name)

    @staticmethod
    def _normalize_extension(extension: str) -> str:
        return extension.lower() if extension.startswith(".") else f".{extension.lower()}"

    def _language_counts(self, repo_path: Path) -> dict[str, int]:
        counts: dict[str, int] = {}
        for current, directories, files in os.walk(repo_path):
            directories[:] = [directory for directory in directories if directory.lower() not in IGNORED_DIRECTORIES and not (Path(current) / directory).is_symlink()]
            for filename in files:
                if self._skip_file(Path(current) / filename):
                    continue
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
