from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import Event, Lock

from .config import Settings
from .file_policy import is_protected_path
from .models import TestFailure, TestKind, TestRunResponse, ValidationCheck
from .process_env import child_env, is_process_startup_failure
from .repository_service import RepositoryError, RepositoryRecord, RepositoryService


class TestService:
    OUTPUT_LIMIT = 20_000

    def __init__(self, repositories: RepositoryService, settings: Settings):
        self.repositories = repositories
        self.settings = settings
        self._active: dict[str, Event] = {}
        self._lock = Lock()

    def reserve(self, run_id: str) -> None:
        with self._lock:
            if run_id in self._active:
                raise RepositoryError("TEST_ALREADY_RUNNING", "A validation command is already running for this task.")
            self._active[run_id] = Event()

    def is_running(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._active

    def cancel(self, run_id: str) -> None:
        with self._lock:
            event = self._active.get(run_id)
            if event:
                event.set()

    def release(self, run_id: str) -> None:
        with self._lock:
            self._active.pop(run_id, None)

    def default_checks(self, repository_id: str) -> list[ValidationCheck]:
        repository = self.repositories.get(repository_id)
        package = self.repositories._safe_repo_path(repository, "package.json")
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict) or not isinstance(data.get("scripts"), dict):
            return []
        scripts = {name: command.strip() for name, command in data["scripts"].items() if isinstance(command, str) and command.strip()}
        selected = []
        if "test" in scripts:
            selected.append(("test", "测试", "test"))
        if "api:test" in scripts and scripts["api:test"] != scripts.get("test"):
            selected.append(("api-tests", "API 测试", "api:test"))
        static = next((name for name in ("typecheck", "check", "lint") if name in scripts), None)
        if static:
            selected.append(("static", "静态检查", static))
        if "build" in scripts:
            selected.append(("build", "构建", "build"))
        return [ValidationCheck(id=check_id, label=label, kind="script", workdir=".", script=script, timeout_seconds=300) for check_id, label, script in selected]

    def input_hash(self, repository_id: str) -> str:
        repository = self.repositories.get(repository_id)
        digest = hashlib.sha256()
        entries = 0

        def walk_error(error: OSError) -> None:
            raise error

        try:
            for current, directories, files in os.walk(repository.path, onerror=walk_error):
                base = Path(current)
                relative_base = base.relative_to(repository.path)
                directories[:] = sorted(name for name in directories if not is_protected_path(relative_base / name) and not (base / name).is_symlink())
                for name in [*directories, *sorted(files)]:
                    path = base / name
                    if is_protected_path(relative_base / name) or path.is_symlink():
                        continue
                    entries += 1
                    if entries > self.settings.max_tree_entries:
                        raise RepositoryError("VALIDATION_SCOPE_TOO_LARGE", "The validation inputs exceed the repository entry limit; narrow the workspace before retrying.")
                    relative = path.relative_to(repository.path).as_posix()
                    path = self.repositories._safe_repo_path(repository, relative)
                    digest.update((relative + "\0").encode("utf-8"))
                    if name in directories:
                        digest.update(b"directory\0")
                        continue
                    if not path.is_file():
                        raise RepositoryError("VALIDATION_INPUT_UNAVAILABLE", "Validation inputs must be readable regular files.")
                    content = hashlib.sha256()
                    with path.open("rb") as source:
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            content.update(block)
                    digest.update(content.digest())
        except OSError as exc:
            raise RepositoryError("VALIDATION_INPUT_UNAVAILABLE", "A validation input could not be read; retry after the workspace is accessible.") from exc
        return digest.hexdigest()

    def run(self, run_id: str, repository_id: str, kind: TestKind, timeout_seconds: int, target: str | None = None, *, workdir: str = ".", script: str | None = None) -> TestRunResponse:
        repository = self.repositories.get(repository_id)
        with self._lock:
            cancel = self._active.setdefault(run_id, Event())
        started = time.perf_counter()
        command: list[str] = []
        details = {"workdir": workdir, "target": target, "script": script}
        try:
            directory = self._validation_directory(repository, workdir)
            details["workdir"] = directory.relative_to(repository.path).as_posix()
            command = self._detect_command(repository, kind, target, workdir=workdir, script=script) or []
            if not command:
                return self._result(run_id, kind, command, "not_found", started, stderr="No supported validation command was detected.", **details)
            executable = shutil.which(command[0])
            if not executable:
                return self._result(run_id, kind, command, "not_found", started, stderr="The required validation executable is not available.", **details)
            if cancel.is_set():
                return self._result(run_id, kind, command, "cancelled", started, **details)
            # Capture output in the state directory: an inherited TMP may point at
            # a sandbox-owned path this process cannot write to.
            self.settings.state_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=self.settings.state_dir) as stdout, tempfile.TemporaryFile(dir=self.settings.state_dir) as stderr:
                process = subprocess.Popen(
                    [executable, *command[1:]], cwd=directory, stdout=stdout, stderr=stderr,
                    env=child_env(),
                    shell=False, start_new_session=os.name != "nt",
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                )
                limit = min(timeout_seconds, self.settings.command_timeout_seconds)
                status = "passed"
                while process.poll() is None:
                    if cancel.is_set():
                        status = "cancelled"
                        self._stop(process)
                        break
                    if time.perf_counter() - started >= limit:
                        status = "timed_out"
                        self._stop(process)
                        break
                    cancel.wait(0.05)
                process.wait()
                if status == "passed" and process.returncode:
                    status = "blocked" if is_process_startup_failure(process.returncode) else "failed"
                stdout.seek(0)
                stderr.seek(0)
                out = stdout.read(self.OUTPUT_LIMIT + 1)
                err = stderr.read(self.OUTPUT_LIMIT + 1)
                result = self._result(run_id, kind, command, status, started, stdout=out[:self.OUTPUT_LIMIT].decode("utf-8", errors="replace"), stderr=err[:self.OUTPUT_LIMIT].decode("utf-8", errors="replace"), **details)
                result.exit_code = process.returncode
                result.output_truncated = len(out) > self.OUTPUT_LIMIT or len(err) > self.OUTPUT_LIMIT
                if status == "blocked":
                    result.stderr = (result.stderr + "\n" if result.stderr else "") + "The validation process could not be started by the API process. Restart the API from a normal shell."
                if status == "timed_out":
                    result.stderr += "\nValidation exceeded its time limit."
                if status == "cancelled":
                    result.stderr += "\nValidation was cancelled."
                result.failed_tests = self._parse_failures(result.stdout, result.stderr) if status == "failed" else []
                for failure in result.failed_tests:
                    if failure.path:
                        try:
                            failure.path = (directory / failure.path).resolve().relative_to(repository.path).as_posix()
                        except ValueError:
                            failure.path = None
                return result
        except RepositoryError as exc:
            return self._result(run_id, kind, command, "blocked", started, stderr=exc.message, **details)
        except (ValueError, TypeError):
            return self._result(run_id, kind, command, "blocked", started, stderr="The repository validation configuration is invalid.", **details)
        except OSError:
            return self._result(run_id, kind, command, "not_found", started, stderr="The validation process could not be started.", **details)
        finally:
            self.release(run_id)

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()

    @staticmethod
    def _result(run_id, kind, command, status, started, *, stdout="", stderr="", workdir=".", target=None, script=None):
        return TestRunResponse(run_id=run_id, kind=kind, command=command, status=status, duration_ms=round((time.perf_counter() - started) * 1000), stdout=stdout, stderr=stderr, workdir=workdir, target=target, script=script)

    def _validation_directory(self, repository: RepositoryRecord, workdir: str) -> Path:
        directory = self.repositories._safe_repo_path(repository, workdir)
        if not directory.is_dir():
            raise RepositoryError("INVALID_TEST_WORKDIR", "Select an existing directory inside this repository.")
        return directory

    def _detect_command(self, repository: RepositoryRecord, kind: TestKind, target: str | None = None, *, workdir: str = ".", script: str | None = None) -> list[str] | None:
        directory = self._validation_directory(repository, workdir)
        if script is not None and kind != "script":
            raise RepositoryError("INVALID_TEST_SCRIPT", "Use script validation to select a package script.")
        if kind == "script" and (target is not None or not script or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]*", script)):
            raise RepositoryError("INVALID_TEST_SCRIPT", "Select a package script name without command arguments or a test target.")
        selected = None
        if target:
            relative = target.split("::", 1)[0]
            selected_path = self.repositories._safe_repo_path(repository, (Path(workdir) / relative).as_posix())
            if not selected_path.exists() or not selected_path.is_relative_to(directory):
                raise RepositoryError("INVALID_TEST_TARGET", "Select an existing test file inside the validation directory.")
            if "::" in target and not re.fullmatch(r"[A-Za-z0-9_:]+", target.split("::", 1)[1]):
                raise RepositoryError("INVALID_TEST_TARGET", "The test name contains unsupported characters.")
            selected = selected_path.relative_to(directory).as_posix() + ("::" + target.split("::", 1)[1] if "::" in target else "")
            if selected.startswith("-"):
                raise RepositoryError("INVALID_TEST_TARGET", "The test target cannot be a command option.")
        python_tests = any(any(directory.glob(pattern)) for pattern in ("tests", "test_*.py", "*_test.py"))
        if kind == "pytest" or (kind == "auto" and python_tests):
            return [self._python_executable(repository, directory), "-m", "pytest", "-q", *([selected] if selected else [])]
        package = directory / "package.json"
        data = json.loads(package.read_text(encoding="utf-8")) if package.exists() else {}
        if not isinstance(data, dict) or not isinstance(data.get("scripts", {}), dict):
            raise RepositoryError("INVALID_TEST_SCRIPT", "The package scripts configuration is invalid.")
        scripts = data.get("scripts", {})
        manager = self._package_manager(directory, repository.path)
        if kind == "script":
            if not isinstance(scripts.get(script), str) or not scripts[script].strip():
                raise RepositoryError("INVALID_TEST_SCRIPT", "The selected script is missing from this directory's package.json.")
            return [manager, "run", script]
        if kind == "static":
            for name in ("typecheck", "check", "lint"):
                if name in scripts:
                    return [manager, "run", name]
            if python_tests or (directory / "pyproject.toml").exists():
                paths = [selected.split("::", 1)[0]] if selected else [name for name in ("app", "src", "tests") if (directory / name).is_dir()]
                paths = paths or [file.name for file in directory.glob("*.py")]
                return [self._python_executable(repository, directory), "-m", "compileall", "-q", *paths] if paths else None
            return None
        if kind in {"auto", "frontend"} and "test" in scripts:
            dependencies = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            extra = ["--run"] if "vitest" in dependencies else ["--runInBand"] if "jest" in dependencies else []
            if selected:
                extra.append(selected)
            return [manager, "test", *(["--"] if manager == "npm" and extra else []), *extra]
        return None

    @staticmethod
    def _package_manager(directory: Path, root: Path) -> str:
        for current in (directory, *directory.parents):
            for marker, manager in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("package-lock.json", "npm"), ("npm-shrinkwrap.json", "npm")):
                if (current / marker).is_file():
                    return manager
            if current == root:
                break
        return "npm"

    @staticmethod
    def _python_executable(repository: RepositoryRecord, directory: Path | None = None) -> str:
        relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
        directories = [directory, repository.path] if directory and directory != repository.path else [repository.path]
        for current in directories:
            for name in (".venv", "venv"):
                environment = current / name
                if environment.is_dir():
                    executable = environment / relative
                    if not executable.is_file():
                        raise RepositoryError("PROJECT_PYTHON_UNAVAILABLE", "The project virtual environment has no Python executable. Recreate it before retrying.", {"path": str(executable)})
                    return str(executable)
        return sys.executable

    @staticmethod
    def _parse_failures(stdout: str, stderr: str) -> list[TestFailure]:
        failures: list[TestFailure] = []
        combined = f"{stdout}\n{stderr}"
        seen: set[tuple[str, int]] = set()
        for match in re.finditer(r"(?P<path>(?:[A-Za-z]:)?[A-Za-z0-9_./\\-]+\.(?:py|ts|tsx|js|jsx)):(?P<line>\d+)", combined):
            path = match.group("path").replace("\\", "/")
            line = int(match.group("line"))
            if (path, line) in seen:
                continue
            seen.add((path, line))
            end = combined.find("\n", match.start())
            context = combined[match.start():end if end >= 0 else len(combined)].strip()
            failures.append(TestFailure(path=path, line=line, message=context[:500]))
            if len(failures) >= 20:
                break
        if not failures and stderr.strip():
            failures.append(TestFailure(message=stderr.strip()[:500]))
        return failures
