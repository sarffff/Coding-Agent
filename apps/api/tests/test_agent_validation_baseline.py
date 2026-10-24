import asyncio
import json

import pytest

from app import main
from conftest import git_output
from app.models import TestFailure as ValidationFailure, TestRunResponse as ValidationResult
from app.model_provider import FakeModelProvider
from app.models import PatchFile, TestRunRequest as ValidationRequest
from test_agent_loop import model_api, _wait_for_loop
from test_api_workflow import apply_value, approve, create_task, post


def result(run_id, status, names=()):
    return ValidationResult(
        run_id=run_id, kind="pytest", command=["python", "-m", "pytest"],
        status=status, duration_ms=1, stdout="", stderr="fixture error" if status == "blocked" else "",
        failed_tests=[ValidationFailure(path="test_app.py", test_name=name, message="assert failed") for name in names],
    )


@pytest.mark.parametrize("status", ["passed", "failed"])
def test_baseline_precedes_model_and_does_not_count_as_repair_failure(model_api, monkeypatch, status):
    client, directory = model_api
    task = create_task(client, directory)
    original = (directory / "app.py").read_text(encoding="utf-8")
    calls = []

    def run(run_id, repository_id, kind, timeout_seconds, target=None, **options):
        calls.append((directory / "app.py").read_text(encoding="utf-8"))
        main.test_service.release(run_id)
        return result(run_id, status, ["test_value"] if status == "failed" else [])

    monkeypatch.setattr(main.test_service, "run", run)
    asyncio.run(main.agent_service._ensure_validation_baseline(task["id"]))
    asyncio.run(main.agent_service._ensure_validation_baseline(task["id"]))
    assert calls == [original]
    assert main.task_service.validation_baseline(task["id"])[0].status == status
    assert main.task_service.get_budget(task["id"]).consecutive_failures == 0
    assert main.task_service.list_iterations(task["id"])[0].test_runs == []


def test_blocked_baseline_prevents_drafting_and_can_be_retried(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    statuses = iter(["blocked", "passed"])

    def run(run_id, *args, **options):
        main.test_service.release(run_id)
        return result(run_id, next(statuses))

    monkeypatch.setattr(main.test_service, "run", run)
    rejected = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={})
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "VALIDATION_BASELINE_BLOCKED"
    assert main.task_service.get(task["id"]).agent_blocked_reason == "VALIDATION_BASELINE_BLOCKED"
    asyncio.run(main.agent_service._ensure_validation_baseline(task["id"]))
    assert main.task_service.validation_baseline(task["id"])[0].status == "passed"


def test_required_baseline_runs_every_check_without_completing_task(model_api, monkeypatch):
    client, directory = model_api
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Fix value", "validation_checks": [
        {"id": "unit", "label": "Unit", "kind": "pytest"},
        {"id": "static", "label": "Static", "kind": "static"},
    ]})
    calls = []

    def run(run_id, repository_id, kind, *args, **options):
        calls.append(kind)
        main.test_service.release(run_id)
        return result(run_id, "passed")

    monkeypatch.setattr(main.test_service, "run", run)
    asyncio.run(main.agent_service._ensure_validation_baseline(task["id"]))
    assert calls == ["pytest", "static"]
    assert [item.check_id for item in main.task_service.validation_baseline(task["id"])] == ["unit", "static"]
    assert main.task_service.get(task["id"]).status == "awaiting_approval"


def test_baseline_comparison_distinguishes_old_and_new_failures(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    results = iter([result(task["run_id"], "failed", ["test_old", "test_fixed"]),
                    result(task["run_id"], "failed", ["test_old", "test_new"])])

    def run(run_id, *args, **options):
        main.test_service.release(run_id)
        return next(results)

    monkeypatch.setattr(main.test_service, "run", run)
    asyncio.run(main.agent_service._ensure_validation_baseline(task["id"]))
    checked = post(client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest"})
    assert checked["baseline_comparison"] == {
        "existing": ["test_app.py::test_old"],
        "introduced": ["test_app.py::test_new"],
        "resolved": ["test_app.py::test_fixed"],
    }


def test_verification_edits_require_explicit_confirmation_before_writing(model_api):
    client, directory = model_api
    task = create_task(client, directory)
    path = directory / "test_app.py"
    original = path.read_text(encoding="utf-8")
    preview = post(client, f"/api/v1/runs/{task['run_id']}/patches/preview", {
        "files": [{"path": "test_app.py", "content": "def test_value():\n    assert True\n"}],
    })
    assert preview["requires_validation_confirmation"] is True
    assert preview["validation_changes"] == ["test_app.py"]
    denied = client.post(f"/api/v1/runs/{task['run_id']}/patches/apply", json={"patch_id": preview["patch_id"], "confirm": True})
    assert denied.status_code == 409
    assert denied.json()["code"] == "VALIDATION_REVIEW_REQUIRED"
    assert path.read_text(encoding="utf-8") == original
    post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {
        "patch_id": preview["patch_id"], "confirm": True, "confirm_validation_changes": True,
    })
    assert path.read_text(encoding="utf-8") == "def test_value():\n    assert True\n"
    assert main.agent_service.validation_changes(task["id"]) == []


def test_external_verification_change_cannot_be_hidden_by_running_tests(model_api):
    client, directory = model_api
    task = create_task(client, directory)
    (directory / "test_app.py").write_text("def test_value():\n    assert True\n", encoding="utf-8")
    denied = client.post(f"/api/v1/runs/{task['run_id']}/tests", json={})
    assert denied.status_code == 409
    assert denied.json()["code"] == "VALIDATION_REVIEW_REQUIRED"


def test_new_test_is_allowed_and_block_reason_is_visible(model_api):
    client, directory = model_api
    task = create_task(client, directory)
    preview = post(client, f"/api/v1/runs/{task['run_id']}/patches/preview", {
        "files": [{"path": "test_new.py", "operation": "create", "content": "def test_new():\n    assert True\n"}],
    })
    assert preview["requires_validation_confirmation"] is False
    main.task_service.block_agent(task["id"], "AGENT_NO_PROGRESS", "The same failure repeated.")
    state = client.get(f"/api/v1/tasks/{task['id']}/agent").json()
    assert state["action"] == "manual_review"
    assert "same failure" in state["message"]
    assert state["task"]["next_action"]


def test_automatic_verification_change_stops_before_writing(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    original = (directory / "test_app.py").read_text(encoding="utf-8")

    def run(run_id, *args, **options):
        main.test_service.release(run_id)
        return result(run_id, "failed", ["test_value"])

    monkeypatch.setattr(main.test_service, "run", run)
    FakeModelProvider.seed([PatchFile(path="test_app.py", content="def test_value():\n    assert True\n")])
    post(client, f"/api/v1/runs/{task['run_id']}/loop")
    state = _wait_for_loop(client, task["id"])
    assert state["task"]["agent_blocked_reason"] == "VALIDATION_REVIEW_REQUIRED"
    assert (directory / "test_app.py").read_text(encoding="utf-8") == original
    assert main.task_service.list_iterations(task["id"])[0].patch_id is None
    calls = len(FakeModelProvider._calls)
    review = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
    assert review["requires_validation_confirmation"] is True
    assert len(FakeModelProvider._calls) == calls


def test_auto_runner_does_not_switch_after_new_language_tests(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    calls = []

    def run(run_id, repository_id, kind, *args, **options):
        calls.append(kind)
        main.test_service.release(run_id)
        checked = result(run_id, "passed")
        checked.kind, checked.command = kind, ["npm", "test"]
        return checked

    monkeypatch.setattr(main.test_service, "run", run)
    asyncio.run(main.agent_service._ensure_validation_baseline(task["id"]))
    (directory / "test_extra.py").write_text("def test_extra():\n    assert True\n", encoding="utf-8")
    post(client, f"/api/v1/runs/{task['run_id']}/tests")
    assert calls == ["auto", "frontend"]


def test_empty_project_can_create_code_and_tests_before_first_validation(model_api, tmp_path):
    client, _ = model_api
    directory = tmp_path / "empty-project"
    directory.mkdir()
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Create value and its test"})
    FakeModelProvider.seed([
        PatchFile(path="value.py", operation="create", content="VALUE = 2\n"),
        PatchFile(path="test_value.py", operation="create", content="from value import VALUE\n\ndef test_value():\n    assert VALUE == 2\n"),
    ])
    post(client, f"/api/v1/runs/{task['run_id']}/loop")
    state = _wait_for_loop(client, task["id"])
    assert state["task"]["status"] == "ready_for_pr"
    assert main.task_service.validation_baseline(task["id"])[0].status == "not_found"
    assert main.task_service.list_iterations(task["id"])[0].test_result.status == "passed"


def test_repeated_identical_failure_stops_for_new_evidence(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)

    def run(run_id, *args, **options):
        main.test_service.release(run_id)
        return result(run_id, "failed", ["test_value"])

    monkeypatch.setattr(main.test_service, "run", run)
    post(client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest"})
    post(client, f"/api/v1/tasks/{task['id']}/repairs", {"feedback": "Investigate the same failure"})
    post(client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest"})
    state = client.get(f"/api/v1/tasks/{task['id']}/agent").json()
    assert state["action"] == "manual_review"
    assert "without progress" in state["message"]


def test_guard_baseline_and_review_block_survive_restart(api_stack, demo_repository):
    client = api_stack.restart()
    task = create_task(client, demo_repository)
    captured = dict(main.task_service.prepare_validation_guard(task["id"]))
    checked = result(task["run_id"], "failed", ["test_value"])
    checked.stdout = "x" * 5_000
    main.task_service.record_validation_baseline(task["id"], [checked], "baseline-hash")
    main.task_service.block_agent(task["id"], "VALIDATION_REVIEW_REQUIRED", "Review the test edit.", {"patch_id": "pending-patch"})

    restarted = api_stack.restart()
    state = restarted.get(f"/api/v1/tasks/{task['id']}/agent").json()
    assert state["action"] == "recover"
    assert state["task"]["agent_blocked_reason"] == "VALIDATION_REVIEW_REQUIRED"
    assert main.task_service.prepare_validation_guard(task["id"]) == captured
    assert main.task_service.validation_baseline_hash(task["id"]) == "baseline-hash"
    assert main.task_service.validation_runner(task["id"], ValidationRequest()) == "pytest"
    assert main.task_service.validation_baseline(task["id"])[0].failed_tests[0].test_name == "test_value"
    assert main.task_service.validation_baseline(task["id"])[0].stdout == "x" * 4_000
    assert main.task_service.validation_review_patch_id(task["id"]) == "pending-patch"
    post(restarted, f"/api/v1/tasks/{task['id']}/recover")
    (demo_repository / "test_app.py").write_text("def test_value():\n    assert True\n", encoding="utf-8")
    denied = restarted.post(f"/api/v1/runs/{task['run_id']}/tests", json={})
    assert denied.status_code == 409
    assert denied.json()["code"] == "VALIDATION_REVIEW_REQUIRED"


def test_verification_change_during_validation_cannot_record_a_pass(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)

    def run(run_id, *args, **options):
        (directory / "test_app.py").write_text("def test_value():\n    assert True\n", encoding="utf-8")
        main.test_service.release(run_id)
        return result(run_id, "passed")

    monkeypatch.setattr(main.test_service, "run", run)
    denied = client.post(f"/api/v1/runs/{task['run_id']}/tests", json={})
    assert denied.status_code == 409
    assert denied.json()["code"] == "VALIDATION_REVIEW_REQUIRED"
    iteration = main.task_service.list_iterations(task["id"])[0]
    assert iteration.test_result.status == "blocked"
    assert iteration.test_result.validation_hash is None
    assert main.task_service.get(task["id"]).agent_blocked_reason == "VALIDATION_REVIEW_REQUIRED"


@pytest.mark.parametrize("kind", ["static", "frontend"])
def test_different_runner_cannot_claim_baseline_failures_resolved(model_api, kind):
    client, directory = model_api
    task = create_task(client, directory)
    main.task_service.record_validation_baseline(task["id"], [result(task["run_id"], "failed", ["test_value"])], "baseline-hash")
    checked = result(task["run_id"], "passed")
    checked.kind = kind
    assert main.agent_service._baseline_comparison(task["id"], checked) == {}


@pytest.mark.parametrize("external_change", [False, True])
def test_approved_verification_rollback_restores_only_reviewed_guard_paths(model_api, external_change):
    client, directory = model_api
    extra = directory / "test_other.py"
    extra.write_text("def test_other():\n    assert True\n", encoding="utf-8")
    git_output(directory, "add", "test_other.py")
    git_output(directory, "commit", "-qm", "Add other acceptance test")
    task = create_task(client, directory)
    original = (directory / "test_app.py").read_text(encoding="utf-8")
    preview = post(client, f"/api/v1/runs/{task['run_id']}/patches/preview", {
        "files": [{"path": "test_app.py", "content": "def test_value():\n    assert True\n"}],
    })
    applied = post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {
        "patch_id": preview["patch_id"], "confirm": True, "confirm_validation_changes": True,
    })
    if external_change:
        extra.write_text("def test_other():\n    assert 1\n", encoding="utf-8")
    main.task_service.block_agent(task["id"], "AGENT_NO_PROGRESS", "Review before continuing.")
    post(client, f"/api/v1/runs/{task['run_id']}/rollback", {"checkpoint_id": applied["checkpoint"]["id"]})
    assert (directory / "test_app.py").read_text(encoding="utf-8") == original
    assert main.agent_service.validation_changes(task["id"]) == (["test_other.py"] if external_change else [])
    assert main.task_service.validation_baseline(task["id"]) == []
    assert main.task_service.get(task["id"]).agent_blocked_reason is None
    if not external_change:
        post(client, f"/api/v1/tasks/{task['id']}/iterations")
        FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])
        assert post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")["requires_validation_confirmation"] is False


@pytest.mark.parametrize("content, code", [('{"scripts": {}}', "INVALID_TEST_SCRIPT"), ("invalid json", "VALIDATION_INPUT_UNAVAILABLE")])
def test_invalid_approved_validation_edit_rolls_back_before_reporting_failure(model_api, content, code):
    client, directory = model_api
    original = json.dumps({"scripts": {"test": 'node -e "console.log(1)"'}})
    (directory / "package.json").write_text(original, encoding="utf-8")
    git_output(directory, "add", "package.json")
    git_output(directory, "commit", "-qm", "Add acceptance script")
    task = create_task(client, directory)
    captured = dict(main.task_service.prepare_validation_guard(task["id"]))
    preview = post(client, f"/api/v1/runs/{task['run_id']}/patches/preview", {
        "files": [{"path": "package.json", "content": content}],
    })
    denied = client.post(f"/api/v1/runs/{task['run_id']}/patches/apply", json={
        "patch_id": preview["patch_id"], "confirm": True, "confirm_validation_changes": True,
    })
    assert denied.status_code == 400
    assert denied.json()["code"] == code
    assert (directory / "package.json").read_text(encoding="utf-8") == original
    assert main.task_service.prepare_validation_guard(task["id"]) == captured
    iteration = main.task_service.list_iterations(task["id"])[0]
    assert iteration.patch_id is None
    assert iteration.checkpoint_id is None
    assert main.task_service.get(task["id"]).status == "coding"


def test_approved_validation_change_keeps_frozen_runner_after_baseline_reset(model_api, tmp_path, monkeypatch):
    client, _ = model_api
    directory = tmp_path / "frontend"
    directory.mkdir()
    (directory / "package.json").write_text(json.dumps({"scripts": {"test": 'node -e "console.log(1)"'}}), encoding="utf-8")
    (directory / "a.test.js").write_text("// original test\n", encoding="utf-8")
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Fix frontend", "validation_checks": []})
    approve(client, task, "plan")
    post(client, f"/api/v1/tasks/{task['id']}/iterations")
    calls = []

    def run(run_id, repository_id, kind, *args, **options):
        calls.append(kind)
        main.test_service.release(run_id)
        checked = result(run_id, "passed")
        checked.kind, checked.command = kind, ["npm", "test"]
        return checked

    monkeypatch.setattr(main.test_service, "run", run)
    asyncio.run(main.agent_service._ensure_validation_baseline(task["id"]))
    preview = post(client, f"/api/v1/runs/{task['run_id']}/patches/preview", {
        "files": [{"path": "a.test.js", "content": "// approved test change\n"}],
    })
    post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {
        "patch_id": preview["patch_id"], "confirm": True, "confirm_validation_changes": True,
    })
    assert main.task_service.validation_baseline(task["id"]) == []
    (directory / "test_extra.py").write_text("def test_extra():\n    assert True\n", encoding="utf-8")
    checked = post(client, f"/api/v1/runs/{task['run_id']}/tests")
    assert calls == ["auto", "frontend"]
    assert checked["baseline_comparison"] == {}


def test_rejected_validation_edit_does_not_prune_previous_checkpoint(model_api, monkeypatch):
    client, directory = model_api
    (directory / "package.json").write_text(json.dumps({"scripts": {"test": 'node -e "console.log(1)"'}}), encoding="utf-8")
    git_output(directory, "add", "package.json")
    git_output(directory, "commit", "-qm", "Add acceptance script")
    monkeypatch.setattr(main.settings, "checkpoint_max_per_task", 1)
    task = create_task(client, directory)
    previous = apply_value(client, task, 0)["checkpoint"]["id"]
    main.task_service.record_test_result(task["run_id"], result(task["run_id"], "failed", ["test_value"]))
    post(client, f"/api/v1/tasks/{task['id']}/repairs")
    preview = post(client, f"/api/v1/runs/{task['run_id']}/patches/preview", {
        "files": [{"path": "package.json", "content": '{"scripts": {}}'}],
    })
    denied = client.post(f"/api/v1/runs/{task['run_id']}/patches/apply", json={
        "patch_id": preview["patch_id"], "confirm": True, "confirm_validation_changes": True,
    })
    assert denied.json()["code"] == "INVALID_TEST_SCRIPT"
    checkpoint = main.patch_service.list_checkpoints([previous])[0]
    assert checkpoint.pruned is False
    post(client, f"/api/v1/runs/{task['run_id']}/rollback", {"checkpoint_id": previous})
    assert (directory / "app.py").read_text(encoding="utf-8") == "def value():\n    return 1\n"
