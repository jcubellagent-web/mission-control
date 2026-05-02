import type { AgentEvent, AgentId, AgentJob, AgentStatus, Approval, MissionControlState, SignalItem } from "./types";

const CONFIG = window.MC_V2_CONFIG || {};

function normalizeStatus(row: any, fallbackAgent: AgentId = "joshex"): AgentStatus | null {
  if (!row) return null;
  if (row.agent_id) return row as AgentStatus;
  return {
    agent_id: fallbackAgent,
    status: row.status || "info",
    objective: row.objective || "No objective reported",
    detail: row.detail || row.summary || "",
    current_tool: row.currentTool || row.current_tool || "",
    active: Boolean(row.active),
    updated_at: row.updatedAt || row.checkedAt || row.updated_at || "",
    steps: row.steps || [],
  };
}

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json() as Promise<T>;
}

async function supabaseFetch<T>(path: string): Promise<T> {
  const response = await fetch(`${CONFIG.supabaseUrl}/rest/v1/${path}`, {
    headers: {
      apikey: CONFIG.supabaseKey || "",
      Authorization: `Bearer ${CONFIG.supabaseKey || ""}`,
    },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Supabase ${path}: ${response.status}`);
  return response.json() as Promise<T>;
}

function dedupeStatus(rows: Array<AgentStatus | null>): AgentStatus[] {
  const byAgent = new Map<AgentId, AgentStatus>();
  for (const row of rows) {
    if (!row) continue;
    const existing = byAgent.get(row.agent_id);
    if (!existing || timestampValue(row.updated_at) >= timestampValue(existing.updated_at)) {
      byAgent.set(row.agent_id, row);
    }
  }
  return [...byAgent.values()];
}

function timestampValue(value?: string | null): number {
  if (!value) return 0;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function mergeStatuses(primary: AgentStatus[], fallback: AgentStatus[]): AgentStatus[] {
  return dedupeStatus([...primary, ...fallback]).sort((a, b) => timestampValue(b.updated_at) - timestampValue(a.updated_at));
}

function mergeJobs(primary: AgentJob[], fallback: AgentJob[]): AgentJob[] {
  const rows = new Map<string, AgentJob>();
  for (const job of [...primary, ...fallback]) {
    if (!job?.title) continue;
    const key = job.id || `${job.agent_id}-${job.title}`;
    const existing = rows.get(key);
    if (!existing || timestampValue(job.updated_at) >= timestampValue(existing.updated_at)) {
      rows.set(key, job);
    }
  }
  return [...rows.values()]
    .sort((a, b) => timestampValue(b.updated_at) - timestampValue(a.updated_at))
    .slice(0, 24);
}

async function loadFromSupabase(): Promise<MissionControlState> {
  const [statuses, events, jobs, approvals, sidecars] = await Promise.all([
    supabaseFetch<AgentStatus[]>("mc_v2_agent_status?select=*&order=updated_at.desc"),
    supabaseFetch<AgentEvent[]>("mc_v2_events?select=*&order=created_at.desc&limit=40"),
    supabaseFetch<AgentJob[]>("mc_v2_jobs?select=*&order=updated_at.desc&limit=24"),
    supabaseFetch<Approval[]>("mc_v2_approvals?select=*&risk_tier=eq.dashboard-safe&order=created_at.desc&limit=20"),
    loadSidecars(),
  ]);
  const fallback = await loadFallback();
  const normalizedStatuses = statuses.map((row) => normalizeStatus(row)).filter(Boolean) as AgentStatus[];
  const mergedJobs = mergeJobs(jobs, fallback.jobs);
  return {
    source: jobs.length && fallback.jobs.length ? "Supabase v2 + local jobs" : jobs.length ? "Supabase v2" : "Local jobs fallback",
    statuses: normalizedStatuses.length ? mergeStatuses(normalizedStatuses, fallback.statuses) : fallback.statuses,
    events: events.length ? events : fallback.events,
    jobs: mergedJobs.length ? mergedJobs : fallback.jobs,
    approvals: approvals.length ? approvals : fallback.approvals,
    modelUsage: sidecars.modelUsage || fallback.modelUsage,
    signals: sidecars.signals.length ? sidecars.signals : fallback.signals,
  };
}

async function loadFallback(): Promise<MissionControlState> {
  const [brain, personal, dashboard, sidecars] = await Promise.all([
    fetchJson<any>("/data/brain-feed.json").catch(() => null),
    fetchJson<any>("/data/personal-codex.json").catch(() => null),
    fetchJson<any>("/data/dashboard-data.json").catch(() => null),
    loadSidecars(),
  ]);
  const statuses = dedupeStatus([
    normalizeStatus(brain, "joshex"),
    normalizeStatus({
      agent_id: "joshex",
      status: personal?.status || "info",
      objective: personal?.objective || "Personal Codex",
      detail: personal?.summary || "",
      current_tool: "local sidecar",
      active: personal?.status === "active",
      updated_at: personal?.updatedAt,
      steps: (personal?.recentActivity || []).slice(0, 6).map((item: any) => ({
        label: item.event,
        status: "done",
        tool: "personal-codex.json",
      })),
    }),
    normalizeStatus(dashboard?.jaimesBrainFeed && { ...dashboard.jaimesBrainFeed, agent_id: "jaimes" }, "jaimes"),
    normalizeStatus(dashboard?.jainBrainFeed && { ...dashboard.jainBrainFeed, agent_id: "jain" }, "jain"),
  ]);
  const events = (dashboard?.recentActivity || []).slice(0, 16).map((event: any, index: number) => ({
    id: `fallback-event-${index}`,
    agent_id: "joshex",
    event_type: "note",
    status: "info",
    title: event.event || event.title || "Recent activity",
    detail: event.detail || "",
    tool: "dashboard-data.json",
    created_at: event.time || dashboard?.generatedAt || "",
  }));
  const approvals = (dashboard?.actionRequired || []).slice(0, 8).map((item: any, index: number) => ({
    id: `fallback-approval-${index}`,
    agent_id: "joshex",
    title: item.title,
    detail: item.priority || "",
    requested_by: "joshex",
    status: "pending",
    risk_tier: "dashboard-safe",
    created_at: dashboard?.generatedAt || "",
  }));
  return {
    source: "Local v1 fallback",
    statuses,
    events,
    jobs: buildFallbackJobs(dashboard),
    approvals,
    modelUsage: dashboard?.modelUsage || sidecars.modelUsage,
    signals: sidecars.signals,
  };
}

function ownerToAgentId(owner?: string): AgentId {
  const text = String(owner || "").toLowerCase();
  if (text.includes("jaimes")) return "jaimes";
  if (text.includes("j.a.i.n") || text.includes("jain")) return "jain";
  if (text.includes("joshex") || text.includes("codex")) return "joshex";
  return "josh";
}

function buildFallbackJobs(dashboard: any): AgentJob[] {
  const codexJobs = Array.isArray(dashboard?.codexJobs) ? dashboard.codexJobs : [];
  const crons = Array.isArray(dashboard?.crons) ? dashboard.crons : [];
  const rows: AgentJob[] = [];

  for (const job of codexJobs) {
    rows.push({
      id: String(job.id || `${job.owner || "job"}-${job.title || rows.length}`),
      agent_id: ownerToAgentId(job.owner || job.agent),
      title: job.title || job.name || "Mission Control job",
      status: job.status || "info",
      detail: job.detail || job.description || job.tool || "",
      tool: job.tool || "codex-jobs",
      started_at: job.started_at || job.startedAt || null,
      completed_at: job.completed_at || job.completedAt || null,
      updated_at: job.updated_at || job.updatedAt || job.time || dashboard?.generatedAt || "",
    });
  }

  for (const cron of crons.filter((item: any) => item?.todayRelevant).slice(0, 12)) {
    rows.push({
      id: `cron-${cron.name || rows.length}`,
      agent_id: ownerToAgentId(cron.agent),
      title: cron.name || "Scheduled job",
      status: cron.status || cron.runStatus || "scheduled",
      detail: cron.description || cron.schedule || "",
      tool: cron.sourceLabel || cron.source || "scheduled job",
      started_at: null,
      completed_at: null,
      updated_at: cron.lastRun || dashboard?.generatedAt || "",
    });
  }

  return rows
    .filter((row) => row.title)
    .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")))
    .slice(0, 24);
}

export async function loadMissionControl(): Promise<MissionControlState> {
  if (CONFIG.supabaseUrl && CONFIG.supabaseKey) {
    try {
      return await loadFromSupabase();
    } catch (error) {
      console.warn(error);
    }
  }
  return loadFallback();
}

async function loadSidecars(): Promise<{ modelUsage?: MissionControlState["modelUsage"]; signals: SignalItem[] }> {
  const [modelUsage, jainBreaking, breaking, jainNewsfeed, newsfeed] = await Promise.all([
    fetchJson<MissionControlState["modelUsage"]>("/data/modelUsage.json").catch(() => undefined),
    fetchJson<any>("/data/jain-breaking-highlights.json").catch(() => null),
    fetchJson<any>("/data/breaking-highlights.json").catch(() => null),
    fetchJson<any>("/data/jain-newsfeed.json").catch(() => null),
    fetchJson<any>("/data/newsfeed.json").catch(() => null),
  ]);
  return {
    modelUsage,
    signals: buildSignals(jainBreaking || breaking, jainNewsfeed || newsfeed),
  };
}

function buildSignals(highlights: any, feed: any): SignalItem[] {
  const rows: SignalItem[] = [];
  for (const item of Array.isArray(highlights?.items) ? highlights.items : []) {
    rows.push({
      id: item.id || item.url || item.title,
      label: item.label || "Signal",
      title: item.title || "Untitled signal",
      reason: item.reason || "",
      source: item.source || "J.A.I.N",
      score: item.score,
      time: item.sentAt || item.displayTime || highlights.updatedAt,
      url: item.url,
    });
  }
  for (const item of Array.isArray(feed?.signal) ? feed.signal : []) {
    rows.push({
      id: item.url || item.title,
      label: item.category || "Signal",
      title: item.title || item.headline || "Untitled signal",
      reason: item.reason || item.insight || "",
      source: item.source || "Intelligence Feed",
      score: item.score,
      time: item.published || feed.generatedAt,
      url: item.url,
    });
  }
  for (const text of Array.isArray(feed?.tldr) ? feed.tldr : []) {
    rows.push({
      id: `tldr-${text}`,
      label: "TLDR",
      title: text,
      reason: "Intelligence feed summary",
      source: "J.A.I.N",
      time: feed.generatedAt,
    });
  }
  const seen = new Set<string>();
  return rows.filter((row) => {
    const key = row.id || row.title;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 12);
}
