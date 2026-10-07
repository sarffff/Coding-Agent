import subprocess
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def git_output(directory: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=directory, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def build_settings(demo_repository: Path, tmp_path: Path, **overrides):
    from app.config import Settings

    return Settings(
        workspace_root=demo_repository.parent,
        state_dir=tmp_path / "state",
        command_timeout_seconds=15,
        **overrides,
    )


def mount_services(monkeypatch, settings) -> object:
    """Point the app's module-level service aliases at a freshly built graph."""
    from app import main
    from app.services import build_services

    services = build_services(settings)
    for name in (
        "settings",
        "repository_service",
        "task_service",
        "patch_service",
        "test_service",
        "audit_service",
        "git_service",
        "approval_service",
        "remote_service",
        "idempotency_service",
    ):
        monkeypatch.setattr(main, name, getattr(services, name))
    return services


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
def git_services(demo_repository, tmp_path):
    from app.config import Settings
    from app.repository_service import RepositoryService
    from app.git_service import GitService
    settings = Settings(workspace_root=demo_repository.parent, state_dir=tmp_path / "state")
    repositories = RepositoryService(settings)
    repository = repositories.register(str(demo_repository))
    return repositories, GitService(repositories, settings), repository.id


@pytest.fixture
def api_client(demo_repository, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.config import Settings

    services = mount_services(monkeypatch, build_settings(demo_repository, tmp_path))
    with TestClient(main.app) as client:
        yield client
    services.store.close()


@pytest.fixture
def api_stack(demo_repository, tmp_path, monkeypatch):
    """Rebuilds the whole service graph over the same state directory.

    Each rebuild is a process restart for the purposes of these tests: state
    must come back from disk and in-flight tasks must ask for confirmation.
    """

    from fastapi.testclient import TestClient
    from app import main

    class RestartableApi:
        services: object = None

        def restart(self):
            if self.services is not None:
                self.services.store.close()
            self.services = mount_services(monkeypatch, build_settings(demo_repository, tmp_path))
            return TestClient(main.app)

        @property
        def task_service(self):
            return self.services.task_service

    return RestartableApi()
