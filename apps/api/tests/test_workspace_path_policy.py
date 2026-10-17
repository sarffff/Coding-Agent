import ctypes
import os
from pathlib import Path

import pytest

from app.config import Settings
from app.repository_service import RepositoryError, RepositoryService
from app.store import StateStore


windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows 路径形态专项")


def build_service(workspace_root: Path, state_dir: Path) -> RepositoryService:
    settings = Settings(workspace_root=workspace_root, state_dir=state_dir)
    return RepositoryService(settings, StateStore(state_dir / "state.db"))


def short_path(value: Path) -> str:
    """The 8.3 form when the volume generates it, otherwise the long path unchanged."""
    buffer = ctypes.create_unicode_buffer(260)
    resolved = str(value.resolve())
    if ctypes.windll.kernel32.GetShortPathNameW(resolved, buffer, 260) == 0:
        return resolved
    return buffer.value or resolved


def test_filesystem_root_is_not_registerable(tmp_path):
    root = Path(os.path.abspath(os.sep)).anchor
    service = build_service(Path(root), tmp_path / "state")
    with pytest.raises(RepositoryError) as error:
        service.register(root)
    assert error.value.code == "PATH_IS_FILESYSTEM_ROOT"


def test_outside_workspace_reports_the_resolved_path_and_root(tmp_path, demo_repository):
    workspace = demo_repository
    service = build_service(workspace, tmp_path / "state")

    with pytest.raises(RepositoryError) as error:
        service.register(str(tmp_path))
    assert error.value.code == "PATH_OUTSIDE_WORKSPACE"
    assert error.value.details["path"] == str(tmp_path.resolve())
    assert error.value.details["workspace_root"] == str(workspace.resolve())

    # validate() answers with a reason instead of raising, so the UI can pre-check
    rejected = service.validate(str(tmp_path))
    assert rejected.valid is False
    assert rejected.reason
    assert rejected.reason_code == "PATH_OUTSIDE_WORKSPACE"


def test_narrowing_the_workspace_root_locks_out_existing_repositories(tmp_path, demo_repository):
    state_dir = tmp_path / "state"
    broad = build_service(demo_repository.parent, state_dir)
    repository = broad.register(str(demo_repository))
    assert [item.id for item in broad.list()] == [repository.id]
    broad.store.close()

    narrow_root = tmp_path / "narrow"
    narrow_root.mkdir()
    narrow = build_service(narrow_root, state_dir)
    # the registration is still visible, but every use is refused until the root moves back
    assert [item.id for item in narrow.list()] == [repository.id]
    with pytest.raises(RepositoryError) as error:
        narrow.get(repository.id)
    assert error.value.code == "PATH_OUTSIDE_WORKSPACE"
    assert error.value.details["repository_id"] == repository.id
    narrow.store.close()


@windows_only
def test_windows_path_forms_all_resolve_into_the_same_repository(tmp_path, demo_repository):
    service = build_service(demo_repository.parent, tmp_path / "state")
    canonical = str(demo_repository.resolve())

    variants = {
        "正斜杠": canonical.replace("\\", "/"),
        "盘符小写": canonical[0].lower() + canonical[1:],
        "中间段大小写": "".join(ch.upper() if i == 3 else ch for i, ch in enumerate(canonical)),
        "尾部分隔符": canonical + "\\",
        "8.3 短名": short_path(demo_repository),
    }

    resolved = {name: str(service._resolve_allowed_path(value)) for name, value in variants.items()}
    assert set(resolved.values()) == {canonical}, resolved

    repository = service.register(variants["8.3 短名"])
    assert repository.path == canonical
    assert service.register(canonical).id == repository.id
    service.store.close()
