import { FolderOpen, Menu, TerminalSquare } from "lucide-react";
import { usePreferences } from "../lib/preferences";

type WorkspaceHeaderProps = {
  title: string;
  chat?: boolean;
  apiStatus: "checking" | "connected" | "offline";
  navigationOpen: boolean;
  folderOpening: boolean;
  onOpenNavigation: () => void;
  onOpenFolder: () => void | Promise<void>;
  onOpenRun?: () => void;
};

export function WorkspaceHeader(props: WorkspaceHeaderProps) {
  const { t } = usePreferences();
  return <header className={`topbar${props.chat ? " topbar--chat" : ""}`}>
    <div className="topbar-left">
      <button type="button" className="icon-button menu-toggle" aria-label={t("openNavigation")} aria-controls="workspace-sidebar" aria-expanded={props.navigationOpen} onClick={props.onOpenNavigation}><Menu size={18} /></button>
      <div className="breadcrumbs">{props.chat ? null : <><span>{t("workspace")}</span><span className="breadcrumb-slash">/</span></>}<strong className="topbar-title" title={props.title}>{props.title}</strong></div>
    </div>
    <div className="topbar-actions">
      <div className={`connection-status connection-status--${props.apiStatus}`} role="status"><span className="pulse-dot" />{t(props.apiStatus === "connected" ? "apiConnected" : props.apiStatus === "offline" ? "apiOffline" : "checkingApi")}</div>
      {props.onOpenRun ? <button type="button" className="icon-button" onClick={props.onOpenRun} aria-label={t("nav.runs")} title={t("nav.runs")}><TerminalSquare size={15} /></button> : null}
      <button type="button" className="topbar-folder" aria-label={t("folder.openLocal")} onClick={props.onOpenFolder} disabled={props.folderOpening}><FolderOpen size={15} /><span>{t("folder.openLocal")}</span></button>
    </div>
  </header>;
}
