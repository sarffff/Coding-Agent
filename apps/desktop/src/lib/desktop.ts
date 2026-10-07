export type DesktopStatus = {
  apiBaseUrl: string;
  workspaceRoot: string;
  stateDir: string;
  logFile: string;
  version: string;
  electron: string;
  node: string;
  platform: string;
  backendReady: boolean;
  error: string;
};

export type DesktopBridge = {
  status: DesktopStatus;
  getStatus(): Promise<DesktopStatus>;
  pickDirectory(options?: { fallbackPath?: string }): Promise<string | null>;
  pickWorkspaceRoot(): Promise<{ canceled: boolean } & Partial<DesktopStatus>>;
  openLog(): Promise<{ opened: boolean; file: string }>;
  onStatusChanged(listener: (status: DesktopStatus) => void): () => void;
};

declare global {
  interface Window {
    forge?: DesktopBridge;
  }
}

export const desktopBridge: DesktopBridge | undefined = typeof window === "undefined" ? undefined : window.forge;

export const isDesktopShell = Boolean(desktopBridge);

export const desktopStatus: DesktopStatus | null = desktopBridge?.status ?? null;
