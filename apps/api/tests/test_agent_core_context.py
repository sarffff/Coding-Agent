import pytest

from app import main
from app.model_provider import PatchProposal
from app.models import PatchFile
from test_api_workflow import create_task, post
from test_model_draft import model_api


@pytest.fixture
def captured_drafts(model_api, monkeypatch):
    calls = []

    async def model(goal, context, failure):
        calls.append((goal, dict(context)))
        return PatchProposal(files=[PatchFile(path="new.py", operation="create", content="value = 2\n")])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    return calls


@pytest.mark.parametrize("unavailable,path", [
    ("oversized", "target.py"), ("missing", "missing.py"), ("protected", ".env"),
])
def test_unavailable_user_selection_is_preserved_without_supplying_its_content(model_api, monkeypatch, captured_drafts, unavailable, path):
    client, directory = model_api
    task = create_task(client, directory)
    source = "SELECTED_PRIVATE_MARKER = 1\n" + "# selected content\n" * 100
    if unavailable != "missing":
        (directory / path).write_text(source, encoding="utf-8", newline="")
    monkeypatch.setattr(main.settings, "llm_max_context_bytes", 1_000)

    post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": [path]})
    goal, context = captured_drafts[-1]
    assert path not in task["goal"]
    assert "# Selected context paths" in goal
    assert f"{path}: not supplied as complete file content" in goal
    assert path not in context
    assert "SELECTED_PRIVATE_MARKER" not in goal + str(context)


def test_relative_selection_alias_is_recognized_as_complete_context(model_api, captured_drafts):
    client, directory = model_api
    task = create_task(client, directory)
    source = "SELECTED_VALUE = 1\n"
    (directory / "target.py").write_text(source, encoding="utf-8", newline="")

    post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": ["./target.py"]})
    goal, context = captured_drafts[-1]
    assert "# Selected context paths" in goal
    assert "target.py" in goal.split("# Selected context paths", 1)[1]
    assert "target.py: not supplied as complete file content" not in goal
    assert context["target.py"] == source
