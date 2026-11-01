import { useEffect, useRef, useState } from "react";
import { Check, Plus, Trash2 } from "lucide-react";
import { saveTaskPlan, type AgentTask, type CompletionCriterion } from "../lib/api";
import { describeError } from "../lib/errorMessages";
import { usePreferences } from "../lib/preferences";

type Plan = NonNullable<AgentTask["plan"]>;
type Props = {
  taskId: string;
  plan: Plan;
  criteria: CompletionCriterion[];
  currentIteration: number;
  disabled: boolean;
  onSaved: (task: AgentTask) => void;
  onBusyChange: (busy: boolean) => void;
  onDirtyChange?: (dirty: boolean) => void;
};

export function TaskPlanEditor({ taskId, plan, criteria, currentIteration, disabled, onSaved, onBusyChange, onDirtyChange }: Props) {
  const { t } = usePreferences();
  const source = JSON.stringify(plan);
  const [draft, setDraft] = useState(plan);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const requestRef = useRef<AbortController | null>(null);
  const covered = plan.steps.every(step => criteria.some(criterion => criterion.step_id === step.id));
  const changed = JSON.stringify(draft) !== source || !covered;
  const locked = currentIteration > 0;
  const unavailable = disabled || saving || locked;
  const valid = Boolean(draft.objective.trim()) && draft.steps.length > 0 && draft.steps.every(step => step.title.trim() && step.description.trim());
  const availableSteps = 12 - criteria.filter(criterion => !criterion.step_id).length;

  useEffect(() => { setDraft(JSON.parse(source) as Plan); setSaved(false); }, [source]);
  useEffect(() => { onDirtyChange?.(changed && !locked); }, [changed, locked, onDirtyChange]);
  useEffect(() => () => { requestRef.current?.abort(); onBusyChange(false); onDirtyChange?.(false); }, [taskId, onBusyChange, onDirtyChange]);

  const update = (id: string, changes: Partial<Plan["steps"][number]>) => {
    setDraft(current => ({ ...current, steps: current.steps.map(step => step.id === id ? { ...step, ...changes } : step) }));
    setSaved(false);
  };
  const save = async () => {
    if (unavailable || !changed || !valid || requestRef.current) return;
    const controller = new AbortController();
    requestRef.current = controller;
    setSaving(true); onBusyChange(true); setError(null); setSaved(false);
    try {
      const task = await saveTaskPlan(taskId, { ...draft, steps: draft.steps.map(step => ({ ...step,
        files: step.files.map(path => path.trim()).filter(Boolean),
        verification: step.verification.map(item => item.trim()).filter(Boolean),
      })) }, controller.signal);
      if (controller.signal.aborted) return;
      if (task.plan) setDraft(task.plan);
      onSaved(task); onDirtyChange?.(false); setSaved(true);
    } catch (reason) {
      if (!controller.signal.aborted) setError(describeError(reason, t("plan.saveError")));
    } finally {
      requestRef.current = null;
      if (!controller.signal.aborted) { setSaving(false); onBusyChange(false); }
    }
  };

  return <form aria-label={t("plan.editTitle")} onSubmit={event => { event.preventDefault(); void save(); }}>
    <div className="test-result-top" style={{ margin: "14px 0 8px" }}><strong>{t("plan.editTitle")}</strong><span>{draft.steps.length} / 12</span></div>
    <p className="empty-detail">{t(locked ? "plan.lockedHint" : "plan.editHint")}</p>
    <fieldset disabled={unavailable} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
      <label>{t("plan.objective")}<textarea rows={2} value={draft.objective} required maxLength={32000} style={{ width: "100%" }} onChange={event => { setDraft(current => ({ ...current, objective: event.target.value })); setSaved(false); }} /></label>
      {draft.steps.map((step, index) => <fieldset key={step.id} style={{ border: "1px solid var(--line)", borderRadius: 6, padding: 12, margin: "10px 0", minWidth: 0 }}>
        <legend>{index + 1}. {t("plan.step")}</legend>
        <div className="validation-options"><label>{t("plan.stepTitle")}<input value={step.title} required maxLength={120} onChange={event => update(step.id, { title: event.target.value })} /></label><button type="button" className="small-action small-action--muted" disabled={draft.steps.length <= 1} aria-label={`${t("plan.removeStep")} ${index + 1}`} onClick={() => { setDraft(current => ({ ...current, steps: current.steps.filter(item => item.id !== step.id) })); setSaved(false); }}><Trash2 size={13} />{t("completion.remove")}</button></div>
        <label>{t("plan.description")}<textarea rows={2} value={step.description} required maxLength={500} style={{ width: "100%" }} onChange={event => update(step.id, { description: event.target.value })} /></label>
        <div className="validation-options"><label>{t("plan.files")}<input value={step.files.join(",")} maxLength={2000} onChange={event => update(step.id, { files: event.target.value.split(",") })} /></label><label>{t("plan.verification")}<input value={step.verification.join(";")} maxLength={2000} onChange={event => update(step.id, { verification: event.target.value.split(";") })} /></label><label>{t("plan.risk")}<select value={step.risk} onChange={event => update(step.id, { risk: event.target.value as Plan["steps"][number]["risk"] })}>{["low", "medium", "high"].map(risk => <option key={risk} value={risk}>{t("risk." + risk)}</option>)}</select></label></div>
      </fieldset>)}
      {!locked ? <div className="patch-form-actions"><button type="button" className="small-action small-action--muted" disabled={draft.steps.length >= availableSteps} onClick={() => { setDraft(current => ({ ...current, steps: [...current.steps, { id: crypto.randomUUID(), title: "", description: "", files: [], verification: [], risk: "low" }] })); setSaved(false); }}><Plus size={13} />{t("plan.addStep")}</button><button type="submit" className="small-action" disabled={!changed || !valid}>{t(saving ? "working" : "plan.save")}</button>{changed ? <span>{t("plan.unsaved")}</span> : null}</div> : null}
    </fieldset>
    {saved ? <p className="success-note" role="status"><Check size={13} />{t("plan.saved")}</p> : null}
    {error ? <p className="inline-error" role="alert">{error}</p> : null}
  </form>;
}
