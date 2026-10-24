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
    assert response.status_code == 409
    assert response.json()["code"] == "AGENT_NO_PROGRESS"
    assert len(calls) == 2
    assert (directory / "app.py").read_text(encoding="utf-8").endswith("return 1\n")
    assert (directory / ".env").read_text(encoding="utf-8") == "SECRET=hidden\n"
    assert client.get(f"/api/v1/runs/{task['run_id']}/checkpoints").json()["total"] == 0
    budget = client.get(f"/api/v1/tasks/{task['id']}/budget").json()
    assert budget["remote_calls"] == 2 and budget["consecutive_failures"] == 1


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
    assert response.status_code == 409
    assert response.json()["code"] == "AGENT_NO_PROGRESS"
    assert len(calls) == 2
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["budget"]["consecutive_failures"] == 1
    assert (directory / "app.py").read_text(encoding="utf-8").endswith("return 1\n")
    assert not any(event.action == "patch.draft" for event in main.audit_service.list_for_run(task["run_id"]))


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


def test_current_read_requests_keep_existing_target_alongside_new_evidence(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    (directory / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 2)
    calls = []

    async def model(goal, context, failure):
        calls.append(dict(context))
        if len(calls) == 1:
            assert "app.py" in dict(context)
            return PatchProposal(context_requests=[ContextRequest(action="read", path="extra.py"), ContextRequest(action="read", path="app.py")])
        assert set(dict(context)) == {"app.py", "extra.py"}
        return PatchProposal(files=[PatchFile(path="app.py", content="def value():\n    return 2\n")])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": ["app.py", "test_app.py"]})
    assert "+    return 2" in draft["diff"] and len(calls) == 2


def test_repeated_read_of_unchanged_context_is_not_progress(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    calls = []

    async def model(goal, context, failure):
        calls.append(goal)
        return PatchProposal(context_requests=[ContextRequest(action="read", path="app.py")])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={})
    assert response.status_code == 409 and response.json()["code"] == "AGENT_NO_PROGRESS"
    assert len(calls) == 2


def test_reread_after_external_change_stops_before_another_model_turn(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    external = "def value():\n    return 99  # user edit\n"
    calls = []

    async def model(goal, context, failure):
        calls.append(goal)
        (directory / "app.py").write_text(external, encoding="utf-8")
        return PatchProposal(context_requests=[ContextRequest(action="read", path="app.py")])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={})
    assert response.status_code == 400 and response.json()["code"] == "PATCH_CONFLICT"
    assert len(calls) == 1
    assert (directory / "app.py").read_text(encoding="utf-8") == external


def test_evicted_context_keeps_its_original_hash_when_reread(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    (directory / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 1)
    calls = []
    external = "def value():\n    return 99  # user edit\n"

    async def model(goal, context, failure):
        calls.append(dict(context))
        if len(calls) == 1:
            assert "app.py" in dict(context)
            return PatchProposal(context_requests=[ContextRequest(action="read", path="extra.py")])
        assert list(dict(context)) == ["extra.py"]
        (directory / "app.py").write_text(external, encoding="utf-8")
        return PatchProposal(context_requests=[ContextRequest(action="read", path="app.py")])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={"context_paths": ["app.py"]})
    assert response.status_code == 400 and response.json()["code"] == "PATCH_CONFLICT"
    assert len(calls) == 2 and (directory / "app.py").read_text(encoding="utf-8") == external


def test_request_history_keeps_unresolved_locations_ahead_of_completed_probes(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    nested = directory / "src" / "domain"
    nested.mkdir(parents=True)
    pending = "MARKER = 'pending-evidence'\n"
    (nested / "pending.py").write_text(pending, encoding="utf-8", newline="")
    (directory / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 1)
    monkeypatch.setattr(main.settings, "llm_max_context_bytes", 1_000)
    calls = []

    async def run(task, command):
        return "probe-" + command[-1] + ("Z" * 2_000 if command[-1] == "1" else "")

    async def model(goal, context, failure):
        calls.append(goal)
        if len(calls) == 1:
            return PatchProposal(context_requests=[ContextRequest(action="search", query="pending-evidence"),
                                                   ContextRequest(action="read", path="missing.py"),
                                                   ContextRequest(action="run", command=["python", "1"])])
        section = goal.split("\n\n# Context results\n", 1)[1]
        assert len(section.encode("utf-8")) <= 1_000
        assert "Read missing.py failed" in section
        if len(calls) < 4:
            assert "src/domain/pending.py:1" in section and "pending-evidence" in section
        if len(calls) == 2:
            return PatchProposal(context_requests=[ContextRequest(action="read", path="extra.py"), ContextRequest(action="run", command=["python", "2"])])
        if len(calls) == 3:
            return PatchProposal(context_requests=[ContextRequest(action="read", path="src/domain/pending.py"), ContextRequest(action="run", command=["python", "3"])])
        assert "Search 'pending-evidence'" not in section and "Read extra.py." not in section
        assert "probe-1" not in section and "probe-2" in section and "probe-3" in section
        assert dict(context)["src/domain/pending.py"] == pending
        return PatchProposal(files=[PatchFile(path="src/domain/pending.py", content=pending.replace("MARKER", "RESOLVED"))])

    monkeypatch.setattr(main.agent_service, "_run_for_model", run)
    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
    assert "+RESOLVED" in draft["diff"] and len(calls) == 4
