import os

import pytest

from app.models import PatchFile, SearchRequest
from app.patch_service import PatchService
from app.repository_service import RepositoryError


def test_read_symbols_context_and_sensitive_search(demo_repository, git_services):
    repositories, _, repository_id = git_services
    (demo_repository / ".env").write_text("SECRET_VALUE=must-not-be-returned\n", encoding="utf-8")
    (demo_repository / "example.ts").write_text("export interface User { name: string }\nexport function hello() { return 1; }\n", encoding="utf-8")
    source = repositories.read_file(repository_id, "app.py")
    assert source.symbols[0].name == "value"
    assert source.symbols[0].line == 1
    assert len(source.content_hash) == 64
    symbols = repositories.read_file(repository_id, "example.ts").symbols
    assert [(symbol.name, symbol.line) for symbol in symbols] == [("User", 1), ("hello", 2)]
    context = repositories.context(repository_id)
    assert "app.py" in context.entry_files
    assert "." in context.test_directories
    matches, _ = repositories.search(repository_id, SearchRequest(query="must-not-be-returned"))
    assert matches == []
    entries, _ = repositories.tree(repository_id)
    assert ".env" not in {entry.path for entry in entries}
    for name in (".env", "../outside.py", ".git/config"):
        with pytest.raises(RepositoryError):
            repositories.read_file(repository_id, name)


def test_search_truncation_and_glob(demo_repository, git_services):
    repositories, _, repository_id = git_services
    (demo_repository / "matches.py").write_text("needle\nneedle\n", encoding="utf-8")
    matches, truncated = repositories.search(repository_id, SearchRequest(query="needle", glob="matches.py", max_results=2))
    assert len(matches) == 2 and not truncated
    matches, truncated = repositories.search(repository_id, SearchRequest(query="needle", glob="matches.py", max_results=1))
    assert len(matches) == 1 and truncated


def test_rollback_restores_later_patches_and_preserves_external_edits(demo_repository, git_services):
    repositories, git, repository_id = git_services
    patches = PatchService(repositories, git.settings)
    original = (demo_repository / "app.py").read_text(encoding="utf-8")
    first = patches.preview(repository_id, [PatchFile(path="app.py", content="value = 2\n")], run_id="run-one")
    first_result = patches.apply(first.patch_id)
    second = patches.preview(repository_id, [PatchFile(path="app.py", content="value = 3\n"), PatchFile(path="new.py", operation="create", content="new = True\n")], run_id="run-one")
    second_result = patches.apply(second.patch_id)
    (demo_repository / "app.py").write_text("external edit\n", encoding="utf-8")
    with pytest.raises(RepositoryError) as error:
        patches.rollback(first_result.checkpoint.id)
    assert error.value.code == "ROLLBACK_CONFLICT"
    assert (demo_repository / "app.py").read_text(encoding="utf-8") == "external edit\n"
    assert (demo_repository / "new.py").exists()
    (demo_repository / "app.py").write_bytes(b"value = 3\n")
    restored = patches.rollback(first_result.checkpoint.id)
    assert set(restored.restored_checkpoint_ids) == {first_result.checkpoint.id, second_result.checkpoint.id}
    assert (demo_repository / "app.py").read_text(encoding="utf-8") == original
    assert not (demo_repository / "new.py").exists()
    assert all(item.restored for item in patches.list_checkpoints(restored.restored_checkpoint_ids))


def test_symlinked_source_and_patch_are_rejected(demo_repository, git_services, tmp_path):
    repositories, git, repository_id = git_services
    outside = tmp_path / "outside.py"
    outside.write_text("keep private\n", encoding="utf-8")
    link = demo_repository / "escape.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("This host cannot create test symlinks")
    with pytest.raises(RepositoryError):
        repositories.read_file(repository_id, "escape.py")
    patches = PatchService(repositories, git.settings)
    with pytest.raises(RepositoryError):
        patches.preview(repository_id, [PatchFile(path="escape.py", content="changed")])
    assert outside.read_text(encoding="utf-8") == "keep private\n"
