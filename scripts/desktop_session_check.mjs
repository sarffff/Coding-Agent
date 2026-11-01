import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import { runInNewContext } from "node:vm";

const require = createRequire(new URL("../apps/desktop/package.json", import.meta.url));
const { createServer } = await import(pathToFileURL(require.resolve("vite")));
const server = await createServer({ root: fileURLToPath(new URL("../apps/desktop", import.meta.url)), configFile: false, server: { middlewareMode: true, hmr: false }, appType: "custom" });
try {
  const code = (await server.transformRequest("/src/App.tsx", { ssr: true })).code;
  const mergeRunEvents = runInNewContext(`${code.slice(code.indexOf("function mergeRunEvents("), code.indexOf("function taskFromApi("))}; mergeRunEvents`);
  const apiCode = (await server.transformRequest("/src/lib/api.ts", { ssr: true })).code;
  const errorClass = apiCode.slice(apiCode.indexOf("class ApiRequestError"), apiCode.indexOf("async function request("));
  const ApiRequestError = runInNewContext(`${errorClass}; ApiRequestError`);
  const handler = code.slice(code.indexOf("const sendChatMessage ="), code.indexOf("const stopChatLoop ="));
  const stopHandler = code.slice(code.indexOf("const stopCoworkRequest ="), code.indexOf("const decideChatApproval ="));
  const apiName = handler.match(/([\w$]+)\.createTask/)[1];
  const errorName = handler.match(/([\w$]+)\.describeError/)[1];
  const failures = [];
  const check = async (name, action) => {
    try {
      await action();
      console.log(`PASS ${name}`);
    } catch (error) {
      failures.push(error);
      console.error(`FAIL ${name}: ${error.message}`);
    }
  };
  let finishCreate;
  const state = { selected: "A", error: null, tasks: [], loop: false, setup: false };
  const noop = () => {};
  const context = {
    [apiName]: { ApiRequestError, createTask: () => new Promise(resolve => { finishCreate = resolve; }), startAgentLoop: async () => ({ task: {}, loop_running: true, action: "review_patch" }) },
    [errorName]: { describeError: error => error.message },
    chatComposingNew: true, railTab: "code", activeTask: null, loopRunning: false,
    chatSendingRef: { current: false }, chatModeSavingRef: { current: false }, chatModelSavingRef: { current: false },
    taskRef: { current: "A" }, chatSelectionRef: { current: 0 }, createsCodeTask: true,
    completedLocalTask: false, selectedRepositoryId: "folder-A", chatMode: "standard",
    setChatSending: noop, setChatError: value => { state.error = value; },
    setTasks: update => { state.tasks = update(state.tasks); },
    setActiveTaskId: id => { state.selected = id; }, setChatComposingNew: noop,
    setChatPending: noop, setChatAction: noop, setLoopRunning: value => { state.loop = value; },
    taskFromApi: value => value, updateTaskFromApi: noop, t: value => value,
    chatSessionKey: "A", chatSessionRef: { current: "A" },
    chatRequestsRef: { current: new Map() }, chatRetriesRef: { current: new Map() }, chatErrorsRef: { current: new Map() },
    chatComposerKeysRef: { current: new Map() },
    chatSetupFeedbackRef: { current: "" }, crypto: { randomUUID }, AbortController,
    setChatSetupTaskId: noop, setChatSetupOpen: value => { state.setup = value; }, setChatSetupError: noop,
    setValidationDirty: noop, setCompletionDirty: noop, setChatStateMessage: noop,
    setChatReplies: update => { state.replies = update(state.replies ?? {}); }, setChatPollError: noop,
    mergeRunEvents,
  };
  const send = runInNewContext(`${handler}; sendChatMessage`, context);
  const pending = send("A task goal");
  state.selected = "B";
  context.taskRef.current = "B";
  context.chatSelectionRef.current++;
  context.chatSessionRef.current = "B";
  finishCreate({ id: "created-A", run_id: "run-A" });
  assert.equal(await pending, true);
  assert.equal(state.selected, "B", "A late creation must preserve the user's current selection");
  assert.equal(state.loop, false, "A late creation must not change B's running indicator");
  assert.equal(state.tasks[0].id, "created-A", "The created task must remain available in its own history");
  assert.equal(state.setup, false, "A late response must not open A's setup over B");

  let starts = 0;
  context[apiName].createTask = async () => ({ id: "prepared-A", run_id: "prepared-run" });
  context[apiName].startAgentLoop = async () => { starts++; return { task: {}, loop_running: true }; };
  context.chatSessionRef.current = "A";
  context.taskRef.current = "";
  await send("Prepare a task");
  assert.equal(state.selected, "prepared-A");
  assert.equal(state.setup, true, "The first Code send must show the required checks before execution");
  assert.equal(starts, 0, "Creating a task must not run its default checks before user preparation");
  assert.equal(context.chatComposerKeysRef.current.get("prepared-A"), "A", "Task creation preserves the logical draft identity");

  let rejectStart;
  context.chatComposingNew = false;
  context.createsCodeTask = false;
  context.activeTaskStarted = true;
  context.activeTask = { id: "code-A", runId: "run-A" };
  context.chatSessionKey = "code-A";
  context.chatSessionRef.current = "code-A";
  context.taskRef.current = "code-A";
  context[apiName].startAgentLoop = () => new Promise((_resolve, reject) => { rejectStart = reject; });
  const failed = send("Continue A");
  context.taskRef.current = "B";
  context.chatSessionRef.current = "B";
  context.chatSelectionRef.current++;
  state.error = "B's own error";
  rejectStart(new Error("A failed"));
  assert.equal(await failed, false);
  assert.equal(state.error, "B's own error", "A late failure must not overwrite B's error");
  assert.equal(context.chatErrorsRef.current.get("code-A"), "A failed", "A failure remains available when returning to A");

  const requestIds = [];
  let attempts = 0;
  context.activeTask = { id: "cowork-A", runId: "chat-run", track: "cowork" };
  context.chatSessionKey = "cowork-A";
  context.chatSessionRef.current = "cowork-A";
  context.taskRef.current = "cowork-A";
  context[apiName].sendCoworkChat = async (_task, _message, requestId) => {
    requestIds.push(requestId);
    if (attempts++ === 0) throw new Error("Timeout");
    return { reply: "OK" };
  };
  context[apiName].listRunEvents = async () => ({ items: [] });
  context.setRunEvents = noop;
  assert.equal(await send("Retry this message"), false);
  assert.equal(await send("Retry this message"), true);
  assert.equal(requestIds[0], requestIds[1], "A failed Cowork retry must preserve the server message identity");
  assert.equal(context.chatRetriesRef.current.has("cowork-A"), false, "Successful retries clear the pending request");

  await check("Cowork reply is visible after switching away and back", async () => {
    let finishReply;
    state.events = [];
    context.setRunEvents = items => { state.events = typeof items === "function" ? items(state.events) : items; };
    context[apiName].sendCoworkChat = () => new Promise(resolve => { finishReply = resolve; });
    context[apiName].listRunEvents = async () => ({ items: [{ id: "reply-A" }] });
    const pendingReply = send("A pending reply");
    context.taskRef.current = "cowork-B";
    context.chatSessionRef.current = "cowork-B";
    context.chatSelectionRef.current++;
    context.taskRef.current = "cowork-A";
    context.chatSessionRef.current = "cowork-A";
    context.chatSelectionRef.current++;
    finishReply({ reply: "A reply" });
    assert.equal(await pendingReply, true);
    assert.equal(state.events[0]?.id, "reply-A", "Returning to A before its reply completes must refresh A's transcript");
  });

  await check("Completed replies survive refresh failure and the next send", async () => {
    context.activeTask = { id: "retained-chat", runId: "retained-run", track: "cowork" };
    context.chatSessionKey = "retained-chat";
    context.chatSessionRef.current = "retained-chat";
    context.taskRef.current = "retained-chat";
    context.setChatPollError = error => { state.pollError = error; };
    context.setRunEvents = items => { state.events = typeof items === "function" ? items(state.events) : items; };
    context[apiName].listRunEvents = async () => { throw new Error("History unavailable"); };
    context[apiName].sendCoworkChat = async (_task, _message, _requestId, _signal, onDelta, files, text) => {
      assert.equal(text, "First message");
      assert.equal(files[0].name, "note.md");
      onDelta("First ");
      onDelta("answer");
      return { reply: "First answer" };
    };
    assert.equal(await send("First message\n\n# Attached: note.md\n```\nsource\n```", undefined, [{ name: "note.md", content: "source" }], "First message"), true);
    assert.equal(state.pollError, "History unavailable");
    assert.equal(context.chatRetriesRef.current.has("retained-chat"), false, "An accepted message must not become a failed retry because history is unavailable");
    const first = state.replies["retained-chat"][0];
    assert.equal(first.content, "First answer");
    assert.equal(first.status, "completed");
    let finishSecond;
    context[apiName].sendCoworkChat = (_task, _message, _requestId, _signal, onDelta) => {
      onDelta("Second partial");
      return new Promise(resolve => { finishSecond = resolve; });
    };
    const secondSend = send("Second message");
    const during = state.replies["retained-chat"];
    assert.equal(during.length, 2);
    assert.equal(during[0].requestId, first.requestId);
    assert.equal(during[0].content, "First answer");
    assert.equal(during[1].content, "Second partial");
    finishSecond({ reply: "Second answer" });
    assert.equal(await secondSend, true);
    const second = state.replies["retained-chat"][1];
    const audit = reply => ({ id: reply.requestId, action: "chat.assistant", details: { request_id: reply.requestId }, summary: reply.content });
    context[apiName].sendCoworkChat = async (_task, _message, requestId) => {
      state.third = { requestId, content: "Third answer" };
      return { reply: "Third answer" };
    };
    context[apiName].listRunEvents = async () => ({ items: [audit(first), audit(second), audit(state.third)] });
    assert.equal(await send("Third message"), true);
    assert.equal(state.replies["retained-chat"], undefined, "Only confirmed audit replies remove their corresponding local receipts");
    assert.equal(state.pollError, null);
    const current = [{ id: "old" }, { id: "new" }];
    assert.deepEqual(Array.from(mergeRunEvents(current, [{ id: "old" }]), event => event.id), ["old", "new"], "A stale refresh must not erase a newer audit entry");
  });

  await check("Follow-up Code task owns its composer draft", async () => {
    context.activeTask = { id: "completed-A", runId: "completed-run", repositoryId: "folder-A", track: "code", status: "completed" };
    context.chatSessionKey = "completed-A";
    context.chatSessionRef.current = "completed-A";
    context.taskRef.current = "completed-A";
    context.createsCodeTask = true;
    context.completedLocalTask = true;
    context[apiName].createTask = async () => ({ id: "follow-up-B", run_id: "follow-up-run" });
    assert.equal(await send("A new goal after completion"), true);
    assert.notEqual(context.chatComposerKeysRef.current.get("follow-up-B"), "completed-A", "Completed A and new B must not share their composer draft");
  });

  await check("Restoring a new draft never reuses a pending or claimed identity", async () => {
    const source = code.slice(code.indexOf("const startNewChatSession ="), code.indexOf("const selectChatSession ="));
    const draftName = source.match(/([\w$]+)\.createNewChatDraftId/)[1];
    const saved = { id: "original-draft", created: 0 };
    const draftContext = {
      [draftName]: { createNewChatDraftId: () => `fresh-${++saved.created}` }, chatDraftScope: "api-instance",
      chatSelectionRef: { current: 0 }, taskRef: { current: "" },
      chatRequestsRef: { current: new Map() }, chatComposerKeysRef: { current: new Map() },
      setChatDraftId: id => { saved.id = id; },
      activeTask: { sourceRepositoryId: "source-folder" },
      setSelectedRepositoryId: id => { saved.repository = id; },
    };
    for (const name of ["ActiveNav", "ChatComposingNew", "ActiveTaskId", "RunEvents", "ChatPending", "ChatBudget", "ChatAction", "ChatError", "LoopRunning", "ChatSending", "ChatStateMessage"]) draftContext[`set${name}`] = noop;
    const open = runInNewContext(`${source}; startNewChatSession`, draftContext);
    const originalKey = "new:code:folder:original-draft";
    open(originalKey);
    assert.equal(saved.id, "original-draft", "Track/folder navigation retains an unused persisted draft identity");
    draftContext.chatRequestsRef.current.set(originalKey, {});
    open(originalKey);
    assert.equal(saved.id, "fresh-1", "A pending first send requires an independent new composer");
    draftContext.chatRequestsRef.current.clear();
    draftContext.chatComposerKeysRef.current.set("created-task", originalKey);
    open(originalKey);
    assert.equal(saved.id, "fresh-2", "A created task's logical identity cannot be reused by a new chat");
    open();
    assert.equal(saved.id, "fresh-3", "Explicit new chat always creates a distinct identity");
    assert.equal(saved.repository, "source-folder", "A new task starts from the source folder instead of its execution checkout");
  });

  await check("Rename changes only the displayed title and guards duplicate saves", async () => {
    const mapper = runInNewContext(`${code.slice(code.indexOf("function taskFromApi("), code.indexOf("function eventToStreamEvent("))}; taskFromApi`);
    const original = { id: "named-task", goal: "Original goal\nOriginal details", display_name: null, status: "coding", track: "code" };
    assert.equal(mapper(original).title, "Original goal");
    const renamed = mapper({ ...original, display_name: "Friendly title" });
    assert.equal(renamed.title, "Friendly title");
    assert.equal(renamed.goal, original.goal, "Completion criteria and original goal remain unchanged by display naming");
    const source = code.slice(code.indexOf("const renameChatSession ="), code.indexOf("const stopChatLoop ="));
    const renameName = source.match(/([\w$]+)\.renameTask/)[1];
    let finishRename;
    const saved = { calls: 0, tasks: [mapper(original)] };
    const renameContext = { [renameName]: { renameTask: () => { saved.calls++; return new Promise(resolve => { finishRename = resolve; }); } },
      renameRequestsRef: { current: new Set() }, taskFromApi: mapper, setTasks: update => { saved.tasks = update(saved.tasks); } };
    const rename = runInNewContext(`${source}; renameChatSession`, renameContext);
    const saving = rename(original.id, "Friendly title");
    assert.equal(await rename(original.id, "Another title"), false);
    assert.equal(saved.calls, 1);
    finishRename({ ...original, display_name: "Friendly title" });
    assert.equal(await saving, true);
    assert.equal(saved.tasks[0].goal, original.goal);
    assert.equal(renameContext.renameRequestsRef.current.size, 0);
    renameContext[renameName].renameTask = async () => { throw new Error("Save failed"); };
    assert.equal(await rename(original.id, "Retry title"), false);
    assert.equal(renameContext.renameRequestsRef.current.size, 0);
    renameContext[renameName].renameTask = () => new Promise(resolve => { finishRename = resolve; });
    const lateRename = rename(original.id, "Late title");
    saved.tasks = [];
    finishRename({ ...original, display_name: "Late title" });
    assert.equal(await lateRename, true);
    assert.equal(saved.tasks.length, 0, "A rename response arriving after deletion must not recreate the removed session");
  });

  await check("Deleting a folder preserves navigation that changed while waiting", async () => {
    const source = code.slice(code.indexOf("const removeRepository ="), code.indexOf("const runTask ="));
    const selection = code.slice(code.indexOf("const selectChatSession ="), code.indexOf("const openRegisteredFolder ="));
    const deleteName = source.match(/([\w$]+)\.deleteRepository/)[1];
    const draftName = source.match(/([\w$]+)\.removeChatDraft/)[1];
    let finishDelete;
    const removedTask = { id: "removed-task", repositoryId: "removed-folder", track: "code" };
    const otherTask = { id: "other-task", repositoryId: "other-folder", track: "code" };
    const saved = { tasks: [removedTask, otherTask], active: removedTask.id, selected: removedTask.repositoryId,
      replies: { "removed-alias": [{}], "other-task": [{}] }, deletedDrafts: [], resets: 0 };
    const deletionContext = {
      [deleteName]: { ApiRequestError, deleteRepository: () => new Promise(resolve => { finishDelete = resolve; }) },
      [draftName]: { removeChatDraft: (_scope, key) => { saved.deletedDrafts.push(key); return true; } },
      [errorName]: { describeError: error => error.message },
      repositories: [{ id: "removed-folder", name: "Removed folder" }, { id: "other-folder", name: "Other folder" }],
      tasks: saved.tasks, activeTask: removedTask, selectedRepositoryId: removedTask.repositoryId, chatDraftScope: "api-instance",
      taskRef: { current: removedTask.id }, chatSelectionRef: { current: 0 },
      chatComposerKeysRef: { current: new Map([[removedTask.id, "removed-alias"]]) }, chatRequestsRef: { current: new Map() },
      chatRetriesRef: { current: new Map() }, chatErrorsRef: { current: new Map() },
      window: { confirm: () => true }, t: key => key,
      setTasks: update => { saved.tasks = update(saved.tasks); }, setRepositories: noop, setRepositoryError: noop,
      setActiveTaskId: id => { saved.active = id; }, setSelectedRepositoryId: update => { saved.selected = typeof update === "function" ? update(saved.selected) : update; },
      setDeletedChatDraftKeys: noop, setChatReplies: update => { saved.replies = update(saved.replies); },
      startNewChatSession: () => { saved.resets++; saved.active = "new"; },
    };
    for (const name of ["ActiveNav", "ChatComposingNew", "ChatPending", "ChatAction", "ChatError", "ChatSending", "ChatStateMessage", "LoopRunning", "ChatAgentState", "RunEvents", "ChatBudget", "RailTab"]) deletionContext[`set${name}`] = noop;
    const callbacks = runInNewContext(`${source}; ${selection}; ({ removeRepository, selectChatSession })`, deletionContext);
    const deleting = callbacks.removeRepository("removed-folder");
    callbacks.selectChatSession("other-task");
    finishDelete();
    await deleting;
    assert.equal(saved.active, "other-task");
    assert.equal(saved.selected, "other-folder");
    assert.equal(saved.resets, 0, "A folder deletion cannot open a new chat over a later selection");
    assert.deepEqual(saved.tasks.map(task => task.id), ["other-task"]);
    assert.deepEqual(saved.deletedDrafts, ["removed-task", "removed-alias"]);
    assert.equal(saved.replies["removed-alias"], undefined);
    assert.equal(saved.replies["other-task"].length, 1);
  });

  await check("Cowork Stop during conversation creation prevents the model request", async () => {
    let finishConversation;
    let modelCalls = 0;
    context.chatComposingNew = true;
    context.railTab = "cowork";
    context.activeTask = null;
    context.chatSessionKey = "cowork-draft-stop";
    context.chatSessionRef.current = "cowork-draft-stop";
    context.taskRef.current = "";
    context[apiName].createTask = () => new Promise(resolve => { finishConversation = resolve; });
    context[apiName].sendCoworkChat = async () => { modelCalls++; return { reply: "Must not run" }; };
    context[apiName].cancelCoworkChat = async () => ({ cancelled: true });
    const stop = runInNewContext(`${stopHandler}; stopCoworkRequest`, context);
    const pendingConversation = send("Cancel before conversation creation finishes");
    await stop();
    finishConversation({ id: "cancelled-conversation", run_id: "cancelled-run" });
    await pendingConversation;
    assert.equal(modelCalls, 0, "Stopping during createTask must prevent the subsequent model request");
    assert.equal(context.chatRequestsRef.current.size, 0, "A cancelled creation must release its composer busy state");
  });

  const base = apiCode.slice(apiCode.indexOf("function resolveApiBaseUrl()"), apiCode.indexOf("class ApiRequestError"));
  const requestCode = apiCode.slice(apiCode.indexOf("async function request("), apiCode.indexOf("function getApiHealth("));
  const desktopName = base.match(/([\w$]+)\.desktopBridge/)[1];
  const bridge = { status: { apiBaseUrl: "http://127.0.0.1:18001" } };
  const urls = [];
  const apiContext = {
    ApiRequestError,
    [desktopName]: { desktopBridge: bridge },
    __vite_ssr_import_meta__: { env: {} },
    fetch: async url => { urls.push(url); return { ok: true, json: async () => ({}) }; },
  };
  const request = runInNewContext(`${base}; ${requestCode}; request`, apiContext);
  await request("/health");
  bridge.status.apiBaseUrl = "http://127.0.0.1:18002";
  await request("/health");
  assert.equal(urls[1], "http://127.0.0.1:18002/health", "Requests must follow a restarted backend's new port");
  await check("Unavailable desktop backend never fetches the browser fallback", async () => {
    for (const backendState of ["failed", "stopped"]) {
      for (const apiBaseUrl of ["", "http://127.0.0.1:18002"]) {
        bridge.status = { apiBaseUrl, backendReady: false, backendState, error: "Managed API is unavailable" };
        const before = urls.length;
        await assert.rejects(request("/health"), error => error instanceof ApiRequestError && error.code === "BACKEND_UNAVAILABLE");
        assert.equal(urls.length, before, `${backendState} desktop backend must not fetch a stale address or localhost:8000`);
      }
    }
    apiContext[desktopName].desktopBridge = undefined;
    await request("/health");
    assert.equal(urls.at(-1), "http://127.0.0.1:8000/health", "Browser mode retains its default API address");
  });
  if (failures.length) throw new AggregateError(failures, `${failures.length} desktop session checks failed`);
  console.log("Desktop session and API recovery checks passed");
} finally {
  await server.close();
}
