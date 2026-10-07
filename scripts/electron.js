#!/usr/bin/env node
/**
 * Electron 桌面壳启动器
 *
 * 用法:
 *   node scripts/electron.js          # 构建产物 + 托管本地 API，启动桌面壳
 *   node scripts/electron.js dev      # 连同 vite 开发服务器一起启动（HMR）
 *   node scripts/electron.js smoke    # 隐藏窗口自检，成功退出码 0
 */

const { spawn } = require("child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const desktopDir = path.join(root, "apps", "desktop");
const distIndex = path.join(desktopDir, "dist", "index.html");
const devPort = Number(process.env.FORGE_ELECTRON_DEV_PORT || 5173);
const devUrl = `http://127.0.0.1:${devPort}`;

function localBin(packageName, entry) {
  return path.join(desktopDir, "node_modules", packageName, entry);
}

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd || root,
      env: { ...process.env, ...options.env },
      stdio: options.stdio || "inherit",
      shell: false,
      windowsHide: Boolean(options.windowsHide),
    });
    child.on("error", reject);
    child.on("close", (code) => resolve(code ?? 1));
  });
}

function isFile(file) {
  return fs.existsSync(file);
}

async function buildRenderer() {
  if (isFile(localBin("vite", "bin/vite.js"))) {
    console.log("[electron] 构建 Desktop 产物...");
    const code = await run(process.execPath, [localBin("vite", "bin/vite.js"), "build"], { cwd: desktopDir, windowsHide: true });
    if (code !== 0) throw new Error(`vite build 失败（退出码 ${code}）`);
    return;
  }
  if (!isFile(distIndex)) throw new Error(`找不到 ${distIndex}，请先执行 pnpm build`);
}

async function startViteDev() {
  const viteBin = localBin("vite", "bin/vite.js");
  if (!isFile(viteBin)) throw new Error("找不到 apps/desktop/node_modules/vite，请先 pnpm install");
  const server = spawn(process.execPath, [viteBin, "--port", String(devPort), "--strictPort"], {
    cwd: desktopDir,
    env: process.env,
    stdio: "inherit",
    shell: false,
  });
  let exited = false;
  server.on("exit", () => {
    exited = true;
  });
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (exited) throw new Error("vite 开发服务器提前退出");
    if (await ping(devPort)) return server;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  server.kill();
  throw new Error(`vite 开发服务器在 30 秒内没有就绪（${devUrl}）`);
}

function ping(port) {
  return new Promise((resolve) => {
    const request = http.get({ host: "127.0.0.1", port, path: "/", timeout: 1200 }, (response) => {
      response.resume();
      resolve(response.statusCode < 500);
    });
    request.on("error", () => resolve(false));
    request.on("timeout", () => {
      request.destroy();
      resolve(false);
    });
  });
}

async function launchElectron(env) {
  const electronCli = localBin("electron", "cli.js");
  if (!isFile(electronCli)) throw new Error("找不到 apps/desktop/node_modules/electron，请先 pnpm install");
  return run(process.execPath, [electronCli, "."], { cwd: desktopDir, env, windowsHide: true });
}

async function main() {
  const mode = process.argv[2] || "start";

  if (mode === "dev") {
    const server = await startViteDev();
    try {
      return await launchElectron({ FORGE_ELECTRON_DEV_URL: devUrl });
    } finally {
      server.kill();
    }
  }

  if (mode === "smoke") {
    if (!isFile(distIndex)) await buildRenderer();
    return launchElectron({ FORGE_ELECTRON_SMOKE: "1", FORGE_ELECTRON_DEV_URL: "" });
  }

  if (mode === "start") {
    await buildRenderer();
    return launchElectron({});
  }

  throw new Error(`未知模式 "${mode}"，可用：start / dev / smoke`);
}

main().then((code) => process.exit(code)).catch((error) => {
  console.error(`[electron] ${error.message}`);
  process.exit(1);
});
