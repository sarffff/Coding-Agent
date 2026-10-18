import asyncio

import pytest

from app import main
from app.model_provider import ContextRequest, PatchProposal, _proposal_from_payload
from app.models import PatchFile
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
