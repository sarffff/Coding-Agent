from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from .repository_service import RepositoryError


@dataclass(frozen=True, slots=True)
class PartialFileContext:
    source_content: str
    content_hash: str
    start: int
    end: int
    start_line: int
    end_line: int
    total_lines: int


class ContextFiles(list[tuple[str, str]]):
    def __init__(self, items=(), partial_files: dict[str, PartialFileContext] | None = None):
        super().__init__(items)
        paths = {path for path, _ in self}
        self.partial_files = {path: source for path, source in (partial_files or {}).items() if path in paths}


def clone_context(context_files: list[tuple[str, str]], items=None) -> ContextFiles:
    return ContextFiles(context_files if items is None else items, getattr(context_files, "partial_files", {}))


def read_context(context_files: list[tuple[str, str]], path: str, content: str, content_hash: str,
                 start_line: int | None = None, end_line: int | None = None) -> ContextFiles:
    result = clone_context(context_files)
    result[:] = [item for item in result if item[0] != path]
    result.partial_files.pop(path, None)
    if start_line is None and end_line is None:
        result.append((path, content))
        return result
    lines = content.splitlines(keepends=True)
    if start_line is None or end_line is None or not 1 <= start_line <= end_line <= len(lines):
        raise RepositoryError("FILE_RANGE_INVALID", "Read ranges must select existing lines with 1 <= start_line <= end_line.", {"path": path, "line_count": len(lines)})
    start = sum(map(len, lines[:start_line - 1]))
    end = start + sum(map(len, lines[start_line - 1:end_line]))
    result.append((path, content[start:end]))
    result.partial_files[path] = PartialFileContext(content, content_hash, start, end, start_line, end_line, len(lines))
    return result


def nested_instruction_paths(path: str) -> list[str]:
    target = PurePosixPath(path.replace("\\", "/"))
    if target.is_absolute() or ".." in target.parts or PureWindowsPath(target.as_posix()).drive or "\x00" in path:
        return []
    directories = [*reversed(target.parent.parents), target.parent]
    return list(dict.fromkeys((directory / "AGENTS.md").as_posix() for directory in directories))


def esm_source_candidates(path: str) -> list[str]:
    target = PurePosixPath(path)
    extensions = {".js": (".ts", ".tsx"), ".jsx": (".tsx", ".ts"), ".mjs": (".mts",), ".cjs": (".cts",)}
    return [target.with_suffix(suffix).as_posix() for suffix in extensions.get(target.suffix, ())]
