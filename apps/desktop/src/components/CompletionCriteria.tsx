import { useEffect, useState } from "react";
import { Check, Plus, Trash2 } from "lucide-react";
import { confirmCompletion, saveCompletionCriteria, type AgentState, type AgentTask, type CompletionCriterion, type CompletionConfirmation } from "../lib/api";
import { describeError } from "../lib/errorMessages";
import { usePreferences } from "../lib/preferences";

type CompletionCriteriaProps = {
  taskId: string;
  goal: string;
  criteria: CompletionCriterion[];
  confirmation: CompletionConfirmation | null;
  currentIteration: number;
  preparation?: boolean;
  state?: AgentState | null;
  disabled: boolean;
  onSaved: (task: AgentTask) => void;
  onBusyChange: (busy: boolean) => void;
  onDirtyChange?: (dirty: boolean) => void;
};

export function CompletionCriteria({ taskId, goal, criteria, confirmation, currentIteration, preparation = false, state, disabled, onSaved, onBusyChange, onDirtyChange }: CompletionCriteriaProps) {
  const { language } = usePreferences();
  const text = (zh: string, en: string) => language === "zh-CN" ? zh : en;
  const [draft, setDraft] = useState(criteria);
  const [evidence, setEvidence] = useState<Record<string, string>>(() => Object.fromEntries((confirmation?.evidence ?? []).map(item => [item.criterion_id, item.evidence])));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const savedCriteria = JSON.stringify(criteria);
  const activeState = state?.task.id === taskId ? state : null;
  const displayedCriteria = preparation ? draft : activeState?.task.completion_criteria ?? criteria;
  const savedConfirmation = activeState ? activeState.task.completion_confirmation : confirmation;
  const savedEvidence = JSON.stringify(savedConfirmation?.evidence ?? []);
  const locked = currentIteration > 0;
  const changed = JSON.stringify(draft) !== savedCriteria;
  const unavailable = disabled || saving || locked;
  const canConfirm = !preparation && !disabled && !saving && activeState?.can_confirm_completion === true
    && activeState.completion_status !== "confirmed" && Boolean(activeState.iteration?.id)
    && Boolean(activeState.completion_scope_hash && activeState.completion_validation_hash)
    && displayedCriteria.length > 0 && displayedCriteria.every(item => Boolean(evidence[item.id]?.trim()));

  useEffect(() => { setDraft(JSON.parse(savedCriteria) as CompletionCriterion[]); }, [savedCriteria]);
  useEffect(() => {
    setEvidence(Object.fromEntries((JSON.parse(savedEvidence) as CompletionConfirmation["evidence"]).map(item => [item.criterion_id, item.evidence])));
  }, [savedEvidence]);
  useEffect(() => { if (preparation) onDirtyChange?.(changed); }, [preparation, changed, onDirtyChange]);

  const update = (id: string, value: string) => {
    onDirtyChange?.(true);
    setDraft(items => items.map(item => item.id === id ? { ...item, text: value } : item));
    setSaved(false);
  };

  const save = async () => {
    if (unavailable || !changed || draft.some(item => !item.text.trim())) return;
    setSaving(true);
    onBusyChange(true);
    setError(null);
    setSaved(false);
    try {
      const task = await saveCompletionCriteria(taskId, draft.map(item => ({ ...item, text: item.text.trim() })));
      setDraft(task.completion_criteria);
      onSaved(task);
      onDirtyChange?.(false);
      setSaved(true);
    } catch (reason) {
      setError(describeError(reason, text("保存完成条件失败。", "Could not save completion criteria.")));
    } finally {
      setSaving(false);
      onBusyChange(false);
    }
  };

  const confirm = async () => {
    if (!canConfirm || !activeState?.iteration || !activeState.completion_scope_hash || !activeState.completion_validation_hash) return;
    setSaving(true);
    onBusyChange(true);
    setError(null);
    try {
      const task = await confirmCompletion(taskId, {
        scope_hash: activeState.completion_scope_hash,
        validation_hash: activeState.completion_validation_hash,
        iteration_id: activeState.iteration.id,
        evidence: displayedCriteria.map(item => ({ criterion_id: item.id, evidence: evidence[item.id].trim() })),
      });
      onSaved(task);
    } catch (reason) {
      setError(describeError(reason, text("确认完成条件失败。", "Could not confirm completion criteria.")));
    } finally {
      setSaving(false);
      onBusyChange(false);
    }
  };

  if (!preparation && !criteria.length) return null;
  return <form aria-label={text("完成条件", "Completion criteria")} onSubmit={event => { event.preventDefault(); void (preparation ? save() : confirm()); }}>
    <div className="test-result-top" style={{ margin: "14px 0 8px" }}>
      <strong>{text("完成条件", "Completion criteria")}</strong>
      {!preparation ? <span>{text(activeState?.completion_status === "confirmed" ? "已确认" : activeState?.completion_status === "stale" ? "需要重新复核" : "待复核", activeState?.completion_status === "confirmed" ? "Confirmed" : activeState?.completion_status === "stale" ? "Needs review again" : "Pending review")}</span> : null}
    </div>
    {!preparation ? <><p className="empty-detail"><strong>{text("原始目标", "Original goal")}</strong> · {goal}</p><p className="empty-detail">{text("逐条说明当前改动如何满足条件，再明确确认。验证通过不会自动确认完成。", "Explain how the current changes meet every criterion, then explicitly confirm. Passing validation does not confirm completion.")}</p></> : <p className="empty-detail">{text(locked ? "首次迭代已开始，完成条件已锁定。" : "填写可验收的结果。条件在首次迭代开始后锁定；留空沿用原有流程。", locked ? "Completion criteria are locked after the first iteration starts." : "Describe verifiable outcomes. Criteria lock after the first iteration starts; an empty list keeps the existing workflow.")}</p>}
    {displayedCriteria.map((item, index) => <fieldset key={item.id} disabled={preparation ? unavailable : disabled || saving || activeState?.completion_status === "confirmed"} style={{ border: "1px solid var(--line)", borderRadius: 6, padding: 12, margin: "10px 0", minWidth: 0 }}>
      <legend>{index + 1}. {text("完成条件", "Completion criterion")}</legend>
      {preparation && !locked ? <div className="validation-options"><label>{text("预期结果", "Expected outcome")}<input value={item.text} required maxLength={500} onChange={event => update(item.id, event.target.value)} /></label><button className="small-action small-action--muted" type="button" aria-label={`${text("移除条件", "Remove criterion")} ${index + 1}`} onClick={() => { onDirtyChange?.(true); setDraft(items => items.filter(entry => entry.id !== item.id)); setSaved(false); }}><Trash2 size={13} />{text("移除", "Remove")}</button></div> : <>
        <p className="empty-detail">{item.text}</p>
        {!preparation ? <label>{text("完成证据", "Completion evidence")}<textarea value={evidence[item.id] ?? ""} required maxLength={1000} rows={3} style={{ width: "100%" }} placeholder={text("说明结果，并引用相关文件或验证记录。", "Explain the result and reference relevant files or validation records.")} onChange={event => setEvidence(current => ({ ...current, [item.id]: event.target.value }))} /></label> : null}
      </>}
    </fieldset>)}
    {preparation && !locked ? <div className="patch-form-actions" style={{ marginTop: 12 }}>
      <button className="small-action small-action--muted" type="button" disabled={unavailable || draft.length >= 12} onClick={() => { onDirtyChange?.(true); setDraft(items => [...items, { id: crypto.randomUUID(), text: "" }]); setSaved(false); }}><Plus size={13} />{text("添加条件", "Add criterion")}</button>
      <button className="small-action" type="submit" disabled={unavailable || !changed || draft.some(item => !item.text.trim())}>{text(saving ? "处理中" : "保存完成条件", saving ? "Working" : "Save completion criteria")}</button>
      {changed ? <span>{text("有未保存的完成条件", "Unsaved completion criteria")}</span> : null}
    </div> : null}
    {!preparation && activeState?.completion_status === "stale" ? <p className="inline-error" role="status">{text("目标范围、迭代或验证结果已改变。请复核证据并重新确认。", "The scope, iteration or validation changed. Review the evidence and confirm again.")}</p> : null}
    {!preparation && activeState?.completion_status !== "confirmed" ? <div className="patch-form-actions"><button className="small-action" type="submit" disabled={!canConfirm}>{text(saving ? "处理中" : "确认完成条件", saving ? "Working" : "Confirm completion criteria")}</button>{!activeState?.can_confirm_completion ? <span>{text("等待当前改动的必要验证通过。", "Waiting for required validation of the current changes.")}</span> : null}</div> : null}
    {saved ? <p className="success-note" role="status"><Check size={13} />{text("完成条件已保存。", "Completion criteria saved.")}</p> : null}
    {error ? <p className="inline-error" role="alert">{error}</p> : null}
  </form>;
}
