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
  return path.join(app.getPath("userData"), SMOKE ? "state-smoke" : "state");
}

/**
 * The self-check registers a throwaway repository, and the API has no unregister
 * endpoint, so smoke runs get roots they can be destroyed again instead of the
 * operator's real desktop state.
 */
function acceptanceWorkspaceRoot() {
  return path.join(app.getPath("home"), "forge-workspace-acceptance");
}

function resetSmokeRoots() {
  fs.rmSync(stateDir(), { recursive: true, force: true });
  fs.rmSync(acceptanceWorkspaceRoot(), { recursive: true, force: true });
}

function currentWorkspaceRoot() {
  if (SMOKE) return acceptanceWorkspaceRoot();
  const settings = config ? config.read() : { workspaceRoot: "" };
  return settings.workspaceRoot || defaultWorkspaceRoot();
}

function statusPayload() {
  return {
    apiBaseUrl: backend ? `http://127.0.0.1:${backend.port}` : "",
    workspaceRoot: currentWorkspaceRoot(),
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
  const workspaceRoot = currentWorkspaceRoot();
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
  if (SMOKE) resetSmokeRoots();
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

const INTERACTION_PROBE = `(async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const steps = [];
  const add = (name, ok, detail) => {
    steps.push({ step: name, ok: Boolean(ok), detail: String(detail === undefined ? "" : detail).slice(0, 180) });
  };

  add("协议来源", location.origin === "forge-app://desktop", location.origin);

  const bodyBackground = getComputedStyle(document.body).backgroundColor;
  add("样式随协议加载", bodyBackground !== "rgba(0, 0, 0, 0)", bodyBackground);

  const bridge = window.forge;
  add("桥可用", Boolean(bridge && typeof bridge.pickDirectory === "function" && typeof bridge.pickWorkspaceRoot === "function"), typeof bridge);

  let api = { status: -1, items: null };
  try {
    const response = await fetch(bridge.status.apiBaseUrl + "/api/v1/repositories");
    const json = await response.json().catch(() => null);
    api = { status: response.status, items: json && Array.isArray(json.items) ? json.items.length : null };
  } catch (failure) {
    api = { status: -1, items: String(failure) };
  }
  add("渲染进程直连 API", api.status === 200 && typeof api.items === "number", JSON.stringify(api));

  const box = [...document.querySelectorAll("textarea")].find((el) => (el.getAttribute("aria-label") || "").includes("描述目标"));
  if (box) {
    const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
    setValue.call(box, "桌面端交互验收探针");
    box.dispatchEvent(new Event("input", { bubbles: true }));
    await wait(150);
    // The send button carries only an icon; its accessible name comes from aria-label.
    const send = document.querySelector("button.chat-send");
    add("受控输入与发送态", box.value.includes("验收探针") && Boolean(send) && send.disabled === false, send ? "disabled=" + send.disabled : "未找到发送按钮");
  } else {
    const seen = [...document.querySelectorAll("textarea")].map((el) => el.getAttribute("aria-label") || el.getAttribute("placeholder") || "?").join(" | ");
    add("受控输入与发送态", false, "未找到目标输入框；现有 textarea: " + (seen || "无"));
  }

  return { steps, mounted: document.querySelectorAll("#root *").length, title: document.title, apiBaseUrl: bridge ? bridge.status.apiBaseUrl : "", workspaceRoot: bridge ? bridge.status.workspaceRoot : "" };
})()`;

const FOLDER_FLOW_PROBE = `(async (marker) => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const steps = [];
  const add = (name, ok, detail) => steps.push({ step: name, ok: Boolean(ok), detail: String(detail === undefined ? "" : detail).slice(0, 180) });
  const text = () => document.body.innerText || "";
  const clickWhere = async (predicate, settle) => {
    const el = [...document.querySelectorAll("button")].find(predicate);
    if (!el) return false;
    el.click();
    await wait(settle);
    return true;
  };

  // The smoke window is created with show:false, so innerText is not a reliable
  // visibility signal here; assert the entry exists and report what it says.
  const entries = [...document.querySelectorAll("button")].filter((el) => el.textContent.includes(marker.folder));
  add(
    "侧栏登记了新文件夹",
    document.body.textContent.includes(marker.folder) && entries.length > 0,
    "条目=" + entries.length + "; 首个文字=" + (entries[0] ? entries[0].textContent.trim().replace(/\\s+/g, " ").slice(0, 80) : "无"),
  );
  add("选中该文件夹", await clickWhere((el) => el.textContent.includes(marker.folder), 400), "无可选条目");

  const nav = await clickWhere((el) => el.textContent.trim() === "文件浏览", 600);
  add("切到文件浏览", nav && text().includes(marker.file), text().includes(marker.file) ? "" : "文件树里没有 " + marker.file);

  const opened = await clickWhere((el) => el.textContent.trim() === marker.file, 700);
  add("读取文件内容", opened && text().includes(marker.token), opened ? "正文里没有标记串" : "文件树里点不到 " + marker.file);

  return steps;
})`;

/**
 * The native picker cannot be driven headlessly (a modal dialog would hang the run),
 * so registration goes through the shell's own API and the window reloads to pick it
 * up. Everything downstream of the picker - sidebar entry, selection, tree, file read -
 * is then exercised for real inside the renderer.
 */
async function runFolderFlow(apiBaseUrl) {
  const folder = path.join(acceptanceWorkspaceRoot(), "acceptance-folder");
  fs.mkdirSync(folder, { recursive: true });
  const marker = { folder: "acceptance-folder", file: "notes.md", token: "ACCEPTANCE_TOKEN_7431" };
  fs.writeFileSync(path.join(folder, marker.file), `# acceptance\n\n${marker.token}\n`);

  const response = await net.fetch(`${apiBaseUrl}/api/v1/repositories`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ path: folder, name: marker.folder }),
  });
  if (!response.ok) throw new Error(`注册返回 ${response.status}: ${(await response.text()).slice(0, 160)}`);

  mainWindow.webContents.reload();
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    // Evaluation can reject while the document is being replaced; that just means "not ready".
    try {
      const ready = await mainWindow.webContents.executeJavaScript(`document.body ? document.body.textContent.includes(${JSON.stringify(marker.folder)}) : false`, true);
      if (ready) break;
    } catch {
      /* keep polling */
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return mainWindow.webContents.executeJavaScript(`(${FOLDER_FLOW_PROBE})(${JSON.stringify(marker)})`, true);
}

async function runSmokeChecks() {
  let probe = null;
  let health = false;
  let error = "";
  const steps = [];
  const add = (name, ok, detail) => {
    steps.push({ step: name, ok: Boolean(ok), detail: String(detail === undefined ? "" : detail).slice(0, 180) });
  };

  try {
    const loadedUrl = mainWindow.webContents.getURL();
    add("窗口加载地址", loadedUrl.startsWith(APP_ORIGIN) || (DEV_URL && loadedUrl.startsWith(DEV_URL)), loadedUrl);

    probe = await mainWindow.webContents.executeJavaScript(INTERACTION_PROBE, true);
    if (!probe.apiBaseUrl) throw new Error("window.forge.status.apiBaseUrl is empty");
    const response = await net.fetch(`${probe.apiBaseUrl}/health`);
    health = response.ok;
    add("壳侧健康检查", health, response.status);
    add("渲染已挂载", probe.mounted > 0, probe.mounted);
    add("工作区根目录已建立", fs.existsSync(probe.workspaceRoot), probe.workspaceRoot);
    add("端口记忆已落盘", config.read().apiPort === backend?.port, `${config.read().apiPort} vs ${backend?.port}`);
    steps.push(...probe.steps);

    const folderSteps = await runFolderFlow(probe.apiBaseUrl);
    steps.push(...folderSteps);
  } catch (failure) {
    error = describe(failure);
  }

  const allSteps = steps.length ? steps : [{ step: "探针未跑完", ok: false, detail: error }];
  const pass = Boolean(probe && health && !error && !lastError && allSteps.every((step) => step.ok));
  console.log(JSON.stringify({ smoke: "forge-electron", pass, health, error, lastError, ...probe, steps: allSteps.map((step) => `${step.ok ? "PASS" : "FAIL"} ${step.step}${step.ok ? "" : ` -> ${step.detail}`}`) }, null, 2));
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
      title: "打开文件夹",
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
