const { contextBridge, ipcRenderer } = require("electron");

// The window is only created after the managed backend answered /health, so the
// synchronous snapshot is enough for module-scope API base URL resolution.
const status = ipcRenderer.sendSync("forge:status-sync") || {};

contextBridge.exposeInMainWorld("forge", {
  status,
  getStatus: () => ipcRenderer.invoke("forge:status"),
  pickDirectory: (options) => ipcRenderer.invoke("forge:pick-directory", options),
  pickWorkspaceRoot: () => ipcRenderer.invoke("forge:pick-workspace-root"),
  openLog: () => ipcRenderer.invoke("forge:open-log"),
  onStatusChanged: (listener) => {
    const handler = (_event, payload) => listener(payload);
    ipcRenderer.on("forge:status-changed", handler);
    return () => ipcRenderer.removeListener("forge:status-changed", handler);
  },
});
