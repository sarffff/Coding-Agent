const fs = require("node:fs");
const path = require("node:path");

function stripWindowsNamespace(file) {
  const value = String(file);
  if (value.startsWith("\\\\?\\UNC\\")) return `\\\\${value.slice(8)}`;
  if (value.startsWith("\\\\?\\")) return value.slice(4);
  return value;
}

function nativeDirectory(file) {
  const resolved = stripWindowsNamespace(path.resolve(file));
  try {
    return stripWindowsNamespace(fs.realpathSync.native(resolved));
  } catch {
    return resolved;
  }
}

function directoryDialogOptions(title, preferred) {
  const properties = ["openDirectory"];
  if (process.platform === "darwin") properties.push("createDirectory");
  const options = { title, properties };
  if (preferred && fs.existsSync(preferred)) options.defaultPath = nativeDirectory(preferred);
  return options;
}

function pickedDirectory(result) {
  if (!result || result.canceled || !result.filePaths?.length) return null;
  return nativeDirectory(result.filePaths[0]);
}

/**
 * Where the workspace-root picker should open. When the user just tried to open a
 * folder that turned out to be outside the workspace, start one level above it so
 * the parent is reachable without climbing the drive by hand.
 */
function workspaceDialogStart(currentRoot, suggestPath) {
  if (typeof suggestPath === "string" && suggestPath) {
    const parent = nativeDirectory(path.dirname(suggestPath));
    if (fs.existsSync(parent)) return parent;
  }
  return currentRoot;
}

module.exports = { stripWindowsNamespace, nativeDirectory, directoryDialogOptions, pickedDirectory, workspaceDialogStart };
