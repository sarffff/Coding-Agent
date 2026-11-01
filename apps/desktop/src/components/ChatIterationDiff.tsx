import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { listCheckpoints, type RunCheckpoint } from "../lib/api";
import { usePreferences } from "../lib/preferences";

export function selectIterationCheckpoint(items: RunCheckpoint[], runId: string, checkpointId: string): RunCheckpoint | null {
  return items.find(item => item.id === checkpointId && item.run_id === runId) ?? null;
}

export function ChatIterationDiff({ runId, checkpointId, rolledBack }: { runId: string; checkpointId: string; rolledBack: boolean }) {
  const { language } = usePreferences();
  const zh = language === "zh-CN";
  const [open, setOpen] = useState(false);
  const [checkpoint, setCheckpoint] = useState<RunCheckpoint | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    setCheckpoint(null);
    setError(null);
    if (!open) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true);
    listCheckpoints(runId, controller.signal).then(payload => {
      if (controller.signal.aborted) return;
      const current = selectIterationCheckpoint(payload.items, runId, checkpointId);
      if (!current) throw new Error(zh ? "找不到本轮检查点。" : "This iteration's checkpoint was not found.");
      setCheckpoint(current);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : (zh ? "无法读取差异。" : "Unable to load changes."));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [runId, checkpointId, open, retry, zh]);
  return <details className="chat-iteration-diff" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><span>{zh ? "查看本轮差异" : "View iteration changes"}</span><ChevronDown size={12} aria-hidden="true" /></summary>
    {loading ? <p role="status">{zh ? "正在读取差异…" : "Loading changes…"}</p> : null}
    {error ? <div className="chat-diff-error" role="alert"><span>{error}</span><button type="button" onClick={() => setRetry(value => value + 1)}>{zh ? "重试" : "Retry"}</button></div> : null}
    {checkpoint ? <>
      {rolledBack || checkpoint.restored ? <p>{zh ? "该检查点已回滚，以下是历史改动。" : "This checkpoint was restored; the changes below are historical."}</p> : null}
      {checkpoint.pruned ? <p>{zh ? "检查点备份已按保留策略清理。" : "Checkpoint backups were removed by the retention policy."}</p> : null}
      {checkpoint.diff ? <pre className="diff-view" tabIndex={0} aria-label={zh ? "本轮统一差异" : "Iteration unified diff"}>{checkpoint.diff}</pre> : <p>{zh ? "该检查点没有文本差异，文件创建或删除请查看改动列表。" : "This checkpoint has no text diff; review the file list for creation or deletion."}</p>}
    </> : null}
  </details>;
}
