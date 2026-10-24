import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models import PatchFile, ValidationCheck
from app.repository_service import RepositoryError, RepositoryRecord
from app.validation_guard import capture_validation_guard, validation_guard_changes
from conftest import git_output


@pytest.fixture
def repository(tmp_path):
    directory = tmp_path / "repo"
    directory.mkdir()
    return RepositoryRecord(id="guard", name="guard", path=directory, registered_at=datetime.now(timezone.utc), has_git=False)


def write(repository, relative, content):
    path = repository.path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")
    return path


def script_check(script="test", workdir="."):
    return ValidationCheck(id="test", label="Tests", kind="script", script=script, workdir=workdir)


@pytest.mark.parametrize("relative", ["test_api.py", "value_test.py", "src/value.test.ts", "src/value.spec.js", "tests/data/input.json", "__tests__/fixture.txt", "conftest.py"])
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_existing_tests_are_guarded_in_memory_and_on_disk(repository, relative, operation):
    path = write(repository, relative, "original acceptance\n")
    captured = capture_validation_guard(repository, [])
    assert captured[f"file:{relative}"] and all(len(value) == 64 for value in captured.values())
    patch = PatchFile(path=relative, operation=operation, content="weakened acceptance\n" if operation == "update" else None)
    assert validation_guard_changes(repository, captured, [patch]) == [relative]
    assert path.read_text(encoding="utf-8") == "original acceptance\n"
    if operation == "delete":
        path.unlink()
    else:
        path.write_text(patch.content, encoding="utf-8", newline="")
    assert validation_guard_changes(repository, captured) == [relative]


def test_new_tests_source_changes_and_dependencies_are_allowed(repository):
    write(repository, "test_original.py", "assert True\n")
    write(repository, "app.py", "value = 1\n")
    package = {"scripts": {"test": "node --test"}, "dependencies": {"old": "1"}}
    write(repository, "package.json", json.dumps(package))
    captured = capture_validation_guard(repository, [script_check()])
    package["dependencies"]["new"] = "2"
    changes = [PatchFile(path="test_new.py", operation="create", content="assert True\n"), PatchFile(path="app.py", content="value = 2\n"), PatchFile(path="package.json", content=json.dumps(package))]
    assert validation_guard_changes(repository, captured, changes) == []
    write(repository, "test_new.py", "assert True\n")
    write(repository, "app.py", "value = 2\n")
    write(repository, "package.json", json.dumps(package))
    assert validation_guard_changes(repository, captured) == []
    assert "original acceptance" not in str(captured)


@pytest.mark.parametrize("workdir", [".", "apps/web"])
@pytest.mark.parametrize("script", ["test", "test:unit", "test:leaf", "pretest", "posttest"])
def test_selected_scripts_their_dependencies_and_lifecycle_are_guarded(repository, workdir, script):
    relative = (Path(workdir) / "package.json").as_posix()
    scripts = {"test": "npm run test:unit", "test:unit": "pnpm run test:leaf", "test:leaf": "node --test", "pretest": "node prepare.js", "posttest": "node cleanup.js", "start": "node server.js"}
    write(repository, relative, json.dumps({"scripts": scripts}))
    captured = capture_validation_guard(repository, [script_check(workdir=workdir)])
    scripts[script] = "node -e 'process.exit(0)'"
    patch = PatchFile(path=relative, content=json.dumps({"scripts": scripts}))
    assert validation_guard_changes(repository, captured, [patch]) == [relative]


def test_unselected_scripts_and_dependencies_are_allowed(repository):
    package = {"scripts": {"test": "node --test", "start": "node old.js"}}
    write(repository, "package.json", json.dumps(package))
    captured = capture_validation_guard(repository, [script_check()])
    package["scripts"]["start"] = "node new.js"
    package["dependencies"] = {"library": "1"}
    assert validation_guard_changes(repository, captured, [PatchFile(path="package.json", content=json.dumps(package))]) == []


def test_ambiguous_script_reference_guards_the_scripts_mapping(repository):
    scripts = {"test": "npm run $TEST_SCRIPT", "test:unit": "node --test"}
    write(repository, "package.json", json.dumps({"scripts": scripts}))
    captured = capture_validation_guard(repository, [script_check()])
    scripts["test:unit"] = "echo passed"
    assert validation_guard_changes(repository, captured, [PatchFile(path="package.json", content=json.dumps({"scripts": scripts}))]) == ["package.json"]


@pytest.mark.parametrize("script", ["test:missing", "pretest", "posttest"])
def test_new_scripts_that_change_selected_validation_are_guarded(repository, script):
    package = {"scripts": {"test": "npm run test:missing --if-present"}}
    write(repository, "package.json", json.dumps(package))
    captured = capture_validation_guard(repository, [script_check()])
    package["scripts"][script] = "echo passed"
    assert validation_guard_changes(repository, captured, [PatchFile(path="package.json", content=json.dumps(package))]) == ["package.json"]


def test_selected_validation_wrapper_files_are_guarded(repository):
    write(repository, "package.json", json.dumps({"scripts": {"test": "node scripts/check.mjs"}}))
    write(repository, "scripts/check.mjs", "runTests();\n")
    captured = capture_validation_guard(repository, [script_check()])
    assert validation_guard_changes(repository, captured, [PatchFile(path="scripts/check.mjs", content="console.log('passed');\n")]) == ["scripts/check.mjs"]


def test_inline_script_quotes_do_not_break_wrapper_detection(repository):
    write(repository, "package.json", json.dumps({"scripts": {"test": 'node -e "console.log(1 || 2);" && node scripts/check.mjs'}}))
    write(repository, "scripts/check.mjs", "runTests();\n")
    captured = capture_validation_guard(repository, [script_check()])
    assert "file:scripts/check.mjs" in captured


@pytest.mark.parametrize("command", ["cd apps/web && npm run test", "npm --prefix apps/web run test", "pnpm -r run test"])
def test_cross_package_indirection_conservatively_guards_existing_package_scripts(repository, command):
    write(repository, "package.json", json.dumps({"scripts": {"test": command}}))
    write(repository, "apps/web/package.json", json.dumps({"scripts": {"test": "node --test"}}))
    captured = capture_validation_guard(repository, [script_check()])
    changed = PatchFile(path="apps/web/package.json", content=json.dumps({"scripts": {"test": "echo passed"}, "dependencies": {"new": "1"}}))
    assert validation_guard_changes(repository, captured, [changed]) == ["apps/web/package.json"]


@pytest.mark.parametrize("kind,script", [("frontend", "test"), ("auto", "test"), ("static", "check")])
def test_detected_validation_scripts_are_guarded(repository, kind, script):
    write(repository, "package.json", json.dumps({"scripts": {script: "node --test"}}))
    check = ValidationCheck(id="check", label="Check", kind=kind)
    captured = capture_validation_guard(repository, [check])
    patch = PatchFile(path="package.json", content=json.dumps({"scripts": {script: "echo passed"}}))
    assert validation_guard_changes(repository, captured, [patch]) == ["package.json"]


def test_empty_checklist_guards_auto_selected_script_and_wrapper(repository):
    write(repository, "package.json", json.dumps({"scripts": {"test": "node scripts/check.mjs"}}))
    write(repository, "scripts/check.mjs", "runTests();\n")
    captured = capture_validation_guard(repository, [])
    assert "file:scripts/check.mjs" in captured
    assert validation_guard_changes(repository, captured, [PatchFile(path="package.json", content=json.dumps({"scripts": {"test": "echo passed"}}))]) == ["package.json"]


def test_empty_checklist_preserves_auto_pytest_precedence(repository):
    write(repository, "test_api.py", "assert True\n")
    write(repository, "package.json", json.dumps({"scripts": {"test": "node --test"}}))
    captured = capture_validation_guard(repository, [])
    assert validation_guard_changes(repository, captured, [PatchFile(path="package.json", content=json.dumps({"scripts": {"test": "echo passed"}}))]) == []


def test_static_higher_priority_script_cannot_replace_the_selected_check(repository):
    package = {"scripts": {"check": "node --test"}}
    write(repository, "package.json", json.dumps(package))
    captured = capture_validation_guard(repository, [ValidationCheck(id="static", label="Static", kind="static")])
    package["scripts"]["lint"] = "eslint src"
    assert validation_guard_changes(repository, captured, [PatchFile(path="package.json", content=json.dumps(package))]) == []
    package["scripts"]["typecheck"] = "echo passed"
    assert validation_guard_changes(repository, captured, [PatchFile(path="package.json", content=json.dumps(package))]) == ["package.json"]


@pytest.mark.parametrize("relative", ["pytest.ini", "tox.ini", "jest.config.ts", "vitest.config.ts", "vite.config.ts", "playwright.config.ts", "cypress.config.js", ".mocharc.json"])
def test_existing_validation_configuration_is_guarded(repository, relative):
    write(repository, relative, "original config\n")
    captured = capture_validation_guard(repository, [])
    assert validation_guard_changes(repository, captured, [PatchFile(path=relative, content="weaker config\n")]) == [relative]


@pytest.mark.parametrize("relative", ["tsconfig.json", "tsconfig.app.json", "eslint.config.js", ".eslintrc", ".eslintrc.json", ".eslintignore"])
def test_static_validation_configuration_is_guarded(repository, relative):
    write(repository, "package.json", json.dumps({"scripts": {"typecheck": "tsc", "lint": "eslint src"}}))
    write(repository, relative, "original config\n")
    captured = capture_validation_guard(repository, [script_check(script="typecheck"), script_check(script="lint")])
    assert validation_guard_changes(repository, captured, [PatchFile(path=relative, content="weaker config\n")]) == [relative]


@pytest.mark.parametrize("command", ["tsc --project config/custom.json", "tsc --project=config/custom.json"])
def test_selected_script_explicit_json_configuration_is_guarded(repository, command):
    write(repository, "package.json", json.dumps({"scripts": {"typecheck": command}}))
    write(repository, "config/custom.json", '{"compilerOptions": {"strict": true}}')
    captured = capture_validation_guard(repository, [script_check(script="typecheck")])
    assert validation_guard_changes(repository, captured, [PatchFile(path="config/custom.json", content='{"compilerOptions": {"strict": false}}')]) == ["config/custom.json"]


def test_embedded_eslint_configuration_is_guarded(repository):
    package = {"scripts": {"lint": "eslint src"}, "eslintConfig": {"rules": {"no-unused-vars": "error"}}}
    write(repository, "package.json", json.dumps(package))
    captured = capture_validation_guard(repository, [script_check(script="lint")])
    package["eslintConfig"]["rules"] = {}
    assert validation_guard_changes(repository, captured, [PatchFile(path="package.json", content=json.dumps(package))]) == ["package.json"]


@pytest.mark.parametrize("reference,relative", [("./config/shared.json", "configs/config/shared.json"), ("./config/shared", "configs/config/shared.json"), ("../shared.json", "shared.json")])
def test_local_extended_validation_configuration_is_guarded(repository, reference, relative):
    write(repository, "package.json", json.dumps({"scripts": {"typecheck": "tsc --project configs/tsconfig.json"}}))
    write(repository, "configs/tsconfig.json", json.dumps({"extends": reference}))
    write(repository, relative, '{"compilerOptions": {"strict": true}}')
    captured = capture_validation_guard(repository, [script_check(script="typecheck")])
    assert validation_guard_changes(repository, captured, [PatchFile(path=relative, content='{"compilerOptions": {"strict": false}}')]) == [relative]


def test_jsonc_array_and_recursive_extended_configs_are_guarded_without_reading_packages(repository):
    write(repository, "package.json", json.dumps({"scripts": {"typecheck": "tsc"}}))
    write(repository, "tsconfig.json", '// Compiler configuration\n{"extends": ["./config/shared.json", "@tsconfig/node",],}\n')
    write(repository, "config/shared.json", '{"extends": "../base.json"}')
    write(repository, "base.json", '{"extends": "./tsconfig.json", "compilerOptions": {"strict": true}}')
    captured = capture_validation_guard(repository, [script_check(script="typecheck")])
    assert {"file:config/shared.json", "file:base.json"} <= captured.keys()
    assert validation_guard_changes(repository, captured, [PatchFile(path="base.json", content='{"compilerOptions": {"strict": false}}')]) == ["base.json"]


def test_extended_config_property_escape_is_recognized(repository):
    write(repository, "tsconfig.json", '{"\\u0065xtends": "./config/shared.json"}')
    write(repository, "config/shared.json", '{"compilerOptions": {"strict": true}}')
    captured = capture_validation_guard(repository, [])
    assert "file:config/shared.json" in captured


def test_test_data_is_not_parsed_as_configuration(repository, monkeypatch):
    fixture = write(repository, "tests/data/input.json", '{"extends": "../../outside.json"}')
    original = Path.read_text

    def without_fixture_parse(path, *args, **kwargs):
        assert path != fixture, "test data must remain a streaming hash input"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", without_fixture_parse)
    assert "file:tests/data/input.json" in capture_validation_guard(repository, [])


@pytest.mark.parametrize("relative,original,dependency_change,validation_change", [
    ("pyproject.toml", '[project]\ndependencies = ["library"]\n[tool.pytest.ini_options]\naddopts = "-q"\n', '[project]\ndependencies = ["library", "new"]\n[tool.pytest.ini_options]\naddopts = "-q"\n', '[project]\ndependencies = ["library"]\n[tool.pytest.ini_options]\naddopts = "--ignore tests"\n'),
    ("setup.cfg", '[metadata]\nname = demo\n[options]\ninstall_requires = library\n[tool:pytest]\naddopts = -q\n', '[metadata]\nname = demo\n[options]\ninstall_requires = library, new\n[tool:pytest]\naddopts = -q\n', '[metadata]\nname = demo\n[options]\ninstall_requires = library\n[tool:pytest]\naddopts = --ignore tests\n'),
])
def test_mixed_config_only_guards_validation_sections(repository, relative, original, dependency_change, validation_change):
    write(repository, relative, original)
    captured = capture_validation_guard(repository, [])
    assert validation_guard_changes(repository, captured, [PatchFile(path=relative, content=dependency_change)]) == []
    assert validation_guard_changes(repository, captured, [PatchFile(path=relative, content=validation_change)]) == [relative]


def test_embedded_jest_config_is_guarded_without_locking_dependencies(repository):
    package = {"scripts": {"test": "jest"}, "jest": {"testMatch": ["**/*.test.js"]}}
    write(repository, "package.json", json.dumps(package))
    captured = capture_validation_guard(repository, [script_check()])
    package["jest"]["testMatch"] = ["nothing"]
    assert validation_guard_changes(repository, captured, [PatchFile(path="package.json", content=json.dumps(package))]) == ["package.json"]


def test_invalid_existing_or_proposed_config_does_not_silently_drop_guard(repository):
    write(repository, "pyproject.toml", "[tool.pytest.ini_options]\naddopts = []\n")
    captured = capture_validation_guard(repository, [])
    assert validation_guard_changes(repository, captured, [PatchFile(path="pyproject.toml", content="invalid toml [")]) == ["pyproject.toml"]
    write(repository, "pyproject.toml", "invalid toml [")
    with pytest.raises(RepositoryError, match="configuration") as error:
        capture_validation_guard(repository, [])
    assert error.value.code == "VALIDATION_INPUT_UNAVAILABLE"


@pytest.mark.parametrize("relative,content", [
    ("pytest.ini", "[pytest]\naddopts = --ignore=test_api.py\n"),
    ("pyproject.toml", '[tool.pytest.ini_options]\naddopts = "--ignore=test_api.py"\n'),
    ("setup.cfg", "[tool:pytest]\naddopts = --ignore=test_api.py\n"),
    ("jest.config.js", "module.exports = {testMatch: ['nothing']};\n"),
    ("package.json", '{"jest": {"testMatch": ["nothing"]}}'),
    ("package.json", '{"scripts": {"test": "echo passed"}}'),
])
def test_added_validation_config_requires_review_on_disk_and_in_candidate(repository, relative, content):
    write(repository, "test_api.py", "def test_original():\n    assert False\n")
    captured = capture_validation_guard(repository, [])
    candidate = [PatchFile(path=relative, operation="create", content=content), PatchFile(path="test_new.py", operation="create", content="def test_new():\n    assert True\n")]
    assert validation_guard_changes(repository, captured, candidate) == [relative]
    assert not (repository.path / relative).exists()
    write(repository, relative, content)
    write(repository, "test_new.py", "def test_new():\n    assert True\n")
    assert validation_guard_changes(repository, captured) == [relative]


@pytest.mark.parametrize("relative,content", [
    ("pyproject.toml", '[project]\nname = "demo"\ndependencies = ["library"]\n'),
    ("setup.cfg", "[metadata]\nname = demo\n[options]\ninstall_requires = library\n"),
    ("package.json", '{"dependencies": {"library": "1"}, "scripts": {"start": "node app.js"}}'),
])
def test_added_dependency_manifest_without_validation_config_is_allowed(repository, relative, content):
    write(repository, "test_api.py", "assert True\n")
    captured = capture_validation_guard(repository, [])
    assert validation_guard_changes(repository, captured, [PatchFile(path=relative, operation="create", content=content)]) == []
    write(repository, relative, content)
    assert validation_guard_changes(repository, captured) == []


def test_empty_project_can_bootstrap_tests_and_config(repository):
    captured = capture_validation_guard(repository, [])
    changes = [PatchFile(path="pytest.ini", operation="create", content="[pytest]\ntestpaths = tests\n"), PatchFile(path="tests/test_new.py", operation="create", content="assert True\n")]
    assert validation_guard_changes(repository, captured, changes) == []
    write(repository, "pytest.ini", "[pytest]\ntestpaths = tests\n")
    write(repository, "tests/test_new.py", "assert True\n")
    assert validation_guard_changes(repository, captured) == []


def test_gitignored_config_is_guarded_and_candidate_matches_disk(repository):
    git_output(repository.path, "init", "-q", "-b", "main")
    repository.has_git = True
    write(repository, ".gitignore", "pytest.ini\n")
    write(repository, "test_api.py", "assert True\n")
    captured = capture_validation_guard(repository, [])
    content = "[pytest]\naddopts = --ignore=test_api.py\n"
    assert validation_guard_changes(repository, captured, [PatchFile(path="pytest.ini", operation="create", content=content)]) == ["pytest.ini"]
    write(repository, "pytest.ini", content)
    assert validation_guard_changes(repository, captured) == ["pytest.ini"]
    existing = capture_validation_guard(repository, [])
    assert validation_guard_changes(repository, existing) == []


def test_existing_manifest_cannot_add_a_validation_script_slot_without_review(repository):
    write(repository, "test_api.py", "assert True\n")
    package = {"scripts": {"lint": "eslint src"}, "dependencies": {"library": "1"}}
    write(repository, "package.json", json.dumps(package))
    captured = capture_validation_guard(repository, [])
    package["scripts"]["lint"] = "eslint app"
    package["scripts"]["start"] = "node app.js"
    assert validation_guard_changes(repository, captured, [PatchFile(path="package.json", content=json.dumps(package))]) == []
    package["scripts"]["test"] = "echo passed"
    assert validation_guard_changes(repository, captured, [PatchFile(path="package.json", content=json.dumps(package))]) == ["package.json"]


def test_legacy_test_baseline_still_detects_new_validation_config(repository):
    write(repository, "test_api.py", "assert True\n")
    captured = capture_validation_guard(repository, [])
    captured = {key: value for key, value in captured.items() if not key.startswith("scope:")}
    patch = PatchFile(path="pytest.ini", operation="create", content="[pytest]\naddopts = --ignore=test_api.py\n")
    assert validation_guard_changes(repository, captured, [patch]) == ["pytest.ini"]


@pytest.mark.parametrize("relative", ["../outside.py", "/outside.py", "C:/outside.py", "C:outside.py"])
def test_workdirs_and_proposed_paths_cannot_escape_repository(repository, relative):
    with pytest.raises(RepositoryError):
        capture_validation_guard(repository, [script_check(workdir=relative)])
    with pytest.raises(RepositoryError):
        validation_guard_changes(repository, {}, [PatchFile(path=relative, content="outside")])


@pytest.mark.parametrize("has_git", [False, True])
def test_scan_limit_is_explicit_and_generated_or_protected_files_are_ignored(repository, has_git):
    write(repository, "test_api.py", "assert True\n")
    write(repository, "src.py", "value = 1\n")
    write(repository, "node_modules/test_package.js", "generated")
    write(repository, "coverage/test_output.py", "generated")
    write(repository, ".env", "PRIVATE=secret")
    if has_git:
        git_output(repository.path, "init", "-q", "-b", "main")
        repository.has_git = True
    with pytest.raises(RepositoryError) as error:
        capture_validation_guard(repository, [], max_entries=1)
    assert error.value.code == "VALIDATION_SCOPE_TOO_LARGE"
    captured = capture_validation_guard(repository, [], max_entries=20)
    assert [key for key in captured if not key.startswith("scope:")] == ["file:test_api.py"]


def test_git_ignored_outputs_are_not_scanned(repository):
    git_output(repository.path, "init", "-q", "-b", "main")
    repository.has_git = True
    write(repository, ".gitignore", ".tmp/\n")
    write(repository, "test_api.py", "assert True\n")
    for index in range(8):
        write(repository, f".tmp/test_output{index}.py", "generated")
    captured = capture_validation_guard(repository, [], max_entries=2)
    assert {key for key in captured if not key.startswith("scope:")} == {"file:test_api.py"}


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_symlinks_are_explicitly_rejected(repository, monkeypatch, kind):
    relative = "test_link.py" if kind == "file" else "tests"
    write(repository, relative if kind == "file" else "tests/input.txt", "acceptance")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == repository.path / relative or original(path))
    with pytest.raises(RepositoryError) as error:
        capture_validation_guard(repository, [])
    assert error.value.code == "SYMLINK_NOT_ALLOWED"


def test_unreadable_test_cannot_be_silently_omitted(repository, monkeypatch):
    path = write(repository, "test_api.py", "assert True\n")
    original = Path.open

    def blocked_open(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("test input unavailable")
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", blocked_open)
    with pytest.raises(RepositoryError) as error:
        capture_validation_guard(repository, [])
    assert error.value.code == "VALIDATION_INPUT_UNAVAILABLE"


def test_disappeared_input_during_capture_is_not_silently_omitted(repository, monkeypatch):
    path = write(repository, "test_api.py", "assert True\n")
    original = Path.open

    def disappeared_open(candidate, *args, **kwargs):
        if candidate == path:
            raise FileNotFoundError("input disappeared")
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", disappeared_open)
    with pytest.raises(RepositoryError) as error:
        capture_validation_guard(repository, [])
    assert error.value.code == "VALIDATION_INPUT_UNAVAILABLE"
