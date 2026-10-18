import json
import os
import shutil
from pathlib import Path

import pytest
from conftest import build_settings

from app.repository_service import RepositoryError, RepositoryService
from app.test_service import TestService as ValidationService


@pytest.fixture
def validation_workspace(tmp_path):
    directory = tmp_path / "project"
    (directory / "apps/api/tests").mkdir(parents=True)
    (directory / "apps/web").mkdir()
    settings = build_settings(directory, tmp_path)
    repositories = RepositoryService(settings)
    repository = repositories.get(repositories.register(str(directory)).id)
    return directory, repository, ValidationService(repositories, settings)


@pytest.mark.parametrize("passes", [True, False])
def test_nested_pytest_runs_in_its_directory_and_reports_repository_paths(validation_workspace, passes):
    directory, repository, service = validation_workspace
    (directory / "apps/api/tests/test_value.py").write_text(
        f"from pathlib import Path\n\ndef test_value():\n    assert Path.cwd().name == 'api'\n    assert {passes}\n",
        encoding="utf-8",
    )

    result = service.run("nested-python", repository.id, "pytest", 15, "tests/test_value.py::test_value", workdir="apps/api")

    assert result.status == ("passed" if passes else "failed"), result.stderr
    assert result.workdir == "apps/api"
    assert result.target == "tests/test_value.py::test_value"
    assert result.command[-1] == result.target
    assert not service.is_running("nested-python")
    if not passes:
        assert any(failure.path == "apps/api/tests/test_value.py" and failure.line == 5 for failure in result.failed_tests)


@pytest.mark.skipif(not shutil.which("node") or not shutil.which("npm"), reason="Node and npm are required for package script execution")
def test_named_script_runs_from_nested_package(validation_workspace):
    directory, repository, service = validation_workspace
    (directory / "apps/web/package.json").write_text(json.dumps({"scripts": {"verify": "node verify.js"}}), encoding="utf-8")
    (directory / "apps/web/verify.js").write_text(
        "const assert = require('node:assert/strict');\n"
        "assert.equal(require('node:path').basename(process.cwd()), 'web');\n"
        "console.log('nested-script-ok');\n",
        encoding="utf-8",
    )

    result = service.run("nested-script", repository.id, "script", 15, workdir="apps/web", script="verify")

    assert result.status == "passed", result.stderr
    assert result.command == ["npm", "run", "verify"]
    assert "nested-script-ok" in result.stdout
    assert result.workdir == "apps/web"
    assert result.script == "verify"
    assert result.target is None


@pytest.mark.parametrize("workdir", ["../outside", "apps/api/../../..", "apps/missing", "apps/api/file.py", ".git"])
def test_invalid_workdir_is_blocked_before_execution(validation_workspace, workdir):
    directory, repository, service = validation_workspace
    (directory / "apps/api/file.py").touch()

    result = service.run("unsafe-directory", repository.id, "pytest", 15, workdir=workdir)

    assert result.status == "blocked"
    assert result.command == []
    assert not service.is_running("unsafe-directory")


@pytest.mark.parametrize("target", ["../web/test_value.py", "../../../outside.py", "./-q", "tests/test_value.py::test_value;echo", "missing.py"])
def test_invalid_nested_target_is_blocked_before_execution(validation_workspace, target):
    directory, repository, service = validation_workspace
    (directory / "apps/web/test_value.py").touch()
    (directory / "apps/api/-q").touch()
    (directory / "apps/api/tests/test_value.py").touch()

    result = service.run("unsafe-target", repository.id, "pytest", 15, target, workdir="apps/api")

    assert result.status == "blocked"
    assert result.command == []


def test_absolute_workdir_and_target_are_rejected(validation_workspace):
    directory, repository, service = validation_workspace
    target = directory / "apps/api/tests/test_value.py"
    target.touch()

    assert service.run("absolute-directory", repository.id, "pytest", 15, workdir=str(directory / "apps/api")).status == "blocked"
    assert service.run("absolute-target", repository.id, "pytest", 15, str(target), workdir="apps/api").status == "blocked"


@pytest.mark.parametrize("script", [None, "", "missing", "--help", "verify --watch", "verify;echo", "verify&&echo"])
def test_script_requires_a_declared_name_without_arguments(validation_workspace, script):
    directory, repository, service = validation_workspace
    (directory / "apps/web/package.json").write_text(json.dumps({"scripts": {"verify": "node verify.js"}}), encoding="utf-8")

    with pytest.raises(RepositoryError) as error:
        service._detect_command(repository, "script", workdir="apps/web", script=script)

    assert error.value.code == "INVALID_TEST_SCRIPT"


def test_script_is_local_and_cannot_silently_ignore_other_options(validation_workspace):
    directory, repository, service = validation_workspace
    (directory / "package.json").write_text(json.dumps({"scripts": {"verify": "node verify.js"}}), encoding="utf-8")
    for options in ({"workdir": "apps/web", "script": "verify"}, {"script": "verify", "target": "apps/web"}):
        with pytest.raises(RepositoryError, match="script"):
            service._detect_command(repository, "script", **options)
    with pytest.raises(RepositoryError, match="script"):
        service._detect_command(repository, "pytest", script="verify")


@pytest.mark.parametrize("package", [[], {"scripts": []}, {"scripts": {"verify": 1}}, {"scripts": {"verify": " "}}])
def test_invalid_package_script_configuration_is_blocked(validation_workspace, package):
    directory, repository, service = validation_workspace
    (directory / "apps/web/package.json").write_text(json.dumps(package), encoding="utf-8")

    result = service.run("invalid-script-config", repository.id, "script", 15, workdir="apps/web", script="verify")

    assert result.status == "blocked"
    assert result.command == []


def test_nested_checks_use_nearest_package_lock_with_repository_root_limit(validation_workspace):
    directory, repository, service = validation_workspace
    (directory / "apps/web/package.json").write_text(json.dumps({"scripts": {"test": "node test.js", "check": "node check.js"}}), encoding="utf-8")
    (directory.parent / "pnpm-lock.yaml").touch()
    assert service._detect_command(repository, "frontend", workdir="apps/web") == ["npm", "test"]
    (directory / "pnpm-lock.yaml").touch()
    assert service._detect_command(repository, "script", workdir="apps/web", script="check") == ["pnpm", "run", "check"]
    (directory / "apps/yarn.lock").touch()
    assert service._detect_command(repository, "static", workdir="apps/web") == ["yarn", "run", "check"]
    (directory / "apps/web/package-lock.json").touch()
    assert service._detect_command(repository, "frontend", workdir="apps/web") == ["npm", "test"]


def test_nested_python_prefers_local_environment_then_repository_environment(validation_workspace):
    directory, repository, service = validation_workspace
    interpreter_path = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    root_python = directory / ".venv" / interpreter_path
    root_python.parent.mkdir(parents=True)
    root_python.touch()
    for kind in ("pytest", "auto", "static"):
        assert service._detect_command(repository, kind, workdir="apps/api")[0] == str(root_python)

    local_python = directory / "apps/api/venv" / interpreter_path
    local_python.parent.mkdir(parents=True)
    local_python.touch()
    assert service._detect_command(repository, "pytest", workdir="apps/api")[0] == str(local_python)

    (directory / "apps/api/.venv").mkdir()
    with pytest.raises(RepositoryError) as error:
        service._detect_command(repository, "pytest", workdir="apps/api")
    assert error.value.code == "PROJECT_PYTHON_UNAVAILABLE"


def test_validation_hash_tracks_dependencies_new_files_and_package_configuration(validation_workspace):
    directory, repository, service = validation_workspace
    dependency = directory / "apps/api/dependency.py"
    dependency.write_text("VALUE = 1\n", encoding="utf-8")
    original = service.input_hash(repository.id)
    assert service.input_hash(repository.id) == original

    dependency.write_text("VALUE = 2\n", encoding="utf-8")
    changed = service.input_hash(repository.id)
    assert changed != original
    added = directory / "apps/api/tests/test_extra.py"
    added.write_text("def test_extra(): pass\n", encoding="utf-8")
    assert service.input_hash(repository.id) != changed
    added.unlink()
    assert service.input_hash(repository.id) == changed

    package = directory / "apps/web/package.json"
    package.write_text('{"scripts":{"check":"node check.js"}}', encoding="utf-8")
    configured = service.input_hash(repository.id)
    assert configured != changed
    package.write_text('{"scripts":{"check":"node other.js"}}', encoding="utf-8")
    assert service.input_hash(repository.id) != configured
    before_rename = service.input_hash(repository.id)
    dependency.rename(directory / "apps/api/renamed.py")
    renamed = service.input_hash(repository.id)
    assert renamed != before_rename
    (directory / "apps/api/renamed.py").unlink()
    assert service.input_hash(repository.id) != renamed


def test_validation_hash_streams_files_larger_than_context_limit(validation_workspace):
    directory, repository, service = validation_workspace
    service.settings.max_file_size_bytes = 4
    asset = directory / "fixture.bin"
    asset.write_bytes(b"\0" * 1024 + b"a")
    original = service.input_hash(repository.id)
    asset.write_bytes(b"\0" * 1024 + b"b")
    assert service.input_hash(repository.id) != original


def test_validation_hash_ignores_generated_and_protected_inputs_without_reading_them(validation_workspace, monkeypatch):
    directory, repository, service = validation_workspace
    original = service.input_hash(repository.id)
    excluded = []
    for name in ("dist", ".forge", "__pycache__", "apps/web/dist", "credentials"):
        generated = directory / name / "generated.py"
        generated.parent.mkdir(parents=True)
        generated.write_text("must not be read", encoding="utf-8")
        excluded.append(generated)
    for name in (".env", ".env.local", "credentials.json", "private.pem"):
        secret = directory / name
        secret.write_text("must not be read", encoding="utf-8")
        excluded.append(secret)
    real_open = Path.open

    def checked_open(path, *args, **kwargs):
        assert path not in excluded
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", checked_open)
    assert service.input_hash(repository.id) == original


def test_validation_hash_skips_file_and_directory_symlinks(validation_workspace, monkeypatch):
    directory, repository, service = validation_workspace
    original = service.input_hash(repository.id)
    outside = directory.parent / "outside"
    outside.mkdir()
    secret = outside / "secret.py"
    secret.write_text("must not be read", encoding="utf-8")
    try:
        (directory / "linked.py").symlink_to(secret)
        (directory / "linked-directory").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is not supported by this environment")
    real_open = Path.open

    def checked_open(path, *args, **kwargs):
        assert path.resolve() != secret
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", checked_open)
    assert service.input_hash(repository.id) == original


def test_validation_hash_refuses_incomplete_input_scopes(validation_workspace):
    _, repository, service = validation_workspace
    service.settings.max_tree_entries = 1

    with pytest.raises(RepositoryError) as error:
        service.input_hash(repository.id)

    assert error.value.code == "VALIDATION_SCOPE_TOO_LARGE"


def test_validation_hash_refuses_unreadable_input(validation_workspace, monkeypatch):
    directory, repository, service = validation_workspace
    (directory / "source.py").write_text("VALUE = 1\n", encoding="utf-8")

    def denied_open(path, *args, **kwargs):
        raise PermissionError("Input cannot be read")

    monkeypatch.setattr(Path, "open", denied_open)
    with pytest.raises(RepositoryError) as error:
        service.input_hash(repository.id)

    assert error.value.code == "VALIDATION_INPUT_UNAVAILABLE"


def test_default_checks_detect_this_project_scripts_without_exposing_commands(validation_workspace):
    directory, repository, service = validation_workspace
    scripts = {"api:test": "node scripts/py.js -m pytest apps/api/tests -q", "check": "pnpm --filter desktop typecheck", "build": "pnpm --filter desktop build", "dev": "pnpm dev"}
    (directory / "package.json").write_text(json.dumps({"scripts": scripts}), encoding="utf-8")

    checks = service.default_checks(repository.id)

    assert [(check.id, check.script) for check in checks] == [("api-tests", "api:test"), ("static", "check"), ("build", "build")]
    assert all(check.kind == "script" and check.workdir == "." and check.timeout_seconds == 300 and check.label for check in checks)
    assert all(command not in json.dumps([check.model_dump() for check in checks]) for command in scripts.values())


def test_default_checks_deduplicate_test_commands_and_choose_first_static_check(validation_workspace):
    directory, repository, service = validation_workspace
    package = directory / "package.json"
    package.write_text(json.dumps({"scripts": {"test": "pytest", "api:test": " pytest ", "typecheck": "tsc", "check": "lint", "lint": "eslint", "build": " "}}), encoding="utf-8")
    assert [(check.id, check.script) for check in service.default_checks(repository.id)] == [("test", "test"), ("static", "typecheck")]

    package.write_text(json.dumps({"scripts": {"test": "node test.js", "api:test": "pytest", "typecheck": None, "check": " ", "lint": "eslint", "build": 1}}), encoding="utf-8")
    assert [(check.id, check.script) for check in service.default_checks(repository.id)] == [("test", "test"), ("api-tests", "api:test"), ("static", "lint")]


@pytest.mark.parametrize("manifest", [None, "invalid json", "[]", "{}", '{"scripts":[]}', '{"scripts":{"test":null,"check":3,"build":" "}}'])
def test_default_checks_ignore_missing_or_invalid_root_manifest_and_nested_packages(validation_workspace, manifest):
    directory, repository, service = validation_workspace
    (directory / "apps/web/package.json").write_text('{"scripts":{"test":"node test.js","build":"node build.js"}}', encoding="utf-8")
    if manifest is not None:
        (directory / "package.json").write_text(manifest, encoding="utf-8")

    assert service.default_checks(repository.id) == []
