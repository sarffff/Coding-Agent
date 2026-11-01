import { useEffect, useState } from "react";
import { AsyncMessage } from "@coding-agent/ui";
import type { PlanStepProgress } from "@coding-agent/types";
import { getAgentState, type AgentTask } from "../lib/api";
import { describeError } from "../lib/errorMessages";
import { usePreferences } from "../lib/preferences";

type Props = { taskId: string; revision: string; plan: NonNullable<AgentTask["plan"]>; hidden: boolean };

export function PlanProgress({ taskId, revision, plan, hidden }: Props) {
  const { t } = usePreferences();
  const [progress, setProgress] = useState<PlanStepProgress[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (hidden) return;
    const controller = new AbortController();
    setLoading(true); setError(null);
    getAgentState(taskId, controller.signal).then(state => {
      if (!controller.signal.aborted && state.task.id === taskId) setProgress(state.plan_progress ?? []);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(describeError(reason, t("plan.progressError")));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [taskId, revision, hidden, refresh, t]);

  return <div className="plan-list" hidden={hidden} aria-label={t("plan.progressTitle")} aria-busy={loading}>
    <div className="plan-label">{t("plan.progressTitle")} / {plan.steps.length} {t("steps")}</div>
    <p className="empty-detail">{t("plan.progressHint")}</p>
    {loading ? <AsyncMessage tone="loading">{t("common.loading")}</AsyncMessage> : null}
    {error ? <AsyncMessage tone="error" onRetry={() => setRefresh(value => value + 1)} retryLabel={t("common.refresh")}>{error}</AsyncMessage> : null}
    {plan.steps.map((step, index) => {
      const item = !loading && !error ? progress.find(entry => entry.step_id === step.id) : undefined;
      const status = item?.status ?? "pending";
      return <div className="plan-step" key={step.id}>
        <span className={`plan-step-index ${status === "confirmed" ? "plan-step-index--active" : ""}`}>{index + 1}</span>
        <div className="plan-step-copy"><strong>{step.title}</strong><span>{step.description}</span>
          <small>{t("plan.progress." + status)}</small>
          {item?.changed_files.length ? <span>{t("plan.actualFiles")} · {item.changed_files.join(", ")}</span> : null}
          {item?.iteration_ids.length ? <small style={{ overflowWrap: "anywhere" }}>{t("plan.iterations")} · {item.iteration_ids.join(", ")}</small> : null}
          {item?.evidence.map(evidence => <p className="empty-detail" key={evidence.criterion_id}>{t("completion.evidence")} · {evidence.evidence}</p>)}
        </div>
        <span className={`plan-risk plan-risk--${step.risk}`}>{t(`risk.${step.risk}`)}</span>
      </div>;
    })}
  </div>;
}
