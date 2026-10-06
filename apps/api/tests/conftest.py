import subprocess
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def git_output(directory: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=directory, check=True, capture_output=True, text=True, encoding="utf-8").stdout


@pytest.fixture
def demo_repository(tmp_path):
    directory = tmp_path / "demo"
    directory.mkdir()
    git_output(directory, "init", "-q", "-b", "main")
    git_output(directory, "config", "user.name", "Forge Integration Test")
    git_output(directory, "config", "user.email", "forge-test@example.invalid")
    git_output(directory, "config", "core.autocrlf", "false")
    (directory / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (directory / "test_app.py").write_text("from app import value\n\ndef test_value():\n    assert value() == 2\n", encoding="utf-8")
    (directory / "notes.txt").write_text("original notes\n", encoding="utf-8")
    (directory / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    git_output(directory, "add", ".")
    git_output(directory, "commit", "-qm", "Initial demo")
    return directory


@pytest.fixture
def git_services(demo_repository):
    from app.config import Settings
    from app.repository_service import RepositoryService
    from app.git_service import GitService
    settings = Settings(workspace_root=demo_repository.parent)
    repositories = RepositoryService(settings)
    repository = repositories.register(str(demo_repository))
    return repositories, GitService(repositories, settings), repository.id


@pytest.fixture
def api_client(demo_repository, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.approval_service import ApprovalService
    from app.audit_service import AuditService
    from app.config import Settings
    from app.git_service import GitService
    from app.patch_service import PatchService
    from app.repository_service import RepositoryService
    from app.task_service import TaskService
    from app.test_service import TestService
    settings = Settings(workspace_root=demo_repository.parent, command_timeout_seconds=15)
    repositories = RepositoryService(settings)
    services = {
        "settings": settings, "repository_service": repositories,
        "task_service": TaskService(repositories), "patch_service": PatchService(repositories, settings),
        "test_service": TestService(repositories, settings), "audit_service": AuditService(),
        "git_service": GitService(repositories, settings), "approval_service": ApprovalService(),
    }
    for name, value in services.items():
        monkeypatch.setattr(main, name, value)
    with TestClient(main.app) as client:
        yield client
