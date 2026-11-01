import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Check, Code2, FolderOpen, Languages, LayoutDashboard, ListChecks, Monitor, Moon, MoreHorizontal, Pencil, Plus, Search, Settings2, ShieldCheck, Sun, TerminalSquare, Trash2, X } from "lucide-react";
import type { TaskTrack } from "../lib/api";
import { usePreferences, type Language, type ThemeMode } from "../lib/preferences";
import "../styles/sidebar-sessions.css";

export type NavKey = "chat" | "overview" | "tasks" | "repositories" | "runs" | "approvals";
type Session = { id: string; title: string; track: TaskTrack; status: string };
type FolderGroup = { id: string; name: string; path: string; rows: Session[] };

type SidebarProps = {
  open: boolean;
  activeNav: NavKey;
  track: TaskTrack;
  activeSessionId: string;
  sessions: Session[];
  groups: FolderGroup[];
  approvalCount: number;
  folderOpening: boolean;
  deletionBusy: boolean;
  onClose: () => void;
  onTrackChange: (track: TaskTrack) => void;
  onNavigate: (view: NavKey) => void;
  onNewSession: () => void;
  onOpenFolder: () => void | Promise<void>;
  onOpenSettings: () => void;
  onSelectSession: (id: string) => void;
  onDeleteSession: (session: Session) => void;
  onRenameSession?: (id: string, title: string) => Promise<boolean>;
  onOpenRegisteredFolder: (id: string) => void;
};

const navigation = [
  { key: "overview", icon: LayoutDashboard },
  { key: "repositories", icon: FolderOpen },
  { key: "runs", icon: TerminalSquare },
  { key: "approvals", icon: ShieldCheck },
] as const;
const tracks: TaskTrack[] = ["cowork", "code"];
type RenameState = { sessionId: string; title: string; original: string; saving: boolean; error: string | null };

export function Sidebar(props: SidebarProps) {
  const { t, language, theme, setLanguage, setTheme } = usePreferences();
  const sidebarRef = useRef<HTMLElement>(null);
  const preferencesRef = useRef<HTMLDetailsElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const renameTriggerRef = useRef<string | null>(null);
  const [search, setSearch] = useState("");
  const [rename, setRename] = useState<RenameState | null>(null);
  const renameRef = useRef(rename);
  renameRef.current = rename;
  const renamingRef = useRef(new Set<string>());
  const renameId = useId();
  const words = language === "zh-CN"
    ? { search: "搜索会话或文件夹", clear: "清空搜索", noResults: "没有匹配的会话或文件夹。", rename: "重命名会话", name: "会话名称", save: "保存名称", cancel: "取消重命名", saving: "正在保存…", limit: "名称应为 1–200 个字符，不能包含换行。", failed: "保存名称失败，请重试。" }
    : { search: "Search conversations or folders", clear: "Clear search", noResults: "No matching conversations or folders.", rename: "Rename conversation", name: "Conversation name", save: "Save name", cancel: "Cancel rename", saving: "Saving…", limit: "Use 1–200 characters without line breaks.", failed: "Could not save the name. Try again." };
  const query = search.trim().toLocaleLowerCase();
  const matches = (value: string) => value.toLocaleLowerCase().includes(query);
  const sessions = props.sessions.filter(session => !query || matches(session.title));
  const groups = props.groups.map(group => ({ ...group, rows: !query || matches(group.name) || matches(group.path) ? group.rows : group.rows.filter(session => matches(session.title)) }))
    .filter(group => !query || matches(group.name) || matches(group.path) || group.rows.length > 0);

  useEffect(() => { setSearch(""); setRename(null); }, [props.track]);
  useEffect(() => {
    if (rename) renameInputRef.current?.focus();
    else if (renameTriggerRef.current) sidebarRef.current?.querySelector<HTMLButtonElement>(`[data-rename-session="${CSS.escape(renameTriggerRef.current)}"]`)?.focus();
  }, [rename?.sessionId]);

  useEffect(() => {
    const closePreferences = (event: PointerEvent) => {
      const menu = preferencesRef.current;
      if (menu?.open && !menu.contains(event.target as Node)) menu.open = false;
    };
    document.addEventListener("pointerdown", closePreferences);
    return () => document.removeEventListener("pointerdown", closePreferences);
  }, []);

  useEffect(() => {
    if (!props.open) return;
    const previous = document.activeElement as HTMLElement | null;
    sidebarRef.current?.querySelector<HTMLButtonElement>(".mobile-close")?.focus();
    return () => previous?.focus();
  }, [props.open]);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 900px)");
    const closeOnDesktop = () => { if (!media.matches) props.onClose(); };
    media.addEventListener("change", closeOnDesktop);
    return () => media.removeEventListener("change", closeOnDesktop);
  }, [props.onClose]);

  const navigate = (view: NavKey) => { props.onNavigate(view); props.onClose(); };
  const selectSession = (id: string) => { props.onSelectSession(id); props.onClose(); };
  const cancelRename = () => {
    if (renameRef.current && renamingRef.current.has(renameRef.current.sessionId)) return;
    setRename(null);
  };
  const saveRename = async () => {
    const edit = renameRef.current;
    if (!edit || !props.onRenameSession || renamingRef.current.has(edit.sessionId)) return;
    const title = edit.title.trim();
    if (!title || Array.from(title).length > 200 || /[\r\n]/.test(title)) {
      setRename(current => current?.sessionId === edit.sessionId ? { ...current, error: words.limit } : current);
      return;
    }
    if (title === edit.original) { setRename(null); return; }
    renamingRef.current.add(edit.sessionId);
    setRename(current => current?.sessionId === edit.sessionId ? { ...current, saving: true, error: null } : current);
    try {
      const accepted = await props.onRenameSession(edit.sessionId, title);
      setRename(current => current?.sessionId === edit.sessionId ? accepted ? null : { ...current, saving: false, error: words.failed } : current);
    } catch {
      setRename(current => current?.sessionId === edit.sessionId ? { ...current, saving: false, error: words.failed } : current);
    } finally { renamingRef.current.delete(edit.sessionId); }
  };
  const onRenameKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      if (!event.nativeEvent.isComposing && event.keyCode !== 229) void saveRename();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      cancelRename();
    }
  };
  const onTrackKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = tracks.indexOf(props.track);
    const next = event.key === "ArrowRight" ? (index + 1) % tracks.length
      : event.key === "ArrowLeft" ? (index + tracks.length - 1) % tracks.length
      : event.key === "Home" ? 0 : event.key === "End" ? tracks.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault();
    props.onTrackChange(tracks[next]);
    event.currentTarget.querySelector<HTMLButtonElement>(`[data-track="${tracks[next]}"]`)?.focus();
  };
  const onSidebarKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      if (preferencesRef.current?.open) {
        preferencesRef.current.open = false;
        preferencesRef.current.querySelector<HTMLElement>("summary")?.focus();
      } else if (props.open) props.onClose();
      else return;
      event.preventDefault();
      event.stopPropagation();
    }
    if (event.key !== "Tab" || !props.open) return;
    const controls = [...(sidebarRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), summary, select, [tabindex="0"]') ?? [])].filter(element => element.tabIndex >= 0 && element.getClientRects().length && !element.closest("details:not([open]) > :not(summary)"));
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  };
  const renderSession = (session: Session) => (
    <div key={session.id} className={`chat-session-row ${session.id === props.activeSessionId ? "is-active" : ""}`}>
      {rename?.sessionId === session.id ? <form className="rail-session-rename" onSubmit={event => { event.preventDefault(); void saveRename(); }} onKeyDown={onRenameKeyDown}>
        <input ref={renameInputRef} value={rename.title} disabled={rename.saving} aria-label={words.name} aria-invalid={Boolean(rename.error)} aria-describedby={rename.error ? `${renameId}-error` : undefined} onChange={event => setRename(current => current ? { ...current, title: event.target.value, error: null } : current)} />
        <div className="rail-rename-actions"><span role="status">{rename.saving ? words.saving : `${Array.from(rename.title.trim()).length}/200`}</span><button type="submit" disabled={rename.saving} aria-label={words.save} title={words.save}><Check size={13} /></button><button type="button" disabled={rename.saving} onClick={cancelRename} aria-label={words.cancel} title={words.cancel}><X size={13} /></button></div>
        {rename.error ? <p id={`${renameId}-error`} role="alert">{rename.error}</p> : null}
      </form> : <button type="button" className={`chat-session ${session.id === props.activeSessionId ? "is-active" : ""}`} aria-current={session.id === props.activeSessionId ? "true" : undefined} onClick={() => selectSession(session.id)} title={session.title}>
        <span className={`chat-session-dot chat-session-dot--${session.track === "cowork" ? "cowork" : session.status}`} />
        <span className="chat-session-title">{session.title}</span>
      </button>}
      {props.onRenameSession && rename?.sessionId !== session.id ? <button type="button" className="chat-session-rename" data-rename-session={session.id} disabled={props.deletionBusy || Boolean(rename?.saving) || renamingRef.current.has(session.id)} aria-label={`${words.rename}: ${session.title}`} title={words.rename} onClick={() => { renameTriggerRef.current = session.id; setRename({ sessionId: session.id, title: session.title, original: session.title, saving: false, error: null }); }}><Pencil size={12} /></button> : null}
      <button type="button" className="chat-session-delete" disabled={props.deletionBusy || rename?.sessionId === session.id} aria-label={t("rail.deleteChat")} title={t("rail.deleteChat")} onClick={(event) => { event.stopPropagation(); props.onDeleteSession(session); }}>
        <Trash2 size={13} />
      </button>
    </div>
  );

  return <>
    {props.open ? <button type="button" className="sidebar-scrim" tabIndex={-1} aria-label={t("closeNavigation")} onClick={props.onClose} /> : null}
    <aside id="workspace-sidebar" ref={sidebarRef} className={`sidebar rail ${props.open ? "sidebar--open" : ""}`} onKeyDown={onSidebarKeyDown}>
      <div className="rail-brand-row">
        <div className="rail-brand"><Code2 size={20} /><span>Coding Agent</span></div>
        <button type="button" className="icon-button mobile-close" aria-label={t("closeNavigation")} onClick={props.onClose}><X size={17} /></button>
      </div>
      <div className="rail-head">
        <div className="rail-tabs" role="tablist" aria-label={t("workspace")} onKeyDown={onTrackKeyDown}>
          {tracks.map(track => <button key={track} type="button" id={`track-${track}`} data-track={track} role="tab" aria-selected={props.track === track} aria-controls="workspace-content" tabIndex={props.track === track ? 0 : -1} className={props.track === track ? "is-active" : ""} onClick={() => props.onTrackChange(track)}>{track === "cowork" ? <ListChecks size={14} /> : <Code2 size={14} />}<span className="rail-tab-label">{t(`rail.${track}`)}</span></button>)}
        </div>
      </div>
      <button type="button" className="rail-new" onClick={() => { props.onNewSession(); props.onClose(); }}><Plus size={16} />{t("rail.new")}</button>
      <nav className="rail-nav" aria-label={t("primaryNavigation")}>
        {navigation.map(({ key, icon: Icon }) => <button key={key} type="button" className={`rail-nav-item ${props.activeNav === key ? "is-active" : ""}`} aria-current={props.activeNav === key ? "page" : undefined} onClick={() => navigate(key)}><Icon size={16} strokeWidth={1.8} /><span>{t(`nav.${key}`)}</span>{key === "approvals" && props.approvalCount ? <span className="nav-count">{props.approvalCount}</span> : null}</button>)}
      </nav>
      <div className="rail-section"><span>{t(props.track === "cowork" ? "rail.chats" : "rail.folders")}</span>{props.track === "code" ? <button type="button" className="icon-button" aria-label={t("folder.openLocal")} onClick={props.onOpenFolder} disabled={props.folderOpening}><Plus size={15} /></button> : null}</div>
      <div className="rail-session-search"><Search size={13} aria-hidden="true" /><input ref={searchRef} type="search" value={search} placeholder={words.search} aria-label={words.search} onChange={event => setSearch(event.target.value)} onKeyDown={event => { if (event.key === "Escape" && search) { event.preventDefault(); event.stopPropagation(); setSearch(""); } }} />{search ? <button type="button" aria-label={words.clear} title={words.clear} onClick={() => { setSearch(""); searchRef.current?.focus(); }}><X size={12} /></button> : null}</div>
      <div className="rail-conversations">
        {props.track === "cowork" ? sessions.length ? sessions.map(renderSession) : <p className="rail-empty" role="status">{query ? words.noResults : t("chat.noSessions")}</p>
          : groups.length ? groups.map(({ id, name, path, rows }) => <div className="rail-group" key={id}><div className="rail-group-head"><details className="rail-folder-group" open><summary title={path || name}><FolderOpen size={14} /><span>{name}</span>{path ? <button type="button" className="rail-folder-add" aria-label={t("rail.newInFolder")} title={t("rail.newInFolder")} onClick={event => { event.preventDefault(); event.stopPropagation(); props.onOpenRegisteredFolder(id); props.onClose(); }}><Plus size={13} /></button> : null}</summary><div className="rail-folder-sessions">{rows.map(renderSession)}</div></details></div></div>) : <p className="rail-empty" role="status">{query ? words.noResults : t("rail.emptyFolders")}</p>}
      </div>
      <div className="rail-footer">
        <div className="rail-local"><span className="pulse-dot" />{t("localExecution")}</div>
        <details ref={preferencesRef} className="rail-account-wrap">
          <summary className="rail-account" aria-label={t("rail.account")}><div className="avatar"><Monitor size={16} /></div><div className="user-copy"><strong>{t("rail.localAccount")}</strong><span>{t("rail.preferences")}</span></div><MoreHorizontal size={16} /></summary>
          <div className="rail-menu">
            <label className="preference-control"><Languages size={14} /><select value={language} onChange={event => setLanguage(event.target.value as Language)} aria-label={t("language")}><option value="zh-CN">{t("chinese")}</option><option value="en-US">{t("english")}</option></select></label>
            <label className="preference-control"><span className="preference-theme-icon">{theme === "light" ? <Sun size={14} /> : theme === "dark" ? <Moon size={14} /> : <Monitor size={14} />}</span><select value={theme} onChange={event => setTheme(event.target.value as ThemeMode)} aria-label={t("theme")}><option value="system">{t("system")}</option><option value="light">{t("light")}</option><option value="dark">{t("dark")}</option></select></label>
            <button type="button" className="rail-menu-item" onClick={() => { if (preferencesRef.current) preferencesRef.current.open = false; props.onOpenSettings(); }}><Settings2 size={14} />{t("settings")}</button>
          </div>
        </details>
      </div>
    </aside>
  </>;
}
