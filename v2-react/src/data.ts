import { arrayValue, booleanValue, isAgentId, isRecord, recordValue, stringValue } from "./dataAdapters";
import type { AgentEvent, AgentId, AgentJob, AgentStatus, Approval, MissionControlState, SignalItem } from "./types";

const CONFIG = window.MC_V2_CONFIG || {};
const REALTIME_TABLES = ["brain_feed", "mc_v2_agent_status", "mc_v2_events", "mc_v2_jobs", "mc_v2_approvals"];
const JOB_ROW_LIMIT = 64;
const LIVE_ROW_WINDOW_MS = 2 * 60 * 60 * 1000;
const STALE_BLOCKER_WINDOW_MS = 6 * 60 * 60 * 1000;
const BRAIN_FEED_TRUTH_WINDOW_MS = 12 * 60 * 60 * 1000;
const AGENT_STATUS_ORDER: AgentId[] = ["joshex", "josh", "jaimes", "jain"];

function normalizeSteps(value: unknown): AgentStatus["steps"] {
  return arrayValue(value).filter(isRecord).map((step) => ({
    label: stringValue(step.label),
    title: stringValue(step.title),
    status: stringValue(step.status),
    tool: stringValue(step.tool),
    kind: stringValue(step.kind),
  }));
}

function normalizeStatus(row: unknown, fallbackAgent: AgentId = "joshex"): AgentStatus | null {
  if (!isRecord(row)) return null;
  const rowAgent = String(row.agent_id || "").toLowerCase();
  const agent_id = isAgentId(rowAgent) ? rowAgent : fallbackAgent;
  return {
    agent_id,
    status: stringValue(row.status, "info"),
    objective: stringValue(row.objective, "No objective reported"),
    detail: stringValue(row.detail, stringValue(row.summary)),
    current_tool: stringValue(row.currentTool, stringValue(row.current_tool)),
    active: booleanValue(row.active),
    updated_at: stringValue(row.updatedAt, stringValue(row.checkedAt, stringValue(row.updated_at))),
    steps: normalizeSteps(row.steps),
  };
}

function normalizeBrainFeedRow(row: unknown): AgentStatus | null {
  if (!isRecord(row)) return null;
  const data = recordValue(row.data);
  if (!Object.keys(data).length) return null;
  const id = String(row.id || data.agentId || "").toLowerCase();
  const fallbackAgent: AgentId = id === "main" ? "josh" : id === "josh2" ? "josh" : id as AgentId;
  if (!["joshex", "josh", "jaimes", "jain"].includes(fallbackAgent)) return null;
  return normalizeStatus(
    {
      ...data,
      agent_id: data.agent_id || data.agentId || fallbackAgent,
      updated_at: data.updated_at || data.updatedAt || row.updated_at,
    },
    fallbackAgent,
  );
}

function normalizeGeneratedAgentFeed(row: unknown, agent: AgentId): AgentStatus | null {
  if (!isRecord(row)) return null;
  return normalizeStatus({ ...row, agent_id: agent }, agent);
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

function canonicalJobText(value?: string | null): string {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function hasScheduledJobFingerprint(job: AgentJob): boolean {
  return Boolean(
    job.todayRelevant
    || job.schedule
    || job.sourceLabel
    || job.nextRun
    || job.lastRun
    || String(job.id || "").startsWith("cron-"),
  );
}

function jobMergeKey(job: AgentJob): string {
  const owner = job.agent_id || "agent";
  const title = canonicalJobText(job.title);
  if (!hasScheduledJobFingerprint(job)) {
    return `live-${owner}-${title}`;
  }
  const id = canonicalJobText(job.id);
  if (id) return `scheduled-${id}`;
  return `scheduled-${owner}-${title}-${canonicalJobText(job.schedule || job.sourceLabel || job.tool)}`;
}

function jobStatusText(job: AgentJob): string {
  return String(job.status || job.runStatus || "").toLowerCase();
}

function isBlockingJobStatus(job: AgentJob): boolean {
  return ["blocked", "error", "failed"].includes(jobStatusText(job));
}

function isClearJobStatus(job: AgentJob): boolean {
  return ["done", "ok", "info", "complete", "completed", "ready"].includes(jobStatusText(job));
}

function jobTopicTokens(job: AgentJob): Set<string> {
  const stopWords = new Set([
    "agent",
    "blocked",
    "checked",
    "complete",
    "completed",
    "done",
    "error",
    "failed",
    "josh",
    "monitor",
    "ready",
    "status",
    "shared",
  ]);
  return new Set(
    canonicalJobText(`${job.title} ${job.detail} ${job.tool}`)
      .split(" ")
      .filter((token) => token.length > 3 && !stopWords.has(token)),
  );
}

function jobTopicsOverlap(a: AgentJob, b: AgentJob): boolean {
  const aTokens = jobTopicTokens(a);
  const bTokens = jobTopicTokens(b);
  let shared = 0;
  for (const token of aTokens) {
    if (bTokens.has(token)) shared += 1;
  }
  return shared >= 1;
}

function blockedJobSuperseded(job: AgentJob, jobs: AgentJob[], now: number): boolean {
  const updated = timestampValue(job.updated_at);
  if (!isBlockingJobStatus(job) || !updated || now - updated <= STALE_BLOCKER_WINDOW_MS) return false;
  return jobs.some((other) => {
    if (other === job || other.agent_id !== job.agent_id) return false;
    const otherUpdated = timestampValue(other.updated_at);
    return otherUpdated > updated && isClearJobStatus(other) && jobTopicsOverlap(job, other);
  });
}

function statusByFreshestRow(rows: AgentStatus[]): Map<AgentId, AgentStatus> {
  const byAgent = new Map<AgentId, AgentStatus>();
  for (const row of rows) {
    const existing = byAgent.get(row.agent_id);
    if (!existing || timestampValue(row.updated_at) >= timestampValue(existing.updated_at)) {
      byAgent.set(row.agent_id, row);
    }
  }
  return byAgent;
}

function isFreshBrainFeedTruth(row?: AgentStatus): boolean {
  if (!row) return false;
  const stamp = timestampValue(row.updated_at);
  return Boolean(stamp) && Date.now() - stamp <= BRAIN_FEED_TRUTH_WINDOW_MS;
}

function mergeStatuses(visibleBrainFeed: AgentStatus[], primary: AgentStatus[], fallback: AgentStatus[]): AgentStatus[] {
  const visibleByAgent = statusByFreshestRow(visibleBrainFeed);
  const primaryByAgent = statusByFreshestRow(primary);
  const fallbackByAgent = statusByFreshestRow(fallback);
  const agents = new Set<AgentId>([
    ...AGENT_STATUS_ORDER,
    ...visibleByAgent.keys(),
    ...primaryByAgent.keys(),
    ...fallbackByAgent.keys(),
  ]);
  const rows: AgentStatus[] = [];
  for (const agent of agents) {
    const visible = visibleByAgent.get(agent);
    const primaryRow = primaryByAgent.get(agent);
    const fallbackRow = fallbackByAgent.get(agent);
    const fallbackFreshActive = fallbackRow && Boolean(fallbackRow.active) && isFreshBrainFeedTruth(fallbackRow);
    if (visible && (isFreshBrainFeedTruth(visible) || !primaryRow)) {
      rows.push(visible);
    } else if (fallbackFreshActive && (!primaryRow || !primaryRow.active)) {
      rows.push(fallbackRow);
    } else if (primaryRow) {
      rows.push(primaryRow);
    } else if (visible) {
      rows.push(visible);
    } else if (fallbackRow) {
      rows.push(fallbackRow);
    }
  }
  return rows.sort((a, b) => {
    const orderDelta = AGENT_STATUS_ORDER.indexOf(a.agent_id) - AGENT_STATUS_ORDER.indexOf(b.agent_id);
    if (orderDelta) return orderDelta;
    return timestampValue(b.updated_at) - timestampValue(a.updated_at);
  });
}

function priorityJobRank(job: AgentJob): number {
  const text = `${job.title} ${job.tool} ${job.agent_id}`.toLowerCase();
  if (/personal gmail|gmail morning|gmail inbox|gmail triage|email triage|mail triage|inbox triage|inbox review|unread email/.test(text)) return 3;
  if (/sorare/.test(text)) return 2;
  if (/fantasy|waiver|roster|lineup|pitcher|baseball/.test(text)) return 1;
  return 0;
}

function isLowSignalApproval(row: Approval): boolean {
  const text = `${row.title} ${row.detail} ${row.id}`.toLowerCase();
  return /smoke|test|v2 handoff/.test(text);
}

function mergeJobs(primary: AgentJob[], fallback: AgentJob[]): AgentJob[] {
  const rows = new Map<string, AgentJob>();
  for (const job of [...primary, ...fallback]) {
    if (!job?.title) continue;
    const key = jobMergeKey(job);
    const existing = rows.get(key);
    if (!existing || timestampValue(job.updated_at) >= timestampValue(existing.updated_at)) {
      rows.set(key, job);
    }
  }
  return [...rows.values()]
    .sort((a, b) => {
      const rankDelta = priorityJobRank(b) - priorityJobRank(a);
      if (rankDelta) return rankDelta;
      return timestampValue(b.updated_at) - timestampValue(a.updated_at);
    })
    .slice(0, JOB_ROW_LIMIT);
}

function selectLiveSupabaseJobs(jobs: AgentJob[]): AgentJob[] {
  const now = Date.now();
  const liveRows = jobs.filter((job) => {
    const updated = timestampValue(job.updated_at);
    if (isBlockingJobStatus(job)) return !blockedJobSuperseded(job, jobs, now);
    if (!["active", "running", "queued"].includes(jobStatusText(job))) return false;
    return Boolean(updated) && now - updated <= LIVE_ROW_WINDOW_MS;
  });
  const rows = new Map<string, AgentJob>();
  for (const job of liveRows) {
    const key = jobMergeKey(job);
    const existing = rows.get(key);
    if (!existing || timestampValue(job.updated_at) >= timestampValue(existing.updated_at)) {
      rows.set(key, job);
    }
  }
  return [...rows.values()]
    .sort((a, b) => timestampValue(b.updated_at) - timestampValue(a.updated_at))
    .slice(0, 8);
}

async function loadFromSupabase(): Promise<MissionControlState> {
  const [statuses, brainFeedRows, events, jobs, approvals, sidecars] = await Promise.all([
    supabaseFetch<AgentStatus[]>("mc_v2_agent_status?select=*&order=updated_at.desc"),
    supabaseFetch<any[]>("brain_feed?select=id,data,updated_at&id=in.(joshex,josh,josh2,main,jaimes,jain)").catch(() => []),
    supabaseFetch<AgentEvent[]>("mc_v2_events?select=*&order=created_at.desc&limit=40"),
    supabaseFetch<AgentJob[]>(`mc_v2_jobs?select=*&order=updated_at.desc&limit=${JOB_ROW_LIMIT}`),
    supabaseFetch<Approval[]>("mc_v2_approvals?select=*&risk_tier=eq.dashboard-safe&order=created_at.desc&limit=20"),
    loadSidecars(),
  ]);
  const fallback = await loadFallback();
  const normalizedStatuses = statuses.map((row) => normalizeStatus(row)).filter(Boolean) as AgentStatus[];
  const brainFeedStatuses = brainFeedRows.map((row) => normalizeBrainFeedRow(row)).filter(Boolean) as AgentStatus[];
  const mergedJobs = mergeJobs(selectLiveSupabaseJobs(jobs), fallback.jobs);
  const visibleApprovals = approvals.filter((row) => row.status === "pending" && !isLowSignalApproval(row));
  return {
    source: jobs.length && fallback.jobs.length ? "Supabase + local jobs" : jobs.length ? "Supabase" : "Local jobs fallback",
    statuses: normalizedStatuses.length || brainFeedStatuses.length
      ? mergeStatuses(brainFeedStatuses, normalizedStatuses, fallback.statuses)
      : fallback.statuses,
    events: events.length ? events : fallback.events,
    jobs: mergedJobs.length ? mergedJobs : fallback.jobs,
    approvals: visibleApprovals,
    agenticCrypto: sidecars.agenticCrypto || fallback.agenticCrypto,
    modelUsage: sidecars.modelUsage || fallback.modelUsage,
    modelRouter: fallback.modelRouter,
    reliabilityUpgrades: sidecars.reliabilityUpgrades || fallback.reliabilityUpgrades,
    capabilityStack: fallback.capabilityStack,
    capabilityInventory: fallback.capabilityInventory,
    capabilityWatch: fallback.capabilityWatch,
    runtimeLayout: sidecars.runtimeLayout || fallback.runtimeLayout,
    signalHealth: sidecars.signalHealth || fallback.signalHealth,
    signals: sidecars.signals.length ? sidecars.signals : fallback.signals,
  };
}

async function loadFallback(): Promise<MissionControlState> {
  const [brain, joshexBrain, jaimesBrain, jainBrain, personal, dashboard, sidecars] = await Promise.all([
    fetchJson<any>("/data/brain-feed.json").catch(() => null),
    fetchJson<any>("/data/joshex-brain-feed.json").catch(() => null),
    fetchJson<any>("/data/jaimes-brain-feed.json").catch(() => null),
    fetchJson<any>("/data/jain-brain-feed.json").catch(() => null),
    fetchJson<any>("/data/personal-codex.json").catch(() => null),
    fetchJson<any>("/data/dashboard-data.json").catch(() => null),
    loadSidecars(),
  ]);
  const brainAgent = String(brain?.agentId || brain?.agent_id || brain?.agent || "").toLowerCase();
  const brainAgentId: AgentId = brainAgent.includes("josh") && !brainAgent.includes("joshex")
    ? "josh"
    : brainAgent.includes("jaimes")
    ? "jaimes"
    : brainAgent.includes("jain")
    ? "jain"
    : "joshex";
  const sharedEvents = Array.isArray(dashboard?.sharedEvents) ? dashboard.sharedEvents : [];
  const latestJoshexEvent = sharedEvents
    .filter((event: any) => String(event?.agent || "").toLowerCase() === "joshex")
    .sort((a: any, b: any) => timestampValue(b?.time) - timestampValue(a?.time))[0];
  const joshexFallbackStatus = latestJoshexEvent
    ? {
        agent_id: "joshex",
        status: ["active", "queued", "blocked", "error"].includes(String(latestJoshexEvent.status || "").toLowerCase())
          ? latestJoshexEvent.status
          : "ready",
        objective: latestJoshexEvent.title || "JOSHeX local update",
        detail: latestJoshexEvent.detail || "",
        current_tool: latestJoshexEvent.tool || "local Brain Feed event",
        active: ["active", "queued"].includes(String(latestJoshexEvent.status || "").toLowerCase()),
        updated_at: latestJoshexEvent.time,
        steps: [{
          label: latestJoshexEvent.title || "JOSHeX local update",
          status: latestJoshexEvent.status || "ready",
          tool: latestJoshexEvent.tool || "shared-events.json",
          kind: latestJoshexEvent.type || latestJoshexEvent.event_type || "shared-event",
        }],
      }
    : {
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
      };
  const statuses = dedupeStatus([
    normalizeGeneratedAgentFeed(dashboard?.agentBrainFeeds?.joshex, "joshex"),
    normalizeGeneratedAgentFeed(dashboard?.agentBrainFeeds?.josh, "josh"),
    normalizeGeneratedAgentFeed(dashboard?.agentBrainFeeds?.jaimes, "jaimes"),
    normalizeGeneratedAgentFeed(dashboard?.agentBrainFeeds?.jain, "jain"),
    normalizeStatus(brain, brainAgentId),
    normalizeStatus(joshexBrain && { ...joshexBrain, agent_id: "joshex" }, "joshex"),
    normalizeStatus(joshexFallbackStatus),
    normalizeStatus(jaimesBrain && { ...jaimesBrain, agent_id: "jaimes" }, "jaimes"),
    normalizeStatus(jainBrain && { ...jainBrain, agent_id: "jain" }, "jain"),
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
    detail: item.detail || item.title || "",
    requested_by: "joshex",
    status: "pending",
    risk_tier: item.priority || "dashboard-safe",
    created_at: dashboard?.generatedAt || "",
  }));
  return {
    source: "Josh 2.0 local live feed",
    statuses,
    events,
    jobs: buildFallbackJobs(dashboard),
    approvals,
    agenticCrypto: sidecars.agenticCrypto,
    modelUsage: dashboard?.modelUsage || sidecars.modelUsage,
    modelRouter: dashboard?.modelRouter,
    reliabilityUpgrades: dashboard?.reliabilityUpgrades || sidecars.reliabilityUpgrades,
    capabilityStack: dashboard?.capabilityStack,
    capabilityInventory: dashboard?.capabilityInventory,
    capabilityWatch: dashboard?.capabilityWatch,
    runtimeLayout: sidecars.runtimeLayout || dashboard?.runtimeLayout,
    signalHealth: sidecars.signalHealth,
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
      title: job.title || job.name || "Control Tower job",
      status: job.status || "info",
      detail: job.detail || job.description || job.tool || "",
      tool: job.tool || "codex-jobs",
      started_at: job.started_at || job.startedAt || null,
      completed_at: job.completed_at || job.completedAt || null,
      updated_at: job.updated_at || job.updatedAt || job.time || dashboard?.generatedAt || "",
    });
  }

  const activeCrons = crons.filter((item: any) => {
    const status = String(item?.runStatus || item?.status || "").toLowerCase();
    return ["active", "running", "queued"].includes(status);
  });
  const priorityCrons = crons.filter((item: any) => priorityJobRank({
    id: "",
    agent_id: ownerToAgentId(item?.agent),
    title: item?.name || "",
    status: item?.status || item?.runStatus || "",
    detail: item?.description || item?.schedule || "",
    tool: item?.sourceLabel || item?.source || "",
    updated_at: item?.lastRun || dashboard?.generatedAt || "",
  }));
  const dailyCrons = crons.filter((item: any) => {
    const status = String(item?.status || "").toLowerCase();
    const runStatus = String(item?.runStatus || "").toLowerCase();
    if (status === "paused" || runStatus === "paused") return false;
    return item?.todayRelevant;
  });
  const selectedCrons = new Map<string, any>();
  for (const cron of [...activeCrons, ...priorityCrons, ...dailyCrons]) {
    selectedCrons.set(String(cron?.name || selectedCrons.size), cron);
  }

  for (const cron of selectedCrons.values()) {
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
      schedule: cron.schedule || "",
      sourceLabel: cron.sourceLabel || cron.source || "",
      runStatus: cron.runStatus || "",
      lastRun: cron.lastRun || undefined,
      nextRun: cron.nextRun || undefined,
      verifiedToday: Boolean(cron.verifiedToday),
      todayRelevant: Boolean(cron.todayRelevant),
    });
  }

  return rows
    .filter((row) => row.title)
    .sort((a, b) => {
      const rankDelta = priorityJobRank(b) - priorityJobRank(a);
      if (rankDelta) return rankDelta;
      return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
    })
    .slice(0, JOB_ROW_LIMIT);
}

export async function loadMissionControl(): Promise<MissionControlState> {
  if (CONFIG.supabaseMode === "primary" && CONFIG.supabaseUrl && CONFIG.supabaseKey) {
    try {
      return await loadFromSupabase();
    } catch (error) {
      console.warn(error);
    }
  }
  return loadFallback();
}

function subscribeLocalLiveFeed(onChange: () => void, onState?: (state: "connected" | "polling") => void) {
  if (typeof EventSource === "undefined") {
    onState?.("polling");
    return null;
  }

  let closed = false;
  let pending = false;
  const events = new EventSource("/events/mission-control");
  const scheduleRefresh = () => {
    if (pending) return;
    pending = true;
    window.setTimeout(() => {
      pending = false;
      if (!closed) onChange();
    }, 150);
  };

  events.addEventListener("open", () => {
    if (closed) return;
    onState?.("connected");
  });
  events.addEventListener("mission-control", scheduleRefresh);
  events.addEventListener("error", () => {
    if (!closed) onState?.("polling");
  });

  return () => {
    closed = true;
    events.close();
  };
}

export function subscribeMissionControlRealtime(onChange: () => void, onState?: (state: "connected" | "polling") => void) {
  if (CONFIG.supabaseMode !== "primary") {
    const unsubscribeLocal = subscribeLocalLiveFeed(onChange, onState);
    if (unsubscribeLocal) return unsubscribeLocal;
  }

  if (!CONFIG.supabaseUrl || !CONFIG.supabaseKey || typeof WebSocket === "undefined") {
    onState?.("polling");
    return () => {};
  }
  const url = `${CONFIG.supabaseUrl.replace(/^http/i, "ws")}/realtime/v1/websocket?apikey=${encodeURIComponent(CONFIG.supabaseKey)}&vsn=1.0.0`;
  const socket = new WebSocket(url);
  const topic = "realtime:mission-control";
  let ref = 0;
  let closed = false;
  let heartbeat: number | undefined;
  let pending = false;

  const send = (event: string, payload: Record<string, unknown>, target = topic) => {
    if (socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ topic: target, event, payload, ref: String(++ref) }));
  };
  const scheduleRefresh = () => {
    if (pending) return;
    pending = true;
    window.setTimeout(() => {
      pending = false;
      onChange();
    }, 250);
  };

  socket.addEventListener("open", () => {
    if (closed) return;
    send("phx_join", {
      config: {
        broadcast: { self: false },
        presence: { key: "" },
        postgres_changes: REALTIME_TABLES.map((table) => ({ event: "*", schema: "public", table })),
      },
      access_token: CONFIG.supabaseKey,
    });
    heartbeat = window.setInterval(() => send("heartbeat", {}, "phoenix"), 25_000);
  });
  socket.addEventListener("message", (event) => {
    try {
      const message = JSON.parse(event.data);
      if (message.event === "phx_reply" && message.payload?.status === "ok") onState?.("connected");
      if (message.event === "postgres_changes") scheduleRefresh();
    } catch {
      // Ignore malformed realtime frames and keep polling as fallback.
    }
  });
  socket.addEventListener("close", () => {
    if (heartbeat) window.clearInterval(heartbeat);
    if (!closed) onState?.("polling");
  });
  socket.addEventListener("error", () => onState?.("polling"));

  return () => {
    closed = true;
    if (heartbeat) window.clearInterval(heartbeat);
    if (socket.readyState === WebSocket.OPEN) send("phx_leave", {});
    socket.close();
  };
}

async function loadSidecars(): Promise<{
  agenticCrypto?: MissionControlState["agenticCrypto"];
  modelUsage?: MissionControlState["modelUsage"];
  reliabilityUpgrades?: MissionControlState["reliabilityUpgrades"];
  runtimeLayout?: MissionControlState["runtimeLayout"];
  signalHealth?: MissionControlState["signalHealth"];
  signals: SignalItem[];
}> {
  const [agenticCrypto, modelUsage, reliabilityUpgrades, runtimeLayout, signalHealth, dailySignals, jainBreaking, breaking, jainNewsfeed, newsfeed] = await Promise.all([
    fetchJson<MissionControlState["agenticCrypto"]>("/data/agentic-crypto-wallet.json").catch(() => undefined),
    fetchJson<MissionControlState["modelUsage"]>("/data/modelUsage.json").catch(() => undefined),
    fetchJson<MissionControlState["reliabilityUpgrades"]>("/data/reliability-upgrades.json").catch(() => undefined),
    fetchJson<MissionControlState["runtimeLayout"]>("/data/mission-control-runtime-layout.json").catch(() => undefined),
    fetchJson<MissionControlState["signalHealth"]>("/data/jain-signal-health.json").catch(() => undefined),
    fetchJson<any>("/data/jain-daily-signals.json").catch(() => null),
    fetchJson<any>("/data/jain-breaking-highlights.json").catch(() => null),
    fetchJson<any>("/data/breaking-highlights.json").catch(() => null),
    fetchJson<any>("/data/jain-newsfeed.json").catch(() => null),
    fetchJson<any>("/data/newsfeed.json").catch(() => null),
  ]);
  return {
    agenticCrypto,
    modelUsage,
    reliabilityUpgrades,
    runtimeLayout,
    signalHealth: signalHealth || recordValue(dailySignals).signalHealth as MissionControlState["signalHealth"],
    signals: buildSignals(dailySignals, jainBreaking || breaking, jainNewsfeed || newsfeed),
  };
}

function isTodayEt(value?: string): boolean {
  if (!value) return false;
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return false;
  const day = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(parsed);
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  return day === today;
}

function signalSort(a: SignalItem, b: SignalItem): number {
  const scoreDelta = (b.score || 0) - (a.score || 0);
  if (scoreDelta) return scoreDelta;
  return timestampValue(b.time) - timestampValue(a.time);
}

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function impactScenarios(value: unknown): SignalItem["impactScenarios"] | undefined {
  if (!isRecord(value)) return undefined;
  return {
    low: stringValue(value.low),
    medium: stringValue(value.medium),
    med: stringValue(value.med),
    high: stringValue(value.high),
  };
}

function buildSignals(daily: unknown, highlights: unknown, feed: unknown): SignalItem[] {
  const dailyRecord = recordValue(daily);
  const dailyItems = arrayValue(dailyRecord.items).filter(isRecord);
  if (dailyItems.length) {
    return dailyItems.slice(0, 10).map((item) => ({
      id: stringValue(item.id, stringValue(item.url, stringValue(item.title))),
      label: stringValue(item.label, "Daily Signal"),
      title: stringValue(item.title, "Untitled signal"),
      reason: stringValue(item.reason),
      impact: stringValue(item.impact),
      impactScenarios: impactScenarios(item.impactScenarios),
      kind: stringValue(item.kind, "signal"),
      source: stringValue(item.source, "J.A.I.N"),
      score: numberValue(item.score),
      time: stringValue(item.time, stringValue(dailyRecord.generatedAt)),
      url: stringValue(item.url),
      section: stringValue(item.section),
      sectionLabel: stringValue(item.sectionLabel),
      rank: numberValue(item.rank),
    }));
  }
  const rows: SignalItem[] = [];
  const highlightRecord = recordValue(highlights);
  for (const item of arrayValue(highlightRecord.items).filter(isRecord)) {
    const time = stringValue(item.sentAt, stringValue(item.displayTime, stringValue(highlightRecord.updatedAt)));
    if (!isTodayEt(time)) continue;
    const impact = recordValue(item.impact);
    rows.push({
      id: stringValue(item.id, stringValue(item.url, stringValue(item.title))),
      label: stringValue(item.label, "Signal"),
      title: stringValue(item.title, "Untitled signal"),
      reason: stringValue(item.reason),
      impact: Object.keys(impact).length
        ? `Low: ${stringValue(impact.low)} Med: ${stringValue(impact.medium, stringValue(impact.med))} High: ${stringValue(impact.high)}`
        : "",
      impactScenarios: impactScenarios(item.impact),
      kind: "breaking",
      source: stringValue(item.source, "J.A.I.N"),
      score: numberValue(item.score),
      time,
      url: stringValue(item.url),
    });
  }
  const feedRecord = recordValue(feed);
  for (const item of arrayValue(feedRecord.signal).filter(isRecord)) {
    const time = stringValue(item.published, stringValue(feedRecord.generatedAt));
    if (!isTodayEt(time)) continue;
    rows.push({
      id: stringValue(item.url, stringValue(item.title)),
      label: stringValue(item.category, "Signal"),
      title: stringValue(item.title, stringValue(item.headline, "Untitled signal")),
      reason: stringValue(item.reason, stringValue(item.insight)),
      impact: stringValue(item.impact),
      impactScenarios: impactScenarios(item.impact),
      kind: "intelligence",
      source: stringValue(item.source, "Intelligence Feed"),
      score: numberValue(item.score),
      time,
      url: stringValue(item.url),
    });
  }
  for (const text of arrayValue(feedRecord.tldr)) {
    const title = stringValue(text);
    if (!title) continue;
    rows.push({
      id: `tldr-${title}`,
      label: "TLDR",
      title,
      reason: "Intelligence feed summary",
      source: "J.A.I.N",
      time: stringValue(feedRecord.generatedAt),
    });
  }
  const seen = new Set<string>();
  return rows.filter((row) => {
    const key = row.id || row.title;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort(signalSort).slice(0, 10);
}
