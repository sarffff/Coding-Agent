import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { runInNewContext } from "node:vm";

const require = createRequire(new URL("../apps/desktop/package.json", import.meta.url));
const { createServer } = await import(pathToFileURL(require.resolve("vite")));
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const editing = { sessionId: "chat-a", title: "New title", original: "API repair", saving: false, error: null };
const variants = { all: {}, api: { search: " API " }, folder: { search: "cookbook" }, path: { search: "special" }, empty: { search: "no-such-session" }, edit: { rename: editing }, saving: { rename: { ...editing, saving: true } }, failure: { rename: { ...editing, error: "Could not save the name. Try again." } } };
const server = await createServer({ root: fileURLToPath(new URL("../apps/desktop", import.meta.url)), configFile: false,
  server: { middlewareMode: true, hmr: false }, appType: "custom", plugins: [{
    name: "sidebar-session-fixtures", enforce: "pre",
    transform(source, id) {
      const variant = id.match(/[\\/]Sidebar\.tsx\?sessions-([^&]+)/)?.[1];
      if (!variant) return;
      const fixture = variants[variant];
      assert.ok(fixture, "Only declared sidebar fixtures are loaded");
      let result = source;
      if (fixture.search) result = result.replace('useState("")', `useState(${JSON.stringify(fixture.search)})`);
      if (fixture.rename) result = result.replace('useState<RenameState | null>(null)', `useState<RenameState | null>(${JSON.stringify(fixture.rename)})`);
      return result;
    },
  }] });
try {
  const { PreferencesProvider } = await server.ssrLoadModule("/src/lib/preferences.tsx");
  const panels = {};
  for (const variant of Object.keys(variants)) panels[variant] = (await server.ssrLoadModule(`/src/components/Sidebar.tsx?sessions-${variant}`)).Sidebar;
  const noop = () => {};
  const props = { open: false, activeNav: "chat", track: "cowork", activeSessionId: "chat-a",
    sessions: [{ id: "chat-a", title: "API repair", track: "cowork", status: "running" }, { id: "chat-b", title: "UI review", track: "cowork", status: "done" }],
    groups: [{ id: "repo-a", name: "Cookbook", path: "D:/work/cookbook", rows: [{ id: "code-a", title: "Fix API", track: "code", status: "coding" }, { id: "code-b", title: "Add ingredient", track: "code", status: "done" }] },
      { id: "repo-b", name: "Workspace", path: "D:/special/work", rows: [{ id: "code-c", title: "UI change", track: "code", status: "coding" }] }],
    approvalCount: 0, folderOpening: false, deletionBusy: false, onClose: noop, onTrackChange: noop, onNavigate: noop,
    onNewSession: noop, onOpenFolder: noop, onOpenSettings: noop, onSelectSession: noop, onDeleteSession: noop,
    onOpenRegisteredFolder: noop, onRenameSession: async () => true };
  const render = (variant = "all", overrides = {}, language = "en-US") => {
    globalThis.window = { localStorage: { getItem: key => key === "forge.language" ? language : "light" }, matchMedia: () => ({ matches: false }) };
    return renderToStaticMarkup(React.createElement(PreferencesProvider, null, React.createElement(panels[variant], { ...props, ...overrides })));
  };
  const all = render();
  assert.match(all, /aria-label="Search conversations or folders"/);
  assert.match(all, /aria-label="Rename conversation: API repair"/);
  assert.equal((all.match(/class="chat-session-delete"/g) ?? []).length, 2, "Rename preserves every delete entry");
  assert.doesNotMatch(render("all", { onRenameSession: undefined }), /class="chat-session-rename"/, "Missing rename callback does not leave inactive controls");
  const filtered = render("api");
  assert.match(filtered, />API repair<\/span>/);
  assert.doesNotMatch(filtered, />UI review<\/span>/);
  assert.match(filtered, /aria-label="Clear search"/);
  const code = render("api", { track: "code" });
  assert.match(code, />Cookbook<\/span>/);
  assert.match(code, />Fix API<\/span>/);
  assert.doesNotMatch(code, />Add ingredient<\/span>|>Workspace<\/span>/);
  const folder = render("folder", { track: "code" });
  assert.match(folder, />Fix API<\/span>/);
  assert.match(folder, />Add ingredient<\/span>/);
  assert.doesNotMatch(folder, />Workspace<\/span>/);
  assert.match(render("path", { track: "code" }), />UI change<\/span>/);
  assert.match(render("empty"), /role="status">No matching conversations or folders\./);
  assert.doesNotMatch(render("empty"), /class="chat-session-title"/);
  assert.match(render("edit"), /aria-label="Conversation name"/);
  assert.match(render("edit"), /class="chat-session-delete" disabled=""/);
  assert.match(render("saving"), /role="status">Saving…/);
  assert.match(render("saving"), /disabled="" aria-label="Save name"/);
  assert.match(render("failure"), /aria-invalid="true"/);
  assert.match(render("failure"), /role="alert">Could not save the name\. Try again\./);
  assert.match(render("all", {}, "zh-CN"), /搜索会话或文件夹/);

  const compiled = (await server.transformRequest("/src/components/Sidebar.tsx", { ssr: true })).code;
  const callbacks = compiled.slice(compiled.indexOf("const cancelRename ="), compiled.indexOf("const onTrackKeyDown ="));
  assert.match(compiled, /input:not\(:disabled\)/, "The mobile focus trap includes search and rename inputs");
  const setup = (title = "Changed title", handler = async () => true) => {
    let current = { ...editing, title };
    const renameRef = { current }, renamingRef = { current: new Set() }, calls = [];
    const context = { renameRef, renamingRef, words: { limit: "Name limit", failed: "Save failed" },
      props: { onRenameSession: async (...args) => { calls.push(args); return handler(...args); } },
      setRename: update => { current = typeof update === "function" ? update(current) : update; renameRef.current = current; } };
    return { ...runInNewContext(`${callbacks}; ({saveRename, cancelRename, onRenameKeyDown});`, context), calls, renameRef, renamingRef,
      read: () => current, set: value => { current = value; renameRef.current = value; } };
  };
  for (const title of ["  ", "x".repeat(201), "a\nb", "😀".repeat(201)]) {
    const invalid = setup(title);
    await invalid.saveRename();
    assert.equal(invalid.calls.length, 0, "Invalid names never reach the mutation callback");
    assert.equal(invalid.read().error, "Name limit");
  }
  const unicode = setup(`  ${"😀".repeat(200)}  `);
  await unicode.saveRename();
  assert.equal(unicode.calls[0][1], "😀".repeat(200), "The limit counts Unicode characters and trims before saving");
  assert.equal(unicode.read(), null);
  const unchanged = setup(editing.original);
  await unchanged.saveRename();
  assert.equal(unchanged.calls.length, 0, "An unchanged name requires no request");
  const rejected = setup("Keep draft", async () => false);
  await rejected.saveRename();
  assert.equal(rejected.read().title, "Keep draft");
  assert.equal(rejected.read().saving, false);
  assert.equal(rejected.read().error, "Save failed");
  const thrown = setup("Keep on network error", async () => { throw new Error("Offline"); });
  await thrown.saveRename();
  assert.equal(thrown.read().title, "Keep on network error");
  assert.equal(thrown.renamingRef.current.size, 0);
  let finish;
  const delayed = setup("A title", () => new Promise(resolve => { finish = resolve; }));
  const request = delayed.saveRename();
  await delayed.saveRename();
  delayed.cancelRename();
  assert.equal(delayed.calls.length, 1, "Repeated saves share the pending request");
  assert.equal(delayed.read().saving, true, "A pending mutation cannot be abandoned as completed");
  delayed.set({ ...editing, sessionId: "chat-b", title: "B draft" });
  finish(false);
  await request;
  assert.equal(delayed.read().title, "B draft", "A late result cannot overwrite another conversation's rename draft");
  assert.equal(delayed.read().error, null);
  assert.equal(delayed.renamingRef.current.size, 0);
  const keyboard = setup();
  const keys = key => ({ key, nativeEvent: { isComposing: false }, keyCode: 0, preventDefault() {}, stopPropagation() {} });
  keyboard.onRenameKeyDown({ ...keys("Enter"), nativeEvent: { isComposing: true } });
  assert.equal(keyboard.calls.length, 0, "IME confirmation does not save a partial name");
  keyboard.onRenameKeyDown(keys("Escape"));
  assert.equal(keyboard.read(), null);
  console.log("Desktop sidebar session checks passed: grouped search, folder/path matches, bilingual empty state, rename validation, retry, deduplication, source-session isolation and keyboard handling.");
} finally {
  delete globalThis.window;
  await server.close();
}
