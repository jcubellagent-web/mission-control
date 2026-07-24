import type { AgentId, AgentStatus } from "./types";

export type CanonicalRouteId = "codex" | "antigravity" | "ollama" | "grok";

export type CanonicalRoute = {
  id: CanonicalRouteId;
  label: string;
  providerLabel: string;
  color: string;
  description: string;
};

export const CANONICAL_ROUTES: Record<CanonicalRouteId, CanonicalRoute> = {
  codex: {
    id: "codex",
    label: "Codex",
    providerLabel: "Codex / OpenAI",
    color: "#65D1D5",
    description: "Execution, code, tools, and high-confidence changes.",
  },
  antigravity: {
    id: "antigravity",
    label: "Antigravity",
    providerLabel: "Antigravity / Gemini",
    color: "#72D69A",
    description: "Gemini review, synthesis, long-context reading, and judgment.",
  },
  ollama: {
    id: "ollama",
    label: "Ollama",
    providerLabel: "Ollama",
    color: "#A8ABB3",
    description: "Local drafting, compression, and private low-risk utility.",
  },
  grok: {
    id: "grok",
    label: "Grok",
    providerLabel: "Grok / xAI",
    color: "#1677FF",
    description: "X-native research, current social signals, and verification.",
  },
};

export const CANONICAL_ROUTE_ORDER: CanonicalRouteId[] = ["codex", "antigravity", "ollama", "grok"];

const AGENT_ROUTE_FALLBACK: Record<AgentId, CanonicalRouteId> = {
  joshex: "codex",
  josh2: "codex",
  jaimes: "antigravity",
  jain: "grok",
};

function routeText(values: unknown[]): string {
  return values
    .flatMap((value) => Array.isArray(value) ? value : [value])
    .filter((value) => value != null)
    .map((value) => typeof value === "string" ? value : JSON.stringify(value))
    .join(" ")
    .toLowerCase();
}

export function canonicalRouteIdFromValues(...values: unknown[]): CanonicalRouteId | null {
  const text = routeText(values);
  if (!text.trim()) return null;
  if (/\b(?:grok|xai|x\.ai)\b/.test(text)) return "grok";
  if (/\b(?:antigravity|gemini|google ai|google model)\b/.test(text)) return "antigravity";
  if (/\b(?:ollama|local\/ollama|local model|llama[- _]?\d*|gemma[- _]?\d*|qwen[- _]?\d*|glm[- _]?\d*)\b/.test(text)) return "ollama";
  if (/\b(?:codex|openai|gpt[- _]?\d*)\b/.test(text)) return "codex";
  return null;
}

export function routeForAgentStatus(status: AgentStatus): CanonicalRoute {
  const verified = verifiedRouteForAgentStatus(status);
  if (verified) return verified;
  const explicit = canonicalRouteIdFromValues(
    status.model,
    status.current_tool,
    status.steps?.map((step) => [step.tool, step.kind, step.title, step.label]),
  );
  if (explicit) return CANONICAL_ROUTES[explicit];

  const contextual = canonicalRouteIdFromValues(status.objective, status.detail);
  return CANONICAL_ROUTES[contextual || AGENT_ROUTE_FALLBACK[status.agent_id] || "codex"];
}

export function verifiedRouteForAgentStatus(status: AgentStatus): CanonicalRoute | null {
  if (!status.route_verified) return null;
  const id = canonicalRouteIdFromValues(status.model_family, status.model);
  return id ? CANONICAL_ROUTES[id] : null;
}

export function liveWorkModelLabel(route: CanonicalRoute, modelId?: string | null): string {
  if (route.id === "codex") return "GPT";
  if (route.id === "antigravity") return "Gemini";
  if (route.id === "ollama") return /\bglm(?:[-_.\s]|$)/i.test(String(modelId || "")) ? "GLM" : "Ollama";
  return "Grok";
}

export function routeForProvider(provider: Record<string, unknown>): CanonicalRoute | null {
  const providerIdentity = routeText([provider.id, provider.label, provider.provider]);
  if (/\b(?:openrouter|anthropic|claude)\b/.test(providerIdentity)) return null;
  const id = canonicalRouteIdFromValues(
    provider.id,
    provider.label,
    provider.provider,
    provider.role,
    provider.lastModelUsed,
    provider.model,
  );
  return id ? CANONICAL_ROUTES[id] : null;
}

export function routeCssProperties(route: CanonicalRoute): Record<string, string> {
  return { "--route-color": route.color };
}
