from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import shlex
import subprocess
import tomllib
from pathlib import Path, PureWindowsPath

from .file_policy import is_protected_path
from .models import PatchFile, ValidationCheck
from .process_env import child_env
from .repository_service import RepositoryError, RepositoryRecord
from .test_service import _generated


_CONFIG_PREFIXES = ("jest.config.", "vitest.config.", "vite.config.", "playwright.config.", "cypress.config.", "karma.conf.", ".mocharc.", "eslint.config.", ".eslintrc.")
_PACKAGE_CONFIG = ("jest", "vitest", "mocha", "nyc", "eslintConfig")
_PYPROJECT_TOOLS = ("pytest", "coverage", "ruff", "mypy")
_VALIDATION_SCRIPTS = ("test", "check", "typecheck", "lint", "build")
_SCRIPT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]*\Z")
_MANAGER = re.compile(r"\b(?:npm|pnpm|yarn)(?:\s+[^;&|\n]*)?")
_SCRIPT_CALL = re.compile(r"\b(?:npm|pnpm|yarn)\s+(?:run(?:-script)?\s+)?([A-Za-z0-9][A-Za-z0-9:_.-]*)(?=\s|[;&|]|$)")


def _safe_path(repository: RepositoryRecord, relative: str) -> Path:
    path = Path(relative.replace("\\", "/"))
    if path.is_absolute() or PureWindowsPath(relative).drive or ".." in path.parts or is_protected_path(path):
        raise RepositoryError("PROTECTED_PATH", "Validation inputs must stay inside the readable repository area.")
    candidate = repository.path / path
    for parent in (candidate, *candidate.parents):
        if parent == repository.path:
            break
        if parent.is_symlink():
            raise RepositoryError("SYMLINK_NOT_ALLOWED", "Symlink paths cannot be validation inputs.")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(repository.path):
        raise RepositoryError("PATH_OUTSIDE_REPOSITORY", "A validation input is outside the registered repository.")
    return resolved


def _skip(relative: str | Path) -> bool:
    return is_protected_path(relative) or any(part.lower() == ".tmp" or _generated(part) for part in Path(relative).parts)


def _inputs(repository: RepositoryRecord, max_entries: int):
    def fail_walk(error: OSError) -> None:
        raise error

    if repository.has_git:
        result = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=repository.path, capture_output=True, timeout=30, env=child_env(), check=False)
        if result.returncode:
            raise RepositoryError("VALIDATION_INPUT_UNAVAILABLE", "Git could not list validation inputs.")
        entries = ((relative, False) for relative in sorted(set(filter(None, result.stdout.decode("utf-8").split("\0")))))
    else:
        def walk():
            for current, directories, files in os.walk(repository.path, onerror=fail_walk):
                base = Path(current).relative_to(repository.path)
                directories[:] = sorted(name for name in directories if not _skip(base / name))
                for name in directories:
                    yield (base / name).as_posix(), True
                for name in sorted(files):
                    yield (base / name).as_posix(), False
        entries = walk()
    count = 0
    for relative, directory in entries:
        if _skip(relative):
            continue
        count += 1
        if count > max_entries:
            raise RepositoryError("VALIDATION_SCOPE_TOO_LARGE", "Validation inputs exceed the repository entry limit; narrow the workspace before retrying.")
        path = _safe_path(repository, relative)
        if not directory and path.exists():
            if not path.is_file():
                raise RepositoryError("VALIDATION_INPUT_UNAVAILABLE", "Validation inputs must be readable regular files.")
            yield relative


def _is_test(relative: str) -> bool:
    path = Path(relative)
    name = path.name.lower()
    return name.startswith("test_") or name.endswith("_test.py") or ".test." in name or ".spec." in name or name == "conftest.py" or any(part.lower() in {"tests", "__tests__"} for part in path.parts[:-1])


def _configuration_key(relative: str) -> str | None:
    name = Path(relative).name.lower()
    if name == "package.json":
        return f"config:{relative}:package"
    if name in {"pyproject.toml", "setup.cfg"}:
        return f"config:{relative}:{'pyproject' if name == 'pyproject.toml' else 'setup'}"
    if name in {"pytest.ini", "tox.ini", ".mocharc", ".eslintrc", ".eslintignore"} or name.startswith(_CONFIG_PREFIXES) or (name.startswith("tsconfig") and name.endswith(".json")):
        return "file:" + relative
    return None


def _configuration_paths(repository: RepositoryRecord, max_entries: int):
    # Validation tools consume configuration even when Git ignores the file.
    local = repository.model_copy(update={"has_git": False})
    return (relative for relative in _inputs(local, max_entries) if _configuration_key(relative))


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _structured_hash(value: object) -> str:
    return _hash(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"))


def _config_value(content: bytes, kind: str):
    text = content.decode("utf-8-sig")
    if kind == "pyproject":
        tool = tomllib.loads(text).get("tool", {})
        if not isinstance(tool, dict):
            raise ValueError("Validation tool configuration must be an object.")
        return {name: tool[name] for name in _PYPROJECT_TOOLS if name in tool}
    if kind == "setup":
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(text)
        return {section: dict(config[section]) for section in config.sections() if section == "tool:pytest" or section.startswith(("coverage:", "mypy", "flake8"))}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Package configuration must be an object.")
    if kind == "package":
        return {name: data[name] for name in _PACKAGE_CONFIG if name in data}
    scripts = data.get("scripts", {})
    if not isinstance(scripts, dict):
        raise ValueError("Package scripts must be an object.")
    return scripts if kind == "scripts" else scripts.get(kind.removeprefix("script:"))


def _read_value(repository: RepositoryRecord, key: str, overrides: dict[str, bytes | None] | None = None) -> str:
    prefix, relative = key.split(":", 1)
    kind = None
    if prefix in {"script", "config"}:
        relative, kind = relative.split(":", 1)
        if prefix == "script":
            kind = "script:" + kind
    elif prefix != "file":
        raise RepositoryError("VALIDATION_INPUT_UNAVAILABLE", "The validation baseline is invalid.")
    path = _safe_path(repository, relative)
    if overrides is not None and relative in overrides:
        content = overrides[relative]
        if content is None:
            raise FileNotFoundError(relative)
        return _structured_hash(_config_value(content, kind)) if kind else _hash(content)
    if kind:
        return _structured_hash(_config_value(path.read_bytes(), kind))
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _selected_script(repository: RepositoryRecord, check: ValidationCheck) -> tuple[str, str] | None:
    directory = _safe_path(repository, check.workdir)
    if not directory.is_dir():
        raise RepositoryError("INVALID_TEST_WORKDIR", "Select an existing validation directory inside the repository.")
    if check.kind == "pytest" or (check.kind == "auto" and any(any(directory.glob(pattern)) for pattern in ("tests", "test_*.py", "*_test.py"))):
        return None
    package = _safe_path(repository, (directory.relative_to(repository.path) / "package.json").as_posix())
    if not package.exists():
        if check.kind == "script":
            raise RepositoryError("INVALID_TEST_SCRIPT", "The selected package script is missing.")
        return None
    scripts = _config_value(package.read_bytes(), "scripts")
    selected = check.script if check.kind == "script" else next((name for name in ("typecheck", "check", "lint") if name in scripts), None) if check.kind == "static" else "test" if "test" in scripts else None
    if selected is None:
        return None
    if not isinstance(scripts.get(selected), str) or not scripts[selected].strip():
        raise RepositoryError("INVALID_TEST_SCRIPT", "The selected package script is missing or invalid.")
    return package.relative_to(repository.path).as_posix(), selected


def _wrapper_keys(repository: RepositoryRecord, package: str, command: str) -> set[str]:
    keys: set[str] = set()
    tokens = shlex.shlex(command, posix=False, punctuation_chars=";&|")
    tokens.whitespace_split = True
    for token in tokens:
        token = token.split("=", 1)[-1].strip("\"'")
        if Path(token).suffix.lower() not in {".js", ".mjs", ".cjs", ".py", ".sh", ".ps1", ".bat", ".cmd", ".json"}:
            continue
        relative = (Path(package).parent / token).as_posix()
        if not _skip(relative) and _safe_path(repository, relative).is_file():
            keys.add("file:" + relative)
    return keys


def _local_extended_config_keys(repository: RepositoryRecord, keys: set[str], max_entries: int) -> set[str]:
    pending = [key.removeprefix("file:") for key in keys if key.startswith("file:") and key.endswith(".json")]
    visited: set[str] = set()
    protected: set[str] = set()
    while pending:
        relative = pending.pop()
        if relative in visited:
            continue
        visited.add(relative)
        if len(visited) > max_entries:
            raise RepositoryError("VALIDATION_SCOPE_TOO_LARGE", "Extended validation configuration exceeds the repository entry limit.")
        # ponytail: conservatively keep local extends strings in JSON/JSONC; package resolution stays outside readable inputs.
        content = _safe_path(repository, relative).read_text(encoding="utf-8-sig")
        for key, value in re.findall(r'("(?:\\.|[^"\\])*")\s*:\s*(\[(?:[^"\]]|"(?:\\.|[^"\\])*")*\]|"(?:\\.|[^"\\])*")', content):
            if json.loads(key) != "extends":
                continue
            for quoted in re.findall(r'"(?:\\.|[^"\\])*"', value):
                reference = json.loads(quoted)
                if not reference.startswith("."):
                    continue
                target = os.path.normpath(str(Path(relative).parent / reference))
                path = _safe_path(repository, target)
                if not path.exists() and not path.suffix:
                    target += ".json"
                    path = _safe_path(repository, target)
                if path.is_file():
                    normalized = path.relative_to(repository.path).as_posix()
                    protected.add("file:" + normalized)
                    pending.append(normalized)
    return protected


def _script_keys(repository: RepositoryRecord, package: str, scripts: dict, selected: str, packages: set[str]) -> set[str]:
    protected: set[str] = set()
    pending = [selected]
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        for current in (name, "pre" + name, "post" + name):
            protected.add(f"script:{package}:{current}")
            command = scripts.get(current)
            if command is None:
                continue
            if not isinstance(command, str):
                raise ValueError("Validation script commands must be strings.")
            protected.update(_wrapper_keys(repository, package, command))
            # ponytail: only simple package-manager calls are parsed; ambiguous shell
            # indirection protects the complete scripts mapping instead of guessing.
            managers = list(_MANAGER.finditer(command))
            calls = list(_SCRIPT_CALL.finditer(command))
            if len(managers) != len(calls) or re.search(r"\bcd\s", command) or any(not _SCRIPT_NAME.fullmatch(call[1]) or call[1] in {"run", "run-script", "exec", "dlx", "workspace", "workspaces"} for call in calls):
                protected.update(f"config:{relative}:scripts" for relative in packages | {package})
            pending.extend(call[1] for call in calls if _SCRIPT_NAME.fullmatch(call[1]))
    return protected


def capture_validation_guard(repository: RepositoryRecord, checks: list[ValidationCheck], max_entries: int = 5_000) -> dict[str, str]:
    """Hash existing acceptance inputs; added tests and unrelated dependencies remain editable."""
    keys: set[str] = set()
    explicit_inputs: set[str] = set()
    packages: set[str] = set()
    evidence = False
    try:
        for relative in _inputs(repository, max_entries):
            name = Path(relative).name.lower()
            if name == "package.json":
                packages.add(relative)
            configuration = _configuration_key(relative)
            if configuration:
                keys.add(configuration)
            elif _is_test(relative):
                keys.add("file:" + relative)
            evidence = evidence or _is_test(relative)
        for check in checks or [ValidationCheck(id="auto", label="Auto validation", kind="auto")]:
            selected = _selected_script(repository, check)
            if selected:
                evidence = True
                relative, script = selected
                keys.add(f"config:{relative}:package")
                scripts = _config_value(_safe_path(repository, relative).read_bytes(), "scripts")
                selected_keys = _script_keys(repository, relative, scripts, script, packages)
                keys.update(selected_keys)
                explicit_inputs.update(selected_keys)
                if check.kind == "static":
                    priority = ("typecheck", "check", "lint")
                    keys.update(f"script:{relative}:{name}" for name in priority[:priority.index(script)])
        if evidence:
            for relative in _configuration_paths(repository, max_entries):
                keys.add(_configuration_key(relative))
                if Path(relative).name.lower() == "package.json" and not any(key.startswith(f"script:{relative}:") for key in keys):
                    scripts = _config_value(_safe_path(repository, relative).read_bytes(), "scripts")
                    keys.update(f"script:{relative}:{name}" for name in _VALIDATION_SCRIPTS if name not in scripts)
        configuration_inputs = {key for key in keys if key.startswith("file:") and _configuration_key(key.removeprefix("file:"))}
        keys.update(_local_extended_config_keys(repository, configuration_inputs | explicit_inputs, max_entries))
        captured = {key: _read_value(repository, key) for key in sorted(keys)}
        if evidence:
            captured[f"scope:.:validation-configs:{max_entries}"] = _hash(b"existing validation evidence")
        return captured
    except (OSError, ValueError, configparser.Error, subprocess.TimeoutExpired) as exc:
        raise RepositoryError("VALIDATION_INPUT_UNAVAILABLE", "A validation input or configuration could not be read; retry after it is accessible.") from exc


def validation_guard_changes(repository: RepositoryRecord, captured: dict[str, str], proposed_files: list[PatchFile] | None = None) -> list[str]:
    """Compare the baseline with disk or an in-memory candidate patch, without writing files."""
    overrides: dict[str, bytes | None] = {}
    for proposed in proposed_files or []:
        relative = _safe_path(repository, proposed.path).relative_to(repository.path).as_posix()
        overrides[relative] = None if proposed.operation == "delete" else (proposed.content or "").encode("utf-8")
    changed: set[str] = set()
    scope = next((key for key in captured if key.startswith("scope:.:validation-configs:")), None)
    legacy_scope = not scope and any(key.startswith("script:") or (key.startswith("file:") and _is_test(key.split(":", 1)[1])) for key in captured)
    for key, expected in captured.items():
        if key == scope:
            continue
        relative = key.split(":", 2)[1]
        try:
            if _read_value(repository, key, overrides) != expected:
                changed.add(relative)
        except FileNotFoundError:
            changed.add(relative)
        except (ValueError, configparser.Error):
            if relative in overrides:
                changed.add(relative)
            else:
                raise RepositoryError("VALIDATION_INPUT_UNAVAILABLE", "A validation configuration is invalid.")
        except OSError as exc:
            raise RepositoryError("VALIDATION_INPUT_UNAVAILABLE", "A validation input could not be read.") from exc
    if scope or legacy_scope:
        known = {key.split(":", 2)[1] for key in captured if key != scope}
        try:
            configurations = set(_configuration_paths(repository, int(scope.rsplit(":", 1)[1]) if scope else 5_000))
            configurations.update(relative for relative, content in overrides.items() if content is not None and _configuration_key(relative))
            for relative in configurations - known:
                if relative in overrides and overrides[relative] is None:
                    continue
                key = _configuration_key(relative)
                if key.startswith("file:"):
                    changed.add(relative)
                    continue
                content = overrides[relative] if relative in overrides else _safe_path(repository, relative).read_bytes()
                kind = key.split(":", 2)[2]
                try:
                    relevant = _config_value(content, kind)
                    if kind == "package":
                        scripts = _config_value(content, "scripts")
                        relevant = relevant or any(name in _VALIDATION_SCRIPTS or name.startswith("test:") or name.endswith(":test") for name in scripts)
                    if relevant:
                        changed.add(relative)
                except (ValueError, configparser.Error):
                    changed.add(relative)
        except (OSError, ValueError) as exc:
            raise RepositoryError("VALIDATION_INPUT_UNAVAILABLE", "New validation configuration could not be inspected.") from exc
    return sorted(changed)
