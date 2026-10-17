const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { startBackend, resolvePort, isPortFree, probeHealth, resolvePython } = require("./backend.cjs");

const repoRoot = path.resolve(__dirname, "..", "..", "..");
const shellOrigin = "forge-app://desktop";

const tempDirs = [];
function tempDir(label) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `forge-shell-${label}-`));
  tempDirs.push(dir);
  return dir;
}

test.after(async () => {
  for (const dir of tempDirs) {
    // The child is killed with taskkill /F, so its sqlite file can linger a moment on Windows.
    for (let attempt = 0; attempt < 5; attempt += 1) {
      try {
        fs.rmSync(dir, { recursive: true, force: true });
        break;
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
    }
  }
});

function request(port, options) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port, timeout: 5000, ...options }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        body += chunk;
      });
      response.on("end", () => resolve({ status: response.statusCode, headers: response.headers, body }));
    });
    req.on("error", reject);
    req.on("timeout", () => {
      req.destroy(new Error("request timed out"));
    });
    if (options.body) req.write(options.body);
    req.end();
  });
}

async function listenOnBusyPort(server) {
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  return server.address().port;
}

test("shell backend serves its own API, honors the injected roots and CORS, and frees the port on stop", async () => {
  const workspaceRoot = tempDir("ws");
  const stateDir = tempDir("state");
  const logFile = path.join(stateDir, "backend.log");
  const backend = await startBackend({
    repoRoot,
    workspaceRoot,
    stateDir,
    corsOrigins: [shellOrigin],
    logFile,
  });
  try {
    const health = await request(backend.port, { path: "/health" });
    assert.equal(health.status, 200);
    assert.match(health.body, /coding-agent-api/);

    // The shell's state dir is the one the API actually writes to, not the repo default.
    assert.ok(fs.existsSync(path.join(stateDir, "state.db")), "API did not open the shell state db");

    // FORGE_WORKSPACE_ROOT given by the shell must win over apps/api/.env.
    const outside = await request(backend.port, {
      path: "/api/v1/repositories/validate",
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ path: repoRoot }),
    });
    const verdict = JSON.parse(outside.body);
    assert.equal(verdict.valid, false);
    assert.equal(verdict.reason_code, "PATH_OUTSIDE_WORKSPACE");

    const allowed = await request(backend.port, { path: "/health", headers: { origin: shellOrigin } });
    assert.equal(allowed.headers["access-control-allow-origin"], shellOrigin);
    const rejected = await request(backend.port, { path: "/health", headers: { origin: "http://not-the-shell.example" } });
    assert.equal(rejected.headers["access-control-allow-origin"], undefined);
  } finally {
    await backend.stop();
  }
  assert.equal(await isPortFree(backend.port), true, "stopped backend kept the port bound");
});

test("port memory reuses a silent port but never adopts one that already answers /health", async () => {
  // Node's global agent keeps connections alive, so the probe leaves a live socket behind;
  // without dropping it the squatter server would never finish closing.
  const sockets = new Set();
  const squatter = http.createServer((_req, res) => {
    res.writeHead(200, { "content-type": "application/json" });
    res.end('{"status":"ok","service":"some-other-api"}');
  });
  squatter.on("connection", (socket) => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
  });
  const busy = await listenOnBusyPort(squatter);
  try {
    assert.equal(await probeHealth(busy), true);
    const picked = await resolvePort(busy);
    assert.notEqual(picked, busy, "shell adopted a foreign API on the remembered port");
  } finally {
    for (const socket of sockets) socket.destroy();
    await new Promise((resolve) => squatter.close(resolve));
  }

  const closed = await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
  assert.equal(await probeHealth(closed), false);
  assert.equal(await resolvePort(closed), closed, "a free, silent port should be remembered");
});

test("a missing interpreter is reported as a launch failure, not as a timeout", async () => {
  const workspaceRoot = tempDir("fast-ws");
  const stateDir = tempDir("fast-state");
  const startedAt = Date.now();
  let message = "no error thrown";
  try {
    await startBackend({
      repoRoot,
      workspaceRoot,
      stateDir,
      corsOrigins: [shellOrigin],
      logFile: path.join(stateDir, "backend.log"),
      pythonOverride: path.join(workspaceRoot, "definitely-not-python.exe"),
    });
  } catch (error) {
    message = error.message;
  }
  assert.match(message, /无法启动本地 API/);
  assert.doesNotMatch(message, /30 秒/);
  assert.ok(Date.now() - startedAt < 10_000, "startup failure took the full 30s deadline");
});

test("a backend that exits on its own is reported as an early exit, with the log tail", async () => {
  const workspaceRoot = tempDir("exit-ws");
  const stateDir = tempDir("exit-state");
  // A repo root without apps/api makes uvicorn die immediately, which is the
  // "python exists but the API is broken" case the error page has to describe.
  await assert.rejects(
    startBackend({
      repoRoot: workspaceRoot,
      workspaceRoot,
      stateDir,
      corsOrigins: [shellOrigin],
      logFile: path.join(stateDir, "backend.log"),
      pythonOverride: resolvePython(repoRoot),
    }),
    /本地 API 提前退出/,
  );
});
