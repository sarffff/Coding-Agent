import { ChevronDown, FileCode2, ListChecks, ShieldCheck } from "lucide-react";
import type { AgentState, TestRunResult, ValidationCheckStatus } from "../lib/api";
import { usePreferences } from "../lib/preferences";
import { ChatIterationDiff } from "./ChatIterationDiff";
import "../styles/chat-run-summary.css";

type ChatRunSummaryProps = {
  state: AgentState | null;
  onOpenFile: (path: string, line?: number) => void;
  onOpenRun: () => void;
  onOpenValidation: () => void;
  onReviewCompletion?: () => void;
  busy?: boolean;
};

const words = {
  "zh-CN": {
    title: "执行摘要", iteration: "轮次", files: "改动文件", checks: "检查通过", criteria: "完成条件",
    run: "执行记录", noFiles: "当前轮次尚无文件改动。", rolledBack: "本轮改动已回滚。",
    validation: "必跑检查", automatic: "自动检测", notRun: "尚未执行检查。", reviewValidation: "查看验证与日志",
    workdir: "目录", target: "目标", timeout: "超时", seconds: "秒", failures: "失败定位", outputPruned: "输出已按保留策略清理。",
    outputTruncated: "输出已截断，完整结果请查看执行记录。", pending: "待运行", passed: "通过", failed: "失败",
    timed_out: "超时", not_found: "未找到检查", blocked: "无法执行", cancelled: "已取消", stale: "需重新验证",
    staleHint: "代码或验证输入已改变，旧结果不计入通过。", completion: "完成条件", not_required: "未设置",
    completionPending: "待确认", completionStale: "需重新确认", completionConfirmed: "已确认", reviewCompletion: "复核完成条件",
    completionHint: "检查通过后仍需逐条填写证据并明确确认。", completionStaleHint: "代码、范围或验证已改变，旧确认失效。",
    noCriteria: "未设置完成条件。",
  },
  "en-US": {
    title: "Execution summary", iteration: "Iteration", files: "Changed files", checks: "Checks passed", criteria: "Completion criteria",
    run: "Run history", noFiles: "No file changes in this iteration yet.", rolledBack: "This iteration's changes were rolled back.",
    validation: "Required checks", automatic: "Automatic check", notRun: "No checks have run yet.", reviewValidation: "View validation and logs",
    workdir: "Directory", target: "Target", timeout: "Timeout", seconds: "s", failures: "Failure locations", outputPruned: "Output was removed by the retention policy.",
    outputTruncated: "Output was truncated; open run history for the full result.", pending: "Pending", passed: "Passed", failed: "Failed",
    timed_out: "Timed out", not_found: "Check not found", blocked: "Blocked", cancelled: "Cancelled", stale: "Needs revalidation",
    staleHint: "Code or validation inputs changed; old results do not count as passed.", completion: "Completion criteria", not_required: "Not configured",
    completionPending: "Awaiting confirmation", completionStale: "Needs reconfirmation", completionConfirmed: "Confirmed", reviewCompletion: "Review completion criteria",
    completionHint: "After checks pass, provide evidence for each criterion and confirm explicitly.", completionStaleHint: "Code, scope or validation changed; the earlier confirmation is no longer valid.",
    noCriteria: "No completion criteria configured.",
  },
};

export function ChatRunSummary({ state, onOpenFile, onOpenRun, onOpenValidation, onReviewCompletion, busy = false }: ChatRunSummaryProps) {
  const { language } = usePreferences();
  const text = words[language];
  if (!state || state.task.track !== "code") return null;
  const iteration = state.iteration?.task_id === state.task.id ? state.iteration : null;
  const files = [...new Set(iteration?.changed_files ?? [])];
  const statuses: ValidationCheckStatus[] = state.task.validation_checks.map(check =>
    state.validation_status.find(item => item.check.id === check.id) ?? { check, status: "pending", result: null });
  const lastTest = iteration && state.last_test?.run_id === state.task.run_id && state.last_test?.id === iteration.test_result?.id ? state.last_test : null;
  let automaticStatus: ValidationCheckStatus["status"] = lastTest?.status ?? "pending";
  if (automaticStatus === "passed" && (iteration?.rolled_back || state.action === "run_tests" || !lastTest?.validation_hash
      || (state.completion_validation_hash && lastTest.validation_hash !== state.completion_validation_hash))) automaticStatus = "stale";
  const checks = statuses.length ? statuses : [{ check: null, status: automaticStatus, result: lastTest }];
  const passed = checks.filter(item => item.status === "passed").length;
  const criteria = state.task.completion_criteria;
  const completion = state.completion_status;
  const completionLabel = completion === "confirmed" ? text.completionConfirmed : completion === "stale" ? text.completionStale : completion === "pending" ? text.completionPending : text.not_required;
  const failureResults = checks.flatMap(item => item.result && item.status !== "pending" && item.status !== "stale" ? [item.result] : []);
  const failures = failureResults.flatMap(result => result.failed_tests.map(failure => ({ ...failure, resultId: result.id })));
  const command = (result: TestRunResult | null, script?: string | null, target?: string | null) => result?.command.length ? result.command.join(" ") : script ? `package.json · ${script}` : target || text.automatic;

  return <section className="chat-run-summary" aria-label={text.title} data-task-id={state.task.id}>
    <header className="chat-run-summary-header"><strong>{text.title}</strong><span>{text.iteration} {iteration?.number ?? 0}</span><button type="button" onClick={onOpenRun}>{text.run}</button></header>
    <details key={state.task.id}>
      <summary>
        <span className="chat-run-summary-metric"><FileCode2 size={14} aria-hidden="true" /><span>{text.files}</span><strong>{iteration?.rolled_back ? 0 : files.length}</strong></span>
        <span className="chat-run-summary-metric"><ListChecks size={14} aria-hidden="true" /><span>{text.checks}</span><strong>{passed}/{checks.length}</strong></span>
        <span className="chat-run-summary-metric" data-status={completion}><ShieldCheck size={14} aria-hidden="true" /><span>{text.criteria}</span><strong>{criteria.length ? `${completion === "confirmed" ? criteria.length : 0}/${criteria.length}` : "—"}</strong></span>
        <ChevronDown className="chat-run-summary-chevron" size={14} aria-hidden="true" />
      </summary>
      <div className="chat-run-summary-body">
        <div className="chat-run-summary-group"><h3>{text.files}</h3>{iteration?.rolled_back ? <p className="chat-run-summary-note">{text.rolledBack}</p> : null}
          {files.length ? <ul className={iteration?.rolled_back ? "chat-run-files chat-run-files--restored" : "chat-run-files"}>{files.map(path => <li key={path}><button type="button" onClick={() => onOpenFile(path)} title={path}><FileCode2 size={13} aria-hidden="true" /><span>{path}</span></button></li>)}</ul> : <p className="chat-run-summary-note">{text.noFiles}</p>}
          {iteration?.checkpoint_id ? <ChatIterationDiff key={`${state.task.run_id}:${iteration.checkpoint_id}`} runId={state.task.run_id} checkpointId={iteration.checkpoint_id} rolledBack={iteration.rolled_back} /> : null}
        </div>
        <div className="chat-run-summary-group"><div className="chat-run-summary-group-heading"><h3>{statuses.length ? text.validation : text.automatic}</h3><button type="button" onClick={onOpenValidation}>{text.reviewValidation}</button></div>
          <ul className="chat-run-checks">{checks.map((item, index) => <li key={item.check?.id ?? "automatic"}>
            <div className="chat-run-check-heading"><strong>{item.check?.label ?? text.automatic}</strong><span className="chat-run-status" data-status={item.status}>{text[item.status]}</span></div>
            <code title={command(item.result, item.check?.script, item.check?.target)}>{command(item.result, item.check?.script, item.check?.target)}</code>
            <p className="chat-run-check-options"><span>{text.workdir}: <code>{item.check?.workdir ?? item.result?.workdir ?? "."}</code></span>{item.check?.target ? <span>{text.target}: <code>{item.check.target}</code></span> : null}{item.check ? <span>{text.timeout}: {item.check.timeout_seconds}{text.seconds}</span> : null}</p>
            {item.status === "stale" ? <p className="chat-run-summary-note">{text.staleHint}</p> : !item.result && index === 0 ? <p className="chat-run-summary-note">{text.notRun}</p> : null}
            {item.result?.output_pruned ? <p className="chat-run-summary-note">{text.outputPruned}</p> : item.result?.output_truncated ? <p className="chat-run-summary-note">{text.outputTruncated}</p> : null}
          </li>)}</ul>
          {failures.length ? <div className="chat-run-failures"><h4>{text.failures}</h4><ul>{failures.map((failure, index) => <li key={`${failure.resultId}-${index}`}>
            {failure.path ? <button type="button" onClick={() => onOpenFile(failure.path!, failure.line ?? undefined)} title={failure.path}><code>{failure.path}{failure.line ? `:${failure.line}` : ""}</code></button> : failure.test_name ? <strong>{failure.test_name}</strong> : null}
            <p>{failure.message}</p>
          </li>)}</ul></div> : null}
        </div>
        <div className="chat-run-summary-group"><div className="chat-run-summary-group-heading"><h3>{text.completion}</h3><span className="chat-run-status" data-status={completion}>{completionLabel}</span></div>
          {criteria.length ? <><ol className="chat-run-criteria">{criteria.map(criterion => <li key={criterion.id}>{criterion.text}</li>)}</ol>
            {completion !== "confirmed" ? <p className="chat-run-summary-note">{completion === "stale" ? text.completionStaleHint : text.completionHint}</p> : null}
            {onReviewCompletion ? <button type="button" className="chat-run-review-completion" disabled={busy} onClick={onReviewCompletion}>{text.reviewCompletion}</button> : null}
          </> : <p className="chat-run-summary-note">{text.noCriteria}</p>}
        </div>
      </div>
    </details>
  </section>;
}
