import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";

const DEFAULT_CHAT_ID = "-1003589561528";
const DEFAULT_THREAD_ID = "1";
const DEFAULT_MENTIONS = ["@jaimes", "@jain", "@j.a.i.n"];
const GROUP_TOPIC_RE = /telegram:group:(-?\d+):(?:topic:)?(\d+)/i;
const DEFAULT_HELPER_TIMEOUT_MS = 1_000;
const MAX_RECEIPT_BYTES = 4_096;
const INBOUND_MESSAGE_TTL_MS = 30_000;
const CLAIM_STALE_MS = 2 * 60_000;
const recentInboundMessages = new Map();

function stringValue(value, fallback = "") {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return fallback;
}

function internalReplayPrompt(text) {
  const lowered = String(text || "").trimStart().toLowerCase();
  return [
    "[context compaction",
    "[prior context",
    "[your active task list was preserved",
    "[async delegation",
  ].some((prefix) => lowered.startsWith(prefix));
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
  if (!sessionKey) return "";
  const now = Date.now();
  const fresh = (recentInboundMessages.get(sessionKey) || [])
    .filter((item) => now - item.recordedAt <= INBOUND_MESSAGE_TTL_MS);
  recentInboundMessages.set(sessionKey, fresh);
  if (!fresh.length) return "";
  // OpenCLAW's message_received and before_dispatch bodies are not guaranteed
  // to be byte-identical. Prefer the exact body, then the freshest message in
  // the same short-lived session window.
  const matching = fingerprint ? fresh.filter((item) => item.fingerprint === fingerprint) : [];
  const candidates = matching.length ? matching : fresh;
  const timestamp = Number(event.timestamp) || 0;
  if (timestamp) {
    candidates.sort((left, right) => {
      const distance = Math.abs(left.timestamp - timestamp) - Math.abs(right.timestamp - timestamp);
      return distance || right.recordedAt - left.recordedAt;
    });
    return candidates[0].messageId;
  }
  return candidates[candidates.length - 1].messageId;
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
    return new RegExp(`(^|[\\s,.:;!?()\\[\\]{}])${escapeRegExp(mention)}(?=$|[\\s,.:;!?()\\[\\]{}])`, "i").test(content);
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
  if (internalReplayPrompt(content)) return "silence";
  return isJaimesMention(content, mentions) ? "silence" : "claim";
}

export function helperArgs(event = {}, ctx = {}, config = {}) {
  const target = parseTelegramTarget(event, ctx);
  // #JAIMES: before_dispatch lacks Telegram messageId, so correlate it with
  // message_received using only a short-lived content hash and session key.
  const messageId = resolveInboundMessageId(event, ctx);
  const runId = messageId
    ? `telegram-message:${target.chatId}:${target.threadId}:${messageId}`
    : stringValue(ctx.runId || event.runId) || (event.timestamp ? `before-dispatch:${event.timestamp}` : "");
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

function claimIdentity(event = {}, ctx = {}) {
  const target = parseTelegramTarget(event, ctx);
  const messageId = resolveInboundMessageId(event, ctx);
  const sessionKey = sessionIdentity(event, ctx);
  const runId = stringValue(ctx.runId || event.runId);
  const identity = messageId
    ? `message:${target.chatId}:${target.threadId}:${messageId}`
    : runId
      ? `run:${sessionKey}:${runId}`
      : `event:${sessionKey}:${contentFingerprint(event)}:${Number(event.timestamp) || 0}`;
  return createHash("sha256").update(identity, "utf8").digest("hex");
}

function claimDirectory(config = {}) {
  return stringValue(
    config.claimDir,
    path.join(process.env.HOME || "/Users/josh2.0", ".openclaw", "telegram", "inbox-claims"),
  );
}

function readClaim(claimPath) {
  try {
    return JSON.parse(fs.readFileSync(claimPath, "utf8"));
  } catch {
    return {};
  }
}

function updateClaim(claimPath, values = {}) {
  try {
    const current = readClaim(claimPath);
    const next = { ...current, ...values, updatedAt: new Date().toISOString() };
    const temp = `${claimPath}.${process.pid}.${Date.now()}.tmp`;
    fs.writeFileSync(temp, `${JSON.stringify(next)}\n`, { mode: 0o600 });
    fs.renameSync(temp, claimPath);
  } catch {
    // The durable reservation already exists. Status enrichment is best effort.
  }
}

export function reserveClaim(event = {}, ctx = {}, config = {}) {
  const directory = claimDirectory(config);
  try {
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  } catch {
    return { ok: false, existing: false, key: "", path: "" };
  }
  const key = claimIdentity(event, ctx);
  const claimPath = path.join(directory, `${key}.json`);
  const now = Date.now();
  if (fs.existsSync(claimPath)) {
    const existing = readClaim(claimPath);
    const age = now - Date.parse(existing.updatedAt || existing.createdAt || 0);
    const status = stringValue(existing.status);
    if (status !== "failed" && (!Number.isFinite(age) || age <= CLAIM_STALE_MS || status === "queued")) {
      return { ok: true, existing: true, key, path: claimPath };
    }
    try { fs.unlinkSync(claimPath); } catch { /* another hook may own it */ }
  }
  try {
    const target = parseTelegramTarget(event, ctx);
    const record = {
      key,
      status: "reserved",
      chatId: target.chatId,
      threadId: target.threadId,
      messageId: resolveInboundMessageId(event, ctx),
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    fs.writeFileSync(claimPath, `${JSON.stringify(record)}\n`, { flag: "wx", mode: 0o600 });
    return { ok: true, existing: false, key, path: claimPath };
  } catch (error) {
    if (error?.code === "EEXIST") return { ok: true, existing: true, key, path: claimPath };
    return { ok: false, existing: false, key, path: claimPath };
  }
}

export function dispatchClaim(event = {}, ctx = {}, config = {}, logger = console) {
  const reservation = reserveClaim(event, ctx, config);
  if (!reservation.ok) {
    logger.error?.("inbox-coordinator: could not reserve the Inbox claim; allowing normal OpenCLAW handling");
    return false;
  }
  if (reservation.existing) return true;
  const args = helperArgs(event, ctx, config);
  const helper = args[0];
  if (!fs.existsSync(helper)) {
    try { fs.unlinkSync(reservation.path); } catch { /* best effort */ }
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
    let spawned = false;
    let stdout = "";
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(ok);
    };
    const failBeforeSpawn = () => {
      if (spawned || settled) return;
      try { child?.kill?.("SIGTERM"); } catch { /* best-effort cleanup */ }
      try { fs.unlinkSync(reservation.path); } catch { /* best effort */ }
      logger.error?.("inbox-coordinator: helper process did not start; allowing normal OpenCLAW handling");
      finish(false);
    };
    const timer = setTimeout(failBeforeSpawn, timeoutMs);
    try {
      child = spawnHelper(pythonPath, args, {
        detached: false,
        stdio: ["pipe", "pipe", "ignore"],
        env: process.env,
      });
      child.once?.("spawn", () => {
        spawned = true;
        updateClaim(reservation.path, { status: "started" });
        finish(true);
      });
      child.once?.("error", () => {
        if (!spawned) return failBeforeSpawn();
        updateClaim(reservation.path, { status: "failed", error: "helper_process_error" });
        logger.error?.("inbox-coordinator: claimed helper process failed after start");
      });
      child.stdout?.on("data", (chunk) => {
        stdout += String(chunk);
        if (Buffer.byteLength(stdout, "utf8") > MAX_RECEIPT_BYTES) {
          stdout = stdout.slice(-MAX_RECEIPT_BYTES);
        }
      });
      child.once?.("close", (code) => {
        const queued = code === 0 && validReceipt(stdout);
        updateClaim(reservation.path, {
          status: queued ? "queued" : "failed",
          error: queued ? "" : `helper_exit_${code ?? "unknown"}`,
        });
        if (!spawned && !queued) failBeforeSpawn();
      });
      child.stdin?.once?.("error", () => {
        if (!spawned) return failBeforeSpawn();
        updateClaim(reservation.path, { status: "failed", error: "helper_stdin_error" });
      });
      child.stdin?.end(prompt, "utf8");
    } catch {
      clearTimeout(timer);
      try { fs.unlinkSync(reservation.path); } catch { /* best effort */ }
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
    // Reserve one durable owner before either hook can dispatch. The hook then
    // returns as soon as the claimed helper process starts; its queued/failure
    // receipt is recorded asynchronously and never releases a second model.
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
