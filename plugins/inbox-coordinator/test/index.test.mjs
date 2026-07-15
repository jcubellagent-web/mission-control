import test from "node:test";
import assert from "node:assert/strict";
import coordinator, { handleInboxEvent, helperArgs, inboxDecision, isJaimesMention, parseTelegramTarget } from "../index.js";

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
});

test("builds helper arguments without prompt content", () => {
  const prompt = "sensitive user request";
  const args = helperArgs({ channel: "telegram", content: prompt }, inboxCtx, { helperPath: "/tmp/helper.py" });
  assert.equal(args.includes(prompt), false);
  assert.deepEqual(args.slice(0, 3), ["/tmp/helper.py", "--claim-inbox", "--run-id"]);
  assert.equal(args[args.indexOf("--session-key") + 1], inboxCtx.sessionKey);
  assert.equal(args[args.indexOf("--message-id") + 1], inboxCtx.messageId);
});

test("claims the runtime-shaped global before_dispatch Inbox event", () => {
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
  let dispatched = false;
  const result = handleInboxEvent(event, ctx, {}, console, (receivedEvent, receivedCtx) => {
    dispatched = true;
    assert.equal(receivedEvent.body, event.body);
    assert.equal(receivedCtx.sessionKey, inboxCtx.sessionKey);
    return true;
  });
  assert.deepEqual(result, { handled: true });
  assert.equal(dispatched, true);
  const args = helperArgs(event, ctx, { helperPath: "/tmp/helper.py" });
  assert.equal(args.includes(event.body), false);
  assert.equal(args.includes("--message-id"), false);
  assert.equal(args[args.indexOf("--run-id") + 1], `before-dispatch:${event.timestamp}`);
});

test("silences a JAIMES mention on the global before_dispatch path", () => {
  const event = { content: "@JAIMES please take this", channel: "telegram", sessionKey: inboxCtx.sessionKey };
  let dispatched = false;
  const result = handleInboxEvent(event, { channelId: "telegram", sessionKey: inboxCtx.sessionKey }, {}, console, () => {
    dispatched = true;
    return true;
  });
  assert.deepEqual(result, { handled: true });
  assert.equal(dispatched, false);
});

test("registers both bound and global claim hooks", () => {
  const hooks = [];
  coordinator.register({ on: (name, handler, options) => hooks.push({ name, handler, options }), pluginConfig: {} });
  assert.deepEqual(hooks.map(({ name }) => name), ["inbound_claim", "before_dispatch"]);
  assert.deepEqual(hooks.map(({ options }) => options.priority), [100, 100]);
});
