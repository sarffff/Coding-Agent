import asyncio
import json

import pytest

from app import main
from app.approval_service import plan_scope_hash
from app.repository_service import RepositoryError
from app.task_service import TaskService
from test_api_workflow import post
from test_cowork_chat import clean_fakes, cowork_api


def test_stream_delivers_partial_text_before_the_turn_finishes(cowork_api, monkeypatch):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "Stream", "track": "cowork"})

    async def scenario():
        released = asyncio.Event()

        async def model_stream(history, system=None):
            yield "first "
            await released.wait()
            yield "second"

        monkeypatch.setattr(main.agent_service.models, "chat_stream", model_stream)
        stream = main.agent_service.chat_stream(task["id"], "hello", "turn-1")
        assert (await anext(stream))["event"] == "start"
        first = await anext(stream)
        assert first == {"event": "delta", "text": "first ", "request_id": "turn-1"}
        assert main.agent_service.is_chat_running(task["id"])
        assert not any(event.action == "chat.assistant" for event in main.audit_service.list_for_run(task["run_id"]))
        released.set()
        remaining = [event async for event in stream]
        assert remaining[-1] == {"event": "done", "reply": "first second", "request_id": "turn-1"}
        assert not main.agent_service.is_chat_running(task["id"])

    asyncio.run(scenario())
    replies = [event for event in main.audit_service.list_for_run(task["run_id"]) if event.action == "chat.assistant"]
    assert [event.summary for event in replies] == ["first second"]
    assert main.task_service.get_budget(task["id"]).remote_calls == 1


@pytest.mark.parametrize("disconnect", [False, True])
def test_cancel_or_disconnect_never_persists_a_partial_reply(cowork_api, monkeypatch, disconnect):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "Cancel", "track": "cowork"})

    async def scenario():
        async def model_stream(history, system=None):
            yield "partial"
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                yield "late reply"

        monkeypatch.setattr(main.agent_service.models, "chat_stream", model_stream)
        stream = main.agent_service.chat_stream(task["id"], "hello", "cancel-1")
        await anext(stream)
        assert (await anext(stream))["text"] == "partial"
        if disconnect:
            await stream.aclose()
        else:
            assert main.agent_service.cancel_chat(task["id"], "other") is False
            assert main.agent_service.cancel_chat(task["id"], "cancel-1") is True
            remaining = [event async for event in stream]
            assert not any(event["event"] == "delta" for event in remaining)
            assert remaining[-1]["code"] == "CHAT_CANCELLED"
        assert not main.agent_service.is_chat_running(task["id"])

    asyncio.run(scenario())
    events = main.audit_service.list_for_run(task["run_id"])
    assert not any(event.action == "chat.assistant" for event in events)
    assert any(event.action == "chat.cancelled" for event in events)


@pytest.mark.parametrize("error", [RepositoryError("MODEL_OUTPUT_TRUNCATED", "Truncated"), ValueError("bad provider event")])
def test_failed_stream_terminates_and_same_request_can_retry(cowork_api, monkeypatch, error):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "Retry", "track": "cowork"})
    calls = []

    async def model_stream(history, system=None):
        calls.append(history)
        yield "partial"
        if len(calls) == 1:
            raise error
        yield " complete"

    monkeypatch.setattr(main.agent_service.models, "chat_stream", model_stream)

    async def scenario():
        first = [event async for event in main.agent_service.chat_stream(task["id"], "hello", "retry-1")]
        assert first[-1]["event"] == "error"
        assert not main.agent_service.is_chat_running(task["id"])
        assert not any(event.action == "chat.assistant" for event in main.audit_service.list_for_run(task["run_id"]))
        second = [event async for event in main.agent_service.chat_stream(task["id"], "hello", "retry-1")]
        assert second[-1]["reply"] == "partial complete"
        replay = [event async for event in main.agent_service.chat_stream(task["id"], "hello", "retry-1")]
        assert [event["event"] for event in replay] == ["start", "done"]

    asyncio.run(scenario())
    events = main.audit_service.list_for_run(task["run_id"])
    assert len(calls) == 2
    assert len([event for event in events if event.action == "chat.user"]) == 1
    assert len([event for event in events if event.action == "chat.assistant"]) == 1


def test_stream_route_frames_and_completed_request_replay(cowork_api, monkeypatch):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "API stream", "track": "cowork"})

    async def model_stream(history, system=None):
        yield "你"
        yield "好"

    monkeypatch.setattr(main.agent_service.models, "chat_stream", model_stream)
    response = client.post(f"/api/v1/tasks/{task['id']}/chat/stream", json={"message": "hello", "request_id": "api-1"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert "event: start\n" in response.text
    assert response.text.count("event: delta\n") == 2
    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert all(frame["request_id"] == "api-1" for frame in frames)
    assert frames[-1]["reply"] == "你好"
    replay = post(client, f"/api/v1/tasks/{task['id']}/chat", {"message": "hello", "request_id": "api-1"})
    assert replay["reply"] == "你好"
    assert main.task_service.get_budget(task["id"]).remote_calls == 1


def test_attachment_metadata_survives_reopen_and_conflicting_retry_is_rejected(cowork_api):
    client, _ = cowork_api
    attachments = [{"name": "notes.md", "content": "你好"}]
    message = "Read this\n\n# Attached: notes.md\n```\n你好\n```"
    metadata = {"message_text": "Read this", "attachments": attachments}
    task = post(client, "/api/v1/tasks", {"goal": message, "track": "cowork", **metadata})
    payload = {"message": message, "request_id": "attachment-1", **metadata}
    post(client, f"/api/v1/tasks/{task['id']}/chat", payload)
    events = client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]
    for event in [event for event in events if event["action"] in {"task.create", "chat.user"}]:
        assert event["details"]["attachments"] == attachments
        assert event["details"]["message_text"] == "Read this"
    changed = {"message": message, "request_id": "attachment-1", "message_text": message, "attachments": []}
    response = client.post(f"/api/v1/tasks/{task['id']}/chat", json=changed)
    assert response.status_code == 409
    assert response.json()["code"] == "CHAT_REQUEST_CONFLICT"


@pytest.mark.parametrize("metadata", [
    {"message_text": "different", "attachments": []},
    {"message_text": "hello", "attachments": [{"name": "x\ny", "content": ""}]},
    {"message_text": "hello", "attachments": [{"name": "x", "content": "中" * 22000}]},
    {"message_text": "hello", "attachments": [{"name": "x", "content": ""}] * 6},
    {"attachments": []},
])
def test_invalid_attachment_metadata_is_rejected_before_recording(cowork_api, metadata):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "No attachments", "track": "cowork"})
    assert client.post("/api/v1/tasks", json={"goal": "hello", "track": "cowork", **metadata}).status_code == 422
    assert client.post(f"/api/v1/tasks/{task['id']}/chat/stream", json={"message": "hello", **metadata}).status_code == 422
    assert not any(event.action == "chat.user" for event in main.audit_service.list_for_run(task["run_id"]))


@pytest.mark.parametrize("track", ["code", "cowork"])
def test_renaming_changes_only_display_name_and_persists(cowork_api, track):
    client, directory = cowork_api
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"goal": "Original goal", "track": track, "repository_id": repository["id"]})
    record = main.task_service.get(task["id"])
    original = record.model_dump()
    scope_hash = plan_scope_hash(record)
    validation_hash = main.agent_service.validation_hash(record)
    response = client.put(f"/api/v1/tasks/{task['id']}/name", json={"name": "  New name  "})
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "New name"
    assert record.goal == "Original goal"
    assert plan_scope_hash(record) == scope_hash
    assert main.agent_service.validation_hash(record) == validation_hash
    changed = record.model_dump()
    for key in {"display_name", "updated_at"}:
        original.pop(key)
        changed.pop(key)
    assert changed == original
    reopened = TaskService(main.repository_service, main.task_service.store, main.settings)
    assert reopened.get(task["id"]).display_name == "New name"
    assert reopened.get(task["id"]).goal == "Original goal"


@pytest.mark.parametrize("name", ["", "   ", "x\ny", "x\ry", "x\x00y", "name\n", "名" * 201])
def test_invalid_session_names_leave_task_unchanged(cowork_api, name):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "Keep goal", "track": "cowork"})
    response = client.put(f"/api/v1/tasks/{task['id']}/name", json={"name": name})
    assert response.status_code == 422
    assert main.task_service.get(task["id"]).display_name is None
    assert main.task_service.get(task["id"]).goal == "Keep goal"
