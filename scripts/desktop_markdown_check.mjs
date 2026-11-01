import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { runInNewContext } from "node:vm";

const require = createRequire(new URL("../apps/desktop/package.json", import.meta.url));
const { createServer } = await import(pathToFileURL(require.resolve("vite")));
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const server = await createServer({ root: fileURLToPath(new URL("../apps/desktop", import.meta.url)),
  configFile: false, server: { middlewareMode: true, hmr: false }, appType: "custom" });
try {
  const { ChatMarkdown } = await server.ssrLoadModule("/src/components/ChatMarkdown.tsx");
  const { PreferencesProvider } = await server.ssrLoadModule("/src/lib/preferences.tsx");
  const render = text => renderToStaticMarkup(React.createElement(PreferencesProvider, null,
    React.createElement(ChatMarkdown, { text })));

  const quote = render("> **Review**\n> Keep the existing tests.\n\n- [x] Fixed\n- [ ] Check again");
  assert.match(quote, /<blockquote>/);
  assert.match(quote, /<strong>Review<\/strong>/);
  assert.match(quote, /type="checkbox"[^>]*disabled=""[^>]*checked=""/);
  assert.equal((quote.match(/type="checkbox"/g) ?? []).length, 2);
  assert.match(quote, /Check again/);
  assert.equal((render(">".repeat(10_000) + "plain reference").match(/<blockquote>/g) ?? []).length, 1,
    "Untrusted deep quotes must not recursively render an unbounded component tree");

  const inline = render("\\*literal\\* and _emphasis_ with `a*b` and **bold**");
  assert.match(inline, /\*literal\*/);
  assert.match(inline, /<em>emphasis<\/em>/);
  assert.match(inline, /<code>a\*b<\/code>/);
  assert.match(inline, /<strong>bold<\/strong>/);
  assert.match(render("3. Three\n4. Four"), /<ol start="3">/);

  const fenced = render("~~~~ts filename=app.ts\nconst message = '<safe>';\n  keepIndent();\n~~~~");
  assert.match(fenced, />ts<\/span>/);
  assert.match(fenced, /aria-label="Copy code"/);
  assert.match(fenced, /<pre[^>]*tabindex="0"/);
  assert.match(fenced, /const message = &#x27;&lt;safe&gt;&#x27;;\n  keepIndent\(\);/);
  assert.match(render("```txt\nunfinished"), /<code>unfinished<\/code>/);

  const table = render("| Expression | Result |\n| :--- | ---: |\n| `a | b` | **OK** |\n| a\\|b | Pending |");
  assert.match(table, /<th[^>]*text-align:left/);
  assert.match(table, /<th[^>]*text-align:right/);
  assert.match(table, /<code>a \| b<\/code>/);
  assert.match(table, />a\|b<\/td>/);
  assert.equal((table.match(/<td/g) ?? []).length, 4, "Pipes in code or escaped text must stay in one cell");
  const unsafe = render("[Safe](https://example.com) [Bad](javascript:alert) [Data](data:text/html,bad) <script>bad()</script>");
  assert.match(unsafe, /href="https:\/\/example.com"/);
  assert.doesNotMatch(unsafe, /href="(?:javascript:|data:)|<script>/);
  assert.match(unsafe, /&lt;script&gt;/);

  const code = (await server.transformRequest("/src/components/ChatMarkdown.tsx", { ssr: true })).code;
  const start = code.indexOf("const copy =");
  const callback = code.slice(start, code.indexOf("return", start));
  const copied = [], states = [];
  const copy = runInNewContext(`${callback}; copy`, {
    code: "a\n  b\n", navigator: { clipboard: { writeText: async text => copied.push(text) } },
    setCopied: state => states.push(state),
  });
  await copy();
  assert.deepEqual(copied, ["a\n  b\n"], "Copy must preserve raw code whitespace");
  assert.deepEqual(states, ["copied"]);
  const failed = [];
  const fail = runInNewContext(`${callback}; copy`, {
    code: "a", navigator: { clipboard: { writeText: async () => { throw new Error("Denied"); } } },
    setCopied: state => failed.push(state),
  });
  await fail();
  assert.deepEqual(failed, ["failed"], "Clipboard failure must produce retry feedback");
  console.log("Desktop Markdown checks passed: quotes, task lists, escaping, emphasis, fenced code, table cells/alignment, safe links and clipboard feedback.");
} finally {
  await server.close();
}
