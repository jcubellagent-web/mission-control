import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import coordinator, { dispatchClaim, dispatchJaimesHandoff, effectHasIrreversibleCheckpoint, handleInboxEvent, helperArgs, inboxDecision, isJaimesMention, jaimesHandoffArgs, jaimesHandoffReady, parseTelegramTarget, rememberInboundMessage, reserveClaim, validJaimesHandoffReceipt, validJaimesIndeterminateReceipt, validJoshClaimReceipt } from "../index.js";

const inboxCtx = {
  channelId: "telegram",
  sessionKey: "agent:main:telegram:group:-1003589561528:topic:1",
  runId: "run-1",
  messageId: "42",
};

function joshReceipt(overrides = {}) {
  return JSON.stringify({
    ok: true,
    status: "queued",
    reaction_ok: true,
    card_start_ok: true,
    header_message_id: "101",
    live_message_id: "102",
    job_id: "job-123",
    ...overrides,
  });
}

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

test("classifies a direct JAIMES mention as a health-gated handoff", () => {
  assert.equal(isJaimesMention("@JAIMES please take this"), true);
  assert.equal(inboxDecision({ channel: "telegram", content: "@JAIMES please take this" }, inboxCtx), "handoff");
  assert.equal(inboxDecision({ channel: "telegram", content: "#jaimes please take this" }, inboxCtx), "claim");
  assert.equal(inboxDecision({ channel: "telegram", content: "@JAIN please take this" }, inboxCtx), "claim");
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

test("passes only privacy-safe effect and cancellation sidecars", () => {
  const args = helperArgs({ channel: "telegram", content: "private body" }, inboxCtx, {
    helperPath: "/tmp/helper.py",
    effectPath: "/tmp/claim.effects.json",
    cancelPath: "/tmp/claim.cancel.json",
  });
  assert.equal(args.includes("private body"), false);
  assert.equal(args[args.indexOf("--effect-path") + 1], "/tmp/claim.effects.json");
  assert.equal(args[args.indexOf("--cancel-path") + 1], "/tmp/claim.cancel.json");
});

test("defaults to the canonical checked-in fast-ack helper", () => {
  const args = helperArgs({ channel: "telegram", content: "safe request" }, inboxCtx);
  assert.equal(
    args[0].endsWith("/.openclaw/workspace/mission-control/scripts/josh_telegram_fast_ack.py"),
    true,
  );
  assert.equal(args[0].endsWith("/.openclaw/workspace/josh_telegram_fast_ack.py"), false);
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
    const args = helperArgs(receivedEvent, receivedCtx, { helperPath: "/tmp/helper.py" });
    assert.equal(args.includes(event.body), false);
    assert.equal(args[args.indexOf("--message-id") + 1], "77");
    assert.equal(args[args.indexOf("--run-id") + 1], "telegram-message:-1003589561528:1:77");
    return true;
  });
  assert.deepEqual(result, { handled: true });
  assert.equal(dispatched, true);
});

test("consumes burst correlations one-to-one even when hook bodies differ", async () => {
  const sessionKey = `${inboxCtx.sessionKey}:burst-correlation`;
  const ctx = { channelId: "telegram", sessionKey };
  const expected = Array.from({ length: 12 }, (_, index) => String(700 + index));
  expected.forEach((messageId, index) => {
    rememberInboundMessage({
      content: `original Telegram body ${index}`,
      messageId,
      timestamp: 10_000 + index,
    }, ctx);
  });

  const seen = [];
  const results = await Promise.all(expected.map((_, index) => handleInboxEvent(
    {
      channel: "telegram",
      content: `different dispatch envelope ${index}`,
      timestamp: 10_000 + index,
    },
    ctx,
    {},
    console,
    async (event, boundCtx) => {
      seen.push(boundCtx.messageId);
      return true;
    },
  )));

  assert.deepEqual(results, expected.map(() => ({ handled: true })));
  assert.deepEqual(seen, expected);
});

test("duplicate framework hooks reuse one consumed message without stealing the next burst item", async () => {
  const sessionKey = `${inboxCtx.sessionKey}:duplicate-hook-correlation`;
  const ctx = { channelId: "telegram", sessionKey };
  rememberInboundMessage({ content: "original first", messageId: "801", timestamp: 30_001 }, ctx);
  rememberInboundMessage({ content: "original second", messageId: "802", timestamp: 30_002 }, ctx);
  const seen = [];
  const dispatch = async (_event, boundCtx) => {
    seen.push(boundCtx.messageId);
    return true;
  };

  await handleInboxEvent({ channel: "telegram", content: "first envelope", timestamp: 30_001 }, ctx, {}, console, dispatch, "inbound_claim");
  await handleInboxEvent({ channel: "telegram", content: "first replay body", timestamp: 30_001 }, ctx, {}, console, dispatch, "before_dispatch");
  await handleInboxEvent({ channel: "telegram", content: "second envelope", timestamp: 30_002 }, ctx, {}, console, dispatch, "inbound_claim");

  assert.deepEqual(seen, ["801", "801", "802"]);
});

test("same-timestamp messages correlate once per hook phase in FIFO order", async () => {
  const sessionKey = `${inboxCtx.sessionKey}:same-timestamp`;
  const ctx = { channelId: "telegram", sessionKey };
  rememberInboundMessage({ content: "first original", messageId: "811", timestamp: 40_000 }, ctx);
  rememberInboundMessage({ content: "second original", messageId: "812", timestamp: 40_000 }, ctx);
  const seen = [];
  const dispatch = async (_event, boundCtx) => {
    seen.push(boundCtx.messageId);
    return true;
  };

  await handleInboxEvent({ channel: "telegram", content: "claim one", timestamp: 40_000 }, ctx, {}, console, dispatch, "inbound_claim");
  await handleInboxEvent({ channel: "telegram", content: "claim two", timestamp: 40_000 }, ctx, {}, console, dispatch, "inbound_claim");
  await handleInboxEvent({ channel: "telegram", content: "dispatch one", timestamp: 40_000 }, ctx, {}, console, dispatch, "before_dispatch");
  await handleInboxEvent({ channel: "telegram", content: "dispatch two", timestamp: 40_000 }, ctx, {}, console, dispatch, "before_dispatch");

  assert.deepEqual(seen, ["811", "812", "811", "812"]);
});

test("thirty-two same-timestamp messages survive the cache and stay phase-aware", async () => {
  const sessionKey = `${inboxCtx.sessionKey}:same-timestamp-twelve`;
  const ctx = { channelId: "telegram", sessionKey };
  const ids = Array.from({ length: 32 }, (_, index) => String(830 + index));
  ids.forEach((messageId, index) => rememberInboundMessage({
    channel: "telegram",
    content: `original ${index}`,
    messageId,
    timestamp: 60_000,
  }, ctx));
  const seen = [];
  for (const phase of ["inbound_claim", "before_dispatch"]) {
    for (let index = 0; index < ids.length; index += 1) {
      await handleInboxEvent(
        { channel: "telegram", content: `rewritten ${phase} ${index}`, timestamp: 60_000 },
        ctx,
        {},
        console,
        async (_event, boundCtx) => { seen.push(`${phase}:${boundCtx.messageId}`); return true; },
        phase,
      );
    }
  }
  assert.deepEqual(seen, [
    ...ids.map((id) => `inbound_claim:${id}`),
    ...ids.map((id) => `before_dispatch:${id}`),
  ]);
});

test("a replay marker cannot consume the real message correlation", async () => {
  const sessionKey = `${inboxCtx.sessionKey}:replay-preserves-cache`;
  const ctx = { channelId: "telegram", sessionKey };
  assert.equal(rememberInboundMessage({ channel: "telegram", content: "real request", messageId: "821", timestamp: 50_000 }, ctx), true);
  assert.equal(rememberInboundMessage({ channel: "telegram", content: "[context compaction] replay", messageId: "822", timestamp: 50_001 }, ctx), false);
  assert.deepEqual(await handleInboxEvent(
    { channel: "telegram", content: "[context compaction] replay", timestamp: 50_001 },
    ctx,
    {},
    console,
    async () => { throw new Error("replay must not dispatch"); },
    "before_dispatch",
  ), { handled: true });
  let correlated = "";
  await handleInboxEvent(
    { channel: "telegram", content: "rewritten real envelope", timestamp: 50_000 },
    ctx,
    {},
    console,
    async (_event, boundCtx) => { correlated = boundCtx.messageId; return true; },
    "before_dispatch",
  );
  assert.equal(correlated, "821");
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

test("silences Josh only when the JAIMES Telegram handoff is fresh and healthy", async () => {
  const event = { content: "@JAIMES please take this", channel: "telegram", sessionKey: inboxCtx.sessionKey };
  const directory = mkdtempSync(join(tmpdir(), "jaimes-health-"));
  const healthPath = join(directory, "health.json");
  writeFileSync(healthPath, JSON.stringify({
    status: "ok",
    checkedAt: new Date().toISOString(),
    probe: { gatewayState: "running", telegramState: "connected", fastAckState: "running", telegramSessionPresent: true },
  }));
  let dispatched = false;
  const acceptedAt = new Date().toISOString();
  const config = {
    jaimesHealthPath: healthPath,
    handoffClaimDir: mkdtempSync(join(tmpdir(), "jaimes-handoff-claims-")),
    handoffSpawn: () => fakeHandoffChild(JSON.stringify({
      ok: true,
      status: "accepted",
      agent: "jaimes",
      chat_id: "-1003589561528",
      thread_id: "1",
      inbound_message_id: "42",
      reaction_ok: true,
      header_message_id: "101",
      live_message_id: "102",
      accepted_at: acceptedAt,
    })),
  };
  assert.equal(jaimesHandoffReady(config), true);
  const result = await handleInboxEvent(event, inboxCtx, config, console, () => {
    dispatched = true;
    return true;
  });
  assert.deepEqual(result, { handled: true });
  assert.equal(dispatched, false);
});

test("falls back to Josh when the JAIMES Telegram handoff is stale or unhealthy", async () => {
  const directory = mkdtempSync(join(tmpdir(), "jaimes-health-"));
  const healthPath = join(directory, "health.json");
  writeFileSync(healthPath, JSON.stringify({
    status: "unhealthy",
    checkedAt: new Date().toISOString(),
    probe: { gatewayState: "running", telegramState: "disconnected", fastAckState: "running", telegramSessionPresent: true },
  }));
  assert.equal(jaimesHandoffReady({ jaimesHealthPath: healthPath }), false);
  const result = await handleInboxEvent(
    { content: "@JAIMES please take this", channel: "telegram", sessionKey: inboxCtx.sessionKey },
    { channelId: "telegram", sessionKey: inboxCtx.sessionKey },
    { jaimesHealthPath: healthPath },
    { error() {} },
  );
  assert.equal(result, undefined);
});

test("health alone never silences Josh without an exact JAIMES receipt", async () => {
  const directory = mkdtempSync(join(tmpdir(), "jaimes-health-"));
  const healthPath = join(directory, "health.json");
  writeFileSync(healthPath, JSON.stringify({
    status: "ok",
    checkedAt: new Date().toISOString(),
    probe: { gatewayState: "running", telegramState: "connected", fastAckState: "running", telegramSessionPresent: true },
  }));
  const result = await handleInboxEvent(
    { content: "@JAIMES verify this", channel: "telegram", sessionKey: inboxCtx.sessionKey },
    inboxCtx,
    {
      jaimesHealthPath: healthPath,
      handoffClaimDir: mkdtempSync(join(tmpdir(), "jaimes-handoff-claims-")),
      handoffSpawn: () => fakeHandoffChild('{"ok":true,"status":"accepted"}'),
    },
    { error() {} },
  );
  assert.equal(result, undefined);
});

test("JAIMES handoff command carries only exact origin ids and no prompt", () => {
  const prompt = "@JAIMES private request body";
  const args = jaimesHandoffArgs({ content: prompt }, inboxCtx, {});
  assert.equal(args.includes(prompt), false);
  assert.equal(args[args.indexOf("--chat-id") + 1], "-1003589561528");
  assert.equal(args[args.indexOf("--thread-id") + 1], "1");
  assert.equal(args[args.indexOf("--message-id") + 1], "42");
});

test("exact accepted JAIMES receipt is required", () => {
  const event = { content: "@JAIMES verify this", channel: "telegram" };
  const accepted = {
    ok: true,
    status: "accepted",
    agent: "jaimes",
    chat_id: "-1003589561528",
    thread_id: "1",
    inbound_message_id: "42",
    reaction_ok: true,
    header_message_id: "101",
    live_message_id: "102",
    accepted_at: new Date().toISOString(),
  };
  assert.equal(validJaimesHandoffReceipt(JSON.stringify(accepted), event, inboxCtx), true);
  assert.equal(validJaimesHandoffReceipt(JSON.stringify({ ...accepted, inbound_message_id: "43" }), event, inboxCtx), false);
  assert.equal(validJaimesHandoffReceipt(JSON.stringify({ ...accepted, reaction_ok: false }), event, inboxCtx), false);
  assert.equal(validJaimesHandoffReceipt(JSON.stringify({ ...accepted, header_message_id: "" }), event, inboxCtx), false);
  assert.equal(validJaimesHandoffReceipt(JSON.stringify({ ...accepted, live_message_id: "" }), event, inboxCtx), false);
  assert.equal(validJaimesHandoffReceipt("not-json", event, inboxCtx), false);
});

test("exact fresh JAIMES in-flight ownership receipt is handled without claiming acceptance", () => {
  const event = { content: "@JAIMES verify this", channel: "telegram" };
  const now = new Date();
  const receipt = {
    ok: true,
    handled: true,
    status: "indeterminate",
    ownership_state: "claimed_in_flight",
    agent: "jaimes",
    chat_id: "-1003589561528",
    thread_id: "1",
    inbound_message_id: "42",
    indeterminate_at: now.toISOString(),
    expires_at: new Date(now.getTime() + 60_000).toISOString(),
  };
  assert.equal(validJaimesIndeterminateReceipt(JSON.stringify(receipt), event, inboxCtx), true);
  assert.equal(validJaimesHandoffReceipt(JSON.stringify(receipt), event, inboxCtx), false);
  assert.equal(validJaimesIndeterminateReceipt(JSON.stringify({ ...receipt, inbound_message_id: "43" }), event, inboxCtx), false);
  assert.equal(validJaimesIndeterminateReceipt(JSON.stringify({ ...receipt, expires_at: new Date(0).toISOString() }), event, inboxCtx), false);
});

test("JAIMES in-flight ownership suppresses Josh fallback across duplicate hooks", async () => {
  let spawns = 0;
  const now = new Date();
  const stdout = JSON.stringify({
    ok: true,
    handled: true,
    status: "indeterminate",
    ownership_state: "claimed_in_flight",
    agent: "jaimes",
    chat_id: "-1003589561528",
    thread_id: "1",
    inbound_message_id: "42",
    indeterminate_at: now.toISOString(),
    expires_at: new Date(now.getTime() + 60_000).toISOString(),
  });
  const config = {
    handoffClaimDir: mkdtempSync(join(tmpdir(), "jaimes-handoff-claims-")),
    handoffSpawn: () => { spawns += 1; return fakeHandoffChild(stdout); },
  };
  const event = { content: "@JAIMES verify this", channel: "telegram" };
  assert.equal(await dispatchJaimesHandoff(event, inboxCtx, config, { error() {} }), true);
  assert.equal(await dispatchJaimesHandoff(event, inboxCtx, config, { error() {} }), true);
  assert.equal(spawns, 1);
});

test("missing Telegram message id falls back without spawning JAIMES", async () => {
  let spawned = false;
  const accepted = await dispatchJaimesHandoff(
    { content: "@JAIMES verify this", channel: "telegram" },
    { channelId: "telegram", sessionKey: `${inboxCtx.sessionKey}:missing-id` },
    { handoffSpawn: () => { spawned = true; return fakeHandoffChild(); } },
    { error() {} },
  );
  assert.equal(accepted, false);
  assert.equal(spawned, false);
});

test("handoff timeout is sticky across the duplicate OpenCLAW hook", async () => {
  let spawns = 0;
  const config = {
    handoffClaimDir: mkdtempSync(join(tmpdir(), "jaimes-handoff-claims-")),
    jaimesHandoffTimeoutMs: 25,
    handoffSpawn: () => { spawns += 1; return fakeHandoffChild("", 0, { close: false }); },
  };
  const event = { content: "@JAIMES verify this", channel: "telegram" };
  assert.equal(await dispatchJaimesHandoff(event, inboxCtx, config, { error() {} }), false);
  assert.equal(await dispatchJaimesHandoff(event, inboxCtx, config, { error() {} }), false);
  assert.equal(spawns, 1);
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

function fakeHandoffChild(stdout = "", code = 0, { close = true } = {}) {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.killed = false;
  child.kill = () => { child.killed = true; };
  queueMicrotask(() => {
    child.emit("spawn");
    if (stdout) child.stdout.emit("data", stdout);
    if (close) child.emit("close", code);
  });
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

function primaryClaimPath(directory) {
  const name = readdirSync(directory).find((entry) => entry.endsWith(".json")
    && !entry.endsWith(".effects.json")
    && !entry.endsWith(".cancel.json"));
  return join(directory, name);
}

test("returns handled only after a valid durable queue receipt", async () => {
  let prompt = "";
  const child = fakeChild((current, stdin) => {
    prompt = stdin;
    current.stdout.emit("data", joshReceipt());
    current.emit("close", 0);
  });
  const claimed = await dispatchClaim({ content: "private prompt" }, inboxCtx, dispatchConfig(() => child));
  assert.equal(claimed, true);
  assert.equal(prompt, "private prompt");
});

test("Josh receipt requires reaction, card start, header, live card, and job", () => {
  assert.equal(validJoshClaimReceipt(joshReceipt()), true);
  for (const invalid of [
    { reaction_ok: false },
    { card_start_ok: false },
    { header_message_id: "" },
    { header_message_id: "0" },
    { live_message_id: "" },
    { live_message_id: "nope" },
    { job_id: "" },
    { status: "would-queue" },
    { ok: false },
  ]) {
    assert.equal(validJoshClaimReceipt(joshReceipt(invalid)), false, JSON.stringify(invalid));
  }
});

test("accepts a delayed full receipt inside the bounded helper window", async () => {
  const child = fakeChild((current) => {
    setTimeout(() => {
      current.stdout.emit("data", joshReceipt());
      current.emit("close", 0);
    }, 35);
  });
  const config = {
    ...dispatchConfig(() => child),
    helperTimeoutMs: 100,
  };
  assert.equal(await dispatchClaim({ content: "delayed receipt" }, inboxCtx, config), true);
  assert.equal(child.killed, false);
});

for (const [name, arrange] of [
  ["nonzero exit", () => fakeChild((child) => child.emit("close", 2))],
  ["empty receipt", () => fakeChild((child) => child.emit("close", 0))],
  ["malformed receipt", () => fakeChild((child) => { child.stdout.emit("data", "not-json"); child.emit("close", 0); })],
  ["queue failure receipt", () => fakeChild((child) => { child.stdout.emit("data", '{"ok":false,"status":"queue-failed"}'); child.emit("close", 0); })],
]) {
  test(`allows normal fallback after a post-spawn ${name} without effect evidence`, async () => {
    const child = arrange();
    const config = dispatchConfig(() => child);
    const claimed = await dispatchClaim({ content: "private prompt" }, inboxCtx, config, { error() {} });
    assert.equal(claimed, false);
    const claimPath = join(config.claimDir, readdirSync(config.claimDir)[0]);
    assert.equal(readFileSync(claimPath, "utf8").includes('"status":"failed"'), true);
    if (child?.killed !== undefined) assert.equal(child.killed, false);
  });
}

test("partial visible receipt is sticky and never opens a duplicate fallback", async () => {
  let spawns = 0;
  const child = fakeChild((current) => {
    current.stdout.emit("data", joshReceipt({ job_id: "" }));
    current.emit("close", 0);
  });
  const config = dispatchConfig(() => { spawns += 1; return child; });
  assert.equal(await dispatchClaim({ content: "partial visible receipt" }, inboxCtx, config, { error() {} }), true);
  assert.equal(await dispatchClaim({ content: "partial visible receipt" }, inboxCtx, config, { error() {} }), true);
  assert.equal(spawns, 1);
  const reservation = reserveClaim({ content: "partial visible receipt" }, inboxCtx, config);
  assert.equal(readFileSync(reservation.path, "utf8").includes('"status":"indeterminate"'), true);
});

test("queue failure with durable card receipts remains handled and indeterminate", async () => {
  let spawns = 0;
  const child = fakeChild((current) => {
    current.stdout.emit("data", JSON.stringify({
      ok: false,
      status: "queue-failed",
      reaction_ok: true,
      card_start_ok: true,
      header_message_id: "301",
      live_message_id: "302",
      job_id: "",
    }));
    current.emit("close", 2);
  });
  const config = dispatchConfig(() => { spawns += 1; return child; });
  const event = { content: "visible card but queue failed" };
  assert.equal(await dispatchClaim(event, inboxCtx, config, { error() {} }), true);
  assert.equal(await dispatchClaim(event, inboxCtx, config, { error() {} }), true);
  assert.equal(spawns, 1);
  const claimPath = primaryClaimPath(config.claimDir);
  assert.equal(JSON.parse(readFileSync(claimPath, "utf8")).status, "indeterminate");
});

test("reaction-only surface failure fails open to native handling", async () => {
  const child = fakeChild((current) => {
    current.stdout.emit("data", JSON.stringify({
      ok: false,
      status: "surface-failed",
      reaction_ok: true,
      card_start_ok: false,
      header_message_id: "",
      live_message_id: "",
      job_id: "",
    }));
    current.emit("close", 2);
  });
  const config = dispatchConfig(() => child);
  assert.equal(await dispatchClaim({ content: "eyes only failure" }, inboxCtx, config, { error() {} }), false);
  const claimPath = join(config.claimDir, readdirSync(config.claimDir)[0]);
  assert.equal(JSON.parse(readFileSync(claimPath, "utf8")).status, "failed");
});

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

test("a pre-spawn timeout remains fail-open", async () => {
  const child = fakeChild(() => {}, { emitSpawn: false });
  const claimed = await dispatchClaim({ content: "private prompt" }, inboxCtx, dispatchConfig(() => child), { error() {} });
  assert.equal(claimed, false);
  assert.equal(child.killed, true);
});

test("a post-spawn timeout with no durable surface checkpoint cancels and fails open", async () => {
  let spawns = 0;
  let spawnOptions;
  const child = fakeChild(() => {});
  const config = dispatchConfig((_python, _args, options) => { spawns += 1; spawnOptions = options; return child; });
  assert.equal(await dispatchClaim({ content: "slow no-effect helper" }, inboxCtx, config, { error() {} }), false);
  assert.equal(child.killed, true);
  const claimPath = primaryClaimPath(config.claimDir);
  assert.equal(JSON.parse(readFileSync(claimPath, "utf8")).status, "failed");
  assert.equal(existsSync(`${claimPath}.cancel.json`), true);
  assert.equal(readFileSync(`${claimPath}.cancel.json`, "utf8").includes("slow no-effect helper"), false);
  assert.equal(spawnOptions.detached, true);
  assert.equal(spawns, 1);
});

test("stale protocol lock and definitive failure state never suppress fallback", async () => {
  let effectPath = "";
  const config = {
    ...dispatchConfig((_python, args) => fakeChild(() => {
      effectPath = args[args.indexOf("--effect-path") + 1];
      writeFileSync(effectPath, JSON.stringify({ version: 1, state: "failed-before-surface" }));
      const lockPath = effectPath.replace(/\.effects\.json$/, ".protocol.lock");
      mkdirSync(lockPath);
      const stale = new Date(Date.now() - 10_000);
      utimesSync(lockPath, stale, stale);
    })),
    helperTimeoutMs: 20,
  };
  assert.equal(await dispatchClaim({ content: "recover stale protocol lock" }, inboxCtx, config, { error() {} }), false);
  assert.equal(effectHasIrreversibleCheckpoint(effectPath), false);
});

test("late definitive no-effect result never inherits an intent-only handled decision", async () => {
  let child;
  const config = {
    ...dispatchConfig((_python, args) => {
      const effectPath = args[args.indexOf("--effect-path") + 1];
      child = fakeChild((current) => {
        setTimeout(() => writeFileSync(effectPath, JSON.stringify({ version: 1, state: "surface-started" })), 15);
        setTimeout(() => {
          writeFileSync(effectPath, JSON.stringify({ version: 1, state: "failed-before-surface" }));
          current.stdout.emit("data", JSON.stringify({
            ok: false, status: "surface-failed", reaction_ok: true,
            card_start_ok: false, header_message_id: "", live_message_id: "",
            surface_indeterminate: false,
          }));
          current.emit("close", 2);
        }, 35);
      });
      return child;
    }),
    helperTimeoutMs: 25,
  };
  assert.equal(await dispatchClaim({ content: "late definitive failure" }, inboxCtx, config, { error() {} }), false);
  assert.equal(child.killed, true);
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.equal(JSON.parse(readFileSync(primaryClaimPath(config.claimDir), "utf8")).status, "failed");
});

test("fresh orphan protocol lock is reclaimed after terminating the owned helper", async () => {
  let child;
  let cancelPath = "";
  const config = {
    ...dispatchConfig((_python, args) => {
      const effectPath = args[args.indexOf("--effect-path") + 1];
      cancelPath = args[args.indexOf("--cancel-path") + 1];
      child = fakeChild(() => {
        setTimeout(() => mkdirSync(effectPath.replace(/\.effects\.json$/, ".protocol.lock")), 10);
      });
      return child;
    }),
    helperTimeoutMs: 25,
  };
  assert.equal(await dispatchClaim({ content: "fresh orphan lock" }, inboxCtx, config, { error() {} }), false);
  assert.equal(child.killed, true);
  assert.equal(existsSync(cancelPath), true);
  assert.equal(JSON.parse(readFileSync(primaryClaimPath(config.claimDir), "utf8")).status, "failed");
});

test("a retry cleans stale cancellation sidecars before spawning", async () => {
  let attempts = 0;
  let secondCancelPath = "";
  const config = {
    ...dispatchConfig((_python, args) => {
      attempts += 1;
      const cancelPath = args[args.indexOf("--cancel-path") + 1];
      if (attempts === 1) return fakeChild(() => {});
      secondCancelPath = cancelPath;
      return fakeChild((current) => {
        assert.equal(existsSync(cancelPath), false);
        current.stdout.emit("data", joshReceipt());
        current.emit("close", 0);
      });
    }),
    helperTimeoutMs: 20,
  };
  const event = { content: "retry after clean cancellation" };
  assert.equal(await dispatchClaim(event, inboxCtx, config, { error() {} }), false);
  assert.equal(await dispatchClaim(event, inboxCtx, config, { error() {} }), true);
  assert.equal(attempts, 2);
  assert.equal(existsSync(secondCancelPath), false);
});

test("a timed-out helper with a durable surface checkpoint is handled and allowed to reconcile", async () => {
  let finished = false;
  const config = {
    ...dispatchConfig((_python, args) => fakeChild((current) => {
      const effectPath = args[args.indexOf("--effect-path") + 1];
      writeFileSync(effectPath, JSON.stringify({ version: 1, state: "indeterminate", stage: "task-header-send" }));
      setTimeout(() => {
        finished = true;
        current.stdout.emit("data", joshReceipt());
        current.emit("close", 0);
      }, 45);
    })),
    helperTimeoutMs: 20,
  };
  assert.equal(await dispatchClaim({ content: "bounded late helper" }, inboxCtx, config, { error() {} }), true);
  await new Promise((resolve) => setTimeout(resolve, 55));
  assert.equal(finished, true);
  const claimPath = primaryClaimPath(config.claimDir);
  assert.equal(JSON.parse(readFileSync(claimPath, "utf8")).status, "queued");
});

test("a late receipt after cancellation cannot undo fail-open", async () => {
  const child = fakeChild((current) => {
    setTimeout(() => {
      current.stdout.emit("data", joshReceipt({ job_id: "" }));
      current.emit("close", 0);
    }, 45);
  });
  const config = {
    ...dispatchConfig(() => child),
    helperTimeoutMs: 20,
  };
  assert.equal(await dispatchClaim({ content: "late partial helper" }, inboxCtx, config, { error() {} }), false);
  await new Promise((resolve) => setTimeout(resolve, 55));
  const claimPath = primaryClaimPath(config.claimDir);
  assert.equal(JSON.parse(readFileSync(claimPath, "utf8")).status, "failed");
});

test("a late clean no-effect exit remains failed after cancellation", async () => {
  const child = fakeChild((current) => {
    setTimeout(() => current.emit("close", 2), 45);
  });
  const config = {
    ...dispatchConfig(() => child),
    helperTimeoutMs: 20,
  };
  assert.equal(await dispatchClaim({ content: "late clean helper" }, inboxCtx, config, { error() {} }), false);
  await new Promise((resolve) => setTimeout(resolve, 55));
  const claimPath = primaryClaimPath(config.claimDir);
  assert.equal(JSON.parse(readFileSync(claimPath, "utf8")).status, "failed");
});

test("atomically suppresses a second hook for the same Telegram message", async () => {
  let spawns = 0;
  const child = fakeChild((current) => {
    current.stdout.emit("data", joshReceipt());
    current.emit("close", 0);
  });
  const config = dispatchConfig(() => { spawns += 1; return child; });
  assert.equal(await dispatchClaim({ content: "one prompt" }, inboxCtx, config), true);
  assert.equal(await dispatchClaim({ content: "one prompt" }, inboxCtx, config), true);
  assert.equal(spawns, 1);
});

test("an eight-message burst receives eight distinct durable claims", async () => {
  const sessionKey = `${inboxCtx.sessionKey}:burst-dispatch`;
  const ctx = { channelId: "telegram", sessionKey };
  const ids = Array.from({ length: 8 }, (_, index) => String(900 + index));
  ids.forEach((messageId, index) => rememberInboundMessage({
    content: `remembered burst ${index}`,
    messageId,
    timestamp: 20_000 + index,
  }, ctx));

  let spawns = 0;
  const config = {
    helperPath: import.meta.filename,
    helperTimeoutMs: 100,
    claimDir: mkdtempSync(join(tmpdir(), "inbox-burst-claims-")),
    spawn: () => {
      spawns += 1;
      const jobId = `job-${spawns}`;
      return fakeChild((current) => {
        current.stdout.emit("data", joshReceipt({ job_id: jobId }));
        current.emit("close", 0);
      });
    },
  };

  const results = await Promise.all(ids.map((_, index) => handleInboxEvent(
    { channel: "telegram", content: `rewritten hook ${index}`, timestamp: 20_000 + index },
    ctx,
    config,
    { error() {} },
  )));
  assert.deepEqual(results, ids.map(() => ({ handled: true })));
  assert.equal(spawns, ids.length);
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
