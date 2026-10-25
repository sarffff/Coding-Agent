import { useEffect, useRef, type KeyboardEvent } from "react";
import { Code2, FolderOpen, Languages, LayoutDashboard, ListChecks, Monitor, Moon, MoreHorizontal, Plus, Settings2, ShieldCheck, Sun, TerminalSquare, Trash2, X } from "lucide-react";
import type { TaskTrack } from "../lib/api";
import { usePreferences, type Language, type ThemeMode } from "../lib/preferences";

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
  onClose: () => void;
  onTrackChange: (track: TaskTrack) => void;
  onNavigate: (view: NavKey) => void;
  onNewSession: () => void;
  onOpenFolder: () => void | Promise<void>;
  onOpenSettings: () => void;
  onSelectSession: (id: string) => void;
  onDeleteSession: (session: Session) => void;
  onOpenRegisteredFolder: (id: string) => void;
};

const navigation = [
  { key: "overview", icon: LayoutDashboard },
  { key: "repositories", icon: FolderOpen },
  { key: "runs", icon: TerminalSquare },
  { key: "approvals", icon: ShieldCheck },
] as const;
const tracks: TaskTrack[] = ["cowork", "code"];

export function Sidebar(props: SidebarProps) {
  const { t, language, theme, setLanguage, setTheme } = usePreferences();
  const sidebarRef = useRef<HTMLElement>(null);
  const preferencesRef = useRef<HTMLDetailsElement>(null);

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
    const controls = [...(sidebarRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), summary, select, [tabindex="0"]') ?? [])].filter(element => element.tabIndex >= 0 && element.getClientRects().length && !element.closest("details:not([open]) > :not(summary)"));
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  };
  const renderSession = (session: Session) => (
    <div key={session.id} className={`chat-session-row ${session.id === props.activeSessionId ? "is-active" : ""}`}>
      <button type="button" className={`chat-session ${session.id === props.activeSessionId ? "is-active" : ""}`} aria-current={session.id === props.activeSessionId ? "true" : undefined} onClick={() => selectSession(session.id)} title={session.title}>
        <span className={`chat-session-dot chat-session-dot--${session.track === "cowork" ? "cowork" : session.status}`} />
        <span className="chat-session-title">{session.title}</span>
      </button>
      <button type="button" className="chat-session-delete" aria-label={t("rail.deleteChat")} title={t("rail.deleteChat")} onClick={(event) => { event.stopPropagation(); props.onDeleteSession(session); }}>
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
      <div className="rail-conversations">
        {props.track === "cowork" ? props.sessions.length ? props.sessions.map(renderSession) : <p className="rail-empty">{t("chat.noSessions")}</p>
          : props.groups.length ? props.groups.map(({ id, name, path, rows }) => <div className="rail-group" key={id}><div className="rail-group-head"><details className="rail-folder-group" open><summary title={path || name}><FolderOpen size={14} /><span>{name}</span><span className="rail-folder-count">{rows.length}</span></summary><div className="rail-folder-sessions">{rows.map(renderSession)}</div></details>{path ? <button type="button" className="rail-folder-add" aria-label={t("rail.newInFolder")} title={t("rail.newInFolder")} onClick={() => { props.onOpenRegisteredFolder(id); props.onClose(); }}><Plus size={13} /></button> : null}</div></div>) : <p className="rail-empty">{t("rail.emptyFolders")}</p>}
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
