from datetime import datetime, timedelta, timezone

from .config import Settings
from .models import TaskIteration


def prune_test_outputs(iterations: list[TaskIteration], settings: Settings) -> bool:
    """Bound retained output without losing validation evidence or checkpoints."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, settings.test_output_retention_days))
    results = {}
    for iteration in iterations:
        for result in [*iteration.test_runs, *([iteration.test_result] if iteration.test_result else [])]:
            results[result.id] = result
    recent = sorted(results.values(), key=lambda result: result.created_at, reverse=True)
    keep = {result.id for result in recent[:max(0, settings.test_output_max_runs)] if result.created_at >= cutoff}
    changed = False
    for iteration in iterations:
        for result in [*iteration.test_runs, *([iteration.test_result] if iteration.test_result else [])]:
            if result.id in keep or result.output_pruned:
                continue
            result.stdout = ""
            result.stderr = ""
            for failure in result.failed_tests:
                failure.message = "Test output removed by retention policy."
            result.output_pruned = True
            changed = True
        if iteration.test_result and iteration.test_result.output_pruned and iteration.failure_summary:
            iteration.failure_summary = "Test output removed by retention policy. Rerun validation for failure details."
    return changed
