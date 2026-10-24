from pathlib import Path

import pytest

from app.repository_service import RepositoryError
from conftest import git_output


def test_local_validation_tracks_content_and_deletion(demo_repository, git_services):
    repositories, service, _ = git_services
    folder = demo_repository.parent / "local"
    folder.mkdir()
    source = folder / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    opened = repositories.register(str(folder))
    original = service.validation_hash(opened.id, ["app.py"])
    source.write_text("value = 2\n", encoding="utf-8")
    changed = service.validation_hash(opened.id, ["app.py"])
    source.unlink()
    deleted = service.validation_hash(opened.id, ["app.py"])
    assert len({original, changed, deleted}) == 3
    assert deleted == service.validation_hash(opened.id, ["app.py"])
    for invalid in ("../demo/app.py", ".env"):
        with pytest.raises(RepositoryError):
            service.validation_hash(opened.id, [invalid])
    assert not (folder / ".git").exists()


def test_commit_matches_preview_and_preserves_other_staging(demo_repository, git_services):
    _, service, repository_id = git_services
    service.create_task_branch(repository_id, "codex/scoped-commit")
    (demo_repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    added = "new 文件 [1].py"
    (demo_repository / added).write_text("answer = 42\n", encoding="utf-8")
    (demo_repository / "test_app.py").unlink()
    (demo_repository / "notes.txt").write_text("user staged notes\n", encoding="utf-8")
    git_output(demo_repository, "add", "notes.txt")
    (demo_repository / "unrelated.txt").write_text("keep untracked\n", encoding="utf-8")
    files = ["app.py", added, "test_app.py"]
    preview = service.preview(repository_id, files, "task: scoped changes")
    assert set(preview.changed_files) == set(files)
    assert "notes.txt" in preview.excluded_files
    assert "unrelated.txt" in preview.excluded_files
    assert "answer = 42" in preview.diff
    index_before = git_output(demo_repository, "show", ":notes.txt")
    head = service.commit(repository_id, files, preview.message, preview.scope_hash, "codex/scoped-commit")
    assert git_output(demo_repository, "rev-parse", "HEAD^{tree}").strip() == preview.tree
    assert head == git_output(demo_repository, "rev-parse", "HEAD").strip()
    assert git_output(demo_repository, "show", "HEAD:notes.txt") == "original notes\n"
    assert git_output(demo_repository, "show", ":notes.txt") == index_before
    assert set(git_output(demo_repository, "diff", "--cached", "--name-only").splitlines()) == {"notes.txt"}
    assert "unrelated.txt" in git_output(demo_repository, "ls-files", "--others", "--exclude-standard")
    assert service.snapshot(repository_id).changed_files == ["notes.txt", "unrelated.txt"]


@pytest.mark.parametrize("change", ["content", "message", "head", "branch"])
def test_changed_preview_cannot_be_committed(demo_repository, git_services, change):
    _, service, repository_id = git_services
    service.create_task_branch(repository_id, "codex/stale-commit")
    (demo_repository / "app.py").write_text("value = 2\n", encoding="utf-8")
    preview = service.preview(repository_id, ["app.py"], "task: first message")
    message = preview.message
    if change == "content":
        (demo_repository / "app.py").write_text("value = 3\n", encoding="utf-8")
    elif change == "message":
        message = "task: different message"
    elif change == "head":
        git_output(demo_repository, "commit", "--allow-empty", "-qm", "External commit")
    else:
        git_output(demo_repository, "switch", "-c", "codex/other")
    head_before = git_output(demo_repository, "rev-parse", "HEAD")
    with pytest.raises(RepositoryError, match="changed") as error:
        service.commit(repository_id, ["app.py"], message, preview.scope_hash, "codex/stale-commit")
    assert error.value.code == "COMMIT_PREVIEW_STALE"
    assert git_output(demo_repository, "rev-parse", "HEAD") == head_before


def test_independently_staged_task_content_is_not_overwritten(demo_repository, git_services):
    _, service, repository_id = git_services
    service.create_task_branch(repository_id, "codex/index-conflict")
    (demo_repository / "app.py").write_text("user_staged = True\n", encoding="utf-8")
    git_output(demo_repository, "add", "app.py")
    (demo_repository / "app.py").write_text("task_content = True\n", encoding="utf-8")
    preview = service.preview(repository_id, ["app.py"], "task: change")
    with pytest.raises(RepositoryError) as error:
        service.commit(repository_id, ["app.py"], preview.message, preview.scope_hash, "codex/index-conflict")
    assert error.value.code == "TASK_INDEX_CONFLICT"
    assert git_output(demo_repository, "show", ":app.py") == "user_staged = True\n"
    assert not (demo_repository / ".git" / "index.lock").exists()


def test_existing_git_lock_is_preserved(demo_repository, git_services):
    _, service, repository_id = git_services
    service.create_task_branch(repository_id, "codex/busy-index")
    (demo_repository / "app.py").write_text("value = 2\n", encoding="utf-8")
    preview = service.preview(repository_id, ["app.py"], "task: change")
    lock_path = demo_repository / ".git" / "index.lock"
    lock_path.write_text("another process", encoding="utf-8")
    with pytest.raises(RepositoryError) as error:
        service.commit(repository_id, ["app.py"], preview.message, preview.scope_hash, "codex/busy-index")
    assert error.value.code == "GIT_BUSY"
    assert lock_path.read_text(encoding="utf-8") == "another process"


def test_dirty_branch_creation_and_sensitive_paths_are_blocked(demo_repository, git_services):
    _, service, repository_id = git_services
    (demo_repository / ".env").write_text("PRIVATE_TOKEN=example\n", encoding="utf-8")
    with pytest.raises(RepositoryError) as error:
        service.create_task_branch(repository_id, "codex/dirty")
    assert error.value.code == "WORKTREE_NOT_CLEAN"
    with pytest.raises(RepositoryError) as error:
        service.preview(repository_id, [".env"], "task: credentials")
    assert error.value.code == "PROTECTED_PATH"


def test_commit_scratch_is_kept_inside_the_state_directory(git_services):
    """An inherited TMP may point somewhere this process cannot write."""
    _, service, _ = git_services
    with service._temporary_directory("probe") as directory:
        created = Path(directory)
        assert created.parent == service.settings.state_dir.resolve()
        assert created.is_dir()


def test_transient_not_a_repository_error_is_retried_once(git_services, monkeypatch):
    from app import git_service as module

    _, service, repository_id = git_services
    real_run = module.subprocess.run
    calls = []

    def flaky(*args, **kwargs):
        calls.append(list(args[0]))
        if len(calls) == 1:
            # What Windows reports when a scanner holds `.git` for a moment.
            return module.subprocess.CompletedProcess(
                args[0], 128, "", "fatal: not a git repository (or any of the parent directories): .git\n"
            )
        return real_run(*args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", flaky)

    snapshot = service.snapshot(repository_id)
    assert snapshot.head
    assert calls[0] == calls[1], "the same command must be retried, not skipped"
    assert len(calls) > 2, "the rest of the snapshot still runs"


def test_missing_git_dir_is_reported_without_retry(git_services, monkeypatch):
    from app import git_service as module

    _, service, repository_id = git_services
    calls = []

    def always_missing(*args, **kwargs):
        calls.append(list(args[0]))
        return module.subprocess.CompletedProcess(
            args[0], 128, "", "fatal: not a git repository (or any of the parent directories): .git\n"
        )

    monkeypatch.setattr(module.subprocess, "run", always_missing)
    monkeypatch.setattr(service, "_git_dir_present", lambda repository: False)

    with pytest.raises(RepositoryError) as excinfo:
        service.snapshot(repository_id)

    details = excinfo.value.details
    assert details["attempts"] == 1, "a genuinely missing .git is not worth a retry"
    assert details["git_dir_present"] is False
    assert details["workdir"].endswith("demo")
    assert "GIT_LITERAL_PATHSPECS" in details["git_env"]
    assert len(calls) == 1


def test_transient_not_a_repository_error_is_retried_once(git_services, monkeypatch):
    from app import git_service as module

    _, service, repository_id = git_services
    real_run = module.subprocess.run
    calls = []

    def flaky(*args, **kwargs):
        calls.append(list(args[0]))
        if len(calls) == 1:
            # What Windows reports when a scanner holds `.git` for a moment.
            return module.subprocess.CompletedProcess(
                args[0], 128, "", "fatal: not a git repository (or any of the parent directories): .git\n"
            )
        return real_run(*args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", flaky)

    snapshot = service.snapshot(repository_id)
    assert snapshot.head
    assert calls[0] == calls[1], "the same command must be retried, not skipped"
    assert len(calls) > 2, "the rest of the snapshot still runs"


def test_missing_git_dir_is_reported_without_retry(git_services, monkeypatch):
    from app import git_service as module

    _, service, repository_id = git_services
    calls = []

    def always_missing(*args, **kwargs):
        calls.append(list(args[0]))
        return module.subprocess.CompletedProcess(
            args[0], 128, "", "fatal: not a git repository (or any of the parent directories): .git\n"
        )

    monkeypatch.setattr(module.subprocess, "run", always_missing)
    monkeypatch.setattr(service, "_git_dir_present", lambda repository: False)

    with pytest.raises(RepositoryError) as excinfo:
        service.snapshot(repository_id)

    details = excinfo.value.details
    assert details["attempts"] == 1, "a genuinely missing .git is not worth a retry"
    assert details["git_dir_present"] is False
    assert details["workdir"].endswith("demo")
    assert "GIT_LITERAL_PATHSPECS" in details["git_env"]
    assert len(calls) == 1


def test_state_changing_commands_are_never_retried(git_services, monkeypatch):
    from app import git_service as module

    _, service, repository_id = git_services
    calls = []

    def always_missing(*args, **kwargs):
        calls.append(list(args[0]))
        return module.subprocess.CompletedProcess(
            args[0], 128, "", "fatal: not a git repository (or any of the parent directories): .git\n"
        )

    monkeypatch.setattr(module.subprocess, "run", always_missing)
    repository = service.repositories.get(repository_id)

    with pytest.raises(RepositoryError) as excinfo:
        service._run(repository, ["commit", "-m", "task: retry me"])

    assert excinfo.value.details["attempts"] == 1, "a retried commit could apply twice"
    assert len(calls) == 1
    assert "commit" not in service.RETRYABLE


def test_state_changing_commands_are_never_retried(git_services, monkeypatch):
    from app import git_service as module

    _, service, repository_id = git_services
    calls = []

    def always_missing(*args, **kwargs):
        calls.append(list(args[0]))
        return module.subprocess.CompletedProcess(
            args[0], 128, "", "fatal: not a git repository (or any of the parent directories): .git\n"
        )

    monkeypatch.setattr(module.subprocess, "run", always_missing)
    repository = service.repositories.get(repository_id)

    with pytest.raises(RepositoryError) as excinfo:
        service._run(repository, ["commit", "-m", "task: retry me"])

    assert excinfo.value.details["attempts"] == 1, "a retried commit could apply twice"
    assert len(calls) == 1
    assert "commit" not in service.RETRYABLE
