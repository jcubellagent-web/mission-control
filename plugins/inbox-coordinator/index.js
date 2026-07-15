import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";

const DEFAULT_CHAT_ID = "-1003589561528";
const DEFAULT_THREAD_ID = "1";
const DEFAULT_MENTIONS = ["@jaimes"];
const GROUP_TOPIC_RE = /telegram:group:(-?\d+):(?:topic:)?(\d+)/i;
// Header + live-card setup has an 8-second SLO. Keep the helper receipt wait
// inside OpenCLAW's 15-second hook budget without timing out a healthy card
// setup at the old 3-second boundary.
const DEFAULT_HELPER_TIMEOUT_MS = 12_000;
const SURFACE_RESOLUTION_BUDGET_MS = 7_500;
const DEFAULT_HANDOFF_TIMEOUT_MS = 14_000;
const DEFAULT_HANDOFF_WAIT_SECONDS = 9;
const DEFAULT_JAIMES_SSH_TARGET = "jc_agent@100.121.89.84";
const DEFAULT_JAIMES_PYTHON = "/Users/jc_agent/.local/bin/python3.11";
const DEFAULT_JAIMES_HELPER = "/Users/jc_agent/.openclaw/workspace/mission-control/scripts/jaimes_telegram_fast_ack.py";
const MAX_RECEIPT_BYTES = 4_096;
const INBOUND_MESSAGE_TTL_MS = 30_000;
const MAX_INBOUND_CORRELATIONS = 64;
const CLAIM_STALE_MS = 2 * 60_000;
const PROTOCOL_LOCK_WAIT_MS = 250;
const PROTOCOL_LOCK_RETRY_MS = 5;
const PROTOCOL_LOCK_STALE_MS = 2_000;
const DEFAULT_JAIMES_HEALTH_MAX_AGE_MS = 10 * 60_000;
const recentInboundMessages = new Map();
const consumedInboundMessages = new Map();

function stringValue(value, fallback = "") {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return fallback;
}

function appendReceiptChunk(current, chunk) {
  const next = current + String(chunk);
  return Buffer.byteLength(next, "utf8") > MAX_RECEIPT_BYTES
    ? next.slice(-MAX_RECEIPT_BYTES)
    : next;
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
  if (["ignore", "silence"].includes(inboxDecision(event, ctx, config))) return false;
  const sessionKey = sessionIdentity(event, ctx);
  const messageId = stringValue(ctx.messageId || event.messageId);
  const fingerprint = contentFingerprint(event);
  if (!sessionKey || !messageId || !fingerprint) return false;
  const now = Date.now();
  const current = recentInboundMessages.get(sessionKey) || [];
  const fresh = current.filter(
    (item) => now - item.recordedAt <= INBOUND_MESSAGE_TTL_MS && item.messageId !== messageId,
  );
  fresh.push({
    messageId,
    fingerprint,
    timestamp: Number(event.timestamp) || 0,
    recordedAt: now,
  });
  recentInboundMessages.set(sessionKey, fresh.slice(-MAX_INBOUND_CORRELATIONS));
  const consumed = (consumedInboundMessages.get(sessionKey) || [])
    .filter((item) => now - item.consumedAt <= INBOUND_MESSAGE_TTL_MS && item.messageId !== messageId);
  consumedInboundMessages.set(sessionKey, consumed.slice(-MAX_INBOUND_CORRELATIONS));
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
  // to be byte-identical. Prefer the exact body, then the nearest timestamp.
  // A caller that needs ownership must use consumeInboundMessageId below so a
  // burst cannot bind two dispatches to the same remembered Telegram message.
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

function consumeInboundMessageId(event = {}, ctx = {}, hookPhase = "") {
  const direct = stringValue(ctx.messageId || event.messageId);
  const sessionKey = sessionIdentity(event, ctx);
  if (!sessionKey) return direct;
  const phase = stringValue(hookPhase, "default");

  const now = Date.now();
  const fresh = (recentInboundMessages.get(sessionKey) || [])
    .filter((item) => now - item.recordedAt <= INBOUND_MESSAGE_TTL_MS);
  recentInboundMessages.set(sessionKey, fresh);
  const consumed = (consumedInboundMessages.get(sessionKey) || [])
    .filter((item) => now - item.consumedAt <= INBOUND_MESSAGE_TTL_MS);
  consumedInboundMessages.set(sessionKey, consumed);

  const rememberConsumption = (item) => {
    const previous = consumed.find((existing) => existing.messageId === item.messageId) || {};
    const phases = new Set(Array.isArray(previous.phases) ? previous.phases : []);
    phases.add(phase);
    const next = consumed.filter((existing) => existing.messageId !== item.messageId);
    next.push({ ...previous, ...item, phases: [...phases], consumedAt: now });
    consumedInboundMessages.set(sessionKey, next.slice(-MAX_INBOUND_CORRELATIONS));
  };

  let selected;
  if (direct) {
    selected = fresh.find((item) => item.messageId === direct);
    if (!selected && !consumed.some((item) => item.messageId === direct)) {
      rememberConsumption({
        messageId: direct,
        fingerprint: contentFingerprint(event),
        timestamp: Number(event.timestamp) || 0,
        recordedAt: now,
      });
    }
  } else {
    if (!fresh.length) {
      recentInboundMessages.set(sessionKey, []);
      return "";
    }
    const phaseConsumed = new Set(consumed
      .filter((item) => Array.isArray(item.phases) && item.phases.includes(phase))
      .map((item) => item.messageId));
    // Correlate each production hook phase with its own FIFO cursor. Telegram
    // can assign the same millisecond timestamp to distinct burst messages,
    // so timestamp equality is never a replay identity.
    selected = fresh.find((item) => !phaseConsumed.has(item.messageId));
  }

  if (selected) {
    recentInboundMessages.set(sessionKey, fresh);
    rememberConsumption(selected);
    return selected.messageId;
  }
  recentInboundMessages.set(sessionKey, fresh);
  return direct;
}

function bindInboundMessage(event = {}, ctx = {}, hookPhase = "") {
  const messageId = consumeInboundMessageId(event, ctx, hookPhase);
  return messageId && !stringValue(ctx.messageId)
    ? { ...ctx, messageId }
    : ctx;
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
  return isJaimesMention(content, mentions) ? "handoff" : "claim";
}

export function helperArgs(event = {}, ctx = {}, config = {}) {
  const target = parseTelegramTarget(event, ctx);
  const messageId = resolveInboundMessageId(event, ctx);
  const runId = messageId
    ? `telegram-message:${target.chatId}:${target.threadId}:${messageId}`
    : stringValue(ctx.runId || event.runId) || (event.timestamp ? `before-dispatch:${event.timestamp}` : "");
  const args = [
    stringValue(config.helperPath, path.join(
      process.env.HOME || "/Users/josh2.0",
      ".openclaw", "workspace", "mission-control", "scripts", "josh_telegram_fast_ack.py",
    )),
    "--claim-inbox",
    "--run-id", runId,
    "--chat-id", target.chatId,
    "--thread-id", target.threadId,
    "--session-key", stringValue(ctx.sessionKey),
  ];
  if (messageId) args.push("--message-id", messageId);
  const effectPath = stringValue(config.effectPath);
  const cancelPath = stringValue(config.cancelPath);
  if (effectPath && cancelPath) {
    args.push("--effect-path", effectPath, "--cancel-path", cancelPath);
  }
  const surfaceDeadlineMs = Number(config.surfaceDeadlineMs);
  if (Number.isFinite(surfaceDeadlineMs) && surfaceDeadlineMs > 0) {
    args.push("--surface-deadline-ms", String(Math.floor(surfaceDeadlineMs)));
  }
  return args;
}

export async function handleInboxEvent(event = {}, ctx = {}, config = {}, logger = console, dispatch = dispatchClaim, hookPhase = "") {
  const decision = inboxDecision(event, ctx, config);
  if (decision === "ignore") return undefined;
  if (decision === "silence") return { handled: true };
  const boundCtx = bindInboundMessage(event, ctx, hookPhase);
  if (decision === "handoff") {
    if (jaimesHandoffReady(config) && await dispatchJaimesHandoff(event, boundCtx, config, logger)) {
      return { handled: true };
    }
    logger.error?.("inbox-coordinator: JAIMES did not confirm exact-message acceptance; allowing Josh 2.0 fallback handling");
    return undefined;
  }
  return await dispatch(event, boundCtx, config, logger) ? { handled: true } : undefined;
}

export function jaimesHandoffReady(config = {}) {
  const healthPath = stringValue(
    config.jaimesHealthPath,
    path.join(process.env.HOME || "/Users/josh2.0", "agent-loops", "state", "jaimes-telegram-health.json"),
  );
  try {
    const health = JSON.parse(fs.readFileSync(healthPath, "utf8"));
    const checkedAt = Date.parse(health.checkedAt || health.updatedAt || "");
    const maxAgeMs = Number.isFinite(config.jaimesHealthMaxAgeMs)
      ? Math.max(1, config.jaimesHealthMaxAgeMs)
      : DEFAULT_JAIMES_HEALTH_MAX_AGE_MS;
    const probe = health.probe || {};
    return health.status === "ok"
      && Number.isFinite(checkedAt)
      && Date.now() - checkedAt <= maxAgeMs
      && probe.gatewayState === "running"
      && probe.telegramState === "connected"
      && probe.fastAckState === "running"
      && probe.telegramSessionPresent === true;
  } catch {
    return false;
  }
}

function positiveTelegramId(value) {
  const text = stringValue(value);
  return /^\d+$/.test(text) && Number(text) > 0 ? text : "";
}

export function jaimesHandoffArgs(event = {}, ctx = {}, config = {}) {
  const target = parseTelegramTarget(event, ctx);
  const messageId = positiveTelegramId(resolveInboundMessageId(event, ctx));
  if (!/^-?\d+$/.test(target.chatId) || !positiveTelegramId(target.threadId) || !messageId) return [];
  const waitSeconds = Number.isFinite(config.jaimesHandoffWaitSeconds)
    ? Math.min(10, Math.max(1, config.jaimesHandoffWaitSeconds))
    : DEFAULT_HANDOFF_WAIT_SECONDS;
  return [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=3",
    "-o", "ServerAliveInterval=3",
    "-o", "ServerAliveCountMax=1",
    stringValue(config.jaimesSshTarget, DEFAULT_JAIMES_SSH_TARGET),
    stringValue(config.jaimesPythonPath, DEFAULT_JAIMES_PYTHON),
    stringValue(config.jaimesHelperPath, DEFAULT_JAIMES_HELPER),
    "--await-handoff",
    "--chat-id", target.chatId,
    "--thread-id", target.threadId,
    "--message-id", messageId,
    "--timeout", String(waitSeconds),
  ];
}

export function validJaimesHandoffReceipt(stdout, event = {}, ctx = {}, maxAgeMs = 120_000) {
  try {
    const receipt = JSON.parse(stdout);
    const target = parseTelegramTarget(event, ctx);
    const messageId = positiveTelegramId(resolveInboundMessageId(event, ctx));
    const acceptedAt = Date.parse(receipt.accepted_at || "");
    return Boolean(
      receipt
      && receipt.ok === true
      && receipt.status === "accepted"
      && receipt.agent === "jaimes"
      && receipt.chat_id === target.chatId
      && receipt.thread_id === target.threadId
      && receipt.inbound_message_id === messageId
      && receipt.reaction_ok === true
      && positiveTelegramId(receipt.header_message_id)
      && positiveTelegramId(receipt.live_message_id)
      && Number.isFinite(acceptedAt)
      && Math.abs(Date.now() - acceptedAt) <= maxAgeMs,
    );
  } catch {
    return false;
  }
}

export function validJaimesIndeterminateReceipt(stdout, event = {}, ctx = {}, maxAgeMs = 120_000) {
  try {
    const receipt = JSON.parse(stdout);
    const target = parseTelegramTarget(event, ctx);
    const messageId = positiveTelegramId(resolveInboundMessageId(event, ctx));
    const indeterminateAt = Date.parse(receipt.indeterminate_at || "");
    const expiresAt = Date.parse(receipt.expires_at || "");
    return Boolean(
      receipt
      && receipt.ok === true
      && receipt.handled === true
      && receipt.status === "indeterminate"
      && receipt.ownership_state === "claimed_in_flight"
      && receipt.agent === "jaimes"
      && receipt.chat_id === target.chatId
      && receipt.thread_id === target.threadId
      && receipt.inbound_message_id === messageId
      && Number.isFinite(indeterminateAt)
      && Math.abs(Date.now() - indeterminateAt) <= maxAgeMs
      && Number.isFinite(expiresAt)
      && expiresAt > Date.now(),
    );
  } catch {
    return false;
  }
}

function handoffClaimDirectory(config = {}) {
  return stringValue(
    config.handoffClaimDir,
    path.join(process.env.HOME || "/Users/josh2.0", ".openclaw", "telegram", "inbox-handoff-claims"),
  );
}

function reserveHandoffClaim(event = {}, ctx = {}, config = {}) {
  const directory = handoffClaimDirectory(config);
  try {
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  } catch {
    return { ok: false, existing: false, path: "" };
  }
  const key = claimIdentity(event, ctx);
  const claimPath = path.join(directory, `${key}.json`);
  const now = Date.now();
  if (fs.existsSync(claimPath)) {
    const existing = readClaim(claimPath);
    const age = now - Date.parse(existing.updatedAt || existing.createdAt || 0);
    if (!Number.isFinite(age) || age <= CLAIM_STALE_MS) {
      return { ok: true, existing: true, path: claimPath };
    }
    try { fs.unlinkSync(claimPath); } catch { /* another hook may own it */ }
  }
  try {
    const target = parseTelegramTarget(event, ctx);
    fs.writeFileSync(claimPath, `${JSON.stringify({
      key,
      status: "reserved",
      chatId: target.chatId,
      threadId: target.threadId,
      messageId: positiveTelegramId(resolveInboundMessageId(event, ctx)),
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    })}\n`, { flag: "wx", mode: 0o600 });
    return { ok: true, existing: false, path: claimPath };
  } catch (error) {
    if (error?.code === "EEXIST") return { ok: true, existing: true, path: claimPath };
    return { ok: false, existing: false, path: claimPath };
  }
}

export function dispatchJaimesHandoff(event = {}, ctx = {}, config = {}, logger = console) {
  const args = jaimesHandoffArgs(event, ctx, config);
  if (!args.length) {
    logger.error?.("inbox-coordinator: exact JAIMES message origin unavailable; allowing Josh 2.0 fallback handling");
    return false;
  }
  const reservation = reserveHandoffClaim(event, ctx, config);
  if (!reservation.ok) return false;
  const timeoutMs = Number.isFinite(config.jaimesHandoffTimeoutMs)
    ? Math.max(100, config.jaimesHandoffTimeoutMs)
    : DEFAULT_HANDOFF_TIMEOUT_MS;
  if (reservation.existing) {
    const existing = readClaim(reservation.path);
    if (existing.status === "accepted" || existing.status === "indeterminate") return true;
    if (existing.status === "failed") return false;
    return new Promise((resolve) => {
      const deadline = Date.now() + timeoutMs;
      const poll = () => {
        const current = readClaim(reservation.path);
        if (current.status === "accepted" || current.status === "indeterminate") return resolve(true);
        if (current.status === "failed" || Date.now() >= deadline) return resolve(false);
        setTimeout(poll, 20);
      };
      poll();
    });
  }

  const sshPath = stringValue(config.sshPath, "/usr/bin/ssh");
  const spawnHelper = config.handoffSpawn || spawn;
  return new Promise((resolve) => {
    let child;
    let settled = false;
    let stdout = "";
    const finish = (handled, error = "", status = handled ? "accepted" : "failed") => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      updateClaim(reservation.path, { status, error });
      resolve(handled);
    };
    const timer = setTimeout(() => {
      try { child?.kill?.("SIGTERM"); } catch { /* best effort */ }
      finish(false, "jaimes_handoff_timeout");
    }, timeoutMs);
    try {
      child = spawnHelper(sshPath, args, {
        detached: false,
        stdio: ["ignore", "pipe", "ignore"],
        env: process.env,
      });
      child.once?.("spawn", () => updateClaim(reservation.path, { status: "started" }));
      child.once?.("error", () => finish(false, "jaimes_handoff_spawn_error"));
      child.stdout?.on("data", (chunk) => {
        stdout = appendReceiptChunk(stdout, chunk);
      });
      child.once?.("close", (code) => {
        const accepted = code === 0 && validJaimesHandoffReceipt(stdout, event, ctx);
        const indeterminate = code === 0 && validJaimesIndeterminateReceipt(stdout, event, ctx);
        const handled = accepted || indeterminate;
        finish(
          handled,
          handled ? "" : `jaimes_handoff_exit_${code ?? "unknown"}`,
          accepted ? "accepted" : indeterminate ? "indeterminate" : "failed",
        );
      });
    } catch {
      finish(false, "jaimes_handoff_dispatch_error");
    }
  });
}

function parseReceipt(stdout) {
  try {
    const receipt = JSON.parse(stdout);
    return receipt && typeof receipt === "object" ? receipt : null;
  } catch {
    return null;
  }
}

export function validJoshClaimReceipt(stdout) {
  const receipt = parseReceipt(stdout);
  return Boolean(
    receipt
    && receipt.ok === true
    && receipt.status === "queued"
    && receipt.reaction_ok === true
    && receipt.card_start_ok === true
    && positiveTelegramId(receipt.header_message_id)
    && positiveTelegramId(receipt.live_message_id)
    && typeof receipt.job_id === "string"
    && receipt.job_id.trim(),
  );
}

function receiptHasPartialEffects(stdout) {
  const receipt = parseReceipt(stdout);
  return Boolean(
    receipt
    && (
      receipt.card_start_ok === true
      || receipt.surface_indeterminate === true
      || positiveTelegramId(receipt.header_message_id)
      || positiveTelegramId(receipt.live_message_id)
      || (typeof receipt.job_id === "string" && receipt.job_id.trim())
    ),
  );
}

function terminateHelperTree(child) {
  try {
    if (Number.isInteger(child?.pid) && child.pid > 0) {
      process.kill(-child.pid, "SIGTERM");
      return;
    }
  } catch { /* fall through to the direct-child fallback */ }
  try { child?.kill?.("SIGTERM"); } catch { /* best effort */ }
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

function protocolPathsForClaim(claimPath) {
  return {
    effectPath: `${claimPath}.effects.json`,
    cancelPath: `${claimPath}.cancel.json`,
    lockPath: `${claimPath}.protocol.lock`,
  };
}

function atomicWriteJson(filePath, value) {
  const temp = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  const fd = fs.openSync(temp, "wx", 0o600);
  try {
    fs.writeFileSync(fd, `${JSON.stringify(value)}\n`, "utf8");
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  fs.renameSync(temp, filePath);
  try {
    const directoryFd = fs.openSync(path.dirname(filePath), "r");
    try { fs.fsyncSync(directoryFd); } finally { fs.closeSync(directoryFd); }
  } catch { /* directory fsync is best effort on non-POSIX test hosts */ }
}

function readJsonFile(filePath) {
  try {
    const value = JSON.parse(fs.readFileSync(filePath, "utf8"));
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
}

function protocolSleep(milliseconds) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}

function withProtocolLock(paths, callback) {
  const deadline = Date.now() + PROTOCOL_LOCK_WAIT_MS;
  while (Date.now() <= deadline) {
    try {
      fs.mkdirSync(paths.lockPath, { mode: 0o700 });
      try {
        return { ok: true, value: callback() };
      } finally {
        try { fs.rmdirSync(paths.lockPath); } catch { /* best effort */ }
      }
    } catch (error) {
      if (error?.code !== "EEXIST") return { ok: false, value: undefined };
      try {
        const age = Date.now() - fs.statSync(paths.lockPath).mtimeMs;
        if (age > PROTOCOL_LOCK_STALE_MS) {
          fs.rmdirSync(paths.lockPath);
          continue;
        }
      } catch { /* another owner may have just released it */ }
      protocolSleep(PROTOCOL_LOCK_RETRY_MS);
    }
  }
  return { ok: false, value: undefined };
}

export function effectHasIrreversibleCheckpoint(effectPath) {
  const effect = readJsonFile(effectPath);
  const unresolvedAttemptOverran = stringValue(effect.state) === "attempting"
    && Number.isFinite(Number(effect.resolve_by_ms))
    && Number(effect.resolve_by_ms) <= Date.now();
  return Boolean(
    ["indeterminate", "queued"].includes(stringValue(effect.state))
    || unresolvedAttemptOverran
    || positiveTelegramId(effect.header_message_id)
    || positiveTelegramId(effect.live_message_id),
  );
}

function cancelBeforeIrreversibleEffect(paths) {
  const locked = withProtocolLock(paths, () => {
    if (effectHasIrreversibleCheckpoint(paths.effectPath)) return { handled: true, cancelled: false };
    atomicWriteJson(paths.cancelPath, {
      version: 1,
      state: "cancelled-before-surface",
      cancelled_at: new Date().toISOString(),
    });
    return { handled: false, cancelled: true };
  });
  // If the lock owner cannot be observed safely, suppress duplicate handling.
  return locked.ok ? locked.value : { handled: true, cancelled: false, uncertain: true };
}

function cleanupClaimProtocol(claimPath) {
  const paths = protocolPathsForClaim(claimPath);
  for (const filePath of [paths.effectPath, paths.cancelPath]) {
    try { fs.unlinkSync(filePath); } catch { /* absent or already cleaned */ }
  }
  try { fs.rmdirSync(paths.lockPath); } catch { /* absent or currently owned */ }
}

function reclaimAfterOwnedHelperTermination(paths) {
  protocolSleep(25);
  if (effectHasIrreversibleCheckpoint(paths.effectPath)) {
    return { handled: true, cancelled: false };
  }
  try { fs.rmdirSync(paths.lockPath); } catch { /* helper may have released it */ }
  return cancelBeforeIrreversibleEffect(paths);
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
    if (
      status === "queued"
      || status === "indeterminate"
      || status === "started"
      || (status !== "failed" && (!Number.isFinite(age) || age <= CLAIM_STALE_MS))
    ) {
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
    cleanupClaimProtocol(claimPath);
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
  if (reservation.existing) {
    const existing = readClaim(reservation.path);
    if (existing.status === "queued" || existing.status === "indeterminate") return true;
    if (existing.status === "failed") return false;
    const timeoutMs = Number.isFinite(config.helperTimeoutMs)
      ? Math.max(1, config.helperTimeoutMs)
      : DEFAULT_HELPER_TIMEOUT_MS;
    return new Promise((resolve) => {
      const deadline = Date.now() + timeoutMs;
      const poll = () => {
        const claim = readClaim(reservation.path);
        if (claim.status === "queued" || claim.status === "indeterminate") return resolve(true);
        if (claim.status === "failed") return resolve(false);
        if (Date.now() >= deadline) {
          const timeoutDecision = cancelBeforeIrreversibleEffect(protocolPathsForClaim(reservation.path));
          if (timeoutDecision.handled) {
            updateClaim(reservation.path, {
              status: "indeterminate",
              error: timeoutDecision.uncertain
                ? "helper_protocol_lock_indeterminate"
                : "helper_surface_receipt_wait_timeout",
            });
            return resolve(true);
          }
          updateClaim(reservation.path, { status: "failed", error: "helper_cancelled_before_surface" });
          return resolve(false);
        }
        setTimeout(poll, 20);
      };
      poll();
    });
  }
  const timeoutMs = Number.isFinite(config.helperTimeoutMs)
    ? Math.max(1, config.helperTimeoutMs)
    : DEFAULT_HELPER_TIMEOUT_MS;
  const protocolPaths = protocolPathsForClaim(reservation.path);
  const surfaceDeadlineMs = Date.now() + Math.max(1, timeoutMs - SURFACE_RESOLUTION_BUDGET_MS);
  const args = helperArgs(event, ctx, {
    ...config,
    effectPath: protocolPaths.effectPath,
    cancelPath: protocolPaths.cancelPath,
    surfaceDeadlineMs,
  });
  const helper = args[0];
  if (!fs.existsSync(helper)) {
    try { fs.unlinkSync(reservation.path); } catch { /* best effort */ }
    logger.error?.("inbox-coordinator: helper unavailable; allowing normal OpenCLAW handling");
    return false;
  }
  const pythonPath = stringValue(config.pythonPath, "/opt/homebrew/bin/python3");
  const prompt = String(event.bodyForAgent || event.body || event.content || "");
  const spawnHelper = config.spawn || spawn;
  return new Promise((resolve) => {
    let child;
    let settled = false;
    let cancelledFailOpen = false;
    let stdout = "";
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(ok);
    };
    const failDispatch = (reason = "helper_receipt_failure") => {
      if (settled) return;
      let timeoutDecision = cancelBeforeIrreversibleEffect(protocolPaths);
      if (timeoutDecision.uncertain) {
        terminateHelperTree(child);
        timeoutDecision = reclaimAfterOwnedHelperTermination(protocolPaths);
      }
      if (!timeoutDecision.handled) {
        cancelledFailOpen = true;
        terminateHelperTree(child);
      }
      const partialEffects = timeoutDecision.handled
        || effectHasIrreversibleCheckpoint(protocolPaths.effectPath)
        || receiptHasPartialEffects(stdout);
      updateClaim(reservation.path, {
        status: partialEffects ? "indeterminate" : "failed",
        error: timeoutDecision.uncertain ? "helper_protocol_lock_indeterminate" : reason,
      });
      if (partialEffects) {
        logger.error?.("inbox-coordinator: helper receipt is indeterminate after possible Telegram effects; suppressing duplicate fallback");
      } else {
        logger.error?.("inbox-coordinator: helper failed before Telegram effects; allowing normal OpenCLAW handling");
      }
      finish(partialEffects);
    };
    const timer = setTimeout(() => failDispatch("helper_receipt_timeout"), timeoutMs);
    try {
      child = spawnHelper(pythonPath, args, {
        // A no-effect timeout terminates the helper and every subprocess it
        // may have started before the irreversible Telegram checkpoint.
        detached: true,
        stdio: ["pipe", "pipe", "ignore"],
        env: process.env,
      });
      child.once?.("spawn", () => {
        updateClaim(reservation.path, { status: "started" });
      });
      child.once?.("error", () => {
        failDispatch("helper_process_error");
      });
      child.stdout?.on("data", (chunk) => {
        stdout = appendReceiptChunk(stdout, chunk);
      });
      child.once?.("close", (code) => {
        if (settled && cancelledFailOpen) return;
        const queued = code === 0 && validJoshClaimReceipt(stdout);
        const partialEffects = !queued && (
          receiptHasPartialEffects(stdout)
          || effectHasIrreversibleCheckpoint(protocolPaths.effectPath)
        );
        updateClaim(reservation.path, {
          status: queued ? "queued" : partialEffects ? "indeterminate" : "failed",
          error: queued ? "" : `helper_exit_${code ?? "unknown"}`,
        });
        // The hook may already have returned after its bounded wait. Reconcile
        // the durable claim from a late receipt without changing that decision:
        // a valid queue becomes queued, visible partial effects stay fenced,
        // and a clean no-effect exit becomes retryable instead of remaining a
        // permanent indeterminate reservation.
        if (settled) return;
        if (!queued) {
          logger.error?.(partialEffects
            ? "inbox-coordinator: helper exited after partial Telegram effects; suppressing duplicate fallback"
            : "inbox-coordinator: helper exited without a valid queue receipt; allowing normal OpenCLAW handling");
        }
        finish(queued || partialEffects);
      });
      child.stdin?.once?.("error", () => {
        failDispatch("helper_stdin_error");
      });
      child.stdin?.end(prompt, "utf8");
    } catch {
      clearTimeout(timer);
      try { fs.unlinkSync(reservation.path); } catch { /* best effort */ }
      cleanupClaimProtocol(reservation.path);
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
    // Reserve one durable owner before either hook can dispatch. The hook is
    // suppressed only after the helper returns a valid durable queue receipt;
    // invalid or missing receipts fall back to normal Josh 2.0 handling.
    api.on("message_received", (event, ctx) => {
      const config = api.pluginConfig || {};
      rememberInboundMessage(event, ctx, config);
    }, { priority: 200 });
    api.on("inbound_claim", async (event, ctx) => {
      const config = event.context?.pluginConfig || api.pluginConfig || {};
      return handleInboxEvent(event, ctx, config, api.logger, dispatchClaim, "inbound_claim");
    }, { priority: 100, timeoutMs: 15_000 });
    api.on("before_dispatch", async (event, ctx) => {
      const config = api.pluginConfig || {};
      return handleInboxEvent(event, ctx, config, api.logger, dispatchClaim, "before_dispatch");
    }, { priority: 100, timeoutMs: 15_000 });
  },
};
