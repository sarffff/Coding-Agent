const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const { pathToFileURL } = require("node:url");
const { runInNewContext } = require("node:vm");

const root = path.resolve(__dirname, "..");
const electronDir = path.join(root, "apps", "desktop", "electron");
fs.mkdirSync(path.join(root, ".tmp"), { recursive: true });
const scratch = fs.mkdtempSync(path.join(root, ".tmp", "desktop-runtime-"));
const failures = [];

function loadBackend(spawnChild = spawn) {
  const module = { exports: {} };
  runInNewContext(fs.readFileSync(path.join(electronDir, "backend.cjs"), "utf8"), {
    module, require: name => name === "node:child_process" ? { spawn: spawnChild } : require(name),
    process, setTimeout, clearTimeout, console,
  });
  return module.exports;
}

function loadMain(startBackend) {
  const app = new EventEmitter();
  Object.assign(app, {
    setName() {}, setAppUserModelId() {}, requestSingleInstanceLock: () => true,
    whenReady: () => new Promise(() => {}), getVersion: () => "test", getPath: () => scratch,
    exitCodes: [], exit(code) { this.exitCodes.push(code); },
  });
  const hooks = {};
  const handlers = new Map();
  const module = { exports: {} };
  runInNewContext(fs.readFileSync(path.join(electronDir, "main.cjs"), "utf8") + `
    Object.assign(hooks, { statusPayload, startManagedBackend, stopManagedBackend, registerIpc,
      initialize() { config = { read: () => ({ apiPort: 1 }), update() {} };
        mainWindow = { webContents: { send: (...args) => hooks.events.push(args), getURL: () => "forge-app://desktop/index.html" } }; },
    });`, {
    module, hooks, __dirname: electronDir, process, console, setTimeout, clearTimeout, Response, URL,
    require: name => name === "electron" ? {
      app, protocol: { registerSchemesAsPrivileged() {} },
      ipcMain: { on() {}, handle: (name, callback) => handlers.set(name, callback) },
    } : name === "./backend.cjs" ? { startBackend, defaultWorkspaceRoot: () => scratch } : require(name.startsWith("./") ? path.join(electronDir, name) : name),
  });
  hooks.events = [];
  hooks.initialize();
  hooks.registerIpc();
  return { hooks, handlers, app };
}

async function check(name, callback) {
  try { await callback(); console.log(`PASS ${name}`); }
  catch (error) { failures.push(name); console.error(`FAIL ${name}: ${error.message}`); }
}

(async () => {
  await check("startup exits are detected before the health deadline", async () => {
    const backend = loadBackend(() => spawn(process.execPath, ["-e", "process.exit(23)"], { stdio: ["ignore", "pipe", "pipe"], windowsHide: true }));
    const started = Date.now();
    await assert.rejects(backend.startBackend({ repoRoot: root, workspaceRoot: scratch, stateDir: scratch,
      corsOrigins: [], logFile: path.join(scratch, "failed.log") }), /启动.*退出|23/);
    assert.ok(Date.now() - started < 5000, "A child that exited must not wait 30 seconds");
  });

  await check("unexpected exit reaches the desktop and a failed restart stays failed", async () => {
    let options;
    let fail = false;
    const { hooks, handlers } = loadMain(async value => {
      options = value;
      if (fail) throw new Error("test startup failure");
      return { port: 18001, logFile: "test.log", isRunning: () => true, stop: async () => {} };
    });
    await hooks.startManagedBackend();
    assert.equal(hooks.statusPayload().backendReady, true);
    assert.equal(typeof options.onExit, "function", "The shell must observe its managed child");
    options.onExit({ code: 23, signal: null, error: null });
    assert.equal(hooks.statusPayload().backendReady, false);
    assert.match(hooks.statusPayload().error, /23/);
    assert.equal(hooks.events.at(-1)[1].backendReady, false);
    fail = true;
    const failed = await handlers.get("forge:restart-backend")();
    assert.equal(failed.backendReady, false);
    assert.match(failed.error, /test startup failure/);
    fail = false;
    const retried = await handlers.get("forge:restart-backend")();
    assert.equal(retried.backendReady, true);
    assert.equal(retried.error, "");
    await hooks.stopManagedBackend();
  });

  await check("stop waits for close even after a kill signal was sent", async () => {
    const backend = loadBackend((_name, _args) => new EventEmitter());
    const child = new EventEmitter();
    Object.assign(child, { pid: 123, killed: true, exitCode: null, signalCode: null, kill() {} });
    let stopped = false;
    const stopping = backend.stopBackend(child).then(() => { stopped = true; });
    await Promise.resolve();
    assert.equal(stopped, false, "child.killed only means a signal was sent");
    child.exitCode = 0;
    child.emit("close", 0, null);
    await stopping;
    assert.equal(stopped, true);
  });

  await check("quitting during startup waits and stops the child before exit", async () => {
    let complete;
    let stopped = false;
    const { hooks, app } = loadMain(() => new Promise(resolve => { complete = resolve; }));
    const starting = hooks.startManagedBackend();
    let prevented = false;
    app.emit("will-quit", { preventDefault() { prevented = true; } });
    assert.equal(prevented, true);
    assert.equal(app.exitCodes.length, 0);
    complete({ port: 18001, isRunning: () => true, stop: async () => { stopped = true; } });
    await starting;
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(stopped, true);
    assert.deepEqual(app.exitCodes, [0]);
  });

  await check("concurrent restart requests share one startup", async () => {
    let launches = 0;
    let complete;
    const { hooks, handlers } = loadMain(() => {
      launches++;
      return new Promise(resolve => { complete = resolve; });
    });
    const first = handlers.get("forge:restart-backend")();
    const second = handlers.get("forge:restart-backend")();
    await Promise.resolve();
    assert.equal(launches, 1);
    complete({ port: 18002, isRunning: () => true, stop: async () => {} });
    assert.equal((await first).backendReady, true);
    assert.equal((await second).apiBaseUrl, "http://127.0.0.1:18002");
    assert.equal(launches, 1);
    await hooks.stopManagedBackend();
  });

  await check("planned stop cleans a real managed child without reporting a crash", async () => {
    const exits = [];
    const backend = loadBackend((program, args, options) => program === "taskkill" ? spawn(program, args, options) :
      spawn(process.execPath, ["-e", "require('http').createServer((req,res)=>{res.end('ok')}).listen(Number(process.argv[1]),'127.0.0.1')", args[args.indexOf("--port") + 1]], options));
    const running = await backend.startBackend({ repoRoot: root, workspaceRoot: scratch, stateDir: scratch,
      corsOrigins: [], logFile: path.join(scratch, "healthy.log"), onExit: event => exits.push(event) });
    try { assert.equal(running.isRunning(), true); }
    finally { await running.stop(); }
    assert.equal(running.isRunning(), false);
    assert.equal(await backend.probeHealth(running.port), false);
    assert.equal(exits.length, 0);
  });

  await check("bridge refreshes a frozen preload snapshot before notifying React", async () => {
    const requireDesktop = createRequire(path.join(root, "apps", "desktop", "package.json"));
    const { createServer } = await import(pathToFileURL(requireDesktop.resolve("vite")));
    const server = await createServer({ root: path.join(root, "apps", "desktop"), configFile: false,
      server: { middlewareMode: true, hmr: false }, appType: "custom" });
    try {
      let changed;
      let status = Object.freeze({ apiBaseUrl: "http://127.0.0.1:18001", backendReady: true, error: "" });
      const native = Object.freeze({ status, onStatusChanged: callback => { changed = callback; return () => {}; },
        getStatus: async () => status, restartBackend: async () => status, pickDirectory: async () => null,
        pickWorkspaceRoot: async () => ({ canceled: false, ...status }), openLog: async () => ({ opened: false, file: "" }) });
      const code = (await server.transformRequest("/src/lib/desktop.ts", { ssr: true })).code;
      const exports = {};
      runInNewContext(code, { window: { forge: native }, __vite_ssr_exports__: exports,
        __vite_ssr_exportName__: (name, get) => Object.defineProperty(exports, name, { get }) });
      let observed;
      exports.desktopBridge.onStatusChanged(() => { observed = exports.desktopBridge.status.apiBaseUrl; });
      status = { apiBaseUrl: "http://127.0.0.1:18002", backendReady: true, error: "" };
      changed(status);
      assert.equal(observed, status.apiBaseUrl);
      assert.equal(exports.desktopBridge.status.apiBaseUrl, status.apiBaseUrl);
      status = { ...status, apiBaseUrl: "http://127.0.0.1:18003" };
      await exports.desktopBridge.getStatus();
      assert.equal(exports.desktopBridge.status.apiBaseUrl, status.apiBaseUrl);
    } finally { await server.close(); }
  });
  if (failures.length) process.exitCode = 1;
  else console.log("Desktop runtime checks passed: startup failure, crash/retry, startup shutdown, concurrent restart, real child cleanup and mutable bridge status.");
})().catch(error => { console.error(error); process.exitCode = 1; });
