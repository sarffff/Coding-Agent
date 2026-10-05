import subprocess
from pathlib import Path

import pytest

from app.config import Settings
from app.models import PatchFile, SearchRequest
from app.patch_service import PatchService
from app.repository_service import RepositoryError, RepositoryService


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, RepositoryService]:
    repo_path = tmp_path / "demo"
    repo_path.mkdir()
    git(repo_path, "init", "-q")
    git(repo_path, "config", "user.email", "test@example.com")
    git(repo_path, "config", "user.name", "Phase 1 Test")
    (repo_path / "app.py").write_text("def hello():\n    return 'forge'\n", encoding="utf-8")
    (repo_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    git(repo_path, "add", ".")
    git(repo_path, "commit", "-m", "initial", "-q")
    return repo_path, RepositoryService(Settings(workspace_root=tmp_path))


def test_register_summary_tree_and_search(repository: tuple[Path, RepositoryService]) -> None:
    repo_path, service = repository
    summary = service.register(str(repo_path))

    assert summary.name == "demo"
    assert summary.branch in {"master", "main"}
    assert summary.changed_files == 0

    entries, truncated = service.tree(summary.id)
    assert truncated is False
    assert {entry.path for entry in entries} >= {"app.py", "README.md"}

    matches, truncated = service.search(summary.id, SearchRequest(query="forge", extensions=["py"]))
    assert truncated is False
    assert matches[0].path == "app.py"
    assert matches[0].line == 2


def test_rejects_path_outside_workspace(repository: tuple[Path, RepositoryService], tmp_path: Path) -> None:
    _, service = repository
    outside = tmp_path.parent / "outside"
    outside.mkdir()
    with pytest.raises(RepositoryError) as error:
        service.register(str(outside))
    assert error.value.code == "PATH_OUTSIDE_WORKSPACE"


def test_patch_apply_and_rollback(repository: tuple[Path, RepositoryService]) -> None:
    repo_path, service = repository
    summary = service.register(str(repo_path))
    patches = PatchService(service, Settings(workspace_root=repo_path.parent))
    original = (repo_path / "app.py").read_text(encoding="utf-8")

    preview = patches.preview(summary.id, [PatchFile(path="app.py", content="def hello():\n    return 'updated'\n")])
    applied = patches.apply(preview.patch_id)
    assert "updated" in (repo_path / "app.py").read_text(encoding="utf-8")

    patches.rollback(applied.checkpoint.id)
    assert (repo_path / "app.py").read_text(encoding="utf-8") == original
