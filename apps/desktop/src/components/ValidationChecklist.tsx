import { useEffect, useState } from "react";
import { Check, Plus, Trash2 } from "lucide-react";
import { getAgentState, saveValidationChecks, type AgentState, type AgentTask, type TestKind, type ValidationCheck } from "../lib/api";
import { usePreferences } from "../lib/preferences";

type ValidationChecklistProps = {
  taskId: string;
  checks: ValidationCheck[];
  currentIteration: number;
  revision: string;
  hidden: boolean;
  disabled: boolean;
  onSaved: (task: AgentTask) => void;
  onBusyChange: (busy: boolean) => void;
};

export function ValidationChecklist({ taskId, checks, currentIteration, revision, hidden, disabled, onSaved, onBusyChange }: ValidationChecklistProps) {
  const { t } = usePreferences();
  const [draft, setDraft] = useState(checks);
  const [state, setState] = useState<AgentState | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const savedChecks = JSON.stringify(checks);

  useEffect(() => { setDraft(JSON.parse(savedChecks) as ValidationCheck[]); }, [savedChecks]);

  useEffect(() => {
    if (hidden) return;
    const controller = new AbortController();
    let loading = false;
    const refresh = async () => {
      if (loading) return;
      loading = true;
      try {
        const next = await getAgentState(taskId, controller.signal);
        if (!controller.signal.aborted) { setState(next); setLoadError(null); }
      } catch (reason) {
        if (!controller.signal.aborted) setLoadError(reason instanceof Error ? reason.message : t("agent.loadError"));
      } finally {
        loading = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [taskId, revision, hidden, t]);

  const locked = currentIteration > 0 || (state?.task.current_iteration ?? 0) > 0;
  const changed = JSON.stringify(draft) !== savedChecks;
  const unavailable = disabled || saving || locked;
  const update = (id: string, values: Partial<ValidationCheck>) => {
    setDraft(items => items.map(item => item.id === id ? { ...item, ...values } : item));
    setSaved(false);
  };

  const save = async () => {
    if (unavailable || !changed) return;
    setSaving(true);
    onBusyChange(true);
    setError(null);
    setSaved(false);
    try {
      const task = await saveValidationChecks(taskId, draft.map(check => ({
        ...check,
        label: check.label.trim(),
        workdir: check.workdir.trim(),
        target: check.target?.trim() || null,
        script: check.script?.trim() || null,
      })));
      setDraft(task.validation_checks);
      onSaved(task);
      setSaved(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("validation.saveError"));
    } finally {
      setSaving(false);
      onBusyChange(false);
    }
  };

  const statuses = state?.validation_status ?? [];
  const displayedChecks = locked ? state?.task.validation_checks ?? checks : draft;
  return <form aria-label={t("validation.checklist")} onSubmit={event => { event.preventDefault(); void save(); }}>
    <div className="test-result-top" style={{ margin: "14px 0 8px" }}>
      <strong>{t("validation.checklist")}</strong>
      {checks.length > 0 ? <span>{statuses.filter(item => item.status === "passed").length}/{checks.length} {t("validation.passed")}</span> : null}
    </div>
    <p className="empty-detail">{t(locked ? "validation.locked" : "validation.checklistHint")}</p>
    {!displayedChecks.length ? <p className="empty-detail">{t("validation.emptyChecklist")}</p> : displayedChecks.map((check, index) => {
      const entry = statuses.find(item => item.check.id === check.id);
      return <fieldset key={check.id} disabled={unavailable} style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "10px 0 0", margin: "10px 0", minWidth: 0 }}>
        <legend style={{ marginLeft: 12, fontSize: 12 }}>{index + 1}. {locked ? check.label : t("validation.check")}</legend>
        {locked ? <div style={{ padding: "0 12px 12px" }}>
          <div className="test-result-top"><span>{t("validation." + check.kind)} · {check.workdir} · {check.timeout_seconds}s</span><span className={`result-badge result-badge--${entry?.status ?? "pending"}`}>{t("validation." + (entry?.status ?? "pending"))}</span></div>
          <code className="test-command">{entry?.result?.command.length ? entry.result.command.join(" ") : check.script || check.target || t("validation.auto")}</code>
          {entry?.status === "stale" ? <span className="output-note">{t("validation.staleHint")}</span> : null}
        </div> : <>
          <div className="validation-options">
            <label>{t("validation.name")}<input value={check.label} onChange={event => update(check.id, { label: event.target.value })} required maxLength={120} /></label>
            <label>{t("validation.kind")}<select value={check.kind} onChange={event => update(check.id, { kind: event.target.value as TestKind, script: null, target: null })}>{["auto", "pytest", "frontend", "static", "script"].map(kind => <option key={kind} value={kind}>{t("validation." + kind)}</option>)}</select></label>
          </div>
          <div className="validation-options">
            <label>{t("validation.workdir")}<input value={check.workdir} onChange={event => update(check.id, { workdir: event.target.value })} required maxLength={500} placeholder="apps/api" /></label>
            <label>{t(check.kind === "script" ? "validation.scriptName" : "validation.target")}<input value={(check.kind === "script" ? check.script : check.target) ?? ""} onChange={event => update(check.id, check.kind === "script" ? { script: event.target.value } : { target: event.target.value })} required={check.kind === "script"} maxLength={check.kind === "script" ? 100 : 500} pattern={check.kind === "script" ? "[A-Za-z0-9][A-Za-z0-9:_.-]*" : undefined} placeholder={check.kind === "script" ? "api:test" : "tests/test_app.py"} /></label>
          </div>
          <div className="validation-options">
            <label>{t("validation.timeout")}<input type="number" min={1} max={300} value={check.timeout_seconds || ""} onChange={event => update(check.id, { timeout_seconds: Number(event.target.value) })} required style={{ width: 76, flex: "none" }} /></label>
            <button className="small-action small-action--muted" type="button" onClick={() => { setDraft(items => items.filter(item => item.id !== check.id)); setSaved(false); }} aria-label={`${t("validation.remove")} ${check.label || index + 1}`}><Trash2 size={13} />{t("validation.remove")}</button>
          </div>
        </>}
      </fieldset>;
    })}
    {!locked ? <div className="patch-form-actions" style={{ marginTop: 12 }}>
      <button className="small-action small-action--muted" type="button" disabled={unavailable || draft.length >= 12} onClick={() => {
        setDraft(items => [...items, { id: crypto.randomUUID(), label: "", kind: "auto", workdir: ".", target: null, script: null, timeout_seconds: 120 }]);
        setSaved(false);
      }}><Plus size={13} />{t("validation.add")}</button>
      <button className="small-action" type="submit" disabled={unavailable || !changed}>{t(saving ? "working" : "validation.save")}</button>
      {changed ? <span>{t("validation.unsaved")}</span> : null}
    </div> : null}
    {saved ? <p className="success-note" role="status"><Check size={13} />{t("validation.saved")}</p> : null}
    {error ? <p className="inline-error" role="alert">{error}</p> : null}
    {loadError ? <p className="inline-error" role="alert">{loadError}</p> : null}
  </form>;
}
