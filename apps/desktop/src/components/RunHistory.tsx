import { useEffect, useState } from "react";
import { AsyncMessage, Dialog } from "@coding-agent/ui";
import { Clock3, RotateCcw, RefreshCw } from "lucide-react";
import { listCheckpoints, listTestHistory, rollbackRun, type RunCheckpoint, type TestHistoryItem } from "../lib/api";
import { usePreferences } from "../lib/preferences";

export function RunHistory({ runId, revision, onRestored, disabled }: { runId: string; revision: string; onRestored: () => Promise<void>; disabled: boolean }) {
  const { t, language } = usePreferences();
  const [tests, setTests] = useState<TestHistoryItem[]>([]);
  const [checkpoints, setCheckpoints] = useState<RunCheckpoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [pendingCheckpoint, setPendingCheckpoint] = useState<RunCheckpoint | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null); setTests([]); setCheckpoints([]);
    Promise.all([listTestHistory(runId, controller.signal), listCheckpoints(runId, controller.signal)])
      .then(([history, snapshots]) => { if (!controller.signal.aborted) { setTests(history.items); setCheckpoints(snapshots.items); } })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : t("common.error")); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [runId, revision, refresh, t]);

  const restore = async (checkpoint: RunCheckpoint) => {
    setPendingCheckpoint(null);
    setBusy(true); setError(null);
    try {
      await rollbackRun(runId, checkpoint.id);
      await onRestored();
      setRefresh(value => value + 1);
    } catch (reason) { setError(reason instanceof Error ? reason.message : t("common.error")); }
    finally { setBusy(false); }
  };
  const time = (value: string) => new Date(value).toLocaleString(language, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return <section className="panel run-history" aria-label={t("history.title")}>
    <Dialog open={pendingCheckpoint !== null} title={t("history.rollback")} onClose={() => setPendingCheckpoint(null)}><p>{t("history.confirm")}</p><pre>{pendingCheckpoint?.files.join("\n")}</pre><div className="dialog-actions"><button className="small-action small-action--muted" onClick={() => setPendingCheckpoint(null)}>{t("cancel")}</button><button className="small-action" onClick={() => pendingCheckpoint && void restore(pendingCheckpoint)}>{t("common.confirm")}</button></div></Dialog>
    <div className="panel-header"><div><div className="section-kicker"><Clock3 size={13} /> HISTORY / {runId.slice(-6)}</div><h2>{t("history.title")}</h2></div><button className="icon-button" title={t("common.refresh")} aria-label={t("common.refresh")} disabled={loading || busy} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={16} /></button></div>
    {error ? <AsyncMessage tone="error" onRetry={() => setRefresh(value => value + 1)} retryLabel={t("common.retry")}>{error}</AsyncMessage> : null}
    {loading ? <p className="browser-empty" role="status">{t("common.loading")}</p> : <div className="history-columns"><div><h3>{t("history.tests")}</h3>{!tests.length ? <p className="history-empty">{t("history.emptyTests")}</p> : [...tests].reverse().map(({ iteration_number, result }) => <details className="test-history-item" key={result.id}><summary><span className={"result-badge result-badge--" + result.status}>{t("validation." + result.status)}</span><strong>{t("history.iteration")} {iteration_number} · {t("validation." + result.kind)}</strong><time>{time(result.created_at)}</time><span>{result.duration_ms} ms</span></summary><div className="test-history-body"><code>{result.command.join(" ")}</code><small>{t("history.exit")}: {result.exit_code ?? "—"}</small>{result.stdout ? <><h4>{t("history.stdout")}</h4><pre className="test-output">{result.stdout}</pre></> : null}{result.stderr ? <><h4>{t("history.stderr")}</h4><pre className="test-output">{result.stderr}</pre></> : null}{result.output_truncated ? <span className="output-note">{t("outputTruncated")}</span> : null}</div></details>)}</div><div><h3>{t("history.checkpoints")}</h3>{!checkpoints.length ? <p className="history-empty">{t("history.emptyCheckpoints")}</p> : [...checkpoints].reverse().map(checkpoint => <div className="checkpoint-row" key={checkpoint.id}><div><time>{time(checkpoint.created_at)}</time><strong>{checkpoint.files.join(", ")}</strong><code>{checkpoint.id}</code></div><button className="small-action small-action--muted" disabled={disabled || busy || checkpoint.restored} onClick={() => setPendingCheckpoint(checkpoint)}><RotateCcw size={13} />{checkpoint.restored ? t("history.restored") : t("history.rollback")}</button></div>)}</div></div>}
    <p className="history-retention">{t("history.inMemory")}</p>
  </section>;
}
