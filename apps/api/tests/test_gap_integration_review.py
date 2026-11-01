import asyncio
from pathlib import Path

import pytest

from app import main
from app.model_provider import ContextRequest, FakeModelProvider, PatchProposal
from app.models import PatchFile, TestRunResponse as RunResult
from app.repository_service import RepositoryError
from conftest import git_output
from test_api_workflow import post
from test_agent_workflow import approve_plan, start_agent_iteration, agent_state
from test_model_draft import model_api


def create_isolated(client, directory, goal, target="test_app.py", *, explicit_criteria=True):
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {
        "repository_id": repository["id"], "goal": goal, "isolated_workspace": True,
        "validation_checks": [{"id": "behavior", "label": "Business behavior", "kind": "pytest", "target": target, "timeout_seconds": 15}],
        "completion_criteria": [{"id": "business", "text": goal}] if explicit_criteria else [],
    })
    approve_plan(client, task)
    start_agent_iteration(client, task)
    return task


def confirm_all(client, task):
    state = agent_state(client, task["id"])
    post(client, f"/api/v1/tasks/{task['id']}/completion-confirmation", {
        "iteration_id": state["iteration"]["id"], "scope_hash": state["completion_scope_hash"],
        "validation_hash": state["completion_validation_hash"],
        "evidence": [{"criterion_id": criterion["id"], "evidence": "The real business assertions pass on the reviewed current files."}
                     for criterion in (state["task"]["completion_criteria"] or [{"id": "task-goal"}])],
    })


def test_isolated_command_detects_shared_git_configuration_writes(model_api, monkeypatch):
    client, directory = model_api
    task = create_isolated(client, directory, "Inspect the current value behavior")
    workspace = Path(task["workspace_path"])
    calls = []

    async def model(goal, context, failure):
        calls.append(goal)
        if len(calls) == 1:
            return PatchProposal(context_requests=[ContextRequest(action="run", command=["python", "probe.py"])])
        return PatchProposal(no_changes=True, rationale="No source edit is necessary; validate current behavior.")

    def command(run_id, repository_id, argv, *, reserved):
        git_output(workspace, "config", "remote.review.url", "https://example.invalid/changed-target.git")
        return RunResult(run_id=run_id, kind="auto", command=argv, status="passed", exit_code=0, duration_ms=1, stdout="", stderr="")

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    monkeypatch.setattr(main.test_service, "run_command", command)
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={"context_paths": ["app.py"]})
    assert git_output(directory, "config", "--get", "remote.review.url").strip() == "https://example.invalid/changed-target.git"
    assert response.status_code == 409 and response.json()["code"] == "COMMAND_WORKSPACE_CHANGED", response.text
    assert len(calls) == 1


def test_two_file_business_repair_uses_real_tests_and_preserves_the_original_source(model_api):
    client, directory = model_api
    sources = {
        "pricing.py": "def discounted_unit(price, discount):\n    return price + discount\n",
        "invoice.py": "from pricing import discounted_unit\n\ndef total(price, discount, quantity):\n    return discounted_unit(price, discount) + quantity\n",
        "test_invoice.py": "from invoice import total\n\ndef test_quantity_and_discount():\n    assert total(10, 2, 3) == 24\n\ndef test_no_discount():\n    assert total(7, 0, 2) == 14\n",
    }
    for path, content in sources.items():
        (directory / path).write_text(content, encoding="utf-8", newline="")
    git_output(directory, "add", "pricing.py", "invoice.py", "test_invoice.py")
    git_output(directory, "commit", "-qm", "Business fixture")
    task = create_isolated(client, directory, "Fix invoice quantity and discount calculations", "test_invoice.py")
    before = {path: (directory / path).read_bytes() for path in sources}
    FakeModelProvider.seed([
        PatchFile(path="pricing.py", content=sources["pricing.py"].replace("price + discount", "price - discount")),
        PatchFile(path="invoice.py", content=sources["invoice.py"].replace(" + quantity", " * quantity")),
    ])
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": ["pricing.py", "invoice.py"]})
    assert main.task_service.validation_baseline(task["id"])[0].status == "failed"
    assert set(draft["files"]) == {"pricing.py", "invoice.py"}
    applied = post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {"patch_id": draft["patch_id"], "confirm": True})
    result = post(client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest", "target": "test_invoice.py", "check_id": "behavior", "timeout_seconds": 15})
    assert result["status"] == "passed", result
    confirm_all(client, task)
    completed = post(client, f"/api/v1/tasks/{task['id']}/complete-local")
    assert completed["status"] == "completed"
    assert len(applied["applied_files"]) == 2
    assert {path: (directory / path).read_bytes() for path in sources} == before


def test_dirty_source_dependency_cannot_make_an_unvalidated_commit_publishable(model_api, monkeypatch):
    client, directory = model_api
    sources = {
        "pricing.py": "def unit_price():\n    return 1\n",
        "invoice.py": "from pricing import unit_price\n\ndef total(quantity):\n    return unit_price() + quantity\n",
        "test_invoice.py": "from invoice import total\n\ndef test_quantity():\n    assert total(2) == 18\n",
    }
    for path, content in sources.items():
        (directory / path).write_text(content, encoding="utf-8", newline="")
    git_output(directory, "add", "pricing.py", "invoice.py", "test_invoice.py")
    git_output(directory, "commit", "-qm", "Dirty dependency fixture")
    (directory / "pricing.py").write_text(sources["pricing.py"].replace("return 1", "return 9"), encoding="utf-8", newline="")
    task = create_isolated(client, directory, "Fix invoice quantity calculations", "test_invoice.py")
    FakeModelProvider.seed([PatchFile(path="invoice.py", content=sources["invoice.py"].replace(" + quantity", " * quantity"))])
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": ["invoice.py", "pricing.py"]})
    post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {"patch_id": draft["patch_id"], "confirm": True})
    result = post(client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest", "target": "test_invoice.py", "check_id": "behavior", "timeout_seconds": 15})
    assert result["status"] == "passed", result
    confirm_all(client, task)
    preview = post(client, f"/api/v1/tasks/{task['id']}/commits/preview", {"message": "Fix invoice quantity"})
    assert preview["changed_files"] == ["invoice.py"] and "pricing.py" in preview["excluded_files"]
    assert "return 1" in git_output(Path(task["workspace_path"]), "show", "HEAD:pricing.py")
    assert not preview["ready"], "Validation used dirty pricing.py=9, but the proposed commit preserves HEAD pricing.py=1 and would fail the same business test."
    assert preview["scope_blocked_files"] == ["pricing.py"]
    calls = []

    def commit(*args, **kwargs):
        calls.append("git.commit")
        raise AssertionError("Out-of-scope inputs must be rejected before any Git commit.")

    async def push(*args, **kwargs):
        calls.append("remote.push")
        raise AssertionError("Out-of-scope inputs must be rejected before any remote call.")

    monkeypatch.setattr(main.git_service, "commit", commit)
    monkeypatch.setattr(main.remote_service.provider, "push_task_branch", push)
    for kind in ("write", "push"):
        approval = post(client, f"/api/v1/tasks/{task['id']}/approvals", {
            "type": kind, "summary": "Review the proposed task changes", "scope_hash": preview["scope_hash"] if kind == "write" else None,
        })
        post(client, f"/api/v1/approvals/{approval['id']}/decision", {"decision": "approve"})
    workspace = Path(task["workspace_path"])
    original_head = git_output(workspace, "rev-parse", "HEAD")
    response = client.post(f"/api/v1/tasks/{task['id']}/commits", json={"message": "Fix invoice quantity", "scope_hash": preview["scope_hash"]})
    assert response.status_code == 409 and response.json()["code"] == "PUBLISH_INPUTS_OUT_OF_SCOPE", response.text
    with pytest.raises(RepositoryError) as auto_error:
        main.agent_service._auto_commit(main.task_service.get(task["id"]))
    assert auto_error.value.code == "PUBLISH_INPUTS_OUT_OF_SCOPE"
    with pytest.raises(RepositoryError) as remote_error:
        asyncio.run(main.remote_service.push_branch(task["id"]))
    assert remote_error.value.code == "PUBLISH_INPUTS_OUT_OF_SCOPE"
    assert calls == [] and main.remote_service.list_commits(task["id"]) == []
    assert main.remote_service.list_remote_branches(task["id"]) == []
    assert git_output(workspace, "rev-parse", "HEAD") == original_head
    assert git_output(directory, "rev-parse", "HEAD") == original_head
    assert (workspace / "pricing.py").read_bytes() == (directory / "pricing.py").read_bytes()


def test_default_goal_evidence_keeps_a_validated_commit_preview_ready(model_api):
    client, directory = model_api
    task = create_isolated(client, directory, "Return the value required by test_value", explicit_criteria=False)
    FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": ["app.py"]})
    post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {"patch_id": draft["patch_id"], "confirm": True})
    result = post(client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest", "target": "test_app.py", "check_id": "behavior", "timeout_seconds": 15})
    assert result["status"] == "passed", result
    confirm_all(client, task)
    assert agent_state(client, task["id"])["completion_status"] == "confirmed"
    preview = post(client, f"/api/v1/tasks/{task['id']}/commits/preview", {"message": "Fix value"})
    assert preview["changed_files"] == ["app.py"]
    assert preview["ready"], "Default task-goal evidence is current and must use the same validation hash as explicit criteria."
