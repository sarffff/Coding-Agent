import { useState } from "react";
import { ArrowLeft, Bot, Languages, Monitor, Search, Settings2, Terminal } from "lucide-react";
import { usePreferences, type Language, type ThemeMode } from "../lib/preferences";
import { SettingsPanel, type SettingsSection } from "./SettingsPanel";
import "../styles/settings.css";

type SettingsCategory = "general" | SettingsSection;

const categories = [
  { id: "general", key: "settings.general", icon: Settings2 },
  { id: "models", key: "settings.models.title", icon: Bot },
  { id: "runtime", key: "settings.runtime", icon: Terminal },
] as const;

export function SettingsPage({ onBack, initialCategory = "general" }: { onBack: () => void; initialCategory?: SettingsCategory }) {
  const { t, language, theme, setLanguage, setTheme } = usePreferences();
  const [category, setCategory] = useState<SettingsCategory>(initialCategory);
  const [query, setQuery] = useState("");
  const visibleCategories = categories.filter(item => t(item.key).toLowerCase().includes(query.trim().toLowerCase()));
  const current = categories.find(item => item.id === category)!;

  return <div className="settings-page">
    <aside className="settings-page-sidebar">
      <button className="settings-page-back" onClick={onBack}><ArrowLeft size={16} /><span>{t("settings.backToApp")}</span></button>
      <div className="settings-page-brand"><span className="settings-page-brand-icon"><Terminal size={19} /></span><div><strong>Forge</strong><span>{t("settings.localApp")}</span></div></div>
      <label className="settings-page-search"><Search size={15} /><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder={t("settings.search")} aria-label={t("settings.search")} /></label>
      <nav aria-label={t("settings.navigation")}>
        {visibleCategories.map(({ id, key, icon: Icon }) => <button key={id} className={`settings-page-nav${category === id ? " is-active" : ""}`} aria-current={category === id ? "page" : undefined} onClick={() => setCategory(id)}><Icon size={16} /><span>{t(key)}</span></button>)}
        {!visibleCategories.length ? <p className="settings-page-search-empty">{t("settings.noResults")}</p> : null}
      </nav>
    </aside>

    <main className="settings-page-main">
      <header className="settings-page-heading"><h1>{t(current.key)}</h1><p>{t(`settings.${category}Description`)}</p></header>
      <div className="settings-page-general" hidden={category !== "general"}>
        <section className="settings-page-section">
          <h2>{t("settings.appearance")}</h2>
          <div className="settings-page-card">
            <label className="settings-page-row"><span className="settings-page-row-copy"><strong><Monitor size={16} />{t("theme")}</strong><small>{t("settings.themeDescription")}</small></span><select value={theme} onChange={event => setTheme(event.target.value as ThemeMode)} aria-label={t("theme")}><option value="system">{t("system")}</option><option value="light">{t("light")}</option><option value="dark">{t("dark")}</option></select></label>
            <label className="settings-page-row"><span className="settings-page-row-copy"><strong><Languages size={16} />{t("language")}</strong><small>{t("settings.languageDescription")}</small></span><select value={language} onChange={event => setLanguage(event.target.value as Language)} aria-label={t("language")}><option value="zh-CN">{t("chinese")}</option><option value="en-US">{t("english")}</option></select></label>
          </div>
        </section>
      </div>
      <div hidden={category === "general"}>
        <SettingsPanel embedded section={category === "runtime" ? "runtime" : "models"} />
      </div>
    </main>
  </div>;
}
