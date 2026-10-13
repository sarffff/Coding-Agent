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
def test_cowork_chat_counts_success_and_failure_and_stops_at_the_budget(cowork_api, monkeypatch, succeeds):
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
    blocked = client.post(task_url + "/chat", json={"message": "again"})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "TASK_BUDGET_EXHAUSTED"
    assert len(calls) == 1
    budget = client.get(task_url + "/budget").json()
    assert budget["remote_calls"] == 1
    assert budget["consecutive_failures"] == (0 if succeeds else 1)
