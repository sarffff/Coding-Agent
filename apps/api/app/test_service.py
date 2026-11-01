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


# Test/build by-products that validation itself rewrites; hashing them would turn every pass into
# "files changed during testing". Only used for the validation hash, never for read/edit policy.
_GENERATED_NAMES = {"coverage", "htmlcov", ".nyc_output", ".mypy_cache", ".tox", ".nox", ".hypothesis", ".gradle", "target", ".coverage", ".eslintcache"}
_GENERATED_SUFFIXES = (".egg-info", ".tsbuildinfo", ".pyc")


# Programs the drafting model may run (argv, no shell). ponytail: program allowlist only — arguments
# are free (python -c can do anything), so commands run with the API user's rights on the host and
# are offered only outside conservative mode; move to a container when untrusted repos matter.
COMMAND_EXECUTABLES = frozenset({"python", "python3", "pytest", "node", "npm", "pnpm", "yarn", "go", "cargo", "ruff", "mypy", "tsc", "eslint"})


def _generated(name: str) -> bool:
    lower = name.lower()
    return lower in _GENERATED_NAMES or lower.endswith(_GENERATED_SUFFIXES) or lower.startswith(".coverage.")


class TestService:
    OUTPUT_LIMIT = 20_000
    COMMAND_SUFFIX = ":command"  # model commands get their own slot so is_running(run_id) stays validation-only

    def __init__(self, repositories: RepositoryService, settings: Settings, runtime_source=None):
        self.repositories = repositories
        self.settings = settings
        self.runtime_source = runtime_source
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
            for key in (run_id, run_id + self.COMMAND_SUFFIX):
                event = self._active.get(key)
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
        files = 0
        try:
            entries = self._git_inputs(repository) if repository.has_git else self._walk_inputs(repository)
            for relative, is_directory in entries:
                digest.update((relative + "\0").encode("utf-8"))
                if is_directory:
                    digest.update(b"directory\0")
                    continue
                files += 1
                if files > self.settings.max_tree_entries:
                    raise RepositoryError("VALIDATION_SCOPE_TOO_LARGE", "The validation inputs exceed the repository entry limit; narrow the workspace before retrying.")
                try:
                    path = self.repositories._safe_repo_path(repository, relative)
                except RepositoryError as exc:
                    if exc.code == "SYMLINK_NOT_ALLOWED":
                        continue
                    raise
                if not path.exists():  # a tracked file the task deleted
                    digest.update(b"missing\0")
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

    def _git_inputs(self, repository: RepositoryRecord):
        """Tracked plus untracked-but-not-ignored files: .gitignore already names this project's outputs."""
        listed = self.repositories._run_git(repository.path, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        for relative in sorted(set(filter(None, listed.split("\0")))):
            parts = Path(relative).parts
            if is_protected_path(relative) or any(_generated(part) for part in parts) or (repository.path / relative).is_symlink():
                continue
            yield relative, False

    def _walk_inputs(self, repository: RepositoryRecord):
        def walk_error(error: OSError) -> None:
            raise error

        for current, directories, files in os.walk(repository.path, onerror=walk_error):
            base = Path(current)
            relative_base = base.relative_to(repository.path)
            directories[:] = sorted(name for name in directories if not self._skip_input(base, relative_base, name))
            for name in directories:
                yield (relative_base / name).as_posix(), True
            for name in sorted(files):
                if not self._skip_input(base, relative_base, name):
                    yield (relative_base / name).as_posix(), False

    @staticmethod
    def _skip_input(base: Path, relative_base: Path, name: str) -> bool:
        return is_protected_path(relative_base / name) or _generated(name) or (base / name).is_symlink()

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
            result = self._execute(run_id, kind, [executable, *command[1:]], command, directory, cancel, min(timeout_seconds, self.settings.command_timeout_seconds), started, details)
            if result.status == "timed_out":
                result.stderr += "\nValidation exceeded its time limit."
            if result.status == "cancelled":
                result.stderr += "\nValidation was cancelled."
            result.failed_tests = self._parse_failures(result.stdout, result.stderr) if result.status == "failed" else []
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

    def run_command(self, run_id: str, repository_id: str, argv: list[str], timeout_seconds: int = 120, *, reserved: bool = False) -> TestRunResponse:
        """Run one allowlisted program for the drafting model, no shell, in the repository root.

        Shares the validation cancel path (cancel(run_id) also stops it) but not its reservation,
        so the task never looks like it is "waiting for tests" while the model inspects something.
        """
        repository = self.repositories.get(repository_id)
        key = f"{run_id}{self.COMMAND_SUFFIX}"
        if not reserved:
            self.reserve(key)
        started = time.perf_counter()
        details = {"workdir": ".", "target": None, "script": None}
        try:
            name = argv[0].lower().removesuffix(".exe")
            if name not in COMMAND_EXECUTABLES:
                return self._result(key, "auto", argv, "blocked", started, stderr=f"{argv[0]!r} is not an allowed program. Allowed: {', '.join(sorted(COMMAND_EXECUTABLES))}.", **details)
            if name in {"python", "python3", "pytest"}:
                executable, args = self._python_executable(repository), ["-m", "pytest", *argv[1:]] if name == "pytest" else argv[1:]
            else:
                executable, args = shutil.which(name), argv[1:]
            if not executable:
                return self._result(key, "auto", argv, "not_found", started, stderr=f"{argv[0]} is not available on PATH.", **details)
            if self._active[key].is_set():
                return self._result(key, "auto", argv, "cancelled", started, **details)
            limit = min(timeout_seconds, self.settings.command_timeout_seconds)
            return self._execute(key, "auto", [executable, *args], argv, repository.path, self._active[key], limit, started, details)
        except RepositoryError as exc:
            return self._result(key, "auto", argv, "blocked", started, stderr=exc.message, **details)
        except OSError:
            return self._result(key, "auto", argv, "not_found", started, stderr="The command could not be started.", **details)
        finally:
            self.release(key)

    def _execute(self, run_id: str, kind: TestKind, argv: list[str], command: list[str], directory: Path, cancel: Event, limit: int, started: float, details: dict) -> TestRunResponse:
        # Capture output in the state directory: an inherited TMP may point at
        # a sandbox-owned path this process cannot write to.
        self.settings.state_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=self.settings.state_dir) as stdout, tempfile.TemporaryFile(dir=self.settings.state_dir) as stderr:
            process = subprocess.Popen(
                argv, cwd=directory, stdout=stdout, stderr=stderr,
                env=child_env(scrub_secrets=True),  # repository and model-chosen code never sees API credentials
                shell=False, start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
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
            stdout.seek(0)
            stderr.seek(0)
            out = stdout.read(self.OUTPUT_LIMIT + 1)
            err = stderr.read(self.OUTPUT_LIMIT + 1)
        result = self._result(run_id, kind, command, status, started, stdout=out[:self.OUTPUT_LIMIT].decode("utf-8", errors="replace"), stderr=err[:self.OUTPUT_LIMIT].decode("utf-8", errors="replace"), **details)
        result.exit_code = process.returncode
        result.output_truncated = len(out) > self.OUTPUT_LIMIT or len(err) > self.OUTPUT_LIMIT
        if status == "passed" and process.returncode:
            reason = self._blocked_reason(argv, process.returncode, result.stdout, result.stderr)
            result.status = "blocked" if reason else "failed"
            if reason:
                result.stderr = (result.stderr + "\n" if result.stderr else "") + reason
        return result

    @staticmethod
    def _blocked_reason(argv: list[str], exit_code: int, stdout: str, stderr: str) -> str | None:
        if is_process_startup_failure(exit_code):
            return "The validation process could not be started by the API process. Restart the API from a normal shell."
        program = Path(argv[0]).stem.lower()
        pytest_command = program == "pytest" or (program.startswith("python") and argv[1:3] == ["-m", "pytest"])
        collection = "Pytest could not complete collection or was interrupted. Check the reported code, dependencies, and test configuration before retrying."
        if pytest_command and exit_code in {2, 3, 4, 5}:
            return {
                2: collection,
                3: "Pytest stopped because of an internal error. Review its diagnostic before retrying validation.",
                4: "Pytest rejected its configuration or command arguments. Review the validation setup before retrying.",
                5: "Pytest did not collect any tests. Check the validation directory and test selection before retrying.",
            }[exit_code]
        package_command = program in {"npm", "pnpm", "yarn"}
        if not pytest_command and not package_command:
            return None
        output = f"{stdout}\n{stderr}"
        if re.search(r"(?m)^\s*(?:.+[/\\])?python(?:\d+(?:\.\d+)*)?(?:\.exe)?: No module named ['\"]?pytest['\"]?\s*$", output):
            return "pytest is not installed in the selected Python environment. Install the project's test dependencies before retrying."
        if package_command:
            # ponytail: match explicit runner diagnostics only; arbitrary script exits and application imports remain failures.
            if re.search(r"(?m)^[=\s]*Interrupted: \d+ errors? during collection[=\s]*$", output):
                return collection
            if re.search(r"(?m)^ERROR: usage: .*pytest", output):
                return "Pytest rejected its configuration or command arguments. Review the validation setup before retrying."
            if re.search(r"(?m)^INTERNALERROR>", output):
                return "Pytest stopped because of an internal error. Review its diagnostic before retrying validation."
            if re.search(r"(?m)^\s*No test(?: files|s) found, exiting with code [1-9]\d*\s*$|^[=\s]*no tests ran in \d+(?:\.\d+)?s[=\s]*$", output):
                return "The test runner did not collect any tests. Check the validation directory and test selection before retrying."
            if re.search(r"ERR_PNPM_(?:NO_IMPORTER_MANIFEST_FOUND|NO_SCRIPT)\b|(?m:^\s*npm (?:ERR!|error) Missing script:)", output):
                return "The package configuration does not provide the selected validation command. Review package.json and the validation setup before retrying."
            runners = r"(?:vitest|jest|mocha|eslint|tsc|vite|tsx|pytest|node|python3?)"
            if re.search(rf"(?m)^(?:(?:/bin/)?(?:ba)?sh: (?:\d+: )?{runners}: (?:not found|command not found)|'{runners}' is not recognized as an internal or external command,?)\s*$", output):
                return "The test runner or package dependencies are unavailable. Check the reported command and install the project's validation dependencies before retrying."
        return None

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

    def _python_executable(self, repository: RepositoryRecord, directory: Path | None = None) -> str:
        relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
        directories = [directory, repository.path] if directory and directory != repository.path else [repository.path]
        source_id = self.runtime_source(repository.id) if self.runtime_source else None
        if source_id:
            source = self.repositories.get(source_id)
            if directory and directory != repository.path:
                directories.append(source.path / directory.relative_to(repository.path))
            directories.append(source.path)
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
