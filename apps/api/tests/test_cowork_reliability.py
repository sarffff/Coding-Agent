import asyncio
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app import main
from app.audit_service import AuditService
from app.models import ConfigUpdateRequest
from app.models import AgentLoopRequest, DraftPatchRequest
from app.repository_service import RepositoryError
from test_api_workflow import post
from test_cowork_chat import clean_fakes, cowork_api
from test_agent_workflow import approve_plan, create_branch, start_agent_iteration


def test_active_chat_blocks_model_changes_before_saving(cowork_api, monkeypatch):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "Pending", "track": "cowork"})

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def reply(history, system=None):
            entered.set()
            await release.wait()
            return "original"

        monkeypatch.setattr(main.agent_service.models, "chat", reply)
        pending = asyncio.create_task(main.agent_service.chat(task["id"], "hello"))
        await asyncio.wait_for(entered.wait(), 5)
        provider = main.agent_service.models
        before = main.settings.model_dump()
        try:
            with pytest.raises(RepositoryError, match="Stop") as error:
                main.config_service.update(ConfigUpdateRequest(llm_model="replacement"))
            assert error.value.code == "MODEL_CONFIG_BUSY"
            assert main.settings.model_dump() == before
            assert main.agent_service.models is provider
            assert not (main.settings.state_dir / "config.json").exists()
            assert main.config_service.update(ConfigUpdateRequest(command_timeout_seconds=42)).command_timeout_seconds == 42
        finally:
            release.set()
            await pending

    asyncio.run(scenario())
    assert client.put("/api/v1/config", json={"llm_model": "replacement"}).status_code == 200


@pytest.mark.parametrize("execution", ["loop", "draft"])
def test_model_configuration_is_locked_for_code_execution(cowork_api, monkeypatch, execution):
    client, folder = cowork_api
    repository = post(client, "/api/v1/repositories", {"path": str(folder)})
    task = post(client, "/api/v1/tasks", {"goal": "Fix value in app.py", "repository_id": repository["id"]})
    if execution == "draft":
        approve_plan(client, task)
        create_branch(client, task)
        start_agent_iteration(client, task)

    async def scenario():
        entered = asyncio.Event()

        async def propose(*args):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(main.agent_service.models, "propose_patch", propose)
        if execution == "loop":
            main.agent_service.start_loop(task["run_id"], AgentLoopRequest())
            pending = None
        else:
            pending = asyncio.create_task(main.agent_service.draft_patch(task["run_id"], DraftPatchRequest()))
        try:
            await asyncio.wait_for(entered.wait(), 30)
            with pytest.raises(RepositoryError) as error:
                main.config_service.update(ConfigUpdateRequest(llm_max_tokens=512))
            assert error.value.code == "MODEL_CONFIG_BUSY"
            assert not (main.settings.state_dir / "config.json").exists()
        finally:
            main.agent_service.stop_loop(task["run_id"])
            if pending:
                await asyncio.gather(pending, return_exceptions=True)
            for _ in range(100):
                if not main.agent_service._loop_running(task["id"]):
                    break
                await asyncio.sleep(0.01)
        assert not main.agent_service._active_drafts

    asyncio.run(scenario())
    assert client.put("/api/v1/config", json={"llm_max_tokens": 512}).status_code == 200


def test_cowork_idle_and_lifetime_limits_do_not_block_next_turn(cowork_api):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "Long chat", "track": "cowork"})
    record = main.task_service.get(task["id"])
    post(client, f"/api/v1/tasks/{task['id']}/chat", {"message": "first"})
    if record.budget.started_at:
        record.budget.started_at -= timedelta(seconds=3601)
    record.budget.remote_calls = 50
    record.budget.consecutive_failures = 3
    record.budget.exhausted_reason = "wall_time"
    response = client.post(f"/api/v1/tasks/{task['id']}/chat", json={"message": "continue"})
    assert response.status_code == 200, response.text
    assert main.task_service.get_budget(task["id"]).exhausted_reason is None
    assert record.budget.consecutive_failures == 0


def test_failed_chat_retry_reuses_message_and_completed_reply(cowork_api, monkeypatch):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "Retry", "track": "cowork"})
    calls = []

    async def reply(history, system=None):
        calls.append(history)
        if len(calls) == 1:
            raise RepositoryError("MODEL_TIMEOUT", "Timeout")
        return "recovered"

    monkeypatch.setattr(main.agent_service.models, "chat", reply)
    url = f"/api/v1/tasks/{task['id']}/chat"
    message = {"message": "same", "request_id": "request-1"}
    assert client.post(url, json=message).status_code == 504
    assert client.post(url, json=message).json()["reply"] == "recovered"
    assert client.post(url, json=message).json()["reply"] == "recovered"
    assert len(calls) == 2
    events = main.audit_service.list_for_run(task["run_id"])
    assert [item.summary for item in events if item.action == "chat.user"] == ["same"]
    assert [item.summary for item in events if item.action == "chat.assistant"] == ["recovered"]
    assert client.post(url, json={**message, "message": "different"}).status_code == 409


def test_cancel_endpoint_discards_reply_and_releases_chat(cowork_api, monkeypatch):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "Cancel", "track": "cowork"})

    async def scenario():
        entered = asyncio.Event()

        async def reply(history, system=None):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return "late reply"

        monkeypatch.setattr(main.agent_service.models, "chat", reply)
        async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as api:
            url = f"/api/v1/tasks/{task['id']}/chat"
            pending = asyncio.create_task(api.post(url, json={"message": "pending", "request_id": "cancel-me"}))
            await asyncio.wait_for(entered.wait(), 5)
            wrong = await api.post(url + "/cancel", json={"request_id": "different"})
            assert wrong.json()["cancelled"] is False
            assert main.agent_service.is_chat_running(task["id"])
            stopped = await api.post(url + "/cancel", json={"request_id": "cancel-me"})
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["cancelled"] is True
            response = await asyncio.wait_for(pending, 5)
            assert response.status_code == 409
            assert response.json()["code"] == "CHAT_CANCELLED"
            assert not main.agent_service.is_chat_running(task["id"])
            assert not any(event.action == "chat.assistant" for event in main.audit_service.list_for_run(task["run_id"]))

    asyncio.run(scenario())
    assert client.delete(f"/api/v1/tasks/{task['id']}").status_code == 200


def test_chat_history_survives_audit_limits_and_task_deletion(cowork_api):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "Keep history", "track": "cowork"})
    post(client, f"/api/v1/tasks/{task['id']}/chat", {"message": "remember"})
    main.audit_service.max_events = 1
    for index in range(3):
        main.audit_service.record("unrelated", "succeeded", str(index), run_id="other")
    events = main.audit_service.list_for_run(task["run_id"])
    assert len([event for event in events if event.action in {"chat.user", "chat.assistant"}]) == 2
    reopened = AuditService(main.audit_service.store, max_events=1)
    assert len([event for event in reopened.list_for_run(task["run_id"]) if event.action.startswith("chat.")]) == 2
    assert client.delete(f"/api/v1/tasks/{task['id']}").status_code == 200
    assert not any(event.action.startswith("chat.") for event in main.audit_service.list_for_run(task["run_id"]))


def test_cowork_reads_relevant_source_without_running_commands(cowork_api, monkeypatch):
    client, folder = cowork_api
    (folder / "feature.py").write_text("def greet_person(name):\n    return f'Hello {name}'\n", encoding="utf-8")
    (folder / ".env").write_text("SECRET=must-not-be-read", encoding="utf-8")
    repository = post(client, "/api/v1/repositories", {"path": str(folder)})
    task = post(client, "/api/v1/tasks", {"goal": "Explain", "track": "cowork", "repository_id": repository["id"]})
    systems = []

    async def reply(history, system=None):
        systems.append(system)
        return "explanation"

    def no_commands(*args, **kwargs):
        raise AssertionError("Cowork must not execute commands")

    monkeypatch.setattr(main.agent_service.models, "chat", reply)
    monkeypatch.setattr(main.test_service, "run_command", no_commands)
    post(client, f"/api/v1/tasks/{task['id']}/chat", {"message": "Explain greet_person in feature.py and .env"})
    assert "def greet_person(name)" in systems[0]
    assert "must-not-be-read" not in systems[0]
    assert (folder / "feature.py").read_text(encoding="utf-8").startswith("def greet_person")


def test_long_chat_history_is_bounded_without_deleting_messages(cowork_api, monkeypatch):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "Bounded history", "track": "cowork"})
    main.settings.llm_max_context_bytes = 1000
    for index in range(20):
        request_id = f"old-{index}"
        main.audit_service.record("chat.user", "succeeded", "u" * 100, run_id=task["run_id"], details={"request_id": request_id})
        main.audit_service.record("chat.assistant", "succeeded", "a" * 100, run_id=task["run_id"], details={"request_id": request_id})
    captured = []

    async def reply(history, system=None):
        captured.extend(history)
        return "bounded"

    monkeypatch.setattr(main.agent_service.models, "chat", reply)
    post(client, f"/api/v1/tasks/{task['id']}/chat", {"message": "current"})
    assert captured[-1] == ("user", "current")
    assert sum(len(content.encode("utf-8")) for _, content in captured) <= 1000
    assert len(main.audit_service.list_for_run(task["run_id"])) == 43


def test_chat_timeout_discards_a_reply_that_ignores_cancellation(cowork_api, monkeypatch):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "Timeout", "track": "cowork"})
    main.settings.llm_timeout_seconds = 1

    async def reply(history, system=None):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return "too late"

    monkeypatch.setattr(main.agent_service.models, "chat", reply)
    response = client.post(f"/api/v1/tasks/{task['id']}/chat", json={"message": "slow"})
    assert response.status_code == 504, response.text
    assert not main.agent_service.is_chat_running(task["id"])
    assert not any(event.action == "chat.assistant" for event in main.audit_service.list_for_run(task["run_id"]))
