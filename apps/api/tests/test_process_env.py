import os

from app.process_env import child_env, is_process_startup_failure


def test_child_env_scrubs_git_config_but_keeps_loader_variables(monkeypatch):
    monkeypatch.setenv("GIT_INDEX_FILE", "/leaked/index")
    monkeypatch.setenv("GIT_DIR", "/leaked/git")
    monkeypatch.delenv("SystemRoot", raising=False)
    monkeypatch.delenv("WINDIR", raising=False)

    env = child_env({"GIT_INDEX_FILE": "/task-owned/index"})

    assert "GIT_DIR" not in env
    assert env["GIT_INDEX_FILE"] == "/task-owned/index"
    if os.name == "nt":
        assert env["SystemRoot"] and env["WINDIR"] == env["SystemRoot"]
        assert env["COMSPEC"].lower().endswith("cmd.exe")


def test_windows_ntstatus_exit_codes_read_as_startup_failures():
    assert is_process_startup_failure(3221225794)  # 0xC0000142 STATUS_DLL_INIT_FAILED
    assert is_process_startup_failure(-1)
    assert not is_process_startup_failure(0)
    assert not is_process_startup_failure(1)
    assert not is_process_startup_failure(None)
