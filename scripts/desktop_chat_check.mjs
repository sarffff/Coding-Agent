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
const server = await createServer({
  root: fileURLToPath(new URL("../apps/desktop", import.meta.url)),
  configFile: false,
  server: { middlewareMode: true },
  appType: "custom",
  plugins: [{
    name: "desktop-chat-fixtures",
    enforce: "pre",
    transform(source, id) {
      if (/[\\/]App\.tsx$/.test(id)) {
        const withFolder = source.replace("useState<Repository[]>([])",
          `useState<Repository[]>(${JSON.stringify([folder])})`);
        assert.notEqual(withFolder, source, "The app fixture must include an opened folder without tasks");
        const withCodeTab = withFolder.replace('useState<"cowork" | "code">("cowork")',
          'useState<"cowork" | "code">("code")');
        assert.notEqual(withCodeTab, withFolder, "The app fixture must select the Code tab");
        return withCodeTab;
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

  const cowork = render({ track: "cowork", selectedRepositoryId: "", sending: true });
  assert.match(cowork, /<details class="folder-picker">/);
  assert.match(cowork, /<button[^>]*class="folder-picker-option is-selected"[^>]*disabled=""[^>]*aria-pressed="true">[\s\S]*?<strong>No folder<\/strong>/);
  assert.match(cowork, /<textarea[^>]*disabled=""/);

  const { default: App } = await server.ssrLoadModule("/src/App.tsx");
  const app = renderToStaticMarkup(React.createElement(PreferencesProvider, null, React.createElement(App)));
  const emptyFolder = app.match(/<details class="rail-folder-group"[\s\S]*?<\/details>/)?.[0];
  assert.ok(emptyFolder, "An opened folder must appear before it has any tasks");
  assert.match(emptyFolder, /<summary title="D:\/work\/Notes">/);
  assert.match(emptyFolder, /<span>Notes<\/span>/);
  assert.match(emptyFolder, /class="rail-folder-count">0<\/span>/);
  assert.match(emptyFolder, /<button[^>]*class="rail-folder-new"/);

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
  const handlers = code.slice(code.indexOf("const sendChatMessage ="), code.indexOf("const decideChatApproval ="));
  const apiName = handlers.match(/([\w$]+)\.createTask/)[1];
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
      [folderApiName]: {
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
      repositoryPathInput: "", setWorkspaceSummary() {}, t: key => key, Error,
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
  await new Promise(setImmediate);
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
  assert.equal(canceledFolder.saved.repositoryDialogOpen, true, "Canceling the native chooser must open workspace settings");
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

  const workspaceHandlers = code.slice(code.indexOf("const changeWorkspaceRoot ="), code.indexOf("const executeTests ="));
  const stopLoopName = workspaceHandlers.match(/([\w$]+)\.stopAgentLoop/)[1];
  const checkWorkspace = ({ canceled = false, error = "" } = {}) => {
    const saved = { selectedRepositoryId: "old-folder", sourceLocation: { path: "a.py", line: 1 },
      desktopRuntime: { workspaceRoot: "D:/old" }, repositoryError: null, repositorySaving: false,
      loaded: 0, stopped: [], activeNav: "runs", chatComposingNew: false, activeTaskId: "old-task",
      loopRunning: true, runEvents: ["old"], chatPending: {}, chatBudget: {}, chatAction: "working", chatError: "old" };
    const context = {
      desktopBridge: {
        pickWorkspaceRoot: async () => canceled ? { canceled: true } : { canceled: false, workspaceRoot: "D:/new", error },
      },
      [stopLoopName]: { stopAgentLoop: async runId => saved.stopped.push(runId) },
      activeTask: { runId: "run-1" },
      loadOverviewData: async () => { saved.loaded += 1; },
      t: key => key, Error,
    };
    for (const key of Object.keys(saved).filter(key => !["loaded", "stopped"].includes(key))) {
      context[`set${key[0].toUpperCase()}${key.slice(1)}`] = value => {
        saved[key] = typeof value === "function" ? value(saved[key]) : value;
      };
    }
    const callbacks = runInNewContext(`${newSession}\n${workspaceHandlers}; ({ changeWorkspaceRoot });`, context);
    return { ...callbacks, saved };
  };
  const switched = checkWorkspace();
  await switched.changeWorkspaceRoot();
  assert.equal(switched.saved.desktopRuntime.workspaceRoot, "D:/new");
  assert.deepEqual(switched.saved.stopped, ["run-1"]);
  assert.equal(switched.saved.selectedRepositoryId, "");
  assert.equal(switched.saved.sourceLocation, null);
  assert.equal(switched.saved.chatComposingNew, true);
  assert.equal(switched.saved.activeTaskId, "");
  assert.equal(switched.saved.loaded, 1);
  assert.equal(switched.saved.repositorySaving, false);
  const canceledWorkspace = checkWorkspace({ canceled: true });
  await canceledWorkspace.changeWorkspaceRoot();
  assert.equal(canceledWorkspace.saved.loaded, 0);
  assert.equal(canceledWorkspace.saved.selectedRepositoryId, "old-folder");
  const failedWorkspace = checkWorkspace({ error: "backend down" });
  await failedWorkspace.changeWorkspaceRoot();
  assert.equal(failedWorkspace.saved.repositoryError, "backend down");
  assert.equal(failedWorkspace.saved.loaded, 0);

  const checkCallbacks = (hasGit) => {
    const original = { id: "old-task", runId: "old-run", repositoryId: "original-folder",
      lifecycleStatus: "ready_for_pr", status: "review", mode: "conservative", track: "code" };
    const saved = { tasks: [original], loopRunning: false, created: [], started: [] };
    const api = {
      createTask: async (...args) => { saved.created.push(args); return { id: "new-task", run_id: "new-run" }; },
      startAgentLoop: async (...args) => { saved.started.push(args); },
      stopAgentLoop: async () => ({ loop_running: true }),
    };
    const noop = () => {};
    const callbacks = runInNewContext(`${handlers}; ({ sendChatMessage, stopChatLoop });`, {
      [apiName]: api, activeTask: original, activeRepository: { has_git: hasGit },
      selectedRepositoryId: "another-folder", chatMode: "standard", railTab: "code",
      chatComposingNew: false, loopRunning: false, chatSendingRef: { current: false },
      setChatSending: noop, setChatError: noop, setChatComposingNew: noop,
      setActiveTaskId: noop, setChatPending: noop, setChatAction: noop,
      setTasks: update => { saved.tasks = update(saved.tasks); },
      setLoopRunning: value => { saved.loopRunning = value; },
      taskFromApi: task => task, t: key => key,
    });
    return { ...callbacks, saved, original };
  };
  const localChat = checkCallbacks(false);
  assert.equal(await localChat.sendChatMessage("A second goal"), true);
  assert.equal(localChat.saved.created.length, 1);
  assert.equal(localChat.saved.created[0][1], "original-folder");
  assert.equal(localChat.saved.created[0][2], "conservative");
  assert.equal(localChat.saved.started[0][0], "new-run");
  assert.ok(localChat.saved.tasks.includes(localChat.original));

  const gitChat = checkCallbacks(true);
  assert.equal(await gitChat.sendChatMessage("Continue this PR"), true);
  assert.equal(gitChat.saved.created.length, 0);
  assert.equal(gitChat.saved.started[0][0], "old-run");
  assert.equal(gitChat.saved.started[0][1].feedback, "Continue this PR");
  gitChat.saved.loopRunning = false;
  await gitChat.stopChatLoop();
  assert.equal(gitChat.saved.loopRunning, true, "A stopping backend keeps the composer locked");
  console.log("Desktop chat checks passed: folder opening and cancellation, empty-folder sidebar, rendering, checkpoint diffs, folder follow-up isolation, and stop-state handling.");
} finally {
  await server.close();
}
