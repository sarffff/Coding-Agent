const fs = require("node:fs");
const path = require("node:path");

/**
 * Resolve a forge-app://desktop/<...> request inside the build output.
 * The scheme is privileged, so anything reachable here is readable by the renderer:
 * paths that leave distDir are refused outright, and a malformed escape falls back to
 * index.html instead of throwing inside the protocol handler.
 */
function resolveDistRequest(distDir, requestUrl) {
  let requested;
  try {
    const url = new URL(requestUrl);
    requested = decodeURIComponent(url.pathname).replace(/^\/+/, "");
  } catch {
    return { file: path.join(distDir, "index.html"), forbidden: false };
  }
  const target = path.resolve(distDir, requested || "index.html");
  if (target !== distDir && !target.startsWith(distDir + path.sep)) {
    return { file: null, forbidden: true };
  }
  const usable = fs.existsSync(target) && !fs.statSync(target).isDirectory();
  return { file: usable ? target : path.join(distDir, "index.html"), forbidden: false };
}

module.exports = { resolveDistRequest };
