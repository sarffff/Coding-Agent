from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .models import ApprovalDecisionRequest, ApprovalRequestCreate, ApprovalSummary, TaskSummary
from .repository_service import RepositoryError
from .store import StateStore


def plan_scope_hash(task: TaskSummary) -> str:
    return hashlib.sha256((task.id + "\n" + task.goal + "\n" + (task.plan.objective if task.plan else "")).encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ApprovalRecord:
    summary: ApprovalSummary
    key: str = ""


class ApprovalService:
    def __init__(self, store: StateStore | None = None) -> None:
        self.store = store
        self._items: dict[str, ApprovalRecord] = {}
        if store:
            for key, summary in store.models("approvals", ApprovalSummary):
                self._items[summary.id] = ApprovalRecord(summary=summary, key=key)

    def _persist(self, record: ApprovalRecord) -> None:
        if not self.store:
            return
        record.key = record.key or self.store.next_key("approvals")
        self.store.put("approvals", record.key, record.summary, record.summary.requested_at.isoformat())

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
                    self._persist(item)
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
        record = ApprovalRecord(summary=summary)
        self._items[summary.id] = record
        self._persist(record)
        return summary

    def get(self, approval_id: str) -> ApprovalSummary:
        record = self._items.get(approval_id)
        if record is None:
            raise RepositoryError("APPROVAL_NOT_FOUND", "Approval request is not registered.", {"approval_id": approval_id})
        self._expire_if_needed(record)
        return record.summary

    def list_for_task(self, task_id: str) -> list[ApprovalSummary]:
        return self.list(task_id=task_id)

    def list(self, task_id: str | None = None, status: str | None = None, approval_type: str | None = None) -> list[ApprovalSummary]:
        items = []
        for record in self._items.values():
            self._expire_if_needed(record)
            item = record.summary
            if (task_id is None or item.task_id == task_id) and (status is None or item.status == status) and (approval_type is None or item.type == approval_type):
                items.append(item)
        return sorted(items, key=lambda item: item.requested_at, reverse=True)

    def decide(self, approval_id: str, payload: ApprovalDecisionRequest, decided_by: str = "local-user") -> ApprovalSummary:
        record = self._items.get(approval_id)
        if record is None:
            raise RepositoryError("APPROVAL_NOT_FOUND", "Approval request is not registered.", {"approval_id": approval_id})
        self._expire_if_needed(record)
        if record.summary.status != "pending":
            raise RepositoryError("APPROVAL_NOT_PENDING", "Only pending approvals can be decided.", {"status": record.summary.status})
        if payload.decision in {"reject", "request_changes"} and not (payload.reason or "").strip():
            raise RepositoryError("APPROVAL_REASON_REQUIRED", "Rejecting or requesting changes requires a reason.")
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
        self._persist(record)
        return record.summary

    def has_approved(self, task_id: str, approval_type: str, scope_hash: str | None = None) -> bool:
        latest = next((item for item in self.list_for_task(task_id) if item.type == approval_type), None)
        return bool(latest and latest.status == "approved" and (scope_hash is None or latest.scope_hash == scope_hash))

    def continuation(self, approval_id: str) -> ApprovalSummary:
        approval = self.get(approval_id)
        if approval.status != "request_changes":
            raise RepositoryError("APPROVAL_CONTINUATION_UNAVAILABLE", "Only a request for changes can be continued.")
        if approval.continued_at is None and self.list(task_id=approval.task_id, approval_type=approval.type)[0].id != approval.id:
            raise RepositoryError("APPROVAL_CONTINUATION_UNAVAILABLE", "A newer approval has superseded this request for changes.")
        return approval

    def mark_continued(self, approval_id: str) -> None:
        record = self._items[approval_id]
        record.summary.continued_at = datetime.now(timezone.utc)
        self._persist(record)

    def invalidate_publish_approvals(self, task_id: str) -> None:
        for record in self._items.values():
            if record.summary.task_id == task_id and record.summary.type != "plan" and record.summary.status in {"pending", "approved"}:
                record.summary.status = "cancelled"
                record.summary.reason = "A review revision requires new approval for the updated changes."
                record.summary.decided_at = datetime.now(timezone.utc)
                record.summary.decided_by = "system"
                self._persist(record)

    def _expire_if_needed(self, record: ApprovalRecord) -> None:
        if record.summary.status in {"pending", "approved"} and record.summary.expires_at <= datetime.now(timezone.utc):
            record.summary.status = "expired"
            self._persist(record)
