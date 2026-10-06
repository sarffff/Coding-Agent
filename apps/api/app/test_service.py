from __future__ import annotations

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
from .models import TestFailure, TestKind, TestRunResponse
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

    def run(self, run_id: str, repository_id: str, kind: TestKind, timeout_seconds: int, target: str | None = None) -> TestRunResponse:
        repository = self.repositories.get(repository_id)
        with self._lock:
            cancel = self._active.setdefault(run_id, Event())
        started = time.perf_counter()
        command: list[str] = []
        try:
            command = self._detect_command(repository, kind, target) or []
            if not command:
                return self._result(run_id, kind, command, "not_found", started, stderr="No supported validation command was detected.")
            executable = shutil.which(command[0])
            if not executable:
                return self._result(run_id, kind, command, "not_found", started, stderr="The required validation executable is not available.")
            if cancel.is_set():
                return self._result(run_id, kind, command, "cancelled", started)
            # Capture output in the state directory: an inherited TMP may point at
            # a sandbox-owned path this process cannot write to.
            self.settings.state_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=self.settings.state_dir) as stdout, tempfile.TemporaryFile(dir=self.settings.state_dir) as stderr:
                process = subprocess.Popen(
                    [executable, *command[1:]], cwd=repository.path, stdout=stdout, stderr=stderr,
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
                result = self._result(run_id, kind, command, status, started, stdout=out[:self.OUTPUT_LIMIT].decode("utf-8", errors="replace"), stderr=err[:self.OUTPUT_LIMIT].decode("utf-8", errors="replace"))
                result.exit_code = process.returncode
                result.output_truncated = len(out) > self.OUTPUT_LIMIT or len(err) > self.OUTPUT_LIMIT
                if status == "blocked":
                    result.stderr = (result.stderr + "\n" if result.stderr else "") + "The validation process could not be started by the API process. Restart the API from a normal shell."
                if status == "timed_out":
                    result.stderr += "\nValidation exceeded its time limit."
                if status == "cancelled":
                    result.stderr += "\nValidation was cancelled."
                result.failed_tests = self._parse_failures(result.stdout, result.stderr) if status == "failed" else []
                return result
        except RepositoryError as exc:
            return self._result(run_id, kind, command, "blocked", started, stderr=exc.message)
        except (ValueError, TypeError):
            return self._result(run_id, kind, command, "blocked", started, stderr="The repository validation configuration is invalid.")
        except OSError:
            return self._result(run_id, kind, command, "not_found", started, stderr="The validation process could not be started.")
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
    def _result(run_id, kind, command, status, started, *, stdout="", stderr=""):
        return TestRunResponse(run_id=run_id, kind=kind, command=command, status=status, duration_ms=round((time.perf_counter() - started) * 1000), stdout=stdout, stderr=stderr)

    def _detect_command(self, repository: RepositoryRecord, kind: TestKind, target: str | None = None) -> list[str] | None:
        selected = None
        if target:
            relative = target.split("::", 1)[0]
            selected_path = self.repositories._safe_repo_path(repository, relative)
            if not selected_path.exists() or target.startswith("-"):
                raise RepositoryError("INVALID_TEST_TARGET", "Select an existing test file inside this repository.")
            if "::" in target and not re.fullmatch(r"[A-Za-z0-9_:]+", target.split("::", 1)[1]):
                raise RepositoryError("INVALID_TEST_TARGET", "The test name contains unsupported characters.")
            selected = selected_path.relative_to(repository.path).as_posix() + ("::" + target.split("::", 1)[1] if "::" in target else "")
        python_tests = any(any(repository.path.glob(pattern)) for pattern in ("tests", "test_*.py", "*_test.py"))
        if kind == "pytest" or (kind == "auto" and python_tests):
            return [sys.executable, "-m", "pytest", "-q", *([selected] if selected else [])]
        package = repository.path / "package.json"
        data = json.loads(package.read_text(encoding="utf-8")) if package.exists() else {}
        scripts = data.get("scripts", {})
        manager = "pnpm" if (repository.path / "pnpm-lock.yaml").exists() else "yarn" if (repository.path / "yarn.lock").exists() else "npm"
        if kind == "static":
            for script in ("typecheck", "check", "lint"):
                if script in scripts:
                    return [manager, "run", script]
            if python_tests or (repository.path / "pyproject.toml").exists():
                paths = [selected.split("::", 1)[0]] if selected else [name for name in ("app", "src", "tests") if (repository.path / name).is_dir()]
                paths = paths or [file.name for file in repository.path.glob("*.py")]
                return [sys.executable, "-m", "compileall", "-q", *paths] if paths else None
            return None
        if kind in {"auto", "frontend"} and "test" in scripts:
            dependencies = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            extra = ["--run"] if "vitest" in dependencies else ["--runInBand"] if "jest" in dependencies else []
            if selected:
                extra.append(selected)
            return [manager, "test", *(["--"] if manager == "npm" and extra else []), *extra]
        return None

    @staticmethod
    def _parse_failures(stdout: str, stderr: str) -> list[TestFailure]:
        failures: list[TestFailure] = []
        combined = f"{stdout}\n{stderr}"
        seen: set[tuple[str, int]] = set()
        for match in re.finditer(r"(?P<path>[A-Za-z0-9_./\\-]+\.(?:py|ts|tsx|js|jsx)):(?P<line>\d+)", combined):
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
