from __future__ import annotations

import os
import sys
from pathlib import Path


# Variable-name fragments that mark credentials; scrubbed from processes that run repository or model code.
_SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "APIKEY", "CREDENTIAL", "PRIVATE_KEY")


def child_env(extra: dict[str, str] | None = None, *, scrub_secrets: bool = False) -> dict[str, str]:
    """Build an environment block for a child process.

    A Windows child fails with STATUS_DLL_INIT_FAILED (0xC0000142) when the
    inherited block is missing ``SystemRoot``, so an API process started from a
    stripped-down shell cannot run ``git`` at all. Rather than trusting whatever
    the parent was launched with, fill the variables the loader needs and drop
    ``GIT_*`` so a caller's git configuration cannot leak into task commands.
    ``scrub_secrets`` also drops credential-looking variables (FORGE_*_API_KEY,
    GITHUB_TOKEN, ...) for processes that execute repository or model-chosen code.
    """

    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    if scrub_secrets:
        env = {key: value for key, value in env.items()
               if not key.upper().startswith("FORGE_") and not any(marker in key.upper() for marker in _SECRET_MARKERS)}
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
