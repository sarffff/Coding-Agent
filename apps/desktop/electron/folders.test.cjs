const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { stripWindowsNamespace, nativeDirectory, directoryDialogOptions, pickedDirectory, workspaceDialogStart } = require("./folders.cjs");

test("Windows long-path prefixes are stripped before the API sees them", () => {
  assert.equal(stripWindowsNamespace("\\\\?\\C:\\Users\\forge"), "C:\\Users\\forge");
  assert.equal(stripWindowsNamespace("\\\\?\\UNC\\server\\share\\repo"), "\\\\server\\share\\repo");
  assert.equal(stripWindowsNamespace("C:\\Users\\forge"), "C:\\Users\\forge");
});

test("directory dialog is Windows-safe and starts in an existing folder", () => {
  const options = directoryDialogOptions("打开文件夹", os.homedir());
  assert.deepEqual(options.properties, process.platform === "darwin" ? ["openDirectory", "createDirectory"] : ["openDirectory"]);
  assert.equal(options.defaultPath, nativeDirectory(os.homedir()));
  assert.equal("defaultPath" in directoryDialogOptions("打开文件夹", path.join(os.homedir(), "missing-forge-folder")), false);
});

test("picker cancel is ignored; a real pick is resolved", () => {
  assert.equal(pickedDirectory({ canceled: true, filePaths: ["C:\\Nope"] }), null);
  assert.equal(pickedDirectory({ canceled: false, filePaths: [] }), null);
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "forge-pick-"));
  try {
    assert.equal(pickedDirectory({ canceled: false, filePaths: [folder] }), nativeDirectory(folder));
  } finally {
    fs.rmdirSync(folder);
  }
});

test("an out-of-workspace pick starts the workspace picker one level above", () => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "forge-root-"));
  const repo = path.join(parent, "app");
  fs.mkdirSync(repo);
  try {
    assert.equal(workspaceDialogStart(os.homedir(), repo), nativeDirectory(parent));
    // 没有建议路径、或建议路径的上级已经消失时，退回当前根目录
    assert.equal(workspaceDialogStart(os.homedir(), undefined), os.homedir());
    assert.equal(workspaceDialogStart(os.homedir(), path.join(parent, "gone", "repo")), os.homedir());
  } finally {
    fs.rmdirSync(repo);
    fs.rmdirSync(parent);
  }
});
