import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from conftest import build_settings, mount_services
from fastapi.testclient import TestClient
from test_api_workflow import post

from app import main
from app.model_provider import FakeModelProvider
from app.repository_service import RepositoryError


@pytest.fixture(autouse=True)
def clean_fakes():
    FakeModelProvider.reset()
    yield
    FakeModelProvider.reset()


@pytest.fixture
def cowork_api(demo_repository, tmp_path, monkeypatch):
    settings = build_settings(demo_repository, tmp_path, llm_provider="fake")
    services = mount_services(monkeypatch, settings)
    with TestClient(main.app) as client:
        yield client, demo_repository
    services.store.close()


def test_cowork_chat_roundtrip_records_turns_and_never_touches_git(cowork_api):
    client, _ = cowork_api
    FakeModelProvider.seed_chat("2 + 2 is 4.")

    task = post(client, "/api/v1/tasks", {"goal": "math help", "track": "cowork"})
    assert task["track"] == "cowork"
    assert task["plan"] is None  # no coding plan for a chat
    assert task["repository_id"] is None

    reply = post(client, f"/api/v1/tasks/{task['id']}/chat", {"message": "what is 2+2?"})
    assert reply["reply"] == "2 + 2 is 4."

    events = client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]
    by_action = {event["action"]: event["summary"] for event in events}
    assert by_action["chat.user"] == "what is 2+2?"
    assert by_action["chat.assistant"] == "2 + 2 is 4."
    # Chat never commits or branches.
    assert client.get(f"/api/v1/tasks/{task['id']}/commits").json()["total"] == 0


def test_cowork_loop_is_rejected(cowork_api):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "just chatting", "track": "cowork"})
    response = client.post(f"/api/v1/runs/{task['run_id']}/loop", json={})
    assert response.status_code >= 400
    assert response.json()["code"] == "COWORK_NO_LOOP"


def test_cowork_chat_sends_history_and_folder_context(cowork_api):
    client, directory = cowork_api
    FakeModelProvider.seed_chat("first")
    FakeModelProvider.seed_chat("second")
    repository = post(client, "/api/v1/repositories", {"path": str(directory)})
    task = post(client, "/api/v1/tasks", {"goal": "about this folder", "track": "cowork", "repository_id": repository["id"]})

    post(client, f"/api/v1/tasks/{task['id']}/chat", {"message": "hello"})
    post(client, f"/api/v1/tasks/{task['id']}/chat", {"message": "and now?"})

    history, system = FakeModelProvider._chat_calls[-1]
    assert [role for role, _ in history] == ["user", "assistant", "user"]
    assert history[-1] == ("user", "and now?")
    assert system and "attached a local folder" in system  # cowork can read the folder for context


@pytest.mark.parametrize("succeeds", [True, False])
def test_cowork_chat_counts_calls_without_a_lifetime_budget(cowork_api, monkeypatch, succeeds):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "bounded chat", "track": "cowork"})
    main.task_service.get(task["id"]).budget.max_remote_calls = 1
    calls = []

    async def chat(history, system=None):
        calls.append(history)
        if not succeeds:
            raise RepositoryError("MODEL_TIMEOUT", "simulated model timeout")
        return "Hello."

    monkeypatch.setattr(main.agent_service.models, "chat", chat)
    task_url = f"/api/v1/tasks/{task['id']}"
    first = client.post(task_url + "/chat", json={"message": "hello"})
    if succeeds:
        assert first.status_code == 200
        assert first.json()["reply"] == "Hello."
    else:
        assert first.json()["code"] == "MODEL_TIMEOUT"
    again = client.post(task_url + "/chat", json={"message": "again"})
    assert again.status_code == (200 if succeeds else 504)
    assert len(calls) == 2
    budget = client.get(task_url + "/budget").json()
    assert budget["remote_calls"] == 2
    assert budget["consecutive_failures"] == (0 if succeeds else 2)
    assert budget["exhausted_reason"] is None


@pytest.mark.parametrize("attached", [False, True])
@pytest.mark.parametrize("succeeds", [True, False])
def test_pending_cowork_blocks_overlapping_turns_and_deletion(cowork_api, monkeypatch, attached, succeeds):
    client, directory = cowork_api
    repository = post(client, "/api/v1/repositories", {"path": str(directory)}) if attached else None
    payload = {"goal": "pending chat", "track": "cowork", "repository_id": repository["id"] if repository else None}
    task = post(client, "/api/v1/tasks", payload)
    other = post(client, "/api/v1/tasks", {**payload, "goal": "independent chat"})
    task_url = f"/api/v1/tasks/{task['id']}"
    entered, released = Event(), Event()

    async def chat(history, system=None):
        if history[-1][1] != "pending":
            return "Independent reply."
        entered.set()
        while not released.is_set():
            await asyncio.sleep(0.01)
        if not succeeds:
            raise RepositoryError("MODEL_TIMEOUT", "simulated model timeout")
        return "Pending reply."

    monkeypatch.setattr(main.agent_service.models, "chat", chat)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.post, task_url + "/chat", json={"message": "pending"})
        try:
            assert entered.wait(5), "chat did not reach the model"
            overlap = client.post(task_url + "/chat", json={"message": "duplicate"})
            assert overlap.status_code == 409, overlap.text
            assert overlap.json()["code"] == "WORKSPACE_BUSY"
            assert client.get(task_url + "/budget").json()["remote_calls"] == 1
            events = client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]
            assert [event["summary"] for event in events if event["action"] == "chat.user"] == ["pending"]

            blocked = client.delete(task_url)
            assert blocked.status_code == 409, blocked.text
            assert blocked.json()["code"] == "WORKSPACE_BUSY"
            if repository:
                cascade = client.delete(f"/api/v1/repositories/{repository['id']}?cascade=true")
                assert cascade.status_code == 409, cascade.text
                assert cascade.json()["code"] == "WORKSPACE_BUSY"
                assert client.get(f"/api/v1/tasks/{other['id']}").status_code == 200
                assert client.get(f"/api/v1/repositories/{repository['id']}").status_code == 200
            assert client.get(task_url).status_code == 200
            independent = post(client, f"/api/v1/tasks/{other['id']}/chat", {"message": "independent"})
            assert independent["reply"] == "Independent reply."
        finally:
            released.set()
        response = pending.result(timeout=5)

    if succeeds:
        assert response.status_code == 200, response.text
        assert response.json()["reply"] == "Pending reply."
    else:
        assert response.json()["code"] == "MODEL_TIMEOUT"
    assert client.delete(task_url).status_code == 200
    if repository:
        assert client.delete(f"/api/v1/repositories/{repository['id']}?cascade=true").status_code == 200
        assert client.get(f"/api/v1/tasks/{other['id']}").status_code == 404


def test_cancelled_cowork_releases_pending_turn(cowork_api, monkeypatch):
    client, _ = cowork_api
    task = post(client, "/api/v1/tasks", {"goal": "cancelled chat", "track": "cowork"})

    async def scenario():
        entered = asyncio.Event()

        async def chat(history, system=None):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(main.agent_service.models, "chat", chat)
        pending = asyncio.create_task(main.agent_service.chat(task["id"], "pending"))
        await asyncio.wait_for(entered.wait(), timeout=5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not main.agent_service.is_chat_running(task["id"])

    asyncio.run(scenario())
    assert client.delete(f"/api/v1/tasks/{task['id']}").status_code == 200
