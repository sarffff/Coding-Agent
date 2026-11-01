import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { runInNewContext } from "node:vm";

const require = createRequire(new URL("../apps/desktop/package.json", import.meta.url));
const { createServer } = await import(pathToFileURL(require.resolve("vite")));
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const storage = new Map();
let denyWrites = false;
globalThis.window = { localStorage: {
  getItem: key => key === "forge.language" ? "en-US" : storage.get(key) ?? null,
  setItem: (key, value) => { if (denyWrites) throw new Error("Quota exceeded"); storage.set(key, value); },
  removeItem: key => { if (denyWrites) throw new Error("Quota exceeded"); storage.delete(key); },
}, matchMedia: () => ({ matches: false }) };
const server = await createServer({ root: fileURLToPath(new URL("../apps/desktop", import.meta.url)), configFile: false,
  server: { middlewareMode: true, hmr: false }, appType: "custom", plugins: [{
    name: "draft-warning-fixture", enforce: "pre", transform(source, id) {
      if (!/[\\/]ChatPanel\.tsx\?draft-warning$/.test(id)) return;
      return source.replace("useState<Record<string, boolean>>({})", 'useState<Record<string, boolean>>({"ui-session": true})');
    },
  }] });
try {
  const drafts = await server.ssrLoadModule("/src/lib/chatDrafts.ts");
  const firstId = drafts.getNewChatDraftId("api-a");
  assert.match(firstId, /^[a-f\d-]{36}$/i);
  assert.equal(drafts.getNewChatDraftId("api-a"), firstId, "The last new composer identity survives a restart");
  assert.notEqual(drafts.getNewChatDraftId("api-b"), firstId, "New-composer pointers are scoped to the API instance");
  const nextId = drafts.createNewChatDraftId("api-a");
  assert.notEqual(nextId, firstId, "New chat creates a separate persistent draft identity");
  assert.equal(drafts.getNewChatDraftId("api-a"), nextId);
  const stored = { draft: "Fix this", completionDraft: "Keep original tests", attachments: [{ name: "notes.md", content: "你好\n  source" }] };
  assert.equal(drafts.saveChatDraft("api-a", "new:code:repo", stored), true);
  assert.deepEqual(JSON.parse(JSON.stringify(drafts.loadChatDraft("api-a", "new:code:repo"))), stored);
  assert.equal(drafts.loadChatDraft("api-b", "new:code:repo"), null, "Drafts from another API instance are isolated");
  assert.equal(drafts.moveChatDraft("api-a", "new:code:repo", "task-a", stored), true);
  assert.equal(drafts.loadChatDraft("api-a", "new:code:repo"), null);
  assert.equal(drafts.loadChatDraft("api-a", "task-a").draft, "Fix this");
  denyWrites = true;
  assert.doesNotThrow(() => drafts.createNewChatDraftId("unavailable"));
  assert.equal(drafts.saveChatDraft("api-a", "task-a", { ...stored, draft: "New text" }), false);
  assert.equal(drafts.loadChatDraft("api-a", "task-a").draft, "Fix this", "Failed persistence does not erase the earlier draft");
  denyWrites = false;
  assert.equal(drafts.removeChatDraft("api-a", "task-a"), true);
  assert.equal(drafts.loadChatDraft("api-a", "task-a"), null);
  for (let index = 0; index < 32; index++) assert.equal(drafts.saveChatDraft("full", `draft-${index}`, { ...stored, attachments: [] }), true);
  assert.equal(drafts.saveChatDraft("full", "draft-33", stored), false, "Draft count is bounded without evicting another draft");
  assert.equal(drafts.moveChatDraft("full", "draft-0", "task-0", stored), true, "Migrating a new session remains possible at the count limit");
  assert.equal(drafts.saveChatDraft("large", "oversized", { ...stored, attachments: [{ name: "large.txt", content: "x".repeat(1024 * 1024) }] }), false);
  storage.set("forge.chat-drafts.v1:corrupt", "not-json");
  assert.equal(drafts.loadChatDraft("corrupt", "task"), null);
  assert.equal(drafts.saveChatDraft("corrupt", "task", stored), false, "Corrupt storage is not silently replaced");

  const { PreferencesProvider } = await server.ssrLoadModule("/src/lib/preferences.tsx");
  const { ChatPanel } = await server.ssrLoadModule("/src/components/ChatPanel.tsx");
  const { ChatPanel: WarningPanel } = await server.ssrLoadModule("/src/components/ChatPanel.tsx?draft-warning");
  const noop = () => {};
  const base = { sessionKey: "ui-session", goal: null, plan: undefined, events: [], loopRunning: false, sending: false,
    status: "coding", hasActiveRun: true, track: "cowork", repositories: [], selectedRepositoryId: "", onSelectRepository: noop,
    onImportFolder: noop, onBrowseFolder: noop, onSend: async () => true, onStop: noop, onOpenPublish: noop,
    reviewPatch: false, onReviewPatch: noop, error: null, mode: "standard", modeLocked: false, onModeChange: noop,
    pending: null, onDecide: noop, budget: null };
  const render = (overrides = {}, Component = ChatPanel) => renderToStaticMarkup(React.createElement(PreferencesProvider, null,
    React.createElement(Component, { ...base, ...overrides })));
  drafts.saveChatDraft("reopened", "task-a", stored);
  const reopened = render({ draftScope: "reopened", draftSessionKey: "task-a" });
  assert.match(reopened, />Fix this<\/textarea>/, "The actual composer restores its persisted text at mount");
  assert.match(reopened, /class="chat-attachment"/);
  assert.match(reopened, /notes\.md/);
  const event = { id: "user", action: "chat.user", status: "succeeded", created_at: "2026-10-31T13:00:00Z",
    summary: "A long legacy payload with attached content", details: { request_id: "request-a", message_text: "Read these files", attachments: [{ name: "safe.md", content: "你好\n<script>unsafe()</script>" }] } };
  const sent = render({ events: [event] });
  assert.match(sent, /Read these files/);
  assert.match(sent, /aria-label="Sent attachments"/);
  assert.match(sent, /safe\.md/);
  assert.match(sent, /&lt;script&gt;unsafe\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(sent, /A long legacy payload|<script>/);
  assert.match(sent, /<pre tabindex="0" aria-label="safe.md"/);
  assert.doesNotMatch(render({ events: [{ ...event, details: {} }] }), /chat-sent-attachments/, "Legacy text is not guessed into an attachment");
  const goal = render({ goal: "Full Code input", track: "code", events: [{ ...event, action: "task.create" }] });
  assert.match(goal, /Read these files/);
  assert.match(goal, /Sent attachments/);
  assert.doesNotMatch(goal, /Full Code input/);
  assert.match(render({}, WarningPanel), /could not be saved locally/);

  const stream = { requestId: "request-a", content: "A **partial** reply", status: "streaming", userMessage: "Read these files", attachments: stored.attachments };
  const partial = render({ streamingReplies: [stream], sending: true });
  assert.match(partial, /<strong>partial<\/strong>/);
  assert.match(partial, /Replying…/);
  assert.match(partial, /Sent attachments/);
  const recorded = render({ events: [event], streamingReplies: [stream] });
  assert.equal((recorded.match(/class="chat-bubble">Read these files/g) ?? []).length, 1, "The temporary user turn is not duplicated after its audit event arrives");
  const reply = { ...event, id: "reply", action: "chat.assistant", summary: "Final answer", details: { request_id: "request-a" } };
  assert.doesNotMatch(render({ events: [event, reply], streamingReplies: [stream] }), /A <strong>partial/, "Completed audit text replaces the temporary stream");
  assert.match(render({ streamingReplies: [{ ...stream, status: "failed" }] }), /reply above is incomplete|content above is incomplete/);
  assert.match(render({ streamingReplies: [{ ...stream, status: "cancelled" }] }), /Stopped; the reply above is incomplete/);
  assert.match(render({ streamingReplies: [{ ...stream, status: "completed" }] }), /Reply received; chat history is awaiting refresh/);
  assert.doesNotMatch(render({ track: "code", streamingReplies: [stream] }), /A <strong>partial/);
  const nextReply = { requestId: "request-b", content: "The second reply is arriving", status: "streaming", userMessage: "Next question" };
  const pendingHistory = render({ streamingReplies: [{ ...stream, status: "completed" }, nextReply] });
  assert.match(pendingHistory, /A <strong>partial<\/strong> reply/);
  assert.match(pendingHistory, /The second reply is arriving/, "A new stream preserves an earlier reply whose audit refresh failed");
  const mergedHistory = render({ events: [event, reply], streamingReplies: [{ ...stream, status: "completed" }, nextReply] });
  assert.equal((mergedHistory.match(/Final answer/g) ?? []).length, 1);
  assert.doesNotMatch(mergedHistory, /A <strong>partial/);
  assert.match(mergedHistory, /The second reply is arriving/, "Recording one reply only deduplicates its own request");

  const code = (await server.transformRequest("/src/components/ChatPanel.tsx", { ssr: true })).code;
  const draftImport = code.match(/([\w$]+)\.saveChatDraft/)[1];
  const sendStart = code.indexOf("const send =");
  const sendCode = code.slice(sendStart, code.indexOf("const fillIdea =", sendStart));
  const migrationStart = code.indexOf("const deleted = new Set(props.deletedDraftKeys");
  const migrationEnd = code.slice(migrationStart).search(/\n\s*}, \[/);
  const migration = code.slice(migrationStart, migrationStart + migrationEnd);
  const persistStart = code.indexOf("const errors = {};");
  const persistEnd = code.slice(persistStart).search(/\n\s*}, \[/);
  const persistence = code.slice(persistStart, persistStart + persistEnd);
  assert.ok(migrationEnd > 0 && persistEnd > 0, "Checks must exercise actual draft effects");
  let finish;
  const composerA = { ...stored, attachmentError: null, readingAttachments: false, submitting: false };
  const sourceComposerKey = "sending:A";
  const otherComposerKey = "sending:B";
  const context = { [draftImport]: drafts, draftScope: "sending", draftSessionKey: "new:code:repo", composerKey: sourceComposerKey, sessionKey: "A",
    sessionRef: { current: "A" }, draft: stored.draft, completionDraft: stored.completionDraft, attachments: stored.attachments,
    restoredDraft: null, composers: { [sourceComposerKey]: composerA, [otherComposerKey]: { ...composerA, draft: "Keep B" } },
    draftLocationsRef: { current: new Map([[sourceComposerKey, { scope: "sending", key: "new:code:repo" }], [otherComposerKey, { scope: "sending", key: "task-b" }]]) },
    persistedComposersRef: { current: new Map() }, sendingRef: { current: new Set() }, readingAttachmentsRef: { current: new Set() },
    MAX_MESSAGE_CHARS: 32000, busy: false, scrollToBottom: noop, EMPTY_COMPOSER: composerA,
    t: key => key, withAttachments: text => `${text}\n\n# Attached: notes.md\n\`\`\`\n${stored.attachments[0].content}\n\`\`\``,
    props: { track: "cowork", draftScope: "sending", onSend: (...args) => { context.sent = args; return new Promise(resolve => { finish = resolve; }); } },
    setDraftStorageErrors: noop,
    setComposers: update => { context.composers = update(context.composers); },
  };
  for (const [setter, field] of [["setDraft", "draft"], ["setAttachments", "attachments"], ["setCompletionDraft", "completionDraft"], ["setAttachmentError", "attachmentError"], ["setSubmitting", "submitting"]]) {
    context[setter] = update => { const current = context.composers[sourceComposerKey]; context.composers = { ...context.composers, [sourceComposerKey]: { ...current, [field]: typeof update === "function" ? update(current[field]) : update } }; };
  }
  const runDraftEffect = effect => runInNewContext(`(() => {${effect}})()`, context);
  runDraftEffect(persistence);
  const send = runInNewContext(`${sendCode}; send`, context);
  const pending = send();
  assert.equal(drafts.loadChatDraft("sending", "new:code:repo").draft, "Fix this", "A request pending at restart retains its original draft");
  assert.equal(context.sent[2][0].name, "notes.md");
  assert.equal(context.sent[3], "Fix this", "Structured metadata carries the original message separately");
  context.composerKey = otherComposerKey;
  context.draftSessionKey = "task-b";
  context.props.draftTaskKeys = { A: "created-task" };
  context.sessionRef.current = "B";
  denyWrites = true;
  runDraftEffect(migration);
  assert.equal(context.draftLocationsRef.current.get(sourceComposerKey).key, "new:code:repo", "A failed migration retains its original location for retry");
  assert.equal(drafts.loadChatDraft("sending", "new:code:repo").draft, "Fix this");
  denyWrites = false;
  runDraftEffect(migration);
  assert.equal(drafts.loadChatDraft("sending", "new:code:repo"), null);
  assert.equal(drafts.loadChatDraft("sending", "created-task").draft, "Fix this", "A late task creation migrates the original pending draft while another composer is selected");
  finish(false);
  await pending;
  runDraftEffect(persistence);
  assert.equal(drafts.loadChatDraft("sending", "created-task").draft, "Fix this", "A failed late-created request restores its source draft for retry");
  assert.match(render({ sessionKey: "created-task", draftScope: "sending", draftSessionKey: "created-task" }), />Fix this<\/textarea>/, "Opening the task after restart restores the failed source draft without first revisiting its new composer");
  assert.equal(drafts.loadChatDraft("sending", "task-b").draft, "Keep B", "Late source cleanup preserves the selected session's draft");
  context.composers[sourceComposerKey] = composerA;
  context.composerKey = sourceComposerKey;
  context.sessionKey = "A";
  context.draftSessionKey = "created-task";
  const successful = send();
  context.composerKey = otherComposerKey;
  context.draftSessionKey = "task-b";
  finish(true);
  await successful;
  runDraftEffect(persistence);
  assert.equal(drafts.loadChatDraft("sending", "created-task"), null, "Successful send removes the migrated source draft");
  context.composers[sourceComposerKey] = composerA;
  runDraftEffect(persistence);
  drafts.removeChatDraft("sending", "created-task");
  context.props.deletedDraftKeys = ["created-task", "A"];
  context.props.draftTaskKeys = {};
  runDraftEffect(migration);
  assert.equal(context.composers[sourceComposerKey], undefined);
  assert.equal(context.draftLocationsRef.current.has(sourceComposerKey), false);
  assert.equal(context.persistedComposersRef.current.has(sourceComposerKey), false);
  runDraftEffect(persistence);
  assert.equal(drafts.loadChatDraft("sending", "created-task"), null, "A deleted task's in-memory composer cannot restore its removed draft");
  context.composers[sourceComposerKey] = composerA;
  runDraftEffect(migration);
  runDraftEffect(persistence);
  assert.equal(context.composers[sourceComposerKey], undefined, "A late source setter is discarded after deletion");
  assert.equal(drafts.loadChatDraft("sending", "task-b").draft, "Keep B");

  const { selectIterationCheckpoint } = await server.ssrLoadModule("/src/components/ChatIterationDiff.tsx");
  const checkpoint = { id: "current", run_id: "run-a", diff: "+ current", restored: false, pruned: false };
  assert.equal(selectIterationCheckpoint([{ ...checkpoint, run_id: "other" }, checkpoint], "run-a", "current"), checkpoint);
  assert.equal(selectIterationCheckpoint([checkpoint], "run-b", "current"), null);
  const diffSource = await readFile(new URL("../apps/desktop/src/components/ChatIterationDiff.tsx", import.meta.url), "utf8");
  const diffStart = diffSource.indexOf("    setCheckpoint(null);", diffSource.indexOf("useEffect(() =>"));
  const diffEffect = diffSource.slice(diffStart, diffSource.indexOf("  }, [runId", diffStart));
  let deliver;
  const diff = { runId: "run-a", checkpointId: "current", open: true, zh: false, AbortController, selectIterationCheckpoint,
    result: null, error: null, loading: false, setCheckpoint: value => { diff.result = value; }, setError: value => { diff.error = value; }, setLoading: value => { diff.loading = value; },
    listCheckpoints: () => new Promise(resolve => { deliver = resolve; }) };
  const cleanup = runInNewContext(`(() => {${diffEffect}})()`, diff);
  assert.equal(diff.loading, true);
  cleanup();
  deliver({ items: [checkpoint] });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(diff.result, null, "A late diff cannot populate a switched iteration");
  runInNewContext(`(() => {${diffEffect}})()`, diff);
  deliver({ items: [checkpoint] });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(diff.result.id, "current");
  assert.equal(diff.loading, false);
  runInNewContext(`(() => {${diffEffect}})()`, diff);
  deliver({ items: [] });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(diff.error, /checkpoint was not found/);
  console.log("Desktop chat feature checks passed: structured sent attachments, true partial reply presentation, audit deduplication, bounded API-scoped draft recovery/migration, source send cleanup and lazy checkpoint diff isolation.");
} finally {
  delete globalThis.window;
  await server.close();
}
