const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { resolveDistRequest } = require("./dist-file.cjs");

const distDir = fs.mkdtempSync(path.join(os.tmpdir(), "forge-dist-"));
fs.mkdirSync(path.join(distDir, "assets"), { recursive: true });
fs.writeFileSync(path.join(distDir, "index.html"), "<html>shell</html>");
fs.writeFileSync(path.join(distDir, "assets", "app.js"), "console.log(1)");
const outsideFile = path.join(path.dirname(distDir), "forge-dist-outside.txt");
fs.writeFileSync(outsideFile, "secret");

const url = (file) => `forge-app://desktop${file}`;

test("the scheme root and unknown files fall back to index.html", () => {
  assert.equal(resolveDistRequest(distDir, url("/")).file, path.join(distDir, "index.html"));
  assert.equal(resolveDistRequest(distDir, url("")).file, path.join(distDir, "index.html"));
  assert.equal(resolveDistRequest(distDir, url("/missing.js")).file, path.join(distDir, "index.html"));
  assert.equal(resolveDistRequest(distDir, url("/assets/")).file, path.join(distDir, "index.html"));
});

test("assets inside dist are served from their own path", () => {
  assert.equal(resolveDistRequest(distDir, url("/assets/app.js")).file, path.join(distDir, "assets", "app.js"));
});

test("no hostile url can read a file outside dist", () => {
  const hostile = [
    "/../outside.txt",
    "/../../outside.txt",
    "/%2e%2e/outside.txt",
    "/%2e%2e/%2e%2e/outside.txt",
    "/..%2f..%2foutside.txt",
    "/....//outside.txt",
    "/a/b/../../../outside.txt",
    "/C:/Windows/win.ini",
    "/etc/passwd",
  ];
  for (const requested of hostile) {
    const result = resolveDistRequest(distDir, url(requested));
    if (result.forbidden) {
      assert.equal(result.file, null);
      continue;
    }
    const inside = result.file.startsWith(distDir + path.sep);
    assert.ok(inside || result.file === distDir, `${requested} resolved to ${result.file}`);
  }
});

test("the guard itself refuses escapes that survive url parsing", () => {
  for (const requested of ["/..%2f..%2foutside.txt", "/%2e%2e%2f%2e%2e%2foutside.txt"]) {
    const result = resolveDistRequest(distDir, url(requested));
    assert.equal(result.forbidden, true, `${requested} escaped dist`);
    assert.equal(result.file, null);
  }
});

test("a malformed escape cannot throw inside the protocol handler", () => {
  assert.equal(resolveDistRequest(distDir, url("/%")).file, path.join(distDir, "index.html"));
  assert.equal(resolveDistRequest(distDir, url("/%zz/app.js")).file, path.join(distDir, "index.html"));
});

test("Windows separators and absolute paths stay outside the reach of the scheme", { skip: process.platform !== "win32" }, () => {
  for (const requested of ["/..%5c..%5cwindows%5csystem.ini", "/D:%5coutside.txt", "/D:/outside.txt"]) {
    assert.equal(resolveDistRequest(distDir, url(requested)).forbidden, true, `${requested} escaped dist`);
  }
});

test.after(() => {
  fs.rmSync(distDir, { recursive: true, force: true });
  fs.rmSync(outsideFile, { force: true });
});
