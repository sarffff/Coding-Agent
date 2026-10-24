from __future__ import annotations

from pathlib import Path
from threading import Lock

from .repository_service import RepositoryError


class WorkspaceReservations:
    """Keep overlapping folders exclusive for the lifetime of an execution."""

    # ponytail: one local API process; shared leases are needed before using multiple workers.

    def __init__(self) -> None:
        self._active: dict[object, tuple[Path, str, int]] = {}
        self._lock = Lock()

    def acquire(self, path: Path, task_id: str, owner: object | None = None) -> object:
        path = path.resolve()
        owner = owner if owner is not None else object()
        with self._lock:
            for active_owner, (active_path, active_task, _) in self._active.items():
                if active_owner is owner:
                    continue
                if path == active_path or path.is_relative_to(active_path) or active_path.is_relative_to(path):
                    raise RepositoryError(
                        "WORKSPACE_BUSY",
                        "Stop the active execution before operating on this folder.",
                        {"task_id": active_task, "path": str(active_path)},
                    )
            count = self._active.get(owner, (path, task_id, 0))[2]
            self._active[owner] = (path, task_id, count + 1)
        return owner

    def release(self, owner: object) -> None:
        with self._lock:
            reservation = self._active.get(owner)
            if reservation is None:
                return
            path, task_id, count = reservation
            if count == 1:
                del self._active[owner]
            else:
                self._active[owner] = (path, task_id, count - 1)
