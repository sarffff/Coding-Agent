import pytest

from app import main
from app.model_provider import ContextRequest, PatchProposal
from app.models import PatchFile, TestRunResponse as RunResult
from app.repository_service import RepositoryError
from test_api_workflow import create_task, post
from test_model_draft import model_api


def test_draft_summarizes_three_recent_attempts_with_bounded_failure_memory(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    current = main.task_service.list_iterations(task["id"])[-1]
    attempts = []
    for number, status in [(1, "failed"), (2, "failed"), (3, "passed"), (4, "failed"), (5, "failed")]:
        result = RunResult(
            run_id=task["run_id"], kind="pytest", command=["pytest"], status=status,
            duration_ms=1, stdout="", stderr="",
        )
        attempts.append(current.model_copy(update={
            "number": number, "changed_files": [f"attempt_{number}.py"], "test_result": result,
            "failure_summary": f"failure_{number}: " + "x" * 4_000 if status == "failed" else None,
            "rolled_back": number == 5,
        }))
    monkeypatch.setattr(main.task_service, "list_iterations", lambda _task_id: [*attempts, current])
    calls = []

    async def model(goal, context, failure):
        calls.append((goal, failure))
        return PatchProposal(files=[PatchFile(path="app.py", content="def value():\n    return 2\n")])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
    history = calls[0][0].split("# Recent attempts\n", 1)[1]
    assert len(history) <= 3_000
    assert all(f"attempt_{number}.py" in history for number in [2, 3, 4])
    assert "attempt_1.py" not in history and "attempt_5.py" not in history
    assert "passed" in history and "failure_2" in history and "failure_4" in history
    assert calls[0][1].startswith("failure_4")


@pytest.mark.parametrize("error", ["DUPLICATE_PATCH_PATH", "FILE_ALREADY_EXISTS", "PATCH_CONTENT_REQUIRED", "MODEL_NO_CHANGE"])
def test_correctable_preview_errors_are_fed_back_without_writing(model_api, monkeypatch, error):
    client, directory = model_api
    task = create_task(client, directory)
    original = (directory / "app.py").read_bytes()
    calls = []

    async def model(goal, context, failure):
        calls.append((goal, dict(context)))
        if len(calls) == 1:
            if error == "DUPLICATE_PATCH_PATH":
                files = [PatchFile(path="app.py", content="x = 2\n"), PatchFile(path="./app.py", content="x = 3\n")]
            elif error == "FILE_ALREADY_EXISTS":
                files = [PatchFile(path="notes.txt", operation="create", content="changed notes\n")]
            elif error == "PATCH_CONTENT_REQUIRED":
                files = [PatchFile(path="app.py", operation="update")]
            else:
                files = [PatchFile(path="app.py", content=dict(context)["app.py"])]
            return PatchProposal(files=files)
        assert error in goal
        assert (directory / "app.py").read_bytes() == original
        if error == "FILE_ALREADY_EXISTS":
            assert dict(context)["notes.txt"] == (directory / "notes.txt").read_bytes().decode("utf-8")
            return PatchProposal(files=[PatchFile(path="notes.txt", content="changed notes\n")])
        return PatchProposal(files=[PatchFile(path="app.py", content="def value():\n    return 2\n")])

    monkeypatch.setattr(main.settings, "llm_max_context_files", 1)
    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": ["app.py"]})
    assert len(calls) == 2 and draft["diff"]
    assert (directory / "app.py").read_bytes() == original
    assert client.get(f"/api/v1/runs/{task['run_id']}/checkpoints").json()["total"] == 0
    budget = client.get(f"/api/v1/tasks/{task['id']}/budget").json()
    assert budget["remote_calls"] == 2 and budget["consecutive_failures"] == 0


@pytest.mark.parametrize("kind", ["protected", "large", "binary", "missing", "conflict", "conflict_noop"])
def test_unsafe_preview_errors_stop_without_retry_or_overwrite(model_api, monkeypatch, kind):
    client, directory = model_api
    task = create_task(client, directory)
    calls = []
    external = "def value():\n    return 99  # external edit\n"

    async def model(goal, context, failure):
        calls.append(goal)
        if kind == "protected":
            proposed = PatchFile(path=".env", operation="create", content="SECRET=x\n")
        elif kind == "large":
            proposed = PatchFile(path="large.py", operation="create", content="x" * (main.settings.max_file_size_bytes + 1))
        elif kind == "binary":
            proposed = PatchFile(path="binary.py", operation="create", content="x\x00y")
        elif kind == "missing":
            (directory / "app.py").unlink()
            proposed = PatchFile(path="app.py", content="def value():\n    return 2\n")
        else:
            (directory / "app.py").write_text(external, encoding="utf-8")
            proposed = PatchFile(path="app.py", content=dict(context)["app.py"] if kind == "conflict_noop" else "def value():\n    return 2\n")
        return PatchProposal(files=[proposed])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={})
    expected = {"protected": "PROTECTED_PATH", "large": "FILE_TOO_LARGE", "binary": "BINARY_FILE_NOT_ALLOWED", "missing": "FILE_NOT_FOUND", "conflict": "PATCH_CONFLICT", "conflict_noop": "PATCH_CONFLICT"}
    assert response.json()["code"] == expected[kind]
    assert len(calls) == 1
    assert client.get(f"/api/v1/runs/{task['run_id']}/checkpoints").json()["total"] == 0
    if kind in {"conflict", "conflict_noop"}:
        assert (directory / "app.py").read_text(encoding="utf-8") == external


def test_noop_updates_are_removed_but_empty_file_creation_and_deletion_are_changes(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    (directory / "empty_old.py").write_text("", encoding="utf-8")

    async def model(goal, context, failure):
        return PatchProposal(files=[
            PatchFile(path="app.py", content=dict(context)["app.py"]),
            PatchFile(path="empty_old.py", operation="delete"),
            PatchFile(path="empty_new.py", operation="create", content=""),
        ])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": ["app.py", "empty_old.py"]})
    assert draft["files"] == ["empty_old.py", "empty_new.py"] and draft["diff"] == ""
    applied = post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {"patch_id": draft["patch_id"], "confirm": True})
    assert applied["applied_files"] == ["empty_old.py", "empty_new.py"]
    assert not (directory / "empty_old.py").exists() and (directory / "empty_new.py").read_bytes() == b""


def test_noop_filter_preserves_the_duplicate_path_gate(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    calls = []

    async def model(goal, context, failure):
        calls.append(goal)
        if len(calls) == 1:
            return PatchProposal(files=[
                PatchFile(path="app.py", content=dict(context)["app.py"]),
                PatchFile(path="./app.py", content="def value():\n    return 2\n"),
            ])
        assert "DUPLICATE_PATCH_PATH" in goal
        return PatchProposal(files=[PatchFile(path="app.py", content="def value():\n    return 2\n")])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
    assert len(calls) == 2 and draft["files"] == ["app.py"]


@pytest.mark.parametrize("kind", ["context", "invalid", "noop"])
def test_identical_replies_in_unchanged_context_stop_early_and_count_once(model_api, monkeypatch, kind):
    client, directory = model_api
    task = create_task(client, directory)
    calls = []

    async def model(goal, context, failure):
        calls.append(goal)
        if kind == "context":
            return PatchProposal(context_requests=[ContextRequest(action="read", path="missing.py")])
        if kind == "invalid":
            raise RepositoryError("MODEL_INVALID_OUTPUT", "same invalid JSON")
        return PatchProposal(files=[PatchFile(path="app.py", content=dict(context)["app.py"])])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={})
    assert response.json()["code"] == "AGENT_NO_PROGRESS"
    assert len(calls) == 2
    budget = client.get(f"/api/v1/tasks/{task['id']}/budget").json()
    assert budget["remote_calls"] == 2 and budget["consecutive_failures"] == 1
    assert client.get(f"/api/v1/runs/{task['run_id']}/checkpoints").json()["total"] == 0


def test_a_repeated_request_is_allowed_after_the_context_changes(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    (directory / "extra.py").write_text("VALUE = 1\n", encoding="utf-8")
    calls = []

    async def model(goal, context, failure):
        calls.append(dict(context))
        if len(calls) <= 2:
            return PatchProposal(context_requests=[ContextRequest(action="read", path="extra.py")])
        return PatchProposal(files=[PatchFile(path="extra.py", content="VALUE = 2\n")])

    monkeypatch.setattr(main.settings, "llm_max_context_files", 1)
    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": ["app.py"]})
    assert "extra.py" not in calls[0] and "extra.py" in calls[1]
    assert len(calls) == 3 and "+VALUE = 2" in draft["diff"]


def test_different_invalid_suggestions_share_the_existing_retry_limit(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    calls = []

    async def model(goal, context, failure):
        calls.append(goal)
        return PatchProposal(files=[PatchFile(path=f"new_{len(calls)}.py", operation="create")])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={})
    assert response.json()["code"] == "PATCH_CONTENT_REQUIRED"
    assert len(calls) == main.agent_service.CONTEXT_ROUNDS + 1
    assert client.get(f"/api/v1/tasks/{task['id']}/budget").json()["consecutive_failures"] == 1
