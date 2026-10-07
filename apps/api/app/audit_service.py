from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from .models import AuditEvent
from .store import StateStore


class AuditService:
    def __init__(self, store: StateStore | None = None):
        self.store = store
        self._events: list[AuditEvent] = []
        self._keys: dict[str, str] = {}
        if store:
            for key, event in store.models("audit_events", AuditEvent):
                self._events.append(event)
                self._keys[event.id] = key

    def record(
        self,
        action: str,
        status: str,
        summary: str,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        trace_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            id=f"event-{uuid4().hex[:12]}",
            action=action,
            status=status,
            run_id=run_id,
            task_id=task_id,
            trace_id=trace_id,
            summary=summary,
            details=details or {},
            created_at=datetime.now(timezone.utc),
        )
        self._events.append(event)
        if self.store:
            key = self.store.next_key("audit_events")
            self._keys[event.id] = key
            self.store.put("audit_events", key, event, event.created_at.isoformat())
        return event

    def list_for_run(self, run_id: str) -> list[AuditEvent]:
        return [event for event in self._events if event.run_id == run_id]
