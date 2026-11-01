from __future__ import annotations

from dataclasses import dataclass

from .agent_service import AgentService
from .approval_service import ApprovalService
from .audit_service import AuditService
from .config import Settings, get_settings
from .config_service import ConfigService, MODEL_FIELDS, apply_overrides, apply_saved_model
from .git_service import GitService
from .idempotency import IdempotencyService
from .model_provider import build_model_provider
from .patch_service import PatchService
from .remote_service import RemoteService
from .repository_service import RepositoryService
from .store import StateStore
from .task_service import TaskService
from .test_service import TestService
from .workspace_service import WorkspaceService


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
    remote_service: RemoteService
    idempotency_service: IdempotencyService
    agent_service: AgentService
    config_service: ConfigService
    workspace_service: WorkspaceService


def build_services(settings: Settings | None = None) -> Services:
    """Wire the service graph over one shared state store.

    Calling this again against the same `state_dir` is how a restart is
    represented: records are reloaded and in-flight tasks come back marked as
    requiring explicit recovery.
    """

    settings = settings or get_settings()
    apply_overrides(settings, apply_profiles=False)
    model_defaults = {name: getattr(settings, name) for name in MODEL_FIELDS}
    apply_saved_model(settings)
    store = StateStore(settings.state_dir / "state.db")
    repository_service = RepositoryService(settings, store)
    workspace_service = WorkspaceService(repository_service, settings, store)
    task_service = TaskService(repository_service, store)
    # Complete a task creation interrupted after its workspace record was saved.
    # Other mismatches remain blocked by AgentService; never fall back to writing the source.
    for task in task_service.list():
        workspace = workspace_service._records.get(task.id)
        if (workspace and task.repository_id == workspace.source_repository_id
                and task.source_repository_id == workspace.source_repository_id
                and task.status == "awaiting_approval" and not task_service.list_iterations(task.id)):
            task.repository_id = workspace.repository_id
            task.workspace_path = str(workspace.path)
            task.workspace_kind = workspace.kind
            task.task_branch = workspace.branch
            task_service._persist(task_service._record(task.id))
    audit_service = AuditService(store, retention_days=settings.audit_retention_days, max_events=settings.audit_max_events)
    git_service = GitService(repository_service, settings)
    approval_service = ApprovalService(store)
    test_service = TestService(repository_service, settings, workspace_service.source_repository_id_for)
    remote_service = RemoteService(
        repository_service, task_service, git_service, approval_service, audit_service, settings, store, test_service
    )
    patch_service = PatchService(repository_service, settings, store)
    model_provider = build_model_provider(settings)
    agent_service = AgentService(
        task_service, approval_service, test_service, git_service, audit_service, remote_service, patch_service, model_provider, workspace_service
    )
    config_service = ConfigService(settings, model_defaults=model_defaults,
                                   on_provider_change=agent_service.set_model_provider,
                                   before_model_change=agent_service.assert_model_configuration_idle)
    return Services(
        settings=settings,
        store=store,
        repository_service=repository_service,
        task_service=task_service,
        patch_service=patch_service,
        test_service=test_service,
        audit_service=audit_service,
        git_service=git_service,
        approval_service=approval_service,
        remote_service=remote_service,
        idempotency_service=IdempotencyService(store, settings.idempotency_ttl_hours),
        agent_service=agent_service,
        config_service=config_service,
        workspace_service=workspace_service,
    )
