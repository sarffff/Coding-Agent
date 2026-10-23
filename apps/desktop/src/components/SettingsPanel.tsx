import { useEffect, useState } from "react";
import { KeyRound, Save } from "lucide-react";
import { getConfig, updateConfig, type AppConfig, type ConfigUpdate } from "../lib/api";
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

      <div className="settings-grid">
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

        <label className="settings-field">
          <span>{t("settings.anthropicUrl")}</span>
          <input value={draft.anthropic_api_url} onChange={set("anthropic_api_url")} disabled={busy} spellCheck={false} />
        </label>
        <label className="settings-field">
          <span>{t("settings.openaiUrl")}</span>
          <input value={draft.openai_api_url} onChange={set("openai_api_url")} disabled={busy} spellCheck={false} />
        </label>

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
