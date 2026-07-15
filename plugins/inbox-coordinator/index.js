import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";

const DEFAULT_CHAT_ID = "-1003589561528";
const DEFAULT_THREAD_ID = "1";
const DEFAULT_MENTIONS = ["@jaimes", "@jain", "@j.a.i.n"];
const GROUP_TOPIC_RE = /telegram:group:(-?\d+):(?:topic:)?(\d+)/i;
const DEFAULT_HELPER_TIMEOUT_MS = 4_000;
const MAX_RECEIPT_BYTES = 4_096;
const INBOUND_MESSAGE_TTL_MS = 30_000;
const recentInboundMessages = new Map();

function stringValue(value, fallback = "") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function sessionIdentity(event = {}, ctx = {}) {
  return stringValue(ctx.sessionKey || event.sessionKey || ctx.conversationId || event.conversationId);
}

function contentFingerprint(event = {}) {
  const content = String(event.content || event.bodyForAgent || event.body || "");
  return content ? createHash("sha256").update(content, "utf8").digest("hex") : "";
}

export function rememberInboundMessage(event = {}, ctx = {}, config = {}) {
  if (inboxDecision(event, ctx, config) === "ignore") return false;
  const sessionKey = sessionIdentity(event, ctx);
  const messageId = stringValue(ctx.messageId || event.messageId);
  const fingerprint = contentFingerprint(event);
  if (!sessionKey || !messageId || !fingerprint) return false;
  const now = Date.now();
  const current = recentInboundMessages.get(sessionKey) || [];
  const fresh = current.filter((item) => now - item.recordedAt <= INBOUND_MESSAGE_TTL_MS);
  fresh.push({
    messageId,
    fingerprint,
    timestamp: Number(event.timestamp) || 0,
    recordedAt: now,
  });
  recentInboundMessages.set(sessionKey, fresh.slice(-8));
  return true;
}

export function resolveInboundMessageId(event = {}, ctx = {}) {
  const direct = stringValue(ctx.messageId || event.messageId);
  if (direct) return direct;
  const sessionKey = sessionIdentity(event, ctx);
  const fingerprint = contentFingerprint(event);
  if (!sessionKey || !fingerprint) return "";
  const now = Date.now();
  const fresh = (recentInboundMessages.get(sessionKey) || [])
    .filter((item) => now - item.recordedAt <= INBOUND_MESSAGE_TTL_MS && item.fingerprint === fingerprint);
  recentInboundMessages.set(sessionKey, fresh);
  if (!fresh.length) return "";
  const timestamp = Number(event.timestamp) || 0;
  if (timestamp) {
    fresh.sort((left, right) => Math.abs(left.timestamp - timestamp) - Math.abs(right.timestamp - timestamp));
    return fresh[0].messageId;
  }
  return fresh[fresh.length - 1].messageId;
}

export function parseTelegramTarget(event = {}, ctx = {}) {
  const candidates = [ctx.sessionKey, event.conversationId, ctx.conversationId]
    .filter(Boolean)
    .map(String);
  for (const candidate of candidates) {
    const match = GROUP_TOPIC_RE.exec(candidate);
    if (match) return { chatId: match[1], threadId: match[2] };
  }
  return { chatId: "", threadId: "" };
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function isJaimesMention(text, mentions = DEFAULT_MENTIONS) {
  const content = String(text || "");
  return mentions.some((raw) => {
    const mention = String(raw || "").trim();
    if (!mention.startsWith("@")) return false;
    return new RegExp(`(^|\\s)${escapeRegExp(mention)}(?=$|[\\s,.:;!?])`, "i").test(content);
  });
}

export function inboxDecision(event = {}, ctx = {}, config = {}) {
  const channel = String(ctx.channelId || event.channel || "").toLowerCase();
  if (channel !== "telegram") return "ignore";
  const target = parseTelegramTarget(event, ctx);
  const chatId = stringValue(config.chatId, DEFAULT_CHAT_ID);
  const threadId = stringValue(config.threadId, DEFAULT_THREAD_ID);
  if (target.chatId !== chatId || target.threadId !== threadId) return "ignore";
  const mentions = Array.isArray(config.jaimesMentions) && config.jaimesMentions.length
    ? config.jaimesMentions
    : DEFAULT_MENTIONS;
  const content = event.bodyForAgent || event.body || event.content || "";
  return isJaimesMention(content, mentions) ? "silence" : "claim";
}

export function helperArgs(event = {}, ctx = {}, config = {}) {
  const target = parseTelegramTarget(event, ctx);
  // #JAIMES: before_dispatch lacks Telegram messageId, so correlate it with
  // message_received using only a short-lived content hash and session key.
  const messageId = resolveInboundMessageId(event, ctx);
  const runId = stringValue(ctx.runId || event.runId) || (event.timestamp ? `before-dispatch:${event.timestamp}` : "");
  const args = [
    stringValue(config.helperPath, path.join(process.env.HOME || "/Users/josh2.0", ".openclaw", "workspace", "josh_telegram_fast_ack.py")),
    "--claim-inbox",
    "--run-id", runId,
    "--chat-id", target.chatId,
    "--thread-id", target.threadId,
    "--session-key", stringValue(ctx.sessionKey),
  ];
  if (messageId) args.push("--message-id", messageId);
  return args;
}

export async function handleInboxEvent(event = {}, ctx = {}, config = {}, logger = console, dispatch = dispatchClaim) {
  const decision = inboxDecision(event, ctx, config);
  if (decision === "ignore") return undefined;
  if (decision === "silence") return { handled: true };
  return await dispatch(event, ctx, config, logger) ? { handled: true } : undefined;
}

function validReceipt(stdout) {
  try {
    const receipt = JSON.parse(stdout);
    return Boolean(
      receipt
      && receipt.ok === true
      && receipt.status === "queued"
      && typeof receipt.job_id === "string"
      && receipt.job_id.trim(),
    );
  } catch {
    return false;
  }
}

export function dispatchClaim(event = {}, ctx = {}, config = {}, logger = console) {
  const args = helperArgs(event, ctx, config);
  const helper = args[0];
  if (!fs.existsSync(helper)) {
    logger.error?.("inbox-coordinator: helper unavailable; allowing normal OpenCLAW handling");
    return false;
  }
  const pythonPath = stringValue(config.pythonPath, "/opt/homebrew/bin/python3");
  const prompt = String(event.bodyForAgent || event.body || event.content || "");
  const spawnHelper = config.spawn || spawn;
  const timeoutMs = Number.isFinite(config.helperTimeoutMs)
    ? Math.max(1, config.helperTimeoutMs)
    : DEFAULT_HELPER_TIMEOUT_MS;
  return new Promise((resolve) => {
    let child;
    let settled = false;
    let stdout = "";
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(ok);
    };
    const terminate = () => {
      try { child?.kill?.("SIGTERM"); } catch { /* best-effort cleanup */ }
    };
    const fail = () => {
      terminate();
      logger.error?.("inbox-coordinator: helper claim unavailable; allowing normal OpenCLAW handling");
      finish(false);
    };
    const timer = setTimeout(fail, timeoutMs);
    try {
      child = spawnHelper(pythonPath, args, {
        detached: false,
        stdio: ["pipe", "pipe", "ignore"],
        env: process.env,
      });
      child.once?.("error", fail);
      child.stdout?.on("data", (chunk) => {
        stdout += String(chunk);
        if (Buffer.byteLength(stdout, "utf8") > MAX_RECEIPT_BYTES) fail();
      });
      child.once?.("close", (code) => {
        if (code !== 0 || !validReceipt(stdout)) return fail();
        finish(true);
      });
      child.stdin?.once?.("error", fail);
      child.stdin?.end(prompt, "utf8");
    } catch {
      clearTimeout(timer);
      logger.error?.("inbox-coordinator: dispatch failed; allowing normal OpenCLAW handling");
      finish(false);
    }
  });
}

export default {
  id: "inbox-coordinator",
  name: "Inbox Coordinator",
  description: "Owns untagged Josh 2.0 Inbox messages and dispatches one asynchronous worker.",
  register(api) {
    // #JAIMES: before_dispatch is handled only after a bounded helper receipt confirms a queued job.
    api.on("message_received", (event, ctx) => {
      const config = api.pluginConfig || {};
      rememberInboundMessage(event, ctx, config);
    }, { priority: 200 });
    api.on("inbound_claim", async (event, ctx) => {
      const config = event.context?.pluginConfig || api.pluginConfig || {};
      return handleInboxEvent(event, ctx, config, api.logger);
    }, { priority: 100, timeoutMs: 5_000 });
    api.on("before_dispatch", async (event, ctx) => {
      const config = api.pluginConfig || {};
      return handleInboxEvent(event, ctx, config, api.logger);
    }, { priority: 100, timeoutMs: 5_000 });
  },
};
