import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { runInNewContext } from "node:vm";

const require = createRequire(new URL("../apps/desktop/package.json", import.meta.url));
const { createServer } = await import(pathToFileURL(require.resolve("vite")));
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const composer = { draft: "  Check this  ", completionDraft: "", attachments: [{ name: "notes.md", content: "你好" }], attachmentError: null, readingAttachments: false, submitting: false };
const server = await createServer({ root: fileURLToPath(new URL("../apps/desktop", import.meta.url)), configFile: false,
  server: { middlewareMode: true, hmr: false }, appType: "custom", plugins: [{
    name: "presentation-fixtures", enforce: "pre",
    transform(source, id) {
      if (!/[\\/]ChatPanel\.tsx(?:\?.*)?$/.test(id)) return;
      if (id.includes("pending-input")) return source.replace("useState<Record<string, ComposerState>>({})", `useState<Record<string, ComposerState>>(${JSON.stringify({ "ui-session": composer })})`);
      if (id.includes("over-limit")) return source.replace("useState<Record<string, ComposerState>>({})", `useState<Record<string, ComposerState>>(${JSON.stringify({ "ui-session": { ...composer, draft: "x".repeat(32000) } })})`);
      if (id.includes("stopping-check")) return source.replace("useState(new Set<string>())", 'useState(new Set<string>(["ui-session"]))');
    },
  }] });
try {
  const { PreferencesProvider } = await server.ssrLoadModule("/src/lib/preferences.tsx");
  const panels = {};
  for (const variant of ["normal", "pending-input", "over-limit", "stopping-check"]) {
    panels[variant] = (await server.ssrLoadModule(`/src/components/ChatPanel.tsx?${variant}`)).ChatPanel;
  }
  const noop = () => {};
  const props = { sessionKey: "ui-session", goal: "Fix behavior", plan: undefined, events: [], loopRunning: false, sending: false,
    status: "coding", hasActiveRun: true, track: "code", repositories: [], selectedRepositoryId: "", onSelectRepository: noop,
    onImportFolder: noop, onBrowseFolder: noop, onSend: async () => true, onStop: async () => true, onCancelCowork: async () => true,
    onOpenPublish: noop, reviewPatch: false, onReviewPatch: noop, error: null, mode: "standard", modeLocked: false,
    onModeChange: noop, pending: null, onDecide: noop, budget: null, onOpenFile: noop, onOpenRun: noop, onOpenValidation: noop };
  const render = (overrides = {}, variant = "normal", language = "en-US") => {
    globalThis.window = { localStorage: { getItem: key => key === "forge.language" ? language : "light" }, matchMedia: () => ({ matches: false }) };
    return renderToStaticMarkup(React.createElement(PreferencesProvider, null, React.createElement(panels[variant], { ...props, ...overrides })));
  };
  const budget = { iterations: 2, max_iterations: 8, elapsed_seconds: 125, max_wall_time_seconds: 3600, remote_calls: 4,
    max_remote_calls: 50, consecutive_failures: 2, max_consecutive_failures: 3, started_at: null, exhausted_reason: "wall_time" };
  const event = { id: "context", action: "patch.draft", status: "succeeded", summary: "Draft ready", created_at: "2026-10-31T12:00:00Z",
    details: { context_paths: ["source.py"], context_bytes: 2048, iteration_number: 2, model: "actual-model", provider: "fake", files: ["source.py"] } };
  const context = render({ events: [event], budget, contextLimit: 4096, modelName: "configured-model" }, "pending-input");
  assert.match(context, /2:05 \/ 60:00/);
  assert.match(context, /budget-stat-value">2\/3/);
  assert.match(context, /execution time limit was reached/);
  assert.match(context, /Pending attachments/);
  assert.match(context, /notes\.md/);
  assert.match(context, /6 B/);
  assert.match(context, /<dd>43 \/ 32,000<\/dd>/, "Pending length uses the trimmed text sent with the attachments");
  assert.match(context, /<dd>47 B<\/dd>/, "Pending bytes include UTF-8 attachment content and delimiters");
  assert.match(context, /Last request model<\/span><strong>actual-model/);
  assert.match(context, /<time dateTime="2026-10-31T12:00:00Z"/);
  assert.match(context, /Model token usage is unavailable/);
  assert.match(context, /aria-describedby=/);
  assert.doesNotMatch(context, /NaN|undefined/);
  const overLimit = render({}, "over-limit");
  assert.match(overLimit, /aria-invalid="true"/);
  assert.match(overLimit, /class="chat-send" disabled=""/);
  assert.match(overLimit, /The message and attachments exceed 32000 characters\. Shorten the text or remove an attachment\./);
  const stopped = render({ loopRunning: true }, "stopping-check");
  assert.match(stopped, /class="chat-send chat-send--stop" disabled="" aria-label="Stopping…"/);
  assert.match(stopped, /Waiting for the current request and execution to exit/);
  assert.doesNotMatch(stopped, />Resume</);
  const refresh = render({ error: "Cannot refresh state", onRefresh: noop });
  assert.match(refresh, /role="alert"><span>Cannot refresh state<\/span>/);
  assert.match(refresh, />Refresh</);
  const exhausted = render({ status: "paused", onResume: noop, budget });
  assert.doesNotMatch(exhausted, />Resume</, "An exhausted paused task must not offer a failing resume action");
  assert.match(exhausted, />Execution runs</);
  assert.match(render({ requiresRecovery: true, onRecover: noop }, "normal", "zh-CN"), /服务重启过/);
  const check = { id: "original", label: "Original tests", kind: "pytest", workdir: ".", target: null, script: null, timeout_seconds: 30 };
  const result = { id: "check-result", run_id: "run", status: "passed", command: ["python", "-m", "pytest"], workdir: ".", validation_hash: "hash", failed_tests: [] };
  const state = { task: { id: "task", run_id: "run", track: "code", current_iteration: 1, validation_checks: [check], completion_criteria: [{ id: "behavior", text: "Expected behavior" }] },
    iteration: { id: "iteration", task_id: "task", number: 1, changed_files: ["source.py"], rolled_back: false, test_result: result, test_runs: [result] },
    validation_status: [{ check, status: "stale", result }], completion_status: "pending", completion_validation_hash: "hash" };
  const summary = render({ agentState: state, reviewCompletion: true, onOpenCompletion: noop });
  assert.match(summary, /Execution summary/);
  assert.match(summary, /Checks passed<\/span><strong>0\/1/);
  assert.match(summary, /Completion criteria<\/span><strong>0\/1/);
  assert.match(summary, /Needs revalidation/);
  assert.match(summary, /title="source\.py"/);
  const toolGroup = render({ events: [
    { ...event, id: "tool-pass", action: "tests.run", status: "succeeded", summary: "Tests passed" },
    { ...event, id: "tool-fail", action: "tests.run", status: "failed", summary: "Tests failed" },
  ] });
  assert.match(toolGroup, /Used 2 tools/);
  assert.match(toolGroup, /class="chat-tools-failed">1 failed/);

  const code = (await server.transformRequest("/src/components/ChatPanel.tsx", { ssr: true })).code;
  const start = code.indexOf("const stop =");
  const stopCode = code.slice(start, code.indexOf("const pending =", start));
  let finish, calls = 0, stopping = new Set();
  const stoppingRef = { current: new Set() };
  const stopPendingRef = { current: new Set() };
  const stop = runInNewContext(`${stopCode}; stop`, { sessionKey: "A", stoppingRef, stopPendingRef,
    setStoppingSessions: update => { stopping = update(stopping); },
    clearStopping: key => { stopping.delete(key); stoppingRef.current.delete(key); },
    props: { track: "code", onStop: () => { calls++; return new Promise(resolve => { finish = resolve; }); } } });
  const pending = stop();
  await stop();
  assert.equal(calls, 1, "Repeated stop clicks must share one request");
  assert.equal(stopping.has("A"), true);
  stopping.add("B");
  finish(false);
  await pending;
  assert.equal(stopping.has("A"), false, "Rejected stop restores the source controls");
  assert.equal(stopping.has("B"), true, "A late stop result cannot clear B's status");
  const effectStart = code.indexOf("if (stoppingRef.current.has(sessionKey)");
  const effectCode = code.slice(effectStart, code.indexOf("clearStopping(sessionKey);", effectStart) + "clearStopping(sessionKey);".length);
  const stopState = { sessionKey: "A", stoppingRef, stopPendingRef,
    props: { stateLoaded: false, loopRunning: false, sending: false, error: null },
    clearStopping: key => { stopping.delete(key); stoppingRef.current.delete(key); } };
  stopping.add("A"); stoppingRef.current.add("A");
  runInNewContext(effectCode, stopState);
  assert.equal(stopping.has("A"), true, "A session switch cannot treat an unloaded default false as stopped");
  stopState.props.stateLoaded = true;
  stopPendingRef.current.add("A");
  runInNewContext(effectCode, stopState);
  assert.equal(stopping.has("A"), true, "An unfinished stop request keeps its source controls locked");
  stopPendingRef.current.delete("A");
  stopState.props.loopRunning = true;
  stopState.props.error = "Refresh failed";
  runInNewContext(effectCode, stopState);
  assert.equal(stopping.has("A"), true, "A refresh failure does not imply the worker exited");
  stopState.props.loopRunning = false;
  runInNewContext(effectCode, stopState);
  assert.equal(stopping.has("A"), false, "Loaded idle state after stop completion releases the source controls");
  assert.equal(stopping.has("B"), true);
  console.log("Desktop chat presentation checks passed: budget reasons, actual context/model/time, attachment bytes, input limits, stopping/retry feedback, bilingual state and integrated summaries.");
} finally {
  delete globalThis.window;
  await server.close();
}
