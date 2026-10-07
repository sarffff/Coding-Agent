const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");

function resolvePython(repoRoot, override) {
  const venvName = process.platform === "win32" ? path.join(".venv", "Scripts", "python.exe") : path.join(".venv", "bin", "python");
  const venv = path.join(repoRoot, venvName);
  if (override) return override;
  if (fs.existsSync(venv)) return venv;
  return process.platform === "win32" ? "python" : "python3";
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function isPortFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.once("error", () => resolve(false));
    server.once("listening", () => server.close(() => resolve(true)));
    server.listen(port, "127.0.0.1");
  });
}

/**
 * A remembered port keeps the renderer's module-scope API base URL valid across a
 * backend restart. Anything already answering there is not ours, so it is skipped.
 */
async function resolvePort(preferred) {
  if (!preferred) return freePort();
  if (!(await isPortFree(preferred))) return freePort();
  if (await probeHealth(preferred)) return freePort();
  return preferred;
}

function probeHealth(port, timeoutMs = 1500) {
  return new Promise((resolve) => {
    const request = http.get({ host: "127.0.0.1", port, path: "/health", timeout: timeoutMs }, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.on("error", () => resolve(false));
    request.on("timeout", () => {
      request.destroy();
      resolve(false);
    });
  });
}

async function waitForHealth(port, deadlineMs) {
  const started = Date.now();
  while (Date.now() - started < deadlineMs) {
    if (await probeHealth(port)) return true;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return false;
}

/**
 * Owns one uvicorn child process for the desktop shell.
 * The child is bound to 127.0.0.1 on an ephemeral port, with state and workspace
 * roots taken from the shell's own configuration instead of the launch context.
 */
async function startBackend(options) {
  const { repoRoot, workspaceRoot, stateDir, corsOrigins, logFile, pythonOverride, port: preferredPort } = options;
  const port = await resolvePort(preferredPort);
  const apiDir = path.join(repoRoot, "apps", "api");
  const python = resolvePython(repoRoot, pythonOverride || process.env.FORGE_PYTHON);

  fs.mkdirSync(stateDir, { recursive: true });
  fs.mkdirSync(workspaceRoot, { recursive: true });
  const logStream = fs.createWriteStream(logFile, { flags: "a" });
  const startedAt = new Date().toISOString();
  logStream.write(`\n===== ${startedAt} backend launch (port ${port}) =====\n`);

  const child = spawn(
    python,
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port), "--app-dir", apiDir],
    {
      cwd: repoRoot,
      env: {
        ...process.env,
        FORGE_WORKSPACE_ROOT: workspaceRoot,
        FORGE_STATE_DIR: stateDir,
        FORGE_CORS_ORIGINS: JSON.stringify(corsOrigins),
        PYTHONIOENCODING: "utf-8",
        PYTHONUNBUFFERED: "1",
      },
      shell: false,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    }
  );

  let exited = null;
  child.stdout.on("data", (chunk) => logStream.write(chunk));
  child.stderr.on("data", (chunk) => logStream.write(chunk));
  child.on("error", (error) => {
    exited = { code: -1, signal: null, error: error.message };
    logStream.write(`spawn error: ${error.message}\n`);
  });
  child.on("close", (code, signal) => {
    exited = { code, signal, error: null };
    logStream.write(`closed code=${code} signal=${signal}\n`);
  });

  const healthy = await waitForHealth(port, 30_000);
  if (!healthy) {
    const failure = exited;
    await stopBackend(child);
    logStream.end(`startup failed: backend never became healthy\n`);
    const tail = readLogTail(logFile);
    throw new Error(
      failure?.error
        ? `无法启动本地 API：${failure.error}（Python: ${python}）`
        : `本地 API 在 30 秒内没有就绪（Python: ${python}，日志: ${logFile}）\n${tail}`
    );
  }

  return {
    port,
    python,
    logFile,
    child,
    stop: () => stopBackend(child),
  };
}

function stopBackend(child) {
  if (!child || child.killed || child.exitCode !== null) return Promise.resolve();
  return new Promise((resolve) => {
    const finish = () => {
      clearTimeout(killTimer);
      resolve();
    };
    child.once("close", finish);
    if (process.platform === "win32") {
      const killer = spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { shell: false, stdio: "ignore", windowsHide: true });
      killer.on("error", () => child.kill());
    } else {
      child.kill("SIGTERM");
    }
    const killTimer = setTimeout(() => {
      child.kill("SIGKILL");
      finish();
    }, 2_000);
  });
}

function readLogTail(logFile, lines = 12) {
  try {
    return fs
      .readFileSync(logFile, "utf8")
      .split(/\r?\n/)
      .filter(Boolean)
      .slice(-lines)
      .join("\n");
  } catch {
    return "";
  }
}

function defaultWorkspaceRoot() {
  return path.join(os.homedir(), "forge-workspace");
}

module.exports = { startBackend, stopBackend, freePort, isPortFree, resolvePort, probeHealth, resolvePython, defaultWorkspaceRoot };
