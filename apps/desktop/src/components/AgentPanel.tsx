import { useEffect, useRef, useState } from "react";
import { Activity, ArrowUpRight, Bot, RefreshCw, ShieldCheck } from "lucide-react";
import { AsyncMessage } from "@coding-agent/ui";
import { advanceAgent, getAgentState, type AgentAction, type AgentAdvanceAction, type AgentState, type TaskBudget, type TestKind } from "../lib/api";
import { usePreferences } from "../lib/preferences";

const actionLabels: Record<AgentAction, string> = {
  recover: "recovery.action",
  request_plan_approval: "requestPlanApproval",
  await_plan_approval: "awaitingApproval",
  connect_repository: "addRepository",
  create_branch: "createBranch",
  start_iteration: "startIteration",
  review_patch: "agent.reviewPatch",
  run_tests: "runTests",
  wait_for_tests: "running",
  start_repair: "repair",
  review_commit: "previewLocalDiff",
  review_push: "publish.pushTitle",
  review_pr: "publish.prTitle",
  await_pr: "agent.awaitPr",
  completed: "done",
  paused: "paused",
  cancelled: "cancelled",
  manual_review: "agent.manualReview",
};

const advanceActions: AgentAdvanceAction[] = ["request_plan_approval", "start_iteration", "start_repair", "run_tests"];

type AgentPanelProps = {
  taskId: string;
  revision: string;
  testKind: TestKind;
  testTarget: string;
  disabled: boolean;
  hidden: boolean;
  onAdvanced: () => Promise<void>;
  onBusyChange: (action: AgentAdvanceAction | null) => void;
};

export function AgentPanel({ taskId, revision, testKind, testTarget, disabled, hidden, onAdvanced, onBusyChange }: AgentPanelProps) {
  const { t } = usePreferences();
  const [state, setState] = useState<AgentState | null>(null);
  const [loading, setLoading] = useState(true);
  const [advancing, setAdvancing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const loadRequest = useRef<AbortController | null>(null);
  const advanceRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    if (hidden) {
      loadRequest.current?.abort();
      setLoading(false);
      return;
    }
    if (advanceRequest.current) return;
    const controller = new AbortController();
    loadRequest.current = controller;
    setLoading(true);
    setError(null);
    getAgentState(taskId, controller.signal)
      .then(result => { if (!controller.signal.aborted) setState(result); })
      .catch(reason => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : t("agent.loadError"));
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [taskId, revision, refresh, hidden, t]);

  useEffect(() => () => {
    loadRequest.current?.abort();
    advanceRequest.current?.abort();
  }, [taskId]);

  const nextAction = advanceActions.find(action => action === state?.action);
  const advance = async () => {
    if (!state?.can_advance || !nextAction || disabled || loading || error || advanceRequest.current) return;
    loadRequest.current?.abort();
    const controller = new AbortController();
    advanceRequest.current = controller;
    setAdvancing(true);
    setLoading(false);
    setError(null);
    onBusyChange(nextAction);
    let advanced = false;
    try {
      const result = await advanceAgent(taskId, {
        action: nextAction,
        kind: testKind,
        target: testTarget.trim() || undefined,
      }, crypto.randomUUID(), controller.signal);
      if (controller.signal.aborted) return;
      advanced = true;
      setState(result);
      await onAdvanced();
      if (!controller.signal.aborted) setRefresh(value => value + 1);
    } catch (reason) {
      if (!controller.signal.aborted) {
        const detail = reason instanceof Error ? ` ${reason.message}` : "";
        setError(t(advanced ? "agent.syncError" : "agent.advanceError") + detail);
      }
    } finally {
      advanceRequest.current = null;
      setAdvancing(false);
      onBusyChange(null);
    }
  };

  const canRefresh = !loading && !advancing;
  return <section className="panel run-history" hidden={hidden} aria-label={t("agent.title")} aria-busy={loading || advancing}>
    <div className="panel-header">
      <div><div className="section-kicker"><Bot size={13} /> AGENT / {taskId.slice(-6)}</div><h2>{t("agent.title")}</h2></div>
      <button type="button" className="icon-button" title={t("common.refresh")} aria-label={t("common.refresh")} disabled={!canRefresh} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={16} /></button>
    </div>
    {loading ? <AsyncMessage tone="loading">{t("common.loading")}</AsyncMessage> : null}
    {error ? <AsyncMessage tone="error" onRetry={canRefresh ? () => setRefresh(value => value + 1) : undefined} retryLabel={t("common.refresh")}>{error}</AsyncMessage> : null}
    {state ? <>
      <div className="iteration-bar">
        <div className="iteration-status"><strong>{t(actionLabels[state.action])}</strong><span>{state.requires_approval ? t("agent.approvalRequired") : t("agent.scope")}</span></div>
        <button type="button" className="small-action" onClick={() => void advance()} disabled={!state.can_advance || !nextAction || disabled || loading || advancing || Boolean(error)}>
          {advancing ? <Activity size={13} className="spin" /> : <ArrowUpRight size={13} />}{advancing ? t("working") : t("agent.advance")}
        </button>
      </div>
      <p className="history-retention">{state.message}</p>
      {state.task.budget ? <BudgetMeter budget={state.task.budget} /> : null}
      {state.pending_approval ? <div className="recovery-note"><ShieldCheck size={14} /><span>{state.pending_approval.summary}</span></div> : null}
    </> : null}
  </section>;
}

function BudgetMeter({ budget }: { budget: TaskBudget }) {
  const { t } = usePreferences();
  const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
  const governors = [
    { key: "iterations", used: budget.iterations, max: budget.max_iterations, text: `${budget.iterations}/${budget.max_iterations}` },
    { key: "wall_time", used: budget.elapsed_seconds, max: budget.max_wall_time_seconds, text: `${clock(budget.elapsed_seconds)} / ${clock(budget.max_wall_time_seconds)}` },
    { key: "remote_calls", used: budget.remote_calls, max: budget.max_remote_calls, text: `${budget.remote_calls}/${budget.max_remote_calls}` },
    { key: "consecutive_failures", used: budget.consecutive_failures, max: budget.max_consecutive_failures, text: `${budget.consecutive_failures}/${budget.max_consecutive_failures}` },
  ];
  return <div className="budget-meter" aria-label={t("budget.title")}>
    {governors.map(governor => {
      const ratio = governor.max ? Math.min(1, governor.used / governor.max) : 0;
      const tone = budget.exhausted_reason === governor.key ? "exhausted" : ratio >= 0.8 ? "warn" : "ok";
      return <div className={`budget-stat budget-stat--${tone}`} key={governor.key} title={t("budget." + governor.key)}>
        <span className="budget-stat-label">{t("budget." + governor.key)}</span>
        <span className="budget-stat-value">{governor.text}</span>
        <span className="budget-bar"><span className="budget-bar-fill" style={{ width: `${Math.round(ratio * 100)}%` }} /></span>
      </div>;
    })}
  </div>;
}
