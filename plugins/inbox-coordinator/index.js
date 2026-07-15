import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";

const DEFAULT_CHAT_ID = "-1003589561528";
const DEFAULT_THREAD_ID = "1";
const DEFAULT_MENTIONS = ["@jaimes", "@jain", "@j.a.i.n"];
const GROUP_TOPIC_RE = /telegram:group:(-?\d+):(?:topic:)?(\d+)/i;

function stringValue(value, fallback = "") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
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
  const messageId = stringValue(ctx.messageId || event.messageId) || (event.timestamp ? String(event.timestamp) : "");
  const args = [
    stringValue(config.helperPath, path.join(process.env.HOME || "/Users/josh2.0", ".openclaw", "workspace", "josh_telegram_fast_ack.py")),
    "--claim-inbox",
    "--run-id", stringValue(ctx.runId),
    "--message-id", messageId,
    "--chat-id", target.chatId,
    "--thread-id", target.threadId,
    "--session-key", stringValue(ctx.sessionKey),
  ];
  return args;
}

export function handleInboxEvent(event = {}, ctx = {}, config = {}, logger = console, dispatch = dispatchClaim) {
  const decision = inboxDecision(event, ctx, config);
  if (decision === "ignore") return undefined;
  if (decision === "silence") return { handled: true };
  return dispatch(event, ctx, config, logger) ? { handled: true } : undefined;
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
  try {
    const child = spawn(pythonPath, args, {
      detached: true,
      stdio: ["pipe", "ignore", "ignore"],
      env: process.env,
    });
    child.stdin.end(prompt, "utf8");
    child.unref();
    return true;
  } catch {
    logger.error?.("inbox-coordinator: dispatch failed; allowing normal OpenCLAW handling");
    return false;
  }
}

export default {
  id: "inbox-coordinator",
  name: "Inbox Coordinator",
  description: "Owns untagged Josh 2.0 Inbox messages and dispatches one asynchronous worker.",
  register(api) {
    // #JAIMES: OpenClaw 2026.7.1 sends unbound Topic 1 traffic through global before_dispatch.
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
