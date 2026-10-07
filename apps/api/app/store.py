from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel


SCHEMA_VERSION = 2


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
            CREATE TABLE IF NOT EXISTS idempotency (
                key TEXT PRIMARY KEY,
                endpoint TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idempotency_endpoint ON idempotency (endpoint);
            """
        )
        version = self._connection.execute("SELECT value FROM meta WHERE name = 'schema_version'").fetchone()
        if version is None:
            self._connection.execute("INSERT INTO meta (name, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
        elif version[0] > SCHEMA_VERSION:
            raise ValueError(f"State file {path} was written by a newer version (schema {version[0]} > {SCHEMA_VERSION}).")
        elif version[0] < SCHEMA_VERSION:
            self._migrate(version[0], SCHEMA_VERSION)
        self._connection.commit()

    def _migrate(self, current_version: int, target_version: int) -> None:
        """Migrate state database between schema versions."""
        with self._lock, self._connection:
            if current_version == 1 and target_version >= 2:
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS idempotency (
                        key TEXT PRIMARY KEY,
                        endpoint TEXT NOT NULL,
                        request_hash TEXT NOT NULL,
                        status_code INTEGER NOT NULL,
                        response_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idempotency_endpoint ON idempotency (endpoint);
                    """
                )
                self._connection.execute("UPDATE meta SET value = ? WHERE name = 'schema_version'", (2,))
                current_version = 2
            if current_version != target_version:
                raise ValueError(f"Migration from schema {current_version} to {target_version} not fully supported.")

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

    def get_idempotency(self, key: str) -> tuple[str, str, int, str] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT endpoint, request_hash, status_code, response_json FROM idempotency WHERE key = ?",
                (key,),
            ).fetchone()
            return row if row else None

    def save_idempotency(
        self, key: str, endpoint: str, request_hash: str, status_code: int, response_json: str, created_at: str
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO idempotency (key, endpoint, request_hash, status_code, response_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO NOTHING",
                (key, endpoint, request_hash, status_code, response_json, created_at),
            )

    def prune_idempotency(self, before: str) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute("DELETE FROM idempotency WHERE created_at < ?", (before,))
            return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self._connection.close()
