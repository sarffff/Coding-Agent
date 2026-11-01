const { contextBridge, ipcRenderer } = require("electron");

// The initial snapshot is followed by status-changed events after a restart or exit.
const status = ipcRenderer.sendSync("forge:status-sync") || {};

contextBridge.exposeInMainWorld("forge", {
  status,
  getStatus: () => ipcRenderer.invoke("forge:status"),
  restartBackend: () => ipcRenderer.invoke("forge:restart-backend"),
  pickDirectory: (options) => ipcRenderer.invoke("forge:pick-directory", options),
  pickWorkspaceRoot: () => ipcRenderer.invoke("forge:pick-workspace-root"),
  openLog: () => ipcRenderer.invoke("forge:open-log"),
  onStatusChanged: (listener) => {
    const handler = (_event, payload) => listener(payload);
    ipcRenderer.on("forge:status-changed", handler);
    return () => ipcRenderer.removeListener("forge:status-changed", handler);
  },
});
