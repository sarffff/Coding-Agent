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
  const { t } = usePreferences();
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
      setError(describeError(reason, t("completion.saveError")));
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
      setError(describeError(reason, t("completion.confirmError")));
    } finally {
      setSaving(false);
      onBusyChange(false);
    }
  };

  if (!preparation && !criteria.length) return null;
  return <form aria-label={t("completion.title")} onSubmit={event => { event.preventDefault(); void (preparation ? save() : confirm()); }}>
    <div className="test-result-top" style={{ margin: "14px 0 8px" }}>
      <strong>{t("completion.title")}</strong>
      {!preparation ? <span>{t("completion." + (activeState?.completion_status === "confirmed" ? "confirmed" : activeState?.completion_status === "stale" ? "stale" : "pending"))}</span> : null}
    </div>
    {!preparation ? <><p className="empty-detail"><strong>{t("completion.goal")}</strong> · {goal}</p><p className="empty-detail">{t("completion.hint")}</p></> : <p className="empty-detail">{t(locked ? "completion.lockedHint" : "completion.prepHint")}</p>}
    {displayedCriteria.map((item, index) => <fieldset key={item.id} disabled={preparation ? unavailable : disabled || saving || activeState?.completion_status === "confirmed"} style={{ border: "1px solid var(--line)", borderRadius: 6, padding: 12, margin: "10px 0", minWidth: 0 }}>
      <legend>{index + 1}. {t("completion.criterion")}</legend>
      {preparation && !locked ? <div className="validation-options"><label>{t("completion.expected")}<input value={item.text} required maxLength={500} onChange={event => update(item.id, event.target.value)} /></label><button className="small-action small-action--muted" type="button" aria-label={`${t("completion.removeLabel")} ${index + 1}`} onClick={() => { onDirtyChange?.(true); setDraft(items => items.filter(entry => entry.id !== item.id)); setSaved(false); }}><Trash2 size={13} />{t("completion.remove")}</button></div> : <>
        <p className="empty-detail">{item.text}</p>
        {!preparation ? <label>{t("completion.evidence")}<textarea value={evidence[item.id] ?? ""} required maxLength={1000} rows={3} style={{ width: "100%" }} placeholder={t("completion.evidenceHint")} onChange={event => setEvidence(current => ({ ...current, [item.id]: event.target.value }))} /></label> : null}
      </>}
    </fieldset>)}
    {preparation && !locked ? <div className="patch-form-actions" style={{ marginTop: 12 }}>
      <button className="small-action small-action--muted" type="button" disabled={unavailable || draft.length >= 12} onClick={() => { onDirtyChange?.(true); setDraft(items => [...items, { id: crypto.randomUUID(), text: "" }]); setSaved(false); }}><Plus size={13} />{t("completion.add")}</button>
      <button className="small-action" type="submit" disabled={unavailable || !changed || draft.some(item => !item.text.trim())}>{t(saving ? "completion.working" : "completion.save")}</button>
      {changed ? <span>{t("completion.unsaved")}</span> : null}
    </div> : null}
    {!preparation && activeState?.completion_status === "stale" ? <p className="inline-error" role="status">{t("completion.staleError")}</p> : null}
    {!preparation && activeState?.completion_status !== "confirmed" ? <div className="patch-form-actions"><button className="small-action" type="submit" disabled={!canConfirm}>{t(saving ? "completion.working" : "completion.confirm")}</button>{!activeState?.can_confirm_completion ? <span>{t("completion.waiting")}</span> : null}</div> : null}
    {saved ? <p className="success-note" role="status"><Check size={13} />{t("completion.saved")}</p> : null}
    {error ? <p className="inline-error" role="alert">{error}</p> : null}
  </form>;
}
