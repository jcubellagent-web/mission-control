import type { AgentId } from "./types";

export const AGENT_IDS = ["joshex", "josh", "jaimes", "jain"] as const;

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

export function isAgentId(value: unknown): value is AgentId {
  return typeof value === "string" && (AGENT_IDS as readonly string[]).includes(value);
}

export function stringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.length ? value : fallback;
}

export function booleanValue(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") return ["true", "1", "yes", "active", "working"].includes(value.toLowerCase());
  return Boolean(value);
}

export function arrayValue<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

export function recordValue(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}
