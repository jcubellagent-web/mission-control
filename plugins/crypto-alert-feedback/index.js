import fs from "node:fs";
import path from "node:path";
import { createHash, randomUUID } from "node:crypto";

// #JAIMES: Feedback stays inside the existing gateway consumer and a private,
// hash-chained ledger so it cannot create a competing Telegram poller.
const DEFAULT_CHAT_ID = "-1003589561528";
const DEFAULT_THREAD_ID = "20";
const DEFAULT_PENDING_TTL_SECONDS = 24 * 60 * 60;
const DEFAULT_MAX_REASON_CHARS = 1500;
const ALERT_KEY_RE = /^[0-9a-f]{16}$/;
const RATE_RE = /^rate:([fi]):([0-9a-f]{16})$/;
const SKIP_RE = /^skip:([0-9a-f]{16})$/;
let stateQueue = Promise.resolve();

function text(value, fallback = "") {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return fallback;
}

function integer(value, fallback, minimum, maximum) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(maximum, Math.max(minimum, Math.trunc(parsed))) : fallback;
}

function defaults(config = {}) {
  const home = process.env.HOME || "/Users/jc_agent";
  return {
    chatId: text(config.chatId, DEFAULT_CHAT_ID),
    threadId: text(config.threadId, DEFAULT_THREAD_ID),
    pendingPath: text(config.pendingPath, path.join(home, ".openclaw/private/crypto-alert-feedback-pending.json")),
    ledgerPath: text(config.ledgerPath, path.join(home, ".openclaw/private/crypto-alert-feedback.jsonl")),
    learningPath: text(config.learningPath, path.join(home, "crypto-radar-runtime/data/radar-state/crypto-alert-feedback-learning.json")),
    outboxPath: text(config.outboxPath, path.join(home, "crypto-radar-runtime/data/radar-state/material-research-watch-outbox.jsonl")),
    pendingTtlSeconds: integer(config.pendingTtlSeconds, DEFAULT_PENDING_TTL_SECONDS, 60, 604800),
    maxReasonChars: integer(config.maxReasonChars, DEFAULT_MAX_REASON_CHARS, 1, 4000),
  };
}

function sortValue(value) {
  if (Array.isArray(value)) return value.map(sortValue);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortValue(value[key])]));
}

function canonical(value) {
  return JSON.stringify(sortValue(value));
}

function digest(value) {
  return createHash("sha256").update(canonical(value), "utf8").digest("hex");
}

function senderHash(value) {
  return createHash("sha256").update(`telegram:${value}`, "utf8").digest("hex").slice(0, 24);
}

function ensurePrivateParent(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
}

export function readLedger(filePath) {
  if (!fs.existsSync(filePath)) return [];
  let previousHash = "";
  return fs.readFileSync(filePath, "utf8").split(/\n/).filter(Boolean).map((line) => {
    const row = JSON.parse(line);
    const recordHash = row.recordHash;
    const payload = { ...row };
    delete payload.recordHash;
    if (payload.previousHash !== previousHash || typeof recordHash !== "string" || digest(payload) !== recordHash) {
      throw new Error("crypto-alert-feedback-ledger-tampered");
    }
    previousHash = recordHash;
    return row;
  });
}

export function appendLedger(filePath, record) {
  const rows = readLedger(filePath);
  const payload = {
    ...record,
    previousHash: rows.at(-1)?.recordHash || "",
  };
  const row = { ...payload, recordHash: digest(payload) };
  ensurePrivateParent(filePath);
  const fd = fs.openSync(filePath, "a", 0o600);
  try {
    fs.writeSync(fd, `${canonical(row)}\n`, null, "utf8");
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  fs.chmodSync(filePath, 0o600);
  return row;
}

function readPending(config, now = Date.now()) {
  try {
    const parsed = JSON.parse(fs.readFileSync(config.pendingPath, "utf8"));
    const rows = Array.isArray(parsed.pending) ? parsed.pending : [];
    const minCreatedAt = now - config.pendingTtlSeconds * 1000;
    return rows.filter((row) => Number(row.createdAtMs) >= minCreatedAt);
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
}

function writePending(config, pending) {
  ensurePrivateParent(config.pendingPath);
  const temporary = `${config.pendingPath}.tmp-${process.pid}`;
  fs.writeFileSync(temporary, `${canonical({ version: 1, pending })}\n`, { encoding: "utf8", mode: 0o600 });
  fs.renameSync(temporary, config.pendingPath);
  fs.chmodSync(config.pendingPath, 0o600);
}

function withStateLock(callback) {
  const next = stateQueue.then(callback, callback);
  stateQueue = next.catch(() => undefined);
  return next;
}

function normalizeWarning(value) {
  return text(value).split(":", 1)[0].slice(0, 80);
}

export function resolveAlertPattern(config, alertKey) {
  if (!ALERT_KEY_RE.test(alertKey)) return {};
  try {
    const lines = fs.readFileSync(config.outboxPath, "utf8").split(/\n/).filter(Boolean);
    for (let index = lines.length - 1; index >= 0; index -= 1) {
      const row = JSON.parse(lines[index]);
      if (!text(row.record_hash).startsWith(alertKey)) continue;
      const exact = row.exact_identity || {};
      return {
        chain: text(exact.chain, "unknown").slice(0, 40),
        state: text(row.state, "unknown").slice(0, 80),
        ruleVersion: text(row.rule_version, "unknown").slice(0, 160),
        warningFamilies: Array.isArray(row.warnings)
          ? [...new Set(row.warnings.map(normalizeWarning).filter(Boolean))].slice(0, 8)
          : Array.isArray(row.risk_flags)
            ? [...new Set(row.risk_flags.map(normalizeWarning).filter(Boolean))].slice(0, 8)
            : [],
      };
    }
  } catch {
    return {};
  }
  return {};
}

function patternKey(pattern) {
  return [pattern.chain, pattern.state, pattern.ruleVersion].map((value) => text(value, "unknown")).join("|");
}

export function refreshLearningSummary(config) {
  const ratings = readLedger(config.ledgerPath).filter((row) => row.kind === "crypto-alert-rating-v0.1");
  const patterns = {};
  for (const row of ratings) {
    const key = patternKey(row.pattern || {});
    const bucket = patterns[key] || {
      chain: text(row.pattern?.chain, "unknown"),
      state: text(row.pattern?.state, "unknown"),
      ruleVersion: text(row.pattern?.ruleVersion, "unknown"),
      total: 0,
      fire: 0,
      ice: 0,
    };
    bucket.total += 1;
    bucket[row.rating] += 1;
    bucket.fireRate = Number((bucket.fire / bucket.total).toFixed(3));
    patterns[key] = bucket;
  }
  const summary = {
    version: 1,
    generatedAt: new Date().toISOString(),
    totals: {
      ratings: ratings.length,
      fire: ratings.filter((row) => row.rating === "fire").length,
      ice: ratings.filter((row) => row.rating === "ice").length,
    },
    patterns: Object.values(patterns).sort((left, right) => right.total - left.total),
  };
  ensurePrivateParent(config.learningPath);
  const temporary = `${config.learningPath}.tmp-${process.pid}`;
  fs.writeFileSync(temporary, `${canonical(summary)}\n`, { encoding: "utf8", mode: 0o600 });
  fs.renameSync(temporary, config.learningPath);
  fs.chmodSync(config.learningPath, 0o600);
  return summary;
}

function targetFrom(event = {}, ctx = {}) {
  const conversation = text(ctx.conversationId || event.conversationId || ctx.sessionKey || event.sessionKey);
  const match = conversation.match(/telegram:(?:group:)?(-?\d+):(?:topic:)?(\d+)/i);
  return {
    channelId: text(ctx.channelId || event.channelId || ctx.channel || event.channel).toLowerCase(),
    chatId: text(ctx.chatId || event.chatId || match?.[1]),
    threadId: text(ctx.threadId || event.threadId || match?.[2]),
    senderId: text(ctx.senderId || event.senderId),
    replyToBody: text(ctx.replyToBody || event.replyToBody),
  };
}

function isTarget(config, target) {
  return (!target.channelId || target.channelId === "telegram")
    && target.chatId === config.chatId
    && target.threadId === config.threadId
    && Boolean(target.senderId);
}

function inboundText(event = {}) {
  return text(event.bodyForAgent || event.BodyForAgent || event.content || event.body);
}

function choosePending(pending, target) {
  const candidates = pending
    .filter((row) => row.chatId === target.chatId && row.threadId === target.threadId && row.senderId === target.senderId)
    .sort((left, right) => right.createdAtMs - left.createdAtMs);
  if (!candidates.length) return null;
  if (/^Why [🔥🧊]\?$/u.test(target.replyToBody)) {
    const expectedRating = target.replyToBody.includes("🔥") ? "fire" : "ice";
    return candidates.find((row) => row.rating === expectedRating) || candidates[0];
  }
  return candidates[0];
}

export async function handleRatingCallback(context, api, config) {
  const payload = text(context?.callback?.payload);
  const rate = RATE_RE.exec(payload);
  const skip = SKIP_RE.exec(payload);
  const target = {
    channelId: text(context?.channel).toLowerCase(),
    chatId: text(context?.callback?.chatId),
    threadId: text(context?.threadId),
    senderId: text(context?.senderId),
  };
  if (!isTarget(config, target) || context?.auth?.isAuthorizedSender !== true) return { handled: false };
  if (!rate && !skip) return { handled: false };

  if (rate) {
    const [, shortRating, alertKey] = rate;
    const rating = shortRating === "f" ? "fire" : "ice";
    const emoji = rating === "fire" ? "🔥" : "🧊";
    await withStateLock(async () => {
      const pending = readPending(config).filter((row) => !(row.alertKey === alertKey && row.senderId === target.senderId));
      const hashedSender = senderHash(target.senderId);
      const existing = readLedger(config.ledgerPath).find((row) => row.kind === "crypto-alert-rating-v0.1"
        && row.alertKey === alertKey && row.senderHash === hashedSender);
      const feedbackId = existing?.feedbackId || randomUUID();
      if (!existing) {
        appendLedger(config.ledgerPath, {
          kind: "crypto-alert-rating-v0.1",
          feedbackId,
          recordedAt: new Date().toISOString(),
          alertKey,
          alertMessageId: text(context.callback.messageId),
          senderHash: hashedSender,
          rating,
          pattern: resolveAlertPattern(config, alertKey),
        });
      }
      pending.push({
        feedbackId,
        alertKey,
        alertMessageId: text(context.callback.messageId),
        chatId: target.chatId,
        threadId: target.threadId,
        senderId: target.senderId,
        rating: existing?.rating || rating,
        createdAtMs: Date.now(),
      });
      writePending(config, pending);
      refreshLearningSummary(config);
    });
    await context.respond.clearButtons();
    const outbound = await api.runtime.channel.outbound.loadAdapter("telegram");
    if (!outbound?.sendPayload) throw new Error("crypto-alert-feedback-telegram-outbound-unavailable");
    await outbound.sendPayload({
      cfg: api.config,
      to: target.chatId,
      accountId: context.accountId,
      threadId: target.threadId,
      replyToId: text(context.callback.messageId),
      payload: {
        text: `Why ${emoji}?`,
        channelData: { telegram: { buttons: [[
          { text: "No written feedback", callback_data: `calert:skip:${alertKey}` },
        ]] } },
      },
    });
    return { handled: true };
  }

  const alertKey = skip[1];
  await withStateLock(async () => {
    const pending = readPending(config);
    const selected = pending.find((row) => row.alertKey === alertKey && row.senderId === target.senderId);
    if (!selected) return;
    appendLedger(config.ledgerPath, {
      kind: "crypto-alert-reason-v0.1",
      feedbackId: selected.feedbackId,
      recordedAt: new Date().toISOString(),
      alertKey,
      reason: null,
      reasonMode: "declined",
    });
    writePending(config, pending.filter((row) => row !== selected));
  });
  await context.respond.clearButtons();
  return { handled: true };
}

export async function handleFeedbackMessage(event = {}, ctx = {}, config) {
  const target = targetFrom(event, ctx);
  const reason = inboundText(event);
  if (!isTarget(config, target) || !reason || reason.startsWith("/") || reason.length > config.maxReasonChars) return undefined;
  return await withStateLock(async () => {
    const pending = readPending(config);
    const selected = choosePending(pending, target);
    if (!selected) return undefined;
    appendLedger(config.ledgerPath, {
      kind: "crypto-alert-reason-v0.1",
      feedbackId: selected.feedbackId,
      recordedAt: new Date().toISOString(),
      alertKey: selected.alertKey,
      reason,
      reasonMode: /^Why [🔥🧊]\?$/u.test(target.replyToBody) ? "reply" : "next-message",
    });
    writePending(config, pending.filter((row) => row !== selected));
    return { handled: true, reply: { text: "Feedback saved." } };
  });
}

export default {
  id: "crypto-alert-feedback",
  name: "Crypto Alert Feedback",
  description: "Captures optional private learning feedback for Topic 20 crypto alerts.",
  register(api) {
    const config = defaults(api.pluginConfig || {});
    api.registerInteractiveHandler({
      channel: "telegram",
      namespace: "calert",
      handler: (context) => handleRatingCallback(context, api, config),
    });
    api.on("inbound_claim", (event, ctx) => handleFeedbackMessage(event, ctx, config), {
      priority: 400,
      timeoutMs: 5000,
    });
  },
};
