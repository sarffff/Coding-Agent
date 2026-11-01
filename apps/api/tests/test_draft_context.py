import pytest

from app import main
from app.model_provider import ContextRequest, FakeModelProvider, PatchProposal
from app.models import CompletionCriterion, PatchFile, TestRunResponse as ValidationResult
from test_api_workflow import apply_value, create_task, post
from test_model_draft import model_api


@pytest.fixture(autouse=True)
def clean_fake_model():
    FakeModelProvider.reset()
    yield
    FakeModelProvider.reset()


@pytest.mark.parametrize("repair", [False, True])
def test_draft_feedback_keeps_original_and_iteration_goals(model_api, repair):
    client, directory = model_api
    task = create_task(client, directory)
    iteration_goal = task["goal"]
    if repair:
        apply_value(client, task, 0)
        failed = post(client, f"/api/v1/runs/{task['run_id']}/tests", {"kind": "pytest", "timeout_seconds": 15})
        assert failed["status"] == "failed"
        iteration = post(client, f"/api/v1/tasks/{task['id']}/repairs", {"feedback": "Restore the failing behavior without changing callers"})
        iteration_goal = iteration["goal"]
    feedback = "Keep the change surgical and preserve the public API"
    FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])
    post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"feedback": feedback})
    goal, _, failure = FakeModelProvider._calls[-1]
    assert task["goal"] in goal
    assert iteration_goal in goal
    assert feedback in goal
    if repair:
        assert failure and "assert value() == 2" in failure


def test_root_instructions_survive_context_requests_and_draft_audits_final_context(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    instructions = "只改任务涉及的文件，保留已有测试。\n"
    extra_content = "# 中文上下文\nVALUE = 1\n"
    (directory / "AGENTS.md").write_text(instructions, encoding="utf-8", newline="")
    (directory / "extra.py").write_text(extra_content, encoding="utf-8", newline="")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 2)
    monkeypatch.setattr(main.settings, "llm_max_context_bytes", 1_000)
    calls = []

    async def model(goal, context, failure):
        calls.append(list(context))
        assert dict(context)["AGENTS.md"] == instructions
        assert len(context) <= 2
        assert sum(len(content.encode("utf-8")) for _, content in context) <= 1_000
        if len(calls) == 1:
            assert "extra.py" not in dict(context)
            return PatchProposal(context_requests=[ContextRequest(action="read", path="extra.py")])
        assert dict(context)["extra.py"] == extra_content
        return PatchProposal(files=[PatchFile(path="extra.py", content=extra_content.replace("VALUE = 1", "VALUE = 2"))], rationale="Update the requested value")

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    draft = post(client, f"/api/v1/runs/{task['run_id']}/patches/draft", {"context_paths": ["app.py"]})
    assert len(calls) == 2 and "+VALUE = 2" in draft["diff"]
    assert (directory / "extra.py").read_text(encoding="utf-8") == extra_content
    event = next(event for event in main.audit_service.list_for_run(task["run_id"]) if event.action == "patch.draft")
    assert event.details["context_paths"] == [path for path, _ in calls[-1]]
    assert event.details["context_bytes"] == sum(len(content.encode("utf-8")) for _, content in calls[-1])
    assert isinstance(event.details["duration_ms"], int) and event.details["duration_ms"] >= 0
    assert event.details["model"] == main.settings.llm_model
    assert event.details["rationale"] == "Update the requested value"
    assert extra_content not in str(event.details)


@pytest.mark.parametrize("unreadable", ["binary", "file_limit", "context_budget"])
def test_root_instructions_use_existing_safe_read_and_context_limits(model_api, monkeypatch, unreadable):
    client, directory = model_api
    task = create_task(client, directory)
    if unreadable == "binary":
        (directory / "AGENTS.md").write_bytes(b"private\x00binary")
    else:
        (directory / "AGENTS.md").write_text("instruction " * 150, encoding="utf-8")
        if unreadable == "file_limit":
            monkeypatch.setattr(main.settings, "max_file_size_bytes", 128)
        else:
            monkeypatch.setattr(main.settings, "llm_max_context_bytes", 1_000)
    FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])
    original = (directory / "app.py").read_bytes()
    context = main.agent_service._gather_context(task["repository_id"], ["app.py"], task["id"], task["goal"], None)
    response = client.post(f"/api/v1/runs/{task['run_id']}/patches/draft", json={"context_paths": ["app.py"]})
    assert response.status_code == 400 and response.json()["code"] == "MODEL_CONTEXT_LIMIT", response.text
    assert (directory / "app.py").read_bytes() == original
    assert "AGENTS.md" not in dict(context)
    assert dict(context)["app.py"].splitlines()[-1].strip() == "return 1"
    assert len(context) <= main.settings.llm_max_context_files
    assert sum(len(content.encode("utf-8")) for _, content in context) <= main.settings.llm_max_context_bytes


def test_draft_audit_keeps_request_model_when_settings_change_during_the_call(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    requested_model = main.settings.llm_model

    async def model(goal, context, failure):
        monkeypatch.setattr(main.settings, "llm_model", "model-configured-for-the-next-request")
        return PatchProposal(files=[PatchFile(path="app.py", content="def value():\n    return 2\n")])

    monkeypatch.setattr(main.agent_service.models, "propose_patch", model)
    post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
    event = next(event for event in main.audit_service.list_for_run(task["run_id"]) if event.action == "patch.draft")
    assert main.settings.llm_model != requested_model
    assert event.details["model"] == requested_model


def test_explicit_and_traceback_paths_precede_related_files_and_large_repo_fallbacks(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    nested = directory / "src" / "domain"
    nested.mkdir(parents=True)
    sources = {
        "AGENTS.md": "保留验收规则。\n",
        "src/domain/handler.py": "from .policy import charge\n\ndef handle():\n    return charge()\n",
        "src/domain/policy.py": "def charge():\n    return 9\n",
        "src/domain/test_charge.py": "from .policy import charge\n\nassert charge() == 10\n",
    }
    for path, content in sources.items():
        (directory / path).write_text(content, encoding="utf-8", newline="")
    for number in range(60):
        (directory / f"unrelated_{number:03}.py").write_text("def unrelated():\n    return 0\n", encoding="utf-8")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 3)
    monkeypatch.setattr(main.settings, "llm_max_context_bytes", 1_000)
    failure = ValidationResult(run_id=task["run_id"], kind="pytest", command=[], status="failed", duration_ms=1,
                               stdout="", stderr=f'File "{directory / "src/domain/test_charge.py"}", line 3\nAssertionError')
    context = main.agent_service._gather_context(task["repository_id"], ["src/domain/handler.py"], task["id"],
                                                "Repair selected behavior", failure)
    assert [path for path, _ in context] == ["AGENTS.md", "src/domain/handler.py", "src/domain/test_charge.py"]
    assert all(content == sources[path] for path, content in context)
    assert sum(len(content.encode("utf-8")) for _, content in context) <= 1_000


@pytest.mark.parametrize("location", ["request", "goal", "traceback"])
def test_targeted_context_does_not_fill_remaining_budget_with_unrelated_app(model_api, location):
    client, directory = model_api
    task = create_task(client, directory)
    fixture = directory / "acceptance/core-agent-20261024"
    fixture.mkdir(parents=True)
    (fixture / "calculator.py").write_text("def fixture_calculate():\n    return 1\n", encoding="utf-8")
    (fixture / "test_calculator.py").write_text("from calculator import fixture_calculate\n\nassert fixture_calculate() == 2\n", encoding="utf-8")
    (directory / "AGENTS.md").write_text("Preserve the existing tests.\n", encoding="utf-8")
    (directory / "src").mkdir()
    (directory / "src/App.tsx").write_text("export function App() { return 'unrelated application'; }\n" * 100, encoding="utf-8")
    for number in range(60):
        (directory / f"unrelated_{number:03}.py").write_text("UNRELATED = 1\n", encoding="utf-8")
    paths = ["acceptance/core-agent-20261024/calculator.py", "acceptance/core-agent-20261024/test_calculator.py"]
    goal = "Repair selected behavior"
    failure = None
    if location == "goal":
        goal = "Fix " + " so ".join(paths) + " passes"
    elif location == "traceback":
        failure = ValidationResult(run_id=task["run_id"], kind="pytest", command=[], status="failed", duration_ms=1,
                                   stdout="", stderr=f'File "{fixture / "test_calculator.py"}", line 3\nAssertionError')
    context = main.agent_service._gather_context(task["repository_id"], paths if location == "request" else [], task["id"], goal, failure)
    assert set(dict(context)) == {"AGENTS.md", *paths}
    assert sum(len(content.encode("utf-8")) for _, content in context) < 1_000


@pytest.mark.parametrize("unavailable", ["missing", "oversized"])
def test_root_instructions_without_usable_target_keep_source_fallback(model_api, monkeypatch, unavailable):
    client, directory = model_api
    task = create_task(client, directory)
    (directory / "AGENTS.md").write_text("Keep the change surgical.\n", encoding="utf-8")
    if unavailable == "oversized":
        (directory / "target.py").write_text("# large source\n" * 100, encoding="utf-8")
    monkeypatch.setattr(main.settings, "llm_max_context_bytes", 1_000)
    context = main.agent_service._gather_context(task["repository_id"], ["target.py"], task["id"], "Repair selected behavior", None)
    assert "AGENTS.md" in dict(context) and "app.py" in dict(context)
    assert "target.py" not in dict(context)


@pytest.mark.parametrize("import_statement", ["from .policy import charge", "from src.domain.policy import charge", "import src.domain.policy"])
def test_initial_context_adds_direct_python_dependency_before_unrelated_files(model_api, monkeypatch, import_statement):
    client, directory = model_api
    task = create_task(client, directory)
    nested = directory / "src" / "domain"
    nested.mkdir(parents=True)
    (nested / "handler.py").write_text(import_statement + "\n\ndef handle():\n    return 1\n", encoding="utf-8", newline="")
    dependency = "def charge():\n    return 9\n"
    (nested / "policy.py").write_text(dependency, encoding="utf-8", newline="")
    (nested / "aaa.py").write_text("UNRELATED = 1\n", encoding="utf-8")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 2)
    context = main.agent_service._gather_context(task["repository_id"], ["src/domain/handler.py"], task["id"], "Repair selected behavior", None)
    assert list(dict(context)) == ["src/domain/handler.py", "src/domain/policy.py"]
    assert dict(context)["src/domain/policy.py"] == dependency


def test_initial_context_adds_relative_typescript_dependency_safely(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    nested = directory / "src" / "domain"
    nested.mkdir(parents=True)
    (nested / "handler.ts").write_text("import { charge } from '../policy';\nexport function handle() { return charge(); }\n", encoding="utf-8")
    dependency = "export function charge() { return 9; }\n"
    (directory / "src/policy.ts").write_text(dependency, encoding="utf-8", newline="")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 2)
    context = main.agent_service._gather_context(task["repository_id"], ["src/domain/handler.ts"], task["id"], "Repair selected behavior", None)
    assert dict(context).get("src/policy.ts") == dependency


def test_initial_context_limits_caller_expansion_to_three_files(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    nested = directory / "src"
    nested.mkdir()
    (nested / "rules.py").write_text("def charge():\n    return 9\n", encoding="utf-8")
    for number in range(8):
        (nested / f"consumer_{number}.py").write_text(f"def consume_{number}():\n    return charge()\n", encoding="utf-8")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 4)
    context = main.agent_service._gather_context(task["repository_id"], ["src/rules.py"], task["id"], "Repair selected behavior", None)
    assert [path for path, _ in context] == ["src/rules.py", "src/consumer_0.py", "src/consumer_1.py", "src/consumer_2.py"]


def test_initial_keyword_search_uses_goal_without_validation_summaries(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    queries = []
    original = main.repository_service.search

    def search(repository_id, request):
        if not request.extensions:
            queries.append(request.query)
        return original(repository_id, request)

    monkeypatch.setattr(main.repository_service, "search", search)
    FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])
    post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
    assert set(queries) <= {"value", "verify"}


def test_related_imports_preserve_read_safety_and_complete_file_byte_budget(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    nested = directory / "src" / "domain"
    nested.mkdir(parents=True)
    (directory.parent / "outside.ts").write_text("const SECRET = 'outside';\n", encoding="utf-8")
    (directory / "src/.env").write_text("SECRET=protected\n", encoding="utf-8")
    handler = "import '../../../outside';\nimport '../.env';\nimport './oversized';\nimport { charge } from './policy';\n"
    (nested / "handler.ts").write_text(handler, encoding="utf-8", newline="")
    (nested / "oversized.ts").write_text("// " + "说明" * 300, encoding="utf-8")
    dependency = "export function charge() { return 9; }\n"
    (nested / "policy.ts").write_text(dependency, encoding="utf-8", newline="")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 2)
    monkeypatch.setattr(main.settings, "llm_max_context_bytes", 1_000)
    context = main.agent_service._gather_context(task["repository_id"], ["src/domain/handler.ts"], task["id"], "Repair selected behavior", None)
    assert dict(context) == {"src/domain/handler.ts": handler, "src/domain/policy.ts": dependency}
    assert sum(len(content.encode("utf-8")) for _, content in context) <= 1_000


@pytest.mark.parametrize("statement", ["from app import service", "from app.service import charge"])
def test_python_imports_resolve_from_nearby_package_root(model_api, monkeypatch, statement):
    client, directory = model_api
    task = create_task(client, directory)
    (directory / "apps/api/tests").mkdir(parents=True)
    (directory / "apps/api/app").mkdir()
    (directory / "apps/api/tests/target.py").write_text("import asyncio\nimport sys\n" + statement + "\n", encoding="utf-8")
    dependency = "def charge():\n    return 9\n"
    (directory / "apps/api/app/service.py").write_text(dependency, encoding="utf-8", newline="")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 2)
    context = main.agent_service._gather_context(task["repository_id"], ["apps/api/tests/target.py"], task["id"], "Repair selected behavior", None)
    assert dict(context).get("apps/api/app/service.py") == dependency


def test_quoted_traceback_path_with_spaces_is_read_as_one_failure_location(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    (directory / "src/my domain").mkdir(parents=True)
    source = "def broken():\n    return 1\n"
    path = "src/my domain/handler.py"
    (directory / path).write_text(source, encoding="utf-8", newline="")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 1)
    failure = ValidationResult(run_id=task["run_id"], kind="pytest", command=[], status="failed", duration_ms=1,
                               stdout="", stderr=f'File "{directory / path}", line 2\nAssertionError')
    context = main.agent_service._gather_context(task["repository_id"], [], task["id"], "Repair selected behavior", failure)
    assert context == [(path, source)]


def test_generated_tmp_sources_cannot_replace_real_initial_context(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    generated = directory / ".tmp" / "generated-demo"
    generated.mkdir(parents=True)
    for name in ("app.py", "main.py", "test_app.py"):
        (generated / name).write_text("def selected_behavior():\n    return 'generated-evidence'\n", encoding="utf-8")
    monkeypatch.setattr(main.settings, "llm_max_context_files", 1)
    context = main.agent_service._gather_context(task["repository_id"], [], task["id"], "Repair selected behavior", None)
    assert [path for path, _ in context] == ["app.py"]
    assert "generated-evidence" not in str(context)


def test_completion_criteria_reach_model_without_polluting_initial_search(model_api, monkeypatch):
    client, directory = model_api
    task = create_task(client, directory)
    criterion = CompletionCriterion(id="behavior", text="criterionmarker: value returns 2 and keeps the public API")
    main.task_service.get(task["id"]).completion_criteria = [criterion]
    queries = []
    original = main.repository_service.search

    def search(repository_id, request):
        queries.append(request.query)
        return original(repository_id, request)

    monkeypatch.setattr(main.repository_service, "search", search)
    FakeModelProvider.seed([PatchFile(path="app.py", content="def value():\n    return 2\n")])
    post(client, f"/api/v1/runs/{task['run_id']}/patches/draft")
    goal = FakeModelProvider._calls[-1][0]
    assert task["goal"] in goal and "# Completion criteria" in goal
    assert f"{criterion.id}: {criterion.text}" in goal
    assert "Do not weaken verification" in goal
    assert all("criterionmarker" not in query and "completion" not in query for query in queries)
