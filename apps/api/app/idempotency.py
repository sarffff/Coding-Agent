from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .repository_service import RepositoryError
from .store import StateStore


KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")


class IdempotencyService:
    """Replay protection for writes whose side effects leave this machine.

    A stored key returns the first response verbatim. A key reused for a
    different endpoint or payload is rejected instead of silently replaying.
    """

    def __init__(self, store: StateStore, ttl_hours: int = 24) -> None:
        self.store = store
        self.ttl_hours = ttl_hours
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()

    def replay(self, key: str | None, endpoint: str, payload: BaseModel) -> JSONResponse | None:
        if not key:
            return None
        self._assert_key(key)
        row = self.store.get_idempotency(key)
        if row is None:
            return None
        stored_endpoint, stored_hash, status_code, response_json = row
        if stored_endpoint != endpoint or stored_hash != self.request_hash(endpoint, payload):
            raise RepositoryError(
                "IDEMPOTENCY_KEY_REUSED",
                f"This Idempotency-Key was already used for '{stored_endpoint}' with a different body.",
                {"stored_endpoint": stored_endpoint},
            )
        return JSONResponse(status_code=status_code, content=json.loads(response_json))

    def start(self, key: str | None) -> None:
        if not key:
            return
        self._assert_key(key)
        with self._lock:
            if key in self._in_flight:
                raise RepositoryError("IDEMPOTENCY_IN_PROGRESS", "Another request with this Idempotency-Key is still running.")
            self._in_flight.add(key)

    def finish(self, key: str | None) -> None:
        if not key:
            return
        with self._lock:
            self._in_flight.discard(key)

    def record(self, key: str | None, endpoint: str, payload: BaseModel, status_code: int, content: BaseModel) -> None:
        if not key:
            return
        now = datetime.now(timezone.utc)
        self.store.save_idempotency(
            key, endpoint, self.request_hash(endpoint, payload), status_code, content.model_dump_json(), now.isoformat()
        )
        self.store.prune_idempotency((now - timedelta(hours=self.ttl_hours)).isoformat())

    def request_hash(self, endpoint: str, payload: BaseModel) -> str:
        return hashlib.sha256(f"{endpoint}\n{payload.model_dump_json()}".encode("utf-8")).hexdigest()

    def _assert_key(self, key: str) -> None:
        if not KEY_RE.fullmatch(key):
            raise RepositoryError(
                "INVALID_IDEMPOTENCY_KEY",
                "An Idempotency-Key must be 8-200 characters of letters, digits, '.', '_', ':' or '-'.",
            )


async def run_idempotent(
    service: IdempotencyService,
    key: str | None,
    endpoint: str,
    payload: BaseModel,
    action: Callable[[], Awaitable[BaseModel]],
    status_code: int = 201,
) -> BaseModel | JSONResponse:
    replay = service.replay(key, endpoint, payload)
    if replay is not None:
        return replay
    service.start(key)
    try:
        result = await action()
    finally:
        service.finish(key)
    service.record(key, endpoint, payload, status_code, result)
    return result
