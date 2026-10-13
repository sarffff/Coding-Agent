import { useEffect, useState } from "react";
import { ArrowUpRight, RefreshCw, ShieldCheck } from "lucide-react";
import { AsyncMessage, Dialog } from "@coding-agent/ui";
import { continueApproval, decideApproval, listAllApprovals, type Approval } from "../lib/api";
import { usePreferences } from "../lib/preferences";

type Props = {
  compact: boolean;
  revision: string;
  disabled: boolean;
  tasks: { id: string; title: string }[];
  onOpenTask: (taskId: string) => void;
  onOpenCenter: () => void;
  onChanged: (taskId: string) => Promise<void>;
  onBusyChange: (busy: boolean) => void;
  onPendingCount: (count: number) => void;
};

export function ApprovalPanel({ compact, revision, disabled, tasks, onOpenTask, onOpenCenter, onChanged, onBusyChange, onPendingCount }: Props) {
  const { t, language } = usePreferences();
  const [items, setItems] = useState<Approval[]>([]);
  const [filter, setFilter] = useState<Approval["status"] | "all">("pending");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [decision, setDecision] = useState<{ approval: Approval; action: "reject" | "request_changes" } | null>(null);
  const [reason, setReason] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    listAllApprovals(controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;
        setItems(result.items);
        onPendingCount(result.items.filter(item => item.status === "pending").length);
        setLoadError(null);
      })
      .catch(cause => { if (!controller.signal.aborted) setLoadError(cause instanceof Error ? cause.message : t("common.error")); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision, refresh, compact, t, onPendingCount]);

  const act = async (approval: Approval, action: "approve" | "reject" | "request_changes" | "continue") => {
    if (busy || disabled || (action === "reject" || action === "request_changes") && !reason.trim()) return;
    setBusy(true); onBusyChange(true); setError(null);
    let saved = false;
    try {
      if (action === "continue") await continueApproval(approval.id);
      else await decideApproval(approval.id, action, action === "approve" ? undefined : reason.trim());
      saved = true;
      setDecision(null); setReason("");
      await onChanged(approval.task_id);
      if (action === "continue") onOpenTask(approval.task_id);
    } catch (cause) {
      setError((saved ? t("approval.syncError") + " " : "") + (cause instanceof Error ? cause.message : t("common.error")));
    } finally {
      setBusy(false); onBusyChange(false); setRefresh(value => value + 1);
    }
  };

  const filtered = items.filter(item => compact ? item.status === "pending" : filter === "all" || item.status === filter);
  const visible = compact ? filtered.slice(0, 3) : filtered;
  const time = (value: string) => new Date(value).toLocaleString(language);
  const reload = () => { setError(null); setRefresh(value => value + 1); };
  return <section className="panel approval-panel" aria-label={t("approval.title")} aria-busy={loading || busy}>
    <Dialog open={decision !== null} title={t("approval." + (decision?.action ?? "reject"))} onClose={() => { if (!busy) setDecision(null); }}>
      <form className="patch-form" onSubmit={event => { event.preventDefault(); if (decision) void act(decision.approval, decision.action); }}>
        <p>{decision?.approval.summary}</p>
        <label className="dialog-label" htmlFor="approval-reason">{t("approval.reason")}</label>
        <textarea id="approval-reason" value={reason} onChange={event => setReason(event.target.value)} required maxLength={4000} rows={4} disabled={busy} autoFocus />
        <p className="history-retention">{t("approval.reasonHint")}</p>
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
        <div className="dialog-actions"><button type="button" className="small-action small-action--muted" disabled={busy} onClick={() => setDecision(null)}>{t("cancel")}</button><button className="small-action" disabled={busy || disabled || !reason.trim()}>{busy ? t("working") : t("common.confirm")}</button></div>
      </form>
    </Dialog>
    <div className="panel-header"><div><div className="section-kicker"><ShieldCheck size={13} /> {t("humanInTheLoop")}</div><h2>{t("approval.title")}</h2></div>{compact ? <button className="text-button" onClick={onOpenCenter}>{t("viewAll")} <ArrowUpRight size={13} /></button> : <button className="icon-button" disabled={loading || busy} title={t("common.refresh")} aria-label={t("common.refresh")} onClick={reload}><RefreshCw size={16} /></button>}</div>
    {!compact ? <div className="task-filter"><label htmlFor="approval-filter">{t("approval.filter")}</label><select id="approval-filter" value={filter} disabled={busy} onChange={event => setFilter(event.target.value as typeof filter)}>{["pending", "approved", "rejected", "request_changes", "expired", "cancelled", "all"].map(status => <option key={status} value={status}>{t("approval." + status)}</option>)}</select></div> : null}
    {loading ? <AsyncMessage tone="loading">{t("common.loading")}</AsyncMessage> : null}
    {(error || loadError) && !decision ? <AsyncMessage tone="error" onRetry={reload} retryLabel={t("common.refresh")}>{error || loadError}</AsyncMessage> : null}
    {!loading && !visible.length ? <div className="empty-operation"><ShieldCheck size={17} /><span>{t("approval.empty")}</span></div> : null}
    {visible.map(approval => <div className="test-history-item" key={approval.id}>
      <div className="approval-item"><div className="approval-copy"><strong>{t("approval.type." + approval.type)} · {t("approval." + approval.status)}</strong><span>{approval.summary}</span><span>{tasks.find(task => task.id === approval.task_id)?.title ?? approval.task_id}</span></div><button className="small-action small-action--muted" disabled={busy || disabled} onClick={() => onOpenTask(approval.task_id)}>{t("review")}</button></div>
      {!compact ? <div className="test-history-body"><small>{t("approval.requested")}: {time(approval.requested_at)} · {t("approval.expires")}: {time(approval.expires_at)}</small><code>{approval.task_id} · {approval.scope_hash}</code>{approval.reason ? <p>{approval.reason}</p> : null}{approval.decided_at ? <small>{approval.decided_by} · {time(approval.decided_at)}</small> : null}</div> : null}
      {approval.status === "pending" ? <div className="dialog-actions"><button className="small-action" disabled={busy || disabled || loading} onClick={() => void act(approval, "approve")}>{t("approve")}</button>{(["request_changes", "reject"] as const).map(action => <button className="small-action small-action--muted" key={action} disabled={busy || disabled || loading} onClick={() => { setReason(""); setError(null); setDecision({ approval, action }); }}>{t("approval." + action)}</button>)}</div> : null}
      {approval.status === "request_changes" && !compact ? <div className="test-history-body"><p className="history-retention">{t(approval.type === "plan" ? "approval.continuePlanHint" : "approval.continueHint")}</p><button className="small-action" disabled={busy || disabled || loading || Boolean(approval.continued_at) || items.some(item => item.task_id === approval.task_id && item.type === approval.type && item.requested_at > approval.requested_at)} onClick={() => void act(approval, "continue")}>{t(approval.continued_at ? "approval.continued" : "approval.continue")}</button></div> : null}
    </div>)}
  </section>;
}
