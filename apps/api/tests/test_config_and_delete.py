import json

from app import main
from app.config import Settings
from app.config_service import apply_overrides
from test_api_workflow import post
from test_agent_workflow import approve_plan, create_branch, start_agent_iteration, apply_value


def test_config_reports_state_without_leaking_keys(api_client):
    config = api_client.get("/api/v1/config").json()
    assert config["provider"] == "anthropic"
    assert config["model"]
    assert config["anthropic_key_configured"] is False

    updated = api_client.put("/api/v1/config", json={"anthropic_api_key": "sk-secret-value"}).json()
    assert updated["anthropic_key_configured"] is True
    # The key goes in and never comes back out, not even in the audit trail or the snapshot.
    assert "sk-secret-value" not in json.dumps(updated)
    assert "sk-secret-value" not in api_client.get("/api/v1/config").text


def test_config_update_persists_overrides_for_the_next_boot(api_client):
    updated = api_client.put("/api/v1/config", json={"llm_model": "claude-test-9", "command_timeout_seconds": 42}).json()
    assert updated["model"] == "claude-test-9"
    assert updated["command_timeout_seconds"] == 42

    saved = json.loads((main.settings.state_dir / "config.json").read_text(encoding="utf-8"))
    assert saved == {"llm_model": "claude-test-9", "command_timeout_seconds": 42}

    fresh = Settings(_env_file=None, state_dir=main.settings.state_dir)
    assert apply_overrides(fresh) == ["llm_model", "command_timeout_seconds"]
    assert fresh.llm_model == "claude-test-9"
    assert fresh.command_timeout_seconds == 42


def test_switching_provider_rebuilds_the_live_model(api_client):
    assert type(main.agent_service.models).__name__ == "AnthropicModelProvider"

    updated = api_client.put("/api/v1/config", json={"llm_provider": "fake"}).json()
    assert updated["provider"] == "fake"
    assert updated["restart_required"] is False
    assert type(main.agent_service.models).__name__ == "FakeModelProvider"


def test_empty_config_update_is_rejected(api_client):
    response = api_client.put("/api/v1/config", json={})
    assert response.status_code == 422
    assert response.json()["code"] == "CONFIG_EMPTY"


def test_corrupt_override_file_does_not_stop_the_api(api_client, tmp_path):
    path = main.settings.state_dir / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    fresh = Settings(_env_file=None, state_dir=main.settings.state_dir)
    assert apply_overrides(fresh) == []
    assert fresh.llm_provider == "anthropic"


def test_delete_task_removes_checkpoints_and_approvals(api_client, demo_repository):
    repository = post(api_client, "/api/v1/repositories", {"path": str(demo_repository)})
    task = post(api_client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Fix value"})
    approve_plan(api_client, task)
    create_branch(api_client, task)
    start_agent_iteration(api_client, task)
    apply_value(api_client, task, 2)

    checkpoint_dir = main.settings.state_dir / "checkpoints"
    assert list(checkpoint_dir.iterdir())
    assert main.approval_service.list_for_task(task["id"])

    response = api_client.delete(f"/api/v1/tasks/{task['id']}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == task["id"]
    assert body["checkpoints"] >= 1
    assert body["approvals"] >= 1

    assert api_client.get(f"/api/v1/tasks/{task['id']}").status_code == 404
    assert not list(checkpoint_dir.iterdir())
    assert main.approval_service.list_for_task(task["id"]) == []
    # The repository itself survives a task delete.
    assert api_client.get(f"/api/v1/repositories/{repository['id']}").status_code == 200

    assert api_client.delete(f"/api/v1/tasks/{task['id']}").status_code == 404


def test_delete_repository_needs_cascade_and_never_touches_disk(api_client, demo_repository):
    repository = post(api_client, "/api/v1/repositories", {"path": str(demo_repository)})
    post(api_client, "/api/v1/tasks", {"repository_id": repository["id"], "goal": "Fix value"})

    blocked = api_client.delete(f"/api/v1/repositories/{repository['id']}")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "REPOSITORY_IN_USE"
    assert blocked.json()["details"]["tasks"] == 1

    deleted = api_client.delete(f"/api/v1/repositories/{repository['id']}?cascade=true")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["tasks"] == 1

    assert api_client.get("/api/v1/repositories").json()["total"] == 0
    assert api_client.get("/api/v1/tasks").json()["total"] == 0
    assert api_client.delete(f"/api/v1/repositories/{repository['id']}").status_code == 404
    # Unregistering a folder must never delete the user's files.
    assert (demo_repository / "app.py").is_file()


def test_saved_models_switch_the_active_provider(api_client):
    models = [
        {"id": "local", "label": "Qwen local", "provider": "openai", "model": "Qwen3.8-27B",
         "base_url": "https://local.example/v1", "api_key": "sk-local-secret"},
        {"id": "claude", "label": "Claude", "provider": "anthropic", "model": "claude-test-x",
         "base_url": "", "api_key": "sk-ant-secret"},
    ]
    updated = api_client.put("/api/v1/config", json={"models": models, "active_model_id": "local"}).json()

    assert updated["active_model_id"] == "local"
    assert [item["id"] for item in updated["models"]] == ["local", "claude"]
    assert all(item["api_key_configured"] for item in updated["models"])
    # Saved keys are write-only, exactly like the flat fields.
    assert "sk-local-secret" not in json.dumps(updated)
    assert "sk-ant-secret" not in json.dumps(updated)
    assert (updated["provider"], updated["model"]) == ("openai", "Qwen3.8-27B")
    assert type(main.agent_service.models).__name__ == "OpenAIModelProvider"

    switched = api_client.put("/api/v1/config", json={"active_model_id": "claude"}).json()
    assert (switched["provider"], switched["model"]) == ("anthropic", "claude-test-x")
    assert type(main.agent_service.models).__name__ == "AnthropicModelProvider"

    # The choice survives a restart: apply_overrides points the flat settings at the active profile.
    fresh = Settings(_env_file=None, state_dir=main.settings.state_dir)
    assert apply_overrides(fresh) == ["active_model_id"]
    assert (fresh.llm_provider, fresh.llm_model) == ("anthropic", "claude-test-x")
    assert fresh.anthropic_api_key == "sk-ant-secret"

    # Dropping the active entry falls back to the flat settings instead of pointing at a ghost.
    removed = api_client.put("/api/v1/config", json={"models": [models[0]]}).json()
    assert removed["active_model_id"] is None
    assert [item["id"] for item in removed["models"]] == ["local"]

    unknown = api_client.put("/api/v1/config", json={"active_model_id": "ghost"})
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "CONFIG_INVALID_VALUE"


def test_editing_the_model_list_keeps_keys_the_ui_cannot_see(api_client):
    models = [{"id": "local", "label": "Qwen", "provider": "openai", "model": "qwen", "base_url": "", "api_key": "sk-keep"}]
    api_client.put("/api/v1/config", json={"models": models, "active_model_id": "local"})

    # The UI only has the masked view; saving the list again must not wipe the key.
    edited = api_client.put("/api/v1/config", json={"models": [
        {"id": "local", "label": "Qwen renamed", "provider": "openai", "model": "qwen", "base_url": ""},
        {"id": "second", "label": "Second", "provider": "anthropic", "model": "claude-y", "base_url": "", "api_key": "sk-new"},
    ]}).json()

    assert [item["label"] for item in edited["models"]] == ["Qwen renamed", "Second"]
    assert all(item["api_key_configured"] for item in edited["models"])
    assert main.settings.openai_api_key == "sk-keep"

    # An explicit empty key clears it.
    cleared = api_client.put("/api/v1/config", json={"models": [
        {"id": "local", "label": "Qwen renamed", "provider": "openai", "model": "qwen", "base_url": "", "api_key": ""},
    ]}).json()
    assert cleared["models"][0]["api_key_configured"] is False
