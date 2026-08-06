import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  appendLedger,
  handleFeedbackMessage,
  handleRatingCallback,
  readLedger,
  refreshLearningSummary,
  resolveAlertPattern,
} from "../index.js";

// #JAIMES: Tests cover rating, reply/new-message capture, skip, isolation, and privacy.
function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "crypto-alert-feedback-"));
  const config = {
    chatId: "-1003589561528",
    threadId: "20",
    pendingPath: path.join(root, "pending.json"),
    ledgerPath: path.join(root, "feedback.jsonl"),
    learningPath: path.join(root, "learning.json"),
    outboxPath: path.join(root, "outbox.jsonl"),
    pendingTtlSeconds: 86400,
    maxReasonChars: 1500,
  };
  fs.writeFileSync(config.outboxPath, `${JSON.stringify({
    record_hash: "0123456789abcdef" + "0".repeat(48),
    exact_identity: { chain: "solana" },
    state: "corroborated-research-watch",
    rule_version: "midlife-continuation-v0.1-prospective",
    warnings: ["social-growth-not-independently-verified", "missing-evidence:holder_growth"],
  })}\n`);
  return { root, config };
}

function callbackContext(payload, calls, overrides = {}) {
  return {
    channel: "telegram",
    accountId: "default",
    threadId: "20",
    senderId: "12345",
    auth: { isAuthorizedSender: true },
    callback: {
      payload,
      messageId: overrides.messageId || "77",
      chatId: "-1003589561528",
    },
    respond: {
      clearButtons: async () => { calls.cleared += 1; },
    },
    ...overrides,
  };
}

function fakeApi(calls) {
  return {
    config: {},
    runtime: { channel: { outbound: { loadAdapter: async () => ({
      sendPayload: async (payload) => { calls.sent.push(payload); return { messageId: "88" }; },
    }) } } },
  };
}

test("rating tap records immediately and replies to the rated alert", async () => {
  const { config } = fixture();
  const calls = { cleared: 0, sent: [] };
  const result = await handleRatingCallback(
    callbackContext("rate:f:0123456789abcdef", calls),
    fakeApi(calls),
    config,
  );
  assert.deepEqual(result, { handled: true });
  assert.equal(calls.cleared, 1);
  assert.equal(calls.sent.length, 1);
  assert.equal(calls.sent[0].replyToId, "77");
  assert.equal(calls.sent[0].payload.text, "Why 🔥?");
  assert.equal(calls.sent[0].payload.channelData.telegram.buttons[0][0].callback_data, "calert:skip:0123456789abcdef");
  const rating = readLedger(config.ledgerPath)[0];
  assert.equal(rating.rating, "fire");
  assert.equal(rating.pattern.chain, "solana");
  assert.equal(rating.pattern.ruleVersion, "midlife-continuation-v0.1-prospective");
  assert.equal(JSON.parse(fs.readFileSync(config.learningPath)).totals.fire, 1);
});

test("next topic message is captured as reason and claimed", async () => {
  const { config } = fixture();
  const calls = { cleared: 0, sent: [] };
  await handleRatingCallback(callbackContext("rate:i:0123456789abcdef", calls), fakeApi(calls), config);
  const result = await handleFeedbackMessage(
    { content: "This was already too extended to be useful." },
    {
      channelId: "telegram",
      conversationId: "telegram:group:-1003589561528:topic:20",
      threadId: "20",
      senderId: "12345",
    },
    config,
  );
  assert.deepEqual(result, { handled: true, reply: { text: "Feedback saved." } });
  const rows = readLedger(config.ledgerPath);
  assert.equal(rows[0].rating, "ice");
  assert.equal(rows[1].reason, "This was already too extended to be useful.");
  assert.equal(rows[1].reasonMode, "next-message");
  assert.equal(JSON.parse(fs.readFileSync(config.pendingPath)).pending.length, 0);
});

test("reply wording selects the matching pending rating", async () => {
  const { config } = fixture();
  const calls = { cleared: 0, sent: [] };
  await handleRatingCallback(callbackContext("rate:f:0123456789abcdef", calls), fakeApi(calls), config);
  const secondKey = "fedcba9876543210";
  await handleRatingCallback(callbackContext(`rate:i:${secondKey}`, calls, { messageId: "78" }), fakeApi(calls), config);
  await handleFeedbackMessage(
    { content: "High-alpha timing." },
    {
      channelId: "telegram",
      conversationId: "telegram:group:-1003589561528:topic:20",
      threadId: "20",
      senderId: "12345",
      replyToBody: "Why 🔥?",
    },
    config,
  );
  const reasons = readLedger(config.ledgerPath).filter((row) => row.kind === "crypto-alert-reason-v0.1");
  assert.equal(reasons[0].alertKey, "0123456789abcdef");
  assert.equal(reasons[0].reasonMode, "reply");
});

test("skip keeps the rating and records declined text feedback", async () => {
  const { config } = fixture();
  const calls = { cleared: 0, sent: [] };
  const api = fakeApi(calls);
  await handleRatingCallback(callbackContext("rate:i:0123456789abcdef", calls), api, config);
  await handleRatingCallback(callbackContext("skip:0123456789abcdef", calls, { messageId: "88" }), api, config);
  const rows = readLedger(config.ledgerPath);
  assert.equal(rows[0].rating, "ice");
  assert.equal(rows[1].reason, null);
  assert.equal(rows[1].reasonMode, "declined");
  assert.equal(JSON.parse(fs.readFileSync(config.pendingPath)).pending.length, 0);
});

test("unrelated topic, sender, command, and oversized text are not consumed", async () => {
  const { config } = fixture();
  const calls = { cleared: 0, sent: [] };
  await handleRatingCallback(callbackContext("rate:f:0123456789abcdef", calls), fakeApi(calls), config);
  const cases = [
    [{ content: "wrong topic" }, { channelId: "telegram", conversationId: "telegram:group:-1003589561528:topic:17", threadId: "17", senderId: "12345" }],
    [{ content: "wrong sender" }, { channelId: "telegram", conversationId: "telegram:group:-1003589561528:topic:20", threadId: "20", senderId: "999" }],
    [{ content: "/new" }, { channelId: "telegram", conversationId: "telegram:group:-1003589561528:topic:20", threadId: "20", senderId: "12345" }],
    [{ content: "x".repeat(1501) }, { channelId: "telegram", conversationId: "telegram:group:-1003589561528:topic:20", threadId: "20", senderId: "12345" }],
  ];
  for (const [event, ctx] of cases) assert.equal(await handleFeedbackMessage(event, ctx, config), undefined);
  assert.equal(readLedger(config.ledgerPath).length, 1);
});

test("hash chain detects tampering", () => {
  const { config } = fixture();
  appendLedger(config.ledgerPath, { kind: "crypto-alert-rating-v0.1", rating: "fire" });
  appendLedger(config.ledgerPath, { kind: "crypto-alert-reason-v0.1", reason: "useful" });
  fs.writeFileSync(config.ledgerPath, fs.readFileSync(config.ledgerPath, "utf8").replace("useful", "changed"));
  assert.throws(() => readLedger(config.ledgerPath), /ledger-tampered/);
});

test("learning summary exposes counts but never reason text or sender identifiers", () => {
  const { config } = fixture();
  appendLedger(config.ledgerPath, {
    kind: "crypto-alert-rating-v0.1",
    rating: "fire",
    senderHash: "private",
    pattern: resolveAlertPattern(config, "0123456789abcdef"),
  });
  appendLedger(config.ledgerPath, {
    kind: "crypto-alert-reason-v0.1",
    reason: "private reasoning",
  });
  const summary = refreshLearningSummary(config);
  const serialized = JSON.stringify(summary);
  assert.equal(summary.totals.fire, 1);
  assert.equal(serialized.includes("private reasoning"), false);
  assert.equal(serialized.includes("senderHash"), false);
});
