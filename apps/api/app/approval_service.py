from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .models import ApprovalDecisionRequest, ApprovalRequestCreate, ApprovalSummary
from .repository_service import RepositoryError


@dataclass(slots=True)
class ApprovalRecord:
    summary: ApprovalSummary


class ApprovalService:
    def __init__(self) -> None:
        self._items: dict[str, ApprovalRecord] = {}

    def create(self, task_id: str, run_id: str, payload: ApprovalRequestCreate) -> ApprovalSummary:
        now = datetime.now(timezone.utc)
        for item in self._items.values():
            if item.summary.task_id == task_id and item.summary.type == payload.type and item.summary.status == "pending":
                self._expire_if_needed(item)
                requested_scope = payload.scope_hash or "0" * 64
                if item.summary.status == "pending" and item.summary.scope_hash == requested_scope:
                    return item.summary
                if item.summary.status == "pending":
                    item.summary.status = "cancelled"
                    item.summary.reason = "Superseded by a new task scope."
                    item.summary.decided_at = now
                    item.summary.decided_by = "system"
        summary = ApprovalSummary(
            id=f"approval-{uuid4().hex[:12]}",
            task_id=task_id,
            run_id=run_id,
            type=payload.type,
            status="pending",
            summary=payload.summary.strip(),
            scope_hash=payload.scope_hash or "0" * 64,
            requested_at=now,
            expires_at=now + timedelta(minutes=payload.expires_in_minutes),
        )
        self._items[summary.id] = ApprovalRecord(summary=summary)
        return summary

    def get(self, approval_id: str) -> ApprovalSummary:
        record = self._items.get(approval_id)
        if record is None:
            raise RepositoryError("APPROVAL_NOT_FOUND", "Approval request is not registered.", {"approval_id": approval_id})
        self._expire_if_needed(record)
        return record.summary

    def list_for_task(self, task_id: str) -> list[ApprovalSummary]:
        items = []
        for record in self._items.values():
            if record.summary.task_id == task_id:
                self._expire_if_needed(record)
                items.append(record.summary)
        return sorted(items, key=lambda item: item.requested_at, reverse=True)

    def decide(self, approval_id: str, payload: ApprovalDecisionRequest, decided_by: str = "local-user") -> ApprovalSummary:
        record = self._items.get(approval_id)
        if record is None:
            raise RepositoryError("APPROVAL_NOT_FOUND", "Approval request is not registered.", {"approval_id": approval_id})
        self._expire_if_needed(record)
        if record.summary.status != "pending":
            raise RepositoryError("APPROVAL_NOT_PENDING", "Only pending approvals can be decided.", {"status": record.summary.status})
        if payload.decision == "approve":
            record.summary.status = "approved"
        elif payload.decision == "reject":
            record.summary.status = "rejected"
        elif payload.decision == "request_changes":
            record.summary.status = "request_changes"
        else:
            record.summary.status = "cancelled"
        record.summary.reason = payload.reason.strip() if payload.reason else None
        record.summary.decided_at = datetime.now(timezone.utc)
        record.summary.decided_by = decided_by
        return record.summary

    def has_approved(self, task_id: str, approval_type: str, scope_hash: str | None = None) -> bool:
        return any(
            item.status == "approved"
            and item.type == approval_type
            and (scope_hash is None or item.scope_hash == scope_hash)
            for item in self.list_for_task(task_id)
        )

    @staticmethod
    def _expire_if_needed(record: ApprovalRecord) -> None:
        if record.summary.status == "pending" and record.summary.expires_at <= datetime.now(timezone.utc):
            record.summary.status = "expired"

