from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel


SCHEMA_VERSION = 1


class StateStore:
    """Write-through key/value store for domain records, ordered by insertion.

    Keys are prefixed with a per-scope sequence so that reading back by key
    reproduces the original insertion order after a restart.
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS records (
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (scope, key)
            ) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS records_scope ON records (scope);
            CREATE TABLE IF NOT EXISTS meta (
                name TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            """
        )
        version = self._connection.execute("SELECT value FROM meta WHERE name = 'schema_version'").fetchone()
        if version is None:
            self._connection.execute("INSERT INTO meta (name, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
        elif version[0] > SCHEMA_VERSION:
            raise ValueError(f"State file {path} was written by a newer version (schema {version[0]} > {SCHEMA_VERSION}).")
        elif version[0] < SCHEMA_VERSION:
            raise ValueError(f"State file {path} needs migration (schema {version[0]} < {SCHEMA_VERSION}).")
        self._connection.commit()

    def next_key(self, scope: str) -> str:
        with self._lock, self._connection:
            row = self._connection.execute(
                "INSERT INTO meta (name, value) VALUES (?, 1) ON CONFLICT(name) DO UPDATE SET value = value + 1 RETURNING value",
                (f"seq:{scope}",),
            ).fetchone()
        return f"{row[0]:012d}"

    def put(self, scope: str, key: str, payload: BaseModel | str, updated_at: str) -> None:
        text = payload.model_dump_json() if isinstance(payload, BaseModel) else payload
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO records (scope, key, payload, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(scope, key) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
                (scope, key, text, updated_at),
            )

    def delete(self, scope: str, key: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM records WHERE scope = ? AND key = ?", (scope, key))

    def items(self, scope: str) -> list[tuple[str, str]]:
        with self._lock:
            cursor = self._connection.execute("SELECT key, payload FROM records WHERE scope = ? ORDER BY key", (scope,))
            return cursor.fetchall()

    def models(self, scope: str, model: type[BaseModel]) -> Iterable[tuple[str, BaseModel]]:
        for key, payload in self.items(scope):
            yield key, model.model_validate_json(payload)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
