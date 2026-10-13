from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .models import AuditEvent
from .store import StateStore


class AuditService:
    def __init__(self, store: StateStore | None = None, *, retention_days: int = 30, max_events: int = 5_000):
        self.store = store
        self.retention_days = max(0, retention_days)
        self.max_events = max(0, max_events)
        self._events: list[AuditEvent] = []
        self._keys: dict[str, str] = {}
        if store:
            for key, event in store.models("audit_events", AuditEvent):
                self._events.append(event)
                self._keys[event.id] = key
        self._prune()

    def _prune(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        eligible = [event for event in self._events if event.created_at >= cutoff]
        kept = eligible[-self.max_events:] if self.max_events else []
        kept_ids = {event.id for event in kept}
        for event in self._events:
            if event.id not in kept_ids:
                key = self._keys.pop(event.id, None)
                if self.store and key:
                    self.store.delete("audit_events", key)
        self._events = kept

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
        self._prune()
        return event

    def list_for_run(self, run_id: str) -> list[AuditEvent]:
        self._prune()
        return [event for event in self._events if event.run_id == run_id]
