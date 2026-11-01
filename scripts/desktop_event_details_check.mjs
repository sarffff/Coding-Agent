import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(new URL("../apps/desktop/package.json", import.meta.url));
const { createServer } = await import(pathToFileURL(require.resolve("vite")));
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const server = await createServer({ root: fileURLToPath(new URL("../apps/desktop", import.meta.url)), configFile: false,
  server: { middlewareMode: true, hmr: false }, appType: "custom", plugins: [{
    name: "tool-detail-click-fixture",
    enforce: "pre",
    transform(source, id) {
      if (!/ChatEventDetails\.tsx\?click-check$/.test(id)) return;
      const fixture = source.replace('const { language } = usePreferences();', 'const language = "en-US";');
      assert.notEqual(fixture, source, "The callback fixture must only replace the preference hook");
      return fixture;
    },
  }] });
try {
  const { ChatEventDetails } = await server.ssrLoadModule("/src/components/ChatEventDetails.tsx");
  const { PreferencesProvider } = await server.ssrLoadModule("/src/lib/preferences.tsx");
  const event = { id: "event", action: "patch.preview", status: "succeeded", run_id: "run", task_id: "task", trace_id: null,
    summary: "Prepared a patch", details: {}, created_at: "2026-10-31T00:00:00Z" };
  const render = (overrides = {}, props = {}) => renderToStaticMarkup(React.createElement(PreferencesProvider, null,
    React.createElement(ChatEventDetails, { event: { ...event, ...overrides }, onOpenFile() {}, ...props })));
  const patch = render({ details: { files: ["src/app.py", "src/app.py", "tests/test_app.py"], additions: 3, deletions: 1 } });
  assert.match(patch, /2 files/);
  assert.match(patch, /3 added lines/);
  assert.match(patch, /1 deleted lines/);
  assert.match(patch, /data-path="src\/app.py" data-line="1"/);
  assert.equal((patch.match(/data-path="src\/app.py"/g) ?? []).length, 1, "File paths are deduplicated");
  const readonly = render({ details: { files: ["app.py"] } }, { onOpenFile: undefined });
  assert.doesNotMatch(readonly, /<button/, "No inactive navigation control when file browsing is unavailable");
  const applied = render({ action: "patch.apply", details: { patch_id: "patch", checkpoint_id: "saved-1" } });
  assert.match(applied, /Checkpoint/);
  assert.match(applied, /saved-1/);
  assert.doesNotMatch(applied, /\d+ files|added lines|deleted lines/, "Checkpoint-only events do not invent file or line counts");
  const tests = render({ action: "tests.run", status: "failed", details: { command: ["python", "-m", "pytest", "tests/test_app.py"],
    check_id: "api", workdir: "apps/api", exit_code: 1,
    failed_tests: [{ path: "tests/test_app.py", line: 27, test_name: "test_answer", message: "Expected <script>42</script>" }] } });
  assert.match(tests, /<details class="chat-event-details" open=""/);
  assert.match(tests, /data-status="failed"/);
  assert.match(tests, /python -m pytest tests\/test_app.py/);
  assert.match(tests, /apps\/api/);
  assert.match(tests, /data-path="tests\/test_app.py" data-line="27"/);
  assert.match(tests, /Expected &lt;script&gt;42&lt;\/script&gt;/);
  assert.doesNotMatch(tests, /<script>/);
  const blocked = render({ action: "tests.baseline", status: "failed", details: { status: "blocked", check_id: "syntax" } });
  assert.match(blocked, /Pre-change validation/);
  assert.match(blocked, /data-status="blocked"/);
  assert.doesNotMatch(blocked, /data-status="passed"/);
  const malformed = render({ details: { files: [null, 1, {}, "", "safe.py"], additions: "10", deletions: -1,
    command: null, failed_tests: [null, false, "bad"], api_key: "do-not-display", output: "PRIVATE LOG" } });
  assert.match(malformed, /1 file/);
  assert.doesNotMatch(malformed, /added lines|deleted lines|do-not-display|PRIVATE LOG/);
  assert.equal(render(), "", "Empty patch metadata stays absent");
  assert.equal(render({ action: "chat.assistant", details: { files: ["secret.py"], command: ["secret"] } }), "", "Only supported tool actions expose metadata");
  const noLine = render({ action: "tests.run", status: "failed", details: { failed_tests: [{ path: "app.py", line: -4, message: "Failed" }, { message: "No file location" }] } });
  assert.match(noLine, /data-path="app.py" data-line="1"/);
  assert.equal((noLine.match(/data-path=/g) ?? []).length, 1, "Missing paths never create a fake file link");
  const { ChatEventDetails: ClickableDetails } = await server.ssrLoadModule("/src/components/ChatEventDetails.tsx?click-check");
  const opened = [];
  const tree = ClickableDetails({ event: { ...event, action: "tests.run", status: "failed", details: {
    files: ["source.py"], failed_tests: [{ path: "tests/test_source.py", line: 29 }, { path: "unknown-line.py", line: null }],
  } }, onOpenFile: (path, line) => opened.push([path, line]) });
  const visit = node => {
    if (Array.isArray(node)) { node.forEach(visit); return; }
    if (!React.isValidElement(node)) return;
    if (node.type === "button" && node.props["data-path"]) node.props.onClick();
    visit(node.props.children);
  };
  visit(tree);
  assert.deepEqual(opened, [["source.py", 1], ["tests/test_source.py", 29], ["unknown-line.py", 1]], "File controls open their actual source path and validated line");
  const savedWindow = globalThis.window;
  try {
    globalThis.window = { localStorage: { getItem: key => key === "forge.language" ? "zh-CN" : "light" }, matchMedia: () => ({ matches: false }) };
    const chinese = render({ action: "tests.baseline", status: "failed", details: { status: "timed_out" } });
    assert.match(chinese, /修改前验证/);
    assert.match(chinese, /超时/);
  } finally {
    if (savedWindow === undefined) delete globalThis.window;
    else globalThis.window = savedWindow;
  }
  console.log("Desktop tool event detail checks passed: actual patch/checkpoint metadata, validation status and locations, safe malformed data, readonly links and bilingual rendering.");
} finally {
  await server.close();
}
