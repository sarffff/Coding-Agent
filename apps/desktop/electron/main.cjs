const { app, BrowserWindow, Menu, dialog, ipcMain, net, protocol, shell } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const { createConfig } = require("./config.cjs");
const { defaultWorkspaceRoot, startBackend } = require("./backend.cjs");

const APP_SCHEME = "forge-app";
const APP_HOST = "desktop";
const APP_ORIGIN = `${APP_SCHEME}://${APP_HOST}`;
const DEV_URL = process.env.FORGE_ELECTRON_DEV_URL || "";
const SMOKE = process.env.FORGE_ELECTRON_SMOKE === "1";

const desktopRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(desktopRoot, "..", "..");
const distDir = path.join(desktopRoot, "dist");

app.setName("Forge Coding Agent");
if (process.platform === "win32") app.setAppUserModelId("com.forge.codingagent");

let mainWindow = null;
let backend = null;
let config = null;
let lastError = "";

function logFile() {
  return path.join(app.getPath("userData"), "logs", "backend.log");
}

function stateDir() {
  return path.join(app.getPath("userData"), "state");
}

function statusPayload() {
  const settings = config ? config.read() : { workspaceRoot: "" };
  return {
    apiBaseUrl: backend ? `http://127.0.0.1:${backend.port}` : "",
    workspaceRoot: settings.workspaceRoot || defaultWorkspaceRoot(),
    stateDir: stateDir(),
    logFile: backend?.logFile || logFile(),
    version: app.getVersion(),
    electron: process.versions.electron,
    node: process.versions.node,
    platform: process.platform,
    backendReady: Boolean(backend),
    error: lastError,
  };
}

function corsOrigins() {
  const origins = new Set([APP_ORIGIN, "http://localhost:5173", "http://127.0.0.1:5173"]);
  if (DEV_URL) origins.add(DEV_URL);
  return [...origins];
}

function resolveApiRoot() {
  if (process.env.FORGE_API_ROOT) return path.resolve(process.env.FORGE_API_ROOT);
  if (app.isPackaged) return "";
  return repoRoot;
}

async function startManagedBackend() {
  const apiRoot = resolveApiRoot();
  if (!apiRoot) {
    throw new Error("打包版没有内置 Python API。请设置 FORGE_API_ROOT 指向仓库根目录，或先用 pnpm desktop:electron 以源码方式运行。");
  }
  const workspaceRoot = config.read().workspaceRoot || defaultWorkspaceRoot();
  backend = await startBackend({
    repoRoot: apiRoot,
    workspaceRoot,
    stateDir: stateDir(),
    corsOrigins: corsOrigins(),
    logFile: logFile(),
    port: config.read().apiPort,
  });
  config.update({ apiPort: backend.port });
  lastError = "";
}

async function stopManagedBackend() {
  const current = backend;
  backend = null;
  if (current) await current.stop();
}

function describe(error) {
  return error instanceof Error ? error.message : String(error);
}

function errorPage(message) {
  const safe = String(message).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Forge 未就绪</title>
<style>body{font-family:'Segoe UI',sans-serif;background:#111313;color:#ebece8;padding:34px 40px;line-height:1.6}
h1{font-size:17px;margin:0 0 12px}p{font-size:13px;color:#b7bdb6;margin:0 0 12px}
pre{background:#171a1a;border:1px solid rgba(231,238,223,.12);border-radius:6px;padding:14px;font-size:11px;white-space:pre-wrap;overflow-wrap:anywhere}
code{color:#c9e26a}</style></head><body>
<h1>桌面壳已就绪，但工作台还起不来。</h1>
<p>请确认 <code>apps/api</code> 存在，Python 3.11+ 已安装 <code>apps/api/requirements.txt</code>（可用 <code>FORGE_PYTHON</code> 指定解释器），并已执行 <code>pnpm build</code>。日志：<code>${logFile()}</code></p>
<pre>${safe}</pre>
</body></html>`;
  return "data:text/html;charset=utf-8," + encodeURIComponent(html);
}

function installAppScheme() {
  protocol.registerSchemesAsPrivileged([
    { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } },
  ]);
}

function serveDist() {
  protocol.handle(APP_SCHEME, (request) => {
    const url = new URL(request.url);
    const requested = decodeURIComponent(url.pathname).replace(/^\/+/, "");
    const target = path.resolve(distDir, requested || "index.html");
    if (target !== distDir && !target.startsWith(distDir + path.sep)) {
      return new Response("Forbidden", { status: 403 });
    }
    const usable = fs.existsSync(target) && !fs.statSync(target).isDirectory();
    return net.fetch(pathToFileURL(usable ? target : path.join(distDir, "index.html")).toString());
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    show: !SMOKE,
    title: "Forge Coding Agent",
    backgroundColor: "#111313",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (DEV_URL && url.startsWith(DEV_URL)) return;
    if (!url.startsWith(APP_ORIGIN)) event.preventDefault();
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

async function boot() {
  try {
    await startManagedBackend();
  } catch (error) {
    lastError = describe(error);
    console.error("[forge] backend startup failed:", lastError);
  }

  createWindow();
  if (!SMOKE) {
    Menu.setApplicationMenu(
      Menu.buildFromTemplate([
        { label: "Forge", submenu: [{ role: "reload" }, { role: "toggleDevTools" }, { type: "separator" }, { role: "quit" }] },
      ])
    );
  }

  if (!lastError && !DEV_URL && !fs.existsSync(path.join(distDir, "index.html"))) {
    lastError = `找不到前端构建产物 ${path.join(distDir, "index.html")}，请先执行 pnpm build。`;
  }
  await mainWindow.loadURL(lastError ? errorPage(lastError) : DEV_URL || `${APP_ORIGIN}/index.html`);
  if (SMOKE) await runSmokeChecks();
}

async function runSmokeChecks() {
  const probe = `({
    mounted: document.querySelectorAll("#root *").length,
    bridge: Boolean(window.forge),
    apiBaseUrl: window.forge ? window.forge.status.apiBaseUrl : "",
    workspaceRoot: window.forge ? window.forge.status.workspaceRoot : "",
    title: document.title,
  })`;
  let result = null;
  let health = false;
  let error = "";
  try {
    result = await mainWindow.webContents.executeJavaScript(probe, true);
    if (!result.apiBaseUrl) throw new Error("window.forge.status.apiBaseUrl is empty");
    const response = await net.fetch(`${result.apiBaseUrl}/health`);
    health = response.ok;
    if (!health) throw new Error(`health returned ${response.status}`);
  } catch (failure) {
    error = describe(failure);
  }
  const pass = Boolean(result && health && result.mounted > 0 && result.bridge && !error && !lastError);
  console.log(JSON.stringify({ smoke: "forge-electron", pass, health, error, lastError, ...result }, null, 2));
  app.exit(pass ? 0 : 1);
}

function registerIpc() {
  ipcMain.on("forge:status-sync", (event) => {
    event.returnValue = statusPayload();
  });
  ipcMain.handle("forge:status", () => statusPayload());
  ipcMain.handle("forge:pick-directory", async (_event, options) => {
    const fallback = typeof options?.fallbackPath === "string" ? options.fallbackPath : statusPayload().workspaceRoot;
    const result = await dialog.showOpenDialog(mainWindow, {
      title: "选择 Git 仓库目录",
      defaultPath: fs.existsSync(fallback) ? fallback : app.getPath("home"),
      properties: ["openDirectory", "createDirectory"],
    });
    if (result.canceled || !result.filePaths.length) return null;
    return path.resolve(result.filePaths[0]);
  });
  ipcMain.handle("forge:pick-workspace-root", async () => {
    const current = statusPayload().workspaceRoot;
    const result = await dialog.showOpenDialog(mainWindow, {
      title: "选择工作区根目录（API 只允许读写其内的仓库）",
      defaultPath: fs.existsSync(current) ? current : app.getPath("home"),
      properties: ["openDirectory", "createDirectory"],
    });
    if (result.canceled || !result.filePaths.length) return { canceled: true, ...statusPayload() };
    const nextRoot = path.resolve(result.filePaths[0]);
    config.update({ workspaceRoot: nextRoot });
    await stopManagedBackend();
    try {
      await startManagedBackend();
    } catch (error) {
      lastError = describe(error);
      return { canceled: false, error: lastError, ...statusPayload() };
    }
    const status = statusPayload();
    mainWindow?.webContents.send("forge:status-changed", status);
    return { canceled: false, ...status };
  });
  ipcMain.handle("forge:open-log", async () => {
    const file = statusPayload().logFile;
    if (!fs.existsSync(file)) return { opened: false, file };
    await shell.openPath(file);
    return { opened: true, file };
  });
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  installAppScheme();
  app
    .whenReady()
    .then(() => {
      fs.mkdirSync(path.dirname(logFile()), { recursive: true });
      config = createConfig(app.getPath("userData"));
      serveDist();
      registerIpc();
      return boot();
    })
    .catch((error) => {
      console.error("[forge] fatal:", describe(error));
      app.exit(1);
    });
}

app.on("window-all-closed", () => {
  app.quit();
});

let quitting = false;
app.on("will-quit", (event) => {
  if (!backend || quitting) return;
  event.preventDefault();
  quitting = true;
  void stopManagedBackend().finally(() => app.exit(0));
});

app.on("web-contents-created", (_event, contents) => {
  contents.on("will-attach-webview", (event) => event.preventDefault());
});
