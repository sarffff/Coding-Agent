import sys
import time
from threading import Event

import pytest
from conftest import build_settings

from app.repository_service import RepositoryService
from app.test_service import TestService as ValidationService


@pytest.fixture
def validation_workspace(tmp_path):
    directory = tmp_path / "project"
    directory.mkdir()
    settings = build_settings(directory, tmp_path)
    repositories = RepositoryService(settings)
    repository = repositories.get(repositories.register(str(directory)).id)
    return directory, repository, ValidationService(repositories, settings)


@pytest.mark.parametrize("source", ["import missing_forge_application_module\n", "def test_invalid(:\n    pass\n"])
def test_pytest_collection_error_blocks_without_guessing_an_environment_cause(validation_workspace, source):
    directory, repository, service = validation_workspace
    (directory / "test_app.py").write_text(source, encoding="utf-8")

    result = service.run("collection", repository.id, "auto", 15)

    assert result.status == "blocked", result.stderr
    assert result.exit_code == 2
    assert result.failed_tests == []
    assert "collection" in result.stderr.lower()
    assert "code, dependencies, and test configuration" in result.stderr
    assert "Restart the API" not in result.stderr


def test_pytest_invalid_options_are_blocked_with_configuration_diagnostic(validation_workspace):
    directory, repository, service = validation_workspace
    (directory / "pytest.ini").write_text("[pytest]\naddopts = --unknown-forge-option\n", encoding="utf-8")
    (directory / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    result = service.run("usage", repository.id, "pytest", 15)

    assert result.status == "blocked", result.stderr
    assert result.exit_code == 4
    assert "configuration or command arguments" in result.stderr
    assert "Restart the API" not in result.stderr


def test_pytest_no_tests_is_blocked_rather_than_passed_or_repairable(validation_workspace):
    _, repository, service = validation_workspace

    result = service.run("empty", repository.id, "pytest", 15)

    assert result.status == "blocked", result.stderr
    assert result.exit_code == 5
    assert "did not collect any tests" in result.stderr
    assert result.failed_tests == []


def test_real_pytest_assertion_failure_remains_repairable(validation_workspace):
    directory, repository, service = validation_workspace
    (directory / "test_app.py").write_text("def test_value():\n    assert 1 == 2\n", encoding="utf-8")

    result = service.run("assertion", repository.id, "pytest", 15)

    assert result.status == "failed", result.stderr
    assert result.exit_code == 1
    assert any(failure.path == "test_app.py" and failure.line == 2 for failure in result.failed_tests)


def execute_result(service, directory, monkeypatch, argv, exit_code, *, stdout="", stderr="", cancel=False, timeout=False):
    class Process:
        returncode = exit_code

        def poll(self):
            return None if cancel or timeout else self.returncode

        def wait(self):
            return self.returncode

    def spawn(*args, **kwargs):
        kwargs["stdout"].write(stdout.encode("utf-8"))
        kwargs["stderr"].write(stderr.encode("utf-8"))
        return Process()

    monkeypatch.setattr("app.test_service.subprocess.Popen", spawn)
    monkeypatch.setattr(service, "_stop", lambda process: None)
    event = Event()
    if cancel:
        event.set()
    return service._execute("result", "script", argv, argv, directory, event, 0 if timeout else 15, time.perf_counter(), {})


@pytest.mark.parametrize("exit_code, diagnostic", [(2, "collection"), (3, "internal error"), (4, "configuration or command arguments"), (5, "did not collect any tests")])
def test_pytest_exit_semantics_follow_actual_command_not_selected_kind(validation_workspace, monkeypatch, exit_code, diagnostic):
    directory, _, service = validation_workspace

    result = execute_result(service, directory, monkeypatch, [sys.executable, "-m", "pytest", "-q"], exit_code)

    assert result.status == "blocked"
    assert diagnostic in result.stderr
    assert "Restart the API" not in result.stderr


@pytest.mark.parametrize("exit_code", [1, 2, 3, 4, 5])
def test_arbitrary_script_exit_codes_do_not_use_pytest_semantics(validation_workspace, monkeypatch, exit_code):
    directory, _, service = validation_workspace

    result = execute_result(service, directory, monkeypatch, ["node", "build.js"], exit_code, stderr="Build failed")

    assert result.status == "failed"
    assert result.stderr == "Build failed"


@pytest.mark.parametrize("output, diagnostic", [
    ("No test files found, exiting with code 1\n", "did not collect any tests"),
    ("No tests found, exiting with code 1\n", "did not collect any tests"),
    ("sh: 1: vitest: not found\n", "test runner or package dependencies"),
    ("'jest' is not recognized as an internal or external command,\noperable program or batch file.\n", "test runner or package dependencies"),
    ("ERR_PNPM_NO_IMPORTER_MANIFEST_FOUND No package.json found\n", "package configuration"),
    ("npm error Missing script: \"test\"\n", "package configuration"),
    ("Interrupted: 1 error during collection\n", "collection"),
])
def test_recognized_package_runner_blockers_have_neutral_diagnostics(validation_workspace, monkeypatch, output, diagnostic):
    directory, _, service = validation_workspace

    result = execute_result(service, directory, monkeypatch, ["npm", "run", "test"], 1, stderr=output)

    assert result.status == "blocked"
    assert diagnostic in result.stderr
    assert "Restart the API" not in result.stderr


@pytest.mark.parametrize("argv, output", [
    (["node", "build.js"], "No tests found, exiting with code 1\n"),
    (["npm", "run", "test"], "Error: Cannot find module './newly-broken-import.js'\n"),
    (["npm", "run", "test"], "AssertionError: expected 'No tests found' to equal 'tests found'\n"),
    (["npm", "run", "test"], "src/app.ts(3,4): error TS2307: Cannot find module './missing-source'.\n"),
    ([sys.executable, "-m", "pytest"], "ModuleNotFoundError: No module named 'application_dependency'\n"),
    ([sys.executable, "-m", "pytest"], "ModuleNotFoundError: No module named 'pytest'\n"),
])
def test_unknown_errors_and_project_import_regressions_are_not_environment_blockers(validation_workspace, monkeypatch, argv, output):
    directory, _, service = validation_workspace

    result = execute_result(service, directory, monkeypatch, argv, 1, stderr=output)

    assert result.status == "failed"
    assert result.stderr == output


def test_missing_pytest_runner_is_explicitly_blocked(validation_workspace, monkeypatch):
    directory, _, service = validation_workspace

    result = execute_result(service, directory, monkeypatch, [sys.executable, "-m", "pytest"], 1, stderr="python.exe: No module named pytest\n")

    assert result.status == "blocked"
    assert "pytest is not installed" in result.stderr


@pytest.mark.parametrize("cancel, timeout, status", [(True, False, "cancelled"), (False, True, "timed_out")])
def test_interrupt_status_wins_over_runner_diagnostics(validation_workspace, monkeypatch, cancel, timeout, status):
    directory, _, service = validation_workspace

    result = execute_result(service, directory, monkeypatch, [sys.executable, "-m", "pytest"], 2, stderr="Interrupted: 1 error during collection\n", cancel=cancel, timeout=timeout)

    assert result.status == status
    assert result.stderr == "Interrupted: 1 error during collection\n"


def test_startup_failure_keeps_existing_api_process_diagnostic(validation_workspace, monkeypatch):
    directory, _, service = validation_workspace

    result = execute_result(service, directory, monkeypatch, [sys.executable, "-m", "pytest"], 3221225794)

    assert result.status == "blocked"
    assert "Restart the API from a normal shell" in result.stderr
