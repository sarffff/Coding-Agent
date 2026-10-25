import { useEffect, useState } from "react";
import { KeyRound, Save } from "lucide-react";
import { getConfig, updateConfig, type AppConfig, type ConfigUpdate, type ModelProfileInput, type ModelProviderKind } from "../lib/api";
import { describeError } from "../lib/errorMessages";
import { usePreferences } from "../lib/preferences";

type Draft = {
  llm_provider: string;
  llm_model: string;
  anthropic_api_url: string;
  openai_api_url: string;
  anthropic_api_key: string;
  openai_api_key: string;
  llm_max_tokens: string;
  llm_timeout_seconds: string;
  command_timeout_seconds: string;
  llm_max_context_files: string;
  llm_max_context_bytes: string;
};

function toDraft(config: AppConfig): Draft {
  return {
    llm_provider: config.provider,
    llm_model: config.model,
    anthropic_api_url: config.anthropic_api_url,
    openai_api_url: config.openai_api_url,
    anthropic_api_key: "",
    openai_api_key: "",
    llm_max_tokens: String(config.llm_max_tokens),
    llm_timeout_seconds: String(config.llm_timeout_seconds),
    command_timeout_seconds: String(config.command_timeout_seconds),
    llm_max_context_files: String(config.llm_max_context_files),
    llm_max_context_bytes: String(config.llm_max_context_bytes),
  };
}

/** Only send what actually changed; an untouched key field must never overwrite the stored one. */
function diff(config: AppConfig, draft: Draft): ConfigUpdate {
  const patch: ConfigUpdate = {};
  if (draft.llm_provider !== config.provider) patch.llm_provider = draft.llm_provider;
  if (draft.llm_model.trim() !== config.model) patch.llm_model = draft.llm_model.trim();
  if (draft.anthropic_api_url.trim() !== config.anthropic_api_url) patch.anthropic_api_url = draft.anthropic_api_url.trim();
  if (draft.openai_api_url.trim() !== config.openai_api_url) patch.openai_api_url = draft.openai_api_url.trim();
  if (draft.anthropic_api_key) patch.anthropic_api_key = draft.anthropic_api_key;
  if (draft.openai_api_key) patch.openai_api_key = draft.openai_api_key;
  const numbers: Array<[keyof Draft, keyof ConfigUpdate]> = [
    ["llm_max_tokens", "llm_max_tokens"],
    ["llm_timeout_seconds", "llm_timeout_seconds"],
    ["command_timeout_seconds", "command_timeout_seconds"],
    ["llm_max_context_files", "llm_max_context_files"],
    ["llm_max_context_bytes", "llm_max_context_bytes"],
  ];
  for (const [draftKey, apiKey] of numbers) {
    const value = Number(draft[draftKey]);
    if (Number.isFinite(value) && value !== config[apiKey as keyof AppConfig]) {
      (patch as Record<string, number>)[apiKey as string] = value;
    }
  }
  return patch;
}

export function SettingsPanel() {
  const { t } = usePreferences();
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [modelError, setModelError] = useState<string | null>(null);
  const [modelBusy, setModelBusy] = useState(false);
  const models = config?.models ?? [];
  const [newModel, setNewModel] = useState({ label: "", provider: "openai" as ModelProviderKind, model: "", base_url: "", api_key: "" });

  useEffect(() => {
    const controller = new AbortController();
    getConfig(controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return;
        setConfig(next);
        setDraft(toDraft(next));
        setError(null);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(describeError(reason, t("settings.loadError")));
      });
    return () => controller.abort();
  }, [t]);

  const set = (key: keyof Draft) => (event: { target: { value: string } }) => {
    setSaved(false);
    setDraft((current) => (current ? { ...current, [key]: event.target.value } : current));
  };

  const save = async () => {
    if (!config || !draft || busy) return;
    const patch = diff(config, draft);
    if (!Object.keys(patch).length) {
      setSaved(true);
      setError(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const next = await updateConfig(patch);
      setConfig(next);
      setDraft(toDraft(next));
      setSaved(true);
    } catch (reason) {
      setError(describeError(reason, t("settings.saveError")));
    } finally {
      setBusy(false);
    }
  };

  const saveModels = async (models: ModelProfileInput[], activeModelId?: string) => {
    setModelBusy(true);
    setModelError(null);
    try {
      const next = await updateConfig({ models, ...(activeModelId ? { active_model_id: activeModelId } : {}) });
      setConfig(next);
      setDraft(toDraft(next));
    } catch (reason) {
      setModelError(describeError(reason, t("settings.models.saveError")));
    } finally {
      setModelBusy(false);
    }
  };

  const modelInput = (item: AppConfig["models"][number]): ModelProfileInput => ({
    id: item.id, label: item.label, provider: item.provider, model: item.model, base_url: item.base_url,
  });

  const addModel = async () => {
    if (!config || modelBusy) return;
    if (!newModel.label.trim() || !newModel.model.trim()) {
      setModelError(t("settings.models.required"));
      return;
    }
    const id = `m-${Date.now().toString(36)}`;
    const entry: ModelProfileInput = {
      id, label: newModel.label.trim(), provider: newModel.provider, model: newModel.model.trim(),
      base_url: newModel.base_url.trim(), api_key: newModel.api_key || undefined,
    };
    await saveModels([...models.map(modelInput), entry], id);
    setNewModel({ label: "", provider: "openai", model: "", base_url: "", api_key: "" });
  };

  const removeModel = async (item: AppConfig["models"][number]) => {
    if (!config || modelBusy) return;
    if (!window.confirm(t("settings.models.confirmRemove").replace("{name}", item.label))) return;
    await saveModels(models.filter((entry) => entry.id !== item.id).map(modelInput));
  };

  const activateModel = async (id: string) => {
    if (!config || modelBusy || config.active_model_id === id) return;
    await saveModels(models.map(modelInput), id);
  };

  if (!config || !draft) {
    return (
      <section className="panel settings-panel">
        <div className="panel-header"><div><div className="section-kicker"><KeyRound size={13} /> {t("settings.title")}</div><h2>{t("settings.subtitle")}</h2></div></div>
        {error ? <div className="inline-error" role="alert">{error}</div> : <p className="settings-note">{t("common.loading")}</p>}
      </section>
    );
  }

  return (
    <section className="panel settings-panel">
      <div className="panel-header">
        <div><div className="section-kicker"><KeyRound size={13} /> {t("settings.title")}</div><h2>{t("settings.subtitle")}</h2></div>
        <button className="small-action" onClick={() => void save()} disabled={busy}>
          <Save size={13} /> {saved ? t("settings.saved") : t("settings.save")}
        </button>
      </div>

      {error ? <div className="inline-error" role="alert">{error}</div> : null}
      {config.restart_required ? <p className="settings-note">{t("settings.restartRequired")}</p> : null}

      <section className="settings-models">
        <div className="settings-models-head">
          <h3>{t("settings.models.title")}</h3>
          <span>{t("settings.models.hint")}</span>
        </div>
        {models.length ? (
          <ul className="settings-model-list">
            {models.map((item) => (
              <li key={item.id} className={item.id === config.active_model_id ? "is-active" : ""}>
                <div className="settings-model-copy">
                  <strong>{item.label}</strong>
                  <span>{item.provider} · {item.model}</span>
                  <small>{item.base_url || t("settings.models.defaultUrl")} · {item.api_key_configured ? t("settings.models.keySet") : t("settings.models.keyMissing")}</small>
                </div>
                <div className="settings-model-actions">
                  {item.id === config.active_model_id
                    ? <span className="settings-model-badge">{t("settings.models.active")}</span>
                    : <button type="button" className="small-action small-action--muted" disabled={modelBusy} onClick={() => void activateModel(item.id)}>{t("settings.models.use")}</button>}
                  <button type="button" className="text-button" disabled={modelBusy} onClick={() => void removeModel(item)}>{t("settings.models.remove")}</button>
                </div>
              </li>
            ))}
          </ul>
        ) : <p className="settings-note">{t("settings.models.empty")}</p>}
        <div className="settings-model-add">
          <input value={newModel.label} onChange={(event) => setNewModel({ ...newModel, label: event.target.value })} placeholder={t("settings.models.label")} aria-label={t("settings.models.label")} maxLength={80} />
          <select value={newModel.provider} onChange={(event) => setNewModel({ ...newModel, provider: event.target.value as ModelProviderKind })} aria-label={t("settings.models.provider")}>
            <option value="openai">openai</option>
            <option value="anthropic">anthropic</option>
            <option value="fake">{t("settings.fake")}</option>
          </select>
          <input value={newModel.model} onChange={(event) => setNewModel({ ...newModel, model: event.target.value })} placeholder={t("settings.models.model")} aria-label={t("settings.models.model")} maxLength={200} />
          <input value={newModel.base_url} onChange={(event) => setNewModel({ ...newModel, base_url: event.target.value })} placeholder={t("settings.models.baseUrl")} aria-label={t("settings.models.baseUrl")} maxLength={500} />
          <input type="password" autoComplete="off" value={newModel.api_key} onChange={(event) => setNewModel({ ...newModel, api_key: event.target.value })} placeholder={t("settings.models.apiKey")} aria-label={t("settings.models.apiKey")} maxLength={500} />
          <button type="button" className="small-action" disabled={modelBusy} onClick={() => void addModel()}>{t("settings.models.add")}</button>
        </div>
        {modelError ? <p className="inline-error" role="alert">{modelError}</p> : null}
      </section>

      <div className="settings-grid">
        {models.length ? <p className="settings-note settings-note--span">{t("settings.models.controls")}</p> : null}
        {models.length ? null : <>
        <label className="settings-field">
          <span>{t("settings.provider")}</span>
          <select value={draft.llm_provider} onChange={set("llm_provider")} disabled={busy}>
            <option value="anthropic">anthropic</option>
            <option value="openai">openai</option>
            <option value="fake">{t("settings.fake")}</option>
          </select>
        </label>
        <label className="settings-field">
          <span>{t("settings.model")}</span>
          <input value={draft.llm_model} onChange={set("llm_model")} disabled={busy} spellCheck={false} />
        </label>

        {models.length ? null : <>
        <label className="settings-field settings-field--wide">
          <span>{t("settings.anthropicKey")}</span>
          <input
            type="password"
            value={draft.anthropic_api_key}
            onChange={set("anthropic_api_key")}
            disabled={busy}
            autoComplete="off"
            placeholder={config.anthropic_key_configured ? t("settings.keyConfigured") : t("settings.keyMissing")}
          />
        </label>
        <label className="settings-field settings-field--wide">
          <span>{t("settings.openaiKey")}</span>
          <input
            type="password"
            value={draft.openai_api_key}
            onChange={set("openai_api_key")}
            disabled={busy}
            autoComplete="off"
            placeholder={config.openai_key_configured ? t("settings.keyConfigured") : t("settings.keyMissing")}
          />
        </label>
        </>}

        <label className="settings-field">
          <span>{t("settings.anthropicUrl")}</span>
          <input value={draft.anthropic_api_url} onChange={set("anthropic_api_url")} disabled={busy} spellCheck={false} />
        </label>
        <label className="settings-field">
          <span>{t("settings.openaiUrl")}</span>
          <input value={draft.openai_api_url} onChange={set("openai_api_url")} disabled={busy} spellCheck={false} />
        </label>
        </>}

        <label className="settings-field">
          <span>{t("settings.llmTimeout")}</span>
          <input type="number" min={5} max={900} value={draft.llm_timeout_seconds} onChange={set("llm_timeout_seconds")} disabled={busy} />
        </label>
        <label className="settings-field">
          <span>{t("settings.commandTimeout")}</span>
          <input type="number" min={1} max={3600} value={draft.command_timeout_seconds} onChange={set("command_timeout_seconds")} disabled={busy} />
        </label>
        <label className="settings-field">
          <span>{t("settings.maxTokens")}</span>
          <input type="number" min={256} max={131072} value={draft.llm_max_tokens} onChange={set("llm_max_tokens")} disabled={busy} />
        </label>
        <label className="settings-field">
          <span>{t("settings.contextFiles")}</span>
          <input type="number" min={1} max={64} value={draft.llm_max_context_files} onChange={set("llm_max_context_files")} disabled={busy} />
        </label>
        <label className="settings-field">
          <span>{t("settings.contextBytes")}</span>
          <input type="number" min={1000} max={1000000} value={draft.llm_max_context_bytes} onChange={set("llm_max_context_bytes")} disabled={busy} />
        </label>
      </div>

      <p className="settings-note">{t("settings.keyHint")}</p>

      <div className="settings-readonly">
        <h3>{t("settings.readOnly")}</h3>
        <dl>
          <div><dt>{t("settings.workspace")}</dt><dd title={config.workspace_root}>{config.workspace_root}</dd></div>
          <div><dt>{t("settings.stateDir")}</dt><dd title={config.state_dir}>{config.state_dir}</dd></div>
          <div><dt>{t("settings.budgets")}</dt><dd>{config.task_max_iterations} · {Math.round(config.task_max_wall_time_seconds / 60)} min · {config.task_max_remote_calls} · {config.task_max_consecutive_failures}</dd></div>
          <div><dt>{t("settings.retention")}</dt><dd>{config.audit_retention_days}d · {config.test_output_retention_days}d</dd></div>
          <div><dt>{t("settings.overrides")}</dt><dd title={config.overrides_path}>{config.overrides_path}</dd></div>
        </dl>
      </div>
    </section>
  );
}
