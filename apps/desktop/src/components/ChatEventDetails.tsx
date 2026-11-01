import { ChevronDown, FileCode2 } from "lucide-react";
import type { AuditEvent } from "../lib/api";
import { usePreferences } from "../lib/preferences";
import "../styles/chat-event-details.css";

type ChatEventDetailsProps = {
  event: AuditEvent;
  onOpenFile?: (path: string, line?: number) => void;
};

const text = (value: unknown) => typeof value === "string" && value.trim() ? value : null;
const count = (value: unknown) => typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => text(item) !== null) : [];

export function ChatEventDetails({ event, onOpenFile }: ChatEventDetailsProps) {
  const { language } = usePreferences();
  const zh = language === "zh-CN";
  const details = event.details ?? {};
  const isValidation = event.action.startsWith("tests.");
  if (!isValidation && !/^(patch\.|checkpoint\.)/.test(event.action)) return null;
  const files = [...new Set([
    ...strings(details.files), ...strings(details.applied_files), ...strings(details.restored_files),
  ])];
  const additions = count(details.additions);
  const deletions = count(details.deletions);
  const command = strings(details.command).join(" ");
  const workdir = text(details.workdir);
  const checkId = text(details.check_id);
  const checkpointId = text(details.checkpoint_id);
  const exitCode = typeof details.exit_code === "number" && Number.isInteger(details.exit_code) ? details.exit_code : null;
  const failures = Array.isArray(details.failed_tests) ? details.failed_tests.flatMap(item => {
    if (!item || typeof item !== "object") return [];
    const path = text(item.path);
    const message = text(item.message);
    const name = text(item.test_name);
    const line = typeof item.line === "number" && Number.isInteger(item.line) && item.line > 0 ? item.line : 1;
    return path || message || name ? [{ path, message, name, line }] : [];
  }) : [];
  const labels: Record<string, string> = zh
    ? { passed: "通过", failed: "失败", timed_out: "超时", not_found: "未找到检查", blocked: "无法执行", cancelled: "已取消", stale: "需重新验证", pending: "待运行" }
    : { passed: "Passed", failed: "Failed", timed_out: "Timed out", not_found: "Check not found", blocked: "Blocked", cancelled: "Cancelled", stale: "Needs revalidation", pending: "Pending" };
  const reportedStatus = text(details.status);
  const status = isValidation ? (reportedStatus && labels[reportedStatus] ? reportedStatus : event.status === "succeeded" ? "passed" : event.status === "failed" ? "failed" : "pending") : null;
  if (!files.length && additions === null && deletions === null && !command && !workdir && !checkId && !checkpointId && exitCode === null && !failures.length && !status) return null;
  const fileLink = (path: string, line = 1) => onOpenFile
    ? <button type="button" className="chat-event-file" title={path} data-path={path} data-line={line} onClick={() => onOpenFile(path, line)}><FileCode2 size={12} aria-hidden="true" /><span>{path}{line > 1 ? `:${line}` : ""}</span></button>
    : <span className="chat-event-file"><FileCode2 size={12} aria-hidden="true" /><span>{path}{line > 1 ? `:${line}` : ""}</span></span>;
  return <details className="chat-event-details" open={failures.length > 0}>
    <summary>
      <span>{isValidation ? (event.action === "tests.baseline" ? (zh ? "修改前验证" : "Pre-change validation") : (zh ? "验证详情" : "Validation details")) : files.length ? (zh ? `${files.length} 个文件` : `${files.length} file${files.length === 1 ? "" : "s"}`) : (zh ? "改动详情" : "Change details")}</span>
      {status ? <span className="chat-event-status" data-status={status}>{labels[status]}</span> : null}
      {additions !== null ? <span className="chat-event-added" aria-label={zh ? `新增 ${additions} 行` : `${additions} added lines`}>+{additions}</span> : null}
      {deletions !== null ? <span className="chat-event-deleted" aria-label={zh ? `删除 ${deletions} 行` : `${deletions} deleted lines`}>−{deletions}</span> : null}
      <ChevronDown size={12} aria-hidden="true" />
    </summary>
    <div className="chat-event-content">
      {command || workdir || checkId || checkpointId || exitCode !== null ? <dl className="chat-event-metadata">
        {checkId ? <div><dt>{zh ? "检查" : "Check"}</dt><dd>{checkId}</dd></div> : null}
        {workdir ? <div><dt>{zh ? "目录" : "Directory"}</dt><dd><code>{workdir}</code></dd></div> : null}
        {command ? <div><dt>{zh ? "命令" : "Command"}</dt><dd><code>{command}</code></dd></div> : null}
        {exitCode !== null ? <div><dt>{zh ? "退出码" : "Exit code"}</dt><dd>{exitCode}</dd></div> : null}
        {checkpointId ? <div><dt>{zh ? "检查点" : "Checkpoint"}</dt><dd><code>{checkpointId}</code></dd></div> : null}
      </dl> : null}
      {files.length ? <ul className="chat-event-files">{files.slice(0, 50).map(path => <li key={path}>{fileLink(path)}</li>)}{files.length > 50 ? <li className="chat-event-more">{zh ? `另有 ${files.length - 50} 个文件` : `${files.length - 50} more files`}</li> : null}</ul> : null}
      {failures.length ? <ul className="chat-event-failures">{failures.slice(0, 10).map((failure, index) => <li key={index}>
        {failure.path ? fileLink(failure.path, failure.line) : null}
        {failure.name ? <code className="chat-event-test-name">{failure.name}</code> : null}
        {failure.message ? <p>{failure.message.slice(0, 800)}{failure.message.length > 800 ? "…" : ""}</p> : null}
      </li>)}{failures.length > 10 ? <li className="chat-event-more">{zh ? `另有 ${failures.length - 10} 条失败，请查看执行记录` : `${failures.length - 10} more failures; see the execution record`}</li> : null}</ul> : null}
    </div>
  </details>;
}
