import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(new URL("../apps/desktop/package.json", import.meta.url));
const { createServer } = await import(pathToFileURL(require.resolve("vite")));
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const server = await createServer({ root: fileURLToPath(new URL("../apps/desktop", import.meta.url)), configFile: false,
  server: { middlewareMode: true, hmr: false }, appType: "custom" });
try {
  const { ChatRunSummary } = await server.ssrLoadModule("/src/components/ChatRunSummary.tsx");
  const { PreferencesProvider } = await server.ssrLoadModule("/src/lib/preferences.tsx");
  const checks = [
    { id: "tests", label: "API tests", kind: "pytest", workdir: "apps/api", target: "tests/test_app.py", script: null, timeout_seconds: 120 },
    { id: "types", label: "Types", kind: "script", workdir: ".", target: null, script: "check", timeout_seconds: 60 },
    { id: "build", label: "Build", kind: "script", workdir: "apps/desktop", target: null, script: "build", timeout_seconds: 120 },
  ];
  const result = { id: "result-1", run_id: "run-1", status: "passed", command: ["python", "-m", "pytest"], workdir: ".",
    validation_hash: "current", failed_tests: [], output_pruned: false, output_truncated: false };
  const iteration = { id: "iteration-1", task_id: "task-1", number: 1, changed_files: ["app.py", "tests/test_app.py", "app.py"],
    rolled_back: false, test_result: result, test_runs: [result] };
  const base = { task: { id: "task-1", run_id: "run-1", track: "code", status: "coding", validation_checks: checks,
    completion_criteria: [{ id: "behavior", text: "Preserve expected behavior" }, { id: "validation", text: "Pass original tests" }] },
    action: "review_patch", iteration, last_test: result,
    validation_status: checks.map((check, index) => ({ check, status: ["passed", "stale", "pending"][index], result: index === 2 ? null : { ...result, id: `result-${index}`, command: index === 1 ? ["pnpm", "run", "check"] : result.command } })),
    completion_status: "pending", completion_validation_hash: "current" };
  const render = (state = base, overrides = {}, language = "en-US") => {
    globalThis.window = { localStorage: { getItem: key => key === "forge.language" ? language : "dark" }, matchMedia: () => ({ matches: true }) };
    return renderToStaticMarkup(React.createElement(PreferencesProvider, null, React.createElement(ChatRunSummary,
      { state, onOpenFile() {}, onOpenRun() {}, onOpenValidation() {}, onReviewCompletion() {}, ...overrides })));
  };
  assert.equal(render(null), "");
  assert.equal(render({ ...base, task: { ...base.task, track: "cowork" } }), "");
  const mixed = render();
  assert.match(mixed, /Execution summary/);
  assert.match(mixed, /Changed files<\/span><strong>2<\/strong>/, "Duplicate paths count once");
  assert.match(mixed, /Checks passed<\/span><strong>1\/3<\/strong>/, "Stale checks never count as passed");
  assert.match(mixed, /pnpm run check/);
  assert.match(mixed, /Directory: <code>apps\/api<\/code>/);
  assert.match(mixed, /Target: <code>tests\/test_app.py<\/code>/);
  assert.match(mixed, /Timeout: 120s/);
  assert.match(mixed, /Needs revalidation/);
  assert.match(mixed, /Awaiting confirmation/);
  assert.match(mixed, /Completion criteria<\/span><strong>0\/2<\/strong>/);
  assert.match(mixed, /provide evidence for each criterion/);
  assert.match(render({ ...base, completion_status: "confirmed" }), /Completion criteria<\/span><strong>2\/2<\/strong>/);
  assert.match(render({ ...base, completion_status: "stale" }), /Needs reconfirmation/);
  assert.match(render(base, { busy: true }), /disabled="">Review completion criteria/);

  for (const [status, label] of [["failed", "Failed"], ["timed_out", "Timed out"], ["blocked", "Blocked"], ["not_found", "Check not found"], ["cancelled", "Cancelled"]]) {
    const failed = render({ ...base, validation_status: [{ check: checks[0], status, result: { ...result, status,
      failed_tests: [{ path: "tests/test_app.py", line: 42, test_name: "test_app", message: "expected <ready>" }], output_pruned: status === "blocked", output_truncated: status === "timed_out" } }] });
    assert.match(failed, new RegExp(`data-status="${status}">${label}`));
    assert.match(failed, /tests\/test_app.py:42/);
    assert.match(failed, /expected &lt;ready&gt;/);
    assert.doesNotMatch(failed, /Checks passed<\/span><strong>1\/3/);
    if (status === "blocked") assert.match(failed, /retention policy/);
    if (status === "timed_out") assert.match(failed, /Output was truncated/);
  }
  const preparation = render({ ...base, iteration: null, last_test: null, validation_status: [], completion_status: "not_required",
    task: { ...base.task, validation_checks: [], completion_criteria: [] } });
  assert.match(preparation, /Iteration 0/);
  assert.match(preparation, /No file changes/);
  assert.match(preparation, /No checks have run/);
  assert.match(preparation, /No completion criteria configured/);
  const automatic = { ...base, task: { ...base.task, validation_checks: [] }, validation_status: [] };
  assert.match(render(automatic), /Checks passed<\/span><strong>1\/1/);
  assert.match(render({ ...automatic, action: "run_tests" }), /Checks passed<\/span><strong>0\/1/);
  assert.match(render({ ...automatic, completion_validation_hash: "new-inputs" }), /Needs revalidation/);
  const oldIteration = render({ ...automatic, iteration: { ...iteration, task_id: "other-task" } });
  assert.match(oldIteration, /No file changes/);
  assert.doesNotMatch(oldIteration, />app.py<|python -m pytest/);
  const oldResult = render({ ...automatic, last_test: { ...result, run_id: "another-run" } });
  assert.match(oldResult, /No checks have run/);
  const restored = render({ ...automatic, iteration: { ...iteration, rolled_back: true } });
  assert.match(restored, /Changed files<\/span><strong>0<\/strong>/);
  assert.match(restored, /were rolled back/);
  assert.match(restored, /Needs revalidation/);
  const chinese = render(base, {}, "zh-CN");
  assert.match(chinese, /执行摘要/);
  assert.match(chinese, /需重新验证/);
  assert.match(chinese, /复核完成条件/);
  console.log("Desktop run summary checks passed: real check status, stale results, failure locations, preparation, rollback, completion evidence and bilingual rendering.");
} finally {
  delete globalThis.window;
  await server.close();
}
