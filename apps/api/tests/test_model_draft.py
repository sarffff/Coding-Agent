import asyncio
import hashlib
import json
import os
from threading import Event

import pytest
from conftest import build_settings, mount_services
from test_api_workflow import apply_value, create_task, post

from app import model_provider as mp
from app.config import Settings
from app.model_provider import AnthropicModelProvider, FakeModelProvider, OpenAIModelProvider, PatchProposal, extract_proposal_json
from app.models import PatchFile
from app.repository_service import RepositoryError


@pytest.fixture(autouse=True)
def clean_fake_model():
    FakeModelProvider.reset()
    yield
    FakeModelProvider.reset()


@pytest.fixture
def model_api(demo_repository, tmp_path, monkeypatch):
    from app import main
    from fastapi.testclient import TestClient

    settings = build_settings(demo_repository, tmp_path, llm_provider="fake")
    services = mount_services(monkeypatch, settings)
    with TestClient(main.app) as client:
        yield client, demo_repository
    services.store.close()


def test_extract_proposal_json_handles_fences_prose_and_braces():
    assert extract_proposal_json('{"rationale": "r", "files": []}') == {"rationale": "r", "files": []}

    fenced = extract_proposal_json('Here you go:\n```json\n{"files": [{"path": "a"}]}\n```\nDone.')
    assert fenced["files"][0]["path"] == "a"

    # Braces inside the generated file content must not truncate the JSON span.
    code = extract_proposal_json('{"files": [{"path": "m.py", "content": "def f():\\n    return {}\\n"}]}')
    assert code["files"][0]["content"] == "def f():\n    return {}\n"

    with pytest.raises(RepositoryError) as excinfo:
        extract_proposal_json("no json at all")
    assert excinfo.value.code == "MODEL_INVALID_OUTPUT"

def test_model_draft_flows_into_preview_and_apply(model_api):
    client, directory = model_api
    task = create_task(client, directory)
    FakeModelProvider.seed(
        [PatchFile(path="app.py", content="def value():\n    return 2\n", operation="update")],
        rationale="return 2 so the test passes",
    )
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {})
    assert draft["files"] == ["app.py"]
    assert draft["rationale"] == "return 2 so the test passes"
    assert "+    return 2" in draft["diff"]

    applied = post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {"patch_id": draft["patch_id"], "confirm": True})
    assert applied["applied_files"] == ["app.py"]
    result = post(client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest", "timeout_seconds": 15})
    assert result["status"] == "passed", result


def test_model_draft_rejects_protected_paths(model_api):
    client, directory = model_api
    task = create_task(client, directory)
    FakeModelProvider.seed([PatchFile(path=".env", content="SECRET=1\n", operation="create")])
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={})
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "PROTECTED_PATH"


def test_model_draft_consumes_budget_and_trips_the_breaker(demo_repository, tmp_path, monkeypatch):
    from app import main
    from fastapi.testclient import TestClient

    settings = build_settings(demo_repository, tmp_path, llm_provider="fake", task_max_remote_calls=1)
    services = mount_services(monkeypatch, settings)
    with TestClient(main.app) as client:
        task = create_task(client, demo_repository)
        FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])
        FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 3\n")])
        first = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={})
        assert first.status_code == 200, first.text
        second = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={})
        assert second.status_code == 409
        assert second.json()["code"] == "TASK_BUDGET_EXHAUSTED"
    services.store.close()


def test_both_providers_extract_their_response_shapes(monkeypatch):
    settings = Settings(anthropic_api_key="k", openai_api_key="k")
    payload = {"rationale": "r", "files": [{"path": "a.py", "operation": "create", "content": "x = 1\n"}]}

    async def fake_anthropic(_settings, url, headers, body):
        assert url.endswith("/v1/messages")
        assert headers["x-api-key"] == "k"
        return {"content": [{"type": "text", "text": json.dumps(payload)}]}

    monkeypatch.setattr(mp, "_post_json", fake_anthropic)
    anthropic = asyncio.run(AnthropicModelProvider(settings).propose_patch("goal", [], None))
    assert anthropic.files[0].path == "a.py" and anthropic.rationale == "r"

    async def fake_openai(_settings, url, headers, body):
        assert url.endswith("/chat/completions")
        assert headers["Authorization"] == "Bearer k"
        return {"choices": [{"message": {"content": json.dumps(payload)}}]}

    monkeypatch.setattr(mp, "_post_json", fake_openai)
    openai = asyncio.run(OpenAIModelProvider(settings).propose_patch("goal", [], None))
    assert openai.files[0].path == "a.py"


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_truncated_model_output_is_rejected_even_when_json_is_valid(monkeypatch, provider):
    payload = json.dumps({"files": [{"path": "app.py", "operation": "update", "content": "partial"}]})
    async def truncated(*args):
        if provider == "anthropic":
            return {"stop_reason": "max_tokens", "content": [{"type": "text", "text": payload}]}
        return {"choices": [{"finish_reason": "length", "message": {"content": payload}}]}
    monkeypatch.setattr(mp, "_post_json", truncated)
    settings = Settings(anthropic_api_key="test", openai_api_key="test")
    model = AnthropicModelProvider(settings) if provider == "anthropic" else OpenAIModelProvider(settings)
    with pytest.raises(RepositoryError) as error:
        asyncio.run(model.propose_patch("fix", [], None))
    assert error.value.code == "MODEL_OUTPUT_TRUNCATED"


def test_draft_passes_requested_context_file_content_to_the_model(model_api):
    client, directory = model_api
    task = create_task(client, directory)
    FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])
    post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": ["app.py"]})
    _goal, context_files, _failure = FakeModelProvider._calls[-1]
    gathered = dict(context_files)
    assert "app.py" in gathered  # the requested file's real content reached the model
    assert "def value():" in gathered["app.py"] and "return 1" in gathered["app.py"]


def test_draft_prioritizes_goal_paths_and_bounds_safe_complete_context(model_api, monkeypatch):
    from app import main

    client, directory = model_api
    task = create_task(client, directory)
    (directory / "src").mkdir()
    content = "# " + "说明" * 100 + "\ndef feature():\n    return 1\n"
    (directory / "src" / "feature.py").write_text(content, encoding="utf-8", newline="")
    (directory / ".env").write_text("SECRET=do-not-send\n", encoding="utf-8")
    (directory / "large.py").write_text("# " + "a" * 1_200, encoding="utf-8")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 2)
    monkeypatch.setattr(main.settings, "llm_max_context_bytes", 1_000)
    FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])
    post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {
        "feedback": "Fix src/feature.py", "context_paths": [".env", "missing.py", "large.py"],
    })
    context = FakeModelProvider._calls[-1][1]
    assert context[0] == ("src/feature.py", content)
    assert len(context) <= 2
    assert sum(len(text.encode("utf-8")) for _, text in context) <= 1_000
    assert all(path not in {".env", "missing.py", "large.py"} for path, _ in context)


def test_repair_draft_keeps_previous_failure_and_failing_test_content(model_api, monkeypatch):
    from app import main

    client, directory = model_api
    task = create_task(client, directory)
    apply_value(client, task, 0)
    failed = post(client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest", "timeout_seconds": 15})
    assert failed["status"] == "failed"
    repair = post(client, f"/api/v1/tasks/{task['id']}/repairs", {"feedback": "Repair the failing behavior"})
    assert repair["test_result"] is None
    monkeypatch.setattr(main.settings, "llm_max_context_files", 2)
    FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])
    post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {})
    goal, context, failure = FakeModelProvider._calls[-1]
    assert task["goal"] in goal
    assert "Repair the failing behavior" in goal
    assert failure and "assert value() == 2" in failure
    assert "return 0" in dict(context)["app.py"]
    assert "assert value() == 2" in dict(context)["test_app.py"]
    events = client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]
    draft = next(event for event in reversed(events) if event["action"] == "patch.draft")
    assert draft["details"]["context_paths"] == [path for path, _ in context]
    assert draft["details"]["context_bytes"] == sum(len(content.encode("utf-8")) for _, content in context)
    assert draft["details"]["duration_ms"] >= 0


def test_model_reply_cannot_overwrite_an_edit_made_while_waiting(model_api, monkeypatch):
    from app import main
    from test_agent_loop import _wait_for_loop

    client, directory = model_api
    task = create_task(client, directory)
    entered, release = Event(), Event()
    external = b"def value():\n    return 99  # user edit\n"

    async def delayed_model(goal, context_files, failure):
        assert "return 1" in dict(context_files)["app.py"]
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        # Even a model-provided hash matching the new file must not replace the original baseline.
        return PatchProposal(files=[PatchFile(path="./app.py", content="def value():\n    return 2\n", expected_hash=hashlib.sha256(external).hexdigest())])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", delayed_model)
    post(client, f"/api/v1/runs/{task['run_id']}/loop", {})
    try:
        assert entered.wait(5)
        (directory / "app.py").write_bytes(external)
    finally:
        release.set()
    final = _wait_for_loop(client, task["id"], timeout=5)
    assert final["task"]["status"] == "coding"
    assert (directory / "app.py").read_bytes() == external
    assert client.get(f"/api/v1/runs/{task['run_id']}/checkpoints").json()["total"] == 0
    events = client.get(f"/api/v1/runs/{task['run_id']}/events").json()["items"]
    assert not any(event["action"] == "patch.apply" for event in events)
    assert events[-1]["status"] == "failed"
    assert "File changed since the patch was prepared" in events[-1]["summary"]


@pytest.mark.parametrize("path", ["./app.py", ".\\app.py", pytest.param("APP.PY", marks=pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive paths"))])
def test_draft_normalizes_paths_and_replaces_the_models_hash(model_api, path):
    client, directory = model_api
    task = create_task(client, directory)
    FakeModelProvider.seed([PatchFile(path=path, content="def value():\n    return 2\n", expected_hash="untrusted-model-hash")])
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {})
    assert draft["files"] == ["app.py"]
    post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {"patch_id": draft["patch_id"], "confirm": True})
    assert (directory / "app.py").read_text(encoding="utf-8") == "def value():\n    return 2\n"


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_draft_rejects_changes_to_existing_files_outside_its_context(model_api, monkeypatch, operation):
    from app import main

    client, directory = model_api
    task = create_task(client, directory)
    original = (directory / "test_app.py").read_bytes()
    monkeypatch.setattr(main.settings, "llm_max_context_files", 1)
    FakeModelProvider.seed([PatchFile(path="test_app.py", operation=operation, content="def test_value():\n    pass\n")])
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={"context_paths": ["app.py"]})
    assert response.status_code == 400
    assert response.json()["code"] == "MODEL_CONTEXT_REQUIRED"
    assert "test_app.py" not in dict(FakeModelProvider._calls[-1][1])
    assert (directory / "test_app.py").read_bytes() == original


def test_draft_can_create_a_file_without_an_existing_context_baseline(model_api):
    client, directory = model_api
    task = create_task(client, directory)
    FakeModelProvider.seed([PatchFile(path="new_file.py", operation="create", content="answer = 42\n", expected_hash="untrusted-model-hash")])
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {})
    post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {"patch_id": draft["patch_id"], "confirm": True})
    assert (directory / "new_file.py").read_text(encoding="utf-8") == "answer = 42\n"
