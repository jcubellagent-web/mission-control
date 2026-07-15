import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import coordinator, { dispatchClaim, handleInboxEvent, helperArgs, inboxDecision, isJaimesMention, parseTelegramTarget, rememberInboundMessage, reserveClaim } from "../index.js";

const inboxCtx = {
  channelId: "telegram",
  sessionKey: "agent:main:telegram:group:-1003589561528:topic:1",
  runId: "run-1",
  messageId: "42",
};

test("parses the exact Inbox topic target", () => {
  assert.deepEqual(parseTelegramTarget({}, inboxCtx), { chatId: "-1003589561528", threadId: "1" });
});

test("claims untagged Inbox messages and ignores other topics", () => {
  assert.equal(inboxDecision({ channel: "telegram", content: "please review this" }, inboxCtx), "claim");
  assert.equal(inboxDecision(
    { channel: "telegram", content: "please review this" },
    { ...inboxCtx, sessionKey: "agent:main:telegram:group:-1003589561528:topic:17" },
  ), "ignore");
});

test("silences Josh for a direct JAIMES mention but not a routing hashtag", () => {
  assert.equal(isJaimesMention("@JAIMES please take this"), true);
  assert.equal(inboxDecision({ channel: "telegram", content: "@JAIMES please take this" }, inboxCtx), "silence");
  assert.equal(inboxDecision({ channel: "telegram", content: "#jaimes please take this" }, inboxCtx), "claim");
  assert.equal(isJaimesMention("hey,@JAIMES please take this"), true);
});

test("builds helper arguments without prompt content", () => {
  const prompt = "sensitive user request";
  const args = helperArgs({ channel: "telegram", content: prompt }, inboxCtx, { helperPath: "/tmp/helper.py" });
  assert.equal(args.includes(prompt), false);
  assert.deepEqual(args.slice(0, 3), ["/tmp/helper.py", "--claim-inbox", "--run-id"]);
  assert.equal(args[args.indexOf("--session-key") + 1], inboxCtx.sessionKey);
  assert.equal(args[args.indexOf("--message-id") + 1], inboxCtx.messageId);
});

test("claims the runtime-shaped global before_dispatch Inbox event", async () => {
  const event = {
    content: "please review this privately",
    body: "please review this privately",
    channel: "telegram",
    sessionKey: inboxCtx.sessionKey,
    timestamp: 1784086200000,
    isGroup: true,
  };
  const ctx = {
    channelId: "telegram",
    conversationId: inboxCtx.sessionKey,
    sessionKey: inboxCtx.sessionKey,
  };
  rememberInboundMessage({
    content: event.content,
    messageId: "77",
    timestamp: event.timestamp,
  }, ctx);
  let dispatched = false;
  const result = await handleInboxEvent(event, ctx, {}, console, async (receivedEvent, receivedCtx) => {
    dispatched = true;
    assert.equal(receivedEvent.body, event.body);
    assert.equal(receivedCtx.sessionKey, inboxCtx.sessionKey);
    return true;
  });
  assert.deepEqual(result, { handled: true });
  assert.equal(dispatched, true);
  const args = helperArgs(event, ctx, { helperPath: "/tmp/helper.py" });
  assert.equal(args.includes(event.body), false);
  assert.equal(args[args.indexOf("--message-id") + 1], "77");
  assert.equal(args[args.indexOf("--run-id") + 1], "telegram-message:-1003589561528:1:77");
});

test("correlates the freshest same-session message when hook envelopes differ", () => {
  const ctx = { ...inboxCtx, messageId: undefined };
  rememberInboundMessage({ content: "first prompt", messageId: "88", timestamp: 1000 }, ctx);
  const args = helperArgs({ content: "second prompt", timestamp: 1001 }, ctx, { helperPath: "/tmp/helper.py" });
  assert.equal(args[args.indexOf("--message-id") + 1], "88");
});

test("accepts numeric Telegram message ids", () => {
  const ctx = { ...inboxCtx, messageId: undefined };
  rememberInboundMessage({ content: "numeric prompt", messageId: 89, timestamp: 1002 }, ctx);
  const args = helperArgs({ content: "numeric prompt", timestamp: 1002 }, ctx, { helperPath: "/tmp/helper.py" });
  assert.equal(args[args.indexOf("--message-id") + 1], "89");
});

test("silences a JAIMES mention on the global before_dispatch path", async () => {
  const event = { content: "@JAIMES please take this", channel: "telegram", sessionKey: inboxCtx.sessionKey };
  let dispatched = false;
  const result = await handleInboxEvent(event, { channelId: "telegram", sessionKey: inboxCtx.sessionKey }, {}, console, () => {
    dispatched = true;
    return true;
  });
  assert.deepEqual(result, { handled: true });
  assert.equal(dispatched, false);
});

test("silences framework replay markers without creating a task", async () => {
  let dispatched = false;
  const result = await handleInboxEvent(
    { content: "[context compaction] replay", channel: "telegram", sessionKey: inboxCtx.sessionKey },
    { channelId: "telegram", sessionKey: inboxCtx.sessionKey },
    {},
    console,
    () => { dispatched = true; return true; },
  );
  assert.deepEqual(result, { handled: true });
  assert.equal(dispatched, false);
});

function fakeChild(onPrompt = () => {}, { emitSpawn = true } = {}) {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stdin = new EventEmitter();
  child.stdin.end = (prompt) => {
    if (emitSpawn) child.emit("spawn");
    onPrompt(child, prompt);
  };
  child.killed = false;
  child.kill = () => { child.killed = true; };
  return child;
}

function dispatchConfig(spawn) {
  return {
    helperPath: import.meta.filename,
    spawn,
    helperTimeoutMs: 25,
    claimDir: mkdtempSync(join(tmpdir(), "inbox-claims-")),
  };
}

test("returns handled as soon as the durable claimed helper starts", async () => {
  let prompt = "";
  const child = fakeChild((current, stdin) => {
    prompt = stdin;
    current.stdout.emit("data", '{"ok":true,"status":"queued","job_id":"job-123"}');
    current.emit("close", 0);
  });
  const claimed = await dispatchClaim({ content: "private prompt" }, inboxCtx, dispatchConfig(() => child));
  assert.equal(claimed, true);
  assert.equal(prompt, "private prompt");
});

for (const [name, arrange] of [
  ["nonzero exit", () => fakeChild((child) => child.emit("close", 2))],
  ["empty receipt", () => fakeChild((child) => child.emit("close", 0))],
  ["malformed receipt", () => fakeChild((child) => { child.stdout.emit("data", "not-json"); child.emit("close", 0); })],
  ["empty queued job id", () => fakeChild((child) => { child.stdout.emit("data", '{"ok":true,"status":"queued","job_id":""}'); child.emit("close", 0); })],
  ["queue failure receipt", () => fakeChild((child) => { child.stdout.emit("data", '{"ok":false,"status":"queue-failed"}'); child.emit("close", 0); })],
]) {
  test(`keeps Josh ownership after helper start on ${name}`, async () => {
    const child = arrange();
    const config = dispatchConfig(() => child);
    const claimed = await dispatchClaim({ content: "private prompt" }, inboxCtx, config, { error() {} });
    assert.equal(claimed, true);
    if (child?.killed !== undefined) assert.equal(child.killed, false);
  });
}

test("allows Terra handling on spawn error", async () => {
  const child = fakeChild((current) => current.emit("error", new Error("spawn")), { emitSpawn: false });
  const claimed = await dispatchClaim(
    { content: "private prompt" },
    inboxCtx,
    dispatchConfig(() => child),
    { error() {} },
  );
  assert.equal(claimed, false);
  assert.equal(child.killed, true);
});

test("allows Terra handling when spawning throws", async () => {
  const claimed = await dispatchClaim(
    { content: "private prompt" },
    inboxCtx,
    dispatchConfig(() => { throw new Error("spawn"); }),
    { error() {} },
  );
  assert.equal(claimed, false);
});

test("terminates a timed-out helper and allows Terra handling", async () => {
  const child = fakeChild(() => {}, { emitSpawn: false });
  const claimed = await dispatchClaim({ content: "private prompt" }, inboxCtx, dispatchConfig(() => child), { error() {} });
  assert.equal(claimed, false);
  assert.equal(child.killed, true);
});

test("atomically suppresses a second hook for the same Telegram message", async () => {
  let spawns = 0;
  const child = fakeChild((current) => {
    current.stdout.emit("data", '{"ok":true,"status":"queued","job_id":"job-123"}');
    current.emit("close", 0);
  });
  const config = dispatchConfig(() => { spawns += 1; return child; });
  assert.equal(await dispatchClaim({ content: "one prompt" }, inboxCtx, config), true);
  assert.equal(await dispatchClaim({ content: "one prompt" }, inboxCtx, config), true);
  assert.equal(spawns, 1);
});

test("durable claim records never contain prompt content", () => {
  const config = { claimDir: mkdtempSync(join(tmpdir(), "inbox-claims-")) };
  const reservation = reserveClaim({ content: "private prompt body" }, inboxCtx, config);
  assert.equal(reservation.ok, true);
  assert.equal(readFileSync(reservation.path, "utf8").includes("private prompt body"), false);
});

test("registers message correlation plus bound and global claim hooks", () => {
  const hooks = [];
  coordinator.register({ on: (name, handler, options) => hooks.push({ name, handler, options }), pluginConfig: {} });
  assert.deepEqual(hooks.map(({ name }) => name), ["message_received", "inbound_claim", "before_dispatch"]);
  assert.deepEqual(hooks.map(({ options }) => options.priority), [200, 100, 100]);
});
