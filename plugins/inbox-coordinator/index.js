import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";

const DEFAULT_CHAT_ID = "-1003589561528";
const DEFAULT_THREAD_ID = "1";
const DEFAULT_MENTIONS = ["@jaimes"];
const DEFAULT_REGISTRY_PATH = path.join(
  process.env.HOME || "/Users/josh2.0",
  ".openclaw", "workspace", "mission-control", "config", "telegram-intake-lanes.json",
);
const BUNDLED_REGISTRY_PATH = fileURLToPath(
  new URL("../../config/telegram-intake-lanes.json", import.meta.url),
);
const GROUP_TOPIC_RE = /telegram:group:(-?\d+):(?:topic:)?(\d+)/i;
// Header + live-card setup has an 8-second SLO. Keep the helper receipt wait
// inside OpenCLAW's 15-second hook budget without timing out a healthy card
// setup at the old 3-second boundary.
const DEFAULT_HELPER_TIMEOUT_MS = 12_000;
const DEFAULT_TERMINAL_HELPER_TIMEOUT_MS = 10_000;
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
const FINAL_DELIVERY_GATE_TTL_MS = 60_000;
const MAX_CANONICAL_FINAL_BYTES = 3_500;
const FINAL_SECTION_NAMES = ["Complete", "What was done", "Issues", "Appropriate next steps", "Approval needed"];
const recentInboundMessages = new Map();
const consumedInboundMessages = new Map();
const terminalDeliveryGates = new Map();

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

function nativeTelegramSessionCommand(text) {
  return /^\/(?:new|reset)(?:@[A-Za-z0-9_]+)?\s*$/i.test(String(text || "").trim());
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
  const candidates = [ctx.sessionKey, event.sessionKey, event.conversationId, ctx.conversationId]
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

export function inboxAuthority(config = {}) {
  const registryPath = stringValue(config.registryPath)
    || (fs.existsSync(DEFAULT_REGISTRY_PATH) ? DEFAULT_REGISTRY_PATH : BUNDLED_REGISTRY_PATH);
  try {
    const registry = JSON.parse(fs.readFileSync(registryPath, "utf8"));
    const group = registry?.groups?.[DEFAULT_CHAT_ID];
    const topic = group?.topics?.[DEFAULT_THREAD_ID];
    if (topic?.owner !== "josh2" || topic?.lane !== "inbox") {
      return { available: false, reason: "inbox-authority-mismatch" };
    }
    const mentionOwners = new Map();
    for (const [rawHandle, rawOwner] of Object.entries(registry.mentionOverrides || {})) {
      const handle = String(rawHandle || "").trim().toLowerCase();
      const owner = String(rawOwner || "").trim().toLowerCase();
      if (/^@[a-z0-9_]+$/.test(handle) && ["josh2", "jaimes", "joshex", "jain"].includes(owner)) {
        mentionOwners.set(handle, owner);
      }
    }
    return {
      available: true,
      chatId: DEFAULT_CHAT_ID,
      threadId: DEFAULT_THREAD_ID,
      owner: topic.owner,
      mentionOwners,
    };
  } catch {
    return { available: false, reason: "inbox-authority-unavailable" };
  }
}

function registeredMentionOwners(text, authority) {
  const content = String(text || "");
  const owners = new Set();
  for (const [handle, owner] of authority.mentionOwners || []) {
    if (new RegExp(`(^|[\\s,.:;!?()\\[\\]{}])${escapeRegExp(handle)}(?=$|[\\s,.:;!?()\\[\\]{}])`, "i").test(content)) {
      owners.add(owner);
    }
  }
  return owners;
}

export function inboxDecision(event = {}, ctx = {}, config = {}) {
  const channel = String(ctx.channelId || event.channel || "").toLowerCase();
  if (channel !== "telegram") return "ignore";
  const target = parseTelegramTarget(event, ctx);
  if (target.chatId !== DEFAULT_CHAT_ID || target.threadId !== DEFAULT_THREAD_ID) return "ignore";
  const authority = inboxAuthority(config);
  // A clean authority failure leaves the event to OpenCLAW's native fallback;
  // this plugin must never claim from a stale embedded ownership map.
  if (!authority.available) return "ignore";
  const content = event.bodyForAgent || event.body || event.content || "";
  if (nativeTelegramSessionCommand(content)) return "ignore";
  if (internalReplayPrompt(content)) return "silence";
  const mentioned = registeredMentionOwners(content, authority);
  if (mentioned.size > 1) return "silence";
  if (mentioned.size === 1) {
    const owner = [...mentioned][0];
    if (owner === "jaimes") return "handoff";
    if (owner !== "josh2") return "silence";
  }
  return "claim";
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

function exactInboxTarget(event = {}, ctx = {}, config = {}) {
  const target = parseTelegramTarget(event, ctx);
  const authority = inboxAuthority(config);
  return authority.available
    && target.chatId === authority.chatId
    && target.threadId === authority.threadId;
}

function decodeFinalHtml(value) {
  const entities = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" };
  return String(value || "").replace(/&(amp|lt|gt|quot|#39);/gi, (match, name) => (
    entities[String(name).toLowerCase()] ?? match
  ));
}

function finalFailure(reason, sections = {}) {
  return { ok: false, reason, terminalStatus: "paused", sections };
}

function finalSectionBlock(lines, positions, name) {
  const index = FINAL_SECTION_NAMES.indexOf(name);
  const start = positions[index];
  const end = index + 1 < positions.length ? positions[index + 1] : lines.length;
  const label = `${name}:`;
  const block = lines.slice(start, end);
  return {
    inline: block[0].slice(label.length).trim(),
    tail: block.slice(1).filter((line) => line.trim()),
  };
}

function joinedSection(block) {
  return [block.inline, ...block.tail.map((line) => line.trim().replace(/^-\s+/, ""))]
    .filter(Boolean).join(" ").trim();
}

function bulletCount(block) {
  let count = 0;
  let hasBullet = false;
  for (const line of block.tail) {
    if (line.startsWith("- ")) {
      count += 1;
      hasBullet = true;
    } else if (!hasBullet || !line.startsWith("  ")) {
      return -1;
    }
  }
  return count;
}

function bulletItems(block) {
  if (block.inline) return [];
  const items = [];
  for (const line of block.tail) {
    if (line.startsWith("- ")) {
      items.push(line.slice(2).trim());
    } else if (line.startsWith("  ") && items.length) {
      items[items.length - 1] = `${items[items.length - 1]} ${line.trim()}`.trim();
    } else {
      return [];
    }
  }
  return items;
}

const STATUS_ONLY_PATTERNS = [
  /\b(?:assessment|task|work|review|objective|request)\s+(?:is\s+)?(?:complete|completed|done|finished|closed)\b/i,
  /\bcompleted\s+(?:the\s+)?requested\s+(?:task|work|review|assessment)\b/i,
  /\b(?:checked|reviewed)\s+(?:the\s+)?(?:request|task|objective)\b/i,
  /\bverified\s+(?:the\s+)?(?:worker|runtime|agent|execution)(?:\s+(?:state|status))?\b/i,
  /\b(?:result|summary|final(?:\s+(?:answer|response))?)\s+(?:was\s+)?(?:prepared|delivered|sent|posted)\b/i,
  /\b(?:prepared|delivered|sent|posted)\s+(?:the\s+)?(?:result|summary|final(?:\s+(?:answer|response))?)\b/i,
  /\b(?:closed|completed)\s+(?:the\s+)?(?:task\s+)?lifecycle\b/i,
  /\bagent work reached final review\b/i,
  /\blive card ordering (?:was )?preserved\b/i,
  /\bresponse formatting (?:was )?recovered\b/i,
];

//JAIMES: Negative operational findings are concrete outcomes; keep this in lockstep with the Python and stress validators.
const CONCRETE_RESULT_PATTERN = /\b(?:added|changed|confirmed|created|determined|differ(?:s|ed|ent)?|caus(?:e|es|ed)|repair(?:s|ed)?|(?:en|dis)abl(?:e|es|ed|ing)|failed|fixed|found|healthy|identified|implemented|passed|rejected|removed|reproduced|resolved|restored|returned|supports?|unsupported|updated|verified\s+(?:that|correctly|successfully|\d+)|cannot|can['’]?t|could not|does not|did not|risk|limitation|recommend(?:ed|ation)?|\d+\s+(?:tests?|checks?|cases?)\b)\b/i;
const OPERATIONAL_RESULT_PATTERN = /(?:\b(?:gateway|service|daemon|watcher|process|socket|connection|endpoint|api|launchd|runtime|bot|helper|logs?|files?|source|entry|entries|inventory|messages?|delivery)\b.{0,100}\b(?:running|listening|connected|reachable|responding|registered|loaded|active|healthy|stopped|offline|unreachable|empty|stale|missing|absent|unavailable|unverified|last\s+modified|has\s+no|have\s+no|there\s+(?:is|are)\s+no|port\s+\d{2,5})\b|\b(?:running|listening|connected|reachable|responding|registered|loaded|active|healthy|stopped|offline|unreachable|empty|stale|missing|absent|unavailable|unverified|last\s+modified|has\s+no|have\s+no|there\s+(?:is|are)\s+no|port\s+\d{2,5})\b.{0,100}\b(?:gateway|service|daemon|watcher|process|socket|connection|endpoint|api|launchd|runtime|bot|helper|logs?|files?|source|entry|entries|inventory|messages?|delivery)\b)/i;
const OPERATIONAL_STATUS_FILLER_PATTERN = /\b(?:gateway|service|daemon|watcher|process|runtime|bot|helper|delivery)\s+(?:(?:health|status|operational|connectivity|delivery)\s+){0,2}(?:assessment|review|report|request|task|work)\s+(?:is\s+|was\s+|remains\s+)?(?:active|running|connected|complete|completed|done|last\s+modified)\b/i;
const RISK_OR_LIMITATION_PATTERN = /\b(?:risk|limitation|cannot|can['’]?t|could not|does not|did not|unsupported|blocked|failed|failure|unable|do not|don['’]?t|avoid)\b/i;
const OPERATIONAL_RISK_PATTERN = /(?:\b(?:gateway|service|daemon|watcher|process|socket|connection|endpoint|api|launchd|runtime|bot|helper|logs?|files?|source|entry|entries|inventory|messages?|delivery)\b.{0,100}\b(?:not\s+(?:running|listening|connected|reachable|responding|registered|loaded|active|healthy)|stopped|offline|unreachable|empty|stale|missing|absent|unavailable|unverified|has\s+no|have\s+no|there\s+(?:is|are)\s+no)\b|\b(?:not\s+(?:running|listening|connected|reachable|responding|registered|loaded|active|healthy)|stopped|offline|unreachable|empty|stale|missing|absent|unavailable|unverified|has\s+no|have\s+no|there\s+(?:is|are)\s+no)\b.{0,100}\b(?:gateway|service|daemon|watcher|process|socket|connection|endpoint|api|launchd|runtime|bot|helper|logs?|files?|source|entry|entries|inventory|messages?|delivery)\b)/i;
const NEGATED_OPERATIONAL_RISK_PATTERN = /\b(?:no|not|without)\s+(?:\w+\s+){0,2}(?:stopped|offline|unreachable|empty|stale|missing|absent|unavailable|unverified)\b/gi;
const POSITIVE_OPERATIONAL_ABSENCE_PATTERN = /\b(?:(?:has|have)\s+no|there\s+(?:is|are)\s+no)\s+(?:remaining\s+)?(?:service\s+)?(?:issues?|failures?|errors?|problems?|risks?|blockers?)\b/gi;
const RECOMMENDATION_PATTERN = /\b(?:recommend(?:ed|ation)?|should|must|next step|follow[- ]?up|do not|don['’]?t|avoid|needs? to|requires?)\b/i;
const UNVERIFIED_HEADER_PATTERN = /(?:\b(?:unverified|unknown|unset|not verified)\b|^(?:n\/?a|none)$)/i;

function normalizedBullet(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function isStatusOnlyBullet(value) {
  const text = String(value || "").trim();
  return !text || STATUS_ONLY_PATTERNS.some((pattern) => pattern.test(text));
}

function isSubstantiveBullet(value) {
  const text = String(value || "").trim();
  return text.length >= 18
    && text.split(/\s+/).filter(Boolean).length >= 4
    && !isStatusOnlyBullet(text)
    && !/^(?:n\/?a|none|no action needed)\.?$/i.test(text);
}

function hasConcreteResult(value) {
  const text = String(value || "");
  if (OPERATIONAL_STATUS_FILLER_PATTERN.test(text)) return false;
  return CONCRETE_RESULT_PATTERN.test(text) || OPERATIONAL_RESULT_PATTERN.test(text);
}

function hasOperationalRisk(value) {
  const text = String(value || "")
    .replace(NEGATED_OPERATIONAL_RISK_PATTERN, "")
    .replace(POSITIVE_OPERATIONAL_ABSENCE_PATTERN, "");
  return OPERATIONAL_RISK_PATTERN.test(text);
}

function summaryQualityFailure(items) {
  const unique = new Set(items.map(normalizedBullet));
  if (unique.size !== items.length) return "what-was-done-duplicate-bullets";
  if (items.some(isStatusOnlyBullet)) return "what-was-done-status-filler";
  if (items.some((item) => !isSubstantiveBullet(item))) return "what-was-done-not-substantive";
  if (items.filter(hasConcreteResult).length < 2) return "what-was-done-concrete-outcome-count";
  return "";
}

function verifiedRuntimeModel(event = {}) {
  const provider = stringValue(event.provider);
  const model = stringValue(event.model);
  return provider && model ? `${provider}/${model}` : model;
}

export function parseCanonicalFinalSummary(content, options = {}) {
  const text = String(content || "").trim();
  if (!text) return finalFailure("empty-final");
  if (Buffer.byteLength(text, "utf8") > MAX_CANONICAL_FINAL_BYTES) {
    return finalFailure("final-too-long");
  }
  const pre = /^<pre>([\s\S]*)<\/pre>$/i.exec(text);
  const legacyPre = Boolean(pre);
  let body;
  if (legacyPre) {
    body = decodeFinalHtml(pre[1]).replace(/\r\n?/g, "\n").replace(/^\n|\n$/g, "");
    if (!body || /<\/?[a-z][^>]*>/i.test(body)) return finalFailure("non-plain-pre-content");
  } else {
    const unsupported = text.replace(
      /<\/?(?:b|strong|i|em|u|s|code|blockquote|p|h[1-6]|ul|li|details|summary|footer)>|<br\s*\/?>/gi,
      "",
    );
    if (/<[^>]+>/.test(unsupported)) return finalFailure("unsupported-rich-final-tag");
    body = text
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/(?:blockquote|p|h[1-6]|li|details|summary|footer)>/gi, "\n")
      .replace(/<[^>]+>/g, "");
    body = decodeFinalHtml(body).replace(/\r\n?/g, "\n").replace(/^\n|\n$/g, "");
  }
  const lines = body.split("\n").map((line) => {
    const normalized = line.replace(/^\s*•\s+/, "- ");
    return legacyPre ? normalized : normalized.trim();
  });
  if (legacyPre && lines.some((line) => Array.from(line).length > 38)) {
    return finalFailure("line-over-38-columns");
  }

  const positions = FINAL_SECTION_NAMES.map((name) => lines.findIndex((line) => line.startsWith(`${name}:`)));
  if (positions.some((position) => position < 0)) {
    const missingIndex = positions.findIndex((position) => position < 0);
    return finalFailure(`missing-${FINAL_SECTION_NAMES[missingIndex].toLowerCase().replaceAll(" ", "-")}`);
  }
  if (positions[0] < 1 || positions.some((position, index) => index > 0 && position <= positions[index - 1])) {
    return finalFailure("section-order");
  }

  const allHeaderLines = lines.slice(0, positions[0]).filter((line) => line.trim());
  const modelIndex = allHeaderLines.findIndex((line) => line.startsWith("Model:"));
  const headerLines = modelIndex >= 0 ? allHeaderLines.slice(modelIndex) : [];
  if (!headerLines.length) return finalFailure("missing-model-route-line");
  const header = headerLines.map((line) => line.trim()).join(" ").replace(/\s+/g, " ");
  const exactHeaderFields = ["Model", "Route", "Why"].every((name) => (
    (header.match(new RegExp(`\\b${name}:`, "gi")) || []).length === 1
  ));
  const combinedMatch = /^Model:\s*([^|]+?)\s*\|\s*Route:\s*([^|]+?)\s*\|\s*Why:\s*([^|]+)$/i.exec(header);
  const fields = {};
  let activeField = "";
  for (const line of headerLines) {
    const match = /^(Model|Route|Why):\s*(.*)$/i.exec(line.trim());
    if (match) {
      activeField = match[1].toLowerCase();
      fields[activeField] = match[2].trim();
    } else if (activeField && /^\s{2,}\S/.test(line)) {
      fields[activeField] = `${fields[activeField]} ${line.trim()}`.trim();
    } else {
      return finalFailure("invalid-header-wrap");
    }
  }
  const headerValues = combinedMatch
    ? combinedMatch.slice(1).map((value) => value.trim())
    : [fields.model, fields.route, fields.why].map((value) => String(value || "").trim());
  if (!exactHeaderFields || headerValues.some((value) => !value)) return finalFailure("invalid-model-route-line");
  const expectedModel = stringValue(options.expectedModel).toLowerCase();
  if (expectedModel && headerValues[0].toLowerCase() !== expectedModel) {
    return finalFailure("unverified-model-line");
  }

  const blocks = Object.fromEntries(FINAL_SECTION_NAMES.map((name) => [name, finalSectionBlock(lines, positions, name)]));
  const sections = Object.fromEntries(FINAL_SECTION_NAMES.map((name) => [name, joinedSection(blocks[name])]));
  const complete = sections.Complete.toLowerCase();
  if (!/^(?:yes|no)\b/i.test(complete)) return finalFailure("invalid-complete", sections);
  const doneCount = blocks["What was done"].inline ? -1 : bulletCount(blocks["What was done"]);
  if (doneCount < 3 || doneCount > 5) return finalFailure("what-was-done-bullet-count", sections);
  const issues = sections.Issues.toLowerCase();
  const approval = sections["Approval needed"].toLowerCase();
  const noIssue = /^(?:n\/a|none)\.?$/i.test(issues);
  if (!noIssue && (blocks.Issues.inline || bulletCount(blocks.Issues) < 1)) return finalFailure("invalid-issues", sections);
  const nextSteps = sections["Appropriate next steps"];
  if (!nextSteps) return finalFailure("empty-appropriate-next-steps", sections);
  const noApproval = /^(?:n\/a|none|no action needed)\.?$/i.test(approval);
  if (!noApproval && (blocks["Approval needed"].inline || bulletCount(blocks["Approval needed"]) < 1)) {
    return finalFailure("invalid-approval-needed", sections);
  }
  const approvalNeeded = !noApproval;
  const completeYes = /^yes\b/i.test(complete) && !/\b(?:not|partial|blocked|failed)\b/i.test(complete);
  const doneItems = bulletItems(blocks["What was done"]);
  if (completeYes) {
    if (headerValues.some((value) => UNVERIFIED_HEADER_PATTERN.test(value))) {
      return finalFailure("unverified-header-line", sections);
    }
    const qualityFailure = summaryQualityFailure(doneItems);
    if (qualityFailure) return finalFailure(qualityFailure, sections);
  }
  const resultText = doneItems.join(" ");
  const combinedRiskText = `${resultText} ${nextSteps}`;
  const hasRiskOrLimitation = RISK_OR_LIMITATION_PATTERN.test(combinedRiskText)
    || hasOperationalRisk(combinedRiskText);
  const noActionNeeded = /^(?:no action needed)\.?$/i.test(nextSteps.trim());
  if (noIssue && hasRiskOrLimitation) return finalFailure("issues-required-for-risk-or-limitation", sections);
  if (noActionNeeded && (!noIssue || approvalNeeded || RECOMMENDATION_PATTERN.test(resultText))) {
    return finalFailure("no-action-conflicts-with-summary", sections);
  }
  if (approvalNeeded && noIssue) return finalFailure("approval-requires-issue", sections);
  const explicitFailure = /\b(?:failed|failure|fatal|unrecoverable error)\b/i.test(`${complete} ${issues}`);
  const terminalStatus = completeYes && !approvalNeeded
    ? "done"
    : explicitFailure && !approvalNeeded
      ? "failed"
      : "paused";
  return {
    ok: true,
    reason: "canonical",
    terminalStatus,
    model: headerValues[0],
    route: headerValues[1],
    why: headerValues[2],
    sections,
  };
}

function fixedWidthLines(value, firstPrefix = "", continuation = "   ") {
  const words = String(value || "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return [firstPrefix.trimEnd()];
  const lines = [];
  let prefix = firstPrefix;
  let line = prefix;
  while (words.length) {
    let word = words.shift();
    const separator = line === prefix ? "" : " ";
    if (Array.from(`${line}${separator}${word}`).length <= 38) {
      line = `${line}${separator}${word}`;
      continue;
    }
    if (line !== prefix) {
      lines.push(line);
      prefix = continuation;
      line = prefix;
      words.unshift(word);
      continue;
    }
    const capacity = Math.max(1, 38 - Array.from(prefix).length);
    const characters = Array.from(word);
    lines.push(`${prefix}${characters.slice(0, capacity).join("")}`);
    word = characters.slice(capacity).join("");
    prefix = continuation;
    line = prefix;
    if (word) words.unshift(word);
  }
  if (line !== prefix || !lines.length) lines.push(line);
  return lines;
}

function escapeTelegramHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function codeblockFinalSummary({ owner, complete, model, route, why, detail = "", done, issues, nextSteps, approvals }) {
  const field = (label, value) => fixedWidthLines(
    String(value || "unverified"),
    `${label}: `,
    " ".repeat(label.length + 2),
  );
  const bullets = (items, fallback) => {
    const values = (Array.isArray(items) ? items : []).filter(Boolean).slice(0, 5);
    return (values.length ? values : [fallback]).flatMap((item) => fixedWidthLines(item, "- ", "  "));
  };
  const completion = `${complete ? "Yes" : "No"}${detail ? ` - ${detail}` : ""}`;
  const lines = [
    ...fixedWidthLines(`${owner} · ${complete ? "COMPLETE" : "NEEDS ATTENTION"}`),
    "",
    ...field("Model", model),
    ...field("Route", route),
    ...field("Why", why),
    "",
    ...fixedWidthLines(completion, "Complete: ", "  "),
    "",
    "What was done:",
    ...bullets(done, "Detailed findings were not captured."),
    "",
    "Issues:",
    ...bullets(issues, "None"),
    "",
    "Appropriate next steps:",
    ...bullets(nextSteps, "No action needed."),
    "",
    "Approval needed:",
    ...bullets(approvals, "None"),
  ];
  return `<pre>${escapeTelegramHtml(lines.join("\n"))}</pre>`;
}

export function buildRecoveryFinalSummary(content, expectedModel = "") {
  const source = String(content || "")
    .replace(/<\/?pre>/gi, "\n")
    .replace(/<[^>]*>/g, " ")
    .replace(/[*_`#>]+/g, " ")
    .replace(/^\s*Model:[^\n]*(?:\n\s{3}[^\n]*)*/gim, "\n")
    .replace(/\b(?:Complete|What was done|Issues|Appropriate next steps|Approval needed)\s*:/gi, "\n")
    .replace(/\r/g, "\n");
  const plain = source.replace(/\s+/g, " ").trim();
  const seen = new Set();
  const sourceFragments = source
    .split(/\n+|(?<=[.!?])\s+|\s*[|•]\s*/)
    .map((item) => item.replace(/^[-\s]+/, "").replace(/\s+/g, " ").trim())
    .filter((item) => item && !/^(?:n\/?a|none|no action needed)\.?$/i.test(item))
    .filter((item) => {
      const normalized = normalizedBullet(item);
      if (!normalized || seen.has(normalized)) return false;
      seen.add(normalized);
      return true;
    });
  const fragments = sourceFragments
    .filter((item) => isSubstantiveBullet(item))
    .slice(0, 5);
  const failed = /\b(?:failed|failure|fatal|blocked|could not|unable)\b/i.test(plain);
  const incomplete = /\b(?:not complete|not completed|not done|incomplete|partial|pending|waiting|still needs?|remains?)\b/i.test(plain);
  const approvalNeeded = /\b(?:waiting for approval|approval (?:needed|required|pending)|needs? approval|approve|sign[- ]?off|permission required|cannot\b[^.]{0,80}\bwithout approval|can['’]t\b[^.]{0,80}\bwithout approval)\b/i.test(plain)
    && !/\b(?:no approval|approval (?:is )?not (?:needed|required)|can (?:proceed|continue|release|deploy) without approval)\b/i.test(plain);
  const sourceQualitySufficient = fragments.length >= 3
    && fragments.filter(hasConcreteResult).length >= 2;
  const complete = sourceQualitySufficient
    && !failed
    && !incomplete
    && !approvalNeeded
    && /\b(?:completed|successfully completed|is complete|work is done|tests? passed|released|deployed)\b/i.test(plain);
  const recoveryItems = [...fragments];
  if (!sourceQualitySufficient) {
    for (const disclosure of [
      "The source lacked three concrete findings.",
      "Missing facts were not inferred or invented.",
      "A detailed result was not captured.",
    ]) {
      if (recoveryItems.length >= 3) break;
      recoveryItems.push(disclosure);
    }
  }
  const riskFragment = sourceFragments.find((item) => (
    RISK_OR_LIMITATION_PATTERN.test(item) || hasOperationalRisk(item)
  ));
  const recommendation = sourceFragments.find((item) => RECOMMENDATION_PATTERN.test(item));
  const model = stringValue(expectedModel, "unverified");
  const completeText = complete
    ? "Yes - agent reported completion."
    : failed
      ? "No - agent reported a problem."
      : !sourceQualitySufficient
        ? "No - findings were incomplete."
        : "No - completion was not explicit.";
  const issues = failed
    ? [riskFragment || "The source reports a failure or blocker."]
    : approvalNeeded
      ? ["The source says approval is required."]
      : incomplete
        ? ["The source says the work is incomplete."]
        : !sourceQualitySufficient
          ? ["Detailed findings were not captured."]
          : riskFragment
            ? [riskFragment]
            : ["None"];
  const nextStep = recommendation
    || (complete ? "No action needed." : "Retry with evidence, findings, and a recommendation.");
  const approvals = approvalNeeded
    ? ["Review and approve the requested next step."]
    : ["None"];
  const completeParts = /^(?:Yes|No)\s*-\s*(.*)$/i.exec(completeText);
  return codeblockFinalSummary({
    owner: "JOSH 2.0",
    complete,
    model,
    route: "Josh 2.0 Inbox",
    why: "format recovery",
    detail: completeParts?.[1] || "",
    done: recoveryItems.slice(0, 5),
    issues,
    nextSteps: [nextStep],
    approvals,
  });
}

export function terminalHelperArgs(event = {}, ctx = {}, config = {}) {
  const sessionKey = stringValue(event.sessionKey || ctx.sessionKey);
  const target = parseTelegramTarget({ ...event, sessionKey }, ctx);
  const args = [
    stringValue(config.helperPath, path.join(
      process.env.HOME || "/Users/josh2.0",
      ".openclaw", "workspace", "mission-control", "scripts", "josh_telegram_fast_ack.py",
    )),
    "--close-before-final",
    "--chat-id", target.chatId,
    "--thread-id", target.threadId,
    "--session-key", sessionKey,
  ];
  const runId = stringValue(event.runId || ctx.runId);
  const sessionId = stringValue(event.sessionId || ctx.sessionId);
  if (runId) args.push("--run-id", runId);
  if (sessionId) args.push("--session-id", sessionId);
  if (["done", "paused", "failed"].includes(config.terminalStatus)) {
    args.push("--terminal-status", config.terminalStatus);
  }
  if (stringValue(config.finalSummary)) args.push("--final-from-stdin");
  return args;
}

function validTerminalReceipt(receipt) {
  return Boolean(
    receipt
    && receipt.ok === true
    && ["closed", "closed-and-final-delivered", "final-queued-for-retry", "final-delivery-indeterminate", "already-terminal", "no-card-required", "final-already-delivered", "not-applicable"].includes(receipt.status),
  );
}

export function closeLiveCardBeforeFinal(event = {}, ctx = {}, config = {}, logger = console) {
  if (!exactInboxTarget(event, ctx, config)) {
    return Promise.resolve({ ok: true, status: "not-applicable", card_closed: false });
  }
  if (!stringValue(event.runId || ctx.runId)) {
    return Promise.resolve({ ok: false, status: "missing-terminal-run-id", card_closed: false });
  }
  const args = terminalHelperArgs(event, ctx, config);
  const helper = args[0];
  if (!fs.existsSync(helper)) {
    return Promise.resolve({ ok: false, status: "terminal-helper-unavailable" });
  }
  const pythonPath = stringValue(config.pythonPath, "/opt/homebrew/bin/python3");
  const timeoutMs = Number.isFinite(config.terminalHelperTimeoutMs)
    ? Math.max(100, config.terminalHelperTimeoutMs)
    : DEFAULT_TERMINAL_HELPER_TIMEOUT_MS;
  const spawnHelper = config.terminalSpawn || spawn;
  const finalSummary = stringValue(config.finalSummary);
  return new Promise((resolve) => {
    let child;
    let settled = false;
    let stdout = "";
    const finish = (receipt) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(receipt);
    };
    const timer = setTimeout(() => {
      try { child?.kill?.("SIGTERM"); } catch { /* best effort */ }
      finish({ ok: false, status: "terminal-helper-timeout" });
    }, timeoutMs);
    try {
      child = spawnHelper(pythonPath, args, {
        detached: false,
        stdio: [finalSummary ? "pipe" : "ignore", "pipe", "ignore"],
        env: process.env,
      });
      child.once?.("error", () => finish({ ok: false, status: "terminal-helper-spawn-error" }));
      if (finalSummary) {
        child.stdin?.once?.("error", () => finish({ ok: false, status: "terminal-helper-stdin-error" }));
        child.stdin?.end?.(finalSummary, "utf8");
      }
      child.stdout?.on("data", (chunk) => {
        stdout = appendReceiptChunk(stdout, chunk);
      });
      child.once?.("close", (code) => {
        const receipt = parseReceipt(stdout);
        if (code === 0 && validTerminalReceipt(receipt)) return finish(receipt);
        logger.error?.("inbox-coordinator: final delivery paused because the live card did not close");
        return finish(receipt && typeof receipt === "object"
          ? { ...receipt, ok: false }
          : { ok: false, status: `terminal-helper-exit-${code ?? "unknown"}` });
      });
    } catch {
      finish({ ok: false, status: "terminal-helper-dispatch-error" });
    }
  });
}

function terminalGateKey(event = {}, ctx = {}) {
  return stringValue(event.sessionKey || ctx.sessionKey || event.sessionId || ctx.sessionId);
}

function rememberTerminalGate(event = {}, ctx = {}, receipt = {}, extra = {}) {
  const key = terminalGateKey(event, ctx);
  if (!key) return;
  terminalDeliveryGates.set(key, {
    ready: validTerminalReceipt(receipt),
    receipt,
    runId: stringValue(event.runId || ctx.runId),
    recordedAt: Date.now(),
    suppressNativeFinal: receipt.suppress_native_final === true,
    ...extra,
  });
}

function recentTerminalGate(event = {}, ctx = {}) {
  const key = terminalGateKey(event, ctx);
  if (!key) return undefined;
  const gate = terminalDeliveryGates.get(key);
  if (!gate || Date.now() - gate.recordedAt > FINAL_DELIVERY_GATE_TTL_MS) {
    terminalDeliveryGates.delete(key);
    return undefined;
  }
  return gate;
}

function terminalShapedOutbound(content) {
  const text = decodeFinalHtml(String(content || ""))
    .replace(/<\/?pre>/gi, "")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(?:blockquote|p|h[1-6]|li|details|summary|footer)>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/\r\n?/g, "\n");
  if (!/(?:^|\n)\s*Complete\s*:/i.test(text)) return false;
  const markers = [
    /(?:^|\n)\s*Model\s*:/i,
    /(?:^|\n)\s*What was done\s*:/i,
    /(?:^|\n)\s*Passed\s*:/i,
    /(?:^|\n)\s*Failed\s*:/i,
    /(?:^|\n)\s*Issues\s*:/i,
    /(?:^|\n)\s*Handoff\s*:/i,
    /(?:^|\n)\s*Normal Telegram use\s*:/i,
    /(?:^|\n)\s*(?:Appropriate next steps|Next|Approval needed)\s*:/i,
  ];
  return markers.filter((pattern) => pattern.test(text)).length >= 2;
}

export async function gateTelegramFinalization(event = {}, ctx = {}, config = {}, logger = console) {
  if (!exactInboxTarget(event, ctx, config)) return undefined;
  const expectedModel = verifiedRuntimeModel(event);
  const finalText = String(event.lastAssistantMessage || "");
  const finalSummary = parseCanonicalFinalSummary(finalText, { expectedModel });
  const identity = stringValue(event.runId || ctx.runId || event.sessionId || ctx.sessionId, "unknown-run");
  if (!finalSummary.ok) {
    rememberTerminalGate(event, ctx, { ok: false, status: "final-format-invalid" }, {
      formatValid: false,
      formatReason: finalSummary.reason,
      expectedModel,
    });
    return {
      action: "revise",
      reason: "The Telegram final response must be one concise structured summary before it can be delivered.",
      retry: {
        instruction: `Rewrite the same result as one mobile-safe Telegram <pre> code block with every line pre-wrapped to 38 columns. Start with JOSH 2.0 · COMPLETE or NEEDS ATTENTION, then put Model: ${expectedModel || "<verified provider/model>"}, Route: <actual lane>, and Why: <verified reason> on separate rows. Then use Complete: Yes/No, What was done: with 3-5 unique, concrete findings, outcomes, or changes from the actual work (never task-status, delivery, card, or formatting filler), Issues:, Appropriate next steps:, and Approval needed: in that exact order. Use plain - bullets with two-space hanging indents and no nested HTML. Do not claim No action needed when the result identifies a risk, limitation, issue, or recommendation. Keep the whole response under 3,500 bytes and do not add another live card.`,
        idempotencyKey: `telegram-final-format:${identity}`,
        maxAttempts: 2,
      },
    };
  }
  const receipt = await closeLiveCardBeforeFinal(event, ctx, {
    ...config,
    terminalStatus: finalSummary.terminalStatus,
    finalSummary: finalText,
  }, logger);
  rememberTerminalGate(event, ctx, receipt, { formatValid: true, expectedModel });
  if (validTerminalReceipt(receipt)) {
    return { action: "continue", reason: "The existing Telegram live work card is terminal." };
  }
  return {
    action: "revise",
    reason: "The interpreted Telegram live work card must reach its terminal state before the final response is delivered.",
    retry: {
      instruction: "Keep the same final answer, ensure the existing interpreted live work card is present, and retry finalization. Do not create a generic or duplicate card.",
      idempotencyKey: `telegram-live-card-before-final:${identity}`,
      maxAttempts: 2,
    },
  };
}

export async function enforceTelegramFinalDelivery(event = {}, ctx = {}, config = {}, logger = console) {
  if (!exactInboxTarget(event, ctx, config)) return undefined;
  const prior = recentTerminalGate(event, ctx);
  // message_sending is a generic outbound hook. Without a preceding natural
  // finalization marker, interim/system messages remain untouched.  A
  // terminal-shaped assistant message is different: sending it directly would
  // bypass the canonical close/final path and create the quoted duplicate seen
  // in Inbox.  Fail closed until before_agent_finalize records the exact run.
  if (!prior) {
    if (terminalShapedOutbound(event.content)) {
      return {
        cancel: true,
        cancelReason: "A terminal Inbox response must pass the canonical final-delivery gate.",
      };
    }
    return undefined;
  }
  if (prior.suppressNativeFinal) {
    terminalDeliveryGates.delete(terminalGateKey(event, ctx));
    return {
      cancel: true,
      cancelReason: "A structured final summary was already delivered by the terminal card path.",
    };
  }
  let finalText = String(event.content || "");
  let finalSummary = parseCanonicalFinalSummary(finalText, { expectedModel: prior.expectedModel });
  let formatRecovered = false;
  if (!finalSummary.ok) {
    finalText = buildRecoveryFinalSummary(finalText, prior.expectedModel);
    finalSummary = parseCanonicalFinalSummary(finalText, { expectedModel: prior.expectedModel });
    formatRecovered = finalSummary.ok;
    if (!finalSummary.ok) {
      return {
        cancel: true,
        cancelReason: "The outbound final response could not be normalized safely.",
      };
    }
  }
  if (prior?.ready) {
    terminalDeliveryGates.delete(terminalGateKey(event, ctx));
    return formatRecovered ? { content: finalText } : undefined;
  }
  const retryEvent = prior.runId ? { ...event, runId: prior.runId } : event;
  const receipt = await closeLiveCardBeforeFinal(retryEvent, ctx, {
    ...config,
    terminalStatus: finalSummary.terminalStatus,
    finalSummary: finalText,
  }, logger);
  rememberTerminalGate(retryEvent, ctx, receipt, { formatValid: true, expectedModel: prior.expectedModel });
  if (validTerminalReceipt(receipt)) {
    terminalDeliveryGates.delete(terminalGateKey(event, ctx));
    if (receipt.suppress_native_final === true) {
      return {
        cancel: true,
        cancelReason: "A structured final summary was already delivered by the terminal card path.",
      };
    }
    return undefined;
  }
  return {
    cancel: true,
    cancelReason: "The live work card could not be closed before this final response.",
  };
}

export async function handleInboxEvent(event = {}, ctx = {}, config = {}, logger = console, dispatch = dispatchClaim, hookPhase = "") {
  const decision = inboxDecision(event, ctx, config);
  if (decision === "ignore") return undefined;
  if (decision === "silence") return { handled: true };
  const boundCtx = bindInboundMessage(event, ctx, hookPhase);
  const inboundMessageId = positiveTelegramId(boundCtx.messageId || event.messageId);
  if (!inboundMessageId) {
    logger.error?.("inbox-coordinator: exact Telegram message id unavailable; allowing normal OpenCLAW handling");
    return undefined;
  }
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
    const deliveryTier = Number(receipt.delivery_tier || 0);
    const noCardContract = Boolean(
      receipt.no_card_required === true
      && receipt.lifecycle_writer_enabled === true
      && (
        (deliveryTier === 1 && receipt.reaction_ok === false)
        || (deliveryTier === 2 && receipt.reaction_ok === true)
      )
    );
    const cardContract = Boolean(
      receipt.reaction_ok === true
      && positiveTelegramId(receipt.header_message_id)
      && positiveTelegramId(receipt.live_message_id)
    );
    return Boolean(
      receipt
      && receipt.ok === true
      && receipt.status === "accepted"
      && receipt.agent === "jaimes"
      && receipt.chat_id === target.chatId
      && receipt.thread_id === target.threadId
      && receipt.inbound_message_id === messageId
      && (noCardContract || cardContract)
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
  const surfaceContract = receipt?.surface_contract || "";
  const deliveryTier = Number(receipt?.delivery_tier || 0);
  const noCardRequired = receipt?.no_card_required === true;
  const knownContract = !surfaceContract
    || surfaceContract === "header-live-v1"
    || surfaceContract === "live-only-v2"
    || surfaceContract === "tier-1-final-v3"
    || surfaceContract === "tier-2-final-v3";
  const headerRequired = ![
    "live-only-v2", "tier-1-final-v3", "tier-2-final-v3",
  ].includes(surfaceContract);
  const declarationConsistent = surfaceContract === "live-only-v2"
    ? receipt?.header_required !== true
    : surfaceContract === "tier-1-final-v3" || surfaceContract === "tier-2-final-v3"
      ? receipt?.header_required !== true && noCardRequired
    : surfaceContract === "header-live-v1"
      ? receipt?.header_required !== false
      : receipt?.header_required !== false;
  const tierSurfaceValid = noCardRequired
    ? (
      (deliveryTier === 1 && receipt?.reaction_ok === false)
      || (deliveryTier === 2 && receipt?.reaction_ok === true)
    )
    : (
      (deliveryTier === 0 || deliveryTier === 3)
      && receipt?.reaction_ok === true
      && receipt?.card_start_ok === true
      && positiveTelegramId(receipt?.live_message_id)
      && (!headerRequired || positiveTelegramId(receipt?.header_message_id))
    );
  return Boolean(
    receipt
    && knownContract
    && declarationConsistent
    && receipt.ok === true
    && receipt.status === "queued"
    && tierSurfaceValid
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
    api.on("before_agent_finalize", async (event, ctx) => {
      const config = api.pluginConfig || {};
      return gateTelegramFinalization(event, ctx, config, api.logger);
    }, { priority: 300, timeoutMs: 12_000 });
    api.on("message_sending", async (event, ctx) => {
      const config = api.pluginConfig || {};
      return enforceTelegramFinalDelivery(event, ctx, config, api.logger);
    }, { priority: 300, timeoutMs: 12_000 });
  },
};
