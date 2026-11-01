import os
import subprocess

import pytest

from app.config import Settings
from app.patch_service import PatchService
from app.repository_service import RepositoryError, RepositoryService
from app.store import StateStore
from app.workspace_service import WorkspaceService
from conftest import git_output


def services(source, tmp_path, store=None, **overrides):
    settings = Settings(_env_file=None, workspace_root=tmp_path, state_dir=tmp_path / "state", **overrides)
    repositories = RepositoryService(settings, store)
    registered = repositories.register(str(source))
    return WorkspaceService(repositories, settings, store), repositories, registered.id


def test_git_tasks_preserve_dirty_source_and_index_and_do_not_share_files(demo_repository, tmp_path):
    workspace, repositories, source_id = services(demo_repository, tmp_path)
    (demo_repository / "app.py").write_text("VALUE = 'staged'\n", encoding="utf-8")
    git_output(demo_repository, "add", "app.py")
    (demo_repository / "app.py").write_text("VALUE = 'current'\n", encoding="utf-8")
    (demo_repository / "new.py").write_text("NEW = True\n", encoding="utf-8")
    (demo_repository / "notes.txt").unlink()
    original_index = (demo_repository / ".git" / "index").read_bytes()
    original_head = git_output(demo_repository, "rev-parse", "HEAD")

    first = workspace.create("task-first", source_id)
    second = workspace.create("task-second", source_id)
    assert first.repository_id != second.repository_id != source_id
    assert first.path != second.path != demo_repository
    assert first.branch.startswith("codex/task-")
    assert git_output(first.path, "branch", "--show-current").strip() == first.branch
    assert (first.path / "app.py").read_bytes() == (demo_repository / "app.py").read_bytes()
    assert (first.path / "new.py").read_bytes() == (demo_repository / "new.py").read_bytes()
    assert not (first.path / "notes.txt").exists()
    (first.path / "app.py").write_text("VALUE = 'first task'\n", encoding="utf-8")
    git_output(first.path, "add", "app.py")
    assert (second.path / "app.py").read_text(encoding="utf-8") == "VALUE = 'current'\n"
    assert (demo_repository / ".git" / "index").read_bytes() == original_index
    assert git_output(demo_repository, "branch", "--show-current").strip() == "main"
    assert git_output(demo_repository, "rev-parse", "HEAD") == original_head
    assert repositories.get(first.repository_id).has_git


@pytest.mark.parametrize("has_git", [False, True])
def test_workspace_persists_and_creation_is_idempotent(demo_repository, tmp_path, has_git):
    source = demo_repository if has_git else tmp_path / "plain"
    if not has_git:
        source.mkdir()
        (source / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    store = StateStore(tmp_path / "state" / "state.sqlite3")
    workspace, repositories, source_id = services(source, tmp_path, store)
    original = workspace.create("persisted-task", source_id)
    (original.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    store.close()
    store = StateStore(tmp_path / "state" / "state.sqlite3")
    try:
        settings = Settings(_env_file=None, workspace_root=tmp_path, state_dir=tmp_path / "state")
        repositories = RepositoryService(settings, store)
        restarted = WorkspaceService(repositories, settings, store)
        assert restarted.get("persisted-task") == original
        assert restarted.create("persisted-task", source_id) == original
        assert (original.path / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
        assert restarted.delete("persisted-task")
        assert restarted.get("persisted-task") is None
        assert store.items("workspaces") == []
        assert source.is_dir() and not original.path.exists()
        with pytest.raises(RepositoryError, match="not registered"):
            repositories.get(original.repository_id)
    finally:
        store.close()


def test_workspace_copy_omits_protected_tracked_files_and_generated_outputs(demo_repository, tmp_path):
    protected = demo_repository / "credentials.json"
    protected.write_text('{"test-only": true}', encoding="utf-8")
    git_output(demo_repository, "add", "credentials.json")
    git_output(demo_repository, "commit", "-qm", "Add test credential placeholder")
    (demo_repository / "config.sample").write_text("SAFE = 1\n", encoding="utf-8")
    for name in ("node_modules", ".venv", "__pycache__", "htmlcov"):
        directory = demo_repository / name
        directory.mkdir()
        (directory / "generated.txt").write_text("test-only", encoding="utf-8")
    workspace, _, source_id = services(demo_repository, tmp_path)
    record = workspace.create("protected-task", source_id)
    assert not (record.path / "credentials.json").exists()
    assert (record.path / "config.sample").read_text(encoding="utf-8") == "SAFE = 1\n"
    assert all(not (record.path / name).exists() for name in ("node_modules", ".venv", "__pycache__", "htmlcov"))
    assert protected.read_text(encoding="utf-8") == '{"test-only": true}'


def test_command_diff_restores_bytes_then_uses_patch_checkpoints(tmp_path):
    source = tmp_path / "plain"
    source.mkdir()
    (source / "app.py").write_bytes(b"VALUE = 1\r\n")
    (source / "obsolete.py").write_text("OLD = 1\n", encoding="utf-8")
    workspace, repositories, source_id = services(source, tmp_path)
    record = workspace.create("command-task", source_id)
    before = workspace.snapshot(record.task_id)
    (record.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (record.path / "obsolete.py").unlink()
    (record.path / "added").mkdir()
    (record.path / "added" / "new.py").write_text("NEW = True\n", encoding="utf-8")
    (record.path / ".coverage").write_bytes(b"\x00generated")
    changes = workspace.diff(before)
    assert [(item.path, item.operation) for item in changes] == [("added/new.py", "create"), ("app.py", "update"), ("obsolete.py", "delete")]
    assert workspace.restore(before) == ["added/new.py", "app.py", "obsolete.py"]
    assert (record.path / "app.py").read_bytes() == b"VALUE = 1\r\n"
    assert not (record.path / "added").exists()
    assert (record.path / ".coverage").exists()
    patches = PatchService(repositories, workspace.settings)
    preview = patches.preview(record.repository_id, changes, run_id="command-run")
    assert preview.requires_delete_confirmation
    applied = patches.apply(preview.patch_id, confirm=True)
    assert (record.path / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert applied.checkpoint.run_id == "command-run"
    patches.rollback(applied.checkpoint.id)
    assert (record.path / "app.py").read_bytes() == b"VALUE = 1\r\n"
    assert (source / "app.py").read_bytes() == b"VALUE = 1\r\n"


@pytest.mark.parametrize("content", [b"\x00binary", b"\xffinvalid-utf8", b"x" * 33])
def test_unreviewable_command_changes_can_still_be_restored(tmp_path, content):
    source = tmp_path / "plain"
    source.mkdir()
    (source / "app.py").write_bytes(b"VALUE = 1\n")
    workspace, _, source_id = services(source, tmp_path, max_file_size_bytes=32)
    record = workspace.create("binary-task", source_id)
    before = workspace.snapshot(record.task_id)
    (record.path / "app.py").write_bytes(content)
    with pytest.raises(RepositoryError) as error:
        workspace.diff(before)
    assert error.value.code in {"BINARY_FILE_NOT_ALLOWED", "FILE_TOO_LARGE"}
    workspace.restore(before)
    assert (record.path / "app.py").read_bytes() == b"VALUE = 1\n"


def test_restore_rejects_external_workspace_edits_and_keeps_source_edits(tmp_path):
    source = tmp_path / "plain"
    source.mkdir()
    (source / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    workspace, _, source_id = services(source, tmp_path)
    record = workspace.create("external-task", source_id)
    before = workspace.snapshot(record.task_id)
    (record.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    workspace.diff(before)
    (source / "app.py").write_text("VALUE = 'source user edit'\n", encoding="utf-8")
    (record.path / "app.py").write_text("VALUE = 'workspace user edit'\n", encoding="utf-8")
    with pytest.raises(RepositoryError) as error:
        workspace.restore(before)
    assert error.value.code == "WORKSPACE_RESTORE_CONFLICT"
    assert (record.path / "app.py").read_text(encoding="utf-8") == "VALUE = 'workspace user edit'\n"
    assert workspace.delete(record.task_id)
    assert (source / "app.py").read_text(encoding="utf-8") == "VALUE = 'source user edit'\n"


@pytest.mark.parametrize("tamper", ["path", "marker"])
def test_workspace_delete_rejects_tampered_ownership(tmp_path, tamper):
    source = tmp_path / "plain"
    source.mkdir()
    (source / "keep.py").write_text("KEEP = True\n", encoding="utf-8")
    workspace, _, source_id = services(source, tmp_path)
    record = workspace.create("unsafe-task", source_id)
    original_path = record.path
    if tamper == "path":
        record.path = source
    else:
        (record.path.parent / ".owner.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RepositoryError) as error:
        workspace.delete(record.task_id)
    assert error.value.code in {"WORKSPACE_PATH_UNSAFE", "WORKSPACE_OWNERSHIP_INVALID"}
    assert (source / "keep.py").read_text(encoding="utf-8") == "KEEP = True\n"
    assert original_path.is_dir()


def test_symlinks_are_not_copied_or_followed_during_cleanup(tmp_path):
    source = tmp_path / "plain"
    source.mkdir()
    (source / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.py").write_text("KEEP = True\n", encoding="utf-8")
    try:
        (source / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode or elevated rights")
    workspace, _, source_id = services(source, tmp_path)
    record = workspace.create("links-task", source_id)
    assert not (record.path / "linked").exists()
    (record.path / "new-link").symlink_to(outside, target_is_directory=True)
    assert workspace.delete(record.task_id)
    assert (outside / "keep.py").read_text(encoding="utf-8") == "KEEP = True\n"


def test_scope_limit_refuses_partial_workspace(tmp_path):
    source = tmp_path / "plain"
    source.mkdir()
    for name in ("one.py", "two.py"):
        (source / name).write_text("VALUE = 1\n", encoding="utf-8")
    workspace, _, source_id = services(source, tmp_path, max_tree_entries=1)
    with pytest.raises(RepositoryError) as error:
        workspace.create("large-task", source_id)
    assert error.value.code == "WORKSPACE_SCOPE_TOO_LARGE"
    assert workspace.get("large-task") is None
    assert not workspace.workspace_dir.exists()


@pytest.mark.parametrize("has_git", [False, True])
def test_pending_command_snapshot_recovers_after_restart(demo_repository, tmp_path, has_git):
    source = demo_repository if has_git else tmp_path / "plain"
    if not has_git:
        source.mkdir()
        (source / "app.py").write_bytes(b"VALUE = 1\r\n")
        (source / "notes.txt").write_text("original notes\n", encoding="utf-8")
    store = StateStore(tmp_path / "state" / "state.sqlite3")
    workspace, repositories, source_id = services(source, tmp_path, store)
    record = workspace.create("restart-command", source_id)
    (record.path / "app.py").write_bytes(b"VALUE = 1\r\n")
    before = workspace.snapshot(record.task_id)
    (record.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (record.path / "notes.txt").unlink()
    (record.path / "new.py").write_text("NEW = True\n", encoding="utf-8")
    store.close()
    store = StateStore(tmp_path / "state" / "state.sqlite3")
    try:
        repositories = RepositoryService(workspace.settings, store)
        restarted = WorkspaceService(repositories, workspace.settings, store)
        pending = restarted.pending_snapshot(record.task_id)
        assert pending.snapshot_id == before.snapshot_id
        assert restarted.source_repository_id_for(record.repository_id) == source_id
        assert restarted.source_repository_id_for(source_id) is None
        with pytest.raises(RepositoryError) as error:
            restarted.snapshot(record.task_id)
        assert error.value.code == "COMMAND_SNAPSHOT_PENDING"
        changes = restarted.diff(pending)
        assert {item.path for item in changes} == {"app.py", "notes.txt", "new.py"}
        restarted.restore(pending)
        assert (record.path / "app.py").read_bytes() == b"VALUE = 1\r\n"
        assert (record.path / "notes.txt").read_text(encoding="utf-8") == "original notes\n"
        assert not (record.path / "new.py").exists()
        assert restarted.pending_snapshot(record.task_id) is None
        assert not (record.path.parent / "command-snapshot").exists()
        assert store.items("command_snapshots") == []
        patches = PatchService(repositories, workspace.settings, store)
        preview = patches.preview(record.repository_id, changes, run_id="restart-run")
        patches.apply(preview.patch_id, confirm=True)
        assert (record.path / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    finally:
        store.close()


@pytest.mark.parametrize("damage", ["file-missing", "file-corrupt", "manifest", "directory"])
def test_missing_or_corrupt_command_backups_never_partially_restore(tmp_path, damage):
    source = tmp_path / "plain"
    source.mkdir()
    for name in ("first.py", "second.py"):
        (source / name).write_bytes(b"VALUE = 1\r\n")
    store = StateStore(tmp_path / "state" / "state.sqlite3")
    try:
        workspace, repositories, source_id = services(source, tmp_path, store)
        record = workspace.create("damaged-command", source_id)
        before = workspace.snapshot(record.task_id)
        for name in ("first.py", "second.py"):
            (record.path / name).write_bytes(b"VALUE = 2\n")
        backup_dir = record.path.parent / "command-snapshot"
        if damage == "file-missing":
            next((backup_dir / "files").iterdir()).unlink()
        elif damage == "file-corrupt":
            next((backup_dir / "files").iterdir()).write_bytes(b"damaged")
        elif damage == "manifest":
            (backup_dir / "manifest.json").write_text("{}", encoding="utf-8")
        else:
            backup_dir.rename(record.path.parent / "saved-command-backup")
        restarted = WorkspaceService(repositories, workspace.settings, store)
        with pytest.raises(RepositoryError) as error:
            restarted.pending_snapshot(record.task_id)
        assert error.value.code == "COMMAND_SNAPSHOT_UNAVAILABLE"
        with pytest.raises(RepositoryError) as error:
            workspace.restore(before)
        assert error.value.code == "COMMAND_SNAPSHOT_UNAVAILABLE"
        assert all((record.path / name).read_bytes() == b"VALUE = 2\n" for name in ("first.py", "second.py"))
    finally:
        store.close()


@pytest.mark.parametrize("operation", ["create", "delete"])
def test_empty_git_return_cannot_report_workspace_success(demo_repository, tmp_path, monkeypatch, operation):
    workspace, repositories, source_id = services(demo_repository, tmp_path)
    record = workspace.create("noop-git", source_id) if operation == "delete" else None
    original = repositories._run_git

    def no_op(path, *args):
        if args[:2] == ("worktree", "add" if operation == "create" else "remove"):
            return ""
        return original(path, *args)

    monkeypatch.setattr(repositories, "_run_git", no_op)
    with pytest.raises(RepositoryError) as error:
        workspace.create("noop-git", source_id) if operation == "create" else workspace.delete(record.task_id)
    assert error.value.code == ("WORKSPACE_CREATE_FAILED" if operation == "create" else "WORKSPACE_CLEANUP_FAILED")
    assert demo_repository.is_dir()
    if record:
        assert record.path.is_dir()
        assert workspace.get(record.task_id) == record


@pytest.mark.skipif(os.name != "nt", reason="Windows junction policy")
def test_windows_junction_copy_and_cleanup_do_not_touch_target(tmp_path):
    source, outside = tmp_path / "plain", tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    (source / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (outside / "keep.py").write_text("KEEP = True\n", encoding="utf-8")
    result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(source / "linked"), str(outside)], capture_output=True)
    if result.returncode:
        pytest.skip("Creating a Windows junction is unavailable")
    workspace, _, source_id = services(source, tmp_path)
    record = workspace.create("junction-task", source_id)
    assert not (record.path / "linked").exists()
    result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(record.path / "generated-link"), str(outside)], capture_output=True)
    assert result.returncode == 0
    assert workspace.delete(record.task_id)
    assert (outside / "keep.py").read_text(encoding="utf-8") == "KEEP = True\n"


@pytest.mark.parametrize("cleanup", ["apply", "delete"])
def test_command_draft_survives_restart_and_is_cleared_after_review(tmp_path, cleanup):
    source = tmp_path / "plain"
    source.mkdir()
    (source / "app.py").write_bytes(b"VALUE = 1\r\n")
    store = StateStore(tmp_path / "state" / "state.sqlite3")
    workspace, repositories, source_id = services(source, tmp_path, store)
    record = workspace.create("draft-task", source_id)
    before = workspace.snapshot(record.task_id)
    (record.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    changes = workspace.diff(before)
    workspace.save_command_draft(record.task_id, changes)
    workspace.restore(before)
    store.close()
    store = StateStore(tmp_path / "state" / "state.sqlite3")
    try:
        repositories = RepositoryService(workspace.settings, store)
        restarted = WorkspaceService(repositories, workspace.settings, store)
        assert restarted.command_draft(record.task_id) == changes
        assert restarted.pending_snapshot(record.task_id) is None
        if cleanup == "apply":
            patches = PatchService(repositories, workspace.settings, store)
            preview = patches.preview(record.repository_id, restarted.command_draft(record.task_id), run_id="draft-run")
            patches.apply(preview.patch_id)
            restarted.clear_command_draft(record.task_id)
            assert (record.path / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
        else:
            restarted.delete(record.task_id)
        assert restarted.command_draft(record.task_id) == []
        assert store.items("command_drafts") == []
        assert (source / "app.py").read_bytes() == b"VALUE = 1\r\n"
    finally:
        store.close()
