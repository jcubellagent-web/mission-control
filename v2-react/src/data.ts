import { arrayValue, booleanValue, isAgentId, isRecord, recordValue, stringValue } from "./dataAdapters";
import type { ActiveModelRoute, ActiveWork, AgentEvent, AgentId, AgentJob, AgentStatus, Approval, CanonicalModelFamily, ControlTowerHot, MissionControlState, SignalItem, TodayJobEvidence, TodayJobOccurrence, TodayJobOutcome, TodayJobsMeta } from "./types";

const JOB_ROW_LIMIT = 64;
const LIVE_ROW_WINDOW_MS = 2 * 60 * 60 * 1000;
const STALE_BLOCKER_WINDOW_MS = 6 * 60 * 60 * 1000;
const BRAIN_FEED_TRUTH_WINDOW_MS = 12 * 60 * 60 * 1000;
const LOW_FREQUENCY_SIDECAR_TTL_MS = 60 * 1000;
const AGENT_STATUS_ORDER: AgentId[] = ["joshex", "josh2", "jaimes", "jain"];

type SidecarSnapshot = {
  agenticCrypto?: MissionControlState["agenticCrypto"];
  modelUsage?: MissionControlState["modelUsage"];
  reliabilityUpgrades?: MissionControlState["reliabilityUpgrades"];
  signalHealth?: MissionControlState["signalHealth"];
  signals: SignalItem[];
};

let sidecarSnapshotCache: { expiresAt: number; value: Promise<SidecarSnapshot> } | null = null;

// #JAIMES: prefer the freshest visible Brain Feed row when it is current; only fall back to sidecar status when the visible lane is stale or missing.

function canonicalAgentId(value: unknown, fallback: AgentId = "joshex"): AgentId {
  const text = String(value || "").toLowerCase();
  if (text === "main" || text === "josh" || text === "josh2" || text.includes("josh2") || text.includes("josh 2")) {
    return "josh2";
  }
  if (text.includes("jaimes")) return "jaimes";
  if (text.includes("j.a.i.n") || text.includes("jain")) return "jain";
  if (text.includes("joshex") || text.includes("codex")) return "joshex";
  return isAgentId(text) ? text : fallback;
}

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
  const agent_id = canonicalAgentId(row.agent_id, fallbackAgent);
  return {
    agent_id,
    status: stringValue(row.status, "info"),
    objective: stringValue(row.objective, "No objective reported"),
    detail: stringValue(row.detail, stringValue(row.summary)),
    current_tool: stringValue(row.currentTool, stringValue(row.current_tool)),
    model: stringValue(row.model, stringValue(row.modelAuth)),
    model_family: normalizeModelFamily(row.model_family || row.modelFamily),
    route_verified: booleanValue(row.route_verified ?? row.routeVerified),
    work_id: stringValue(row.work_id, stringValue(row.workId)),
    run_id: stringValue(row.run_id, stringValue(row.runId)),
    phase: stringValue(row.phase),
    lease_until: stringValue(row.lease_until, stringValue(row.leaseUntil)),
    origin_claim_hash: stringValue(row.origin_claim_hash, stringValue(row.originClaimHash)),
    source: stringValue(row.source, stringValue(row.statusSource, "Live Work Board")),
    active: booleanValue(row.active),
    updated_at: stringValue(row.updatedAt, stringValue(row.checkedAt, stringValue(row.updated_at))),
    steps: normalizeSteps(row.steps),
  };
}

function normalizeModelFamily(value: unknown): CanonicalModelFamily | undefined {
  const family = String(value || "").toLowerCase();
  return ["codex", "antigravity", "ollama", "grok"].includes(family)
    ? family as CanonicalModelFamily
    : undefined;
}

function unexpired(value?: string): boolean {
  if (!value) return true;
  const lease = timestampValue(value);
  return Boolean(lease) && lease > Date.now();
}

function normalizeHotWork(row: unknown): ActiveWork | null {
  if (!isRecord(row)) return null;
  const ownerAgent = canonicalAgentId(row.ownerAgent);
  const workId = stringValue(row.workId);
  const runId = stringValue(row.runId);
  const objective = stringValue(row.objective);
  if (!workId || !runId || !objective) return null;
  return {
    workId,
    runId,
    generation: Number(row.generation || 1),
    sequence: Number(row.sequence || 1),
    status: stringValue(row.status, "active"),
    ownerAgent,
    ownerLabel: stringValue(row.ownerLabel),
    objective,
    phase: stringValue(row.phase, stringValue(row.status, "active")),
    tool: stringValue(row.tool, "agent runtime"),
    detail: stringValue(row.detail),
    origin: stringValue(row.origin),
    originClaimHash: stringValue(row.originClaimHash),
    modelFamily: normalizeModelFamily(row.modelFamily),
    modelId: stringValue(row.modelId) || null,
    routeVerified: booleanValue(row.routeVerified),
    executionRole: row.executionRole === "worker" ? "worker" : "controller",
    controllerWorkId: stringValue(row.controllerWorkId) || null,
    controllerRunId: stringValue(row.controllerRunId) || null,
    leaseUntil: stringValue(row.leaseUntil),
    createdAt: stringValue(row.createdAt),
    updatedAt: stringValue(row.updatedAt),
    lastMeaningfulAt: stringValue(row.lastMeaningfulAt),
    stale: booleanValue(row.stale),
  };
}

function normalizeHotProjection(value: unknown): ControlTowerHot | undefined {
  if (!isRecord(value)) return undefined;
  const activeWorks = arrayValue(value.activeWorks)
    .map(normalizeHotWork)
    .filter((row): row is ActiveWork => Boolean(row && !row.stale && unexpired(row.leaseUntil)));
  const activeModelRoutes = arrayValue(value.activeModelRoutes)
    .filter(isRecord)
    .map((row): ActiveModelRoute | null => {
      const modelFamily = normalizeModelFamily(row.modelFamily);
      const ownerAgent = canonicalAgentId(row.ownerAgent);
      const workId = stringValue(row.workId);
      const runId = stringValue(row.runId);
      const modelId = stringValue(row.modelId);
      if (!modelFamily || !workId || !runId || !modelId || !booleanValue(row.routeVerified) || !unexpired(stringValue(row.leaseUntil))) return null;
      return {
        workId,
        runId,
        ownerAgent,
        modelFamily,
        modelId,
        routeVerified: true,
        executionRole: row.executionRole === "worker" ? "worker" : "controller",
        controllerWorkId: stringValue(row.controllerWorkId),
        controllerRunId: stringValue(row.controllerRunId),
        activatedAt: stringValue(row.activatedAt),
        updatedAt: stringValue(row.updatedAt),
        leaseUntil: stringValue(row.leaseUntil),
        sourceEventId: stringValue(row.sourceEventId),
        revision: Number.isFinite(Number(row.revision)) ? Number(row.revision) : undefined,
      };
    })
    .filter((row): row is ActiveModelRoute => Boolean(row));
  return {
    schemaVersion: Number.isFinite(Number(value.schemaVersion)) ? Number(value.schemaVersion) : undefined,
    revision: Number.isFinite(Number(value.revision)) ? Number(value.revision) : undefined,
    generatedAt: stringValue(value.generatedAt),
    storeUpdatedAt: stringValue(value.storeUpdatedAt),
    source: stringValue(value.source, "control-tower-work-store"),
    freshness: recordValue(value.freshness),
    counts: recordValue(value.counts),
    activeWorks,
    activeModelRoutes,
  };
}

function hotStatus(work: ActiveWork): AgentStatus {
  return {
    agent_id: work.ownerAgent,
    status: work.status,
    objective: work.objective,
    detail: work.detail || work.phase,
    current_tool: work.tool,
    model: work.modelId || undefined,
    model_family: work.modelFamily || undefined,
    route_verified: work.routeVerified,
    work_id: work.workId,
    run_id: work.runId,
    phase: work.phase,
    lease_until: work.leaseUntil,
    origin_claim_hash: work.originClaimHash,
    source: "Canonical work ledger",
    active: true,
    updated_at: work.updatedAt,
    steps: [{ label: work.phase, status: work.status, tool: work.tool, kind: "work-ledger" }],
  };
}

function overlayHotStatuses(hot: ControlTowerHot | undefined, fallback: AgentStatus[]): AgentStatus[] {
  const legacyActiveStatuses = new Set(["accepted", "planned", "routed", "active", "verifying", "working", "running", "pending", "live"]);
  const byAgent = new Map(fallback.map((row) => {
    const legacyLooksActive = Boolean(row.active) || legacyActiveStatuses.has(String(row.status || "").toLowerCase());
    if (!hot || !legacyLooksActive) return [row.agent_id, row] as const;
    // Once the canonical leased ledger is available, an older Brain Feed or
    // heartbeat row cannot independently occupy Live Work. Preserve explicit
    // blocker/error states above, but downgrade unsupported legacy activity.
    return [row.agent_id, {
      ...row,
      status: "ready",
      active: false,
      objective: "No active canonical work",
      detail: "Ready; the canonical ledger has no unexpired work for this agent.",
      route_verified: false,
      work_id: undefined,
      run_id: undefined,
      phase: "ready",
      lease_until: undefined,
      source: "Canonical work ledger",
    }] as const;
  }));
  const newestByAgent = new Map<AgentId, ActiveWork>();
  for (const work of hot?.activeWorks || []) {
    if (work.executionRole === "worker") continue;
    const current = newestByAgent.get(work.ownerAgent);
    if (!current || timestampValue(work.updatedAt) > timestampValue(current.updatedAt)) newestByAgent.set(work.ownerAgent, work);
  }
  newestByAgent.forEach((work, agent) => byAgent.set(agent, hotStatus(work)));
  return [...byAgent.values()].sort((a, b) => AGENT_STATUS_ORDER.indexOf(a.agent_id) - AGENT_STATUS_ORDER.indexOf(b.agent_id));
}

function normalizeBrainFeedRow(row: unknown): AgentStatus | null {
  if (!isRecord(row)) return null;
  const data = recordValue(row.data);
  if (!Object.keys(data).length) return null;
  const id = String(row.id || data.agentId || "").toLowerCase();
  const fallbackAgent = canonicalAgentId(id);
  if (!["joshex", "josh2", "jaimes", "jain"].includes(fallbackAgent)) return null;
  return normalizeStatus(
    {
      ...data,
      agent_id: data.agent_id || data.agentId || fallbackAgent,
      updated_at: data.updated_at || data.updatedAt || row.updated_at,
    },
    fallbackAgent,
  );
}

async function fetchJson<T>(path: string): Promise<T> {
  // Revalidate with the local server so unchanged JSON can use ETag/304 instead
  // of forcing a full payload transfer on every Control Tower refresh.
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json() as Promise<T>;
}

async function loadDashboardSnapshot(): Promise<any> {
  try {
    const snapshot = await fetchJson<unknown>("/data/control-tower-live.json");
    if (!isRecord(snapshot)) throw new Error("control-tower-live.json is not an object");
    return snapshot;
  } catch {
    // Older publishers may not have emitted the slim snapshot yet. Keep the
    // full dashboard as a safe compatibility lane rather than failing the UI.
    return fetchJson<any>("/data/dashboard-data.json").catch(() => null);
  }
}

async function loadActiveWorkProjection(): Promise<unknown> {
  try {
    const snapshot = await fetchJson<unknown>("/api/control-tower-hot");
    if (!isRecord(snapshot)) throw new Error("active work projection is not an object");
    return snapshot;
  } catch {
    // Compatibility for older Control Tower servers that only expose the
    // full work-store projection. normalizeHotProjection still strips history.
    return fetchJson<unknown>("/data/control-tower-hot.json").catch(() => null);
  }
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
    "josh2",
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
    if (visible && (isFreshBrainFeedTruth(visible) || !primaryRow)) {
      rows.push(visible);
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

function actionItemRequiresApproval(item: any): boolean {
  const kind = String(item?.kind || item?.type || "").trim().toLowerCase();
  return item?.requiresApproval === true || kind === "approval";
}

function mergeJobs(primary: AgentJob[], fallback: AgentJob[]): AgentJob[] {
  const rows = new Map<string, AgentJob>();
  const now = Date.now();
  for (const job of [...primary, ...fallback]) {
    if (!job?.title) continue;
    const updated = timestampValue(job.updated_at);
    const staleLiveBlocker = isBlockingJobStatus(job)
      && Boolean(updated)
      && now - updated > STALE_BLOCKER_WINDOW_MS
      && !hasScheduledJobFingerprint(job);
    if (staleLiveBlocker) continue;
    const key = jobMergeKey(job);
    const existing = rows.get(key);
    if (!existing || timestampValue(job.updated_at) >= timestampValue(existing.updated_at)) {
      rows.set(key, job);
    }
  }
  return [...rows.values()]
    .filter((job, _index, jobs) => !blockedJobSuperseded(job, jobs, now))
    .sort((a, b) => {
      const rankDelta = priorityJobRank(b) - priorityJobRank(a);
      if (rankDelta) return rankDelta;
      return timestampValue(b.updated_at) - timestampValue(a.updated_at);
    })
    .slice(0, JOB_ROW_LIMIT);
}

function normalizeTodayJobOutcome(row: any): TodayJobOutcome {
  const explicit = String(row?.outcome || "").toLowerCase();
  if (["complete", "skipped", "broken", "pending"].includes(explicit)) return explicit as TodayJobOutcome;
  const status = String(row?.runStatus || row?.status || row?.state || "").toLowerCase();
  if (row?.verifiedToday || /complete|completed|success|succeeded|done|passed|ok/.test(status)) return "complete";
  if (/skip|disabled|paused|cancel/.test(status)) return "skipped";
  if (/broken|error|failed|missed|blocked|timeout/.test(status)) return "broken";
  return "pending";
}

function normalizeTodayJobEvidence(value: unknown): string | TodayJobEvidence | undefined {
  if (typeof value === "string") return value || undefined;
  if (!isRecord(value)) return undefined;
  return {
    source: stringValue(value.source) || undefined,
    status: stringValue(value.status) || undefined,
    at: stringValue(value.at) || null,
    summary: stringValue(value.summary) || null,
  };
}

function normalizeTodayJobsProjection(dashboard: any): { todayJobs: TodayJobOccurrence[]; todayJobsMeta?: TodayJobsMeta } {
  const projected = Array.isArray(dashboard?.todayJobs) ? dashboard.todayJobs : null;
  const crons = Array.isArray(dashboard?.crons) ? dashboard.crons : [];
  const relevantCrons = crons.filter((row: any) => row?.todayRelevant);
  const sourceRows = projected || (relevantCrons.length ? relevantCrons : crons);
  const todayJobs: TodayJobOccurrence[] = sourceRows
    .filter((row: any) => row && (row.name || row.title))
    .map((row: any, index: number): TodayJobOccurrence => ({
      occurrenceId: String(row.occurrenceId || row.id || `cron-${row.definitionId || row.name || index}`),
      definitionId: row.definitionId ? String(row.definitionId) : undefined,
      name: String(row.name || row.title || "Scheduled job"),
      owner: row.owner ? String(row.owner) : undefined,
      agent: row.agent ? String(row.agent) : undefined,
      source: row.source ? String(row.source) : undefined,
      sourceLabel: row.sourceLabel ? String(row.sourceLabel) : undefined,
      category: row.category ? String(row.category) : undefined,
      description: row.description ? String(row.description) : undefined,
      scheduledAt: row.scheduledAt || row.nextRun || undefined,
      scheduledTime: row.scheduledTime || row.time || undefined,
      schedule: row.schedule || undefined,
      outcome: normalizeTodayJobOutcome(row),
      runStatus: row.runStatus || row.status || undefined,
      lastRun: row.lastRun || undefined,
      durationMs: Number.isFinite(Number(row.durationMs)) ? Number(row.durationMs) : undefined,
      duration: row.duration || undefined,
      evidence: normalizeTodayJobEvidence(row.evidence),
      rolledUp: Boolean(row.rolledUp),
      expectedRuns: Number.isFinite(Number(row.expectedRuns)) ? Number(row.expectedRuns) : undefined,
      completedRuns: Number.isFinite(Number(row.completedRuns)) ? Number(row.completedRuns) : undefined,
    }))
    .sort((a: TodayJobOccurrence, b: TodayJobOccurrence) => timestampValue(a.scheduledAt) - timestampValue(b.scheduledAt));

  const todayJobsMeta = isRecord(dashboard?.todayJobsMeta)
    ? dashboard.todayJobsMeta as TodayJobsMeta
    : undefined;
  return { todayJobs, todayJobsMeta };
}

async function loadFallback(): Promise<MissionControlState> {
  const [brain, personal, dashboard, sidecars, joshexFeed, jaimesFeed, jainFeed, rawWorkHot] = await Promise.all([
    fetchJson<any>("/data/brain-feed.json").catch(() => null),
    fetchJson<any>("/data/personal-codex.json").catch(() => null),
    loadDashboardSnapshot(),
    loadSidecars(),
    fetchJson<any>("/data/joshex-brain-feed.json").catch(() => null),
    fetchJson<any>("/data/jaimes-brain-feed.json").catch(() => null),
    fetchJson<any>("/data/jain-brain-feed.json").catch(() => null),
    loadActiveWorkProjection(),
  ]);
  const codexJobs = Array.isArray(dashboard?.codexJobs)
    ? null
    : await fetchJson<any>("/data/codex-jobs.json").catch(() => null);
  const workHot = normalizeHotProjection(rawWorkHot);
  const brainAgent = String(brain?.agentId || brain?.agent_id || brain?.agent || "").toLowerCase();
  const brainAgentId: AgentId = brainAgent.includes("josh") && !brainAgent.includes("joshex")
    ? "josh2"
    : brainAgent.includes("jaimes")
    ? "jaimes"
    : brainAgent.includes("jain")
    ? "jain"
    : "joshex";
  const legacyStatuses = dedupeStatus([
    normalizeStatus(brain, brainAgentId),
    normalizeStatus(joshexFeed, "joshex"),
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
    normalizeStatus(jaimesFeed, "jaimes"),
    normalizeStatus(jainFeed, "jain"),
    normalizeStatus(dashboard?.jaimesBrainFeed && { ...dashboard.jaimesBrainFeed, agent_id: "jaimes" }, "jaimes"),
    normalizeStatus(dashboard?.jainBrainFeed && { ...dashboard.jainBrainFeed, agent_id: "jain" }, "jain"),
  ]);
  const statuses = overlayHotStatuses(workHot, legacyStatuses);
  const statusEvents = statuses
    .filter((status) => timestampValue(status.updated_at))
    .sort((a, b) => timestampValue(b.updated_at) - timestampValue(a.updated_at))
    .slice(0, 6)
    .map((status, index) => ({
      id: `live-status-${status.agent_id}-${index}`,
      agent_id: status.agent_id,
      event_type: "status",
      status: status.status || "info",
      title: status.objective || `${status.agent_id} status`,
      detail: status.detail || status.current_tool || "Live status update",
      tool: status.current_tool || "live sidecar",
      created_at: status.updated_at || "",
    }));
  const dashboardEvents = (dashboard?.recentActivity || []).slice(0, 16).map((event: any, index: number) => ({
    id: `fallback-event-${index}`,
    agent_id: "joshex",
    event_type: "note",
    status: "info",
    title: event.event || event.title || "Recent activity",
    detail: event.detail || "",
    tool: "Control Tower live snapshot",
    created_at: event.time || dashboard?.generatedAt || "",
  }));
  const events = [...statusEvents, ...dashboardEvents]
    .sort((a, b) => timestampValue(b.created_at) - timestampValue(a.created_at))
    .slice(0, 16);
  const actionItems = Array.isArray(dashboard?.actionRequired) ? dashboard.actionRequired : [];
  const approvals = actionItems.filter(actionItemRequiresApproval).slice(0, 8).map((item: any, index: number) => ({
    id: `fallback-approval-${index}`,
    agent_id: "joshex",
    title: item.title,
    detail: item.priority || "",
    requested_by: "joshex",
    status: "pending",
    risk_tier: "dashboard-safe",
    created_at: dashboard?.generatedAt || "",
  }));
  const operationalAlerts = actionItems.filter((item: any) => !actionItemRequiresApproval(item)).slice(0, 8).map((item: any, index: number) => ({
    id: `fallback-system-alert-${index}`,
    title: item.title || "System attention",
    detail: item.detail || item.priority || "Inspect the related Control Tower source.",
    priority: String(item.priority || "medium").toLowerCase(),
    kind: String(item.kind || item.type || "system").toLowerCase(),
    url: item.url || "#brain-feed",
    created_at: item.created_at || item.updatedAt || dashboard?.generatedAt || "",
  }));
  const { todayJobs, todayJobsMeta } = normalizeTodayJobsProjection(dashboard);
  return {
    source: "Local live sidecars",
    statuses,
    events,
    jobs: buildFallbackJobs(dashboard, codexJobs),
    todayJobs,
    todayJobsMeta,
    workHot,
    activeModelRoutes: workHot?.activeModelRoutes || [],
    approvals,
    operationalAlerts,
    agenticCrypto: sidecars.agenticCrypto,
    modelUsage: dashboard?.modelUsage || sidecars.modelUsage,
    modelRouter: dashboard?.modelRouter,
    qualityControl: dashboard?.qualityControl,
    reliabilityUpgrades: dashboard?.reliabilityUpgrades || sidecars.reliabilityUpgrades,
    brainAtlas: dashboard?.brainAtlas,
    capabilityStack: dashboard?.capabilityStack,
    capabilityInventory: dashboard?.capabilityInventory,
    capabilityWatch: dashboard?.capabilityWatch,
    machineHealth: dashboard?.machineHealth,
    runtimeLayout: dashboard?.runtimeLayout,
    sharedOperatingLayer: dashboard?.sharedOperatingLayer,
    agentControl: dashboard?.agentControl,
    agentContextRegistry: dashboard?.agentContextRegistry,
    memoryOperations: dashboard?.memoryOperations,
    codingVisibility: dashboard?.codingVisibility,
    trackedTasks: Array.isArray(dashboard?.trackedTasks) ? dashboard.trackedTasks : [],
    agentBus: Array.isArray(dashboard?.agentBus) ? dashboard.agentBus : [],
    signalHealth: sidecars.signalHealth,
    signals: sidecars.signals,
  };
}

function ownerToAgentId(owner?: string): AgentId {
  const text = String(owner || "").toLowerCase();
  if (text.includes("jaimes")) return "jaimes";
  if (text.includes("j.a.i.n") || text.includes("jain")) return "jain";
  if (text.includes("joshex") || text.includes("codex")) return "joshex";
  return "josh2";
}

export function buildFallbackJobs(dashboard: any, directCodexJobs?: any): AgentJob[] {
  const now = Date.now();
  const codexJobs = Array.isArray(directCodexJobs?.jobs)
    ? directCodexJobs.jobs
    : Array.isArray(dashboard?.codexJobs)
      ? dashboard.codexJobs
      : [];
  const usesProjectedTodayJobs = !Array.isArray(dashboard?.crons)
    && Array.isArray(dashboard?.todayJobs);
  const crons = Array.isArray(dashboard?.crons)
    ? dashboard.crons
    : usesProjectedTodayJobs
      ? dashboard.todayJobs
      : [];
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

  const priorityCrons = crons.filter((item: any) => priorityJobRank({
    id: "",
    agent_id: ownerToAgentId(item?.agent),
    title: item?.name || "",
    status: item?.status || item?.runStatus || "",
    detail: item?.description || item?.schedule || "",
    tool: item?.sourceLabel || item?.source || "",
    updated_at: item?.lastRun || dashboard?.generatedAt || "",
  }));
  // #JAIMES: `todayJobs` is already today's bounded projection; do not require
  // the cron-only `todayRelevant` flag or normal scheduled rows disappear.
  const dailyCrons = usesProjectedTodayJobs
    ? crons
    : crons.filter((item: any) => item?.todayRelevant);
  const selectedCrons = new Map<string, any>();
  for (const cron of [...priorityCrons, ...dailyCrons]) {
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
    .filter((row) => {
      if (!row.title) return false;
      const updated = timestampValue(row.updated_at);
      const staleLiveBlocker = isBlockingJobStatus(row)
        && Boolean(updated)
        && now - updated > STALE_BLOCKER_WINDOW_MS
        && !hasScheduledJobFingerprint(row);
      return !staleLiveBlocker;
    })
    .filter((row, _index, jobs) => !blockedJobSuperseded(row, jobs, now))
    .sort((a, b) => {
      const rankDelta = priorityJobRank(b) - priorityJobRank(a);
      if (rankDelta) return rankDelta;
      return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
    })
    .slice(0, JOB_ROW_LIMIT);
}

export async function loadMissionControl(): Promise<MissionControlState> {
  return loadFallback();
}

export function subscribeMissionControlRealtime(_onChange: () => void, onState?: (state: "connected" | "polling") => void) {
  if (typeof window === "undefined" || typeof window.EventSource === "undefined") {
    onState?.("polling");
    return () => {};
  }

  // #JAIMES: the Josh 2.0 local SSE lane is the fast path; the existing 10s
  // refresh in main.tsx remains the reconciliation/fallback path.
  const events = new EventSource("/events/mission-control");
  const refreshIntervalMs = 500;
  let refreshTimer: number | null = null;
  let lastRefreshAt = Number.NEGATIVE_INFINITY;
  let refreshQueued = false;

  const deliverRefresh = () => {
    refreshTimer = null;
    if (!refreshQueued) return;
    refreshQueued = false;
    lastRefreshAt = Date.now();
    _onChange();
  };

  const scheduleRefresh = () => {
    refreshQueued = true;
    if (refreshTimer !== null) return;

    const elapsed = Date.now() - lastRefreshAt;
    const delay = Math.max(0, refreshIntervalMs - elapsed);
    if (delay === 0) {
      deliverRefresh();
      return;
    }
    refreshTimer = window.setTimeout(deliverRefresh, delay);
  };
  const handleLiveUpdate = () => scheduleRefresh();

  events.addEventListener("open", () => onState?.("connected"));
  events.addEventListener("mission-control", handleLiveUpdate);
  events.addEventListener("error", () => onState?.("polling"));

  return () => {
    if (refreshTimer !== null) window.clearTimeout(refreshTimer);
    refreshQueued = false;
    events.removeEventListener("mission-control", handleLiveUpdate);
    events.close();
  };
}

export function invalidateMissionControlSidecars() {
  sidecarSnapshotCache = null;
}

function loadSidecars(): Promise<SidecarSnapshot> {
  const now = Date.now();
  if (sidecarSnapshotCache && sidecarSnapshotCache.expiresAt > now) {
    return sidecarSnapshotCache.value;
  }
  const value = fetchSidecars();
  sidecarSnapshotCache = { expiresAt: now + LOW_FREQUENCY_SIDECAR_TTL_MS, value };
  return value;
}

async function fetchSidecars(): Promise<SidecarSnapshot> {
  const [agenticCrypto, modelUsage, reliabilityUpgrades, signalHealth, dailySignals, jainBreaking, breaking, jainNewsfeed, newsfeed] = await Promise.all([
    fetchJson<MissionControlState["agenticCrypto"]>("/data/agentic-crypto-wallet.json").catch(() => undefined),
    fetchJson<MissionControlState["modelUsage"]>("/data/modelUsage.json").catch(() => undefined),
    fetchJson<MissionControlState["reliabilityUpgrades"]>("/data/reliability-upgrades.json").catch(() => undefined),
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
