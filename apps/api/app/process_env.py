from __future__ import annotations

import os
import sys
from pathlib import Path


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build an environment block for a child process.

    A Windows child fails with STATUS_DLL_INIT_FAILED (0xC0000142) when the
    inherited block is missing ``SystemRoot``, so an API process started from a
    stripped-down shell cannot run ``git`` at all. Rather than trusting whatever
    the parent was launched with, fill the variables the loader needs and drop
    ``GIT_*`` so a caller's git configuration cannot leak into task commands.
    """

    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    if os.name == "nt":
        system_root = env.get("SystemRoot") or env.get("WINDIR") or str(Path(sys.prefix).anchor) + "Windows"
        env["SystemRoot"] = system_root
        env["WINDIR"] = system_root
        env.setdefault("COMSPEC", str(Path(system_root) / "System32" / "cmd.exe"))
    env.update(extra or {})
    return env


def is_process_startup_failure(exit_code: int | None) -> bool:
    """True for Windows NTSTATUS failure codes such as 0xC0000142 (3221225794)."""

    if exit_code is None:
        return False
    return exit_code < 0 or exit_code >= 0x80000000
