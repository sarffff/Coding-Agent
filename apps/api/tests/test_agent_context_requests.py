import asyncio

import pytest

from app import main
from app.model_provider import ContextRequest, PatchProposal, _proposal_from_payload
from app.models import PatchFile
from app.repository_service import RepositoryError
from test_api_workflow import create_task, post
from test_model_draft import model_api


def test_model_can_search_read_and_edit_a_file_missing_from_initial_context(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    nested = directory / "src/internal/domain"
    nested.mkdir(parents=True)
    source = nested / "rules.py"
    source.write_text("def charge():\n    return 9\n", encoding="utf-8")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 1)
    calls = []

    async def model(goal, context, failure):
        calls.append((goal, dict(context)))
        if len(calls) == 1:
            assert "src/internal/domain/rules.py" not in dict(context)
            return PatchProposal(context_requests=[ContextRequest(action="search", query="def charge")])
        if len(calls) == 2:
            assert "src/internal/domain/rules.py" in goal
            return PatchProposal(context_requests=[ContextRequest(action="read", path="src/internal/domain/rules.py")])
        assert list(dict(context)) == ["src/internal/domain/rules.py"]
        return _proposal_from_payload({"files": [{"path": "src/internal/domain/rules.py", "edits": [{"old_text": "return 9", "new_text": "return 10"}]}]}, context)

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
    assert "return 10" in draft["diff"]
    assert source.read_text(encoding="utf-8").endswith("return 9\n")
    assert len(calls) == 3
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["budget"]["remote_calls"] == 3
    events = client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]
    assert [event["details"]["action"] for event in events if event["action"] == "context.request"] == ["search", "read"]


@pytest.mark.parametrize("path", [".env", "missing.py"])
def test_unreadable_context_requests_do_not_write_files_or_loop_forever(model_api, monkeypatch, path):
    client, directory = model_api
    task = create_task(client, directory)
    (directory / ".env").write_text("SECRET=hidden\n", encoding="utf-8")
    calls = []

    async def model(goal, context, failure):
        calls.append(goal)
        assert "SECRET=hidden" not in goal + str(context)
        return PatchProposal(context_requests=[ContextRequest(action="read", path=path)])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={})
    assert response.status_code == 400
    assert response.json()["code"] == "MODEL_CONTEXT_LIMIT"
    assert len(calls) <= 5
    assert (directory / "app.py").read_text(encoding="utf-8").endswith("return 1\n")


def test_pause_during_a_manual_context_request_discards_late_response(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)

    async def model(goal, context, failure):
        main.task_service.pause(task["id"])
        await asyncio.sleep(0)
        return PatchProposal(files=[PatchFile(path="app.py", content="def value():\n    return 2\n")])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={})
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_TASK_STATE"
    assert not any(event.action == "patch.draft" for event in main.audit_service.list_for_run(task["run_id"]))


def test_rejected_replies_are_fed_back_and_missing_files_are_read_for_the_model(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    (directory / "extra.py").write_text("X = 1\n", encoding="utf-8")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 1)
    calls = []

    async def model(goal, context, failure):
        calls.append((goal, dict(context)))
        if len(calls) == 1:
            return _proposal_from_payload({"files": [{"path": "app.py", "edits": [{"old_text": "missing", "new_text": "x"}]}]}, [("app.py", "def value():\n    return 1\n")])
        if len(calls) == 2:
            assert "MODEL_EDIT_CONFLICT" in goal
            return _proposal_from_payload({"files": [{"path": "extra.py", "edits": [{"old_text": "X = 1", "new_text": "X = 2"}]}]}, [])
        assert "MODEL_CONTEXT_REQUIRED" in goal and "extra.py" in dict(context)
        return _proposal_from_payload({"files": [{"path": "extra.py", "edits": [{"old_text": "X = 1", "new_text": "X = 2"}]}]}, context)

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
    assert "+X = 2" in draft["diff"] and len(calls) == 3
    budget = client.get(f"/api/v1/tasks/{task['id']}").json()["budget"]
    assert budget["remote_calls"] == 3 and budget["consecutive_failures"] == 0


def test_model_that_keeps_returning_bad_replies_stops_and_counts_one_failure(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    calls = []

    async def model(goal, context, failure):
        calls.append(goal)
        raise RepositoryError("MODEL_INVALID_OUTPUT", "not json")

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={})
    assert response.json()["code"] == "MODEL_INVALID_OUTPUT"
    assert len(calls) == main.agent_service.CONTEXT_ROUNDS + 1
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["budget"]["consecutive_failures"] == 1


@pytest.mark.parametrize("mode", ["standard", "conservative"])
def test_model_run_requests_return_output_except_in_conservative_mode(model_api, monkeypatch, mode):
    client, directory = model_api
    task = create_task(client, directory)
    main.task_service.set_mode(task["id"], mode)
    calls = []

    async def model(goal, context, failure):
        calls.append(goal)
        if len(calls) == 1:
            return PatchProposal(context_requests=[
                ContextRequest(action="run", command=["python", "-c", "print('probe-output'); raise SystemExit(3)"]),
                ContextRequest(action="run", command=["bash", "-c", "echo no"]),
            ])
        return PatchProposal(files=[PatchFile(path="app.py", content="def value():\n    return 2\n")])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
    result = calls[1]
    if mode == "standard":
        assert "probe-output" in result and "failed (exit 3)" in result
        assert "'bash' is not an allowed program" in result
    else:
        assert "(exit 3)" not in result and "disabled in conservative mode" in result
    assert not main.test_service.is_running(task["run_id"])  # model commands never look like validation
    events = client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]
    runs = [event for event in events if event["action"] == "context.request"]
    assert [event["details"]["action"] for event in runs] == ["run", "run"]
    if mode == "standard":
        assert "probe-output" in runs[0]["details"]["output"]  # shown in the chat transcript


def _run_then_edit(model_api, command):
    """Shared setup: one model-chosen program, then read the nested file and edit it."""
    client, directory = model_api
    task = create_task(client, directory)
    nested = directory / "src/internal/domain"
    nested.mkdir(parents=True)
    (nested / "rules.py").write_text("def charge():\n    return 9\n", encoding="utf-8")
    calls = []

    async def model(goal, context, failure):
        calls.append(goal)
        if len(calls) == 1:
            return PatchProposal(context_requests=[ContextRequest(action="run", command=command)])
        if len(calls) == 2:
            return PatchProposal(context_requests=[ContextRequest(action="read", path="src/internal/domain/rules.py")])
        return _proposal_from_payload(
            {"files": [{"path": "src/internal/domain/rules.py", "edits": [{"old_text": "return 9", "new_text": "return 10"}]}]},
            context,
        )

    return client, task, model


def test_model_command_is_charged_to_the_task_budget(model_api, monkeypatch):
    client, task, model = _run_then_edit(model_api, ["python", "-c", "print('hi')"])
    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
    # Three model turns plus the one program the model ran: commands share the same counter.
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["budget"]["remote_calls"] == 4


def test_blocked_program_is_not_charged(model_api, monkeypatch):
    client, task, model = _run_then_edit(model_api, ["cat", "app.py"])
    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
    # The program never ran, so only the three model turns are charged.
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["budget"]["remote_calls"] == 3
