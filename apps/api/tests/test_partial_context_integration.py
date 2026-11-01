import pytest

from app import main
from app.model_provider import ContextRequest, PatchProposal, _proposal_from_payload
from test_api_workflow import create_task, post
from test_model_draft import model_api


def test_large_file_range_draft_and_apply_preserve_unread_bytes(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    prefix = "# unseen prefix\r\n" * 100
    suffix = "# unseen suffix\r\n" * 100
    original = prefix + "def value():\r\n    return 1\r\n" + suffix
    (directory / "app.py").write_bytes(original.encode("utf-8"))
    monkeypatch.setattr(main.settings, "llm_max_context_bytes", 512)
    calls = []

    async def model(goal, context, failure):
        calls.append(context)
        assert sum(len(content.encode("utf-8")) for _, content in context) <= 512
        if len(calls) == 1:
            assert "app.py" not in dict(context)
            return PatchProposal(context_requests=[ContextRequest(action="read", path="app.py", start_line=101, end_line=102)])
        assert dict(context)["app.py"] == "def value():\r\n    return 1\r\n"
        assert context.partial_files["app.py"].source_content == original
        return _proposal_from_payload({"files": [{"path": "app.py", "edits": [{"old_text": "return 1", "new_text": "return 2"}]}]}, context)

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": ["app.py"]})
    assert len(calls) == 2 and "+    return 2" in draft["diff"]
    assert (directory / "app.py").read_bytes() == original.encode("utf-8")
    applied = post(client, f"/api/v1/runs/{task['run_id']}/patches/apply", {"patch_id": draft["patch_id"], "confirm": True})
    assert applied["applied_files"] == ["app.py"]
    assert (directory / "app.py").read_bytes() == (prefix + "def value():\r\n    return 2\r\n" + suffix).encode("utf-8")


@pytest.mark.parametrize("when", ["during_model", "after_preview"])
def test_range_draft_rejects_external_changes_in_unread_bytes(model_api, monkeypatch, when):
    client, directory = model_api
    task = create_task(client, directory)
    original = "# unseen\n" * 100 + "def value():\n    return 1\n"
    changed = original.replace("# unseen", "# externally changed", 1)
    (directory / "app.py").write_bytes(original.encode("utf-8"))
    monkeypatch.setattr(main.settings, "llm_max_context_bytes", 512)
    calls = []

    async def model(goal, context, failure):
        calls.append(context)
        if len(calls) == 1:
            return PatchProposal(context_requests=[ContextRequest(action="read", path="app.py", start_line=101, end_line=102)])
        proposal = _proposal_from_payload({"files": [{"path": "app.py", "edits": [{"old_text": "return 1", "new_text": "return 2"}]}]}, context)
        if when == "during_model":
            (directory / "app.py").write_bytes(changed.encode("utf-8"))
        return proposal

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={"context_paths": ["app.py"]})
    if when == "after_preview":
        assert response.status_code == 200, response.text
        (directory / "app.py").write_bytes(changed.encode("utf-8"))
        response = client.post(f"/api/v1/runs/{task['run_id']}/patches/apply", json={"patch_id": response.json()["patch_id"], "confirm": True})
    assert response.status_code == 400 and response.json()["code"] == "PATCH_CONFLICT", response.text
    assert (directory / "app.py").read_bytes() == changed.encode("utf-8")


def test_nested_instructions_are_loaded_for_the_target_and_retained_after_read(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    (directory / "src/domain").mkdir(parents=True)
    (directory / "src/sibling").mkdir()
    sources = {
        "AGENTS.md": "Root requirements.\n", "src/AGENTS.md": "Source requirements.\n",
        "src/domain/AGENTS.md": "Domain requirements.\n", "src/sibling/AGENTS.md": "Sibling-only requirements.\n",
        "src/domain/target.py": "value = 1\n", "src/domain/extra.py": "other = 1\n",
    }
    for path, content in sources.items():
        (directory / path).write_text(content, encoding="utf-8", newline="")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 4)
    calls = []

    async def model(goal, context, failure):
        calls.append(context)
        assert set(dict(context)) >= {"AGENTS.md", "src/AGENTS.md", "src/domain/AGENTS.md"}
        assert "src/sibling/AGENTS.md" not in dict(context)
        if len(calls) == 1:
            return PatchProposal(context_requests=[ContextRequest(action="read", path="src/domain/extra.py")])
        assert "src/domain/extra.py" in dict(context) and "src/domain/target.py" not in dict(context)
        return _proposal_from_payload({"files": [{"path": "src/domain/extra.py", "edits": [{"old_text": "other = 1", "new_text": "other = 2"}]}]}, context)

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": ["src/domain/target.py"]})
    assert len(calls) == 2 and draft["files"] == ["src/domain/extra.py"]


@pytest.mark.parametrize("has_javascript", [False, True])
def test_esm_import_resolves_ts_only_when_the_javascript_file_is_missing(model_api, monkeypatch, has_javascript):
    client, directory = model_api
    task = create_task(client, directory)
    (directory / "src").mkdir()
    (directory / "src/handler.ts").write_text("import { charge } from './policy.js';\n", encoding="utf-8")
    (directory / "src/policy.ts").write_text("export const charge = () => 1;\n", encoding="utf-8")
    if has_javascript:
        (directory / "src/policy.js").write_text("export const charge = () => 2;\n", encoding="utf-8")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 5)
    context = dict(main.agent_service._gather_context(task["repository_id"], ["src/handler.ts"], task["id"], "Repair selected behavior", None))
    if has_javascript:
        assert "src/policy.js" in context and "src/policy.ts" not in context
    else:
        assert "src/policy.ts" in context and "src/policy.js" not in context


def test_nested_instructions_do_not_displace_source_dependency_seeds(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    (directory / "src/domain/deep").mkdir(parents=True)
    for path in ["AGENTS.md", "src/AGENTS.md", "src/domain/AGENTS.md", "src/domain/deep/AGENTS.md"]:
        (directory / path).write_text("Keep the existing public API.\n", encoding="utf-8")
    (directory / "src/domain/deep/handler.ts").write_text("import { charge } from './policy.js';\n", encoding="utf-8")
    (directory / "src/domain/deep/policy.ts").write_text("export const charge = () => 1;\n", encoding="utf-8")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 8)
    context = dict(main.agent_service._gather_context(task["repository_id"], ["src/domain/deep/handler.ts"], task["id"], "Repair selected behavior", None))
    assert "src/domain/deep/policy.ts" in context
    assert set(context) >= {"AGENTS.md", "src/AGENTS.md", "src/domain/AGENTS.md", "src/domain/deep/AGENTS.md"}


def test_no_changes_draft_has_no_patch_and_leaves_completion_to_validation(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    original = (directory / "app.py").read_bytes()

    async def model(goal, context, failure):
        return _proposal_from_payload({"no_changes": True, "rationale": "The requested implementation already exists; run the required checks."})

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": ["app.py"]})
    assert draft["no_changes"] and draft["patch_id"] == "" and draft["files"] == [] and draft["diff"] == ""
    assert (directory / "app.py").read_bytes() == original
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["status"] != "done"
