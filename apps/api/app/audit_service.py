from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from .models import AuditEvent


class AuditService:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

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
        return event

    def list_for_run(self, run_id: str) -> list[AuditEvent]:
        return [event for event in self._events if event.run_id == run_id]
