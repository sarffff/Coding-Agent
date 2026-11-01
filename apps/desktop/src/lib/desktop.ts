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
  backendState?: "starting" | "ready" | "stopping" | "stopped" | "failed";
  error: string;
};

export type DesktopBridge = {
  status: DesktopStatus;
  getStatus(): Promise<DesktopStatus>;
  restartBackend(): Promise<DesktopStatus>;
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

const nativeBridge = typeof window === "undefined" ? undefined : window.forge;
const statusListeners = new Set<(status: DesktopStatus) => void>();

function updateStatus(status: Partial<DesktopStatus>) {
  if (!desktopBridge) return;
  desktopBridge.status = { ...desktopBridge.status, ...status };
  for (const listener of statusListeners) listener(desktopBridge.status);
}

// contextBridge freezes its exported objects; keep the changing snapshot in this realm.
export const desktopBridge: DesktopBridge | undefined = nativeBridge ? {
  ...nativeBridge,
  status: { ...nativeBridge.status },
  async getStatus() {
    const status = await nativeBridge.getStatus();
    updateStatus(status);
    return status;
  },
  async restartBackend() {
    const status = await nativeBridge.restartBackend();
    updateStatus(status);
    return status;
  },
  async pickWorkspaceRoot() {
    const result = await nativeBridge.pickWorkspaceRoot();
    updateStatus(result);
    return result;
  },
  onStatusChanged(listener) {
    statusListeners.add(listener);
    return () => { statusListeners.delete(listener); };
  },
} : undefined;

nativeBridge?.onStatusChanged(updateStatus);

export const isDesktopShell = Boolean(desktopBridge);

export const desktopStatus: DesktopStatus | null = desktopBridge?.status ?? null;
