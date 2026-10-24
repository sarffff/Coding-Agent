import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { runInNewContext } from "node:vm";

const require = createRequire(new URL("../apps/desktop/package.json", import.meta.url));
const { createServer } = await import(pathToFileURL(require.resolve("vite")));
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const folder = { id: "plain", name: "Notes", path: "D:/work/Notes", has_git: false,
  branch: "local", head: null, last_commit: null, changed_files: 0, languages: {},
  package_manager: null, registered_at: "2026-10-12T00:00:00Z" };
const checkpoint = { id: "saved-change", files: ["note.txt"], created_at: "2026-10-12T00:00:00Z",
  restored: false, diff: "--- a/note.txt\n+++ b/note.txt\n-old\n+new <note>" };
const validationChecks = [
  { id: "api", label: "API tests", kind: "pytest", workdir: "apps/api", target: "tests", script: null, timeout_seconds: 120 },
  { id: "types", label: "Type check", kind: "script", workdir: ".", target: null, script: "check", timeout_seconds: 60 },
  { id: "build", label: "Build", kind: "script", workdir: ".", target: null, script: "build", timeout_seconds: 120 },
];
const validationState = {
  task: { current_iteration: 0, validation_checks: validationChecks },
  validation_status: validationChecks.map((check, index) => ({ check, status: ["passed", "stale", "pending"][index],
    result: index === 1 ? { command: ["pnpm", "run", "check"] } : null })),
};
const acceptancePreview = {
  patch_id: "acceptance-preview", repository_id: folder.id, files: ["tests/test_existing.py", "package.json"],
  diff: "", additions: 0, deletions: 0, bytes_changed: 0, requires_delete_confirmation: true,
  requires_validation_confirmation: true, validation_changes: ["tests/test_existing.py", "package.json"],
};
const blockedTask = {
  id: "guard-task", runId: "guard-run", repositoryId: folder.id, lifecycleStatus: "coding", taskBranch: null,
  currentIteration: 1, retryCount: 0, nextAction: "Review the changed acceptance inputs", requiresRecovery: false,
  agentBlockedReason: "VALIDATION_REVIEW_REQUIRED", error: "Affected: tests/test_existing.py; package.json",
  updatedAt: "2026-10-24T00:00:00Z", title: "Review patch", repo: "Notes", branch: "local", status: "running",
  time: "now", accent: "amber", mode: "standard", track: "code", validationChecks: [],
  completionCriteria: [], completionConfirmation: null,
};
const preIterationTask = { ...blockedTask, id: "waiting-plan", runId: "waiting-plan-run", currentIteration: 0,
  lifecycleStatus: "awaiting_approval", status: "review", mode: "conservative", agentBlockedReason: null, error: null };
const preIterationEvent = { id: "loop-start", run_id: preIterationTask.runId, action: "agent.loop", status: "started",
  summary: "Waiting for plan approval", details: {}, created_at: "2026-10-24T00:00:00Z" };
const server = await createServer({
  root: fileURLToPath(new URL("../apps/desktop", import.meta.url)),
  configFile: false,
  server: { middlewareMode: true },
  appType: "custom",
  plugins: [{
    name: "desktop-chat-fixtures",
    enforce: "pre",
    transform(source, id) {
      if (/[\\/]App\.tsx(?:\?.*)?$/.test(id)) {
        const withFolder = source.replace("useState<Repository[]>([])",
          `useState<Repository[]>(${JSON.stringify([folder])})`);
        assert.notEqual(withFolder, source, "The app fixture must include an opened folder without tasks");
        const withCodeTab = withFolder.replace('useState<"cowork" | "code">("cowork")',
          'useState<"cowork" | "code">("code")');
        assert.notEqual(withCodeTab, withFolder, "The app fixture must select the Code tab");
        if (id.includes("pre-iteration")) {
          const task = id.includes("pre-iteration-started") ? { ...preIterationTask, currentIteration: 1 } : preIterationTask;
          const seeded = withCodeTab
            .replace("useState(initialTasks)", `useState(${JSON.stringify([task])})`)
            .replace('const [activeTaskId, setActiveTaskId] = useState("")', `const [activeTaskId, setActiveTaskId] = useState("${task.id}")`)
            .replace('const [loopRunning, setLoopRunning] = useState(false)', `const [loopRunning, setLoopRunning] = useState(${id.includes("pre-iteration-running")})`)
            .replace("useState<AuditEvent[]>([])", `useState<AuditEvent[]>(${JSON.stringify([preIterationEvent])})`);
          assert.notEqual(seeded, withCodeTab, "The pre-iteration fixture must retain the first loop event");
          return seeded;
        }
        if (id.includes("validation-confirmation")) {
          const seeded = withCodeTab
            .replace("useState<PatchPreview | null>(null)", `useState<PatchPreview | null>(${JSON.stringify(acceptancePreview)})`)
            .replace('useState<"cancel" | "delete" | "validation" | null>(null)', 'useState<"cancel" | "delete" | "validation" | null>("validation")')
            .replace("useState(initialTasks)", `useState(${JSON.stringify([blockedTask])})`)
            .replace('const [activeTaskId, setActiveTaskId] = useState("")', 'const [activeTaskId, setActiveTaskId] = useState("guard-task")');
          assert.notEqual(seeded, withCodeTab, "The acceptance dialog fixture must seed the preview and confirmation");
          return seeded;
        }
        return withCodeTab;
      }
      if (/[\\/]ValidationChecklist\.tsx$/.test(id)) {
        const seeded = source.replace("useState<AgentState | null>(null)",
          `useState<AgentState | null>(${JSON.stringify(validationState)})`);
        assert.notEqual(seeded, source, "The checklist fixture must include server validation status");
        return seeded;
      }
      if (!/[\\/]RunHistory\.tsx$/.test(id)) return;
      // Seed the initial SSR state; production still loads the records through its effect.
      const seeded = source.replace("useState<RunCheckpoint[]>([])",
        `useState<RunCheckpoint[]>(${JSON.stringify([checkpoint, { ...checkpoint, id: "legacy-change", diff: "" }])})`);
      assert.notEqual(seeded, source, "The history fixture must populate checkpoint state");
      return seeded;
    },
  }],
});

try {
  const { ChatPanel } = await server.ssrLoadModule("/src/components/ChatPanel.tsx");
  const { PreferencesProvider } = await server.ssrLoadModule("/src/lib/preferences.tsx");
  const { ValidationChecklist } = await server.ssrLoadModule("/src/components/ValidationChecklist.tsx");
  const renderChecklist = (overrides = {}) => renderToStaticMarkup(React.createElement(PreferencesProvider, null,
    React.createElement(ValidationChecklist, { taskId: "task", checks: validationChecks, currentIteration: 0,
      revision: "1", hidden: false, disabled: false, onSaved() {}, onBusyChange() {}, ...overrides })));
  const editableChecks = renderChecklist();
  assert.match(editableChecks, /Required validation checks/);
  assert.match(editableChecks, /Relative working directory/);
  assert.match(editableChecks, /package.json script name/);
  assert.match(editableChecks, /type="number" min="1" max="300"/);
  assert.match(editableChecks, /Add check/);
  assert.match(editableChecks, /Save checklist/);
  const lockedChecks = renderChecklist({ currentIteration: 1 });
  assert.match(lockedChecks, /first iteration has started/);
  assert.doesNotMatch(lockedChecks, /<input|<select|Add check|Save checklist/);
  assert.match(lockedChecks, /API tests/);
  assert.match(lockedChecks, /Needs revalidation/);
  assert.match(lockedChecks, /pnpm run check/);
  assert.match(lockedChecks, /Pending/);
  assert.match(renderChecklist({ checks: [] }), /No checklist configured/);
  const props = {
    goal: null, plan: undefined, events: [], loopRunning: false, sending: false,
    status: null, hasActiveRun: false, track: "code", repositories: [folder],
    selectedRepositoryId: folder.id, onSelectRepository() {}, onImportFolder() {},
    onBrowseFolder() {}, onSend: async () => false, onStop() {}, onOpenPublish() {},
    reviewPatch: false, onReviewPatch() {}, error: null, mode: "standard",
    modeLocked: false, onModeChange() {}, pending: null, onDecide() {}, budget: null,
  };
  const render = (overrides) => renderToStaticMarkup(React.createElement(PreferencesProvider, null,
    React.createElement(ChatPanel, { ...props, ...overrides })));

  const firstFailure = render({ error: "Cannot open this folder" });
  assert.match(firstFailure, /role="alert">Cannot open this folder/);
  assert.match(firstFailure, /D:\/work\/Notes/);
  assert.match(firstFailure, /Browse files/);
  assert.match(firstFailure, /AI edits, tests and rollback are available/);

  const localResult = render({ goal: "Fix tests", status: "ready_for_pr", hasActiveRun: true });
  assert.match(localResult, /Review local results/);
  assert.doesNotMatch(localResult, />Publish<|git init/);

  const gitResult = render({ goal: "Fix tests", status: "ready_for_pr", hasActiveRun: true,
    repositories: [{ ...folder, has_git: true }] });
  assert.match(gitResult, />Publish</);

  const review = render({ goal: "Fix tests", status: "coding", hasActiveRun: true,
    mode: "conservative", reviewPatch: true });
  assert.match(review, />Review patch</);
  const prepared = render({ goal: "Fix tests", status: "awaiting_approval", hasActiveRun: true, prepareTask: true });
  assert.match(prepared, /Review before starting/);

  const cowork = render({ track: "cowork", selectedRepositoryId: "", sending: true });
  assert.match(cowork, /<details class="folder-picker">/);
  assert.match(cowork, /<button[^>]*class="folder-picker-option is-selected"[^>]*disabled=""[^>]*aria-pressed="true">[\s\S]*?<strong>No folder<\/strong>/);
  assert.match(cowork, /<textarea[^>]*disabled=""/);

  const { default: App } = await server.ssrLoadModule("/src/App.tsx");
  const app = renderToStaticMarkup(React.createElement(PreferencesProvider, null, React.createElement(App)));
  assert.match(app, /Review before starting/);
  const preparationHandoffs = markup => (markup.match(/<div class="chat-handoff">[\s\S]*?<\/div>/g) ?? []).filter(item => item.includes("Review before starting"));
  const { default: WaitingPlanApp } = await server.ssrLoadModule("/src/App.tsx?pre-iteration");
  const waitingPlan = renderToStaticMarkup(React.createElement(PreferencesProvider, null, React.createElement(WaitingPlanApp)));
  assert.equal(preparationHandoffs(waitingPlan).length, 1, "A loop waiting for first plan approval must still allow editing completion criteria");
  const { default: StartedIterationApp } = await server.ssrLoadModule("/src/App.tsx?pre-iteration-started");
  const startedIteration = renderToStaticMarkup(React.createElement(PreferencesProvider, null, React.createElement(StartedIterationApp)));
  assert.equal(preparationHandoffs(startedIteration).length, 0, "Beginning the first coding iteration must remove the preparation entry");
  const { default: RunningPreparationApp } = await server.ssrLoadModule("/src/App.tsx?pre-iteration-running");
  const runningPreparation = renderToStaticMarkup(React.createElement(PreferencesProvider, null, React.createElement(RunningPreparationApp)));
  assert.equal(preparationHandoffs(runningPreparation).length, 0, "A running loop must finish before preparation can be edited");
  const emptyFolder = app.match(/<details class="rail-folder-group"[\s\S]*?<\/details>/)?.[0];
  assert.ok(emptyFolder, "An opened folder must appear before it has any tasks");
  assert.match(emptyFolder, /<summary title="D:\/work\/Notes">/);
  assert.match(emptyFolder, /<span>Notes<\/span>/);
  assert.match(emptyFolder, /class="rail-folder-count">0<\/span>/);
  assert.match(emptyFolder, /<button[^>]*class="rail-folder-new"/);
  const apiModule = await server.ssrLoadModule("/src/lib/api.ts");
  const errorsModule = await server.ssrLoadModule("/src/lib/errorMessages.ts");
  for (const errorCode of ["MODEL_NO_CHANGE", "AGENT_NO_PROGRESS", "AGENT_EXECUTION_FAILED", "VALIDATION_REVIEW_REQUIRED", "VALIDATION_BASELINE_BLOCKED", "VALIDATION_INPUT_UNAVAILABLE", "VALIDATION_SCOPE_TOO_LARGE", "COMPLETION_REQUIRED", "COMPLETION_NOT_REQUIRED", "COMPLETION_CONFIRMATION_STALE", "COMPLETION_VALIDATION_REQUIRED", "INVALID_COMPLETION_EVIDENCE"]) {
    const error = new apiModule.ApiRequestError(errorCode, "untranslated fallback");
    errorsModule.setErrorLanguage("zh-CN");
    const chinese = errorsModule.describeError(error, "missing translation");
    errorsModule.setErrorLanguage("en-US");
    const english = errorsModule.describeError(error, "missing translation");
    assert.notEqual(chinese, "untranslated fallback");
    assert.notEqual(english, "untranslated fallback");
    assert.notEqual(chinese, english, `${errorCode} must be translated in both languages`);
  }
  const { default: AcceptanceApp } = await server.ssrLoadModule("/src/App.tsx?validation-confirmation");
  const acceptanceDialog = renderToStaticMarkup(React.createElement(PreferencesProvider, null, React.createElement(AcceptanceApp)));
  assert.match(acceptanceDialog, /Review acceptance changes/);
  assert.match(acceptanceDialog, /rebuilds the acceptance baseline/);
  assert.match(acceptanceDialog, /<pre>tests\/test_existing.py\npackage.json<\/pre>/);
  assert.match(acceptanceDialog, /Creating or deleting an empty file has no text diff/);
  const emptyApplyButton = acceptanceDialog.match(/<button[^>]*>Apply and checkpoint<\/button>/)?.[0];
  assert.ok(emptyApplyButton, "A valid empty-file patch must expose its apply action");
  assert.doesNotMatch(emptyApplyButton, /disabled=/);
  assert.match(acceptanceDialog, /review and explicitly confirm the acceptance changes/);
  assert.match(acceptanceDialog, /Affected: tests\/test_existing.py; package.json/);
  const capturedPatchBodies = [];
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async (_url, options) => {
      capturedPatchBodies.push(JSON.parse(options.body));
      return { ok: true, json: async () => ({}) };
    };
    await apiModule.applyPatch("run", "patch", true);
    await apiModule.applyPatch("run", "patch", true, true);
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(capturedPatchBodies[0].confirm, true);
  assert.equal(capturedPatchBodies[0].confirm_validation_changes, false);
  assert.equal(capturedPatchBodies[1].confirm_validation_changes, true);

  const completionCriteria = [{ id: "goal", text: "The original task goal is met." }];
  const completionRequests = [];
  try {
    globalThis.fetch = async (url, options) => {
      completionRequests.push({ url, method: options.method, body: JSON.parse(options.body) });
      return { ok: true, json: async () => ({}) };
    };
    await apiModule.createTask("Fix value", "repository", "standard", "code", validationChecks, completionCriteria);
    await apiModule.saveCompletionCriteria("task", completionCriteria);
    await apiModule.confirmCompletion("task", { scope_hash: "scope-current", validation_hash: "validation-current",
      iteration_id: "iteration-current", evidence: [{ criterion_id: "goal", evidence: "The regression passes." }] });
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.deepEqual(completionRequests[0].body.completion_criteria, completionCriteria);
  assert.equal(completionRequests[1].method, "PUT");
  assert.match(completionRequests[1].url, /\/tasks\/task\/completion-criteria$/);
  assert.deepEqual(completionRequests[1].body, { criteria: completionCriteria });
  assert.equal(completionRequests[2].method, "POST");
  assert.match(completionRequests[2].url, /\/tasks\/task\/completion-confirmation$/);
  assert.equal(completionRequests[2].body.scope_hash, "scope-current");
  assert.equal(completionRequests[2].body.validation_hash, "validation-current");
  assert.equal(completionRequests[2].body.iteration_id, "iteration-current");
  const { CompletionCriteria } = await server.ssrLoadModule("/src/components/CompletionCriteria.tsx");
  const completionEvidence = [{ criterion_id: "goal", evidence: "  The regression passes.  " }];
  const completionState = {
    task: { id: "task", completion_criteria: completionCriteria, completion_confirmation: null },
    iteration: { id: "iteration-current" }, completion_status: "pending", can_confirm_completion: true,
    completion_scope_hash: "scope-current", completion_validation_hash: "validation-current",
  };
  const priorConfirmation = { scope_hash: "scope-old", validation_hash: "validation-old", iteration_id: "iteration-old", evidence: completionEvidence };
  const renderCompletion = (overrides = {}) => renderToStaticMarkup(React.createElement(PreferencesProvider, null,
    React.createElement(CompletionCriteria, { taskId: "task", goal: "Fix the original goal", criteria: completionCriteria,
      confirmation: null, currentIteration: 1, state: completionState, disabled: false,
      onSaved() {}, onBusyChange() {}, ...overrides })));
  const pendingCompletion = renderCompletion();
  assert.match(pendingCompletion, /Original goal.*Fix the original goal/);
  assert.match(pendingCompletion, /The original task goal is met/);
  assert.match(pendingCompletion, /does not confirm completion/);
  assert.match(pendingCompletion, /<button[^>]*disabled=""[^>]*>Confirm completion criteria<\/button>/,
    "Missing per-criterion evidence must disable confirmation");
  assert.match(pendingCompletion, /<textarea[^>]*maxLength="1000"/);
  const staleState = { ...completionState, completion_status: "stale", task: { ...completionState.task, completion_confirmation: priorConfirmation } };
  const staleCompletion = renderCompletion({ confirmation: priorConfirmation, state: staleState });
  assert.match(staleCompletion, /Needs review again/);
  assert.match(staleCompletion, /Review the evidence and confirm again/);
  assert.match(staleCompletion, /<button class="small-action" type="submit">Confirm completion criteria<\/button>/);
  assert.match(renderCompletion({ confirmation: priorConfirmation, state: { ...staleState, can_confirm_completion: false } }),
    /<button[^>]*disabled=""[^>]*>Confirm completion criteria<\/button>/);
  assert.match(renderCompletion({ state: { ...staleState, task: { ...staleState.task, id: "old-task" } } }),
    /<button[^>]*disabled=""[^>]*>Confirm completion criteria<\/button>/,
    "State from another task must not enable confirmation");
  assert.doesNotMatch(renderCompletion({ state: { ...staleState, completion_status: "confirmed", can_confirm_completion: false } }), /Confirm completion criteria<\/button>/);
  assert.equal(renderCompletion({ criteria: [], state: null }), "", "Empty criteria keep the legacy workflow");
  const criteriaEditor = renderCompletion({ preparation: true, currentIteration: 0, state: null });
  assert.match(criteriaEditor, /Add criterion/);
  assert.match(criteriaEditor, /Save completion criteria/);
  assert.match(criteriaEditor, /<input[^>]*maxLength="500"/);
  assert.doesNotMatch(renderCompletion({ preparation: true, state: null }), /<input|Add criterion|Save completion criteria/);
  const completionHandoff = render({ goal: "Fix tests", status: "ready_for_pr", hasActiveRun: true, reviewCompletion: true });
  assert.match(completionHandoff, />Review completion</);
  assert.doesNotMatch(completionHandoff, /Review local results|>Publish</);

  const { RunHistory } = await server.ssrLoadModule("/src/components/RunHistory.tsx");
  const history = renderToStaticMarkup(React.createElement(PreferencesProvider, null,
    React.createElement(RunHistory, { runId: "test-run", revision: "", onRestored: async () => {}, disabled: false })));
  assert.equal((history.match(/<summary>View changes<\/summary>/g) ?? []).length, 1);
  assert.match(history, /<pre class="diff-view">--- a\/note.txt/);
  assert.match(history, /\+new &lt;note&gt;/);
  assert.match(history, /legacy-change/);
  assert.match(history, /Restore checkpoint/);

  // Execute the real compiled callbacks with API stubs; no browser or new test dependency.
  const { code } = await server.transformRequest("/src/App.tsx", { ssr: true });
  const validationStateStart = code.indexOf("const updateValidationState =");
  const validationStateHandler = code.slice(validationStateStart, code.indexOf("}, []);", validationStateStart) + "}, []);".length);
  const validationStateImport = validationStateHandler.match(/([\w$]+)\.useCallback/)[1];
  const sharedValidation = { state: null };
  const acceptValidationState = runInNewContext(`${validationStateHandler}; updateValidationState;`, {
    [validationStateImport]: { useCallback: callback => callback }, taskRef: { current: "task-current" },
    setValidationAgentState: state => { sharedValidation.state = state; },
  });
  acceptValidationState({ task: { id: "task-old" } });
  assert.equal(sharedValidation.state, null, "A late validation response must not populate another task's completion review");
  acceptValidationState({ task: { id: "task-current" } });
  assert.equal(sharedValidation.state.task.id, "task-current");
  // Error text now comes from a module table; the slices below only need the shape of it.
  const errorImport = code.match(/([\w$]+)\.describeError/)?.[1];
  const errorStub = { describeError: (reason, fallback) => (reason instanceof Error ? reason.message : fallback) };
  const patchHandlers = code.slice(code.indexOf("const applyActivePatch ="), code.indexOf("const createBranch ="));
  const patchApiName = patchHandlers.match(/([\w$]+)\.applyPatch/)[1];
  const checkPatchConfirmation = ({ deletion = false, validation = false } = {}) => {
    const saved = { confirmAction: null, patchRunning: false, requests: [] };
    const context = {
      ...(errorImport ? { [errorImport]: errorStub } : {}),
      [patchApiName]: {
        applyPatch: async (...args) => { saved.requests.push(args); return { checkpoint: { id: "checkpoint" }, applied_files: ["tests/test_existing.py"] }; },
        listRunEvents: async () => ({ items: [] }), listRepositories: async () => ({ items: [] }),
        listIterations: async () => ({ items: [] }), getTask: async () => ({ id: "task" }),
      },
      activeTask: { id: "task", runId: "run" }, patchRunning: false,
      patchPreviewResult: { patch_id: "patch", files: ["tests/test_existing.py"], requires_delete_confirmation: deletion,
        requires_validation_confirmation: validation, validation_changes: validation ? ["tests/test_existing.py"] : [] },
      patchConfirmationRef: { current: null }, t: key => key, updateTaskFromApi() {},
      setPatchError() {}, setPatchApplied() {}, setRunEvents() {}, setRepositories() {}, setIterations() {},
      setPatchPreviewResult: value => { context.patchPreviewResult = value; },
      setConfirmAction: value => { saved.confirmAction = value; context.confirmAction = value; },
      setPatchRunning: value => { saved.patchRunning = value; context.patchRunning = value; },
    };
    const callbacks = runInNewContext(`${patchHandlers}; ({ applyActivePatch, cancelPatchPreview, closeConfirmation });`, context);
    return { ...callbacks, saved, context };
  };
  const validationPatch = checkPatchConfirmation({ validation: true });
  await validationPatch.applyActivePatch();
  assert.equal(validationPatch.saved.confirmAction, "validation");
  assert.equal(validationPatch.saved.requests.length, 0, "Existing acceptance changes need their own review");
  await validationPatch.applyActivePatch("validation");
  assert.equal(validationPatch.saved.requests[0][3], true, "Only explicit acceptance review sets the dedicated confirmation");
  const combinedPatch = checkPatchConfirmation({ deletion: true, validation: true });
  await combinedPatch.applyActivePatch();
  assert.equal(combinedPatch.saved.confirmAction, "delete");
  await combinedPatch.applyActivePatch("delete");
  assert.equal(combinedPatch.saved.confirmAction, "validation");
  assert.equal(combinedPatch.saved.requests.length, 0, "Deletion approval must not approve changed acceptance inputs");
  await combinedPatch.applyActivePatch("validation");
  assert.equal(combinedPatch.saved.requests.length, 1);
  assert.equal(combinedPatch.saved.requests[0][3], true);
  const deletionPatch = checkPatchConfirmation({ deletion: true });
  await deletionPatch.applyActivePatch();
  await deletionPatch.applyActivePatch("delete");
  assert.equal(deletionPatch.saved.requests[0][3], false, "Ordinary deletion approval keeps acceptance confirmation false");
  const canceledPatch = checkPatchConfirmation({ deletion: true, validation: true });
  await canceledPatch.applyActivePatch();
  await canceledPatch.applyActivePatch("delete");
  canceledPatch.cancelPatchPreview();
  assert.equal(canceledPatch.saved.requests.length, 0, "Canceling the second review must not apply the patch");
  assert.equal(canceledPatch.context.patchConfirmationRef.current, null);
  const closedPatch = checkPatchConfirmation({ deletion: true, validation: true });
  await closedPatch.applyActivePatch();
  await closedPatch.applyActivePatch("delete");
  closedPatch.closeConfirmation();
  assert.equal(closedPatch.saved.requests.length, 0);
  assert.equal(closedPatch.context.patchConfirmationRef.current, null);
  await closedPatch.applyActivePatch();
  assert.equal(closedPatch.saved.confirmAction, "delete", "Closing review must discard the earlier deletion confirmation");
  const stalePatch = checkPatchConfirmation({ deletion: true });
  await stalePatch.applyActivePatch();
  stalePatch.context.patchPreviewResult = { ...stalePatch.context.patchPreviewResult, patch_id: "new-preview" };
  await stalePatch.applyActivePatch("delete");
  assert.equal(stalePatch.saved.requests.length, 0, "A confirmation must stay bound to its preview");
  assert.equal(stalePatch.saved.confirmAction, null);
  const switchedPatch = checkPatchConfirmation({ validation: true });
  await switchedPatch.applyActivePatch();
  switchedPatch.context.activeTask = { id: "other-task", runId: "other-run" };
  await switchedPatch.applyActivePatch("validation");
  assert.equal(switchedPatch.saved.requests.length, 0, "Switching tasks must invalidate the pending review");
  const ordinaryPatch = checkPatchConfirmation();
  await ordinaryPatch.applyActivePatch();
  assert.equal(ordinaryPatch.saved.requests[0][3], false);
  const checklistCode = (await server.transformRequest("/src/components/ValidationChecklist.tsx", { ssr: true })).code;
  const checklistHandlers = checklistCode.slice(checklistCode.indexOf("const update ="), checklistCode.indexOf("const statuses ="));
  const checklistApiName = checklistHandlers.match(/([\w$]+)\.saveValidationChecks/)[1];
  const checklistErrorName = checklistHandlers.match(/([\w$]+)\.describeError/)[1];
  const edited = { dirty: false, failure: false, saved: [], draft: validationChecks };
  const checklistContext = {
    [checklistApiName]: { saveValidationChecks: async (_task, checks) => {
      if (edited.failure) throw new Error("save failed");
      edited.saved.push(checks);
      return { validation_checks: checks };
    } }, [checklistErrorName]: errorStub,
    draft: validationChecks, taskId: "task", unavailable: false, changed: true, t: key => key,
    setDraft: value => { edited.draft = typeof value === "function" ? value(edited.draft) : value; checklistContext.draft = edited.draft; },
    onDirtyChange: value => { edited.dirty = value; }, onSaved() {}, onBusyChange() {},
    setSaving() {}, setSaved() {}, setError() {},
  };
  const checklistCallbacks = runInNewContext(`${checklistHandlers}; ({ update, save });`, checklistContext);
  checklistCallbacks.update("api", { label: "  API acceptance  " });
  assert.equal(edited.dirty, true, "Editing a required check must immediately block task startup");
  edited.failure = true;
  await checklistCallbacks.save();
  assert.equal(edited.dirty, true, "A failed save must keep startup blocked");
  edited.failure = false;
  await checklistCallbacks.save();
  assert.equal(edited.dirty, false, "Only a successful checklist save can unblock startup");
  assert.equal(edited.saved[0][0].label, "API acceptance");
  const completionCode = (await server.transformRequest("/src/components/CompletionCriteria.tsx", { ssr: true })).code;
  const completionHandlers = completionCode.slice(completionCode.indexOf("const update ="), completionCode.indexOf("if (!preparation && !criteria.length)"));
  const completionApiName = completionHandlers.match(/([\w$]+)\.saveCompletionCriteria/)[1];
  const completionErrorName = completionHandlers.match(/([\w$]+)\.describeError/)[1];
  const criteriaChanges = { dirty: false, failure: false, saved: [], requests: [], draft: completionCriteria };
  const completionContext = {
    [completionApiName]: {
      saveCompletionCriteria: async (_task, criteria) => {
        if (criteriaChanges.failure) throw new Error("criteria save failed");
        criteriaChanges.saved.push(criteria);
        return { completion_criteria: criteria };
      },
      confirmCompletion: async (_task, payload) => { criteriaChanges.requests.push(payload); return { id: "task" }; },
    },
    [completionErrorName]: errorStub,
    draft: completionCriteria, taskId: "task", unavailable: false, changed: true, canConfirm: false,
    activeState: staleState, displayedCriteria: completionCriteria, evidence: { goal: "  The regression passes.  " },
    t: key => key, setSaving() {}, setSaved() {}, setError() {}, onSaved() {}, onBusyChange() {},
    onDirtyChange: value => { criteriaChanges.dirty = value; },
    setDraft: value => { criteriaChanges.draft = typeof value === "function" ? value(criteriaChanges.draft) : value; completionContext.draft = criteriaChanges.draft; },
  };
  const completionCallbacks = runInNewContext(`${completionHandlers}; ({ update, save, confirm });`, completionContext);
  completionCallbacks.update("goal", "  The original goal is met.  ");
  assert.equal(criteriaChanges.dirty, true);
  criteriaChanges.failure = true;
  await completionCallbacks.save();
  assert.equal(criteriaChanges.dirty, true, "A failed criteria save must preserve startup blocking");
  criteriaChanges.failure = false;
  await completionCallbacks.save();
  assert.equal(criteriaChanges.dirty, false);
  assert.equal(criteriaChanges.saved[0][0].text, "The original goal is met.");
  await completionCallbacks.confirm();
  assert.equal(criteriaChanges.requests.length, 0, "Disabled completion must not submit even when called directly");
  completionContext.canConfirm = true;
  await completionCallbacks.confirm();
  assert.equal(criteriaChanges.requests[0].scope_hash, "scope-current");
  assert.equal(criteriaChanges.requests[0].validation_hash, "validation-current");
  assert.equal(criteriaChanges.requests[0].iteration_id, "iteration-current", "Reconfirmation must bind the current iteration, not the prior confirmation");
  assert.equal(criteriaChanges.requests[0].evidence[0].evidence, "The regression passes.");
  const handlers = code.slice(code.indexOf("const sendChatMessage ="), code.indexOf("const decideChatApproval ="));
  const apiName = handlers.match(/([\w$]+)\.createTask/)[1];
  const healthHandler = code.slice(code.indexOf("const refreshHealth ="), code.indexOf("void refreshHealth();"));
  const health = { status: "connected", failure: true, calls: 0 };
  const healthContext = { checking: false, controller: new AbortController(),
    [apiName]: { getApiHealth: async () => { health.calls++; if (health.failure) throw new Error("offline"); } },
    setApiStatus: value => { health.status = value; },
  };
  const refreshHealth = runInNewContext(`${healthHandler}; refreshHealth;`, healthContext);
  await refreshHealth();
  assert.equal(health.status, "offline", "An API outage must update the connection indicator");
  health.failure = false;
  await refreshHealth();
  assert.equal(health.status, "connected", "The indicator must recover without remounting the app");
  healthContext.controller.abort();
  await refreshHealth();
  assert.equal(health.calls, 2, "Unmounted health polling must stop making requests");

  const pollHandler = code.slice(code.indexOf("const tick ="), code.indexOf("void tick();"));
  const poll = { status: "connected", error: null, calls: 0, failure: true, loopRunning: false };
  let releasePoll;
  class StubApiRequestError extends Error {}
  const pollApi = {
    ApiRequestError: StubApiRequestError,
    getAgentState: async () => {
      poll.calls++;
      if (poll.failure) throw new Error("network down");
      return { task: {}, action: "run_tests", loop_running: false };
    },
    listRunEvents: async () => ({ items: [] }),
  };
  const pollContext = {
    ...(errorImport ? { [errorImport]: errorStub } : {}), [apiName]: pollApi,
    controller: new AbortController(), refreshing: false, taskId: "task", runId: "run", Error,
    setApiStatus: value => { poll.status = value; }, setChatPollError: value => { poll.error = value; },
    setChatError: () => { throw new Error("Polling must not erase a send error"); },
    setLoopRunning: value => { poll.loopRunning = value; }, setRunEvents() {}, setChatPending() {}, setChatBudget() {}, setChatAction() {},
    updateTaskFromApi() {}, t: key => key,
  };
  const tick = runInNewContext(`${pollHandler}; tick;`, pollContext);
  await tick();
  assert.equal(poll.status, "offline");
  assert.equal(poll.error, "network down", "Polling failure must be visible");
  poll.failure = false;
  await tick();
  assert.equal(poll.status, "connected");
  assert.equal(poll.error, null, "A successful refresh must clear only the polling error");
  pollApi.getAgentState = async () => { throw new StubApiRequestError("task unavailable"); };
  await tick();
  assert.equal(poll.status, "connected", "An API domain error does not mean the backend is offline");
  assert.equal(poll.error, "task unavailable");
  pollApi.getAgentState = () => { poll.calls++; return new Promise(resolve => { releasePoll = resolve; }); };
  const pendingPoll = tick();
  await tick();
  assert.equal(poll.calls, 3, "A slow refresh must not overlap with the next tick");
  pollContext.controller.abort();
  releasePoll({ task: {}, loop_running: true });
  await pendingPoll;
  assert.equal(poll.status, "connected");
  assert.equal(poll.loopRunning, false, "A response arriving after cleanup must not change the selected task");
  const newSession = code.slice(code.indexOf("const startNewChatSession ="), code.indexOf("const selectChatSession ="));
  const folderHandlers = code.slice(code.indexOf("const openRegisteredFolder ="), code.indexOf("const changeWorkspaceRoot ="));
  const folderApiName = folderHandlers.match(/([\w$]+)\.registerRepository/)[1];
  const bridgeName = folderHandlers.match(/([\w$]+)\.desktopBridge/)[1];
  const checkFolderCallbacks = ({ desktop = true, picked = folder.path, error = null } = {}) => {
    const saved = { repositories: [folder], selectedRepositoryId: "previous-folder", railTab: "cowork",
      activeNav: "settings", activeTaskId: "old-task", chatComposingNew: false, sidebarOpen: true,
      repositoryDialogOpen: false, repositorySaving: false, repositoryError: null, repositoryPathInput: "",
      runEvents: ["old-event"], chatPending: {}, chatBudget: {}, chatAction: "working", chatError: "old-error",
      loopRunning: false, sourceLocation: { path: "old-folder.py", line: 3 }, registered: [], picked: [] };
    const context = {
      ...(errorImport ? { [errorImport]: errorStub } : {}),
      [folderApiName]: {
        validateRepository: async path => error ? { valid: false, reason: error.message, reason_code: "PATH_OUTSIDE_WORKSPACE" } : { valid: true },
        registerRepository: async path => {
          saved.registered.push(path);
          if (error) throw error;
          return folder;
        },
        getWorkspaceSummary: async () => ({}),
      },
      [bridgeName]: { desktopBridge: desktop ? {
        pickDirectory: async options => { saved.picked.push(options); return picked; },
      } : undefined },
      desktopRuntime: { workspaceRoot: "D:/work" }, repositoryOpeningRef: { current: false },
      repositoryPathInput: "", setWorkspaceSummary() {}, t: key => key, Error, console,
    };
    for (const key of Object.keys(saved).filter(key => !["registered", "picked"].includes(key))) {
      context[`set${key[0].toUpperCase()}${key.slice(1)}`] = value => {
        saved[key] = typeof value === "function" ? value(saved[key]) : value;
      };
    }
    const callbacks = runInNewContext(`${newSession}\n${folderHandlers}; ({ addRepository, openLocalFolder });`, context);
    return { ...callbacks, saved, opening: context.repositoryOpeningRef };
  };
  const desktopFolder = checkFolderCallbacks();
  desktopFolder.addRepository();
  desktopFolder.addRepository();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(desktopFolder.saved.picked.length, 1, "Repeated clicks must not open multiple native choosers");
  assert.equal(desktopFolder.saved.picked[0].fallbackPath, "D:/work");
  assert.deepEqual(desktopFolder.saved.registered, [folder.path]);
  assert.equal(desktopFolder.saved.repositories.length, 1, "Reopening a folder must not duplicate it");
  assert.equal(desktopFolder.saved.selectedRepositoryId, folder.id);
  assert.equal(desktopFolder.saved.sourceLocation, null, "Opening another folder clears the previous file selection");
  assert.equal(desktopFolder.saved.railTab, "code");
  assert.equal(desktopFolder.saved.activeNav, "chat");
  assert.equal(desktopFolder.saved.chatComposingNew, true);
  assert.equal(desktopFolder.saved.activeTaskId, "");
  assert.equal(desktopFolder.saved.repositoryDialogOpen, false);
  assert.equal(desktopFolder.saved.sidebarOpen, false);
  assert.equal(desktopFolder.saved.repositorySaving, false);
  assert.equal(desktopFolder.opening.current, false);

  const canceledFolder = checkFolderCallbacks({ picked: null });
  await canceledFolder.openLocalFolder();
  assert.deepEqual(canceledFolder.saved.registered, [], "Canceling the chooser must not register a folder");
  assert.equal(canceledFolder.saved.selectedRepositoryId, "previous-folder");
  assert.equal(canceledFolder.saved.chatComposingNew, false);
  assert.equal(canceledFolder.saved.repositoryDialogOpen, false);
  assert.equal(canceledFolder.saved.repositorySaving, false);

  const browserFolder = checkFolderCallbacks({ desktop: false });
  browserFolder.addRepository();
  assert.equal(browserFolder.saved.repositoryDialogOpen, true);
  assert.deepEqual(browserFolder.saved.registered, []);
  await browserFolder.openLocalFolder(folder.path);
  assert.deepEqual(browserFolder.saved.registered, [folder.path]);
  assert.equal(browserFolder.saved.selectedRepositoryId, folder.id);
  assert.equal(browserFolder.saved.repositoryDialogOpen, false);

  const rejectedFolder = checkFolderCallbacks({ error: new Error("Outside the workspace") });
  await rejectedFolder.openLocalFolder();
  assert.equal(rejectedFolder.saved.repositoryPathInput, folder.path);
  assert.equal(rejectedFolder.saved.repositoryError, "Outside the workspace");
  assert.equal(rejectedFolder.saved.repositoryDialogOpen, true);
  assert.equal(rejectedFolder.saved.chatComposingNew, false);

  // The shell only swaps the root and reports errors; stopping the loop and resetting the
  // selection belongs to a confirmation flow that does not exist yet, so this check stays
  // with what the code actually does.
  const workspaceHandlers = code.slice(code.indexOf("const changeWorkspaceRoot ="), code.indexOf("const executeTests ="));
  const workspaceBridgeName = workspaceHandlers.match(/([\w$]+)\.desktopBridge/)[1];
  const checkWorkspace = ({ canceled = false, error = "", throws = null } = {}) => {
    const saved = { desktopRuntime: { workspaceRoot: "D:/old" }, repositoryError: null, repositorySaving: false };
    const bridge = {
      pickWorkspaceRoot: async () => {
        if (throws) throw throws;
        return canceled ? { canceled: true } : { canceled: false, workspaceRoot: "D:/new", error };
      },
    };
    const context = {
      ...(errorImport ? { [errorImport]: errorStub } : {}),
      [workspaceBridgeName]: { desktopBridge: bridge },
      desktopBridge: bridge,
      t: key => key, Error,
    };
    for (const key of Object.keys(saved)) {
      context[`set${key[0].toUpperCase()}${key.slice(1)}`] = value => {
        saved[key] = typeof value === "function" ? value(saved[key]) : value;
      };
    }
    const callbacks = runInNewContext(`${workspaceHandlers}; ({ changeWorkspaceRoot });`, context);
    return { ...callbacks, saved };
  };
  const switched = checkWorkspace();
  await switched.changeWorkspaceRoot();
  assert.equal(switched.saved.desktopRuntime.workspaceRoot, "D:/new");
  assert.equal(switched.saved.repositoryError, null);
  assert.equal(switched.saved.repositorySaving, false);
  const canceledWorkspace = checkWorkspace({ canceled: true });
  await canceledWorkspace.changeWorkspaceRoot();
  assert.deepEqual(canceledWorkspace.saved.desktopRuntime, { workspaceRoot: "D:/old" });
  assert.equal(canceledWorkspace.saved.repositorySaving, false);
  const failedWorkspace = checkWorkspace({ error: "backend down" });
  await failedWorkspace.changeWorkspaceRoot();
  assert.equal(failedWorkspace.saved.repositoryError, "backend down");
  assert.equal(failedWorkspace.saved.repositorySaving, false);
  const thrownWorkspace = checkWorkspace({ throws: new Error("bridge unavailable") });
  await thrownWorkspace.changeWorkspaceRoot();
  assert.equal(thrownWorkspace.saved.repositoryError, "bridge unavailable");
  assert.equal(thrownWorkspace.saved.repositorySaving, false);

  const checkCallbacks = (hasGit) => {
    const original = { id: "old-task", runId: "old-run", repositoryId: "original-folder",
      lifecycleStatus: "ready_for_pr", status: "review", mode: "conservative", track: "code", currentIteration: 1 };
    const saved = { tasks: [original], loopRunning: false, taskSetup: null, taskSetupDirty: false, taskCriteriaDirty: false,
      taskSetupError: null, chatSending: false, created: [], started: [] };
    const api = {
      createTask: async (...args) => { saved.created.push(args); return { id: "new-task", run_id: "new-run" }; },
      startAgentLoop: async (...args) => { saved.started.push(args); return { loop_running: true, task: {}, action: "review_patch" }; },
      stopAgentLoop: async () => ({ loop_running: true }),
    };
    const noop = () => {};
    const context = {
      ...(errorImport ? { [errorImport]: errorStub } : {}),
      [apiName]: api, activeTask: original, activeRepository: { has_git: hasGit },
      selectedRepositoryId: "another-folder", chatMode: "standard", railTab: "code",
      chatComposingNew: false, loopRunning: false, chatSendingRef: { current: false },
      taskSetup: null, taskSetupDirty: false, taskCriteriaDirty: false, setupTask: null, validationSaving: false, completionSaving: false,
      activeTaskStarted: true, runEvents: [], updateTaskFromApi: noop,
      setChatSending: value => { saved.chatSending = value; }, setChatError: noop, setChatComposingNew: noop,
      setActiveTaskId: noop, setChatPending: noop, setChatAction: noop,
      setTasks: update => { saved.tasks = update(saved.tasks); },
      setLoopRunning: value => { saved.loopRunning = value; },
      taskFromApi: task => task, t: key => key,
    };
    for (const key of ["taskSetup", "taskSetupDirty", "taskCriteriaDirty", "taskSetupError"]) {
      context[`set${key[0].toUpperCase()}${key.slice(1)}`] = value => { saved[key] = value; context[key] = value; };
    }
    const callbacks = runInNewContext(`${handlers}; ({ sendChatMessage, startPreparedTask, stopChatLoop });`, context);
    return { ...callbacks, saved, original, context, api };
  };
  const localChat = checkCallbacks(false);
  assert.equal(await localChat.sendChatMessage("A second goal"), true);
  assert.equal(localChat.saved.created.length, 1);
  assert.equal(localChat.saved.created[0][1], "original-folder");
  assert.equal(localChat.saved.created[0][2], "conservative");
  assert.equal(localChat.saved.started.length, 0, "Creating a Code task must leave time to configure its checks");
  assert.equal(localChat.saved.taskSetup.taskId, "new-task");
  assert.equal(localChat.saved.taskSetup.mode, "conservative");
  assert.ok(localChat.saved.tasks.includes(localChat.original));
  localChat.context.setupTask = { id: "new-task", runId: "new-run" };
  localChat.context.taskSetupDirty = true;
  await localChat.startPreparedTask();
  assert.equal(localChat.saved.started.length, 0, "Unsaved checks must block task execution");
  localChat.context.taskSetupDirty = false;
  localChat.context.validationSaving = true;
  await localChat.startPreparedTask();
  assert.equal(localChat.saved.started.length, 0, "Saving checks must finish before execution");
  localChat.context.validationSaving = false;
  localChat.context.taskCriteriaDirty = true;
  await localChat.startPreparedTask();
  assert.equal(localChat.saved.started.length, 0, "Unsaved completion criteria must block task execution independently of saved checks");
  localChat.context.taskCriteriaDirty = false;
  localChat.context.completionSaving = true;
  await localChat.startPreparedTask();
  assert.equal(localChat.saved.started.length, 0, "Saving completion criteria must finish before task execution");
  localChat.context.completionSaving = false;
  localChat.api.startAgentLoop = async () => { throw new Error("model unavailable"); };
  await localChat.startPreparedTask();
  assert.equal(localChat.saved.taskSetupError, "model unavailable");
  assert.equal(localChat.saved.taskSetup.taskId, "new-task", "A failed start must keep the prepared task for retry");
  localChat.api.startAgentLoop = async (...args) => { localChat.saved.started.push(args); return { loop_running: true, task: {}, action: "review_patch" }; };
  localChat.context.taskSetup.mode = "standard";
  await localChat.startPreparedTask();
  assert.equal(localChat.saved.started[0][0], "new-run");
  assert.equal(localChat.saved.started[0][1].mode, "standard");
  assert.equal(localChat.saved.taskSetup, null);
  assert.equal(localChat.saved.loopRunning, true);

  const queuedChat = checkCallbacks(true);
  queuedChat.original.currentIteration = 0;
  queuedChat.context.activeTaskStarted = false;
  await queuedChat.sendChatMessage("Use these acceptance checks");
  assert.equal(queuedChat.saved.created.length, 0, "Reopening an unstarted task must reuse it");
  assert.equal(queuedChat.saved.started.length, 0);
  assert.equal(queuedChat.saved.taskSetup.taskId, "old-task");
  assert.equal(queuedChat.saved.taskSetup.feedback, "Use these acceptance checks");

  const gitChat = checkCallbacks(true);
  assert.equal(await gitChat.sendChatMessage("Continue this PR"), true);
  assert.equal(gitChat.saved.created.length, 0);
  assert.equal(gitChat.saved.started[0][0], "old-run");
  assert.equal(gitChat.saved.started[0][1].feedback, "Continue this PR");
  gitChat.saved.loopRunning = false;
  await gitChat.stopChatLoop();
  assert.equal(gitChat.saved.loopRunning, true, "A stopping backend keeps the composer locked");
  console.log("Desktop chat checks passed: folder flows, rendering, checkpoint diffs, startup checks, completion criteria and explicit evidence confirmation, acceptance/deletion confirmation and cancellation, empty-file patches, bilingual errors, stop handling, and polling recovery.");
} finally {
  await server.close();
}
