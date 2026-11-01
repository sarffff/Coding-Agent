import asyncio
import json

import pytest

from app import main
from app.config import Settings
from app.config_service import apply_overrides
from app import model_provider
from app.models import ConfigUpdateRequest, ModelProfile
from app.services import build_services
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
    assert (removed["provider"], removed["model"]) == ("anthropic", "claude-sonnet-5-5")
    assert type(main.agent_service.models).__name__ == "AnthropicModelProvider"
    assert main.settings.anthropic_api_key is None
    assert main.settings.openai_api_key is None

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


@pytest.mark.parametrize(
    "provider, default_url, endpoint, provider_type",
    [
        ("openai", "https://api.openai.com/v1", "/chat/completions", "OpenAIModelProvider"),
        ("anthropic", "https://api.anthropic.com", "/v1/messages", "AnthropicModelProvider"),
    ],
)
def test_blank_profile_url_resets_custom_gateway_and_survives_restart(
    api_client, monkeypatch, provider, default_url, endpoint, provider_type
):
    requests = []

    async def post_json(settings, url, headers, payload):
        key = headers.get("x-api-key") or headers["Authorization"].removeprefix("Bearer ")
        requests.append((url, key, payload["model"]))
        return {"choices": [{"message": {"content": "ok"}}], "content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(model_provider, "_post_json", post_json)
    custom_url = "https://gateway.example/v1"
    models = [
        {"id": "custom", "label": "Custom", "provider": provider, "model": "custom-model",
         "base_url": custom_url, "api_key": "sk-custom"},
        {"id": "default", "label": "Default", "provider": provider, "model": "default-model",
         "base_url": "", "api_key": "sk-default"},
    ]
    created = api_client.put("/api/v1/config", json={"models": models, "active_model_id": "custom"})
    assert created.status_code == 200, created.text
    assert created.json()[f"{provider}_api_url"] == custom_url
    assert asyncio.run(main.agent_service.models.chat([("user", "hello")])) == "ok"

    switched = api_client.put("/api/v1/config", json={"active_model_id": "default"})
    assert switched.status_code == 200, switched.text
    assert switched.json()[f"{provider}_api_url"] == default_url
    assert switched.json()["active_model_id"] == "default"
    assert type(main.agent_service.models).__name__ == provider_type
    assert getattr(main.agent_service.models.settings, f"{provider}_api_url") == default_url
    assert getattr(main.agent_service.models.settings, f"{provider}_api_key") == "sk-default"
    assert main.agent_service.models.settings.llm_model == "default-model"
    assert asyncio.run(main.agent_service.models.chat([("user", "hello")])) == "ok"

    fresh = Settings(_env_file=None, state_dir=main.settings.state_dir, **{f"{provider}_api_url": custom_url})
    restarted = build_services(fresh)
    try:
        assert restarted.config_service.describe().active_model_id == "default"
        assert type(restarted.agent_service.models).__name__ == provider_type
        assert getattr(fresh, f"{provider}_api_url") == default_url
        assert getattr(fresh, f"{provider}_api_key") == "sk-default"
        assert fresh.llm_model == "default-model"
        assert asyncio.run(restarted.agent_service.models.chat([("user", "hello")])) == "ok"
    finally:
        restarted.store.close()

    assert requests == [
        (custom_url + endpoint, "sk-custom", "custom-model"),
        (default_url + endpoint, "sk-default", "default-model"),
        (default_url + endpoint, "sk-default", "default-model"),
    ]


@pytest.mark.parametrize("persisted_baseline", [False, True])
def test_removing_active_model_restores_flat_configuration_after_restart(tmp_path, monkeypatch, persisted_baseline):
    injected = {
        "llm_provider": "openai", "llm_model": "injected-model",
        "openai_api_url": "https://injected.example/v1", "openai_api_key": "injected-key",
        "anthropic_api_url": "https://injected-anthropic.example", "anthropic_api_key": "injected-ant-key",
    }

    def fresh_settings():
        return Settings(_env_file=None, workspace_root=tmp_path, state_dir=tmp_path / "state", **injected)

    services = build_services(fresh_settings())
    baseline = dict(injected)
    try:
        if persisted_baseline:
            baseline.update(llm_provider="anthropic", llm_model="saved-flat-model",
                            anthropic_api_url="https://saved.example", anthropic_api_key="saved-key")
            services.config_service.update(ConfigUpdateRequest(**baseline))
        services.config_service.update(ConfigUpdateRequest(
            models=[ModelProfile(id="active", label="Active", provider="openai", model="active-model",
                                 base_url="https://active.example/v1", api_key="active-key")],
            active_model_id="active",
        ))
    finally:
        services.store.close()

    requests = []

    async def post_json(settings, url, headers, payload):
        key = headers.get("x-api-key") or headers["Authorization"].removeprefix("Bearer ")
        requests.append((url, key, payload["model"]))
        return {"choices": [{"message": {"content": "ok"}}], "content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(model_provider, "_post_json", post_json)
    restarted = build_services(fresh_settings())
    try:
        assert restarted.config_service.describe().active_model_id == "active"
        removed = restarted.config_service.update(ConfigUpdateRequest(models=[]))
        assert removed.active_model_id is None
        assert {name: getattr(restarted.settings, name) for name in baseline} == baseline
        assert restarted.agent_service.models.settings is restarted.settings
        provider_type = "AnthropicModelProvider" if persisted_baseline else "OpenAIModelProvider"
        assert type(restarted.agent_service.models).__name__ == provider_type
        assert asyncio.run(restarted.agent_service.models.chat([("user", "hello")])) == "ok"
    finally:
        restarted.store.close()

    again = build_services(fresh_settings())
    try:
        assert again.config_service.describe().active_model_id is None
        assert {name: getattr(again.settings, name) for name in baseline} == baseline
        assert type(again.agent_service.models).__name__ == provider_type
    finally:
        again.store.close()
    provider = baseline["llm_provider"]
    endpoint = "/v1/messages" if provider == "anthropic" else "/chat/completions"
    assert requests == [(baseline[f"{provider}_api_url"] + endpoint, baseline[f"{provider}_api_key"], baseline["llm_model"])]


def test_rejected_config_update_keeps_live_and_saved_state(api_client):
    models = [{"id": "current", "label": "Current", "provider": "openai", "model": "current-model",
               "api_key": "current-key"}]
    assert api_client.put("/api/v1/config", json={"models": models, "active_model_id": "current"}).status_code == 200
    before_config = main.config_service.describe()
    before_settings = main.settings.model_dump()
    before_provider = main.agent_service.models
    path = main.settings.state_dir / "config.json"
    before_saved = path.read_text(encoding="utf-8")

    rejected = api_client.put("/api/v1/config", json={
        "llm_provider": "fake", "llm_max_tokens": 512, "openai_api_key": "rejected-key",
        "models": [{"id": "replacement", "label": "Replacement", "provider": "fake", "model": "replacement"}],
        "active_model_id": "unknown",
    })
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "CONFIG_INVALID_VALUE"
    assert main.config_service.describe() == before_config
    assert main.settings.model_dump() == before_settings
    assert main.agent_service.models is before_provider
    assert path.read_text(encoding="utf-8") == before_saved


@pytest.mark.parametrize("failure", ["persist", "provider"])
def test_failed_config_publication_keeps_live_and_saved_state(api_client, monkeypatch, failure):
    assert api_client.put("/api/v1/config", json={"llm_model": "saved-model"}).status_code == 200
    before_config = main.config_service.describe()
    before_settings = main.settings.model_dump()
    before_provider = main.agent_service.models
    path = main.settings.state_dir / "config.json"
    before_saved = path.read_text(encoding="utf-8")

    def fail(*args):
        raise OSError("Cannot publish config")

    if failure == "persist":
        monkeypatch.setattr(main.config_service, "_persist", fail)
    else:
        monkeypatch.setattr("app.config_service.build_model_provider", fail)
    with pytest.raises(OSError, match="Cannot publish config"):
        main.config_service.update(ConfigUpdateRequest(
            llm_max_tokens=512, models=[ModelProfile(id="new", label="New", provider="fake", model="new-model")],
            active_model_id="new",
        ))
    assert main.config_service.describe() == before_config
    assert main.settings.model_dump() == before_settings
    assert main.agent_service.models is before_provider
    assert path.read_text(encoding="utf-8") == before_saved
