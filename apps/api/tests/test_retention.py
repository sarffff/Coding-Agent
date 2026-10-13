from datetime import datetime, timedelta, timezone

from app.audit_service import AuditService
from app.config import Settings
from app.models import TaskIteration
from app.models import TestFailure as Failure
from app.models import TestRunResponse as RunResult
from app.retention import prune_test_outputs


def result(run_id, age_days, status="failed"):
    return RunResult(
        run_id=run_id, kind="pytest", command=["pytest"], status=status, duration_ms=5,
        stdout="captured stdout", stderr="captured stderr", validation_hash=f"hash-{run_id}",
        failed_tests=[Failure(path="app.py", line=1, test_name="t", message="boom")],
        created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    )


def iteration(number, test):
    return TaskIteration(
        id=f"it-{number}", task_id="task-1", run_id="run-1", number=number, goal="g",
        status="failed", test_result=test, test_runs=[test], failure_summary="boom",
        created_at=test.created_at, updated_at=test.created_at,
    )


def test_prune_bounds_run_count_but_keeps_validation_evidence():
    settings = Settings(test_output_max_runs=1, test_output_retention_days=30)
    recent, old = result("recent", 0), result("old", 1)
    iterations = [iteration(1, old), iteration(2, recent)]

    assert prune_test_outputs(iterations, settings) is True
    assert recent.output_pruned is False and recent.stdout == "captured stdout"
    assert old.output_pruned is True and old.stdout == "" and old.stderr == ""
    assert old.validation_hash == "hash-old"  # evidence the commit gate still needs is preserved
    assert old.failed_tests[0].message == "Test output removed by retention policy."
    assert iterations[0].failure_summary.startswith("Test output removed")
    assert prune_test_outputs(iterations, settings) is False  # idempotent: already pruned


def test_prune_drops_runs_past_the_retention_window():
    settings = Settings(test_output_max_runs=50, test_output_retention_days=7)
    fresh, expired = result("fresh", 1), result("expired", 30)
    iterations = [iteration(1, expired), iteration(2, fresh)]

    assert prune_test_outputs(iterations, settings) is True
    assert fresh.output_pruned is False
    assert expired.output_pruned is True  # under the count cap, but older than the day cutoff


def test_audit_prune_caps_total_event_count():
    audit = AuditService(store=None, retention_days=30, max_events=2)
    for index in range(4):
        audit.record("task.create", "succeeded", f"event {index}", run_id="run-1")
    assert [event.summary for event in audit.list_for_run("run-1")] == ["event 2", "event 3"]


def test_audit_prune_drops_events_past_retention():
    audit = AuditService(store=None, retention_days=7, max_events=100)
    old = audit.record("x", "succeeded", "old", run_id="run-1")
    old.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    audit.record("y", "succeeded", "fresh", run_id="run-1")
    assert [event.summary for event in audit.list_for_run("run-1")] == ["fresh"]
