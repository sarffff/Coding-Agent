from __future__ import annotations

import shutil
import subprocess
import time
import re
from pathlib import Path

from .config import Settings
from .models import TestFailure, TestKind, TestRunResponse
from .repository_service import RepositoryError, RepositoryRecord, RepositoryService


class TestService:
    ALLOWED_EXECUTABLES = {"pytest", "python", "python3", "pnpm", "npm", "yarn", "node"}

    def __init__(self, repositories: RepositoryService, settings: Settings):
        self.repositories = repositories
        self.settings = settings

    def run(self, run_id: str, repository_id: str, kind: TestKind, timeout_seconds: int) -> TestRunResponse:
        repository = self.repositories.get(repository_id)
        command = self._detect_command(repository, kind)
        if not command:
            return TestRunResponse(
                run_id=run_id,
                kind=kind,
                command=[],
                status="not_found",
                duration_ms=0,
                stdout="",
                stderr="No supported local test command was detected.",
            )
        if command[0] not in self.ALLOWED_EXECUTABLES or shutil.which(command[0]) is None:
            return TestRunResponse(
                run_id=run_id,
                kind=kind,
                command=command,
                status="not_found",
                duration_ms=0,
                stdout="",
                stderr=f"Executable is not available: {command[0]}",
            )

        started = time.perf_counter()
        try:
            result = subprocess.run(
                command,
                cwd=repository.path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(timeout_seconds, self.settings.command_timeout_seconds),
                check=False,
                shell=False,
            )
            status = "passed" if result.returncode == 0 else "failed"
            return TestRunResponse(
                run_id=run_id,
                kind=kind,
                command=command,
                status=status,
                exit_code=result.returncode,
                duration_ms=round((time.perf_counter() - started) * 1000),
                stdout=result.stdout[:20_000],
                stderr=result.stderr[:20_000],
                output_truncated=len(result.stdout) > 20_000 or len(result.stderr) > 20_000,
                failed_tests=self._parse_failures(result.stdout, result.stderr) if status == "failed" else [],
            )
        except subprocess.TimeoutExpired as exc:
            return TestRunResponse(
                run_id=run_id,
                kind=kind,
                command=command,
                status="timed_out",
                duration_ms=round((time.perf_counter() - started) * 1000),
                stdout=(exc.stdout or "")[:20_000],
                stderr="Test command exceeded the configured timeout.",
                output_truncated=True,
                failed_tests=[],
            )
        except OSError as exc:
            raise RepositoryError("TEST_EXECUTION_FAILED", "The test command could not be started.") from exc

    def _detect_command(self, repository: RepositoryRecord, kind: TestKind) -> list[str] | None:
        if kind == "pytest" or (kind == "auto" and self._has_python_tests(repository.path)):
            return ["pytest", "-q"]
        if kind == "frontend" or (kind == "auto" and self._has_frontend_tests(repository.path)):
            if (repository.path / "pnpm-lock.yaml").exists():
                return ["pnpm", "test"]
            if (repository.path / "yarn.lock").exists():
                return ["yarn", "test"]
            return ["npm", "test", "--", "--runInBand"]
        return None

    @staticmethod
    def _has_python_tests(path: Path) -> bool:
        return any(any(path.glob(pattern)) for pattern in ("tests", "test_*.py", "*_test.py"))

    @staticmethod
    def _has_frontend_tests(path: Path) -> bool:
        package_json = path / "package.json"
        if not package_json.exists():
            return False
        try:
            import json

            scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})
            return "test" in scripts
        except (OSError, ValueError):
            return False

    @staticmethod
    def _parse_failures(stdout: str, stderr: str) -> list[TestFailure]:
        """Extract a small, safe failure index from common pytest/Jest output."""
        failures: list[TestFailure] = []
        combined = f"{stdout}\n{stderr}"
        seen: set[tuple[str | None, int | None, str | None]] = set()
        for match in re.finditer(r"(?P<path>[A-Za-z0-9_./\\-]+\.(?:py|ts|tsx|js|jsx)):(?P<line>\d+)", combined):
            path = match.group("path").replace("\\", "/")
            line = int(match.group("line"))
            key = (path, line, None)
            if key in seen:
                continue
            seen.add(key)
            context = combined[match.start(): combined.find("\n", match.start()) if "\n" in combined[match.start():] else len(combined)].strip()
            failures.append(TestFailure(path=path, line=line, message=context[:500]))
            if len(failures) >= 20:
                break
        if not failures and stderr.strip():
            failures.append(TestFailure(message=stderr.strip()[:500]))
        return failures
