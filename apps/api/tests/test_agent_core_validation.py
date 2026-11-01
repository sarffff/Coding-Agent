import pytest
from conftest import build_settings

from app.models import PatchFile
from app.patch_service import PatchService, PersistedCheckpoint
from app.repository_service import RepositoryError, RepositoryService
from app.store import StateStore


@pytest.fixture
def patch_workspace(demo_repository, tmp_path):
    settings = build_settings(demo_repository, tmp_path)
    store = StateStore(settings.state_dir / "state.sqlite3")
    repositories = RepositoryService(settings, store)
    repository_id = repositories.register(str(demo_repository)).id
    patches = PatchService(repositories, settings, store)
    yield demo_repository, patches, repository_id
    store.close()


@pytest.mark.parametrize("missing_directory", [False, True])
def test_missing_persisted_backup_blocks_rollback_before_writing(patch_workspace, missing_directory):
    directory, patches, repository_id = patch_workspace
    preview = patches.preview(repository_id, [
        PatchFile(path="app.py", content="value = 2\n"),
        PatchFile(path="notes.txt", content="updated notes\n"),
    ], run_id="run-one")
    applied = patches.apply(preview.patch_id)
    checkpoint = patches._checkpoints[applied.checkpoint.id]
    (checkpoint.backup_dir / "notes.txt").unlink()
    if missing_directory:
        (checkpoint.backup_dir / "app.py").unlink()
        checkpoint.backup_dir.rmdir()

    restarted = PatchService(patches.repositories, patches.settings, patches.store)

    assert restarted.list_checkpoints([applied.checkpoint.id])
    with pytest.raises(RepositoryError) as error:
        restarted.rollback(applied.checkpoint.id)
    assert error.value.code == "CHECKPOINT_UNAVAILABLE"
    assert (directory / "app.py").read_bytes() == b"value = 2\n"
    assert (directory / "notes.txt").read_bytes() == b"updated notes\n"
    assert not restarted.list_checkpoints([applied.checkpoint.id])[0].restored


def test_existing_empty_backup_still_restores_after_restart(patch_workspace):
    directory, patches, repository_id = patch_workspace
    (directory / "notes.txt").write_bytes(b"")
    preview = patches.preview(repository_id, [PatchFile(path="notes.txt", content="updated\n")])
    applied = patches.apply(preview.patch_id)

    restarted = PatchService(patches.repositories, patches.settings, patches.store)
    restarted.rollback(applied.checkpoint.id)

    assert (directory / "notes.txt").read_bytes() == b""


def test_truncated_persisted_backup_blocks_rollback_before_writing(patch_workspace):
    directory, patches, repository_id = patch_workspace
    preview = patches.preview(repository_id, [PatchFile(path="notes.txt", content="updated\n")])
    applied = patches.apply(preview.patch_id)
    checkpoint = patches._checkpoints[applied.checkpoint.id]
    (checkpoint.backup_dir / "notes.txt").write_bytes(b"")

    restarted = PatchService(patches.repositories, patches.settings, patches.store)

    with pytest.raises(RepositoryError) as error:
        restarted.rollback(applied.checkpoint.id)
    assert error.value.code == "CHECKPOINT_UNAVAILABLE"
    assert (directory / "notes.txt").read_bytes() == b"updated\n"


def test_legacy_checkpoint_without_backup_hashes_still_restores(patch_workspace):
    directory, patches, repository_id = patch_workspace
    original = (directory / "notes.txt").read_bytes()
    preview = patches.preview(repository_id, [PatchFile(path="notes.txt", content="updated\n")])
    applied = patches.apply(preview.patch_id)
    key, persisted = next(iter(patches.store.models("checkpoints", PersistedCheckpoint)))
    legacy = persisted.model_dump_json(exclude={"snapshot_hashes"})
    patches.store.put("checkpoints", key, legacy, applied.checkpoint.created_at.isoformat())

    restarted = PatchService(patches.repositories, patches.settings, patches.store)
    restarted.rollback(applied.checkpoint.id)

    assert (directory / "notes.txt").read_bytes() == original


def test_failed_apply_does_not_poison_later_rollback_chain(patch_workspace, monkeypatch):
    directory, patches, repository_id = patch_workspace
    original = (directory / "app.py").read_bytes()
    original_notes = (directory / "notes.txt").read_bytes()
    first = patches.preview(repository_id, [PatchFile(path="app.py", content="value = 2\n")], run_id="run-one")
    first_result = patches.apply(first.patch_id)
    failing = patches.preview(repository_id, [
        PatchFile(path="app.py", content="value = 3\n"),
        PatchFile(path="notes.txt", content="fail-once\n"),
    ], run_id="run-one")
    write = patches._atomic_write

    def fail_once(path, content):
        if content == "fail-once\n":
            raise OSError("simulated write failure")
        write(path, content)

    monkeypatch.setattr(patches, "_atomic_write", fail_once)
    with pytest.raises(RepositoryError) as error:
        patches.apply(failing.patch_id)
    assert error.value.code == "PATCH_APPLY_FAILED"
    assert (directory / "app.py").read_bytes() == b"value = 2\n"
    assert (directory / "notes.txt").read_bytes() == original_notes
    final = patches.preview(repository_id, [PatchFile(path="app.py", content="value = 4\n")], run_id="run-one")
    final_result = patches.apply(final.patch_id)

    restarted = PatchService(patches.repositories, patches.settings, patches.store)
    result = restarted.rollback(first_result.checkpoint.id)

    assert set(result.restored_checkpoint_ids) == {first_result.checkpoint.id, final_result.checkpoint.id}
    assert (directory / "app.py").read_bytes() == original
    assert (directory / "notes.txt").read_bytes() == original_notes


def test_external_non_utf8_edit_is_a_rollback_conflict(patch_workspace):
    directory, patches, repository_id = patch_workspace
    preview = patches.preview(repository_id, [PatchFile(path="app.py", content="value = 2\n")])
    applied = patches.apply(preview.patch_id)
    (directory / "app.py").write_bytes(b"\xffexternal edit")

    with pytest.raises(RepositoryError) as error:
        patches.rollback(applied.checkpoint.id)

    assert error.value.code == "ROLLBACK_CONFLICT"
    assert (directory / "app.py").read_bytes() == b"\xffexternal edit"


def test_failed_atomic_replace_does_not_leave_temporary_file(tmp_path, monkeypatch):
    target = tmp_path / "source.py"
    target.write_bytes(b"original")

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("app.patch_service.os.replace", fail_replace)
    with pytest.raises(OSError):
        PatchService._atomic_write(target, b"updated")

    assert target.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [target]
