import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { runInNewContext } from "node:vm";

const require = createRequire(new URL("../apps/desktop/package.json", import.meta.url));
const { createServer } = await import(pathToFileURL(require.resolve("vite")));
const server = await createServer({ root: fileURLToPath(new URL("../apps/desktop", import.meta.url)), configFile: false,
  server: { middlewareMode: true, hmr: false }, appType: "custom" });
try {
  const code = (await server.transformRequest("/src/lib/api.ts", { ssr: true })).code;
  const base = code.slice(code.indexOf("function resolveApiBaseUrl()"), code.indexOf("class ApiRequestError"));
  const errorClass = code.slice(code.indexOf("class ApiRequestError"), code.indexOf("async function request("));
  const requestCode = code.slice(code.indexOf("async function request("), code.indexOf("function getApiHealth("));
  const functions = code.slice(code.indexOf("function createTask("), code.indexOf("function cancelCoworkChat("));
  const desktopName = base.match(/([\w$]+)\.desktopBridge/)[1];
  const requestId = "request-1", encoder = new TextEncoder();
  const attachments = [{ name: "notes.md", content: "你好\n😀" }];
  const setup = response => {
    const calls = [], bridge = { status: { apiBaseUrl: "http://127.0.0.1:18001", backendReady: true } };
    const api = runInNewContext(`${base}; ${errorClass}; ${requestCode}; ${functions}; ({sendCoworkChat, createTask, renameTask, ApiRequestError});`, {
      [desktopName]: { desktopBridge: bridge }, __vite_ssr_import_meta__: { env: {} }, TextDecoder, DOMException,
      fetch: async (url, init) => { calls.push({ url, init }); return typeof response === "function" ? response(url, init) : response; },
    });
    return { ...api, calls, bridge };
  };
  const stream = (chunks, options = {}) => {
    const state = { reads: 0, cancelled: 0, released: 0, acquired: 0 };
    const reader = {
      async read() {
        const index = state.reads++;
        if (options.read) return options.read(index);
        if (index === options.failAt) throw new Error("Reader disconnected");
        return index < chunks.length ? { done: false, value: chunks[index] } : { done: true, value: undefined };
      },
      async cancel() { state.cancelled++; if (options.cancelError) throw new Error("Already cancelled"); },
      releaseLock() { state.released++; },
    };
    return { state, response: { ok: true, headers: { get: () => options.contentType ?? "text/event-stream; charset=utf-8" },
      body: options.noBody ? null : { getReader() { state.acquired++; return reader; } } } };
  };
  const frame = (kind, payload = {}, ending = "\n") => `event: ${kind}${ending}data: ${JSON.stringify({ request_id: requestId, ...payload })}${ending}${ending}`;
  const failureCode = expected => error => error.code === expected;
  const failures = [];
  const check = async (name, action) => {
    try { await action(); console.log(`PASS ${name}`); }
    catch (error) { failures.push(error); console.error(`FAIL ${name}: ${error.message}`); }
  };

  await check("UTF-8 reply and CRLF frames survive every byte boundary", async () => {
    const text = "你好😀\nsecond line";
    const trace = `: keepalive\r\n\r\n${frame("start", {}, "\r\n")}${frame("delta", { text: "你好😀" }, "\r\n")}${frame("delta", { text: "\nsecond line" }, "\r\n")}${frame("done", { reply: text }, "\r\n")}`;
    const bytes = encoder.encode(trace);
    for (let split = 1; split < bytes.length; split++) {
      const fixture = stream([bytes.slice(0, split), bytes.slice(split)]);
      const api = setup(fixture.response), deltas = [];
      const result = await api.sendCoworkChat("task", "Question", requestId, undefined, delta => deltas.push(delta));
      assert.equal(result.reply, text, `Completion must preserve Unicode at byte ${split}`);
      assert.deepEqual(deltas, ["你好😀", "\nsecond line"]);
      assert.equal(fixture.state.cancelled, 1, "A done event cancels unread transport data");
      assert.equal(fixture.state.released, 1);
    }
    const oneByte = stream(Array.from(bytes, byte => Uint8Array.of(byte))), api = setup(oneByte.response), deltas = [];
    assert.equal((await api.sendCoworkChat("task", "Question", requestId, undefined, delta => deltas.push(delta))).reply, text);
    assert.equal(deltas.join(""), text);
    assert.equal(oneByte.state.released, 1);
  });

  await check("Multiple data lines parse and only done confirms success", async () => {
    const multiline = `event: delta\ndata: {"request_id":"${requestId}",\ndata: "text":"hello"}\n\n`;
    const fixture = stream([encoder.encode(`${frame("start")}${multiline}${frame("done", { reply: "hello" })}`)]), api = setup(fixture.response), deltas = [];
    assert.equal((await api.sendCoworkChat("task", "Question", requestId, undefined, text => deltas.push(text))).reply, "hello");
    assert.deepEqual(deltas, ["hello"]);
    for (const trace of [frame("delta", { text: "Partial" }), frame("done", { reply: "No delimiter" }).trimEnd()]) {
      const incomplete = stream([encoder.encode(trace)]), client = setup(incomplete.response);
      await assert.rejects(client.sendCoworkChat("task", "Question", requestId, undefined, () => {}), failureCode("CHAT_STREAM_INCOMPLETE"));
      assert.equal(incomplete.state.released, 1);
    }
  });

  await check("Errors, malformed frames and request identity reject with cleanup", async () => {
    const errorStream = stream([encoder.encode(`${frame("delta", { text: "Partial" })}${frame("error", { code: "MODEL_OUTPUT_TRUNCATED", message: "Model output was incomplete" })}`)]);
    const api = setup(errorStream.response), deltas = [];
    await assert.rejects(api.sendCoworkChat("task", "Question", requestId, undefined, text => deltas.push(text)), error => error instanceof api.ApiRequestError && error.code === "MODEL_OUTPUT_TRUNCATED" && error.message === "Model output was incomplete");
    assert.deepEqual(deltas, ["Partial"]);
    assert.equal(errorStream.state.cancelled, 1);
    assert.equal(errorStream.state.released, 1);
    const invalid = ["event: delta\ndata: {not json}\n\n", frame("delta", { text: 12 }), frame("done", { reply: 12 })];
    for (const kind of ["start", "delta", "done", "error"]) {
      invalid.push(frame(kind, { request_id: "other-request", text: "Wrong", reply: "Wrong", code: "MODEL_ERROR" }));
      invalid.push(`event: ${kind}\ndata: ${JSON.stringify({ text: "No ID", reply: "No ID", code: "MODEL_ERROR" })}\n\n`);
    }
    for (const trace of invalid) {
      const fixture = stream([encoder.encode(trace)]), client = setup(fixture.response), delivered = [];
      await assert.rejects(client.sendCoworkChat("task", "Question", requestId, undefined, text => delivered.push(text)), failureCode("CHAT_STREAM_INVALID"));
      assert.deepEqual(delivered, [], "Invalid or unowned data is never delivered to the UI");
      assert.equal(fixture.state.cancelled, 1);
      assert.equal(fixture.state.released, 1);
    }
  });

  await check("Event size limits cover complete and unfinished frames", async () => {
    for (const trace of [frame("delta", { text: "x".repeat(1_048_577) }), "event: delta\ndata: " + "x".repeat(1_048_577)]) {
      const fixture = stream([encoder.encode(trace)]), api = setup(fixture.response), deltas = [];
      await assert.rejects(api.sendCoworkChat("task", "Question", requestId, undefined, text => deltas.push(text)), failureCode("CHAT_STREAM_INVALID"));
      assert.deepEqual(deltas, []);
      assert.equal(fixture.state.cancelled, 1);
      assert.equal(fixture.state.released, 1);
    }
  });

  await check("Reader errors and cancellation release transport without a reply", async () => {
    const broken = stream([], { failAt: 0, cancelError: true }), client = setup(broken.response);
    await assert.rejects(client.sendCoworkChat("task", "Question", requestId, undefined, () => {}), /Reader disconnected/);
    assert.equal(broken.state.cancelled, 1);
    assert.equal(broken.state.released, 1, "Cancel failure still releases the reader lock");
    const controller = new AbortController();
    const fixture = stream([encoder.encode(`${frame("delta", { text: "First" })}${frame("delta", { text: "Late" })}${frame("done", { reply: "FirstLate" })}`)]);
    const api = setup(fixture.response), deltas = [];
    await assert.rejects(api.sendCoworkChat("task", "Question", requestId, controller.signal, text => { deltas.push(text); controller.abort(); }), error => error.name === "AbortError");
    assert.deepEqual(deltas, ["First"], "Aborted requests stop delivering already buffered late frames");
    assert.equal(api.calls[0].init.signal, controller.signal);
    assert.equal(fixture.state.cancelled, 1);
    assert.equal(fixture.state.released, 1);
    const waitingController = new AbortController();
    let notifyWaiting;
    const readerWaiting = new Promise(resolve => { notifyWaiting = resolve; });
    const waiting = stream([], { read: () => new Promise((_, reject) => {
      waitingController.signal.addEventListener("abort", () => reject(new DOMException("Cancelled", "AbortError")), { once: true });
      notifyWaiting();
    }) });
    const waitingApi = setup(waiting.response);
    const pending = waitingApi.sendCoworkChat("task", "Question", requestId, waitingController.signal, () => {});
    await readerWaiting;
    waitingController.abort();
    await assert.rejects(pending, error => error.name === "AbortError");
    assert.equal(waiting.state.cancelled, 1);
    assert.equal(waiting.state.released, 1);
  });

  await check("HTTP failure, unreadable stream and unavailable backend reject", async () => {
    const badHttp = setup({ ok: false, status: 429, json: async () => ({ code: "WORKSPACE_BUSY", message: "Still working", trace_id: "trace", details: { task_id: "task" } }) });
    await assert.rejects(badHttp.sendCoworkChat("task", "Question", requestId, undefined, () => {}), error => error.code === "WORKSPACE_BUSY" && error.traceId === "trace" && error.details.task_id === "task");
    for (const options of [{ noBody: true }, { contentType: "application/json" }, { contentType: "text/event-stream-invalid" }]) {
      const fixture = stream([], options), api = setup(fixture.response);
      await assert.rejects(api.sendCoworkChat("task", "Question", requestId, undefined, () => {}), failureCode("CHAT_STREAM_INVALID"));
      assert.equal(fixture.state.acquired, 0);
    }
    const valid = stream([encoder.encode(frame("done", { reply: "MIME case" }))], { contentType: "Text/Event-Stream ; charset=UTF-8" });
    assert.equal((await setup(valid.response).sendCoworkChat("task", "Question", requestId, undefined, () => {})).reply, "MIME case");
    const unavailable = setup({});
    unavailable.bridge.status.backendReady = false;
    await assert.rejects(unavailable.sendCoworkChat("task", "Question", requestId, undefined, () => {}), failureCode("BACKEND_UNAVAILABLE"));
    assert.equal(unavailable.calls.length, 0);
  });

  await check("Legacy chat, task attachments and rename retain exact request contracts", async () => {
    const legacy = setup({ ok: true, json: async () => ({ reply: "Legacy reply" }) }), controller = new AbortController();
    assert.equal((await legacy.sendCoworkChat("task", "Full message", requestId, controller.signal, undefined, attachments, "Original question")).reply, "Legacy reply");
    assert.equal(legacy.calls[0].url, "http://127.0.0.1:18001/api/v1/tasks/task/chat");
    assert.equal(legacy.calls[0].init.signal, controller.signal);
    assert.deepEqual(JSON.parse(legacy.calls[0].init.body), { message: "Full message", request_id: requestId, attachments, message_text: "Original question" });
    const fixture = stream([encoder.encode(frame("done", { reply: "Stream reply" }))]), streamed = setup(fixture.response);
    await streamed.sendCoworkChat("task", "Full message", requestId, undefined, () => {}, attachments, "Original question");
    assert.match(streamed.calls[0].url, /\/chat\/stream$/);
    assert.deepEqual(JSON.parse(streamed.calls[0].init.body), JSON.parse(legacy.calls[0].init.body));
    const taskApi = setup({ ok: true, json: async () => ({ id: "task" }) });
    const checks = [{ id: "tests", kind: "pytest", workdir: "." }], criteria = [{ id: "behavior", text: "Works" }];
    await taskApi.createTask("Execution goal", "repository", "standard", "code", checks, criteria, attachments, "Visible question");
    assert.deepEqual(JSON.parse(taskApi.calls[0].init.body), { goal: "Execution goal", repository_id: "repository", mode: "standard", track: "code", validation_checks: checks, completion_criteria: criteria, attachments, message_text: "Visible question", isolated_workspace: true });
    await taskApi.renameTask("task", "Display name");
    assert.equal(taskApi.calls[1].url, "http://127.0.0.1:18001/api/v1/tasks/task/name");
    assert.equal(taskApi.calls[1].init.method, "PUT");
    assert.deepEqual(JSON.parse(taskApi.calls[1].init.body), { name: "Display name" }, "Rename never sends an execution goal or validation mutation");
    taskApi.bridge.status.apiBaseUrl = "http://127.0.0.1:18002";
    await taskApi.renameTask("task", "Next name");
    assert.match(taskApi.calls[2].url, /^http:\/\/127\.0\.0\.1:18002\//);
  });

  if (failures.length) throw new AggregateError(failures, `${failures.length} desktop chat stream checks failed`);
  console.log("Desktop chat stream checks passed: arbitrary UTF-8/CRLF chunking, confirmed completion, errors and request ownership, bounded frames, cancellation/reader cleanup, legacy compatibility and metadata contracts.");
} finally {
  await server.close();
}
