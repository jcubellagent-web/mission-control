import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AlertTriangle, CheckCircle2, ClipboardList, Coins, DollarSign, ExternalLink, EyeOff, GitBranch, Moon, Radio, RefreshCw, ShieldCheck, Sun, Timer, UserRoundCheck, WalletCards } from "lucide-react";
import { loadMissionControl, subscribeMissionControlRealtime } from "./data";
import { PRIORITY_JOB_RULES, SORARE_DAILY_GROUPS, SORARE_GENERAL_PATTERN, type PriorityJobKey, type SorareGroupKey } from "./priorityJobs";
import type { AgenticCryptoWallet, AgentId, AgentStatus, MissionControlState, SignalItem } from "./types";
import "./styles.css";

const AGENTS: Record<AgentId, { label: string; role: string; roleBadge: string }> = {
  joshex: { label: "JOSHeX", role: "Personal coordination and private-Mac specialist", roleBadge: "Personal" },
  josh2: { label: "Josh 2.0", role: "Daily Interface Agent", roleBadge: "Interface" },
  jaimes: { label: "JAIMES", role: "Coding, heavy execution, self-improving workhorse", roleBadge: "Execution" },
  jain: { label: "J.A.I.N", role: "Support and disaster-recovery agent", roleBadge: "Support/DR" },
};
const HERO_AGENT_ORDER: AgentId[] = ["joshex", "josh2", "jaimes", "jain"];

type AttentionTarget = "brain-feed" | "today-jobs";
type WorkState = "working" | "waiting" | "blocked" | "ready" | "done" | "quiet";
type AgentVisualState = "working" | "ready" | "waiting" | "blocked" | "stale" | "offline";
type StepTrailState = "done" | "current" | "pending";
type AgentHeadline = { title: string; description: string };
type ControlTowerDisplayState = {
  nightMode?: boolean;
  mode?: string;
  updatedAt?: string;
  updatedBy?: string;
  reason?: string;
};
type AgentIdleContext = {
  complete: string;
  nextTitle: string;
  nextBullets: Array<{ label: string; text: string }>;
  nextAt?: number;
  countdown: string;
};
type AgentBriefRow = { label: string; text: string };
type AgentInsightRow = { label: string; text: string; tone?: "default" | "good" | "watch" | "active" };
type RouteLadderStep = { key: string; label: string; model: string; note: string; active: boolean };
type AttentionItem = {
  id: string;
  label: string;
  title: string;
  detail: string;
  why: string;
  means: string;
  action: string;
  tone: "clear" | "risk" | "watch";
  target: AttentionTarget;
};
type WorkItem = {
  id: string;
  agent_id: AgentId;
  title: string;
  detail: string;
  state: WorkState;
  updated_at: string;
  source: "agent" | "job" | "approval";
  target: AttentionTarget;
  priority: number;
};
type HandoffBeam = {
  id: string;
  from: AgentId;
  to: AgentId;
  label: string;
  tone: "active" | "watch";
};
type SectionCueKey = "brain" | "jobs" | "system";
type LiveCueState = {
  sections: Partial<Record<SectionCueKey, number>>;
  rows: Record<string, number>;
  focus: SectionCueKey | null;
};

const CHANGE_CUE_MS = 3200;
const MIN_EXPECTED_OPERATOR_JOBS = 12;

const JOSH_HEADSHOT_URL = new URL("../../assets/josh-headshot.jpg", import.meta.url).href;

const EMPTY_STATE: MissionControlState = {
  source: "Loading",
  statuses: [],
  events: [],
  jobs: [],
  approvals: [],
  signals: [],
};

function fmtTime(value?: string | null) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function missionText(value?: string | null) {
  const scriptLabels: Record<string, string> = {
    intel_feedback_loop: "intelligence feedback loop",
    feedback_loop: "feedback loop",
    check_josh_health: "Josh health check",
    breaking_news_scanner: "breaking news scanner",
    x_feedback_ml: "X feedback model check",
    launch_scheduler: "launch scheduler",
    host_local_maintenance: "host maintenance",
    sorare_missions: "Sorare mission sweep",
    agent_heartbeat: "status check",
  };
  const humanizeScript = (name: string) => {
    const stem = name.split("/").pop()?.replace(/\.(py|sh|js|ts|tsx)$/i, "") || name;
    return scriptLabels[stem] || stem.replace(/[_-]/g, " ");
  };
  return String(value || "")
    .replace(/Heartbeat:\s*josh2-lan/gi, "Josh 2.0 is online and ready")
    .replace(/Heartbeat:\s*jaimes-via-josh/gi, "JAIMES is online and ready")
    .replace(/Heartbeat:\s*macbook-codex/gi, "JOSHeX is online and ready")
    .replace(/\bagent heartbeat\b/gi, "status check")
    .replace(/\bjosh2-lan\b/gi, "Josh 2.0")
    .replace(/\bjaimes-via-josh\b/gi, "JAIMES")
    .replace(/\bmacbook-codex\b/gi, "JOSHeX")
    .replace(/React v2 Mission Control/gi, "current Control Tower")
    .replace(/Mission Control v2/gi, "Control Tower")
    .replace(/React v2/gi, "React Control Tower")
    .replace(/v2 refresh/gi, "current refresh")
    .replace(/v2 row/gi, "status row")
    .replace(/v2 status\/events/gi, "status/events")
    .replace(/v2 jobs/gi, "jobs")
    .replace(/v2 state/gi, "status")
    .replace(/JAIMES v2 job smoke/gi, "JAIMES job smoke")
    .replace(/JAIMES v2 handoff smoke/gi, "JAIMES handoff smoke")
    .replace(/\b([a-z0-9_.-]+)\s+cron:\s+((?:\/[^ ]+\/)?[A-Za-z0-9_.-]+\.(?:py|sh|js|ts|tsx))/gi, (_, host, script) => `${host} scheduled: ${humanizeScript(script)}`)
    .replace(/(?<![\w./-])((?:\/[^ ]+\/)?[A-Za-z0-9_-]+\.(?:py|sh|js|ts|tsx))(?![\w./-])/gi, (_, script) => humanizeScript(script));
}

function statusClass(status?: string) {
  if (status === "active" || status === "queued") return "is-active";
  if (status === "blocked" || status === "error") return "is-risk";
  if (status === "done" || status === "ready" || status === "approved" || status === "ok") return "is-done";
  return "is-muted";
}

function displayStatus(status?: string) {
  const value = String(status || "").toLowerCase();
  if (value === "active" || value === "queued") return "Working";
  if (value === "blocked" || value === "error") return "Needs focus";
  if (value === "done") return "Done";
  if (value === "ready" || value === "approved" || value === "ok") return "Ready";
  if (value === "offline") return "Offline";
  if (value === "paused" || value === "scheduled" || value === "idle" || value === "stale") return "Quiet";
  return missionText(status || "Quiet");
}

function agentIsReady(status?: AgentStatus) {
  if (!status) return false;
  const value = String(status.status || "").toLowerCase();
  return Boolean(status.active) || ["active", "queued", "ready", "ok", "done", "approved", "stale"].includes(value);
}

function agentOperatingState(status: AgentStatus) {
  const value = String(status.status || "").toLowerCase();
  if (isOptionalJoshexOffline(status)) return "Offline";
  if (value === "blocked" || value === "error") return "Needs focus";
  if (value === "active" || value === "queued" || (status.active && isFreshActiveTimestamp(status.updated_at))) return "Working";
  if (value === "ready" || value === "ok" || value === "done" || value === "approved") return "Ready";
  if (value === "stale" || value === "idle" || value === "offline") return "Quiet";
  return displayStatus(status.status);
}

function workStateClass(state?: WorkState) {
  if (state === "blocked" || state === "waiting") return "is-risk";
  if (state === "working") return "is-active";
  if (state === "ready" || state === "done") return "is-done";
  return "is-muted";
}

function workStateLabel(state?: WorkState) {
  if (state === "blocked") return "Blocked";
  if (state === "waiting") return "Needs Josh";
  if (state === "working") return "Working";
  if (state === "ready") return "Next";
  if (state === "done") return "Done";
  return "Quiet";
}

function statusWorkState(status: AgentStatus): WorkState {
  const value = String(status.status || "").toLowerCase();
  if (value === "blocked" || value === "error") return "blocked";
  if (value === "active" || value === "queued" || status.active) return "working";
  if (value === "ready" || value === "ok" || value === "approved") return "ready";
  if (value === "done") return "done";
  return "quiet";
}

function jobWorkState(job: MissionControlState["jobs"][number], jobs: MissionControlState["jobs"] = []): WorkState {
  const value = String(job.runStatus || job.status || "").toLowerCase();
  if (jobNeedsAttention(job, jobs)) return "blocked";
  if (value === "missed") return jobIsSoftMissedAutomation(job) ? "ready" : "blocked";
  if ((value === "active" || value === "running" || value === "queued") && jobIsFreshActive(job)) return "working";
  if (value === "due" || value === "upcoming" || value === "scheduled") return "ready";
  if (value === "done" || value === "completed" || job.verifiedToday || sameLocalDay(job.lastRun || job.completed_at || job.updated_at)) return "done";
  return "quiet";
}

function jobIsSoftMissedAutomation(job: MissionControlState["jobs"][number]) {
  const value = String(job.runStatus || job.status || "").toLowerCase();
  const source = `${job.sourceLabel || ""} ${job.tool || ""}`.toLowerCase();
  return value === "missed" && source.includes("codex automation") && !jobNeedsAttention(job, []);
}

function buildWorkItems(state: MissionControlState): WorkItem[] {
  const rows: WorkItem[] = [];

  state.approvals
    .filter((approval) => approval.status === "pending")
    .slice(0, 4)
    .forEach((approval) => {
      rows.push({
        id: `approval-${approval.id}`,
        agent_id: approval.agent_id,
        title: missionText(approval.title || "Decision waiting"),
        detail: missionText(approval.detail || "Josh needs to approve, deny, or hold this."),
        state: "waiting",
        updated_at: approval.created_at,
        source: "approval",
        target: "today-jobs",
        priority: 100,
      });
    });

  state.jobs
    .filter((job) => {
      const priority = priorityJobKey(job) !== "general";
      const workState = jobWorkState(job, state.jobs);
      return priority || workState === "blocked" || workState === "working" || workState === "ready";
    })
    .slice(0, 14)
    .forEach((job) => {
      const workState = jobWorkState(job, state.jobs);
      const priorityBoost = priorityJobKey(job) !== "general" ? 20 : 0;
      rows.push({
        id: `job-${job.id}`,
        agent_id: job.agent_id,
        title: compactJobTitle(job),
        detail: compactJobDetail(job, job.tool, jobCategory(job).label),
        state: workState,
        updated_at: job.updated_at,
        source: "job",
        target: "today-jobs",
        priority: (workState === "blocked" ? 80 : workState === "working" ? 60 : workState === "ready" ? 40 : 18) + priorityBoost,
      });
    });

  state.statuses
    .filter((status) => statusWorkState(status) !== "quiet")
    .forEach((status) => {
      const workState = statusWorkState(status);
      rows.push({
        id: `agent-${status.agent_id}`,
        agent_id: status.agent_id,
        title: compactText(status.objective || AGENTS[status.agent_id]?.role, 48),
        detail: compactText(status.detail || status.current_tool || "Current Live Work Board objective", 72),
        state: workState,
        updated_at: status.updated_at,
        source: "agent",
        target: "brain-feed",
        priority: workState === "blocked" ? 75 : workState === "working" ? 55 : workState === "ready" ? 32 : 12,
      });
    });

  const seen = new Set<string>();
  return rows
    .filter((item) => {
      const key = `${item.agent_id}-${item.source}-${item.title}`.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return Boolean(item.title);
    })
    .sort((a, b) => {
      const priorityDelta = b.priority - a.priority;
      if (priorityDelta) return priorityDelta;
      return timeValue(b.updated_at) - timeValue(a.updated_at);
    })
    .slice(0, 18);
}

function jobNeedsAttention(job: MissionControlState["jobs"][number], jobs: MissionControlState["jobs"] = []) {
  const status = String(job.status || "").toLowerCase();
  if (status !== "blocked" && status !== "error") return false;

  const text = `${job.title} ${job.detail} ${job.tool}`.toLowerCase();
  if (text.includes("gemini") && text.includes("auth-required")) return false;
  if (/ops gmail|shared ops gmail|gmail monitor/.test(text) && ageMinutes(job.updated_at) > ACTIVE_JOB_FRESH_MINUTES * 2) return false;

  const updated = timeValue(job.updated_at);
  const allJobs = Array.isArray(jobs) ? jobs : [];
  if (jobFailureSuperseded(job, allJobs)) return false;
  const hasNewerGw12Submit = /gw12|champion|sorare/.test(text)
    && /approval|approve|replacement|improvement/.test(text)
    && allJobs.some((other) => {
      if (other.id === job.id) return false;
      const otherText = `${other.title} ${other.detail} ${other.tool}`.toLowerCase();
      const otherStatus = String(other.status || "").toLowerCase();
      return timeValue(other.updated_at) > updated
        && /gw12|sorare/.test(otherText)
        && /submit|submitted|live/.test(otherText)
        && !["blocked", "error"].includes(otherStatus);
    });
  return !hasNewerGw12Submit;
}

function jobFailureSuperseded(job: MissionControlState["jobs"][number], jobs: MissionControlState["jobs"] = []) {
  const text = `${job.title} ${job.detail} ${job.tool}`.toLowerCase();
  const updated = timeValue(job.updated_at);
  const isOpsGmail = /ops gmail|shared ops gmail|gmail monitor/.test(text);
  if (!isOpsGmail || !updated) return false;
  return jobs.some((other) => {
    if (other.id === job.id || other.agent_id !== job.agent_id) return false;
    const otherText = `${other.title} ${other.detail} ${other.tool}`.toLowerCase();
    const otherStatus = String(other.runStatus || other.status || "").toLowerCase();
    return timeValue(other.updated_at) > updated
      && /ops gmail|shared ops gmail|gmail monitor/.test(otherText)
      && !["blocked", "error", "failed", "missed"].includes(otherStatus);
  });
}

function timeValue(value?: string | null): number {
  if (!value) return 0;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function ageMinutes(value?: string | null) {
  const timestamp = timeValue(value);
  return timestamp ? Math.max(0, Math.round((Date.now() - timestamp) / 60000)) : Number.POSITIVE_INFINITY;
}

const ACTIVE_FOCUS_FRESH_MINUTES = 45;
const ACTIVE_JOB_FRESH_MINUTES = 90;

function jobIsFreshActive(job: JobRow) {
  const status = jobStatusValue(job);
  return ["active", "running", "queued"].includes(status) && ageMinutes(job.updated_at) <= ACTIVE_JOB_FRESH_MINUTES;
}

function isFreshActiveTimestamp(value?: string | null) {
  return ageMinutes(value) <= ACTIVE_FOCUS_FRESH_MINUTES;
}

function ageLabel(value?: string | null) {
  const minutes = ageMinutes(value);
  if (!Number.isFinite(minutes)) return "no update";
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

function freshnessClass(value?: string | null) {
  const minutes = ageMinutes(value);
  if (!Number.isFinite(minutes) || minutes >= 30) return "is-stale";
  if (minutes >= 5) return "is-aging";
  return "is-fresh";
}

function dataQualityIssues(state: MissionControlState): AttentionItem[] {
  const trackedJobs = operatorTrackedJobs(state.jobs);
  const issues: AttentionItem[] = [];
  if (trackedJobs.length < MIN_EXPECTED_OPERATOR_JOBS) {
    issues.push({
      id: "data-jobs-degraded",
      label: "Data",
      title: "Job data needs refresh",
      detail: `${trackedJobs.length} jobs loaded; expected the full operator inventory.`,
      why: "Control Tower is missing part of the scheduled job inventory.",
      means: "This is usually a stale generated-data layer, not proof that the agents stopped working.",
      action: "Refresh Control Tower data and reload the Josh 2.0 kiosk.",
      tone: "risk",
      target: "today-jobs",
    });
  }
  if (state.statuses.length < 3) {
    issues.push({
      id: "data-agent-coverage",
      label: "Data",
      title: "Agent status coverage is low",
      detail: `${state.statuses.length}/3 core agent rows loaded.`,
      why: "Control Tower is missing at least one core Live Work Board status row.",
      means: "A visible agent card may be stale even if the agent itself is healthy.",
      action: "Repair Live Work Board visibility and regenerate Control Tower data.",
      tone: "watch",
      target: "brain-feed",
    });
  }
  return issues;
}

function signalFreshnessSummary(state: MissionControlState) {
  const latestSignal = state.signals
    .map((signal) => signal.time)
    .filter(Boolean)
    .sort()
    .pop();
  const timestamp = state.signalHealth?.generatedAt || latestSignal;
  const minutes = ageMinutes(timestamp);
  const liveCount = state.signalHealth?.counts?.live ?? state.signals.filter((signal) => !signalIsNewsletter(signal)).length;
  const newsletterCount = state.signalHealth?.counts?.newsletter ?? state.signals.filter(signalIsNewsletter).length;
  const total = state.signalHealth?.counts?.total ?? state.signals.length;
  if (!total) {
    return {
      tone: "risk" as const,
      label: "Signal Feed empty",
      detail: "No Signal Feed rows are loaded.",
    };
  }
  if (!Number.isFinite(minutes) || minutes > 60) {
    return {
      tone: "risk" as const,
      label: "Signal Feed stale",
      detail: `Signal Feed last refreshed ${ageLabel(timestamp)} ago.`,
    };
  }
  if (minutes > 15 || liveCount < 5 || newsletterCount < 5) {
    return {
      tone: "watch" as const,
      label: "Signal Feed aging",
      detail: `${liveCount} live and ${newsletterCount} newsletter rows loaded; refreshed ${ageLabel(timestamp)} ago.`,
    };
  }
  return {
    tone: "clear" as const,
    label: "Signal Feed fresh",
    detail: `${liveCount} live and ${newsletterCount} newsletter rows loaded; refreshed ${ageLabel(timestamp)} ago.`,
  };
}

function agentNeedsFocus(status?: AgentStatus) {
  if (!status) return false;
  const value = String(status.status || "").toLowerCase();
  if (value !== "blocked" && value !== "error") return false;
  return ageMinutes(status.updated_at) <= ACTIVE_JOB_FRESH_MINUTES * 2;
}

function missionFocusCount(state: MissionControlState) {
  const trackedJobs = operatorTrackedJobs(state.jobs);
  const dataIssues = dataQualityIssues(state).filter((issue) => issue.tone === "risk").length;
  const pendingApprovals = state.approvals.filter((row) => row.status === "pending").length;
  const riskJobs = trackedJobs.filter((job) => jobNeedsAttention(job, trackedJobs)).length;
  const blockedAgents = state.statuses.filter(agentNeedsFocus).length;
  return dataIssues + pendingApprovals + riskJobs + blockedAgents;
}

function isOptionalJoshexOffline(status: AgentStatus) {
  if (status.agent_id !== "joshex") return false;
  const value = String(status.status || "").toLowerCase();
  if (value === "offline") return true;
  const minutes = ageMinutes(status.updated_at);
  return !Number.isFinite(minutes) || minutes > 120;
}

function agentSla(status: AgentStatus) {
  const minutes = ageMinutes(status.updated_at);
  const expected = 120;
  if (status.agent_id === "joshex" && isOptionalJoshexOffline(status)) {
    const age = Number.isFinite(minutes) ? ageLabel(status.updated_at) : "unknown";
    return { tone: "offline", label: `Offline · last ${age} ago`, detail: "MacBook/Codex optional" };
  }
  if (status.agent_id === "joshex") {
    const age = Number.isFinite(minutes) ? ageLabel(status.updated_at) : "unknown";
    return { tone: "ok", label: `Online · last ${age} ago`, detail: "MacBook/Codex active" };
  }
  if (!Number.isFinite(minutes)) {
    return { tone: "late", label: "No check-in found", detail: "Expected every 2h" };
  }
  const age = ageLabel(status.updated_at);
  if (minutes > expected) {
    return { tone: "late", label: `Late · last ${age} ago`, detail: "Expected every 2h" };
  }
  if (minutes > expected * 0.75) {
    return { tone: "watch", label: `Aging · last ${age} ago`, detail: "Expected every 2h" };
  }
  return { tone: "ok", label: `On time · last ${age} ago`, detail: "Expected every 2h" };
}

function agentRouteText(status: AgentStatus) {
  const stepText = (status.steps || [])
    .map((step) => [step.label, step.title, step.tool, step.kind, step.status].filter(Boolean).join(" "))
    .join(" ");
  return [
    status.agent_id,
    status.status,
    status.objective,
    status.detail,
    status.current_tool,
    stepText,
  ].filter(Boolean).join(" ").toLowerCase();
}

function activeRouteStep(agent: AgentId, status: AgentStatus) {
  const text = agentRouteText(status);
  if (/approval|approve|josh approval|external action|irreversible/.test(text)) return "approve";
  if (/j\.?a\.?i\.?n|jain|fallback|staging|disaster|dr\b/.test(text) || agent === "jain") return "fallback";
  if (/jaimes|hermes|codex|openclaw|terminal|bash|file edit|execution|tool/.test(text) || agent === "jaimes") return "execute";
  if (/gemini pro|judge|escalat|deep review|long context/.test(text)) return "judge";
  if (/gemini|flash|front[- ]?desk|triage|summary|digest/.test(text) || agent === "josh2") return "frontdesk";
  if (agent === "joshex") return "execute";
  return "frontdesk";
}

function routeLadderSteps(agent: AgentId, status: AgentStatus): RouteLadderStep[] {
  const active = activeRouteStep(agent, status);
  return [
    { key: "lite", label: "Lite", model: "Worker", note: "never routes", active: false },
    { key: "frontdesk", label: "Flash", model: "Front desk", note: "default", active: active === "frontdesk" },
    { key: "judge", label: "Pro", model: "Judge", note: "escalate", active: active === "judge" },
    { key: "execute", label: "JAIMES", model: "Execute", note: "tools", active: active === "execute" },
    { key: "fallback", label: "J.A.I.N", model: "Fallback", note: "DR", active: active === "fallback" },
    { key: "approve", label: "Josh", model: "Approve", note: "gate", active: active === "approve" },
  ];
}

function agentClass(agent: AgentId) {
  return `agent-${agent}`;
}

function agentVisualState(status: AgentStatus, activeFocus: boolean, activeWork?: WorkItem): AgentVisualState {
  const value = String(status.status || "").toLowerCase();
  if (isOptionalJoshexOffline(status)) return "offline";
  if (value === "blocked" || value === "error" || activeWork?.state === "blocked") return "blocked";
  if (activeWork?.state === "waiting") return "waiting";
  if (activeFocus || value === "queued" || (value === "active" && isFreshActiveTimestamp(status.updated_at)) || (status.active && isFreshActiveTimestamp(status.updated_at))) return "working";
  if (freshnessClass(status.updated_at) === "is-stale") return "stale";
  return "ready";
}

function stepTrailForAgent(status: AgentStatus, activeFocus: boolean, activeWork?: WorkItem): Array<{ label: string; state: StepTrailState }> {
  const hasUpdate = Boolean(status.updated_at && timeValue(status.updated_at));
  const blocked = activeWork?.state === "blocked" || ["blocked", "error"].includes(String(status.status || "").toLowerCase());
  return [
    { label: "In", state: hasUpdate ? "done" : "pending" },
    { label: blocked ? "Hold" : "Now", state: activeFocus || blocked ? "current" : hasUpdate ? "done" : "pending" },
    { label: "Out", state: activeFocus || blocked ? "pending" : hasUpdate ? "current" : "pending" },
  ];
}

function textMentionsAgent(text: string, agent: AgentId) {
  const normalized = text.toLowerCase();
  if (agent === "joshex") return /\bjoshex\b|\bcodex\b/.test(normalized);
  if (agent === "josh2") return /josh\s*2\.0|\bjosh\b|openclaw|host/.test(normalized);
  if (agent === "jaimes") return /\bjaimes\b|hermes/.test(normalized);
  return /\bj\.?a\.?i\.?n\b|\bjain\b/.test(normalized);
}

function buildHandoffBeams(state: MissionControlState): HandoffBeam[] {
  const cutoff = Date.now() - 30 * 60 * 1000;
  const rows = state.events
    .filter((event) => timeValue(event.created_at) >= cutoff)
    .filter((event) => {
      const text = `${event.event_type} ${event.title} ${event.detail} ${event.tool} ${JSON.stringify(event.metadata || {})}`;
      return /handoff|route|rout|delegate|delegat|assigned|requesting/i.test(text);
    })
    .sort((a, b) => timeValue(b.created_at) - timeValue(a.created_at));
  const beams: HandoffBeam[] = [];
  const seen = new Set<string>();
  rows.forEach((event) => {
    if (beams.length >= 2) return;
    const text = `${event.title} ${event.detail} ${event.tool} ${JSON.stringify(event.metadata || {})}`;
    const from = HERO_AGENT_ORDER.includes(event.agent_id) ? event.agent_id : "joshex";
    const to = HERO_AGENT_ORDER.find((agent) => agent !== from && textMentionsAgent(text, agent));
    if (!to) return;
    const key = `${from}-${to}`;
    if (seen.has(key)) return;
    seen.add(key);
    beams.push({
      id: `${event.id || key}-${event.created_at}`,
      from,
      to,
      label: `${AGENTS[from].label} -> ${AGENTS[to].label}`,
      tone: String(event.status || "").toLowerCase() === "blocked" ? "watch" : "active",
    });
  });
  return beams;
}

function cueRowKey(kind: string, id?: string | number | null) {
  return `${kind}:${id || "unknown"}`;
}

function changedRowClass(active?: boolean) {
  return active ? " has-row-update" : "";
}

function sectionCueClass(key: SectionCueKey, cues: LiveCueState) {
  const updatedAt = cues.sections[key] || 0;
  const isUpdated = Date.now() - updatedAt < CHANGE_CUE_MS;
  return `${isUpdated ? " has-section-update" : ""}${cues.focus === key ? " has-focus-spotlight" : ""}`;
}

function compactSignature(value: unknown) {
  return JSON.stringify(value ?? null);
}

function sectionSignatures(state: MissionControlState): Record<SectionCueKey, string> {
  return {
    brain: compactSignature({
      statuses: state.statuses.map((row) => [row.agent_id, row.status, row.active, row.objective, row.detail, row.updated_at]),
      events: state.events.slice(0, 8).map((row) => [row.id, row.status, row.title, row.created_at]),
      approvals: state.approvals.filter((row) => row.status === "pending").map((row) => [row.id, row.title, row.created_at]),
    }),
    jobs: compactSignature(state.jobs.map((row) => [
      row.id,
      row.status,
      row.runStatus,
      row.title,
      row.updated_at,
      row.lastRun,
      row.nextRun,
      row.started_at,
      row.finished_at,
    ])),
    system: compactSignature({
      reliability: state.reliabilityUpgrades?.items?.map((row) => [row.id, row.status, row.signal, row.next]),
      source: state.source,
    }),
  };
}

function rowSignatures(state: MissionControlState) {
  const rows: Record<string, string> = {};
  state.statuses.forEach((row) => {
    rows[cueRowKey("agent", row.agent_id)] = compactSignature([row.status, row.active, row.objective, row.detail, row.updated_at]);
  });
  state.jobs.forEach((row) => {
    rows[cueRowKey("job", row.id || row.title)] = compactSignature([row.status, row.runStatus, row.updated_at, row.lastRun, row.nextRun, row.detail]);
  });
  state.events.slice(0, 10).forEach((row) => {
    rows[cueRowKey("work", row.id || row.title)] = compactSignature([row.status, row.title, row.detail, row.created_at]);
  });
  buildWorkItems(state).forEach((row) => {
    rows[cueRowKey("work", row.id)] = compactSignature([row.state, row.title, row.detail, row.updated_at, row.agent_id]);
  });
  return rows;
}

function focusSection(state: MissionControlState): SectionCueKey | null {
  if (state.approvals.some((row) => row.status === "pending") || state.jobs.some((job) => jobNeedsAttention(job, state.jobs))) return "jobs";
  const activeAgent = state.statuses.some((row) => ["active", "working"].includes(String(row.status || "").toLowerCase()) && isFreshActiveTimestamp(row.updated_at));
  const activeWork = buildWorkItems(state).some((item) => item.state === "working" && isFreshActiveTimestamp(item.updated_at));
  if (activeAgent || activeWork) return "brain";
  return null;
}

function useLiveCues(state: MissionControlState): LiveCueState {
  const previousRef = useRef<{ sections: Record<SectionCueKey, string>; rows: Record<string, string> } | null>(null);
  const timerRef = useRef<number | null>(null);
  const [cues, setCues] = useState<LiveCueState>({ sections: {}, rows: {}, focus: null });

  useEffect(() => {
    const nextSections = sectionSignatures(state);
    const nextRows = rowSignatures(state);
    const previous = previousRef.current;
    const now = Date.now();
    const nextFocus = focusSection(state);

    if (!previous) {
      previousRef.current = { sections: nextSections, rows: nextRows };
      setCues((current) => ({ ...current, focus: nextFocus }));
      return undefined;
    }

    const changedSections: Partial<Record<SectionCueKey, number>> = {};
    (Object.keys(nextSections) as SectionCueKey[]).forEach((key) => {
      if (previous.sections[key] !== nextSections[key]) changedSections[key] = now;
    });
    const changedRows: Record<string, number> = {};
    Object.entries(nextRows).forEach(([key, signature]) => {
      if (previous.rows[key] && previous.rows[key] !== signature) changedRows[key] = now;
    });

    previousRef.current = { sections: nextSections, rows: nextRows };
    if (Object.keys(changedSections).length || Object.keys(changedRows).length || cues.focus !== nextFocus) {
      setCues((current) => ({
        sections: { ...current.sections, ...changedSections },
        rows: { ...current.rows, ...changedRows },
        focus: nextFocus,
      }));
    }

    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      const cutoff = Date.now() - CHANGE_CUE_MS;
      setCues((current) => ({
        sections: Object.fromEntries(Object.entries(current.sections).filter(([, value]) => value > cutoff)) as LiveCueState["sections"],
        rows: Object.fromEntries(Object.entries(current.rows).filter(([, value]) => value > cutoff)),
        focus: current.focus,
      }));
    }, CHANGE_CUE_MS + 120);

    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [state, cues.focus]);

  return cues;
}

function SectionCue({ label = "updated" }: { label?: string }) {
  return (
    <span className="section-update-cue" aria-hidden="true">
      <i />
      <em>{label}</em>
    </span>
  );
}

async function loadControlTowerDisplayState(): Promise<ControlTowerDisplayState> {
  try {
    const response = await fetch("/data/control-tower-display.json", { cache: "no-store" });
    if (!response.ok) return {};
    return response.json() as Promise<ControlTowerDisplayState>;
  } catch (error) {
    console.warn(error);
    return {};
  }
}

function easternClockParts(now: Date) {
  return {
    time: now.toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
      hour12: true,
    }),
    date: now.toLocaleDateString("en-US", {
      timeZone: "America/New_York",
      weekday: "long",
      month: "long",
      day: "numeric",
    }),
  };
}

function NightModeScreen({
  now,
  onToggle,
  commandSource,
}: {
  now: Date;
  onToggle: () => void;
  commandSource?: string;
}) {
  const clock = easternClockParts(now);
  return (
    <section className="night-mode-screen" aria-label="Control Tower night mode">
      <button type="button" className="night-mode-toggle" onClick={onToggle} aria-label="Exit night mode">
        <Sun size={18} />
        Exit night mode
      </button>
      <div className="night-mode-clock-wrap">
        <p>Control Tower Night Mode</p>
        <strong>{clock.time}</strong>
        <span>{clock.date} ET</span>
        {commandSource ? <em>Set by {commandSource}</em> : null}
      </div>
    </section>
  );
}

class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; error?: Error }
> {
  state = { hasError: false, error: undefined as Error | undefined };

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("Control Tower render error:", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="app-shell hero-shell">
          <header className="mission-header">
            <div className="brand-lockup">
              <div>
                <h1>Josh 2.0 | Control Tower</h1>
                <p>Connection issue — retrying automatically</p>
              </div>
            </div>
            <div className="mission-actions">
              <button
                type="button"
                className="mode-button selected"
                onClick={() => this.setState({ hasError: false, error: undefined })}
                aria-label="Retry"
              >
                <RefreshCw size={15} /> Retry
              </button>
            </div>
          </header>
          <section className="kiosk-grid" style={{ alignItems: "center", justifyContent: "center", minHeight: "50vh" }}>
            <article className="empty-row" style={{ textAlign: "center", padding: "2rem" }}>
              <strong>Control Tower hit a render error</strong>
              <p>The dashboard will retry on the next data refresh (10s).</p>
              <p style={{ fontSize: "11px", color: "var(--muted)", marginTop: "0.5rem" }}>{this.state.error?.message || "Unknown error"}</p>
            </article>
          </section>
        </main>
      );
    }
    return this.props.children;
  }
}

function App() {
  const [state, setState] = useState<MissionControlState>(EMPTY_STATE);
  const [loading, setLoading] = useState(true);
  const [dataError, setDataError] = useState<string | null>(null);
  const [liveMode, setLiveMode] = useState<"connected" | "polling">("polling");
  const [quietMode, setQuietMode] = useState(true);
  const [displayState, setDisplayState] = useState<ControlTowerDisplayState>({});
  const [nightModeOverride, setNightModeOverride] = useState<boolean | null>(null);
  const [clockNow, setClockNow] = useState(() => new Date());
  const liveCues = useLiveCues(state);

  const refresh = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    try {
      const next = await loadMissionControl();
      setState(next);
      setDataError(null);
    } catch (error) {
      console.warn("Control Tower data fetch error:", error);
      setDataError(error instanceof Error ? error.message : "Data fetch failed");
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  const refreshAgenticCrypto = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    try {
      await fetch("/actions/agentic-crypto-refresh?mode=lightweight", { method: "POST", cache: "no-store" });
    } catch (error) {
      console.warn(error);
    } finally {
      await refresh(false);
      if (showLoading) setLoading(false);
    }
  }, [refresh]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(() => {
      refresh(false).catch((error) => console.warn(error));
    }, 10_000);
    const unsubscribe = subscribeMissionControlRealtime(
      () => refresh(false).catch((error) => console.warn(error)),
      setLiveMode,
    );
    return () => {
      window.clearInterval(timer);
      unsubscribe();
    };
  }, [refresh]);

  useEffect(() => {
    const walletTimer = window.setInterval(() => {
      refreshAgenticCrypto(false).catch((error) => console.warn(error));
    }, 5 * 60_000);
    return () => window.clearInterval(walletTimer);
  }, [refreshAgenticCrypto]);

  useEffect(() => {
    let active = true;
    const refreshDisplayState = async () => {
      const next = await loadControlTowerDisplayState();
      if (active) setDisplayState(next);
    };
    refreshDisplayState().catch((error) => console.warn(error));
    const timer = window.setInterval(() => {
      refreshDisplayState().catch((error) => console.warn(error));
    }, 5_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    setNightModeOverride(null);
  }, [displayState.updatedAt]);

  const commandNightMode = Boolean(displayState.nightMode || displayState.mode === "night");
  const nightMode = nightModeOverride ?? commandNightMode;

  useEffect(() => {
    if (!nightMode) return undefined;
    setClockNow(new Date());
    const timer = window.setInterval(() => setClockNow(new Date()), 1_000);
    return () => window.clearInterval(timer);
  }, [nightMode]);

  const toggleNightMode = useCallback(() => {
    setNightModeOverride((current) => {
      const effective = current ?? commandNightMode;
      return !effective;
    });
  }, [commandNightMode]);

  const statusByAgent = useMemo(() => {
    return new Map(state.statuses.map((row) => [row.agent_id, row]));
  }, [state.statuses]);

  const decisionCount = useMemo(() => state.approvals.filter((row) => row.status === "pending").length, [state.approvals]);
  const trackedJobs = useMemo(() => operatorTrackedJobs(state.jobs), [state.jobs]);
  const jobsCount = trackedJobs.length;
  const needsFocusCount = useMemo(() => missionFocusCount(state), [state]);
  const activeJobCount = useMemo(() => trackedJobs.filter((job) => jobWorkState(job, trackedJobs) === "working").length, [trackedJobs]);
  const activeAgentCount = useMemo(() => state.statuses.filter((row) => row.active || row.status === "active").length, [state.statuses]);
  const workingCount = activeJobCount + activeAgentCount;
  const nextJob = useMemo(() => upcomingTodayJobs(trackedJobs, 1)[0], [trackedJobs]);
  const nextRunValue = nextJob ? jobRunCells(nextJob).next : "None";
  const lastUpdate = useMemo(() => [...state.statuses.map((row) => row.updated_at), ...state.events.map((row) => row.created_at)]
    .filter(Boolean)
    .sort()
    .pop(), [state.statuses, state.events]);
  const navigateToPanel = useCallback((target: AttentionTarget) => {
    document.getElementById(target)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  return (
    <main className="app-shell hero-shell">
      {nightMode ? (
        <NightModeScreen now={clockNow} onToggle={toggleNightMode} commandSource={displayState.updatedBy} />
      ) : null}
      <header className="mission-header">
        <div className="brand-lockup">
          <img
            src={JOSH_HEADSHOT_URL}
            alt="Josh 2.0"
            onError={(event) => {
              event.currentTarget.src = "/josh-headshot.jpg";
            }}
          />
          <div>
            <h1>Josh 2.0 | Control Tower</h1>
            <p>Live Work Board command view for the agent ecosystem</p>
          </div>
        </div>
        <section className="status-ribbon header-status-ribbon" aria-label="Control Tower summary">
          <Metric icon={<UserRoundCheck size={18} />} label="Needs Josh" value={decisionCount ? String(decisionCount) : "None"} tone={decisionCount ? "risk" : "clear"} />
          <Metric icon={<AlertTriangle size={18} />} label="System" value={needsFocusCount ? `${needsFocusCount} focus` : "All clear"} tone={needsFocusCount ? "watch" : "clear"} />
          <Metric icon={<ClipboardList size={18} />} label="Jobs" value={String(jobsCount)} tone={workingCount ? "info" : "clear"} />
          <Metric icon={<Timer size={18} />} label="Next" value={nextRunValue} tone="clear" wide />
        </section>
        <div className="mission-actions">
          <span className="source-chip"><ShieldCheck size={15} />{state.source}</span>
          <span className="source-chip">Updated {fmtTime(lastUpdate)}</span>
          <span className="source-chip live-chip">{liveMode === "connected" ? "Realtime" : "Live • 10s"}</span>
          <button
            type="button"
            className={quietMode ? "mode-button selected" : "mode-button"}
            onClick={() => setQuietMode((value) => !value)}
            aria-pressed={quietMode}
            title="Show only active work, warnings, missed jobs, and pending approvals"
          >
            <EyeOff size={15} /> Quiet
          </button>
          <button
            type="button"
            className={nightMode ? "night-header-button selected" : "night-header-button"}
            onClick={toggleNightMode}
            aria-label={nightMode ? "Exit night mode" : "Enter night mode"}
            aria-pressed={nightMode}
            title={nightMode ? "Exit night mode" : "Enter night mode"}
          >
            {nightMode ? <Sun size={16} /> : <Moon size={16} />}
          </button>
          <button type="button" onClick={refresh} aria-label="Refresh">
            <RefreshCw size={16} className={loading ? "spin" : ""} />
          </button>
        </div>
      </header>

      <section className="kiosk-grid">
        <section id="brain-feed" className={`brain-hero-panel${sectionCueClass("brain", liveCues)}`}>
          <SectionCue label={liveCues.focus === "brain" ? "focus" : "updated"} />
          <BrainHero state={state} statuses={statusByAgent} quietMode={quietMode} onNavigate={navigateToPanel} liveCues={liveCues} />
          <section className="support-grid" aria-label="Control Tower support modules">
            <MemoizedFinOpsDashboard
              wallet={state.agenticCrypto}
              modelUsage={state.modelUsage}
              modelRouter={state.modelRouter}
              statuses={state.statuses}
              loading={loading}
              onRefresh={() => refreshAgenticCrypto(true)}
            />
          </section>
        </section>
        <aside className="right-rail">
          <JobsRail jobs={state.jobs} statuses={state.statuses} quietMode={quietMode} liveCues={liveCues} />
        </aside>
      </section>
    </main>
  );
}

function BrainHero({
  state,
  statuses,
  quietMode,
  onNavigate,
  liveCues,
}: {
  state: MissionControlState;
  statuses: Map<AgentId, AgentStatus>;
  quietMode: boolean;
  onNavigate: (target: AttentionTarget) => void;
  liveCues: LiveCueState;
}) {
  const { events, approvals } = state;
  const heroAgents = HERO_AGENT_ORDER;
  const pendingApprovals = approvals.filter((row) => row.status === "pending");
  const recentCutoff = Date.now() - 60 * 60 * 1000;
  const recentActivity = events.filter((event) => timeValue(event.created_at) > recentCutoff).length;
  const activeAgents = Array.from(statuses.values()).filter((row) => row.active || row.status === "active").length;
  const activeJobs = state.jobs.filter((job) => jobIsFreshActive(job)).length;
  const workItems = buildWorkItems(state);
  const [showDetails, setShowDetails] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  const activityScore = Math.min(10, activeAgents * 2 + activeJobs + pendingApprovals.length * 2 + recentActivity);
  const laserSpeed = Math.max(5, 24 - activityScore * 1.9);
  const laserOpacity = Math.min(1, 0.42 + activityScore * 0.06);
  return (
    <section
      className="brain-hero is-flight-deck"
      aria-label="Live Work Board"
      style={{
        "--laser-speed": `${laserSpeed}s`,
        "--laser-opacity": laserOpacity,
      } as React.CSSProperties}
    >
      <div className="brain-hero-title">
        <div>
          <p>Real-time agent work</p>
          <h2>Live Work Board</h2>
        </div>
        <BrainAttentionStrip state={state} quietMode={quietMode} onNavigate={onNavigate} />
        <div className="brain-hero-controls">
          <span>{quietMode ? "Quiet" : `${events.slice(0, 6).length} updates`}</span>
          <button
            type="button"
            className={showDetails ? "selected" : ""}
            onClick={() => setShowDetails((value) => !value)}
            aria-pressed={showDetails}
          >
            {showDetails ? "Hide details" : "Show details"}
          </button>
        </div>
      </div>

      <div className="brain-agent-stage">
        <AgentHandoffBeams state={state} />
        <div className="brain-agent-grid">
          {heroAgents.map((agent) => {
            const status = statuses.get(agent) || offlineStatus(agent);
            const activeWork = workItems.find((item) => item.agent_id === agent && item.source !== "job" && ["waiting", "blocked", "working"].includes(item.state))
              || workItems.find((item) => item.agent_id === agent && ["waiting", "blocked", "working"].includes(item.state));
            const idleContext = buildAgentIdleContext(agent, state, nowMs);
            return (
              <AgentHeroCard
                key={agent}
                agent={agent}
                status={status}
                activeWork={activeWork}
                idleContext={idleContext}
                changed={Boolean(liveCues.rows[cueRowKey("agent", agent)])}
              />
            );
          })}
        </div>
        <SystemRouteLadder
          statuses={heroAgents.map((agent) => statuses.get(agent) || offlineStatus(agent))}
          modelRouter={state.modelRouter}
          modelUsage={state.modelUsage}
        />
      </div>

      {showDetails ? (
        <BrainOperationsSummary state={state} workItems={workItems} quietMode={quietMode} onNavigate={onNavigate} liveCues={liveCues} />
      ) : null}
    </section>
  );
}

function systemRouteLadderSteps(statuses: AgentStatus[], modelRouter?: MissionControlState["modelRouter"]): RouteLadderStep[] {
  const lastRoute = modelRouter?.lastRoute || {};
  const routeText = [
    lastRoute.provider,
    lastRoute.model,
    lastRoute.routeLabel,
    (modelRouter as Record<string, unknown> | undefined)?.activeLane,
    ...statuses.map((status) => agentRouteText(status)),
  ].filter(Boolean).join(" ").toLowerCase();
  const hasActive = statuses.some((status) => {
    const value = String(status.status || "").toLowerCase();
    return (status.active || value === "active" || value === "working") && isFreshActiveTimestamp(status.updated_at);
  });
  return [
    { key: "lite", label: "Lite", model: "Gemini Flash Lite", note: "Tiny worker only; never routes escalation decisions", active: /flash[- ]?lite|lite_worker/.test(routeText) },
    { key: "frontdesk", label: "Flash", model: "Gemini Flash", note: "Fast front desk, summaries, triage, safe review", active: /gemini|flash|front[- ]?desk|triage|summary|digest/.test(routeText) && !/flash[- ]?lite/.test(routeText) },
    { key: "judge", label: "Pro", model: "Gemini Pro", note: "Judgment, long context, escalation checks", active: /gemini pro|judge|escalat|deep review|long context/.test(routeText) },
    { key: "execute", label: "JAIMES", model: "Codex/OpenAI", note: "Verified tools, code, crons, Sorare, heavy execution", active: /jaimes|hermes|backend|execution|terminal|bash|file edit|tool/.test(routeText) },
    { key: "fallback", label: "J.A.I.N", model: "OpenCLAW fallback", note: "Staging, support, recovery, disaster response", active: /j\.?a\.?i\.?n|jain|fallback|staging|disaster|dr\b/.test(routeText) },
    { key: "approve", label: "Josh", model: "Approval gate", note: "External, irreversible, or account-affecting actions", active: /approval|approve|external action|irreversible/.test(routeText) },
  ].map((step) => ({
    ...step,
    active: step.active || (!hasActive && step.key === "frontdesk" && !routeText.trim()),
  }));
}

function routeOwnerLabel(statuses: AgentStatus[], modelRouter?: MissionControlState["modelRouter"]) {
  const live = statuses.find((status) => {
    const value = String(status.status || "").toLowerCase();
    return (status.active || value === "active" || value === "working") && isFreshActiveTimestamp(status.updated_at);
  });
  if (live) return `${AGENTS[live.agent_id as AgentId]?.label || missionText(live.agent_id)} active`;
  const lastRoute = modelRouter?.lastRoute || {};
  return missionText(String(lastRoute.routeLabel || lastRoute.provider || "standby"));
}

function SystemRouteLadder({
  statuses,
  modelRouter,
  modelUsage,
}: {
  statuses: AgentStatus[];
  modelRouter?: MissionControlState["modelRouter"];
  modelUsage?: MissionControlState["modelUsage"];
}) {
  const steps = systemRouteLadderSteps(statuses, modelRouter);
  const active = steps.find((step) => step.active) || steps[1];
  const lastRoute = modelRouter?.lastRoute || {};
  const codexMode = missionText(String(modelRouter?.codexAllowanceMode || modelRouter?.policy?.codexAllowanceMode || modelUsage?.routerPolicy?.codexAllowanceMode || "conserve"));
  const routeQuality = typeof modelRouter?.routeQualityScore === "number" ? `${modelRouter.routeQualityScore}/100` : "tracked";
  const efficiency = typeof modelRouter?.efficiencyScore === "number" ? `${modelRouter.efficiencyScore}/100` : "tracked";
  return (
    <aside className="system-route-ladder" aria-label="Model routing ladder">
      <header>
        <p>Model routing ladder</p>
        <h3>{active.label}: {active.model}</h3>
        <span>{routeOwnerLabel(statuses, modelRouter)}</span>
      </header>
      <div className="system-route-current">
        <span>Current route</span>
        <strong>{missionText(String(lastRoute.provider || active.model || "auto"))}</strong>
        <em>{missionText(String(lastRoute.model || active.note))}</em>
      </div>
      <ol className="system-route-steps">
        {steps.map((step, index) => (
          <li key={step.key} className={step.active ? "is-active" : ""}>
            <i>{index + 1}</i>
            <div>
              <span>{step.label}</span>
              <strong>{step.model}</strong>
              <em>{step.note}</em>
            </div>
          </li>
        ))}
      </ol>
      <footer>
        <span>Codex allowance: {codexMode}</span>
        <span>Route quality: {routeQuality}</span>
        <span>Efficiency: {efficiency}</span>
      </footer>
    </aside>
  );
}

function signalIsNewsletter(signal: SignalItem) {
  const text = `${signal.section || ""} ${signal.sectionLabel || ""} ${signal.kind || ""} ${signal.label || ""} ${signal.source || ""}`.toLowerCase();
  return /newsletter|digest|trend|last/.test(text);
}

function signalScoreLabel(signal: SignalItem) {
  const raw = Number(signal.score);
  if (!Number.isFinite(raw)) return signalIsNewsletter(signal) ? "Digest" : "Live";
  const score = raw <= 1 ? Math.round(raw * 10) : Math.round(raw);
  return `${Math.max(1, Math.min(10, score))}/10`;
}

function signalRowClass(signal: SignalItem) {
  const score = Number(signal.score);
  const classes = [signalIsNewsletter(signal) ? "is-newsletter" : "is-strong"];
  if (Number.isFinite(score) && score < 8) classes.push("is-watch");
  if (freshnessClass(signal.time) === "is-stale") classes.push("freshness-stale");
  if (freshnessClass(signal.time) === "is-aging") classes.push("freshness-aging");
  return classes.join(" ");
}

function signalRows(signals: SignalItem[], newsletter: boolean) {
  return signals
    .filter((signal) => signalIsNewsletter(signal) === newsletter)
    .sort((a, b) => {
      const rankDelta = (a.rank || 999) - (b.rank || 999);
      if (rankDelta) return rankDelta;
      return timeValue(b.time) - timeValue(a.time);
    })
    .slice(0, 5);
}

function cryptoFreshness(wallet?: AgenticCryptoWallet) {
  if (!wallet?.updatedAt) return { label: "not loaded", status: "stale", tone: "watch" };
  const age = Date.now() - timeValue(wallet.updatedAt);
  if (String(wallet.status).toLowerCase() === "error") return { label: "error", status: "error", tone: "risk" };
  if (age > 60 * 60 * 1000) return { label: "stale", status: "stale", tone: "watch" };
  return { label: "fresh", status: "fresh", tone: "clear" };
}

function cryptoStatusClass(value?: string | null) {
  const status = String(value || "").toLowerCase();
  if (status === "ready" || status === "fresh" || status === "low" || status === "finite" || status === "low risk") return "is-clear";
  if (status === "attention" || status === "stale" || status === "low" || status === "medium") return "is-watch";
  if (status === "error" || status === "empty" || status === "revoke recommended" || status === "high") return "is-risk";
  return "is-muted";
}

function chainLabel(value?: string) {
  const raw = String(value || "");
  if (raw.toLowerCase() === "base") return "Base";
  if (raw.toLowerCase() === "ethereum") return "Ethereum";
  if (raw.toLowerCase() === "solana") return "Solana";
  if (raw.toLowerCase() === "all") return "All";
  return raw || "Chain";
}

function amountLabel(value?: number, symbol?: string) {
  if (typeof value !== "number" || Number.isNaN(value)) return `0 ${symbol || ""}`.trim();
  const amount = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: Math.abs(value) >= 100 ? 2 : Math.abs(value) >= 1 ? 4 : 6,
  }).format(value);
  return `${amount} ${symbol || ""}`.trim();
}

function AgenticCryptoPanel({
  wallet,
  loading,
  onRefresh,
}: {
  wallet?: AgenticCryptoWallet;
  loading: boolean;
  onRefresh: () => void;
}) {
  const freshness = cryptoFreshness(wallet);
  const summary = wallet?.summary || {};
  const chains = wallet?.chains || [];
  const tokens = wallet?.tokens || [];
  const nfts = wallet?.nfts || [];
  const errors = wallet?.errors || [];
  return (
    <section id="agentic-crypto" className={`agentic-crypto-panel is-${freshness.tone}`} aria-label="Agentic Crypto wallet status">
      <header className="crypto-wallet-header">
        <div className="crypto-wallet-brand">
          <span><WalletCards size={15} /></span>
          <div>
            <p>Agentic Crypto</p>
            <h2>Josh 2.0 wallet</h2>
          </div>
        </div>
        <div className="crypto-title-actions">
          <span className={`crypto-status ${cryptoStatusClass(freshness.status)}`}><ShieldCheck size={13} />{freshness.label}</span>
          <button type="button" onClick={onRefresh} disabled={loading} title="Refresh read-only wallet inventory">
            <RefreshCw size={13} className={loading ? "spin" : ""} /> Refresh
          </button>
        </div>
      </header>

      <section className="crypto-balance-card">
        <span>Total balance</span>
        <strong>{fmtCurrencyExact(summary.totalEstimatedUsd)}</strong>
        <div>
          <em>{fmtCurrencyExact(summary.liquidEstimatedUsd)} liquid</em>
          <em>{fmtCurrencyExact(summary.nftEstimatedUsd)} collectibles</em>
          <em>Updated {ageLabel(wallet?.updatedAt)}</em>
        </div>
      </section>

      <div className="crypto-account-chips">
        <span>EVM {wallet?.wallets?.evmMasked || "not configured"}</span>
        <span>Solana {wallet?.wallets?.solanaMasked || "not configured"}</span>
        <em>{wallet?.walletMode || "read-only"}</em>
      </div>

      <div className="crypto-chain-pills" aria-label="Chain gas balances">
        {chains.length ? chains.map((chain) => (
          <article key={chain.chain} className={cryptoStatusClass(chain.gasStatus)}>
            <span>{chainLabel(chain.chain)}</span>
            <strong>{amountLabel(chain.gasBalance, chain.gasSymbol)}</strong>
            <em>{chain.gasStatus || "unknown"} · {fmtCurrencyExact(chain.gasValueUsd)}</em>
          </article>
        )) : (
          <article className="is-watch">
            <span>Wallet data</span>
            <strong>Not loaded</strong>
            <em>Refresh inventory</em>
          </article>
        )}
      </div>

      <section className="crypto-wallet-section">
        <header><Coins size={13} /> Assets</header>
        <div className="crypto-wallet-list">
          {(tokens.length ? tokens : []).slice(0, 4).map((token) => (
            <article key={`${token.chain}-${token.symbol}-${token.contractMasked || token.mintMasked || ""}`}>
              <div>
                <strong title={token.name || token.symbol}>{token.symbol}</strong>
                <span>{chainLabel(token.chain)} · {amountLabel(token.amount)}</span>
              </div>
              <em>{fmtCurrencyExact(token.valueUsd)}</em>
            </article>
          ))}
          {!tokens.length ? <p>No token rows loaded yet.</p> : null}
        </div>
      </section>

      <div className="crypto-wallet-lower">
        <section className="crypto-wallet-section is-compact">
          <header><WalletCards size={13} /> Collectibles</header>
          <div className="crypto-wallet-list">
            {(nfts.length ? nfts : []).slice(0, 2).map((nft) => (
              <article key={`${nft.chain}-${nft.collection}`}>
                <div>
                  <strong title={nft.collection}>{compactText(nft.collection, 24)}</strong>
                  <span>{chainLabel(nft.chain)} · {nft.tokenStandard || "NFT"}</span>
                </div>
                <em>{nft.count || 0} held</em>
              </article>
            ))}
            {!nfts.length ? <p>NFT inventory loads on full refresh.</p> : null}
          </div>
        </section>

        <section className="crypto-wallet-section is-compact">
          <header>Activity</header>
          <div className="crypto-wallet-list">
            {(wallet?.recentActivity || []).slice(0, 2).map((row, index) => (
              <article key={`${row.chain}-${row.timestamp}-${index}`}>
                <div>
                  <strong title={row.action}>{compactText(row.action, 26)}</strong>
                  <span>{chainLabel(row.chain)} · {ageLabel(row.timestamp)}</span>
                </div>
                {row.explorerUrl ? (
                  <a href={row.explorerUrl} target="_blank" rel="noreferrer" title="Open block explorer transaction">
                    {row.explorerLabel || "Explorer"} <ExternalLink size={10} />
                  </a>
                ) : <em>{row.status || "read-only"}</em>}
              </article>
            ))}
            {!wallet?.recentActivity?.length ? <p>No activity loaded.</p> : null}
          </div>
        </section>
      </div>

      {errors.length ? (
        <footer className="crypto-errors" title="One or more read-only sources were unavailable during refresh.">
          {errors.length} refresh note{errors.length === 1 ? "" : "s"} · wallet view remains read-only.
        </footer>
      ) : (
        <footer className="crypto-errors is-clear">Read-only view. Writes still require simulation and approval.</footer>
      )}
    </section>
  );
}

function SignalFeed({ state, quietMode, liveCues }: { state: MissionControlState; quietMode: boolean; liveCues: LiveCueState }) {
  const freshness = signalFreshnessSummary(state);
  const topFive = signalRows(state.signals, false);
  const lastFive = signalRows(state.signals, true);
  const rowsShown = topFive.length + lastFive.length;
  return (
    <section id="signal-feed" className={`signal-feed${sectionCueClass("system", liveCues)}`} aria-label="J.A.I.N signal archive">
      <SectionCue label={liveCues.focus === "system" ? "focus" : "updated"} />
      <header className="panel-title compact">
        <div>
          <p>J.A.I.N context</p>
          <h2>Signal Archive</h2>
        </div>
        <span className={`signal-freshness is-${freshness.tone}`}>
          <Radio size={14} /> {rowsShown} showing · {quietMode ? "quiet" : freshness.label}
        </span>
      </header>
      <div className="signal-table">
        <div className="signal-section-label">
          <strong>Top five</strong>
          <span>Developing or breaking stories</span>
        </div>
        <SignalFeedRows rows={topFive} liveCues={liveCues} emptyLabel="No live breaking rows loaded." />
        <div className="signal-section-label">
          <strong>Last 5</strong>
          <span>Newsletter subscription trends</span>
        </div>
        <SignalFeedRows rows={lastFive} liveCues={liveCues} emptyLabel="No newsletter trend rows loaded." newsletter />
      </div>
    </section>
  );
}

function SignalFeedRows({ rows, liveCues, emptyLabel, newsletter = false }: { rows: SignalItem[]; liveCues: LiveCueState; emptyLabel: string; newsletter?: boolean }) {
  if (!rows.length) {
    return (
      <article className="signal-live-empty">
        <span>{newsletter ? "Digest" : "Live"}</span>
        <div className="signal-story">
          <strong>{emptyLabel}</strong>
          <p>Refresh the archived signal pipeline to repopulate this lane.</p>
        </div>
        <p className="signal-impact">No current summary available.</p>
        <em className="signal-source">Control Tower</em>
        <time>--</time>
      </article>
    );
  }
  return (
    <>
      {rows.map((signal) => {
        const changed = Boolean(liveCues.rows[cueRowKey("signal", signal.id || signal.title)]);
        const impact = signal.impact || signal.impactScenarios?.medium || signal.impactScenarios?.med || signal.reason;
        return (
          <article key={signal.id || signal.title} className={`${signalRowClass(signal)}${changedRowClass(changed)}`}>
            <span className="row-change-dot" aria-hidden="true" />
            <span>{signalScoreLabel(signal)}</span>
            <div className="signal-story">
              <strong title={missionText(signal.title)}>{missionText(signal.title)}</strong>
              <p title={missionText(signal.reason)}>{missionText(signal.reason)}</p>
            </div>
            <p className="signal-impact" title={missionText(impact)}>{missionText(impact)}</p>
            <em className="signal-source" title={missionText(signal.source)}>{compactText(signal.source, 22)}</em>
            <time title={fmtTime(signal.time)}>
              <b>{freshnessClass(signal.time) === "is-fresh" ? "live" : ageLabel(signal.time)}</b>
              {fmtTime(signal.time)}
            </time>
          </article>
        );
      })}
    </>
  );
}

function providerKey(provider: any) {
  const text = `${provider?.id || ""} ${provider?.label || ""} ${provider?.role || ""} ${provider?.lastModelUsed || ""}`.toLowerCase();
  if (/gemini|google/.test(text)) return "gemini";
  if (/ollama|local/.test(text)) return "ollama";
  if (/xai|grok/.test(text)) return "xai";
  if (/openrouter/.test(text)) return "openrouter";
  if (/anthropic|claude/.test(text)) return "anthropic";
  if (/openai|codex|gpt/.test(text)) return "openai";
  return String(provider?.id || provider?.label || "provider").toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

function providerRows(modelUsage?: MissionControlState["modelUsage"], modelRouter?: MissionControlState["modelRouter"]) {
  const lastRoute = modelRouter?.lastRoute || {};
  const codexMode = String(modelRouter?.codexAllowanceMode || modelRouter?.policy?.codexAllowanceMode || modelUsage?.routerPolicy?.codexAllowanceMode || "conserve");
  const defaults = [
    {
      id: "openai",
      label: "Codex / OpenAI",
      role: "Trusted execution, code edits, private connectors, terminal work, and final integration.",
      whyChosen: "Default when the task needs system access, code changes, approvals, or high-confidence execution.",
      lastModelUsed: /openai|codex|gpt/i.test(`${lastRoute.provider || ""} ${lastRoute.model || ""}`) ? lastRoute.model : "openai/gpt-5.5",
      budgetType: "subscription",
      plan: "OpenAI Pro",
      subscriptionMonthlyUsd: 200,
      billingLabel: "$200/mo",
      billingNote: "Daily driver; usage windows show allowance pressure, not incremental API billing.",
      status: "ready",
    },
    {
      id: "gemini",
      label: "Gemini",
      role: "Low-cost front-desk review, large-context reading, summaries, and secondary checks.",
      whyChosen: "Use Flash when safe for dashboard-safe synthesis; use Pro for judgment and escalation.",
      lastModelUsed: /gemini|google/i.test(`${lastRoute.provider || ""} ${lastRoute.model || ""}`) ? lastRoute.model : "gemini flash / pro",
      budgetType: "subscription",
      plan: "Gemini Pro",
      subscriptionMonthlyUsd: 20,
      billingLabel: "$20/mo",
      status: "ready",
    },
    {
      id: "ollama",
      label: "Ollama",
      role: "Local/private helper lane for drafts, compression, and low-risk offline utility work.",
      whyChosen: "Use when local model quality is sufficient and cloud subscription quota should be preserved.",
      lastModelUsed: /ollama|local/i.test(`${lastRoute.provider || ""} ${lastRoute.model || ""}`) ? lastRoute.model : "local/ollama",
      budgetType: "subscription",
      plan: "Ollama Pro",
      subscriptionMonthlyUsd: 16.67,
      subscriptionAnnualUsd: 200,
      subscriptionCadence: "annual",
      billingLabel: "$200/yr",
      status: "ready",
    },
    {
      id: "xai",
      label: "xAI / Grok",
      role: "X-native, current-events, social signal, and fast outside-world scans.",
      whyChosen: "Use when the task benefits from X context, current public signal, or Grok-specific subscription capacity.",
      lastModelUsed: /xai|grok/i.test(`${lastRoute.provider || ""} ${lastRoute.model || ""}`) ? lastRoute.model : "grok subscription",
      budgetType: "subscription",
      plan: "X Premium",
      subscriptionMonthlyUsd: 8,
      billingLabel: "$8/mo",
      status: "ready",
    },
    {
      id: "openrouter",
      label: "OpenRouter",
      role: "Fallback and specialist reserve when primary lanes are unavailable or a niche model is needed.",
      whyChosen: "Keep dim unless actively selected by failover or a specialist workflow.",
      lastModelUsed: /openrouter/i.test(`${lastRoute.provider || ""} ${lastRoute.model || ""}`) ? lastRoute.model : "fallback pool",
      budgetType: "fallback cap",
      status: "reserve",
    },
  ];
  const rows = [...defaults, ...(modelRouter?.providers || []), ...(modelUsage?.providerBudgets || [])];
  const byKey = new Map<string, any>();
  rows.forEach((row) => {
    const key = providerKey(row);
    const existing = byKey.get(key);
    byKey.set(key, existing ? {
      ...existing,
      ...row,
      id: row?.id || existing.id,
      label: row?.label || existing.label,
      role: row?.role || existing.role,
      whyChosen: row?.whyChosen || existing.whyChosen,
      lastModelUsed: row?.lastModelUsed || existing.lastModelUsed,
      budgetType: row?.budgetType || existing.budgetType,
    } : row);
  });
  const preferred = ["openai", "gemini", "ollama", "xai", "openrouter"];
  const ordered = preferred.map((key) => byKey.get(key)).filter(Boolean);
  const extras = [...byKey.entries()].filter(([key]) => !preferred.includes(key)).map(([, row]) => row);
  return [...ordered, ...extras];
}

function activeProviderKeys(statuses: AgentStatus[], modelRouter?: MissionControlState["modelRouter"]) {
  const keys = new Set<string>();
  statuses
    .filter((status) => {
      const value = String(status.status || "").toLowerCase();
      return (status.active || value === "active" || value === "working") && isFreshActiveTimestamp(status.updated_at);
    })
    .forEach((status) => {
    const text = `${status.objective} ${status.detail} ${status.current_tool} ${status.steps?.map((step) => `${step.tool || ""} ${step.label || ""}`).join(" ")}`.toLowerCase();
    if (/gemini|google/.test(text)) keys.add("gemini");
    if (/ollama|local model|local\/ollama/.test(text)) keys.add("ollama");
    if (/xai|grok/.test(text)) keys.add("xai");
      if (/openrouter/.test(text)) keys.add("openrouter");
      if (/anthropic|claude/.test(text)) keys.add("anthropic");
      if (/openai|codex|gpt/.test(text)) keys.add("openai");
    });
  const lastRoute = modelRouter?.lastRoute || {};
  const routeFresh = isFreshActiveTimestamp(String(lastRoute.updatedAt || modelRouter?.updatedAt || ""));
  if (routeFresh) {
    const text = `${lastRoute.provider || ""} ${lastRoute.model || ""} ${lastRoute.routeLabel || ""}`.toLowerCase();
    if (/gemini|google/.test(text)) keys.add("gemini");
    if (/ollama|local/.test(text)) keys.add("ollama");
    if (/xai|grok/.test(text)) keys.add("xai");
    if (/openrouter/.test(text)) keys.add("openrouter");
    if (/anthropic|claude/.test(text)) keys.add("anthropic");
    if (/openai|codex|gpt/.test(text)) keys.add("openai");
  }
  return keys;
}

function providerUtilizationPct(provider: any) {
  const explicit = Number(provider?.dailyUtilizationPct ?? provider?.monthlyUtilizationPct);
  if (Number.isFinite(explicit) && explicit > 0) return Math.min(100, Math.round(explicit));
  const windows = providerLimitRows(provider);
  const windowPct = Math.max(...windows.map((window) => Number(window?.usedPercent || 0)).filter(Number.isFinite), 0);
  if (windowPct > 0) return Math.min(100, Math.round(windowPct));
  const spend = Number(provider?.dailySpendUsd ?? provider?.monthlySpendUsd ?? 0);
  const cap = Number(provider?.dailyCapUsd ?? provider?.monthlyCapUsd ?? 0);
  if (Number.isFinite(spend) && Number.isFinite(cap) && cap > 0) return Math.min(100, Math.round((spend / cap) * 100));
  return 0;
}

function providerLimitRows(provider: any) {
  const windows = Array.isArray(provider?.usageWindows) ? provider.usageWindows : [];
  return windows
    .filter((window: any) => window && (window.label || window.remainingLabel || Number.isFinite(Number(window.usedPercent))))
    .slice(0, 5);
}

function providerWindowValue(window: any) {
  if (window?.remainingLabel) return missionText(String(window.remainingLabel));
  const remaining = Number(window?.remainingPercent);
  if (Number.isFinite(remaining)) return `${Math.round(remaining)}% left`;
  const used = Number(window?.usedPercent);
  if (Number.isFinite(used)) return `${Math.round(used)}% used`;
  return missionText(String(window?.status || "tracked"));
}

function providerSpendLabel(provider: any) {
  const windows = providerLimitRows(provider);
  if (windows.length) {
    return windows.slice(0, 2).map((window) => `${missionText(String(window.label || "Limit"))} ${providerWindowValue(window)}`).join(" · ");
  }
  const spend = Number(provider?.dailySpendUsd ?? 0);
  const cap = Number(provider?.dailyCapUsd ?? provider?.monthlyCapUsd ?? 0);
  if (provider?.remainingCreditUsd != null) return `${fmtCurrencyExact(provider.remainingCreditUsd)} remaining`;
  if (cap) return `${fmtCurrencyExact(spend)} used / ${fmtCurrencyExact(cap)} cap`;
  if (provider?.budgetType) return `${missionText(provider.budgetType)} usage tracked`;
  return "Usage tracked from route telemetry";
}

function providerDisplayBlurb(provider: any) {
  const key = providerKey(provider);
  if (key === "openai") return "Execution lane for code, tools, auth, private connectors, and final changes.";
  if (key === "gemini") return "Low-cost reading, review, summaries, and judgment escalation.";
  if (key === "ollama") return "Local drafts, compression, and low-risk offline utility.";
  if (key === "xai") return "X-native signal, current events, and Grok context.";
  if (key === "openrouter") return "Fallback or specialist reserve when primary lanes are unavailable.";
  const raw = missionText(String(provider?.whyChosen || provider?.role || "Available when route policy selects it."));
  return raw.length > 84 ? `${raw.slice(0, 81).trim()}...` : raw;
}

function providerSubscriptionMonthly(provider: any) {
  const monthly = Number(provider?.subscriptionMonthlyUsd ?? provider?.monthlySubscriptionUsd);
  if (Number.isFinite(monthly) && monthly > 0) return monthly;
  const annual = Number(provider?.subscriptionAnnualUsd);
  if (Number.isFinite(annual) && annual > 0) return annual / 12;
  if (String(provider?.budgetType || "").toLowerCase() === "subscription") {
    const cap = Number(provider?.monthlyCapUsd);
    if (Number.isFinite(cap) && cap > 0) return cap;
  }
  return 0;
}

function providerBillingLabel(provider: any) {
  if (provider?.billingLabel) return missionText(String(provider.billingLabel));
  const annual = Number(provider?.subscriptionAnnualUsd);
  if (Number.isFinite(annual) && annual > 0) return `${fmtCurrencyExact(annual)}/yr`;
  const monthly = providerSubscriptionMonthly(provider);
  if (monthly > 0) return `${fmtCurrencyExact(monthly)}/mo`;
  return providerSpendLabel(provider);
}

function subscriptionBaselineUsd(providers: any[]) {
  return providers.reduce((total, provider) => total + providerSubscriptionMonthly(provider), 0);
}

function usagePressureLabel(providers: any[]) {
  const codex = providers.find((provider) => providerKey(provider) === "openai");
  const windows = providerLimitRows(codex);
  const weekly = windows.find((window: any) => /week/i.test(String(window?.label || window?.id || "")));
  const session = windows.find((window: any) => /session/i.test(String(window?.label || window?.id || "")));
  const target = weekly || session || windows[0];
  if (target) return `${missionText(String(target.label || "Limit"))} ${providerWindowValue(target)}`;
  return "allowance tracked";
}

function providerTone(provider: any) {
  const text = `${provider?.status || ""} ${provider?.authStatus || ""} ${provider?.lastTestStatus || ""}`.toLowerCase();
  if (/blocked|error|missing|failed/.test(text)) return "risk";
  if (/watch|reserve|limited|stale|attention/.test(text)) return "watch";
  return "clear";
}

function providerEvidenceLabel(provider: any) {
  const account = provider?.accountEmail || provider?.accountLabel;
  if (provider?.plan || account) return [provider?.plan, account].filter(Boolean).map((part) => missionText(String(part))).join(" · ");
  if (provider?.codexbarSource) return `CodexBar ${missionText(String(provider.codexbarSource))}`;
  const status = provider?.authStatus || provider?.lastTestStatus || provider?.status;
  if (status) return missionText(String(status));
  if (provider?.keyPresent === true) return "verified key present";
  if (provider?.keyPresent === false) return "no key expected";
  return "estimated from route telemetry";
}

function numericOrZero(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function walletTradingGoal(wallet?: AgenticCryptoWallet) {
  const raw = wallet?.tradingGoal || {};
  const solToken = wallet?.tokens?.find((token) => String(token.symbol || "").toUpperCase() === "SOL");
  const current = numericOrZero(raw.current ?? (raw as any).lockedProfitSol ?? (raw as any).profitLockedSol ?? solToken?.amount);
  const target = numericOrZero(raw.target || 3) || 3;
  const unit = raw.unit || "SOL";
  const percent = Math.max(0, Math.min(100, Math.round((current / target) * 100)));
  return {
    title: raw.title || "3 SOL net-profit target",
    description: raw.description || "Trade toward 3 SOL net profit and lock realized gains as SOL.",
    current,
    target,
    unit,
    percent,
    status: missionText(raw.status || "tracking"),
  };
}

function walletTradeRows(wallet?: AgenticCryptoWallet) {
  const explicit = wallet?.tradeLedger;
  if (explicit?.length) return explicit.slice(0, 2);
  return (wallet?.recentActivity || []).slice(0, 2).map((row) => ({
    timestamp: row.timestamp,
    side: String(row.action || "").toLowerCase().includes("approve") ? "approve" : "activity",
    action: row.action || "Wallet activity",
    amount: row.valueSummary || "not classified",
    pnl: null,
    status: row.status,
    chain: row.chain,
    explorerLabel: row.explorerLabel,
    explorerUrl: row.explorerUrl,
  }));
}

function tradeSideLabel(row: NonNullable<AgenticCryptoWallet["tradeLedger"]>[number]) {
  const side = String(row.side || row.action || "activity").toLowerCase();
  if (side.includes("open")) return "Open";
  if (side.includes("close")) return "Close";
  if (side.includes("swap")) return "Swap";
  if (side.includes("rebalance") || side.includes("bridge")) return "Rebalance";
  if (side.includes("approve")) return "Approve";
  return "Activity";
}

function tradeAmountLabel(row: NonNullable<AgenticCryptoWallet["tradeLedger"]>[number]) {
  if (row.amount != null && row.amount !== "") return String(row.amount);
  if (typeof row.valueUsd === "number") return fmtCurrencyExact(row.valueUsd);
  return "amount n/a";
}

function tradePnl(row: NonNullable<AgenticCryptoWallet["tradeLedger"]>[number]) {
  const pnlSol = typeof row.pnlSol === "number" ? `${row.pnlSol >= 0 ? "+" : ""}${amountLabel(row.pnlSol, "SOL")}` : "";
  const pnlUsd = typeof row.pnlUsd === "number" ? `${row.pnlUsd >= 0 ? "+" : ""}${fmtCurrencyExact(row.pnlUsd)}` : "";
  const raw = row.pnl != null ? String(row.pnl) : "";
  const label = pnlSol || pnlUsd || raw || "PnL n/a";
  const numeric = numericOrZero(row.pnlSol ?? row.pnlUsd ?? row.pnl);
  const tone = numeric > 0 ? "positive" : numeric < 0 ? "negative" : "neutral";
  return { label, tone };
}

function FinOpsDashboard({
  wallet,
  modelUsage,
  modelRouter,
  statuses,
  loading,
  onRefresh,
}: {
  wallet?: AgenticCryptoWallet;
  modelUsage?: MissionControlState["modelUsage"];
  modelRouter?: MissionControlState["modelRouter"];
  statuses: AgentStatus[];
  loading: boolean;
  onRefresh: () => void;
}) {
  const freshness = cryptoFreshness(wallet);
  const summary = wallet?.summary || {};
  const goal = walletTradingGoal(wallet);
  const trades = walletTradeRows(wallet);
  const providers = providerRows(modelUsage, modelRouter);
  const activeKeys = activeProviderKeys(statuses, modelRouter);
  const subscriptionBaseline = subscriptionBaselineUsd(providers);
  const usagePressure = usagePressureLabel(providers);
  const routeQuality = typeof modelRouter?.routeQualityScore === "number" ? `${modelRouter.routeQualityScore}/100` : "--";
  const efficiency = typeof modelRouter?.efficiencyScore === "number" ? `${modelRouter.efficiencyScore}/100` : "--";
  const codexMode = String(modelRouter?.codexAllowanceMode || modelRouter?.policy?.codexAllowanceMode || modelUsage?.routerPolicy?.codexAllowanceMode || "normal");
  const lastRoute = modelRouter?.lastRoute || {};
  const lastRouteLabel = missionText(String(lastRoute.routeLabel || lastRoute.provider || "no active route"));
  return (
    <section id="finops-dashboard" className={`finops-dashboard is-${freshness.tone}`} aria-label="FinOps dashboard">
      <header className="finops-header">
        <div>
          <p>FinOps Dashboard</p>
          <h2>Subscription baseline, model load, and wallet guardrails</h2>
        </div>
        <div className="finops-actions">
          <span className={`crypto-status ${cryptoStatusClass(freshness.status)}`}><ShieldCheck size={13} />Wallet {freshness.label}</span>
          <span><Timer size={13} />Auto-refresh 5m</span>
          <button type="button" onClick={onRefresh} disabled={loading} title="Refresh read-only wallet inventory">
            <RefreshCw size={13} className={loading ? "spin" : ""} /> Refresh wallet
          </button>
        </div>
      </header>

      <div className="finops-body">
        <section className="finops-wallet">
          <div className="finops-wallet-total">
            <span>Crypto wallet</span>
            <strong>{fmtCurrencyExact(summary.totalEstimatedUsd)}</strong>
            <p>{fmtCurrencyExact(summary.liquidEstimatedUsd)} liquid · {fmtCurrencyExact(summary.nftEstimatedUsd)} collectibles</p>
          </div>
          <div className="finops-wallet-target">
            <div className="wallet-target-head">
              <div>
                <span>Current target</span>
                <strong>{goal.title}</strong>
              </div>
              <em>{goal.status}</em>
            </div>
            <div className="wallet-target-progress" style={{ "--pct": goal.percent } as React.CSSProperties}>
              <span />
            </div>
            <div className="wallet-target-meta">
              <b>{amountLabel(goal.current, goal.unit)} / {amountLabel(goal.target, goal.unit)}</b>
              <small>{goal.percent}% complete</small>
            </div>
            <p>{goal.description}</p>
          </div>
          <div className="finops-trade-ledger">
            <header>
              <div>
                <span>Recent wallet trades</span>
                <strong>Open / close ledger</strong>
              </div>
              <em>{trades.length || 0} rows</em>
            </header>
            <div className="trade-ledger-list">
              {trades.length ? trades.map((trade, index) => {
                const pnl = tradePnl(trade);
                return (
                  <article key={`${trade.timestamp || "trade"}-${trade.explorerLabel || index}`} className="trade-ledger-row">
                    <span className={`trade-side-pill is-${String(trade.side || "activity").toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}>
                      {tradeSideLabel(trade)}
                    </span>
                    <div className="trade-main">
                      <strong>{missionText(trade.action || trade.pair || trade.asset || "Wallet activity")}</strong>
                      <small>{chainLabel(trade.chain)} · {tradeAmountLabel(trade)}</small>
                    </div>
                    <span className={`trade-pnl is-${pnl.tone}`}>{pnl.label}</span>
                    {trade.explorerUrl ? (
                      <a href={trade.explorerUrl} target="_blank" rel="noreferrer" title={trade.explorerLabel || "Open transaction"}>
                        <ExternalLink size={12} />
                      </a>
                    ) : <i />}
                  </article>
                );
              }) : (
                <p className="wallet-empty-state">No trade ledger is loaded yet. Wallet activity will appear after the next refresh.</p>
              )}
            </div>
          </div>
        </section>

        <section className="finops-models">
          <div className="finops-model-summary">
            <article>
              <span>Subscriptions</span>
              <strong>{fmtCurrencyExact(subscriptionBaseline)}/mo</strong>
            </article>
            <article>
              <span>Daily driver</span>
              <strong>OpenAI Pro</strong>
            </article>
            <article>
              <span>Usage pressure</span>
              <strong>{usagePressure}</strong>
            </article>
            <article>
              <span>Route / efficiency</span>
              <strong>{routeQuality} · {efficiency}</strong>
            </article>
          </div>
          <div className="finops-route-strip">
            <span>Codex allowance: {missionText(codexMode)}</span>
            <span>Last route: {lastRouteLabel}</span>
            <span>Updated {fmtTime(modelUsage?.lastUpdated || modelRouter?.updatedAt)}</span>
          </div>
          <div className="finops-provider-grid">
            {providers.length ? providers.slice(0, 4).map((provider) => {
              const key = providerKey(provider);
              const active = activeKeys.has(key);
              const pct = providerUtilizationPct(provider);
              const tone = providerTone(provider);
              const limits = providerLimitRows(provider);
              return (
                <article key={provider.id || key} data-provider={key} className={`finops-provider-card is-${tone} ${active ? "is-active" : "is-idle"}`}>
                  <header>
                    <span className="provider-glow-dot" />
                    <div>
                      <strong>{provider.label || provider.id || key}</strong>
                      <em>{active ? "in use now" : "idle"}</em>
                    </div>
                  </header>
                  <p>{providerDisplayBlurb(provider)}</p>
                  {limits.length ? (
                    <div className="provider-limit-list">
                      {limits.map((window: any) => {
                        const windowPct = Number(window?.usedPercent || 0);
                        const meterPct = Number.isFinite(windowPct) ? Math.max(0, Math.min(100, Math.round(windowPct))) : 0;
                        return (
                          <div key={window.id || window.label} className={`provider-limit-row is-${window.status || "ok"}`}>
                            <span>{missionText(String(window.label || "Limit"))}</span>
                            <div className="provider-limit-meter" style={{ "--pct": meterPct } as React.CSSProperties}>
                              <i />
                            </div>
                            <em>{providerWindowValue(window)}</em>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="provider-budget-meter" style={{ "--pct": pct } as React.CSSProperties}>
                      <span />
                    </div>
                  )}
                  <footer>
                    <span>{provider.lastModelUsed || "model route available"}</span>
                    <em>{providerBillingLabel(provider)}</em>
                  </footer>
                  <small>{providerEvidenceLabel(provider)}</small>
                </article>
              );
            }) : (
              <article className="finops-provider-card is-watch">
                <header>
                  <span className="provider-glow-dot" />
                  <div>
                    <strong>Provider budgets</strong>
                    <em>not loaded</em>
                  </div>
                </header>
                <p>Route telemetry will appear after the next model-usage refresh.</p>
              </article>
            )}
          </div>
        </section>
      </div>
    </section>
  );
}

function BrainAttentionStrip({ state, quietMode, onNavigate }: { state: MissionControlState; quietMode: boolean; onNavigate: (target: AttentionTarget) => void }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const pendingApprovals = state.approvals.filter((row) => row.status === "pending");
  const trackedJobs = operatorTrackedJobs(state.jobs);
  const riskJobs = trackedJobs.filter((job) => jobNeedsAttention(job, trackedJobs));
  const blockedAgents = state.statuses.filter(agentNeedsFocus);
  const items: AttentionItem[] = [];

  dataQualityIssues(state).forEach((issue) => items.push(issue));
  pendingApprovals.slice(0, 2).forEach((approval) => {
    items.push({
      id: `approval-${approval.id}`,
      label: "Decision",
      title: missionText(approval.title),
      detail: approvalAlertReason(approval),
      why: approvalAlertReason(approval),
      means: "An agent is waiting before it should continue. This is the only class of alert that needs a Josh decision.",
      action: "Open the related job or approval lane, then approve, hold, or deny from the source that requested it.",
      tone: "risk",
      target: "today-jobs",
    });
  });
  if (riskJobs.length) {
    const firstJob = riskJobs[0];
    items.push({
      id: `jobs-${firstJob.id}`,
      label: "Needs focus",
      title: `${riskJobs.length} job${riskJobs.length === 1 ? "" : "s"} need focus`,
      detail: jobAlertReason(firstJob),
      why: jobAlertReason(firstJob),
      means: "A scheduled or priority job reported blocked, error, or missed. Control Tower is surfacing the first affected row so you can inspect it quickly.",
      action: "Open Today's Jobs and inspect the highlighted row first.",
      tone: "risk",
      target: "today-jobs",
    });
  }
  if (blockedAgents.length) {
    const firstAgent = blockedAgents[0];
    items.push({
      id: `agent-${firstAgent.agent_id}`,
      label: "Agents",
      title: `${blockedAgents.length} agent${blockedAgents.length === 1 ? "" : "s"} need focus`,
      detail: agentAlertReason(firstAgent),
      why: agentAlertReason(firstAgent),
      means: `${AGENTS[firstAgent.agent_id]?.label || firstAgent.agent_id} reported a blocked state or stopped giving a healthy status signal.`,
      action: "Open Live Work Board, check the agent tile, and send a test message if the last check-in is late.",
      tone: "risk",
      target: "brain-feed",
    });
  }
  const visibleItems = (quietMode ? items.filter((item) => item.tone !== "clear") : items).slice(0, 1);
  const overflowCount = Math.max(0, items.length - visibleItems.length);
  const selected = visibleItems.find((item) => item.id === selectedId) || null;

  return (
    <section className="brain-attention-strip" aria-label="What needs attention">
      {visibleItems.length ? visibleItems.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`brain-attention-item is-${item.tone} ${selected?.id === item.id ? "selected" : ""}`}
            onClick={() => setSelectedId((current) => current === item.id ? null : item.id)}
            title="Show why this alert is here"
          >
            <span>{item.label}</span>
            <strong>{overflowCount ? `${item.title} · +${overflowCount}` : item.title}</strong>
            <p>{item.detail}</p>
          </button>
        )) : (
          <article className="brain-alerts-clear">
            <span>{quietMode ? "Quiet mode" : "All clear"}</span>
            <strong>No alerts</strong>
          </article>
        )}
      {selected ? (
        <article className={`attention-detail-drawer is-${selected.tone}`}>
          <header>
            <span>{selected.label}</span>
            <strong>{selected.title}</strong>
          </header>
          <p><b>What happened:</b> {selected.why.replace(/^Why:\s*/i, "")}</p>
          <p><b>Why it matters:</b> {selected.means}</p>
          <p><b>Suggested action:</b> {selected.action}</p>
          <footer>
            <button type="button" onClick={() => onNavigate(selected.target)}>Open related section</button>
            <button type="button" onClick={() => setSelectedId(null)}>Dismiss detail</button>
          </footer>
        </article>
      ) : null}
    </section>
  );
}

function AgentHandoffBeams({ state }: { state: MissionControlState }) {
  const beams = buildHandoffBeams(state);
  return (
    <div className={`handoff-beam-layer ${beams.length ? "has-active-beams" : "is-standby"}`} aria-label="Recent agent handoffs">
      {beams.length ? beams.map((beam) => {
        const fromIndex = Math.max(0, HERO_AGENT_ORDER.indexOf(beam.from));
        const toIndex = Math.max(0, HERO_AGENT_ORDER.indexOf(beam.to));
        const leftCenter = Math.min(fromIndex, toIndex) * 33.333 + 16.666;
        const rightCenter = Math.max(fromIndex, toIndex) * 33.333 + 16.666;
        return (
          <span
            key={beam.id}
            className={`handoff-beam is-${beam.tone} ${fromIndex > toIndex ? "is-reverse" : ""}`}
            style={{
              "--beam-left": `${leftCenter}%`,
              "--beam-width": `${Math.max(8, rightCenter - leftCenter)}%`,
            } as React.CSSProperties}
            title={beam.label}
          >
            <em>{beam.label}</em>
          </span>
        );
      }) : (
        <span className="handoff-standby">Routing bus standing by</span>
      )}
    </div>
  );
}

function approvalAlertReason(approval: Approval) {
  const owner = AGENTS[approval.agent_id]?.label || approval.agent_id;
  return `Why: ${missionText(approval.detail || `${owner} is waiting for Josh to approve or deny.`)}`;
}

function jobAlertReason(job?: MissionControlState["jobs"][number]) {
  if (!job) return "Why: Today's Jobs has a blocked or failed row.";
  const owner = AGENTS[job.agent_id]?.label || job.agent_id;
  const reason = missionText(job.detail || job.tool || job.title || "Blocked or failed job.");
  return `Why: ${owner} reported ${displayStatus(job.status).toLowerCase()} - ${reason}`;
}

function agentAlertReason(status?: AgentStatus) {
  if (!status) return "Why: An agent reported a blocked or failed state.";
  const owner = AGENTS[status.agent_id]?.label || status.agent_id;
  const reason = missionText(status.detail || status.objective || status.current_tool || "No extra detail reported.");
  return `Why: ${owner} is ${agentOperatingState(status).toLowerCase()} - ${reason}`;
}

function RuntimeCapabilityPanel({ state }: { state: MissionControlState }) {
  const stack = state.capabilityStack || [];
  const inventory = state.capabilityInventory;
  const watch = state.capabilityWatch;
  const nodes = Array.isArray(inventory?.nodes) ? inventory.nodes : [];
  const runtimeItems = stack.filter((item) => [
    "runtime-inventory",
    "task-ledger",
    "capability-watch",
    "agent-control",
  ].includes(item.id));
  if (!runtimeItems.length && !nodes.length) return null;
  const readyTools = nodes.reduce((sum, node: any) => {
    return sum
      + (node?.openclawCli?.available ? 1 : 0)
      + (node?.hermesCli?.available ? 1 : 0)
      + (node?.geminiCli?.available ? 1 : 0)
      + (node?.codexCli?.available ? 1 : 0);
  }, 0);
  const attention = runtimeItems.filter((item) => reliabilityTone(item.status) === "is-risk").length;
  const watchCount = runtimeItems.filter((item) => reliabilityTone(item.status) === "is-watch").length;
  const headline = attention ? `${attention} attention` : watchCount ? `${watchCount} watch` : "ready";
  return (
    <section className="runtime-capability-panel" aria-label="Agent runtime capability inventory">
      <header className="panel-title compact">
        <div>
          <p>Runtime capability</p>
          <h2>Agent Stack</h2>
        </div>
        <span className={attention ? "is-risk" : watchCount ? "is-watch" : "is-done"}>
          <ShieldCheck size={14} /> {headline}
        </span>
      </header>
      <div className="runtime-capability-grid">
        {runtimeItems.slice(0, 4).map((item) => (
          <article key={item.id} className={reliabilityTone(item.status)}>
            <header>
              <strong>{missionText(item.name)}</strong>
              <em>{missionText(item.status || "tracked")}</em>
            </header>
            <p>{missionText(item.summary || item.detail || "Capability tracked")}</p>
            <small>{missionText(item.detail || "Dashboard-safe inventory")}</small>
          </article>
        ))}
      </div>
      <footer>
        <span>{nodes.length} node{nodes.length === 1 ? "" : "s"} inventoried · {readyTools} ready tool lanes</span>
        <em>Watch {fmtTime(watch?.checkedAt || watch?.updatedAt || inventory?.updatedAt)}</em>
      </footer>
    </section>
  );
}

function MissionTimeline({
  events,
  jobs,
  approvals,
}: {
  events: MissionControlState["events"];
  jobs: MissionControlState["jobs"];
  approvals: MissionControlState["approvals"];
}) {
  const now = Date.now();
  const windowMs = 24 * 60 * 60 * 1000;
  const scaleTicks = [
    { label: "24h", left: 0 },
    { label: "18h", left: 25 },
    { label: "12h", left: 50 },
    { label: "6h", left: 75 },
    { label: "Now", left: 100 },
  ];
  const rows = [
    ...events.map((event) => ({ id: event.id, time: event.created_at, type: "update", agent: event.agent_id, label: event.title, lane: "updates" })),
    ...jobs.map((job) => ({ id: job.id, time: job.updated_at, type: job.status === "error" || job.status === "blocked" ? "risk" : "job", agent: job.agent_id, label: job.title, lane: priorityJobKey(job) === "general" ? "jobs" : "priority" })),
    ...approvals.map((approval) => ({ id: approval.id, time: approval.created_at, type: "handoff", agent: approval.agent_id, label: approval.title, lane: "approvals" })),
  ]
    .map((row) => ({ ...row, at: timeValue(row.time) }))
    .filter((row) => Number.isFinite(row.at) && row.at > now - windowMs)
    .sort((a, b) => a.at - b.at)
    .slice(-36);
  const lanes = [
    { key: "priority", label: "Priority Jobs", rows: rows.filter((row) => row.lane === "priority" || row.type === "risk").slice(-12) },
    { key: "updates", label: "Agent Updates", rows: rows.filter((row) => row.lane === "updates").slice(-12) },
    { key: "approvals", label: "Approvals", rows: rows.filter((row) => row.lane === "approvals").slice(-8) },
  ];

  return (
    <section id="mission-timeline" className="mission-timeline calendar-card" aria-label="Mission timeline last 24 hours">
      <header>
        <strong>24h Priority Timeline</strong>
        <span>{rows.length} mapped events</span>
      </header>
      <div className="calendar-scale" aria-hidden="true">
        {scaleTicks.map((tick) => <span key={tick.label} style={{ left: `${tick.left}%` }}>{tick.label}</span>)}
      </div>
      <div className="timeline-lanes">
        {lanes.map((lane) => (
          <article key={lane.key} className="timeline-lane">
            <span>{lane.label}</span>
            <div className="timeline-track calendar-track">
              {lane.rows.map((row) => {
                const left = Math.max(0, Math.min(100, ((row.at - (now - windowMs)) / windowMs) * 100));
                return (
                  <i
                    key={`${row.type}-${row.id}`}
                    className={`timeline-mark is-${row.type} ${agentClass(row.agent as AgentId)}`}
                    style={{ left: `${left}%` }}
                    title={`${missionText(row.label)} · ${fmtTime(row.time)}`}
                  />
                );
              })}
            </div>
            <em>{lane.rows.length}</em>
          </article>
        ))}
      </div>
      <footer>
        <span><i className="is-job" />Jobs</span>
        <span><i className="is-update" />Updates</span>
        <span><i className="is-handoff" />Approvals</span>
      </footer>
    </section>
  );
}

function MissionHealthPanel({ state }: { state: MissionControlState }) {
  const pendingApprovals = state.approvals.filter((row) => row.status === "pending").length;
  const trackedJobs = operatorTrackedJobs(state.jobs);
  const riskJobs = trackedJobs.filter((job) => jobNeedsAttention(job, trackedJobs)).length;
  const activeAgents = state.statuses.filter((row) => agentIsReady(row)).length;
  const lastUpdate = [...state.statuses.map((row) => row.updated_at), ...state.events.map((row) => row.created_at)]
    .filter(Boolean)
    .sort()
    .pop();
  const minutesSinceUpdate = lastUpdate ? (Date.now() - new Date(lastUpdate).getTime()) / 60000 : 999;
  const freshness = minutesSinceUpdate <= 15 ? 100 : minutesSinceUpdate <= 60 ? 82 : 48;
  const agentScore = Math.min(100, Math.round((activeAgents / 3) * 100));
  const jobsScore = Math.max(40, 100 - riskJobs * 18);
  const approvalScore = pendingApprovals ? 74 : 100;
  const costScore = state.modelUsage?.daily && state.modelUsage.daily > 1 ? 82 : 96;
  const overall = Math.round((freshness + agentScore + jobsScore + approvalScore + costScore) / 5);
  const rows = [
    { label: "Freshness", value: freshness },
    { label: "Agent heartbeat", value: agentScore },
    { label: "Job health", value: jobsScore },
    { label: "Usage signal", value: costScore },
  ];

  return (
    <section className="mission-health-card">
      <div className="panel-title compact">
        <h2>Mission Health</h2>
        <span>{overall}%</span>
      </div>
      <div className="health-overview">
        <div className="health-gauge" style={{ "--score": overall } as React.CSSProperties}>
          <strong>{overall}</strong>
          <span>score</span>
        </div>
        <div className="health-alerts">
          <strong>{riskJobs ? `${riskJobs} job risks` : "Jobs stable"}</strong>
          <p>{pendingApprovals ? `${pendingApprovals} handoff pending` : "No approval blockers"}</p>
          <small>Updated {fmtTime(lastUpdate)}</small>
        </div>
      </div>
      <div className="health-bars">
        {rows.map((row) => (
          <article key={row.label}>
            <span>{row.label}</span>
            <strong>{row.value}%</strong>
            <div><i style={{ width: `${row.value}%` }} /></div>
          </article>
        ))}
      </div>
    </section>
  );
}

function BrainOperationsSummary({
  state,
  workItems,
  quietMode,
  onNavigate,
  liveCues,
}: {
  state: MissionControlState;
  workItems: WorkItem[];
  quietMode: boolean;
  onNavigate: (target: AttentionTarget) => void;
  liveCues: LiveCueState;
}) {
  const pendingApprovals = state.approvals.filter((row) => row.status === "pending").length;
  const trackedJobs = operatorTrackedJobs(state.jobs);
  const riskJobs = trackedJobs.filter((job) => jobNeedsAttention(job, trackedJobs)).length;
  const firstRiskJob = trackedJobs.find((job) => jobNeedsAttention(job, trackedJobs));
  const dataIssues = dataQualityIssues(state);
  const firstDataIssue = dataIssues[0];
  const readyAgents = state.statuses.filter((row) => agentIsReady(row)).length;
  const lastUpdate = [...state.statuses.map((row) => row.updated_at), ...state.events.map((row) => row.created_at)]
    .filter(Boolean)
    .sort()
    .pop();
  const minutesSinceUpdate = lastUpdate ? (Date.now() - new Date(lastUpdate).getTime()) / 60000 : 999;
  const freshness = minutesSinceUpdate <= 15 ? 100 : minutesSinceUpdate <= 60 ? 82 : 48;
  const trackedAgents = Math.max(3, state.statuses.length);
  const freshnessNeedsWatch = freshness < 82;
  const waitingWork = workItems.filter((item) => item.state === "waiting" || item.state === "blocked").length;
  const workingNow = workItems.filter((item) => item.state === "working").length;
  const reliabilityItems = state.reliabilityUpgrades?.items || [];
  const reliabilityAttention = reliabilityItems.filter((item) => ["attention", "blocked", "error"].includes(String(item.status).toLowerCase())).length;
  const reliabilityWatch = reliabilityItems.filter((item) => String(item.status).toLowerCase() === "watch").length;
  const reliabilityReady = reliabilityItems.filter((item) => ["ready", "ok", "done"].includes(String(item.status).toLowerCase())).length;
  const reliabilityFocus = reliabilityItems.find((item) => ["attention", "blocked", "error"].includes(String(item.status).toLowerCase()))
    || reliabilityItems.find((item) => String(item.status).toLowerCase() === "watch");
  const reliabilityWhy = reliabilityFocus
    ? `${reliabilityAttention ? "Attention" : "Watch"}: ${missionText(reliabilityFocus.label)} - ${missionText(reliabilityFocus.signal)}`
    : "Reliability: all tracked probes ready";
  const dataScore = dataIssues.some((issue) => issue.tone === "risk") ? 58 : dataIssues.length ? 82 : 100;
  const agentScore = Math.min(100, Math.round((readyAgents / trackedAgents) * 100));
  const freshnessLabelText = freshness >= 100 ? "Fresh" : freshness >= 82 ? "Aging" : "Stale";
  const overall = Math.round((freshness + dataScore + agentScore + (riskJobs ? 58 : 100) + (pendingApprovals ? 70 : 100)) / 5);
  const confidenceReason = riskJobs
    ? "Lower because a job is blocked."
    : pendingApprovals
      ? "Lower because a decision is waiting."
      : firstDataIssue
        ? firstDataIssue.detail
      : freshnessNeedsWatch
        ? "Lower because visible check-ins are aging."
        : readyAgents < trackedAgents
          ? "Lower because not every agent is ready."
          : overall >= 100
            ? "All visible control surfaces are fresh and ready."
            : "Not 100% because visible control surfaces can lag.";
  const decision = firstDataIssue
    ? {
        tone: firstDataIssue.tone,
        title: firstDataIssue.title,
        detail: firstDataIssue.detail,
        icon: <AlertTriangle size={22} />,
      }
    : riskJobs
    ? {
        tone: "risk",
        title: `${riskJobs} job${riskJobs === 1 ? "" : "s"} needs focus`,
        detail: "Open Today's Jobs and inspect the highlighted row first.",
        icon: <AlertTriangle size={22} />,
      }
    : pendingApprovals
      ? {
          tone: "watch",
          title: `${pendingApprovals} decision${pendingApprovals === 1 ? "" : "s"} waiting`,
          detail: "Review the action strip before assigning more work.",
          icon: <AlertTriangle size={22} />,
        }
      : freshnessNeedsWatch
        ? {
            tone: "watch",
            title: "Status check-in aging",
            detail: "Ask the active agent for a fresh status if this persists.",
            icon: <Timer size={22} />,
          }
      : waitingWork
        ? {
            tone: "watch",
            title: `${waitingWork} work item${waitingWork === 1 ? "" : "s"} waiting`,
            detail: "Open the live work board for the reason and owner.",
            icon: <Timer size={22} />,
          }
        : {
            tone: "clear",
            title: workingNow ? `${workingNow} active workstream${workingNow === 1 ? "" : "s"}` : "No blocking decision",
            detail: workingNow ? "Agents are claimed and reporting in one view." : "Only new alerts need Josh right now.",
            icon: <CheckCircle2 size={22} />,
          };
  const focusReason = firstDataIssue
    ? firstDataIssue.why.replace(/^Why:\s*/i, "")
    : pendingApprovals
    ? approvalAlertReason(state.approvals.find((row) => row.status === "pending")!).replace(/^Why:\s*/i, "")
    : firstRiskJob
      ? jobAlertReason(firstRiskJob).replace(/^Why:\s*/i, "")
      : reliabilityFocus
        ? reliabilityWhy
        : "Reliability probes are ready and no job needs intervention.";
  const coverageLine = `${readyAgents}/${trackedAgents} agents ready · ${trackedJobs.length} jobs tracked`;
  const nextItem = workItems.find((item) => item.state === "ready")
    || workItems.find((item) => item.state === "working")
    || workItems.find((item) => item.state === "done");
  const focusTarget: AttentionTarget = firstDataIssue ? firstDataIssue.target : riskJobs || pendingApprovals ? "today-jobs" : "brain-feed";
  return (
    <section className={`brain-summary-strip is-${decision.tone}${sectionCueClass("system", liveCues)}`} aria-label="Live Work Board mission snapshot">
      <SectionCue label={liveCues.focus === "system" ? "focus" : "updated"} />
      <button
        type="button"
        className={`summary-chip summary-focus is-${decision.tone}`}
        onClick={() => onNavigate(focusTarget)}
        title={focusReason}
      >
        <span>Focus</span>
        <strong>{decision.title}</strong>
        <p>{focusReason}</p>
      </button>
      <article className="summary-chip">
        <span>Agents</span>
        <strong>{coverageLine}</strong>
        <p>{workingNow ? `${workingNow} working` : "No active claim"} · {waitingWork ? `${waitingWork} waiting` : "no waiting work"}</p>
      </article>
      <article className="summary-chip">
        <span>Next</span>
        <strong>{nextItem ? nextItem.title : quietMode ? "Quiet mode" : "Awaiting instruction"}</strong>
        <p>{nextItem ? `${AGENTS[nextItem.agent_id]?.label || nextItem.agent_id} · ${workStateLabel(nextItem.state)}` : "No upcoming handoff"}</p>
      </article>
      <article className="summary-chip summary-confidence">
        <span>Freshness</span>
        <strong>{freshnessLabelText} · {overall}%</strong>
        <p>{ageLabel(lastUpdate)} · {confidenceReason}</p>
      </article>
    </section>
  );
}

function reliabilityTone(status?: string) {
  const value = String(status || "").toLowerCase();
  if (value === "ready" || value === "ok" || value === "done") return "is-done";
  if (value === "watch" || value === "partial") return "is-watch";
  if (value === "attention" || value === "blocked" || value === "error") return "is-risk";
  return "is-muted";
}

function ReliabilityUpgradesPanel({ upgrades }: { upgrades?: MissionControlState["reliabilityUpgrades"] }) {
  const items = upgrades?.items || [];
  if (!items.length) return null;
  const metrics = upgrades?.metrics || [];
  const attention = items.filter((item) => ["attention", "blocked", "error"].includes(String(item.status).toLowerCase())).length;
  const watch = items.filter((item) => String(item.status).toLowerCase() === "watch").length;
  const ready = items.filter((item) => ["ready", "ok", "done"].includes(String(item.status).toLowerCase())).length;

  return (
    <section className="reliability-upgrades-panel" aria-label="Reliability upgrades">
      <header className="panel-title compact">
        <div>
          <p>Upgrade wiring</p>
          <h2>Reliability Cockpit</h2>
        </div>
        <span className={attention ? "is-risk" : watch ? "is-watch" : "is-done"}>
          <Radio size={14} /> {attention ? `${attention} attention` : watch ? `${watch} watch` : `${ready} ready`}
        </span>
      </header>
      <p className="reliability-summary">{missionText(upgrades?.summary || "Reliability upgrade probes are available.")} · Updated {fmtTime(upgrades?.updatedAt)}</p>
      {metrics.length ? (
        <div className="reliability-metrics" aria-label="Reliability metrics">
          {metrics.slice(0, 5).map((metric) => (
            <article key={metric.label} className={reliabilityTone(metric.status)}>
              <span>{missionText(metric.label)}</span>
              <strong>{String(metric.value)}</strong>
            </article>
          ))}
        </div>
      ) : null}
      <div className="reliability-upgrade-list">
        {items.slice(0, 6).map((item) => (
          <article key={item.id} className={`reliability-upgrade-row ${reliabilityTone(item.status)}`}>
            <span className={`status-dot ${reliabilityTone(item.status)}`} aria-hidden="true" />
            <div>
              <header>
                <strong>{missionText(item.label)}</strong>
                <em>{missionText(item.owner)}</em>
              </header>
              <p>{missionText(item.signal)}</p>
              <small>{missionText(item.next)}</small>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function AgentWorkBoard({
  items,
  quietMode,
  onNavigate,
  liveCues,
}: {
  items: WorkItem[];
  quietMode: boolean;
  onNavigate: (target: AttentionTarget) => void;
  liveCues: LiveCueState;
}) {
  const buckets: Array<{ key: WorkState; label: string; empty: string }> = [
    { key: "waiting", label: "Needs Josh", empty: "No decisions waiting" },
    { key: "working", label: "Working", empty: "No active claimed task" },
    { key: "ready", label: "Next", empty: "No upcoming handoff" },
  ];
  const bucketItems = (state: WorkState) => {
    const scoped = quietMode ? items.filter((item) => ["waiting", "blocked", "working"].includes(item.state)) : items;
    if (state === "waiting") return scoped.filter((item) => item.state === "waiting" || item.state === "blocked").slice(0, 1);
    return scoped.filter((item) => item.state === state).slice(0, 1);
  };

  return (
    <section className="agent-work-board" aria-label="Agent work board">
      <header>
        <div>
          <span><GitBranch size={13} />Live work</span>
          <strong>{quietMode ? "Blockers and active claims only" : "Needs Josh · Working · Next"}</strong>
        </div>
      </header>
      <div className="work-board-buckets">
        {buckets.map((bucket) => {
          const rows = bucketItems(bucket.key);
          return (
            <article key={bucket.key} className={`work-bucket ${workStateClass(bucket.key)}`}>
              <h3>{bucket.label}</h3>
              {rows.length ? rows.map((item) => (
                <WorkItemRow
                  key={item.id}
                  item={item}
                  onNavigate={onNavigate}
                  changed={Boolean(liveCues.rows[cueRowKey("work", item.id)])}
                />
              )) : (
                <p className="work-empty">{bucket.empty}</p>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function WorkItemRow({ item, onNavigate, changed }: { item: WorkItem; onNavigate: (target: AttentionTarget) => void; changed?: boolean }) {
  const owner = AGENTS[item.agent_id]?.label || item.agent_id;
  const title = missionText(item.title);
  const detail = missionText(item.detail || workStateLabel(item.state));
  return (
    <button
      type="button"
      className={`work-item-row ${agentClass(item.agent_id)} ${workStateClass(item.state)}${changedRowClass(changed)}`}
      onClick={() => onNavigate(item.target)}
      title={`Open ${item.target === "today-jobs" ? "Today's Jobs" : "Live Work Board"}`}
    >
      <span className="row-change-dot" aria-hidden="true" />
      <span className={`status-dot ${workStateClass(item.state)} ${agentClass(item.agent_id)}`} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{owner} · {detail}</p>
      </div>
      {item.state === "working" ? <span className="work-progress-rail" aria-hidden="true" /> : null}
    </button>
  );
}

function AgentEcosystemMap({ statuses }: { statuses: Map<AgentId, AgentStatus> }) {
  const nodes: Array<{ agent: AgentId; x: number; y: number }> = [
    { agent: "joshex", x: 50, y: 24 },
    { agent: "josh2", x: 20, y: 68 },
    { agent: "jaimes", x: 58, y: 72 },
    { agent: "jain", x: 84, y: 46 },
  ];
  const links = [
    ["joshex", "josh2"],
    ["joshex", "jaimes"],
    ["jaimes", "jain"],
  ] as Array<[AgentId, AgentId]>;
  const byAgent = new Map(nodes.map((node) => [node.agent, node]));

  return (
    <section className="ecosystem-card">
      <div className="panel-title compact">
        <h2>Agent Map</h2>
        <span><GitBranch size={14} />Live routing</span>
      </div>
      <div className="ecosystem-map" aria-label="Agent ecosystem routing map">
        <svg viewBox="0 0 100 86" role="img">
          {links.map(([from, to]) => {
            const a = byAgent.get(from)!;
            const b = byAgent.get(to)!;
            return <line key={`${from}-${to}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} />;
          })}
        </svg>
        {nodes.map((node) => {
          const status = statuses.get(node.agent) || offlineStatus(node.agent);
          return (
            <article
              key={node.agent}
              className={`ecosystem-node ${statusClass(status.status)}`}
              style={{ left: `${node.x}%`, top: `${node.y}%` }}
            >
              <span className={`dot ${statusClass(status.status)}`} />
              <strong>{AGENTS[node.agent].label}</strong>
              <em>{status.status}</em>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function BrainCostCard({ modelUsage }: { modelUsage?: MissionControlState["modelUsage"] }) {
  const topModels = modelUsage?.breakdown?.length ? modelUsage.breakdown : modelUsage?.topModels || [];
  return (
    <section className="brain-cost-card">
      <div className="panel-title compact">
        <h2>Model Cost</h2>
        <span>{fmtCurrency(modelUsage?.daily)} daily</span>
      </div>
      <div className="cost-snapshot">
        <article>
          <span>Weekly</span>
          <strong>{fmtCurrency(modelUsage?.weekly)}</strong>
        </article>
        <article>
          <span>Monthly</span>
          <strong>{fmtCurrency(modelUsage?.monthly)}</strong>
        </article>
        <article>
          <span>Projected</span>
          <strong>{fmtCurrency(modelUsage?.weeklyRunRate?.projectedMonthly)}</strong>
        </article>
      </div>
      <div className="brain-model-list">
        {topModels.slice(0, 3).map((model: any) => (
          <article key={`${model.name}-${model.source || model.window || ""}`}>
            <span>{model.name}</span>
            <strong>{fmtCurrency(model.weeklyCost ?? model.cost)}</strong>
          </article>
        ))}
      </div>
    </section>
  );
}

function compactTaskText(value?: string | null, fallback = "No recent completion reported") {
  return compactText(missionText(value || fallback), 74);
}

function expectedNextBullets(job?: MissionControlState["jobs"][number] | null, agent?: AgentId): AgentIdleContext["nextBullets"] {
  if (!job) {
    return [
      { label: "Input", text: "Direct instruction or the next scheduled agent event." },
      { label: "Checks", text: "Confirm owner, objective, and whether approval is needed." },
      { label: "Output", text: "A visible Live Work Board update with the next concrete step." },
      { label: "Alert", text: "Only if a blocker, missing route, or decision appears." },
    ];
  }

  const title = missionText(job.title);
  const text = `${title} ${job.detail} ${job.tool} ${job.schedule}`.toLowerCase();
  const owner = AGENTS[job.agent_id || agent || "joshex"]?.label || "Agent";

  if (/gmail|inbox|email|mail triage|unread/.test(text)) {
    return [
      { label: "Input", text: "Dashboard-safe inbox counts and the last-24-hour triage window." },
      { label: "Checks", text: "Looks for human requests, urgent ops, approvals, and newsletter noise." },
      { label: "Output", text: "A concise triage status plus any items Josh actually needs to see." },
      { label: "Alert", text: "Only if a reply, approval, or access issue needs attention." },
    ];
  }

  if (/signal|intelligence|news|newsletter|breaking/.test(text)) {
    return [
      { label: "Input", text: "Latest J.A.I.N breaking rows, newsfeed rows, and newsletter trends." },
      { label: "Checks", text: "Dedupes stories and scores relevance without crowding Control Tower." },
      { label: "Output", text: "Telegram push or archived source data when something is worth attention." },
      { label: "Alert", text: "Only if a source is broken or a high-confidence item needs Josh." },
    ];
  }

  if (/daily mission|missions|mission picks|claim|reward/.test(text)) {
    return [
      { label: "Input", text: "Sorare mission board, auth state, eligible cards, and MLB schedule." },
      { label: "Checks", text: "Finds open missions, validates eligibility, and watches deadlines." },
      { label: "Output", text: "Mission choices, submission/claim status, and any blocked action." },
      { label: "Alert", text: "Only if manual approval, login refresh, or a missed window appears." },
    ];
  }

  if (/lineup|lineups|gw|game-week|pre-lock|rp|champion|challenger|deadline|submit/.test(text)) {
    return [
      { label: "Input", text: "Gameweek schedule, card eligibility, probable starters, and lineups." },
      { label: "Checks", text: "Validates slots, RP eligibility, lock timing, and lineup conflicts." },
      { label: "Output", text: "Draft, validation, or submission status for the relevant lineup set." },
      { label: "Alert", text: "Only if deadline risk or a manual roster decision is required." },
    ];
  }

  if (/fantasy|waiver|roster|injury|pitcher|player|baseball/.test(text)) {
    return [
      { label: "Input", text: "Roster state, injury news, waiver pool, and matchup context." },
      { label: "Checks", text: "Compares add/drop edge, ownership, role changes, and claim timing." },
      { label: "Output", text: "Actionable candidate, reason, and when Josh must decide." },
      { label: "Alert", text: "Only if a roster move looks time-sensitive or high-confidence." },
    ];
  }

  if (/brain feed|heartbeat|visibility|agent status|status check/.test(text)) {
    return [
      { label: "Input", text: "Live Work Board rows, local sidecars, and agent heartbeats." },
      { label: "Checks", text: "Confirms each agent is fresh, readable, and mapped to the right tile." },
      { label: "Output", text: "Updated agent cards with current Complete, Next, and live status." },
      { label: "Alert", text: "Only if a visible row is stale or an agent stops reporting." },
    ];
  }

  if (/mission control|kiosk|dashboard|react|watchdog|refresh/.test(text)) {
    return [
      { label: "Input", text: "Dashboard data, sidecars, kiosk server, and Chrome display state." },
      { label: "Checks", text: "Verifies build health, data freshness, layout canaries, and alerts." },
      { label: "Output", text: "A refreshed Control Tower surface and clean kiosk health status." },
      { label: "Alert", text: "Only if the display, data, or refresh path needs repair." },
    ];
  }

  if (/memory|backup|sync|manifest|recovery/.test(text)) {
    return [
      { label: "Input", text: "Local memory bundle, remote manifest, and latest recovery state." },
      { label: "Checks", text: "Verifies freshness, transfer success, and conflicting snapshots." },
      { label: "Output", text: "Confirmed backup state or a clear repair note if sync fails." },
      { label: "Alert", text: "Only if the remote copy is missing, stale, or inconsistent." },
    ];
  }

  if (/hermes|jaimes|jain|agent control|openclaw|route|capability/.test(text)) {
    return [
      { label: "Input", text: `${owner} heartbeat, task queue, command route, and capability state.` },
      { label: "Checks", text: "Confirms the route is reachable and the next job has a clear owner." },
      { label: "Output", text: "Ready/watch status plus the next handoff or repair instruction." },
      { label: "Alert", text: "Only if auth, routing, or command execution blocks the job." },
    ];
  }

  return [
    { label: "Input", text: `${owner} schedule, latest sidecar state, and prior run result.` },
    { label: "Checks", text: "Verifies the job window, freshness, errors, and expected owner." },
    { label: "Output", text: "A plain-English status update and any next operational step." },
    { label: "Alert", text: "Only if a blocker or Josh decision is needed." },
  ];
}

function nextScheduleFromJob(job: MissionControlState["jobs"][number], nowMs: number): number | null {
  const explicit = timeValue(job.nextRun);
  if (explicit > nowMs) return explicit;
  const schedule = missionText(job.schedule || "");
  const lower = schedule.toLowerCase();
  const last = timeValue(job.lastRun || job.completed_at || job.updated_at);
  const intervalMatch = lower.match(/every\s+(\d+)\s*min/);
  if (intervalMatch) {
    const interval = Number(intervalMatch[1]) * 60_000;
    if (!interval) return null;
    if (!last) return nowMs + interval;
    const elapsed = Math.max(0, nowMs - last);
    return last + (Math.floor(elapsed / interval) + 1) * interval;
  }
  if (lower.startsWith("hourly")) {
    const interval = 60 * 60_000;
    if (!last) return nowMs + interval;
    const elapsed = Math.max(0, nowMs - last);
    return last + (Math.floor(elapsed / interval) + 1) * interval;
  }
  const timeMatch = schedule.match(/(\d{1,2})(?::(\d{2}))?\s*(AM|PM)/i);
  if (!timeMatch) return null;
  const hour12 = Number(timeMatch[1]);
  const minute = Number(timeMatch[2] || 0);
  const ampm = timeMatch[3].toUpperCase();
  let hour = hour12 % 12;
  if (ampm === "PM") hour += 12;
  const candidate = new Date(nowMs);
  candidate.setHours(hour, minute, 0, 0);
  if (candidate.getTime() <= nowMs) candidate.setDate(candidate.getDate() + 1);
  return candidate.getTime();
}

function countdownLabel(targetMs?: number, nowMs = Date.now()) {
  if (!targetMs) return "";
  const diff = Math.max(0, targetMs - nowMs);
  const mins = Math.ceil(diff / 60_000);
  if (mins < 60) return `t-${mins}mins`;
  const hours = Math.floor(mins / 60);
  const rem = mins % 60;
  return `t-${hours}h${rem ? ` ${rem}m` : ""}`;
}

function jobIsSchedulable(job: MissionControlState["jobs"][number]) {
  const text = `${job.title} ${job.detail} ${job.tool} ${job.schedule}`.toLowerCase();
  const status = String(job.status || "").toLowerCase();
  if (!job.schedule || status === "error" || status === "blocked") return false;
  if (/test|smoke|placeholder/.test(text)) return false;
  return true;
}

function buildAgentIdleContext(agent: AgentId, state: MissionControlState, nowMs: number): AgentIdleContext {
  const agentJobs = state.jobs.filter((job) => job.agent_id === agent);
  const completedJob = [...agentJobs]
    .filter((job) => ["done", "completed", "ok"].includes(String(job.status || "").toLowerCase()) || Boolean(job.completed_at))
    .sort((a, b) => timeValue(b.completed_at || b.lastRun || b.updated_at) - timeValue(a.completed_at || a.lastRun || a.updated_at))[0];
  const completedEvent = [...state.events]
    .filter((event) => event.agent_id === agent && ["done", "complete", "completed"].includes(String(event.status || event.event_type || "").toLowerCase()))
    .sort((a, b) => timeValue(b.created_at) - timeValue(a.created_at))[0];
  const complete = compactTaskText(
    completedJob?.title || completedEvent?.title,
    agent === "joshex" ? "No recent JOSHeX completion reported" : `No recent ${AGENTS[agent].label} completion reported`,
  );
  const nextCandidates = agentJobs
    .filter(jobIsSchedulable)
    .map((job) => ({ job, nextAt: nextScheduleFromJob(job, nowMs) }))
    .filter((row): row is { job: MissionControlState["jobs"][number]; nextAt: number } => Boolean(row.nextAt && row.nextAt > nowMs))
    .sort((a, b) => a.nextAt - b.nextAt);
  const next = nextCandidates[0];
  if (!next) {
    return {
      complete,
      nextTitle: "no upcoming, awaiting instruction",
      nextBullets: expectedNextBullets(null, agent),
      countdown: "",
    };
  }
  return {
    complete,
    nextTitle: compactTaskText(next.job.title, "scheduled task"),
    nextBullets: expectedNextBullets(next.job, agent),
    nextAt: next.nextAt,
    countdown: countdownLabel(next.nextAt, nowMs),
  };
}

function stepStatusPrefix(value?: string) {
  const status = String(value || "").toLowerCase();
  if (status === "done" || status === "complete" || status === "completed") return "Done";
  if (status === "active" || status === "working" || status === "running") return "Now";
  if (status === "blocked" || status === "error") return "Blocked";
  if (status === "queued" || status === "pending") return "Queued";
  return "";
}

function liveStepRows(status: AgentStatus): AgentBriefRow[] {
  const rows = (status.steps || [])
    .map((step, index) => {
      const title = missionText(step.label || step.title || step.tool || "");
      if (!title) return null;
      const prefix = stepStatusPrefix(step.status);
      const text = prefix ? `${prefix}: ${title}` : title;
      return { label: `Step ${index + 1}`, text: compactText(text, 96) };
    })
    .filter((row): row is AgentBriefRow => Boolean(row));
  return rows.slice(-4).map((row, index) => ({ ...row, label: `Step ${index + 1}` }));
}

function buildActiveAgentBrief(
  status: AgentStatus,
  activeWork: WorkItem | undefined,
  idleContext: AgentIdleContext,
  currentStep: string,
): AgentBriefRow[] {
  const looseStatus = status as AgentStatus & Record<string, unknown>;
  const current = activeWork?.detail || activeWork?.title || currentStep || status.detail || "Working through the current objective.";
  const route = status.current_tool
    || activeWork?.source
    || looseStatus.model
    || looseStatus.route
    || "Agent runtime";
  const next = idleContext.countdown
    ? `${idleContext.countdown} + ${idleContext.nextTitle}`
    : idleContext.nextTitle;
  const issue = looseStatus.blocker || looseStatus.issue || looseStatus.error || "None";
  const steps = liveStepRows(status);
  const stepRows = steps.length ? steps.slice(-2) : [{ label: "Step", text: compactText(current, 96) }];
  const rows: AgentBriefRow[] = [
    { label: "Now", text: compactText(current, 96) },
    ...stepRows,
  ];
  if (String(route).trim() && String(route).trim() !== "Agent runtime") {
    rows.push({ label: "Tool", text: compactText(String(route), 84) });
  }
  rows.push({ label: "Next", text: compactText(next, 84) });
  if (String(issue).trim() && !/^none$/i.test(String(issue).trim())) {
    rows.push({ label: "Watch", text: compactText(String(issue), 84) });
  }
  return rows.slice(0, 5);
}

function nextClockLabel(nextAt?: number) {
  if (!nextAt) return "";
  const date = new Date(nextAt);
  if (!Number.isFinite(date.getTime())) return "";
  const day = localDayLabel(date.toISOString());
  const time = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return day === "Today" ? time : `${day} ${time}`;
}

function headlineShortText(value: string, maxLength = 54) {
  const text = missionText(value).replace(/\s+/g, " ").trim();
  if (text.length <= maxLength) return text;
  const words = text.split(" ");
  const kept: string[] = [];
  for (const word of words) {
    const next = [...kept, word].join(" ");
    if (next.length > maxLength) break;
    kept.push(word);
  }
  return kept.join(" ") || text.slice(0, maxLength).trim();
}

function headlineTitle(value: string) {
  return headlineShortText(value
    .replace(/\s*\([^)]*\)/g, "")
    .replace(/^make\s+/i, "")
    .replace(/^fix\s+/i, "")
    .replace(/^improve\s+/i, "")
    .replace(/distance-readable/i, "readability")
    .replace(/information density/i, "info density")
    .replace(/\bMorning Inbox Triage\b/i, "Inbox Triage")
    .replace(/\bFantasy Waiver Review\b/i, "Fantasy Waivers")
    .replace(/\bMission Control Refresh\b/i, "Control Tower Refresh")
    .replace(/\bBrain Feed Server\b/i, "Live Work Board Refresh"), 34);
}

function briefOutputForHeadline(title: string, rows: AgentBriefRow[]) {
  const text = `${title} ${rows.map((row) => row.text).join(" ")}`.toLowerCase();
  if (/gmail|inbox|email|mail triage|unread/.test(text)) return "Urgent-only inbox review.";
  if (/fantasy|waiver|roster|injury|pitcher|player|baseball/.test(text)) return "Checks injuries, waivers, and roster moves.";
  if (/daily mission|missions|claim|reward|sorare/.test(text)) return "Checks missions, rewards, and blockers.";
  if (/lineup|lineups|gw|game-week|pre-lock|rp|champion|challenger|deadline|submit/.test(text)) return "Validates slots, deadlines, and risk.";
  if (/brain feed|heartbeat|visibility|agent status|status check/.test(text)) return "Confirms each agent is fresh and mapped.";
  if (/mission control|kiosk|dashboard|react|watchdog|refresh/.test(text)) return "Refreshes data and checks kiosk health.";
  if (/signal|intelligence|news|newsletter|breaking/.test(text)) return "Dedupes sources into high-signal alerts.";
  if (/memory|backup|sync|manifest|recovery/.test(text)) return "Checks memory freshness and sync.";
  if (/hermes|jaimes|jain|agent control|openclaw|route|capability/.test(text)) return "Checks routes, ownership, and handoffs.";
  const row = rows.find((item) => item.label.toLowerCase() === "output")
    || rows.find((item) => item.label.toLowerCase() === "checks")
    || rows[0];
  return headlineShortText(row?.text.replace(/^a\s+/i, "").replace(/\.$/, "") || "Reports the result.", 76);
}

function readoutFit(value: string, maxLength = 68) {
  const text = missionText(value)
    .replace(/\s+/g, " ")
    .replace(/\.$/, "")
    .trim();
  if (!text) return "";
  return headlineShortText(text, maxLength);
}

function readoutSummary(value?: string | null, fallback = "Scheduled check.", maxLength = 68) {
  const text = compactText(value || fallback, 160).replace(/\.\.\.$/, "").trim();
  const lower = text.toLowerCase();
  if (!text) return fallback;
  if (/concise triage status|items josh actually needs/i.test(lower)) return "Triage summary plus Josh-only items.";
  if (/telegram push|archived source data|source data/i.test(lower)) return "Publishes or archives source updates.";
  if (/gmail|inbox|email|mail triage|unread/.test(lower)) return "Urgent Gmail triage and Josh-only asks.";
  if (/fantasy|waiver|roster|injury|pitcher|player|baseball/.test(lower)) return "Checks waivers, injuries, and roster moves.";
  if (/daily mission|missions|claim|reward|sorare/.test(lower)) return "Checks Sorare missions, rewards, and blockers.";
  if (/lineup|lineups|gw|game-week|pre-lock|rp|champion|challenger|deadline|submit/.test(lower)) return "Validates Sorare lineups, deadlines, and risk.";
  if (/brain feed|heartbeat|visibility|agent status|status check/.test(lower)) return "Checks agent rows, sidecars, and live publish path.";
  if (/agent card|agent row|complete, next|readout|text fit|objective/.test(lower)) return "Summarizes agent work into readable live rows.";
  if (/mission control|kiosk|dashboard|react|watchdog|refresh|ui/.test(lower)) return "Updates data and verifies the live kiosk.";
  if (/signal|intelligence|news|newsletter|breaking/.test(lower)) return "Refreshes breaking and newsletter signals.";
  if (/memory|backup|sync|manifest|recovery/.test(lower)) return "Checks memory freshness and recovery sync.";
  if (/hermes|jaimes|jain|agent control|openclaw|route|capability/.test(lower)) return "Checks routes, ownership, and handoffs.";
  if (/google|calendar|drive|auth|oauth|scope/.test(lower)) return "Checks Google access and auth health.";
  if (/xai|grok|gemini|model|provider|usage|cost/.test(lower)) return "Tracks model route, usage, and provider health.";
  return readoutFit(text, maxLength);
}

function countdownShortText(value: string) {
  return value
    .replace(/^t-(\d+)mins?$/i, "$1m")
    .replace(/^t-(\d+)h\s+(\d+)m$/i, "$1h $2m")
    .replace(/^t-(\d+)h$/i, "$1h");
}

function nextReadoutSummary(value?: string | null) {
  const text = missionText(value || "no upcoming, awaiting instruction").replace(/\s+/g, " ").trim();
  if (/no upcoming|awaiting instruction/i.test(text)) return "No upcoming task; awaiting instruction.";
  const match = text.match(/^(t-[^+]+)\s*\+\s*(.+)$/i);
  if (match) {
    return `${countdownShortText(match[1].trim())}: ${readoutSummary(match[2], "Next scheduled task.", 50)}`;
  }
  return readoutSummary(text, "Next scheduled task.", 64);
}

function briefText(rows: AgentBriefRow[], labels: string[], fallback: string, maxLength = 118) {
  const match = rows.find((row) => labels.some((label) => row.label.toLowerCase() === label.toLowerCase()));
  return readoutSummary(match?.text || fallback, fallback, maxLength);
}

function buildAgentInsights(
  activeFocus: boolean,
  briefRows: AgentBriefRow[],
  idleContext: AgentIdleContext,
  activeWork: WorkItem | undefined,
  status: AgentStatus,
  currentStep: string,
  nextOutput: string,
): AgentInsightRow[] {
  const next = idleContext.countdown ? `${idleContext.countdown} + ${idleContext.nextTitle}` : idleContext.nextTitle;
  const nextSummary = nextReadoutSummary(next);
  const currentSummary = readoutSummary(activeWork?.detail || activeWork?.title || currentStep || status.detail, "Working through the current step.", 72);
  if (activeFocus) {
    const issue = String((status as AgentStatus & Record<string, unknown>).blocker || (status as AgentStatus & Record<string, unknown>).issue || "").trim();
    if (issue) {
      return [
        { label: "Doing", text: currentSummary, tone: "active" },
        { label: "Watch", text: readoutSummary(issue, "Needs review.", 72), tone: "watch" },
        { label: "Next", text: nextSummary },
      ];
    }
    return [
      { label: "Doing", text: currentSummary, tone: "active" },
      { label: "Output", text: readoutSummary(nextOutput, "Reports the result.", 72), tone: "good" },
      { label: "Next", text: nextSummary },
    ];
  }
  return [
    { label: "Checks", text: briefText(briefRows, ["Checks", "Input"], "Scheduled inputs and agent state.", 72) },
    { label: "Output", text: briefText(briefRows, ["Output"], nextOutput, 72), tone: "good" },
    { label: "Next", text: nextSummary },
  ];
}

function agentHeadline(activeFocus: boolean, objectiveText: string, idleContext: AgentIdleContext, idleRows: AgentBriefRow[]): AgentHeadline {
  const nextTitle = headlineTitle(idleContext.nextTitle);
  const nextOutput = briefOutputForHeadline(nextTitle, idleRows);
  if (activeFocus) {
    const current = headlineTitle(objectiveText || "Active work");
    return {
      title: `Now: ${current}`,
      description: `Next: ${nextTitle} - ${nextOutput}`,
    };
  }
  if (!idleContext.nextAt && /awaiting instruction/i.test(idleContext.nextTitle)) {
    return {
      title: "Ready: awaiting instruction",
      description: "Next task will publish progress to the Live Work Board.",
    };
  }
  const when = nextClockLabel(idleContext.nextAt) || idleContext.countdown || "soon";
  return {
    title: `Next ${when}: ${nextTitle}`,
    description: nextOutput,
  };
}

function AgentHeroCard({
  agent,
  status,
  activeWork,
  idleContext,
  changed,
}: {
  agent: AgentId;
  status: AgentStatus;
  activeWork?: WorkItem;
  idleContext: AgentIdleContext;
  changed?: boolean;
}) {
  const objectiveRef = useRef<HTMLHeadingElement | null>(null);
  const [objectiveScroll, setObjectiveScroll] = useState({ active: false, distance: 0, duration: 18 });
  const freshness = freshnessClass(status.updated_at);
  const objectiveText = missionText(status.objective);
  const activeWorkFresh = activeWork?.state === "working" && isFreshActiveTimestamp(activeWork.updated_at);
  const statusWorkingFresh = ["active", "working"].includes(String(status.status || "").toLowerCase()) && isFreshActiveTimestamp(status.updated_at);
  const activeFocus = activeWorkFresh || statusWorkingFresh;
  const activeWorkDetail = activeWorkFresh ? activeWork : undefined;
  const currentStep = status.steps?.find((step) => step.label || step.title)?.label
    || status.steps?.find((step) => step.label || step.title)?.title
    || status.current_tool
    || status.detail
    || AGENTS[agent].role;
  const idleBriefRows = [
    ...idleContext.nextBullets.slice(0, 4),
    { label: "Last", text: compactText(idleContext.complete, 84) },
    { label: "Next", text: compactText(idleContext.countdown ? `${idleContext.countdown} + ${idleContext.nextTitle}` : idleContext.nextTitle, 84) },
  ];
  const headline = agentHeadline(activeFocus, objectiveText, idleContext, idleBriefRows);
  const supportNote = activeFocus
    ? `Current: ${readoutSummary(activeWorkDetail?.detail || activeWorkDetail?.title || currentStep, "Working through the current step.", 78)}`
    : `Complete: ${readoutSummary(idleContext.complete, "No recent completion reported.", 78)}`;
  const visualState = agentVisualState(status, activeFocus, activeWork);
  const stepTrail = stepTrailForAgent(status, activeFocus, activeWork);
  const showStepTrail = activeFocus || visualState === "waiting" || visualState === "blocked";
  const updateAgeMs = Math.max(0, Date.now() - timeValue(status.updated_at));
  const hotness = Math.max(0, 1 - Math.min(updateAgeMs, 12 * 60_000) / (12 * 60_000));
  const pulseSpeed = activeFocus
    ? Math.max(1.05, 1.75 - hotness * 0.45)
    : visualState === "waiting" || visualState === "blocked"
      ? 2.35
      : 0;
  const railSpeed = activeFocus ? Math.max(1.6, 2.8 - hotness * 0.7) : 2.8;
  useEffect(() => {
    const measure = () => {
      const node = objectiveRef.current;
      if (!node) return;
      const distance = Math.max(0, Math.ceil(node.scrollHeight - node.clientHeight));
      setObjectiveScroll({
        active: distance > 4,
        distance,
        duration: Math.max(16, Math.min(34, 16 + distance * 0.18)),
      });
    };
    measure();
    const frame = window.requestAnimationFrame(measure);
    window.addEventListener("resize", measure);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", measure);
    };
  }, [headline.title, headline.description]);
  return (
    <article
      className={`agent-hero-card ${agentClass(agent)} ${freshness} ${statusClass(status.status)} is-state-${visualState} ${activeFocus ? "is-working-focus" : "is-up-next-focus"}${changedRowClass(changed)}`}
      style={{
        "--agent-pulse-speed": `${pulseSpeed}s`,
        "--agent-rail-speed": `${railSpeed}s`,
      } as React.CSSProperties}
    >
      <span className="row-change-dot" aria-hidden="true" />
      <span className="agent-pulse-ring" aria-hidden="true" />
      <span className="agent-live-rail" aria-hidden="true" />
      <header>
        <span className={`dot ${statusClass(status.status)}`} />
        <span className="agent-name-lockup">
          <b className="agent-role-badge">{AGENTS[agent].roleBadge}</b>
          <strong>{AGENTS[agent].label}</strong>
        </span>
        <em>{agentOperatingState(status)} · {ageLabel(status.updated_at)}</em>
      </header>
      <div
        className={`agent-step-trail ${showStepTrail ? "" : "is-empty"}`}
        aria-label={showStepTrail ? `${AGENTS[agent].label} live step trail` : undefined}
        aria-hidden={showStepTrail ? undefined : true}
      >
        {showStepTrail ? stepTrail.map((step) => (
            <span key={step.label} className={`is-${step.state}`}>
              <i aria-hidden="true" />
              <b>{step.label}</b>
            </span>
          )) : null}
      </div>
      <h3
        ref={objectiveRef}
        className={`agent-objective ${objectiveScroll.active ? "is-scrollable" : ""}`}
        style={{
          "--objective-scroll-distance": `${objectiveScroll.distance}px`,
          "--objective-scroll-duration": `${objectiveScroll.duration}s`,
        } as React.CSSProperties}
      >
        <span className="agent-objective-text">
          <span className="agent-objective-main">{headline.title}</span>
          <span className="agent-objective-description">{headline.description}</span>
        </span>
      </h3>
      <p title={supportNote}>{supportNote}</p>
    </article>
  );
}

type MetricTone = "clear" | "info" | "watch" | "risk";

function Metric({
  icon,
  label,
  value,
  wide = false,
  tone = "info",
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  wide?: boolean;
  tone?: MetricTone;
}) {
  return (
    <article className={`metric is-${tone}${wide ? " is-wide" : ""}`}>
      <span>{icon}{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function AgentRail({ selected, statuses, onSelect }: { selected: AgentId; statuses: AgentStatus[]; onSelect: (agent: AgentId) => void }) {
  return (
    <nav className="agent-rail" aria-label="Agents">
      {(Object.keys(AGENTS) as AgentId[]).map((agent) => {
        const status = statuses.find((row) => row.agent_id === agent);
        return (
          <button
            key={agent}
            type="button"
            className={selected === agent ? "agent-button selected" : "agent-button"}
            onClick={() => onSelect(agent)}
          >
            <span className={`dot ${statusClass(status?.status)}`} />
            <span>
              <b className="agent-role-badge">{AGENTS[agent].roleBadge}</b>
              <strong>{AGENTS[agent].label}</strong>
              <small>{AGENTS[agent].role}</small>
            </span>
            <em>{status?.status || "offline"}</em>
          </button>
        );
      })}
    </nav>
  );
}

function BrainFeed({ events, selectedStatus }: { events: MissionControlState["events"]; selectedStatus: AgentStatus }) {
  return (
    <section className="brain-feed">
      <div className="panel-title">
        <div>
          <p>Live Work Board</p>
          <h2>{missionText(selectedStatus.objective)}</h2>
        </div>
        <span className={`status-pill ${statusClass(selectedStatus.status)}`}>{selectedStatus.status}</span>
      </div>
      <p className="objective-detail">{missionText(selectedStatus.detail || "No detail reported.")}</p>
      <div className="timeline">
        {events.slice(0, 10).map((event) => (
          <article key={event.id} className="timeline-row">
            <span className={`rail ${statusClass(event.status)}`} />
            <div>
              <header>
                <strong>{missionText(event.title)}</strong>
                <time>{fmtTime(event.created_at)}</time>
              </header>
              <p>{missionText(event.detail || event.tool || event.event_type)}</p>
              <footer>
                <span>{AGENTS[event.agent_id]?.label || event.agent_id}</span>
                <span>{event.event_type}</span>
                <span>{missionText(event.tool || "Control Tower")}</span>
              </footer>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

type JobRow = MissionControlState["jobs"][number];

const JOB_CATEGORY_RULES: Array<{ key: string; label: string; matcher: (job: JobRow, text: string) => boolean }> = [
  { key: "mission-control", label: "Control Tower", matcher: (_job, text) => /mission control|control tower|dashboard|brain feed|live work board|react|kiosk|v2/.test(text) },
  { key: "inbox", label: "Inbox & Handoffs", matcher: (_job, text) => /inbox|approval|handoff|ledger/.test(text) },
  { key: "sorare", label: "Sorare MLB", matcher: (_job, text) => /sorare|mlb|baseball/.test(text) },
  { key: "fantasy", label: "Fantasy Ops", matcher: (_job, text) => /fantasy|lineup|waiver|roster|pitcher|player/.test(text) },
  { key: "agent-control", label: "Agent Control", matcher: (_job, text) => /agent control|openclaw|hermes|jaimes|jain|josh 2/.test(text) },
  { key: "automation", label: "Automation", matcher: (_job, text) => /cron|automation|monitor|watch|scheduled|job/.test(text) },
];

const JOB_CATEGORY_ORDER = [...JOB_CATEGORY_RULES.map((rule) => rule.key), "other"];

function jobCategory(job: JobRow) {
  const text = `${job.title} ${job.detail} ${job.tool} ${job.agent_id}`.toLowerCase();
  return JOB_CATEGORY_RULES.find((rule) => rule.matcher(job, text)) || { key: "other", label: "Other", matcher: () => true };
}

function categoryClass(job: JobRow) {
  return `category-${jobCategory(job).key}`;
}

function groupedJobs(jobs: JobRow[], mode: "urgency" | "category" = "urgency") {
  const groups = new Map<string, { key: string; label: string; items: JobRow[] }>();
  jobs.forEach((job) => {
    const category = jobCategory(job);
    if (!groups.has(category.key)) {
      groups.set(category.key, { key: category.key, label: category.label, items: [] });
    }
    groups.get(category.key)!.items.push(job);
  });
  groups.forEach((group) => {
    group.items.sort((a, b) => timeValue(b.updated_at) - timeValue(a.updated_at));
  });
  if (mode === "category") {
    return Array.from(groups.values()).sort((a, b) => JOB_CATEGORY_ORDER.indexOf(a.key) - JOB_CATEGORY_ORDER.indexOf(b.key));
  }
  return Array.from(groups.values()).sort((a, b) => {
    const aUrgent = a.items.some((job) => job.status === "active" || job.status === "queued" || job.status === "blocked" || job.status === "error");
    const bUrgent = b.items.some((job) => job.status === "active" || job.status === "queued" || job.status === "blocked" || job.status === "error");
    if (aUrgent !== bUrgent) return aUrgent ? -1 : 1;
    return b.items.length - a.items.length;
  });
}

function jobGroupSummary(items: JobRow[]) {
  const active = items.filter((job) => jobWorkState(job, items) === "working").length;
  const risk = items.filter((job) => jobNeedsAttention(job, items)).length;
  if (risk) return `${risk} need focus`;
  if (active) return `${active} active`;
  return `${items.length} tracked`;
}

function jobIsActiveOrNeedsAttention(job: JobRow, jobs: JobRow[] = []) {
  return jobIsFreshActive(job) || jobNeedsAttention(job, jobs);
}

function jobIsScheduledInventory(job: JobRow) {
  return Boolean(
    job.todayRelevant
    || job.schedule
    || job.sourceLabel
    || job.nextRun
    || job.lastRun
    || String(job.id || "").startsWith("cron-"),
  );
}

function operatorVisibleJobs(jobs: JobRow[]) {
  return jobs.filter((job) => (
    jobIsScheduledInventory(job)
    || jobIsActiveOrNeedsAttention(job, jobs)
    || priorityJobKey(job) !== "general"
  ));
}

function normalizedJobViewText(value?: string | null) {
  return missionText(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function jobViewMergeKey(job: JobRow) {
  if (priorityJobKey(job) !== "general") {
    return `priority-${job.id || job.agent_id}-${normalizedJobViewText(job.title)}`;
  }
  return [
    "general",
    job.agent_id,
    normalizedJobViewText(job.title),
    normalizedJobViewText(job.schedule || job.nextRun || job.sourceLabel || job.tool),
  ].join("-");
}

function dedupeJobsForView(jobs: JobRow[]) {
  const rows = new Map<string, JobRow>();
  for (const job of jobs) {
    const key = jobViewMergeKey(job);
    const existing = rows.get(key);
    if (!existing || timeValue(job.updated_at || job.lastRun) >= timeValue(existing.updated_at || existing.lastRun)) {
      rows.set(key, job);
    }
  }
  return [...rows.values()];
}

function operatorTrackedJobs(jobs: JobRow[]) {
  return dedupeJobsForView(operatorVisibleJobs(jobs));
}

function jobStatusValue(job: JobRow) {
  return String(job.runStatus || job.status || "").toLowerCase();
}

function jobStatusClass(job: JobRow, jobs: JobRow[] = []) {
  const status = jobStatusValue(job);
  const softMiss = jobIsSoftMissedAutomation(job);
  const superseded = jobFailureSuperseded(job, jobs);
  if (jobNeedsAttention(job, jobs) || (!superseded && (["blocked", "error", "failed"].includes(status) || (status === "missed" && !softMiss)))) return "is-risk";
  if (jobIsFreshActive(job)) return "is-active";
  return "is-done";
}

function jobStatusLabel(job: JobRow, jobs: JobRow[] = []) {
  const status = jobStatusValue(job);
  const softMiss = jobIsSoftMissedAutomation(job);
  const superseded = jobFailureSuperseded(job, jobs);
  if (jobNeedsAttention(job, jobs) || (!superseded && (["blocked", "error", "failed"].includes(status) || (status === "missed" && !softMiss)))) return "Needs focus";
  if (jobIsFreshActive(job)) return "Working";
  return "Ready";
}

function compactText(value?: string | null, maxLength = 72) {
  const text = missionText(value || "")
    .replace(/\bSorare Deadline Guard\b/g, "Deadline guard")
    .replace(/\bSorare Auth Watchdog\b/g, "Auth watchdog")
    .replace(/\bSorare Cookie Freshness\b/g, "Cookie check")
    .replace(/\bSorare Daily Missions\b/g, "Daily missions")
    .replace(/\s+/g, " ")
    .replace(/^Starting\s+/i, "")
    .replace(/^Approved scheduled job with\s+/i, "")
    .replace(/^Approved remediation:\s*/i, "")
    .replace(/cron run started;?\s*/i, "")
    .trim();
  if (!text) return "";
  if (text.length <= maxLength) return text;
  const clipped = text.slice(0, maxLength - 3).replace(/\s+\S*$/, "").trim();
  return `${clipped || text.slice(0, maxLength - 3).trim()}...`;
}

function compactJobDetail(job: JobRow, fallback?: string, categoryLabel?: string) {
  const raw = missionText(job.detail || job.tool || fallback || "");
  const lower = raw.toLowerCase();
  let detail = raw;

  if (lower.includes("backing up recovery memory bundle") || lower.includes("scheduled memory/context backup")) {
    detail = "Memory backup verification";
  } else if (lower.includes("no-submit game-week draft report")) {
    detail = "Builds no-submit GW draft";
  } else if (lower.includes("limited game-week lineups")) {
    detail = "Builds Limited GW lineups";
  } else if (lower.includes("strict gw validator")) {
    detail = "Runs strict GW validator";
  } else if (lower.includes("sorare outcome labels")) {
    detail = "Updates outcome labels";
  } else if (lower.includes("late lineup-deadline safety")) {
    detail = "Lineup deadline safety check";
  } else if (lower.includes("login health")) {
    detail = "Sorare login health check";
  } else if (lower.includes("submits champion lineups")) {
    detail = "Submits Champion lineups";
  } else if (lower.includes("canonical sorare state")) {
    detail = "Mirrors Sorare state";
  } else if (lower.includes("cookie age")) {
    detail = "Cookie age check";
  } else if (lower.includes("daily sorare mission picks")) {
    detail = "Sets daily mission picks";
  } else if (lower.includes("claim sweep")) {
    detail = "Claims overnight rewards";
  } else if (lower.includes("prepares daily sorare missions")) {
    detail = "Prepares daily missions";
  } else if (lower.includes("17:00 et memory backup")) {
    detail = "Memory backup verification";
  } else if (lower.includes("brain feed alerts") || lower.includes("alert strip")) {
    detail = "Live Work Board alert layout polish";
  } else if (lower.includes("post-waiver scan")) {
    detail = "Post-waiver review window";
  } else if (lower.includes("live blocker triage")) {
    detail = "Live blocker triage and refresh fixes";
  } else if (lower.includes("accessibility") && lower.includes("resilience")) {
    detail = "Reliability and accessibility audit";
  }

  return compactText(detail, 72);
}

function compactJobTitle(job: JobRow) {
  const title = missionText(job.title);
  const lower = title.toLowerCase();
  if (lower.includes("sorare pre-lock monitor")) return "Pre-lock monitor";
  if (lower.includes("sorare gw draft report")) return "GW draft report";
  if (lower.includes("gw12 rp t-90")) return "RP T-90 check";
  if (lower.includes("gw12 rp t-20")) return "RP T-20 final";
  if (lower.includes("sorare edge outcome")) return "Edge outcome cycle";
  if (lower.includes("sorare ml training")) return "ML training";
  if (lower.includes("sorare sheet updater")) return "Sheet updater";
  if (lower.includes("sorare cookie auto-refresh")) return "Cookie auto-refresh";
  if (lower.includes("sorare limited gw lineups")) return "Limited GW lineups";
  if (lower.includes("sorare champion lineups")) return "Champion lineup submit";
  if (lower.includes("sorare canonical reflector")) return "Canonical reflector";
  if (lower.includes("sorare limited claim sweep")) return "Limited claim sweep";
  if (lower.includes("sorare daily missions prep")) return "Missions prep";
  if (lower.includes("sorare auth watchdog")) return "Auth watchdog";
  if (lower.includes("sorare cookie freshness")) return "Cookie check";
  if (lower.includes("sorare daily missions")) return "Daily missions";
  if (lower.includes("fantasy waiver scan")) return "Fantasy waiver scan";
  if (lower.includes("memory sync")) return "Memory sync";
  if (lower.includes("memory backup")) return "Memory backup";
  if (lower.includes("moving brain feed")) return "Live Work Board alert strip";
  return compactText(title, 42);
}

function compactOwnerLabel(job: JobRow, count?: number) {
  const owner = AGENTS[job.agent_id]?.label || job.agent_id;
  return count && count > 1 ? `${owner} · ${count}` : owner;
}

function sameLocalDay(value?: string | null) {
  if (!value) return false;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return false;
  const now = new Date();
  return date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();
}

function localDayLabel(value: string) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  const today = new Date();
  const tomorrow = new Date();
  tomorrow.setDate(today.getDate() + 1);
  const isToday = date.getFullYear() === today.getFullYear()
    && date.getMonth() === today.getMonth()
    && date.getDate() === today.getDate();
  const isTomorrow = date.getFullYear() === tomorrow.getFullYear()
    && date.getMonth() === tomorrow.getMonth()
    && date.getDate() === tomorrow.getDate();
  if (isToday) return "Today";
  if (isTomorrow) return "Tmrw";
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

function todayRunLabel(job: JobRow) {
  const status = jobStatusValue(job);
  const next = nextRunTime(job);
  const evidenceTime = job.lastRun || job.completed_at || (["done", "completed"].includes(status) ? job.updated_at : undefined);
  if (status === "missed") return jobIsSoftMissedAutomation(job) ? "Ready" : "Missed today";
  if (job.verifiedToday || sameLocalDay(evidenceTime)) return "Ran today";
  if (status === "active" || status === "running" || status === "queued") return "Now";
  if (status === "due") return "Due now";
  if (next && next >= Date.now() - 15 * 60 * 1000) return "Planned today";
  if (status === "upcoming" || status === "scheduled") return "Planned today";
  return job.todayRelevant ? "No run log" : "Not scheduled today";
}

function nextRunLabel(job: JobRow) {
  if (job.nextRun) {
    const date = new Date(job.nextRun);
    if (Number.isFinite(date.getTime())) {
      return `Next: ${localDayLabel(job.nextRun)} ${date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
    }
  }
  const schedule = missionText(job.schedule || "");
  if (!schedule) return "Next: on demand";
  if (/every|keepalive/i.test(schedule)) {
    const cadence = schedule.replace(/\s*\([^)]*\)/g, "").trim();
    return `Next: ${compactText(cadence, 34)}`;
  }
  const timeMatch = schedule.match(/(\d{1,2})(?::(\d{2}))?\s*(AM|PM)/i);
  if (!timeMatch) return `Next: ${schedule}`;
  const hour12 = Number(timeMatch[1]);
  const minute = Number(timeMatch[2] || 0);
  const ampm = timeMatch[3].toUpperCase();
  let hour = hour12 % 12;
  if (ampm === "PM") hour += 12;
  const next = new Date();
  next.setHours(hour, minute, 0, 0);
  const day = next.getTime() > Date.now() ? "Today" : "Tmrw";
  const clock = `${hour12}${minute ? `:${String(minute).padStart(2, "0")}` : ""} ${ampm}`;
  return `Next: ${day} ${clock}`;
}

function lastRunLabel(job: JobRow) {
  const last = job.lastRun || job.completed_at || job.updated_at;
  if (!last) return "Last: No log";
  const date = new Date(last);
  if (Number.isNaN(date.getTime())) return `Last: ${last}`;
  if (sameLocalDay(last)) {
    return `Last: ${date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  }
  return `Last: ${date.toLocaleString([], { month: "short", day: "numeric" })}`;
}

function jobRunCells(job: JobRow) {
  const today = todayRunLabel(job)
    .replace("Ran today", "Ran")
    .replace("Planned today", "Planned")
    .replace("Not scheduled today", "Not today")
    .replace("No run log", "No log");
  const todayClass = today.toLowerCase().includes("missed")
    ? "is-missed"
    : today.toLowerCase().includes("ran")
      ? "is-ran"
      : today.toLowerCase().includes("now") || today.toLowerCase().includes("due")
        ? "is-due"
        : today.toLowerCase().includes("planned")
          ? "is-planned"
          : "is-quiet";
  return {
    today,
    todayClass,
    last: lastRunLabel(job).replace(/^Last:\s*/, ""),
    next: nextRunLabel(job).replace(/^Next:\s*/, ""),
  };
}

function nextRunTime(job: JobRow) {
  const explicit = timeValue(job.nextRun);
  if (explicit) return explicit;
  const schedule = missionText(job.schedule || "");
  const timeMatch = schedule.match(/(\d{1,2})(?::(\d{2}))?\s*(AM|PM)/i);
  if (!timeMatch) return 0;
  const hour12 = Number(timeMatch[1]);
  const minute = Number(timeMatch[2] || 0);
  const ampm = timeMatch[3].toUpperCase();
  let hour = hour12 % 12;
  if (ampm === "PM") hour += 12;
  const next = new Date();
  next.setHours(hour, minute, 0, 0);
  if (next.getTime() < Date.now() - 15 * 60 * 1000) next.setDate(next.getDate() + 1);
  return next.getTime();
}

function jobIsSoon(job: JobRow, hours = 4) {
  const next = nextRunTime(job);
  if (!next) return false;
  const now = Date.now();
  return next >= now - 15 * 60 * 1000 && next <= now + hours * 60 * 60 * 1000;
}

function jobIsVisibleMaintenance(job: JobRow, allJobs: JobRow[]) {
  return jobIsActiveOrNeedsAttention(job, allJobs) || jobIsSoon(job, 4);
}

function upcomingTodayJobs(jobs: JobRow[], limit = 8) {
  const now = Date.now();
  const endOfToday = new Date();
  endOfToday.setHours(23, 59, 59, 999);
  const end = endOfToday.getTime();
  return [...jobs]
    .filter((job) => {
      const next = nextRunTime(job);
      const status = String(job.runStatus || job.status || "").toLowerCase();
      return (next && next >= now - 15 * 60 * 1000 && next <= end)
        || ["due", "upcoming", "scheduled", "active", "running", "queued"].includes(status)
        || job.todayRelevant;
    })
    .sort((a, b) => {
      const aAttention = jobNeedsAttention(a, jobs) ? 0 : 1;
      const bAttention = jobNeedsAttention(b, jobs) ? 0 : 1;
      if (aAttention !== bAttention) return aAttention - bAttention;
      return (nextRunTime(a) || Number.MAX_SAFE_INTEGER) - (nextRunTime(b) || Number.MAX_SAFE_INTEGER);
    })
    .slice(0, limit);
}

function visibleTimelineJobs(jobs: JobRow[]) {
  const now = Date.now();
  const windowMs = 6 * 60 * 60 * 1000;
  const recent = now - windowMs;
  const rows = jobs.filter((job) => jobIsActiveOrNeedsAttention(job, jobs) || timeValue(job.updated_at) >= recent);
  return (rows.length ? rows : jobs.slice(0, 8)).slice(0, 14);
}

function priorityScore(job: JobRow) {
  const key = priorityJobKey(job);
  if (key === "gmail") return 3;
  if (key === "sorare") return 2;
  if (key === "fantasy") return 1;
  return 0;
}

function operatorStateRank(job: JobRow, jobs: JobRow[]) {
  const state = jobWorkState(job, jobs);
  if (state === "blocked") return 0;
  if (state === "working") return 1;
  if (state === "ready") return 2;
  if (state === "done") return 3;
  return 4;
}

function compareJobsForOperator(a: JobRow, b: JobRow, jobs: JobRow[]) {
  const stateDelta = operatorStateRank(a, jobs) - operatorStateRank(b, jobs);
  if (stateDelta) return stateDelta;
  const priorityDelta = priorityScore(b) - priorityScore(a);
  if (priorityDelta) return priorityDelta;
  const aNext = nextRunTime(a) || Number.MAX_SAFE_INTEGER;
  const bNext = nextRunTime(b) || Number.MAX_SAFE_INTEGER;
  if (aNext !== bNext) return aNext - bNext;
  const todayDelta = Number(Boolean(b.todayRelevant)) - Number(Boolean(a.todayRelevant));
  if (todayDelta) return todayDelta;
  return timeValue(b.updated_at) - timeValue(a.updated_at);
}

function operatorSortedJobs(jobs: JobRow[], allJobs: JobRow[] = jobs) {
  return [...jobs].sort((a, b) => compareJobsForOperator(a, b, allJobs));
}

function representativeJob(jobs: JobRow[], allJobs: JobRow[] = jobs) {
  return operatorSortedJobs(jobs, allJobs)[0];
}

function priorityJobKey(job: JobRow): PriorityJobKey | "general" {
  const text = `${job.title} ${job.tool} ${job.agent_id}`.toLowerCase();
  return PRIORITY_JOB_RULES.find((rule) => rule.pattern.test(text))?.key || "general";
}

function priorityJobGroups(jobs: JobRow[]) {
  const ordered = operatorSortedJobs(jobs, jobs);
  const byPriority = new Map<PriorityJobKey, JobRow[]>();
  PRIORITY_JOB_RULES.forEach((rule) => byPriority.set(rule.key, []));
  const general: JobRow[] = [];
  ordered.forEach((job) => {
    const key = priorityJobKey(job);
    if (key === "general") general.push(job);
    else byPriority.get(key)?.push(job);
  });
  return { byPriority, general };
}

function sorareGroupKey(job: JobRow): SorareGroupKey {
  const title = missionText(job.title).toLowerCase();
  const text = `${title} ${job.detail} ${job.tool}`.toLowerCase();
  if (SORARE_GENERAL_PATTERN.test(title)) return "general";
  const missionRule = SORARE_DAILY_GROUPS.find((group) => group.key === "missions");
  const lineupRule = SORARE_DAILY_GROUPS.find((group) => group.key === "lineups");
  if (missionRule?.pattern.test(text)) return "missions";
  if (lineupRule?.pattern.test(text)) return "lineups";
  return "general";
}

function sorareDailyGroups(jobs: JobRow[]) {
  const sorareJobs = operatorSortedJobs(
    jobs.filter((job) => priorityJobKey(job) === "sorare"),
    jobs,
  );
  return SORARE_DAILY_GROUPS.map((group) => ({
    ...group,
    items: sorareJobs.filter((job) => sorareGroupKey(job) === group.key),
  }));
}

function compactJobStatus(items: JobRow[]) {
  if (!items.length) return "Ready";
  if (items.some((job) => jobNeedsAttention(job, items))) return "Needs focus";
  if (items.some((job) => jobWorkState(job, items) === "working")) return "Working";
  return "Ready";
}

function sorareGroupSummary(group: ReturnType<typeof sorareDailyGroups>[number]) {
  if (!group.items.length) return "No scheduled jobs in this bucket";
  const count = group.items.length;
  if (group.key === "lineups") return `${count} jobs · lineups, pre-lock, RP checks`;
  if (group.key === "missions") return `${count} jobs · missions, prep, claims`;
  if (group.key === "general") return `${count} jobs · health, data, model`;
  const names = group.items
    .slice(0, 3)
    .map((job) => compactJobTitle(job))
    .filter(Boolean)
    .join(" · ");
  const extra = group.items.length > 3 ? ` · +${group.items.length - 3} more` : "";
  return compactText(`${group.items.length} job${group.items.length === 1 ? "" : "s"} · ${names}${extra}`, 68);
}

function AgentOpsHealth({ statuses }: { statuses: AgentStatus[] }) {
  const byAgent = new Map(statuses.map((status) => [status.agent_id, status]));
  return (
    <section className="agent-ops-health" aria-label="Agent operations health">
      {HERO_AGENT_ORDER.map((agent) => {
        const status = byAgent.get(agent) || offlineStatus(agent);
        const operating = agentOperatingState(status);
        return (
          <article key={agent} className={`agent-ops-chip ${agentClass(agent)} ${statusClass(status.status)}`}>
            <span className={`dot ${statusClass(status.status)}`} />
            <div>
              <b>{AGENTS[agent].roleBadge}</b>
              <strong>{AGENTS[agent].label}</strong>
            </div>
            <em>{operating} · {ageLabel(status.updated_at)}</em>
          </article>
        );
      })}
    </section>
  );
}

function JobsRail({
  jobs,
  statuses,
  quietMode,
  liveCues,
}: {
  jobs: MissionControlState["jobs"];
  statuses: AgentStatus[];
  quietMode: boolean;
  liveCues: LiveCueState;
}) {
  const trackedJobs = operatorTrackedJobs(jobs);
  const visibleJobs = quietMode
    ? trackedJobs.filter((job) => jobIsActiveOrNeedsAttention(job, trackedJobs) || priorityJobKey(job) !== "general")
    : trackedJobs;
  const inventoryGroups = groupedJobs(trackedJobs, "category");
  const attentionJobs = trackedJobs.filter((job) => jobNeedsAttention(job, trackedJobs)).length;
  const focusCount = visibleJobs.length;
  const quietInventoryCount = Math.max(0, trackedJobs.length - focusCount);
  const workingCount = trackedJobs.filter((job) => jobWorkState(job, trackedJobs) === "working").length;
  const nextJob = upcomingTodayJobs(trackedJobs, 1)[0];
  const nextRunValue = nextJob ? jobRunCells(nextJob).next : "None";
  const railSummary = attentionJobs
    ? `${attentionJobs} need Josh`
    : workingCount
      ? `${workingCount} running · next ${nextRunValue}`
      : `All clear · next ${nextRunValue}`;
  return (
    <aside id="today-jobs" className={`jobs-rail${sectionCueClass("jobs", liveCues)}`}>
      <SectionCue label={liveCues.focus === "jobs" ? "focus" : "updated"} />
      <div className="panel-title compact">
        <h2>Agent Ops & Jobs</h2>
          <span>{quietMode ? "Quiet focus" : railSummary}</span>
      </div>
      <AgentOpsHealth statuses={statuses} />
      <div className="jobs-stats-strip" aria-label="Agent Ops & Jobs summary">
        <article className={attentionJobs ? "is-risk" : "is-clear"}>
          <span>Action</span>
          <strong>{attentionJobs ? String(attentionJobs) : "None"}</strong>
        </article>
        <article>
          <span>Running</span>
          <strong>{workingCount ? String(workingCount) : "Idle"}</strong>
        </article>
        <article>
          <span>Next</span>
          <strong>{nextRunValue}</strong>
        </article>
        <article>
          <span>Inventory</span>
          <strong>{trackedJobs.length}</strong>
        </article>
      </div>
      <div className="jobs-operator-note" aria-label="Today jobs display policy">
        <strong>Operator view</strong>
        <span>{focusCount} surfaced · {quietInventoryCount} quiet in inventory</span>
      </div>
      <div className="job-list">
        <JobFocusView jobs={visibleJobs} allJobs={trackedJobs} quietMode={quietMode} liveCues={liveCues} />
        <SchedulerInventoryDisclosure groups={inventoryGroups} total={trackedJobs.length} surfaced={focusCount} liveCues={liveCues} />
      </div>
    </aside>
  );
}

function JobFocusView({ jobs, allJobs, quietMode, liveCues }: { jobs: JobRow[]; allJobs: JobRow[]; quietMode: boolean; liveCues: LiveCueState }) {
  const { byPriority, general } = priorityJobGroups(jobs);
  const sorareGroups = sorareDailyGroups(jobs);
  const actionJobs = operatorSortedJobs(allJobs.filter((job) => jobNeedsAttention(job, allJobs)), allJobs).slice(0, 2);
  const visibleGeneral = general.filter((job) => jobIsVisibleMaintenance(job, allJobs)).slice(0, quietMode ? 4 : 3);
  const upcoming = upcomingTodayJobs(allJobs, quietMode ? 5 : 6);
  const runningGeneral = visibleGeneral.filter((job) => jobWorkState(job, allJobs) === "working").length;
  const readyGeneral = Math.max(0, general.length - visibleGeneral.length);
  const readyCount = Math.max(0, allJobs.length - visibleGeneral.length - PRIORITY_JOB_RULES.reduce((sum, rule) => {
    if (rule.key === "sorare") return sum + sorareGroups.reduce((count, group) => count + group.items.length, 0);
    return sum + (byPriority.get(rule.key)?.length || 0);
  }, 0));
  return (
    <section className="job-focus-view">
      <div className="action-jobs-section">
        <header>
          <strong>Action</strong>
          <span>{actionJobs.length ? `${actionJobs.length} need Josh` : "no approval or repair needed"}</span>
        </header>
        <div className="operator-queue-list">
          {actionJobs.length ? actionJobs.map((job) => (
            <JobFocusRow key={`action-${job.id}`} job={job} priority liveCues={liveCues} />
          )) : (
            <article className="operator-clear-card">
              <span className="status-dot is-done" aria-hidden="true" />
              <div>
                <strong>No action needed</strong>
                <p>Agents are reporting, jobs are scheduled, and no current blocker needs Josh.</p>
              </div>
            </article>
          )}
        </div>
      </div>
      <div className="priority-jobs">
        <header>
          <strong>Today matters</strong>
          <span>{quietMode ? "quiet focus" : "Gmail · Sorare · Fantasy"}</span>
        </header>
        <div className="operator-queue-list priority-job-list">
          {PRIORITY_JOB_RULES.map((rule) => {
            if (rule.key === "sorare") return <SorareDailyJobsPanel key={rule.key} groups={sorareGroups} liveCues={liveCues} />;
            const rows = byPriority.get(rule.key) || [];
            const job = representativeJob(rows, allJobs);
            if (job) return <JobFocusRow key={rule.key} job={job} label={rule.label} count={rows.length} priority liveCues={liveCues} />;
            return (
              <article key={rule.key} className="operator-clear-card is-quiet">
                <span className="status-dot is-muted" aria-hidden="true" />
                <div>
                  <strong>{rule.label}</strong>
                  <p>{rule.agent} is ready; no current focus item.</p>
                </div>
              </article>
            );
          })}
        </div>
      </div>
      <div className="upcoming-jobs-section">
        <header>
          <strong>Up next</strong>
          <span>{upcoming.length ? `${upcoming.length} scheduled` : "No scheduled jobs found"}</span>
        </header>
        <div className="operator-queue-list upcoming-job-list">
          {upcoming.length ? upcoming.map((job) => <JobFocusRow key={`upcoming-${job.id}`} job={job} liveCues={liveCues} />) : (
            <article className="maintenance-summary-card">
              <strong>No scheduled jobs found</strong>
              <p>Priority and active work remain surfaced above.</p>
            </article>
          )}
        </div>
      </div>
      <details className="general-jobs-section">
        <summary>
          <strong>Background maintenance</strong>
          <span>{visibleGeneral.length ? `${runningGeneral ? `${runningGeneral} running · ` : ""}${visibleGeneral.length} shown` : "All routine"} · {readyGeneral + readyCount} ready</span>
        </summary>
        <div className="general-job-list">
          {visibleGeneral.length ? visibleGeneral.map((job) => <JobFocusRow key={job.id} job={job} liveCues={liveCues} />) : (
            <article className="maintenance-summary-card">
              <strong>Routine jobs ready</strong>
              <p>{quietMode ? "Quiet mode is hiding routine maintenance." : "Only priority work is active right now."}</p>
            </article>
          )}
          {readyCount > 0 ? (
            <article className="quiet-jobs-row">
              <strong>{readyCount} additional routine jobs ready</strong>
              <p>{quietMode ? "Quiet mode is showing only priority, active, missed, or blocked work." : "Completed or low-signal maintenance is collapsed from the focus view."}</p>
            </article>
          ) : null}
        </div>
      </details>
    </section>
  );
}

function SchedulerInventoryDisclosure({
  groups,
  total,
  surfaced,
  liveCues,
}: {
  groups: Array<{ key: string; label: string; items: JobRow[] }>;
  total: number;
  surfaced: number;
  liveCues: LiveCueState;
}) {
  return (
    <details className="scheduler-inventory-section">
      <summary>
        <strong>Scheduler inventory</strong>
        <span>{total} tracked · {Math.max(0, total - surfaced)} quiet · audit only</span>
      </summary>
      <div className="scheduler-inventory-note">
        <strong>Full scheduler list</strong>
        <p>Use this for audit/debugging. The operator queue above is the source for what needs attention.</p>
      </div>
      <JobCategoryView groups={groups} liveCues={liveCues} />
    </details>
  );
}

function UpcomingJobRow({ job, allJobs, liveCues }: { job: JobRow; allJobs: JobRow[]; liveCues: LiveCueState }) {
  const category = jobCategory(job);
  const run = jobRunCells(job);
  const owner = AGENTS[job.agent_id]?.label || job.agent_id;
  const changed = Boolean(liveCues.rows[cueRowKey("job", job.id || job.title)]);
  const lastText = run.last === "No log" ? "No prior run logged" : `Last ${run.last}`;
  return (
    <article className={`upcoming-job-row ${jobStatusClass(job, allJobs)} ${agentClass(job.agent_id)} ${categoryClass(job)} ${jobNeedsAttention(job, allJobs) ? "needs-focus" : ""}${changedRowClass(changed)}`}>
      <span className="row-change-dot" aria-hidden="true" />
      <span className={`status-dot ${jobStatusClass(job, allJobs)} ${agentClass(job.agent_id)}`} aria-hidden="true" />
      <time title={run.next}>{run.next}</time>
      <div>
        <strong title={missionText(job.title)}>{compactJobTitle(job)}</strong>
        <p title={missionText(job.detail || job.tool)}>By {owner} · {category.label} · {lastText}</p>
      </div>
      <span className={`job-status ${jobStatusClass(job, allJobs)}`}>{jobStatusLabel(job, allJobs)}</span>
    </article>
  );
}

function JobTableHeader() {
  return (
    <div className="job-table-head" aria-hidden="true">
      <span />
      <span>Job</span>
      <span>Owner</span>
      <span>Today</span>
      <span>Last</span>
      <span>Next</span>
      <span>Status</span>
    </div>
  );
}

function JobFocusRow({ job, label, count, priority = false, liveCues }: { job: JobRow; label?: string; count?: number; priority?: boolean; liveCues: LiveCueState }) {
  const category = jobCategory(job);
  const meta = label
    ? `${AGENTS[job.agent_id]?.label || job.agent_id} · ${label}${count && count > 1 ? ` · ${count} jobs` : ""}`
    : `${AGENTS[job.agent_id]?.label || job.agent_id} · ${category.label}`;
  const detail = compactJobDetail(job, meta, label ? undefined : category.label);
  const run = jobRunCells(job);
  const changed = Boolean(liveCues.rows[cueRowKey("job", job.id || job.title)]);
  const displayTitle = label === "Personal Gmail Triage" ? label : compactJobTitle(job);
  return (
    <article className={`job-focus-row ${priority ? "is-priority" : ""} ${jobStatusClass(job)} ${agentClass(job.agent_id)} ${categoryClass(job)}${changedRowClass(changed)}`}>
      <span className="row-change-dot" aria-hidden="true" />
      <span className={`status-dot ${jobStatusClass(job)} ${agentClass(job.agent_id)}`} aria-hidden="true" />
      <div className="job-table-title">
        <strong title={missionText(job.title)}>{displayTitle}</strong>
        <p title={missionText(job.detail || job.tool || meta)}>{detail}</p>
      </div>
      <div className="job-queue-meta" title={`${meta} · ${run.today} · Last ${run.last} · Next ${run.next}`}>
        <span>{compactOwnerLabel(job, count)}</span>
        <span className={`job-run-indicator ${run.todayClass}`}>{run.today}</span>
        <span>{run.next}</span>
      </div>
      <span className={`job-status ${jobStatusClass(job)}`}>{jobStatusLabel(job)}</span>
    </article>
  );
}

function SorareDailyJobsPanel({ groups, liveCues }: { groups: ReturnType<typeof sorareDailyGroups>; liveCues: LiveCueState }) {
  const total = groups.reduce((sum, group) => sum + group.items.length, 0);
  return (
    <details className="sorare-daily-panel">
      <summary>
        <span className="status-dot agent-jaimes" aria-hidden="true" />
        <div>
          <strong>Sorare Daily Jobs</strong>
          <p>{groups.length} collapsed groups · GW Limited Lineups · Daily Missions · General</p>
        </div>
        <em>{total ? `${total} jobs` : "Ready"}</em>
      </summary>
      <div className="sorare-subgroup-list">
        {groups.map((group) => {
          const focusJob = representativeJob(group.items);
          const run = focusJob ? jobRunCells(focusJob) : null;
          const detail = sorareGroupSummary(group);
          if (!focusJob) {
            return (
              <article key={group.key} className="sorare-subgroup is-empty">
                <div className="sorare-subgroup-summary is-quiet">
                  <span className="status-dot is-muted" aria-hidden="true" />
                  <div className="job-table-title">
                    <strong>{group.label}</strong>
                    <p>Waiting for the next scheduled Sorare daily job.</p>
                  </div>
                  <span className="job-table-owner">JAIMES</span>
                  <span className="job-run-cell job-run-indicator is-quiet">Ready</span>
                  <span className="job-run-cell">No active row</span>
                  <span className="job-run-cell">Awaiting schedule</span>
                  <span className="job-status is-done">Ready</span>
                </div>
              </article>
            );
          }
          return (
            <details key={group.key} className="sorare-subgroup">
              <summary className="sorare-subgroup-summary">
                  <span className={`status-dot ${jobStatusClass(focusJob, group.items)} ${agentClass(focusJob.agent_id)}`} aria-hidden="true" />
                  <div className="job-table-title">
                    <strong title={group.label}>{group.label}</strong>
                    <p title={missionText(focusJob.detail || focusJob.tool)}>{detail}</p>
                  </div>
                  <span className="job-table-owner">JAIMES · {group.items.length}</span>
                  <span className={`job-run-cell job-run-indicator ${run?.todayClass || "is-quiet"}`}>{run?.today}</span>
                  <span className="job-run-cell">{run?.last}</span>
                  <span className="job-run-cell">{run?.next}</span>
                  <span className={`job-status ${jobStatusClass(focusJob, group.items)}`}>{compactJobStatus(group.items)}</span>
              </summary>
              <div className="sorare-job-lines" aria-label={`${group.label} line items`}>
                {group.items.map((job) => <SorareJobLine key={job.id} job={job} liveCues={liveCues} />)}
              </div>
            </details>
          );
        })}
      </div>
    </details>
  );
}

function SorareJobLine({ job, liveCues }: { job: JobRow; liveCues: LiveCueState }) {
  const run = jobRunCells(job);
  const detail = compactJobDetail(job);
  const changed = Boolean(liveCues.rows[cueRowKey("job", job.id || job.title)]);
  return (
    <article className={`sorare-job-line ${jobStatusClass(job)} ${agentClass(job.agent_id)} ${categoryClass(job)}${changedRowClass(changed)}`}>
      <span className="row-change-dot" aria-hidden="true" />
      <div>
        <strong title={missionText(job.title)}>{compactJobTitle(job)}</strong>
        <p title={missionText(job.detail || job.tool)}>{detail}</p>
      </div>
      <span className={`job-run-cell job-run-indicator ${run.todayClass}`}>{run.today}</span>
      <span className="job-run-cell">{run.last}</span>
      <span className="job-run-cell">{run.next}</span>
      <span className={`job-status ${jobStatusClass(job)}`}>{jobStatusLabel(job)}</span>
    </article>
  );
}

function JobCategoryView({ groups, liveCues }: { groups: Array<{ key: string; label: string; items: JobRow[] }>; liveCues: LiveCueState }) {
  return (
    <>
      {groups.length ? groups.map((group) => (
        <details key={group.key} className="job-category" open>
          <summary>
            <span>{group.label}</span>
            <em>{jobGroupSummary(group.items)}</em>
            <strong>{group.items.length}</strong>
          </summary>
          <div className="job-category-list">
            {group.items.map((job) => {
              const owner = AGENTS[job.agent_id]?.label || job.agent_id;
              const run = jobRunCells(job);
              return (
                <article key={job.id} className={`job-row compact ${jobStatusClass(job, group.items)} ${agentClass(job.agent_id)} ${categoryClass(job)}${changedRowClass(Boolean(liveCues.rows[cueRowKey("job", job.id || job.title)]))}`}>
                  <span className="row-change-dot" aria-hidden="true" />
                  <span className={`status-dot ${jobStatusClass(job, group.items)} ${agentClass(job.agent_id)}`} aria-hidden="true" />
                  <div>
                    <strong title={missionText(job.title)}>{missionText(job.title)}</strong>
                    <p title={missionText(job.detail || job.tool)}>By {owner} · Next {run.next}</p>
                  </div>
                  <span className={`job-status ${jobStatusClass(job, group.items)}`}>{jobStatusLabel(job, group.items)}</span>
                  <time title={`Last ${run.last}`}>{run.next}</time>
                </article>
              );
            })}
          </div>
        </details>
      )) : <EmptyRow title="No jobs yet" detail="Agent jobs will appear here." />}
    </>
  );
}

function ModelUsageCard({
  modelUsage,
  modelRouter,
}: {
  modelUsage?: MissionControlState["modelUsage"];
  modelRouter?: MissionControlState["modelRouter"];
}) {
  const topModels = modelUsage?.breakdown?.length ? modelUsage.breakdown : modelUsage?.topModels || [];
  const providers = modelRouter?.providers || modelUsage?.providerBudgets || [];
  const codexMode = String(modelRouter?.codexAllowanceMode || modelRouter?.policy?.codexAllowanceMode || modelUsage?.routerPolicy?.codexAllowanceMode || "normal");
  const routeQuality = typeof modelRouter?.routeQualityScore === "number" ? `${modelRouter.routeQualityScore}/100` : "--";
  const efficiency = typeof modelRouter?.efficiencyScore === "number" ? `${modelRouter.efficiencyScore}/100` : "--";
  const lastRoute = modelRouter?.lastRoute || {};
  const lastRouteLabel = String(lastRoute.routeLabel || lastRoute.provider || "no route yet");
  const routeMix = Object.entries(modelRouter?.routeMix || {})
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 3);
  const routeAlerts = modelRouter?.routeAlerts || [];
  return (
    <section className="model-usage-card">
      <div className="panel-title compact">
        <h2>Model Cost & Usage</h2>
        <span><DollarSign size={14} />{codexMode === "normal" ? `${fmtCurrency(modelUsage?.daily)} daily` : `Codex ${codexMode}`}</span>
      </div>
      <div className="cost-grid">
        <MetricMini label="Session" value={fmtCurrency(modelUsage?.session)} />
        <MetricMini label="Weekly" value={fmtCurrency(modelUsage?.weekly)} />
        <MetricMini label="Monthly" value={fmtCurrency(modelUsage?.monthly)} />
        <MetricMini label="Projected" value={fmtCurrency(modelUsage?.weeklyRunRate?.projectedMonthly)} />
      </div>
      <div className={`model-ladder-strip is-${modelRouter?.ladderStatus || "pending"}`}>
        <article>
          <span>Route quality</span>
          <strong>{routeQuality}</strong>
        </article>
        <article>
          <span>Efficiency</span>
          <strong>{efficiency}</strong>
        </article>
        <article>
          <span>Last lane</span>
          <strong>{missionText(lastRouteLabel)}</strong>
        </article>
      </div>
      {routeMix.length ? (
        <div className="route-mix-row" aria-label="Recent model ladder route mix">
          {routeMix.map(([label, count]) => (
            <span key={label}>{missionText(label.replace(/_/g, " "))}: {count}</span>
          ))}
        </div>
      ) : null}
      {routeAlerts.length ? (
        <div className="route-alert-row">
          {routeAlerts.slice(0, 2).map((alert) => <span key={alert}>{missionText(alert)}</span>)}
        </div>
      ) : null}
      <div className="model-list">
        {topModels.slice(0, 5).map((model: any) => (
          <article key={`${model.name}-${model.source || model.window || ""}`}>
            <strong>{model.name}</strong>
            <span>{model.source || model.window || "model"}</span>
            <em>{fmtCurrency(model.weeklyCost ?? model.cost)}</em>
          </article>
        ))}
      </div>
      <div className="provider-budget-list" aria-label="Provider budget caps">
        {providers.slice(0, 4).map((provider: any) => {
          const cap = provider.dailyCapUsd || provider.monthlyCapUsd || 0;
          const spend = provider.dailySpendUsd || 0;
          const pct = cap ? Math.min(100, Math.round((spend / cap) * 100)) : 0;
          return (
            <article key={provider.id} className={`provider-budget is-${provider.status || "ready"}`}>
              <header>
                <strong>{provider.label || provider.id}</strong>
                <em>{provider.status || "ready"}</em>
              </header>
              <div className="provider-budget-meter" style={{ "--pct": pct } as React.CSSProperties}>
                <span />
              </div>
              <p>{fmtCurrency(spend)} today{provider.dailyCapUsd ? ` / ${fmtCurrency(provider.dailyCapUsd)} cap` : ""}</p>
              <footer>
                <span>{provider.lastModelUsed || "model route"}</span>
                {provider.remainingCreditUsd != null ? <em>{fmtCurrency(provider.remainingCreditUsd)} left</em> : null}
              </footer>
              {(provider.authStatus || provider.lastTestStatus) ? (
                <small>
                  {provider.authStatus || provider.lastTestStatus}
                  {provider.keySuffix ? ` · key ...${provider.keySuffix}` : ""}
                </small>
              ) : null}
            </article>
          );
        })}
      </div>
      <footer className="card-footer">
        {modelRouter?.summary || "Codex default; specialist fallback lanes available."} · Updated {fmtTime(modelUsage?.lastUpdated)}
      </footer>
    </section>
  );
}

function MetricMini({ label, value }: { label: string; value: string }) {
  return (
    <article>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function PipelineCard({ status }: { status: AgentStatus }) {
  const phases = [
    { label: "Report intake", value: "ready" },
    { label: "Model audit", value: "ready" },
    { label: "Route outcome", value: status.status },
    { label: "Handoff", value: "active" },
  ];
  return (
    <section className="pipeline-card">
      <div className="panel-title compact">
        <h2>JAIMES / Hermes</h2>
        <span><GitBranch size={14} />Pipeline</span>
      </div>
      <div className="phase-grid">
        {phases.map((phase) => (
          <article key={phase.label}>
            <CheckCircle2 size={17} />
            <span>{phase.label}</span>
            <strong>{phase.value}</strong>
          </article>
        ))}
      </div>
    </section>
  );
}

function ApprovalInbox({ approvals }: { approvals: MissionControlState["approvals"] }) {
  return (
    <section className="approval-inbox">
      <div className="panel-title compact">
        <h2>Handoff Inbox</h2>
        <span><UserRoundCheck size={14} />{approvals.filter((row) => row.status === "pending").length} pending</span>
      </div>
      <div className="approval-list">
        {approvals.length ? approvals.slice(0, 8).map((approval) => (
          <article key={approval.id} className="approval-row">
            <span className={`status-pill ${statusClass(approval.status)}`}>{approval.status}</span>
            <div>
              <strong>{approval.title}</strong>
              <p>{approval.detail}</p>
              <small>{AGENTS[approval.requested_by]?.label || approval.requested_by} to {AGENTS[approval.agent_id]?.label || approval.agent_id}</small>
            </div>
          </article>
        )) : <EmptyRow title="No pending handoffs" detail="Approval rows will appear here." />}
      </div>
    </section>
  );
}

function EmptyRow({ title, detail }: { title: string; detail: string }) {
  return (
    <article className="empty-row">
      <strong>{title}</strong>
      <p>{detail}</p>
    </article>
  );
}

function fmtCurrency(value?: number) {
  if (typeof value !== "number" || Number.isNaN(value)) return "$0.00";
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: value >= 100 ? 0 : 2 }).format(value);
}

function fmtCurrencyExact(value?: number) {
  if (typeof value !== "number" || Number.isNaN(value)) return "$0.00";
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function displayModelName(value?: string | null) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (/gpt-5\.5/i.test(text)) return "gpt-5.5";
  if (/gpt-5\.4-mini/i.test(text)) return "gpt-5.4-mini";
  if (/gpt-5\.4/i.test(text)) return "gpt-5.4";
  return text.replace(/^openai(?:-codex)?\//i, "").replace(/^codex\//i, "");
}

function offlineStatus(agent: AgentId): AgentStatus {
  return {
    agent_id: agent,
    status: "offline",
    objective: "No current Control Tower status has been published yet",
    detail: "This agent has not reported a dashboard-safe status row.",
    current_tool: "",
    active: false,
    updated_at: "",
    steps: [],
  };
}

const MemoizedFinOpsDashboard = React.memo(FinOpsDashboard);
const MemoizedAgenticCryptoPanel = React.memo(AgenticCryptoPanel);
const MemoizedSignalFeed = React.memo(SignalFeed);

createRoot(document.getElementById("root")!).render(<ErrorBoundary><App /></ErrorBoundary>);
