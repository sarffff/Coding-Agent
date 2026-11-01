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
  server: { middlewareMode: true, hmr: false },
  appType: "custom",
  plugins: [{
    name: "desktop-chat-fixtures",
    enforce: "pre",
    transform(source, id) {
      if (id.includes("PlanProgress.tsx?progress-fixture")) {
        const progress = [
          { step_id: "behavior", status: "validated", changed_files: ["src/app.py"], iteration_ids: ["iteration-one", "iteration-two"], evidence: [] },
          { step_id: "review", status: "confirmed", changed_files: [], iteration_ids: [], evidence: [{ criterion_id: "review", evidence: "Reviewed the original behavior and current tests." }] },
        ];
        return source.replace("useState<PlanStepProgress[]>([])", `useState<PlanStepProgress[]>(${JSON.stringify(progress)})`).replace("useState(true)", "useState(false)");
      }
      if (/[\\/]App\.tsx(?:\?.*)?$/.test(id)) {
        const withFolder = source.replace("useState<Repository[]>([])",
          `useState<Repository[]>(${JSON.stringify([folder])})`);
        assert.notEqual(withFolder, source, "The app fixture must include an opened folder without tasks");
        const withCodeTab = withFolder.replace('useState<"cowork" | "code">("cowork")',
          'useState<"cowork" | "code">("code")');
        assert.notEqual(withCodeTab, withFolder, "The app fixture must select the Code tab");
        if (id.includes("scope-blocked")) {
          const commitPreview = { branch: "task/verify", head: "a".repeat(40), diff: "+new value", changed_files: ["app.py"],
            excluded_files: ["local.py"], scope_blocked_files: ["local.py"], commit_message: "Verify behavior", scope_hash: "scope", ready: true };
          return withCodeTab.replace(JSON.stringify([folder]), JSON.stringify([{ ...folder, has_git: true }]))
            .replace("useState(initialTasks)", `useState(${JSON.stringify([blockedTask])})`)
            .replace('const [activeTaskId, setActiveTaskId] = useState("")', 'const [activeTaskId, setActiveTaskId] = useState("guard-task")')
            .replace('useState<NavKey>("chat")', 'useState<NavKey>("runs")')
            .replace("useState<CommitPreview | null>(null)", `useState<CommitPreview | null>(${JSON.stringify(commitPreview)})`);
        }
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
        if (id.includes("settings-page")) {
          return withCodeTab.replace('const [settingsOpen, setSettingsOpen] = useState(false)', 'const [settingsOpen, setSettingsOpen] = useState(true)');
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
    sessionKey: "new-code",
    goal: null, plan: undefined, events: [], loopRunning: false, sending: false,
    status: null, hasActiveRun: false, track: "code", repositories: [folder],
    selectedRepositoryId: folder.id, onSelectRepository() {}, onImportFolder() {},
    onBrowseFolder() {}, onSend: async () => false, onStop() {}, onOpenPublish() {},
    reviewPatch: false, onReviewPatch() {}, error: null, mode: "standard",
    modeLocked: false, onModeChange() {}, pending: null, onDecide() {}, budget: null,
    canSetCompletionCriteria: true,
  };
  const render = (overrides) => renderToStaticMarkup(React.createElement(PreferencesProvider, null,
    React.createElement(ChatPanel, { ...props, ...overrides })));

  const firstFailure = render({ error: "Cannot open this folder" });
  assert.match(firstFailure, /role="alert"><span>Cannot open this folder<\/span>/);
  assert.match(firstFailure, /D:\/work\/Notes/);
  // Upload and folder controls stay inside the composer; permissions, model and context sit below it.
  assert.match(firstFailure, /chat-composer-meta--compact/);
  assert.doesNotMatch(firstFailure, /Browse files/);
  assert.doesNotMatch(firstFailure, /chat-folder-chip/);
  assert.match(firstFailure, /chat-mode-select--standard/);
  assert.match(firstFailure, /class="chat-attach"/);
  assert.match(firstFailure, /class="chat-composer-controls">[\s\S]*?class="chat-attach"[\s\S]*?chat-composer-meta/,
    "The upload button belongs at the left of the textarea footer");
  assert.doesNotMatch(firstFailure.match(/class="chat-composer-actions">[\s\S]*?<\/div>/)?.[0] ?? "", /chat-attach/);
  const modeTrigger = overrides => render(overrides).match(/<summary class="chat-mode-select[^>]*>/)?.[0] ?? "";
  assert.match(modeTrigger({ modeLocked: false, hasActiveRun: true }), /aria-disabled="false"/);
  assert.match(modeTrigger({ modeLocked: true }), /aria-disabled="true"/);
  assert.match(modeTrigger({ loopRunning: true }), /aria-disabled="true"/);
  assert.equal((firstFailure.match(/class="chat-mode-option"/g) ?? []).length, 3);
  assert.match(firstFailure, /class="chat-mode-option"[^>]*aria-pressed="true">[\s\S]*?Standard/);
  assert.match(firstFailure, /Approve the plan, then review and apply patches/);
  assert.match(firstFailure, /class="chat-composer-footer">[\s\S]*?chat-mode-select[\s\S]*?chat-model-picker[\s\S]*?chat-context/,
    "The permissions, model and context controls belong to one composer footer");
  const composerActions = firstFailure.match(/class="chat-composer-actions">[\s\S]*?<\/div>/)?.[0] ?? "";
  assert.doesNotMatch(composerActions, /chat-model-picker|chat-mode-select|chat-context/);
  assert.doesNotMatch(firstFailure, /AI edits, tests and rollback are available/);
  assert.match(firstFailure, /<details class="chat-completion-criteria">/,
    "New Code tasks must offer optional criteria in a closed disclosure");
  assert.doesNotMatch(render({ canSetCompletionCriteria: false, status: "coding", hasActiveRun: true }), /chat-completion-criteria/);

  const localResult = render({ goal: "Fix tests", status: "ready_for_pr", hasActiveRun: true });
  assert.match(localResult, /Review local results/);
  assert.doesNotMatch(localResult, />Publish<|git init/);

  const gitResult = render({ goal: "Fix tests", status: "ready_for_pr", hasActiveRun: true,
    repositories: [{ ...folder, has_git: true }] });
  assert.match(gitResult, />Publish</);

  const review = render({ goal: "Fix tests", status: "coding", hasActiveRun: true,
    mode: "conservative", reviewPatch: true });
  assert.match(review, />Review patch</);
  assert.match(render({ goal: "Fix tests", hasActiveRun: true, status: "awaiting_approval", onPrepare() {} }), />Review before starting</);

  const cowork = render({ track: "cowork", selectedRepositoryId: "", sending: true });
  assert.match(cowork, /<details class="folder-picker">/);
  assert.match(cowork, /<button[^>]*class="folder-picker-option is-selected"[^>]*disabled=""[^>]*aria-pressed="true">[\s\S]*?<strong>No folder<\/strong>/);
  assert.match(cowork, /<textarea[^>]*disabled=""/);
  assert.doesNotMatch(cowork, /chat-completion-criteria/, "Cowork has no coding completion criteria");
  assert.doesNotMatch(cowork, /chat-mode-option/, "Cowork must not offer Code edit permissions");
  assert.match(render({ track: "cowork", sending: true, onCancelCowork() {} }), /chat-send--stop/,
    "A pending Cowork request must expose cancellation");
  assert.match(render({ goal: "Fix tests", hasActiveRun: true, status: "paused", onResume() {} }), />Resume</);
  assert.match(render({ goal: "Fix tests", hasActiveRun: true, status: "coding", requiresRecovery: true, onRecover() {} }), />Continue task</);
  assert.match(render({ goal: "Fix tests", hasActiveRun: true, status: "failed", blockedReason: "VALIDATION_FAILED", stateMessage: "Review the failing check", onOpenRun() {} }), /Review the failing check/);
  assert.match(cowork, /class="chat-readonly"/);

  const savedModels = [
    { id: "fast", label: "Fast local", provider: "openai", model: "local-fast", base_url: "", api_key_configured: true },
    { id: "careful", label: "Careful local", provider: "anthropic", model: "local-careful", base_url: "", api_key_configured: true },
  ];
  const modelProps = { models: savedModels, activeModelId: "careful", modelName: "Careful local", onModelChange() {}, onOpenModelSettings() {} };
  const modelMenu = render(modelProps).match(/<details class="chat-popover chat-model-picker"[\s\S]*?<\/details>/)?.[0] ?? "";
  assert.match(modelMenu, /<summary class="chat-model-chip"[^>]*><span>Careful local<\/span>/);
  assert.equal((modelMenu.match(/class="chat-model-option"/g) ?? []).length, 2);
  assert.match(modelMenu, /class="chat-model-option"[^>]*aria-pressed="true">[\s\S]*?<strong>Careful local<\/strong>[\s\S]*?local-careful/);
  assert.match(modelMenu, /class="chat-model-settings"/);
  const lockedModelMenu = render({ ...modelProps, modelLocked: true }).match(/<details class="chat-popover chat-model-picker"[\s\S]*?<\/details>/)?.[0] ?? "";
  assert.match(lockedModelMenu, /<summary[^>]*aria-disabled="true"/);
  assert.equal((lockedModelMenu.match(/class="chat-model-option"[^>]*disabled=""/g) ?? []).length, 2);
  assert.match(firstFailure, /<summary class="chat-model-chip"[^>]*><span>[^<]+<\/span>/,
    "The model control remains discoverable before configuration has loaded");

  const contextEvents = [
    { id: "old-context", action: "patch.draft", status: "succeeded", summary: "Old draft", created_at: "2026-10-26T00:00:00Z", details: { context_paths: ["old.py"], context_bytes: 1024 } },
    { id: "chat-user", action: "chat.user", status: "succeeded", summary: "Follow-up", created_at: "2026-10-26T00:01:00Z", details: {} },
    { id: "chat-assistant", action: "chat.assistant", status: "succeeded", summary: "Reply", created_at: "2026-10-26T00:02:00Z", details: {} },
    { id: "recent-context", action: "patch.draft", status: "succeeded", summary: "Recent draft", created_at: "2026-10-26T00:03:00Z", details: { context_paths: ["src/main.py", "tests/test_main.py"], context_bytes: 8192 } },
  ];
  const contextHtml = render({ goal: "Fix tests", events: contextEvents, contextLimit: 128 * 1024,
    budget: { iterations: 2, max_iterations: 8, remote_calls: 4, max_remote_calls: 50, elapsed_seconds: 60, max_wall_time_seconds: 3600, consecutive_failures: 0, max_consecutive_failures: 3, started_at: null, exhausted_reason: null } });
  const contextMenu = contextHtml.match(/<details class="chat-popover chat-context"[\s\S]*?<\/details>/)?.[0] ?? "";
  assert.match(contextMenu, /class="chat-context-stats"/);
  assert.match(contextMenu, /<dd>3<\/dd>/, "Context reports the goal and two conversation messages");
  assert.match(contextMenu, /<dd>0 \/ 32,000<\/dd>/);
  assert.match(contextMenu, /<dd>0 \/ 5<\/dd>/);
  assert.match(contextMenu, /8\.0 KB \/ 128 KB/);
  assert.match(contextMenu, /src\/main.py/);
  assert.match(contextMenu, /tests\/test_main.py/);
  assert.doesNotMatch(contextMenu, /old.py/, "Context shows the latest code context snapshot");
  assert.match(contextMenu, /budget-stat-value">2\/8<\/span>/);
  assert.match(contextMenu, /budget-stat-value">4\/50<\/span>/);
  assert.match(contextMenu, /1:00 \/ 60:00/);
  assert.doesNotMatch(contextHtml, /class="chat-usage"/,
    "Budget details belong in the context menu instead of above the conversation");
  assert.doesNotMatch(firstFailure, /class="chat-context-files"/,
    "No code context snapshot is invented for a new conversation");

  const { Sidebar } = await server.ssrLoadModule("/src/components/Sidebar.tsx");
  const renderSidebar = deletionBusy => renderToStaticMarkup(React.createElement(PreferencesProvider, null,
    React.createElement(Sidebar, { open: false, activeNav: "chat", track: "cowork", activeSessionId: "chat-a",
      sessions: [{ id: "chat-a", title: "Chat A", track: "cowork", status: "review" }], groups: [],
      approvalCount: 0, folderOpening: false, deletionBusy, onClose() {}, onTrackChange() {}, onNavigate() {},
      onNewSession() {}, onOpenFolder() {}, onOpenSettings() {}, onSelectSession() {}, onDeleteSession() {}, onOpenRegisteredFolder() {} })));
  assert.match(renderSidebar(true), /class="chat-session-delete"[^>]*disabled=""/,
    "A pending chat must disable the sidebar deletion entry");
  assert.doesNotMatch(renderSidebar(false), /class="chat-session-delete"[^>]*disabled=""/);
  const restoredSidebar = renderSidebar(false);
  const sidebarNavigation = restoredSidebar.match(/<nav class="rail-nav"[\s\S]*?<\/nav>/)?.[0];
  assert.ok(sidebarNavigation, "The original sidebar navigation must remain directly available");
  for (const label of ["Overview", "Files", "Execution runs", "Approvals"]) {
    assert.ok(sidebarNavigation.includes(`<span>${label}</span>`), `${label} must remain in the sidebar navigation`);
  }
  assert.doesNotMatch(restoredSidebar, /rail-more|rail-customize/);
  assert.match(restoredSidebar, /class="rail-local">[\s\S]*?Local execution/);
  assert.match(restoredSidebar, /class="rail-account"[\s\S]*?Language &amp; appearance/);

  const { default: App } = await server.ssrLoadModule("/src/App.tsx");
  const app = renderToStaticMarkup(React.createElement(PreferencesProvider, null, React.createElement(App)));
  assert.match(app, /Review before starting/, "The task preparation dialog remains available before execution");
  const emptyFolder = app.match(/<details class="rail-folder-group"[\s\S]*?<\/details>/)?.[0];
  assert.ok(emptyFolder, "An opened folder must appear before it has any tasks");
  assert.match(emptyFolder, /<summary title="D:\/work\/Notes">/);
  assert.match(emptyFolder, /<span>Notes<\/span>/);
  assert.doesNotMatch(emptyFolder, /rail-folder-count/);
  assert.match(emptyFolder, /<summary[^>]*>[\s\S]*?class="rail-folder-add"[\s\S]*?<\/summary>/,
    "The folder plus takes the old count position inside the heading");
  const { default: ScopeBlockedApp } = await server.ssrLoadModule("/src/App.tsx?scope-blocked");
  const scopeBlockedApp = renderToStaticMarkup(React.createElement(PreferencesProvider, null, React.createElement(ScopeBlockedApp)));
  assert.match(scopeBlockedApp, /Scope review needed/);
  assert.match(scopeBlockedApp, /Validation used these uncommitted files outside the task scope/);
  assert.match(scopeBlockedApp, /complete the task locally/);
  assert.match(scopeBlockedApp, /<code[^>]*>local.py<\/code>/);
  assert.match(scopeBlockedApp, /<button[^>]*disabled=""[^>]*>Request write approval<\/button>/,
    "Scope review blocks commit approval even if an older preview says ready");
  const { default: SettingsApp } = await server.ssrLoadModule("/src/App.tsx?settings-page");
  const settingsPage = renderToStaticMarkup(React.createElement(PreferencesProvider, null, React.createElement(SettingsApp)));
  assert.match(settingsPage, /class="app-shell" data-page="settings"/);
  assert.match(settingsPage, /class="settings-page"/);
  assert.match(settingsPage, /Back to app/);
  assert.match(settingsPage, /Settings categories/);
  assert.doesNotMatch(settingsPage, /role="dialog"/);
  assert.match(settingsPage, /class="chat-view/,
    "Opening settings keeps the underlying chat mounted so its draft survives returning");
  const { SettingsPage } = await server.ssrLoadModule("/src/components/SettingsPage.tsx");
  const modelSettings = renderToStaticMarkup(React.createElement(PreferencesProvider, null,
    React.createElement(SettingsPage, { initialCategory: "models", onBack() {} })));
  assert.match(modelSettings, /<h1>Models<\/h1>/, "The composer settings entry must open the model category directly");
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
      completionRequests.push({ url, method: options.method, body: options.body ? JSON.parse(options.body) : null });
      return { ok: true, json: async () => ({}) };
    };
    await apiModule.createTask("Fix value", "repository", "standard", "code", validationChecks, completionCriteria);
    await apiModule.saveCompletionCriteria("task", completionCriteria);
    await apiModule.confirmCompletion("task", { scope_hash: "scope-current", validation_hash: "validation-current",
      iteration_id: "iteration-current", evidence: [{ criterion_id: "goal", evidence: "The regression passes." }] });
    await apiModule.setTaskMode("task", "conservative");
    await apiModule.saveTaskPlan("task", { objective: "Fix value", repository_summary: "Source", steps: [] });
    await apiModule.completeLocalTask("task");
    await apiModule.createTask("Discuss the design", "repository", "standard", "cowork");
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.deepEqual(completionRequests[0].body.completion_criteria, completionCriteria);
  assert.equal(completionRequests[0].body.isolated_workspace, true, "Code tasks default to isolated execution");
  assert.match(completionRequests[4].url, /\/tasks\/task\/plan$/);
  assert.equal(completionRequests[4].method, "PUT");
  assert.equal(completionRequests[4].body.objective, "Fix value");
  assert.match(completionRequests[5].url, /\/tasks\/task\/complete-local$/);
  assert.equal(completionRequests[5].method, "POST");
  assert.equal(completionRequests[5].body, null, "Local completion uses the server's current validation hash");
  assert.equal(completionRequests[6].body.isolated_workspace, false, "Cowork retains ordinary conversations");
  assert.equal(completionRequests[1].method, "PUT");
  assert.match(completionRequests[1].url, /\/tasks\/task\/completion-criteria$/);
  assert.deepEqual(completionRequests[1].body, { criteria: completionCriteria });
  assert.equal(completionRequests[3].method, "PUT");
  assert.match(completionRequests[3].url, /\/tasks\/task\/mode$/);
  assert.deepEqual(completionRequests[3].body, { mode: "conservative" });
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
  const fallbackCompletion = renderCompletion({ criteria: [], state: null });
  assert.match(fallbackCompletion, /Fix the original goal/);
  assert.match(fallbackCompletion, /Completion evidence/);
  assert.match(fallbackCompletion, /<button[^>]*disabled=""[^>]*>Confirm completion criteria<\/button>/,
    "Legacy tasks still require explicit objective evidence to finish locally");
  const localState = { ...staleState, completion_status: "confirmed", can_confirm_completion: false, can_complete_local: true,
    task: { ...staleState.task, status: "ready_for_pr" } };
  assert.match(renderCompletion({ state: localState }), /<button[^>]*type="button"[^>]*><svg.*Complete local task<\/button>/);
  assert.match(renderCompletion({ state: { ...localState, can_complete_local: false } }), /<button[^>]*type="button"[^>]*disabled=""[^>]*><svg.*Complete local task<\/button>/);
  assert.doesNotMatch(renderCompletion({ state: { ...localState, task: { ...localState.task, status: "completed" } } }), /Complete local task<\/button>/);
  const criteriaEditor = renderCompletion({ preparation: true, currentIteration: 0, state: null });
  assert.match(criteriaEditor, /Add criterion/);
  assert.match(criteriaEditor, /Save completion criteria/);
  assert.match(criteriaEditor, /<input[^>]*maxLength="500"/);
  assert.doesNotMatch(renderCompletion({ preparation: true, state: null }), /<input|Add criterion|Save completion criteria/);
  const scopedPlan = { objective: "Verify existing behavior", repository_summary: "Local source", steps: [
    { id: "behavior", title: "Verify behavior", description: "Keep the existing observable behavior", files: ["src"], risk: "low", verification: ["Run existing tests"] },
    { id: "review", title: "Review results", description: "Record direct evidence", files: [], risk: "low", verification: [] },
  ] };
  const { TaskPlanEditor } = await server.ssrLoadModule("/src/components/TaskPlanEditor.tsx");
  const renderPlan = (overrides = {}) => renderToStaticMarkup(React.createElement(PreferencesProvider, null,
    React.createElement(TaskPlanEditor, { taskId: "task", plan: scopedPlan, criteria: [], currentIteration: 0,
      disabled: false, onSaved() {}, onBusyChange() {}, ...overrides })));
  const unconfirmedPlanMarkup = renderPlan();
  assert.match(unconfirmedPlanMarkup, /Task plan and subgoals/);
  assert.match(unconfirmedPlanMarkup, /value="Verify behavior"/);
  assert.match(unconfirmedPlanMarkup, /<input[^>]*maxLength="120"/);
  assert.match(unconfirmedPlanMarkup, /<button type="submit" class="small-action">Save and confirm subgoals<\/button>/,
    "An unchanged template must still be explicitly confirmed and bound to completion conditions");
  assert.match(renderPlan({ criteria: scopedPlan.steps.map(step => ({ id: step.id, text: step.title, step_id: step.id })) }),
    /<button type="submit" class="small-action" disabled="">Save and confirm subgoals<\/button>/);
  assert.match(renderPlan({ currentIteration: 1 }), /<fieldset disabled=""/);
  assert.doesNotMatch(renderPlan({ currentIteration: 1 }), /Save and confirm subgoals<\/button>/);
  const { PlanProgress } = await server.ssrLoadModule("/src/components/PlanProgress.tsx?progress-fixture");
  const progressMarkup = renderToStaticMarkup(React.createElement(PreferencesProvider, null,
    React.createElement(PlanProgress, { taskId: "task", revision: "now", plan: scopedPlan, hidden: false })));
  assert.match(progressMarkup, /Actual changes.*src\/app.py/);
  assert.match(progressMarkup, /Related iterations.*iteration-one, iteration-two/);
  assert.match(progressMarkup, /Current complete validation passed/);
  assert.match(progressMarkup, /Completion evidence confirmed/);
  assert.match(progressMarkup, /Reviewed the original behavior and current tests/);
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
  const deletionHandler = code.slice(code.indexOf("const deleteSession ="), code.indexOf("const removeRepository ="));
  const deletionApiName = deletionHandler.match(/([\w$]+)\.deleteTask/)[1];
  const deletionDraftName = deletionHandler.match(/([\w$]+)\.removeChatDraft/)[1];
  const checkSessionDeletion = ({ busy = false, confirmed = true } = {}) => {
    const session = { id: "chat-a", title: "Chat A" };
    const saved = { tasks: [session, { id: "chat-b", title: "Chat B" }], activeTaskId: session.id, resets: 0,
      confirmations: 0, requests: [], error: null, removedDrafts: [], deletedKeys: [], replies: { "new:chat-a": [{ requestId: "reply-a" }] } };
    const api = { deleteTask: async id => { saved.requests.push(id); } };
    const taskRef = { current: session.id };
    const context = {
      ...(errorImport ? { [errorImport]: errorStub } : {}), [deletionApiName]: api,
      [deletionDraftName]: { removeChatDraft: (_scope, key) => { saved.removedDrafts.push(key); return true; } }, chatDraftScope: "local", chatComposerKeysRef: { current: new Map([[session.id, "new:chat-a"]]) },
      setChatReplies: update => { saved.replies = update(saved.replies); }, setDeletedChatDraftKeys: update => { saved.deletedKeys = update(saved.deletedKeys); },
      chatRetriesRef: { current: new Map([[session.id, {}], ["new:chat-a", {}]]) }, chatErrorsRef: { current: new Map([[session.id, "failed"], ["new:chat-a", "failed"]]) },
      activeTaskId: session.id, taskRef, chatSendingRef: { current: busy }, t: key => key,
      window: { confirm: () => { saved.confirmations++; return confirmed; } },
      setTasks: update => { saved.tasks = update(saved.tasks); },
      setActiveTaskId: id => { saved.activeTaskId = id; },
      startNewChatSession: () => { saved.resets++; saved.activeTaskId = ""; taskRef.current = ""; },
      setChatError: error => { saved.error = error; },
    };
    return { deleteSession: runInNewContext(`${deletionHandler}; deleteSession;`, context), saved, api, taskRef, session, context };
  };
  const switchedDeletion = checkSessionDeletion();
  let releaseDeletion;
  switchedDeletion.api.deleteTask = async () => new Promise(resolve => { releaseDeletion = resolve; });
  const deleting = switchedDeletion.deleteSession(switchedDeletion.session);
  switchedDeletion.taskRef.current = "chat-b";
  switchedDeletion.saved.activeTaskId = "chat-b";
  releaseDeletion();
  await deleting;
  assert.equal(switchedDeletion.saved.activeTaskId, "chat-b", "A late deletion response must preserve the newly selected chat");
  assert.equal(switchedDeletion.saved.resets, 0);
  assert.deepEqual(switchedDeletion.saved.tasks.map(task => task.id), ["chat-b"]);
  const currentDeletion = checkSessionDeletion();
  await currentDeletion.deleteSession(currentDeletion.session);
  assert.equal(currentDeletion.saved.resets, 1, "Deleting the still-selected chat must open a new conversation");
  assert.deepEqual(currentDeletion.saved.removedDrafts, ["chat-a", "new:chat-a"], "Deletion clears the stable task draft and its logical alias");
  assert.equal(currentDeletion.saved.replies["new:chat-a"], undefined);
  assert.equal(currentDeletion.context.chatComposerKeysRef.current.size, 0);
  assert.equal(currentDeletion.context.chatRetriesRef.current.size, 0);
  assert.equal(currentDeletion.context.chatErrorsRef.current.size, 0);
  for (const options of [{ busy: true }, { confirmed: false }]) {
    const blockedDeletion = checkSessionDeletion(options);
    await blockedDeletion.deleteSession(blockedDeletion.session);
    assert.equal(blockedDeletion.saved.requests.length, 0);
    assert.equal(blockedDeletion.saved.resets, 0);
    if (options.busy) assert.equal(blockedDeletion.saved.confirmations, 0);
  }
  const failedDeletion = checkSessionDeletion();
  failedDeletion.api.deleteTask = async () => { throw new Error("Chat is still replying"); };
  await failedDeletion.deleteSession(failedDeletion.session);
  assert.equal(failedDeletion.saved.tasks.length, 2);
  assert.equal(failedDeletion.saved.resets, 0);
  assert.equal(failedDeletion.saved.error, "Chat is still replying");
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
  const completionHandlers = completionCode.slice(completionCode.indexOf("const update ="), completionCode.indexOf("const complete ="));
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
    requestRef: { current: null }, AbortController,
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
    setLoopRunning: value => { poll.loopRunning = value; }, setRunEvents() {}, setChatPending() {}, setChatBudget() {}, setChatAgentState() {}, setChatAction() {}, setChatStateMessage() {},
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
  const selectSession = code.slice(code.indexOf("const selectChatSession ="), code.indexOf("const openRegisteredFolder ="));
  const selectionContext = {
    [newSession.match(/([\w$]+)\.createNewChatDraftId/)[1]]: { createNewChatDraftId: () => "new-draft" }, chatDraftScope: "local",
    chatSelectionRef: { current: 0 }, taskRef: { current: "old-task" }, tasks: [], chatRequestsRef: { current: new Map() },
    activeTask: null,
    setActiveNav() {}, setChatComposingNew() {}, setActiveTaskId() {}, setRunEvents() {}, setChatPending() {},
    setChatBudget() {}, setChatAction() {}, setChatError() {}, setLoopRunning() {}, setSelectedRepositoryId() {}, setRailTab() {},
    setChatSending() {}, setChatStateMessage() {}, setChatDraftId() {}, setChatAgentState() {},
  };
  const selectionCallbacks = runInNewContext(`${newSession}; ${selectSession}; ({startNewChatSession, selectChatSession});`, selectionContext);
  selectionCallbacks.startNewChatSession();
  assert.equal(selectionContext.chatSelectionRef.current, 1);
  assert.equal(selectionContext.taskRef.current, "", "New conversation selection changes synchronously");
  selectionCallbacks.selectChatSession("another-task");
  assert.equal(selectionContext.chatSelectionRef.current, 2);
  assert.equal(selectionContext.taskRef.current, "another-task", "Selected task changes before asynchronous replies resume");
  const folderHandlers = code.slice(code.indexOf("const openRegisteredFolder ="), code.indexOf("const changeWorkspaceRoot ="));
  const folderApiName = folderHandlers.match(/([\w$]+)\.registerRepository/)[1];
  const bridgeName = folderHandlers.match(/([\w$]+)\.desktopBridge/)[1];
  const checkFolderCallbacks = ({ desktop = true, picked = folder.path, error = null } = {}) => {
    const saved = { repositories: [folder], selectedRepositoryId: "previous-folder", railTab: "cowork",
      activeNav: "settings", activeTaskId: "old-task", chatComposingNew: false, sidebarOpen: true,
      repositoryDialogOpen: false, repositorySaving: false, repositoryError: null, repositoryPathInput: "",
      runEvents: ["old-event"], chatPending: {}, chatBudget: {}, chatAction: "working", chatError: "old-error",
      loopRunning: false, chatSending: false, chatStateMessage: null, chatDraftId: 0,
      sourceLocation: { path: "old-folder.py", line: 3 }, registered: [], picked: [] };
    const context = {
      ...(errorImport ? { [errorImport]: errorStub } : {}),
      [newSession.match(/([\w$]+)\.createNewChatDraftId/)[1]]: { createNewChatDraftId: () => "new-folder-draft" }, chatDraftScope: "local",
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
      chatDraftId: saved.chatDraftId, chatRequestsRef: { current: new Map() }, chatComposerKeysRef: { current: new Map() },
      chatSelectionRef: { current: 0 }, taskRef: { current: "old-task" },
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

  const creationRules = code.match(/const completedLocalTask =[^;]+;\s*const createsCodeTask =[^;]+;/)[0];
  const creationState = context => runInNewContext(`${creationRules}; ({ completedLocalTask, createsCodeTask });`, {
    chatComposingNew: context.chatComposingNew, activeTask: context.activeTask, activeRepository: context.activeRepository,
  });
  const checkCallbacks = (hasGit, track = "code") => {
    const original = { id: "old-task", runId: "old-run", repositoryId: "original-folder",
      lifecycleStatus: "ready_for_pr", status: "review", mode: "conservative", track, currentIteration: 1 };
    const saved = { tasks: [original], loopRunning: false, taskSetup: null, taskSetupDirty: false, taskCriteriaDirty: false,
      taskSetupError: null, chatSending: false, created: [], started: [], error: null, events: [],
      chatSetupTaskId: null, chatSetupOpen: false, chatSetupError: null, validationDirty: false, completionDirty: false };
    const api = {
      createTask: async (...args) => { saved.created.push(args); return { id: "new-task", run_id: "new-run" }; },
      startAgentLoop: async (...args) => { saved.started.push(args); return { loop_running: true, task: {}, action: "review_patch" }; },
      stopAgentLoop: async () => ({ loop_running: true }),
      sendCoworkChat: async () => ({ reply: "Hello" }),
      listRunEvents: async () => ({ items: [{ id: "old-event", task_id: original.id }] }),
    };
    const noop = () => {};
    const context = {
      ...(errorImport ? { [errorImport]: errorStub } : {}),
      [apiName]: api, activeTask: original, activeRepository: { has_git: hasGit },
      selectedRepositoryId: "another-folder", chatMode: "standard", railTab: track,
      chatComposingNew: false, loopRunning: false, chatSendingRef: { current: false }, chatModeSavingRef: { current: false }, chatModelSavingRef: { current: false },
      taskRef: { current: original.id }, chatSelectionRef: { current: 0 }, chatSessionKey: original.id,
      chatSessionRef: { current: original.id }, chatRequestsRef: { current: new Map() },
      chatRetriesRef: { current: new Map() }, chatErrorsRef: { current: new Map() }, chatComposerKeysRef: { current: new Map() },
      chatSetupFeedbackRef: { current: "" }, crypto: { randomUUID: () => "request-id" }, AbortController,
      taskSetup: null, taskSetupDirty: false, taskCriteriaDirty: false, setupTask: null, validationSaving: false, completionSaving: false,
      activeTaskStarted: true, runEvents: [], updateTaskFromApi: noop,
      setChatSending: value => { saved.chatSending = value; }, setChatError: value => { saved.error = value; }, setChatComposingNew: noop,
      setActiveTaskId: id => { context.taskRef.current = id; }, setChatPending: noop, setChatAction: noop, setChatStateMessage: noop,
      mergeRunEvents: (current, incoming) => [...new Map([...current, ...incoming].map(event => [event.id, event])).values()],
      setRunEvents: events => { saved.events = typeof events === "function" ? events(saved.events ?? []) : events; },
      setChatReplies: update => { saved.replies = update(saved.replies ?? {}); },
      setChatPollError: value => { saved.pollError = value; },
      setTasks: update => { saved.tasks = update(saved.tasks); },
      setLoopRunning: value => { saved.loopRunning = value; },
      taskFromApi: task => task, t: key => key,
    };
    for (const name of ["completedLocalTask", "createsCodeTask"]) {
      Object.defineProperty(context, name, { get: () => creationState(context)[name] });
    }
    for (const key of ["taskSetup", "taskSetupDirty", "taskCriteriaDirty", "taskSetupError", "chatSetupTaskId", "chatSetupOpen", "chatSetupError", "validationDirty", "completionDirty"]) {
      context[`set${key[0].toUpperCase()}${key.slice(1)}`] = value => { saved[key] = value; context[key] = value; };
    }
    const callbacks = runInNewContext(`${handlers}; ({ sendChatMessage, stopChatLoop });`, context);
    return { ...callbacks, saved, original, context, api };
  };
  const localChat = checkCallbacks(false);
  localChat.original.lifecycleStatus = "completed";
  localChat.original.status = "done";
  localChat.original.sourceRepositoryId = "original-folder";
  localChat.original.repositoryId = "execution-folder";
  assert.equal(await localChat.sendChatMessage("A second goal"), true);
  assert.equal(localChat.saved.created.length, 1);
  assert.equal(localChat.saved.created[0][1], "original-folder");
  assert.equal(localChat.saved.created[0][2], "standard");
  assert.equal(localChat.saved.started.length, 0, "Creating Code task waits for saved checks before startup");
  assert.equal(localChat.saved.chatSetupTaskId, "new-task");
  assert.equal(localChat.saved.chatSetupOpen, true);
  assert.equal(localChat.context.chatSetupFeedbackRef.current, "A second goal");
  assert.equal(localChat.saved.created[0][5], undefined, "Criteria remain optional by default");
  assert.ok(localChat.saved.tasks.includes(localChat.original));
  const pendingLocalChat = checkCallbacks(false);
  assert.equal(creationState(pendingLocalChat.context).createsCodeTask, false, "Passing validation does not implicitly complete a local task");
  assert.equal(await pendingLocalChat.sendChatMessage("Review the existing goal"), true);
  assert.equal(pendingLocalChat.saved.created.length, 0, "Unconfirmed local results must retain their current task");

  const modelHandler = code.slice(code.indexOf("const changeChatModel ="), code.indexOf("const deleteSession ="));
  const chatModelApiName = modelHandler.match(/([\w$]+)\.updateConfig/)[1];
  const checkChatModelChange = () => {
    const config = { models: savedModels, active_model_id: "careful", model: "local-careful" };
    const saved = { config, name: "Careful local", requests: [], error: null, busy: false };
    const api = { updateConfig: async patch => {
      saved.requests.push(patch);
      return { ...config, active_model_id: patch.active_model_id, model: "local-fast" };
    } };
    const context = {
      ...(errorImport ? { [errorImport]: errorStub } : {}), [chatModelApiName]: api,
      chatModelConfig: config, loopRunning: false, chatSendingRef: { current: false },
      chatModeSavingRef: { current: false }, chatModelSavingRef: { current: false }, t: key => key,
      setChatModelConfig: next => { saved.config = next; context.chatModelConfig = next; },
      setModelName: name => { saved.name = name; },
      setChatModelSaving: busy => { saved.busy = busy; },
      setChatError: error => { saved.error = error; },
    };
    return { saved, api, context, change: runInNewContext(`${modelHandler}; changeChatModel;`, context) };
  };
  const selectedChatModel = checkChatModelChange();
  await selectedChatModel.change("fast");
  assert.equal(selectedChatModel.saved.requests.length, 1);
  assert.equal(JSON.stringify(selectedChatModel.saved.requests[0]), JSON.stringify({ active_model_id: "fast" }),
    "Switching a model sends only its saved id, without rewriting profiles or keys");
  assert.equal(selectedChatModel.saved.config.active_model_id, "fast");
  assert.equal(selectedChatModel.saved.name, "Fast local", "The composer uses the saved model label");
  assert.equal(selectedChatModel.saved.busy, false);
  assert.equal(selectedChatModel.context.chatModelSavingRef.current, false);
  const failedChatModel = checkChatModelChange();
  const originalChatConfig = failedChatModel.saved.config;
  failedChatModel.api.updateConfig = async () => { throw new Error("Model unavailable"); };
  await failedChatModel.change("fast");
  assert.equal(failedChatModel.saved.config, originalChatConfig);
  assert.equal(failedChatModel.saved.name, "Careful local", "A rejected switch keeps the current model");
  assert.equal(failedChatModel.saved.error, "Model unavailable");
  assert.equal(failedChatModel.saved.busy, false);
  assert.equal(failedChatModel.context.chatModelSavingRef.current, false);
  const duplicateChatModel = checkChatModelChange();
  await duplicateChatModel.change("careful");
  assert.equal(duplicateChatModel.saved.requests.length, 0);
  for (const guard of ["loopRunning", "chatSendingRef", "chatModeSavingRef", "chatModelSavingRef"]) {
    const lockedChatModel = checkChatModelChange();
    if (guard === "loopRunning") lockedChatModel.context.loopRunning = true;
    else lockedChatModel.context[guard].current = true;
    await lockedChatModel.change("fast");
    assert.equal(lockedChatModel.saved.requests.length, 0, `${guard} must prevent model changes`);
  }
  const delayedChatModel = checkChatModelChange();
  let finishChatModel;
  delayedChatModel.api.updateConfig = async patch => {
    delayedChatModel.saved.requests.push(patch);
    return new Promise(resolve => { finishChatModel = resolve; });
  };
  const changingChatModel = delayedChatModel.change("fast");
  await delayedChatModel.change("careful");
  assert.equal(delayedChatModel.saved.requests.length, 1, "Repeated model changes share a synchronous guard");
  assert.equal(delayedChatModel.saved.busy, true);
  finishChatModel({ models: savedModels, active_model_id: "fast", model: "local-fast" });
  await changingChatModel;
  assert.equal(delayedChatModel.saved.busy, false);
  const sendingDuringModel = checkCallbacks(true);
  sendingDuringModel.context.chatModelSavingRef.current = true;
  assert.equal(await sendingDuringModel.sendChatMessage("Wait for the model"), false);
  assert.equal(sendingDuringModel.saved.created.length, 0);
  assert.equal(sendingDuringModel.saved.started.length, 0, "Sending waits for the persisted model change");

  const modeHandler = code.slice(code.indexOf("const changeChatMode ="), code.indexOf("const sendChatMessage ="));
  const modeApiName = modeHandler.match(/([\w$]+)\.setTaskMode/)[1];
  const checkModeChange = (createsCodeTask = false) => {
    const task = { id: "task", track: "code", mode: "standard" };
    const saved = { requests: [], updated: [], draftMode: "standard", error: null, busy: false };
    const api = { setTaskMode: async (id, mode) => { saved.requests.push([id, mode]); return { ...task, mode }; } };
    const context = {
      ...(errorImport ? { [errorImport]: errorStub } : {}), [modeApiName]: api,
      createsCodeTask, activeTask: task, loopRunning: false, chatSendingRef: { current: false },
      chatModeSavingRef: { current: false }, taskRef: { current: task.id }, t: key => key,
      setChatMode: mode => { saved.draftMode = mode; },
      setChatModeSaving: busy => { saved.busy = busy; }, setChatError: error => { saved.error = error; },
      updateTaskFromApi: next => { saved.updated.push(next); },
    };
    return { saved, api, context, change: runInNewContext(`${modeHandler}; changeChatMode;`, context) };
  };
  const newMode = checkModeChange(true);
  await newMode.change("conservative");
  assert.equal(newMode.saved.draftMode, "conservative");
  assert.equal(newMode.saved.requests.length, 0, "New task permissions stay in the composer until creation");
  const existingMode = checkModeChange();
  await existingMode.change("conservative");
  assert.equal(existingMode.saved.requests.length, 1);
  assert.equal(existingMode.saved.updated[0].mode, "conservative", "Existing task selection must update from the persisted mode");
  assert.equal(existingMode.context.chatModeSavingRef.current, false);
  assert.equal(existingMode.saved.busy, false);
  const failedMode = checkModeChange();
  failedMode.api.setTaskMode = async () => { throw new Error("Workspace is busy"); };
  await failedMode.change("autonomous");
  assert.equal(failedMode.saved.updated.length, 0, "A rejected change must keep the current permission");
  assert.equal(failedMode.saved.error, "Workspace is busy");
  assert.equal(failedMode.saved.busy, false);
  const delayedMode = checkModeChange();
  let finishMode;
  delayedMode.api.setTaskMode = async (id, mode) => {
    delayedMode.saved.requests.push([id, mode]);
    return new Promise(resolve => { finishMode = resolve; });
  };
  const changingMode = delayedMode.change("conservative");
  await delayedMode.change("autonomous");
  assert.equal(delayedMode.saved.requests.length, 1, "Repeated permission changes share a synchronous guard");
  finishMode({ id: "task", mode: "conservative" });
  await changingMode;
  assert.equal(delayedMode.saved.busy, false);
  const sendingDuringMode = checkCallbacks(true);
  sendingDuringMode.context.chatModeSavingRef.current = true;
  assert.equal(await sendingDuringMode.sendChatMessage("Wait for permissions"), false);
  assert.equal(sendingDuringMode.saved.started.length, 0, "Sending waits for the persisted permission change");

  const taskUpdater = code.slice(code.indexOf("const updateTaskFromApi ="), code.indexOf("const beginIteration ="));
  const pollingMode = checkModeChange();
  let taskSnapshots = [{ ...pollingMode.context.activeTask, updatedAt: "2026-10-25T00:00:00Z" }];
  pollingMode.context.updateTaskFromApi = runInNewContext(`${taskUpdater}; updateTaskFromApi;`, {
    taskFromApi: task => ({ ...task, updatedAt: task.updated_at }),
    setTasks: update => { taskSnapshots = update(taskSnapshots); },
  });
  let deliverOldSnapshot;
  const delayedOldSnapshot = new Promise(resolve => { deliverOldSnapshot = resolve; })
    .then(pollingMode.context.updateTaskFromApi);
  pollingMode.api.setTaskMode = async (id, mode) => ({ id, mode, updated_at: "2026-10-25T00:00:02Z" });
  await pollingMode.change("conservative");
  deliverOldSnapshot({ id: "task", mode: "standard", updated_at: "2026-10-25T00:00:01Z" });
  await delayedOldSnapshot;
  assert.equal(taskSnapshots[0].mode, "conservative", "A delayed older poll must not undo the saved permission");
  pollingMode.context.updateTaskFromApi({ id: "task", mode: "autonomous", updated_at: "2026-10-25T00:00:02.500Z" });
  assert.equal(taskSnapshots[0].mode, "autonomous", "A newer task snapshot must still update the permission");

  const criterionInputs = [{ id: "criterion-1", text: "The reported bug is fixed" }];
  for (const fixture of ["new", "done", "cancelled", "pr_created", "local"]) {
    const criteriaChat = checkCallbacks(fixture !== "local");
    if (fixture === "local") { criteriaChat.original.lifecycleStatus = "completed"; criteriaChat.original.status = "done"; }
    if (fixture === "new") criteriaChat.context.chatComposingNew = true;
    if (["done", "cancelled", "pr_created"].includes(fixture)) {
      criteriaChat.original.lifecycleStatus = fixture;
      criteriaChat.original.status = fixture === "cancelled" ? "cancelled" : "done";
    }
    assert.equal(creationState(criteriaChat.context).createsCodeTask, true, `${fixture} must expose the new-task criteria entry`);
    assert.equal(await criteriaChat.sendChatMessage("Fix this bug", criterionInputs), true);
    assert.deepEqual(criteriaChat.saved.created[0][5], criterionInputs, "Criteria must be sent atomically with task creation");
    assert.equal(criteriaChat.saved.started.length, 0, "Optional criteria still require the preparation handoff");
    assert.equal(criteriaChat.saved.chatSetupTaskId, "new-task");
    assert.equal(criteriaChat.saved.chatSetupOpen, true);
  }

  const queuedChat = checkCallbacks(true);
  queuedChat.original.currentIteration = 0;
  queuedChat.context.activeTaskStarted = false;
  assert.equal(await queuedChat.sendChatMessage("Use these acceptance checks"), true);
  assert.equal(queuedChat.saved.created.length, 0, "Reopening an unstarted task must reuse it");
  assert.equal(queuedChat.saved.started.length, 0);
  assert.equal(queuedChat.saved.chatSetupTaskId, "old-task");
  assert.equal(queuedChat.saved.chatSetupOpen, true);
  assert.equal(queuedChat.context.chatSetupFeedbackRef.current, "Use these acceptance checks");

  const gitChat = checkCallbacks(true);
  assert.equal(await gitChat.sendChatMessage("Continue this PR"), true);
  assert.equal(gitChat.saved.created.length, 0);
  assert.equal(gitChat.saved.started[0][0], "old-run");
  assert.equal(gitChat.saved.started[0][1].feedback, "Continue this PR");
  assert.equal(creationState(gitChat.context).createsCodeTask, false);
  gitChat.saved.loopRunning = false;
  await gitChat.stopChatLoop();
  assert.equal(gitChat.saved.loopRunning, true, "A stopping backend keeps the composer locked");

  const currentCowork = checkCallbacks(false, "cowork");
  assert.equal(await currentCowork.sendChatMessage("Hello"), true);
  assert.equal(currentCowork.saved.events[0].id, "old-event", "A reply to the selected chat must refresh its transcript");
  const switchedCowork = checkCallbacks(false, "cowork");
  let releaseEvents, notifyEvents;
  const eventsRequested = new Promise(resolve => { notifyEvents = resolve; });
  switchedCowork.api.listRunEvents = async () => { notifyEvents(); return new Promise(resolve => { releaseEvents = resolve; }); };
  const replying = switchedCowork.sendChatMessage("Hello");
  await eventsRequested;
  switchedCowork.context.taskRef.current = "other-task";
  switchedCowork.context.chatSessionRef.current = "other-task";
  switchedCowork.context.chatSelectionRef.current++;
  switchedCowork.saved.events = [{ id: "other-event" }];
  releaseEvents({ items: [{ id: "late-event" }] });
  assert.equal(await replying, true);
  assert.equal(switchedCowork.saved.events[0].id, "other-event", "A late Cowork transcript must not overwrite another chat");
  assert.equal(switchedCowork.saved.chatSending, false);
  for (const switched of [false, true]) {
    const failedCowork = checkCallbacks(false, "cowork");
    let rejectReply;
    failedCowork.api.sendCoworkChat = async () => new Promise((_resolve, reject) => { rejectReply = reject; });
    const failingReply = failedCowork.sendChatMessage("Hello");
    if (switched) {
      failedCowork.context.taskRef.current = "other-task";
      failedCowork.context.chatSessionRef.current = "other-task";
      failedCowork.context.chatSelectionRef.current++;
      failedCowork.saved.error = "Other chat error";
    }
    rejectReply(new Error("Model timed out"));
    assert.equal(await failingReply, false);
    assert.equal(failedCowork.saved.error, switched ? "Other chat error" : "Model timed out");
    assert.equal(failedCowork.saved.chatSending, false);
    assert.equal(failedCowork.context.chatSendingRef.current, false);
  }
  for (const switched of [false, true]) {
    const creatingCowork = checkCallbacks(false, "cowork");
    creatingCowork.context.chatComposingNew = true;
    creatingCowork.context.taskRef.current = "";
    let finishCreation;
    creatingCowork.api.createTask = async () => new Promise(resolve => { finishCreation = resolve; });
    const creating = creatingCowork.sendChatMessage("First message");
    if (switched) {
      creatingCowork.context.chatSelectionRef.current++;
      creatingCowork.context.taskRef.current = "other-task";
      creatingCowork.context.chatSessionRef.current = "other-task";
      creatingCowork.saved.events = [{ id: "other-event" }];
    }
    finishCreation({ id: "created-chat", run_id: "created-run" });
    assert.equal(await creating, true);
    assert.equal(creatingCowork.context.taskRef.current, switched ? "other-task" : "created-chat",
      "A late creation must preserve the user's current selection");
    assert.equal(creatingCowork.saved.events[0].id, switched ? "other-event" : "old-event");
    assert.ok(creatingCowork.saved.tasks.some(task => task.id === "created-chat"), "The created chat remains in history");
  }
  for (const switched of [false, true]) {
    const failedCreation = checkCallbacks(false, "cowork");
    failedCreation.context.chatComposingNew = true;
    failedCreation.context.taskRef.current = "";
    let rejectCreation;
    failedCreation.api.createTask = async () => new Promise((_resolve, reject) => { rejectCreation = reject; });
    const creating = failedCreation.sendChatMessage("First message");
    if (switched) {
      failedCreation.context.chatSelectionRef.current++;
      failedCreation.context.chatSessionRef.current = "other-draft";
      failedCreation.saved.error = "New conversation error";
    }
    rejectCreation(new Error("Creation failed"));
    assert.equal(await creating, false);
    assert.equal(failedCreation.saved.error, switched ? "New conversation error" : "Creation failed",
      "A failed creation must respect opening another empty conversation");
  }
  const preparedHandler = code.slice(code.indexOf("const startPreparedTask ="), code.indexOf("const continueChatTask ="));
  const preparedApiName = preparedHandler.match(/([\w$]+)\.startAgentLoop/)[1];
  const preparedStart = () => {
    const saved = { started: [], updated: [], chatSetupStarting: false, chatSetupError: null, chatSetupOpen: true,
      loopRunning: false, action: null, stateMessage: null };
    const api = { startAgentLoop: async (...args) => {
      saved.started.push(args);
      return { task: { id: "prepared-task" }, loop_running: true, action: "run_tests", message: "Checking the task" };
    } };
    const context = {
      ...(errorImport ? { [errorImport]: errorStub } : {}), [preparedApiName]: api,
      setupTask: { id: "prepared-task", runId: "prepared-run", currentIteration: 0, requiresRecovery: false },
      taskRef: { current: "prepared-task" }, chatSetupStarting: false, validationSaving: false, completionSaving: false,
      validationDirty: false, completionDirty: false, planSaving: false, planDirty: false, chatSetupFeedbackRef: { current: "Saved checks" }, t: key => key,
      updateTaskFromApi: task => { saved.updated.push(task); },
    };
    for (const key of ["chatSetupStarting", "chatSetupError", "chatSetupOpen", "loopRunning"]) {
      context[`set${key[0].toUpperCase()}${key.slice(1)}`] = value => { saved[key] = value; context[key] = value; };
    }
    context.setChatAction = value => { saved.action = value; };
    context.setChatStateMessage = value => { saved.stateMessage = value; };
    return { saved, api, context, start: runInNewContext(`${preparedHandler}; startPreparedTask;`, context) };
  };
  for (const guard of ["chatSetupStarting", "validationSaving", "completionSaving", "planSaving", "validationDirty", "completionDirty", "planDirty", "requiresRecovery", "currentIteration"]) {
    const guarded = preparedStart();
    if (guard === "requiresRecovery") guarded.context.setupTask.requiresRecovery = true;
    else if (guard === "currentIteration") guarded.context.setupTask.currentIteration = 1;
    else guarded.context[guard] = true;
    await guarded.start();
    assert.equal(guarded.saved.started.length, 0, `${guard} must prevent premature startup`);
  }
  const unconfirmedPlan = preparedStart();
  unconfirmedPlan.context.setupTask.plan = { steps: [{ id: "behavior" }] };
  unconfirmedPlan.context.setupTask.completionCriteria = [];
  await unconfirmedPlan.start();
  assert.equal(unconfirmedPlan.saved.started.length, 0, "Every subgoal must have a saved completion condition before execution");
  const prepared = preparedStart();
  await prepared.start();
  assert.equal(prepared.saved.started[0][0], "prepared-run");
  assert.equal(prepared.saved.started[0][1].feedback, "Saved checks");
  assert.equal(prepared.saved.chatSetupOpen, false);
  assert.equal(prepared.saved.chatSetupStarting, false);
  assert.equal(prepared.saved.loopRunning, true);
  assert.equal(prepared.saved.stateMessage, "Checking the task");
  const failedPrepared = preparedStart();
  const startSuccess = failedPrepared.api.startAgentLoop;
  failedPrepared.api.startAgentLoop = async (...args) => {
    failedPrepared.saved.started.push(args);
    throw new Error("Model unavailable");
  };
  await failedPrepared.start();
  assert.equal(failedPrepared.saved.chatSetupError, "Model unavailable");
  assert.equal(failedPrepared.saved.chatSetupOpen, true, "Rejected startup keeps the saved preparation for retry");
  assert.equal(failedPrepared.saved.chatSetupStarting, false);
  failedPrepared.api.startAgentLoop = startSuccess;
  await failedPrepared.start();
  assert.equal(failedPrepared.saved.started.length, 2);
  assert.ok(failedPrepared.saved.started.every(args => args[0] === "prepared-run"), "Retry must reuse the prepared task");
  const delayedPrepared = preparedStart();
  let finishPrepared;
  delayedPrepared.api.startAgentLoop = async () => new Promise(resolve => { finishPrepared = resolve; });
  const startingPrepared = delayedPrepared.start();
  delayedPrepared.context.taskRef.current = "another-task";
  finishPrepared({ task: { id: "prepared-task" }, loop_running: true, action: "run_tests", message: "Late reply" });
  await startingPrepared;
  assert.equal(delayedPrepared.saved.loopRunning, false, "Late startup must not lock another conversation");
  assert.equal(delayedPrepared.saved.updated[0].id, "prepared-task", "Late startup still updates its task history");

  const chatActionHandlers = code.slice(code.indexOf("const decideChatApproval ="), code.indexOf("const startNewChatSession ="))
    + code.slice(code.indexOf("const continueChatTask ="), code.indexOf("const retryDesktopBackend ="));
  const checkChatAction = (action) => {
    const task = { id: "action-task", runId: "action-run", lifecycleStatus: "paused", requiresRecovery: action === "recover" };
    const saved = { decisions: [], resumed: [], recovered: [], started: [], refreshed: [], updated: [],
      chatSending: false, error: null, pending: { id: "approval" }, loopRunning: false, chatAction: null, message: null };
    const state = { task: { id: task.id }, action: "run_tests", message: "Ready", loop_running: true };
    const api = {
      decideApproval: async (...args) => { saved.decisions.push(args); },
      resumeTask: async (...args) => { saved.resumed.push(args); return { task: state.task }; },
      recoverTask: async (...args) => { saved.recovered.push(args); return { task: state.task }; },
      startAgentLoop: async (...args) => { saved.started.push(args); return state; },
      getAgentState: async (...args) => { saved.refreshed.push(args); return state; },
    };
    const context = {
      ...(errorImport ? { [errorImport]: errorStub } : {}), [apiName]: api, activeTask: task,
      chatSessionKey: task.id, chatSessionRef: { current: task.id }, taskRef: { current: task.id },
      chatSelectionRef: { current: 0 }, chatSendingRef: { current: false }, chatRequestsRef: { current: new Map() },
      chatErrorsRef: { current: new Map() }, chatRetriesRef: { current: new Map() },
      loopRunning: false, chatModeSavingRef: { current: false }, chatModelSavingRef: { current: false },
      crypto: { randomUUID: () => "action-request" }, AbortController, t: key => key,
      setChatSending: value => { saved.chatSending = value; }, setChatError: value => { saved.error = value; },
      setChatPending: value => { saved.pending = value; }, setLoopRunning: value => { saved.loopRunning = value; },
      setChatAction: value => { saved.chatAction = value; }, setChatStateMessage: value => { saved.message = value; },
      updateTaskFromApi: value => { saved.updated.push(value); },
    };
    const callbacks = runInNewContext(`${chatActionHandlers}; ({ decideChatApproval, continueChatTask, recoverChatTask });`, context);
    const invoke = () => action === "approve" ? callbacks.decideChatApproval("approval", "approve")
      : action === "continue" ? callbacks.continueChatTask() : callbacks.recoverChatTask();
    return { saved, state, api, context, callbacks, invoke };
  };
  for (const action of ["approve", "continue", "recover"]) {
    const method = action === "approve" ? "decideApproval" : action === "continue" ? "resumeTask" : "recoverTask";
    const calls = action === "approve" ? "decisions" : action === "continue" ? "resumed" : "recovered";
    const locked = checkChatAction(action);
    const existingRequest = {};
    locked.context.chatRequestsRef.current.set("action-task", existingRequest);
    await locked.invoke();
    assert.equal(locked.saved[calls].length, 0, `An in-flight current-session request must block ${action}`);
    assert.equal(locked.context.chatRequestsRef.current.get("action-task"), existingRequest);

    const independentAction = checkChatAction(action);
    const otherRequest = {};
    independentAction.context.chatSendingRef.current = true;
    independentAction.context.chatRequestsRef.current.set("other-task", otherRequest);
    await independentAction.invoke();
    assert.equal(independentAction.saved[calls].length, 1, `A request in another session must not block ${action}`);
    assert.equal(independentAction.context.chatRequestsRef.current.get("other-task"), otherRequest);
    assert.equal(independentAction.context.chatRequestsRef.current.has("action-task"), false);
    assert.equal(independentAction.context.chatSendingRef.current, true, "Finishing one action keeps another session's request active");
    assert.equal(independentAction.saved.chatSending, false);

    const repeated = checkChatAction(action);
    repeated.context.chatSessionKey = "source-session";
    repeated.context.chatSessionRef.current = "source-session";
    let finishAction;
    repeated.api[method] = async (...args) => {
      repeated.saved[calls].push(args);
      return new Promise(resolve => { finishAction = resolve; });
    };
    const pendingAction = repeated.invoke();
    const repeatedAction = repeated.invoke();
    assert.equal(repeated.saved[calls].length, 1, `Repeated ${action} clicks must share a synchronous guard`);
    assert.equal(repeated.context.chatRequestsRef.current.has("action-task"), true);
    assert.equal(repeated.context.chatRequestsRef.current.get("source-session"), repeated.context.chatRequestsRef.current.get("action-task"),
      "A newly created task and its original composer share the same action request");
    assert.equal(repeated.saved.chatSending, true);
    finishAction({ task: repeated.state.task });
    await Promise.all([pendingAction, repeatedAction]);
    assert.equal(repeated.context.chatRequestsRef.current.size, 0);
    assert.equal(repeated.context.chatSendingRef.current, false);
    assert.equal(repeated.saved.chatSending, false);
    assert.equal(repeated.saved.message, "Ready");
    assert.equal(repeated.saved[action === "recover" ? "refreshed" : "started"].length, 1);

    const failed = checkChatAction(action);
    failed.api[method] = async () => { throw new Error("Action failed"); };
    await failed.invoke();
    assert.equal(failed.saved.error, "Action failed");
    assert.equal(failed.context.chatRequestsRef.current.size, 0, `A failed ${action} releases its request for retry`);
    assert.equal(failed.saved.chatSending, false);

    const switched = checkChatAction(action);
    let finishSource;
    switched.api[method] = async () => new Promise(resolve => { finishSource = resolve; });
    const sourceAction = switched.invoke();
    switched.context.taskRef.current = "other-task";
    switched.context.chatSessionRef.current = "other-task";
    switched.context.chatSelectionRef.current++;
    switched.saved.pending = { id: "other-approval" };
    switched.saved.message = "Other task";
    switched.saved.error = "Other error";
    finishSource({ task: switched.state.task });
    await sourceAction;
    assert.equal(switched.saved.pending.id, "other-approval", `A late ${action} must preserve another session's approval`);
    assert.equal(switched.saved.message, "Other task", `A late ${action} must preserve another session's state message`);
    assert.equal(switched.saved.error, "Other error");
    assert.equal(switched.saved.loopRunning, false);
    assert.equal(switched.context.chatRequestsRef.current.size, 0);
  }
  const composerCode = (await server.transformRequest("/src/components/ChatPanel.tsx", { ssr: true })).code;
  const sendHandler = composerCode.slice(composerCode.indexOf("const withAttachments ="), composerCode.indexOf("const fillIdea ="));
  const draftImport = sendHandler.match(/([\w$]+)\.saveChatDraft/)[1];
  const compose = { draft: "Keep this goal", busy: false, sendingRef: { current: new Set() }, submitting: false, calls: 0 };
  const composeContext = {
    [draftImport]: { saveChatDraft: () => true },
    draftScope: "local", draftSessionKey: "source", composerKey: "source", setDraftStorageErrors() {},
    ...compose, attachments: [], completionDraft: "", setCompletionDraft() {}, t: key => key, MAX_MESSAGE_CHARS: 32_000,
    sessionKey: "source", sessionRef: { current: "source" }, readingAttachmentsRef: { current: new Set() },
    setAttachments() {}, setAttachmentError() {},
    setDraft: value => { compose.draft = typeof value === "function" ? value(compose.draft) : value; },
    setSubmitting: value => { compose.submitting = value; }, scrollToBottom() {},
    props: { onSend: async () => { compose.calls++; return false; } },
  };
  const send = runInNewContext(`${sendHandler}; send`, composeContext);
  await send();
  assert.equal(compose.draft, "Keep this goal", "A failed send must retain the full draft");
  assert.equal(compose.submitting, false, "Failed sends must release the composer");
  let finishSend;
  composeContext.props.onSend = () => { compose.calls++; return new Promise(resolve => { finishSend = resolve; }); };
  const firstSend = send();
  await send();
  assert.equal(compose.calls, 2, "Repeated clicks while awaiting a send must not duplicate the request");
  finishSend(true);
  await firstSend;
  assert.equal(compose.draft, "", "Only an accepted send clears the draft");

  const checkMessageLength = async (draft, attachments) => {
    const saved = { draft, attachments, error: null, sent: [], submitting: false };
    const context = {
      ...composeContext, draft, attachments, sendingRef: { current: new Set() },
      t: key => key === "chat.messageTooLong" ? "Maximum {max} characters" : key,
      setDraft: value => { saved.draft = typeof value === "function" ? value(saved.draft) : value; },
      setAttachments: value => { saved.attachments = typeof value === "function" ? value(saved.attachments) : value; },
      setAttachmentError: value => { saved.error = value; },
      setSubmitting: value => { saved.submitting = value; },
      props: { onSend: async message => { saved.sent.push(message); return true; } },
    };
    await runInNewContext(`${sendHandler}; send`, context)();
    return saved;
  };
  const attachedFile = { name: "note.txt", content: "" };
  const framingLength = Array.from(runInNewContext(`${sendHandler}; withAttachments("Goal")`, {
    ...composeContext, attachments: [attachedFile],
  })).length;
  const boundaryFile = { ...attachedFile, content: "x".repeat(32_000 - framingLength) };
  const boundaryMessage = await checkMessageLength("Goal", [boundaryFile]);
  assert.equal(boundaryMessage.sent.length, 1, "A combined message of exactly 32,000 characters must send");
  assert.equal(Array.from(boundaryMessage.sent[0]).length, 32_000);
  assert.equal(boundaryMessage.draft, "");
  assert.equal(boundaryMessage.attachments.length, 0);

  const oversizedFile = { ...boundaryFile, content: `${boundaryFile.content}x` };
  const oversizedMessage = await checkMessageLength("Goal", [oversizedFile]);
  assert.equal(oversizedMessage.sent.length, 0, "Attachment framing and content count toward the message limit");
  assert.equal(oversizedMessage.draft, "Goal", "A length rejection must retain the draft");
  assert.deepEqual(oversizedMessage.attachments, [oversizedFile], "A length rejection must retain attachments");
  assert.equal(oversizedMessage.error, "Maximum 32000 characters");
  assert.equal(oversizedMessage.submitting, false);

  const largeFile = { name: "large.txt", content: "x".repeat(40 * 1024) };
  const largeMessage = await checkMessageLength("Goal", [largeFile]);
  assert.equal(largeMessage.sent.length, 0, "A file under the individual byte cap can still exceed the combined message limit");
  assert.equal(largeMessage.draft, "Goal");
  assert.deepEqual(largeMessage.attachments, [largeFile]);

  const emojiMessage = await checkMessageLength(`${"x".repeat(31_999)}😀`, []);
  assert.equal(emojiMessage.sent.length, 1, "The API length limit counts Unicode code points");
  assert.equal(emojiMessage.sent[0].length, 32_001);
  assert.equal(Array.from(emojiMessage.sent[0]).length, 32_000);

  const checkCriteriaInput = async (completionDraft, { accepted = true, track = "code", enabled = true } = {}) => {
    const saved = { draft: "Fix this bug", completionDraft, sent: [], error: null };
    const context = {
      ...composeContext, draft: saved.draft, completionDraft, sendingRef: { current: new Set() },
      setDraft: value => { saved.draft = typeof value === "function" ? value(saved.draft) : value; },
      setCompletionDraft: value => { saved.completionDraft = value; },
      setAttachmentError: value => { saved.error = value; },
      props: { track, canSetCompletionCriteria: enabled, onSend: async (...args) => { saved.sent.push(args); return accepted; } },
    };
    await runInNewContext(`${sendHandler}; send`, context)();
    return saved;
  };
  const parsedCriteria = await checkCriteriaInput("  Fix the bug  \r\n\r\nAdd regression coverage\n");
  assert.deepEqual(JSON.parse(JSON.stringify(parsedCriteria.sent[0][1])), [
    { id: "criterion-1", text: "Fix the bug" }, { id: "criterion-2", text: "Add regression coverage" },
  ]);
  assert.equal(parsedCriteria.completionDraft, "", "Accepted sends clear the completion draft");
  assert.equal((await checkCriteriaInput("\n  ")).sent[0][1], undefined);
  for (const invalid of [Array.from({ length: 13 }, (_, index) => `Condition ${index}`).join("\n"), "x".repeat(501)]) {
    const rejected = await checkCriteriaInput(invalid);
    assert.equal(rejected.sent.length, 0, "Invalid criteria must not reach the API");
    assert.equal(rejected.draft, "Fix this bug");
    assert.equal(rejected.completionDraft, invalid);
    assert.equal(rejected.error, "completion.inputLimit");
  }
  assert.equal((await checkCriteriaInput("😀".repeat(500))).sent[0][1][0].text, "😀".repeat(500));
  const rejectedCriteriaSend = await checkCriteriaInput("Keep this criterion", { accepted: false });
  assert.equal(rejectedCriteriaSend.draft, "Fix this bug");
  assert.equal(rejectedCriteriaSend.completionDraft, "Keep this criterion");
  for (const options of [{ track: "cowork" }, { enabled: false }]) {
    assert.equal((await checkCriteriaInput("Hidden criterion", options)).sent[0][1], undefined);
  }

  const attachmentHandlers = composerCode.slice(composerCode.indexOf("const pickFiles ="), composerCode.indexOf("const fillIdea ="));
  const checkAttachmentReads = (attachments = []) => {
    const saved = { draft: "Read these files", attachments, error: null, sent: [], reading: false };
    const context = {
      ...composeContext, MAX_ATTACHMENTS: 5, MAX_ATTACHMENT_BYTES: 64 * 1024,
      sendingRef: { current: new Set() }, readingAttachmentsRef: { current: new Set() },
      setDraft: value => { saved.draft = typeof value === "function" ? value(saved.draft) : value; },
      setAttachments: value => { saved.attachments = typeof value === "function" ? value(saved.attachments) : value; },
      setAttachmentError: value => { saved.error = value; },
      setReadingAttachments: value => { saved.reading = value; },
      setSubmitting() {},
      props: { onSend: async message => { saved.sent.push(message); return true; } },
    };
    // Keep busy at its old render value to check that the refs guard synchronous races.
    const render = () => runInNewContext(`${attachmentHandlers}; ({ pickFiles, send })`, {
      ...context, draft: saved.draft, attachments: saved.attachments,
    });
    return { saved, context, render };
  };
  const delayedRead = checkAttachmentReads();
  const beforeReadFinishes = delayedRead.render();
  let finishReading;
  const reading = beforeReadFinishes.pickFiles([{ name: "delayed.txt", size: 14,
    text: () => new Promise(resolve => { finishReading = resolve; }) }]);
  assert.equal(delayedRead.saved.reading, true);
  assert.equal(delayedRead.context.readingAttachmentsRef.current.has("source"), true);
  await beforeReadFinishes.send();
  assert.equal(delayedRead.saved.sent.length, 0, "Sending during attachment reads must not omit the selected file");
  assert.equal(delayedRead.saved.draft, "Read these files");
  let duplicateReads = 0;
  await beforeReadFinishes.pickFiles([{ name: "duplicate.txt", size: 1,
    text: async () => { duplicateReads++; return "x"; } }]);
  assert.equal(duplicateReads, 0, "Overlapping file selections must not start another read");
  finishReading("loaded content");
  await reading;
  assert.equal(delayedRead.saved.reading, false);
  assert.equal(delayedRead.context.readingAttachmentsRef.current.has("source"), false);
  await delayedRead.render().send();
  assert.equal(delayedRead.saved.sent.length, 1);
  assert.match(delayedRead.saved.sent[0], /Read these files[\s\S]*Attached: delayed\.txt[\s\S]*loaded content/);
  assert.equal(delayedRead.saved.attachments.length, 0, "Sending the loaded file must clear it");

  const oldAttachments = [{ name: "old.txt", content: "keep this file" }];
  const failedRead = checkAttachmentReads(oldAttachments);
  await failedRead.render().pickFiles([
    { name: "partial.txt", size: 1, text: async () => "x" },
    { name: "unreadable.txt", size: 1, text: async () => { throw new Error("Cannot read file"); } },
  ]);
  assert.equal(failedRead.saved.reading, false, "A read failure must release the composer");
  assert.equal(failedRead.context.readingAttachmentsRef.current.has("source"), false);
  assert.deepEqual(failedRead.saved.attachments, oldAttachments, "A read failure must retain existing attachments");
  assert.ok(failedRead.saved.error);
  await failedRead.render().pickFiles([{ name: "recovered.txt", size: 1, text: async () => "x" }]);
  assert.equal(failedRead.saved.attachments.length, 2, "Another file can be attached after a read failure");
  assert.equal(failedRead.saved.error, null);

  const limitedRead = checkAttachmentReads(Array.from({ length: 4 }, (_, index) => ({ name: `old-${index}.txt`, content: "x" })));
  const oldSelection = limitedRead.render();
  let limitedReads = 0;
  const extraFiles = Array.from({ length: 2 }, (_, index) => ({ name: `extra-${index}.txt`, size: 1,
    text: async () => { limitedReads++; return "x"; } }));
  await oldSelection.pickFiles(extraFiles);
  assert.equal(limitedReads, 1, "Only the remaining attachment slot should be read");
  assert.equal(limitedRead.saved.attachments.length, 5);
  await oldSelection.pickFiles(extraFiles);
  assert.equal(limitedRead.saved.attachments.length, 5, "A stale selection handler must still respect the attachment cap");
  const readsAtLimit = limitedReads;
  await limitedRead.render().pickFiles(extraFiles);
  assert.equal(limitedReads, readsAtLimit, "A full attachment list must not start more reads");
  assert.equal(limitedRead.saved.error, "chat.attachmentLimit");

  const composerState = composerCode.slice(composerCode.indexOf("const sessionKey ="), composerCode.indexOf("const fileRef ="));
  const composerReact = composerState.match(/([\w$]+)\.useState/)[1];
  const composerDrafts = composerState.match(/([\w$]+)\.loadChatDraft/)[1];
  const emptyComposer = { draft: "", completionDraft: "", attachments: [], attachmentError: null, readingAttachments: false, submitting: false };
  const sessionComposers = () => {
    let stateIndex = 0, refIndex = 0;
    const states = [], refs = [], sent = [], jumps = [];
    const hooks = {
      useState: initial => {
        const index = stateIndex++;
        states[index] ??= typeof initial === "function" ? initial() : initial;
        return [states[index], update => { states[index] = typeof update === "function" ? update(states[index]) : update; }];
      },
      useRef: initial => { const index = refIndex++; return refs[index] ??= { current: initial }; },
      useMemo: create => create(), useEffect() {},
    };
    const render = (key, onSend = async message => { sent.push([key, message]); return true; }) => {
      stateIndex = 0; refIndex = 0;
      return runInNewContext(`${composerState}; ${attachmentHandlers}; ({send, pickFiles, setDraft, setAttachments, setCompletionDraft, draft, attachments, completionDraft, attachmentError, readingAttachments, submitting});`, {
        [composerReact]: hooks, EMPTY_COMPOSER: emptyComposer, busy: false,
        [composerDrafts]: { loadChatDraft: () => null, saveChatDraft: () => true },
        MAX_ATTACHMENTS: 5, MAX_ATTACHMENT_BYTES: 64 * 1024, MAX_MESSAGE_CHARS: 32_000,
        props: { sessionKey: key, track: key.startsWith("cowork") ? "cowork" : "code", canSetCompletionCriteria: true, onSend },
        t: key => key, scrollToBottom: () => jumps.push(key),
      });
    };
    return { render, sent, jumps };
  };
  for (const accepted of [false, true]) {
    const composer = sessionComposers();
    const initialA = composer.render("code-A");
    initialA.setDraft("A draft"); initialA.setAttachments([{ name: "A.txt", content: "A file" }]); initialA.setCompletionDraft("A condition");
    let finishA;
    const sendingA = composer.render("code-A", () => new Promise(resolve => { finishA = resolve; }));
    const pendingA = sendingA.send();
    await sendingA.send();
    const initialB = composer.render("cowork-B");
    initialB.setAttachments([{ name: "B.txt", content: "B file" }]); initialB.setCompletionDraft("B condition");
    finishA(accepted); await pendingA;
    const currentB = composer.render("cowork-B");
    assert.equal(currentB.draft, "", "A failed send must not restore its draft into an empty B composer");
    assert.equal(currentB.attachments[0].name, "B.txt", "A successful send must not remove B's attachments");
    assert.equal(currentB.completionDraft, "B condition", "A successful send must not remove B's completion draft");
    assert.equal(currentB.submitting, false);
    const returnedA = composer.render("code-A");
    assert.equal(returnedA.draft, accepted ? "" : "A draft");
    assert.equal(returnedA.attachments.length, accepted ? 0 : 1);
    assert.equal(returnedA.completionDraft, accepted ? "" : "A condition");
    assert.equal(returnedA.submitting, false);
    assert.equal(composer.jumps.length, 0, "A late send must not jump the current B transcript");
  }
  const independent = sessionComposers();
  independent.render("code-A").setDraft("A draft");
  let finishA;
  const pendingA = independent.render("code-A", () => new Promise(resolve => { finishA = resolve; })).send();
  independent.render("cowork-B").setDraft("B draft");
  await independent.render("cowork-B").send();
  assert.equal(independent.sent[0][0], "cowork-B", "Sending A must not lock B's composer");
  independent.render("cowork-B").setDraft("Next B draft");
  finishA(false); await pendingA;
  assert.equal(independent.render("cowork-B").draft, "Next B draft");
  assert.equal(independent.render("code-A").draft, "A draft");
  for (const failed of [false, true]) {
    const composer = sessionComposers();
    let finishRead;
    const readA = composer.render("code-A").pickFiles([{ name: "A.txt", size: 1, text: () => new Promise((resolve, reject) => { finishRead = failed ? () => reject(new Error("Unreadable")) : () => resolve("A content"); }) }]);
    await composer.render("cowork-B").pickFiles([{ name: "B.txt", size: 1, text: async () => "B content" }]);
    finishRead(); await readA;
    const currentB = composer.render("cowork-B");
    assert.equal(currentB.attachments.length, 1);
    assert.equal(currentB.attachments[0].name, "B.txt", "A late file read must remain attached to A");
    assert.equal(currentB.attachmentError, null, "A late read error must not appear in B");
    const returnedA = composer.render("code-A");
    assert.equal(returnedA.readingAttachments, false);
    assert.equal(returnedA.attachments.length, failed ? 0 : 1);
    assert.equal(Boolean(returnedA.attachmentError), failed);
  }
  const markdownReply = render({ track: "cowork", goal: "Explain", events: [{ id: "markdown", action: "chat.assistant", status: "succeeded", summary: "# Result\n\n- **Passed**\n\n```js\nconst value = '<tag>';\n```\n\n| File | Result |\n| --- | --- |\n| app.py | OK |\n\n[Docs](https://example.com) [Bad](javascript:alert) <script>alert(1)</script>", details: {}, created_at: "2026-10-31T00:00:00Z" }] });
  assert.match(markdownReply, /<h1>Result<\/h1>/);
  assert.match(markdownReply, /<li><strong>Passed<\/strong><\/li>/);
  assert.match(markdownReply, /<pre[^>]*><code>const value = &#x27;&lt;tag&gt;&#x27;;<\/code><\/pre>/);
  assert.match(markdownReply, /<table><thead>/);
  assert.match(markdownReply, /href="https:\/\/example.com"/);
  assert.doesNotMatch(markdownReply, /href="javascript:|<script>/);
  const markdownCode = (await server.transformRequest("/src/components/ChatMarkdown.tsx", { ssr: true })).code;
  const copyStart = markdownCode.indexOf("const copy =");
  const copyHandler = markdownCode.slice(copyStart, markdownCode.indexOf("return", copyStart));
  const copiedCode = [], copyStates = [];
  const copyCode = runInNewContext(`${copyHandler}; copy`, { code: "a\n  b", navigator: { clipboard: { writeText: async value => copiedCode.push(value) } }, setCopied: value => copyStates.push(value) });
  await copyCode();
  assert.equal(copiedCode[0], "a\n  b", "Code copying must preserve raw whitespace without Markdown fences");
  assert.equal(copyStates[0], "copied");

  const settingsCode = (await server.transformRequest("/src/components/SettingsPanel.tsx", { ssr: true })).code;
  const settingsHelpers = settingsCode.slice(settingsCode.indexOf("function toDraft("), settingsCode.indexOf("function SettingsPanel("));
  const settingsHandlers = settingsCode.slice(settingsCode.indexOf("const save ="), settingsCode.indexOf("if (!config || !draft) {"));
  const settingsApiName = settingsHandlers.match(/([\w$]+)\.updateConfig/)[1];
  const settingsErrorName = settingsHandlers.match(/([\w$]+)\.describeError/)[1];
  const baseConfig = { provider: "anthropic", model: "base-model", anthropic_api_url: "https://base.example",
    openai_api_url: "https://api.openai.com/v1", llm_max_tokens: 4096, llm_timeout_seconds: 300,
    command_timeout_seconds: 60, llm_max_context_files: 12, llm_max_context_bytes: 120000,
    models: [], active_model_id: null };
  const newModel = { label: "Local", provider: "openai", model: "local-model", base_url: "https://local.example/v1", api_key: "new-key" };
  const activatedConfig = { ...baseConfig, provider: "openai", model: "local-model", openai_api_url: newModel.base_url,
    models: [{ ...newModel, id: "local" }], active_model_id: "local" };
  const checkSettingsSave = (draftChanges = {}) => {
    const initialDraft = runInNewContext(`${settingsHelpers}; toDraft(config);`, { config: baseConfig });
    const saved = { config: baseConfig, draft: { ...initialDraft, ...draftChanges }, newModel: { ...newModel },
      busy: false, modelBusy: false, modelError: null, error: null, saved: false, requests: [] };
    const api = { updateConfig: async patch => { saved.requests.push(patch); return activatedConfig; } };
    const context = {
      [settingsApiName]: api, [settingsErrorName]: errorStub, t: key => key,
      savingRef: { current: false }, window: { confirm: () => true },
      setConfig: next => { saved.config = next; },
      setDraft: next => { saved.draft = typeof next === "function" ? next(saved.draft) : next; },
      setNewModel: next => { saved.newModel = next; }, setBusy: next => { saved.busy = next; },
      setModelBusy: next => { saved.modelBusy = next; }, setSaved: next => { saved.saved = next; },
      setError: next => { saved.error = next; }, setModelError: next => { saved.modelError = next; },
    };
    return { saved, api, context, render: () => {
      Object.assign(context, { config: saved.config, draft: saved.draft, newModel: saved.newModel, models: saved.config.models });
      return runInNewContext(`(() => { ${settingsHelpers}; ${settingsHandlers}; return {save, addModel, removeModel, activateModel}; })();`, context);
    } };
  };
  const modelFailure = checkSettingsSave({ llm_max_tokens: "8192", llm_timeout_seconds: "90", openai_api_key: "unsaved-key" });
  modelFailure.api.updateConfig = async () => { throw new Error("Model save failed"); };
  await modelFailure.render().addModel();
  assert.deepEqual(modelFailure.saved.newModel, newModel, "A failed model addition must retain its label, gateway and secret");
  assert.equal(modelFailure.saved.config, baseConfig);
  assert.equal(modelFailure.saved.modelError, "Model save failed");
  assert.equal(modelFailure.context.savingRef.current, false);
  assert.equal(modelFailure.saved.modelBusy, false);
  assert.equal(modelFailure.saved.draft.llm_max_tokens, "8192");
  modelFailure.api.updateConfig = async patch => { modelFailure.saved.requests.push(patch); return activatedConfig; };
  await modelFailure.render().addModel();
  assert.equal(modelFailure.saved.newModel.label, "", "Only a successful model addition should clear its form");
  assert.equal(modelFailure.saved.newModel.api_key, "");
  assert.equal(modelFailure.saved.draft.llm_provider, "openai", "An untouched provider should follow activation");
  assert.equal(modelFailure.saved.draft.llm_model, "local-model");
  assert.equal(modelFailure.saved.draft.openai_api_url, newModel.base_url);
  assert.equal(modelFailure.saved.draft.llm_max_tokens, "8192", "Model saves must preserve unsaved runtime limits");
  assert.equal(modelFailure.saved.draft.llm_timeout_seconds, "90");
  assert.equal(modelFailure.saved.draft.openai_api_key, "unsaved-key", "Model saves must preserve unsaved runtime keys");
  await modelFailure.render().save();
  assert.deepEqual(JSON.parse(JSON.stringify(modelFailure.saved.requests.at(-1))),
    { openai_api_key: "unsaved-key", llm_max_tokens: 8192, llm_timeout_seconds: 90 },
    "A subsequent runtime save should send the retained edits without undoing activation");

  const editedFlatModel = checkSettingsSave({ llm_provider: "fake", llm_model: "unsaved-model", openai_api_url: "https://unsaved.example" });
  await editedFlatModel.render().addModel();
  assert.equal(editedFlatModel.saved.draft.llm_provider, "fake");
  assert.equal(editedFlatModel.saved.draft.llm_model, "unsaved-model");
  assert.equal(editedFlatModel.saved.draft.openai_api_url, "https://unsaved.example");
  const removalDraft = checkSettingsSave({ llm_max_tokens: "8192" });
  await removalDraft.render().addModel();
  removalDraft.api.updateConfig = async () => baseConfig;
  await removalDraft.render().removeModel(activatedConfig.models[0]);
  assert.equal(removalDraft.saved.draft.llm_provider, baseConfig.provider, "Untouched model fields should follow active removal");
  assert.equal(removalDraft.saved.draft.openai_api_url, baseConfig.openai_api_url);
  assert.equal(removalDraft.saved.draft.llm_max_tokens, "8192");

  for (const startsWithModel of [false, true]) {
    const overlappingSave = checkSettingsSave({ llm_max_tokens: "8192" });
    let finishSave;
    overlappingSave.api.updateConfig = async patch => {
      overlappingSave.saved.requests.push(patch);
      return new Promise(resolve => { finishSave = () => resolve(activatedConfig); });
    };
    const handlers = overlappingSave.render();
    const pendingSave = startsWithModel ? handlers.addModel() : handlers.save();
    assert.equal(overlappingSave.context.savingRef.current, true);
    await handlers.save();
    await handlers.addModel();
    await handlers.activateModel("local");
    await handlers.removeModel(activatedConfig.models[0]);
    assert.equal(overlappingSave.saved.requests.length, 1, "Runtime and model saves must share a synchronous reentry guard");
    finishSave();
    await pendingSave;
    assert.equal(overlappingSave.context.savingRef.current, false);
    assert.equal(overlappingSave.saved.busy, false);
    assert.equal(overlappingSave.saved.modelBusy, false);
  }

  const scrollCode = (await server.transformRequest("/src/components/useChatScrollPinning.ts", { ssr: true })).code;
  const scrollStart = scrollCode.indexOf("function useChatScrollPinning(");
  const scrollBody = scrollCode.slice(scrollStart, scrollCode.indexOf("\n}", scrollStart) + 2);
  const scrollReact = scrollBody.match(/([\w$]+)\.useRef/)[1];
  const effects = [];
  let clock = 1000, jumpVisible = false, resize;
  const scrollHooks = {
    useRef: value => ({ current: value }), useCallback: callback => callback,
    useState: value => [value, next => { jumpVisible = next; }],
    useEffect: callback => { effects.push(callback); },
  };
  const pinned = runInNewContext(`${scrollBody}; useChatScrollPinning(0, "conversation")`, {
    [scrollReact]: scrollHooks, performance: { now: () => clock },
    ResizeObserver: class { constructor(callback) { resize = callback; } observe() {} disconnect() {} },
  });
  const viewport = { scrollTop: 600, scrollHeight: 1000, clientHeight: 400, calls: 0,
    scrollTo({ top }) { this.scrollTop = top - this.clientHeight; this.calls++; } };
  pinned.containerRef.current = viewport;
  pinned.contentRef.current = {};
  effects.forEach(effect => effect());
  const initialScrolls = viewport.calls;
  viewport.scrollTop = 100;
  pinned.markUserInput();
  pinned.updatePosition();
  viewport.scrollHeight = 1400;
  clock += 200;
  resize();
  assert.equal(viewport.calls, initialScrolls, "Incoming content must not pull a reader away from older messages");
  assert.equal(jumpVisible, true);
  pinned.scrollToBottom();
  assert.equal(viewport.scrollTop, 1000);
  assert.equal(jumpVisible, false, "Jumping to the latest message must restore following");
  viewport.scrollTop -= 40;
  pinned.markUserInput();
  pinned.updatePosition();
  viewport.scrollHeight += 80;
  const beforeCooldown = viewport.calls;
  clock += 100;
  resize();
  assert.equal(viewport.calls, beforeCooldown, "The 150ms user-input guard must win over content growth");
  clock += 100;
  resize();
  assert.equal(viewport.calls, beforeCooldown + 1, "Within 40px of the bottom, following resumes after the input guard");
  console.log("Desktop chat checks passed: folder flows, Markdown rendering and safe links, checkpoint diffs, prepared startup and retry, completion criteria and explicit evidence confirmation, acceptance/deletion confirmation and cancellation, empty-file patches, bilingual errors, stop handling, polling recovery, session draft/attachment isolation, settings draft preservation and scroll pinning.");
} finally {
  await server.close();
}
