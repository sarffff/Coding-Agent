from __future__ import annotations

from dataclasses import dataclass

from .approval_service import ApprovalService
from .audit_service import AuditService
from .config import Settings, get_settings
from .git_service import GitService
from .patch_service import PatchService
from .repository_service import RepositoryService
from .store import StateStore
from .task_service import TaskService
from .test_service import TestService


@dataclass(slots=True)
class Services:
    settings: Settings
    store: StateStore
    repository_service: RepositoryService
    task_service: TaskService
    patch_service: PatchService
    test_service: TestService
    audit_service: AuditService
    git_service: GitService
    approval_service: ApprovalService


def build_services(settings: Settings | None = None) -> Services:
    """Wire the service graph over one shared state store.

    Calling this again against the same `state_dir` is how a restart is
    represented: records are reloaded and in-flight tasks come back marked as
    requiring explicit recovery.
    """

    settings = settings or get_settings()
    store = StateStore(settings.state_dir / "state.db")
    repository_service = RepositoryService(settings, store)
    task_service = TaskService(repository_service, store)
    return Services(
        settings=settings,
        store=store,
        repository_service=repository_service,
        task_service=task_service,
        patch_service=PatchService(repository_service, settings, store),
        test_service=TestService(repository_service, settings),
        audit_service=AuditService(store),
        git_service=GitService(repository_service, settings),
        approval_service=ApprovalService(store),
    )
