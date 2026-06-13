import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AlertTriangle, CheckCircle2, ClipboardList, Coins, DollarSign, EyeOff, GitBranch, Radio, RefreshCw, ShieldCheck, Timer, UserRoundCheck, WalletCards } from "lucide-react";
import { loadMissionControl, subscribeMissionControlRealtime } from "./data";
import { PRIORITY_JOB_RULES, SORARE_DAILY_GROUPS, SORARE_GENERAL_PATTERN, type PriorityJobKey, type SorareGroupKey } from "./priorityJobs";
import type { AgenticCryptoWallet, AgentId, AgentStatus, MissionControlState, SignalItem } from "./types";
import "./styles.css";

const AGENTS: Record<AgentId, { label: string; role: string }> = {
  joshex: { label: "JOSHeX", role: "Private coordination" },
  josh: { label: "Josh 2.0", role: "Control Tower host" },
  jaimes: { label: "JAIMES", role: "Hermes workhorse" },
  jain: { label: "J.A.I.N", role: "Breaking + signals" },
};
const HERO_AGENT_ORDER: AgentId[] = ["joshex", "josh", "jaimes", "jain"];

type AttentionTarget = "brain-feed" | "today-jobs" | "signal-feed";
type WorkState = "working" | "waiting" | "blocked" | "ready" | "done" | "quiet";
type AgentVisualState = "working" | "routine" | "ready" | "waiting" | "blocked" | "stale";
type StepTrailState = "done" | "current" | "pending";
type AgentHeadline = { eyebrow: string; time?: string; title: string; description: string };
type AgentIdleContext = {
  complete: string;
  nextTitle: string;
  nextBullets: Array<{ label: string; text: string }>;
  nextAt?: number;
  countdown: string;
};
type AgentBriefRow = { label: string; text: string };
type AgentInsightRow = { label: string; text: string; tone?: "default" | "good" | "watch" | "active" };
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
type SectionCueKey = "brain" | "jobs" | "system" | "signal" | "crypto";
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
    "jaimes-ops-drift-check": "JAIMES ops drift check",
    "jaimes-model-efficiency-guard": "JAIMES model efficiency guard",
    index: "Control Tower build",
  };
  const humanizeScript = (name: string) => {
    const stem = name.split("/").pop()?.replace(/\.(py|sh|js|ts|tsx|html)$/i, "") || name;
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
    .replace(/\bjaimes-ops-drift-check\b/gi, "JAIMES ops drift check")
    .replace(/\bjaimes-model-efficiency-guard\b/gi, "JAIMES model efficiency guard")
    .replace(/current Control Tower/gi, "current Control Tower")
    .replace(/Control Tower/gi, "Control Tower")
    .replace(/React v2/gi, "React Control Tower")
    .replace(/v2 refresh/gi, "current refresh")
    .replace(/v2 row/gi, "status row")
    .replace(/v2 status\/events/gi, "status/events")
    .replace(/v2 jobs/gi, "jobs")
    .replace(/v2 state/gi, "status")
    .replace(/JAIMES v2 job smoke/gi, "JAIMES job smoke")
    .replace(/JAIMES v2 handoff smoke/gi, "JAIMES handoff smoke")
    .replace(/\b([a-z0-9_.-]+)\s+cron:\s+((?:\/[^ ]+\/)?[A-Za-z0-9_.-]+\.(?:py|sh|js|ts|tsx|html))/gi, (_, host, script) => `${host} scheduled: ${humanizeScript(script)}`)
    .replace(/(?<![\w./-])((?:\/[^ ]+\/)?[A-Za-z0-9_-]+\.(?:py|sh|js|ts|tsx|html))(?![\w./-])/gi, (_, script) => humanizeScript(script));
}

function sourceTruthLabel(source?: string | null) {
  const value = missionText(source || "").trim();
  if (!value) return "Local live source";
  if (/josh\s*2\.0.*local.*live/i.test(value)) return "Josh 2.0 live source";
  if (/supabase/i.test(value)) return "Cloud mirror";
  return value.length > 28 ? `${value.slice(0, 27).trim()}...` : value;
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
  if (value === "paused" || value === "scheduled" || value === "idle" || value === "stale" || value === "offline") return "Quiet";
  return missionText(status || "Quiet");
}

function agentIsReady(status?: AgentStatus) {
  if (!status) return false;
  const value = String(status.status || "").toLowerCase();
  return Boolean(status.active) || ["active", "queued", "ready", "ok", "done", "approved", "stale"].includes(value);
}

function missionTruthSummary(state: MissionControlState) {
  const dataIssues = dataQualityIssues(state);
  const riskDataIssues = dataIssues.filter((issue) => issue.tone === "risk");
  const softDataIssues = dataIssues.filter((issue) => issue.tone !== "risk");
  const layoutIssues = state.runtimeLayout?.issues || [];
  const layoutMissing = !state.runtimeLayout;
  const layoutRisk = Boolean(state.runtimeLayout && (state.runtimeLayout.ok === false || layoutIssues.length));
  const layoutOk = !layoutMissing && !layoutRisk;
  const layoutAge = state.runtimeLayout?.checkedAt ? ageLabel(state.runtimeLayout.checkedAt) : "not checked";
  const readyAgents = state.statuses.filter((row) => {
    const value = String(row.status || "").toLowerCase();
    return !["blocked", "error", "offline", "stale"].includes(value) && ageMinutes(row.updated_at) <= 120;
  }).length;
  const trackedAgents = Math.max(4, state.statuses.length);
  const agentOk = readyAgents >= trackedAgents;
  const walletOk = !state.agenticCrypto || ["fresh", "ok", "ready"].includes(String(state.agenticCrypto.status || "").toLowerCase());
  const riskItems = [
    layoutRisk ? "screen fit" : null,
    agentOk ? null : "agents",
    walletOk ? null : "wallet",
    riskDataIssues.length ? "data guard" : null,
  ].filter((item): item is string => Boolean(item));
  const watchItems = [
    layoutMissing ? "screen check" : null,
    softDataIssues.length ? "freshness check" : null,
  ].filter((item): item is string => Boolean(item));
  const tone = riskItems.length ? "risk" : watchItems.length ? "watch" : "clear";
  return {
    tone,
    label: riskItems.length ? "Needs review" : watchItems.length ? "Watch" : "Live",
    short: `${readyAgents}/${trackedAgents} agents · kiosk ${layoutOk ? "ready" : "watch"}`,
    detail: riskItems.length
      ? `Check ${riskItems.join(", ")}`
      : watchItems.length
        ? `${sourceTruthLabel(state.source)} · ${watchItems.join(", ")} · kiosk ${layoutAge}`
      : `${sourceTruthLabel(state.source)} · kiosk ${layoutAge}`,
  };
}

function agentOperatingState(status: AgentStatus) {
  const value = String(status.status || "").toLowerCase();
  if (value === "blocked" || value === "error") return "Needs focus";
  if (value === "ready" || value === "ok" || value === "done" || value === "approved" || isReadyHeartbeatStatus(status)) return "Ready";
  if (agentIsWorking(status)) return "Working";
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

function isReadyHeartbeatStatus(status: AgentStatus) {
  const value = String(status.status || "").toLowerCase();
  if (value === "blocked" || value === "error") return false;
  const text = missionText(`${status.objective} ${status.detail} ${status.current_tool}`).toLowerCase();
  const explicitlyReady = ["ready", "ok", "done", "idle", "info"].includes(value);
  return (
    explicitlyReady
    || text.includes("online and ready")
    || text.includes("not actively working")
    || text.includes("no active queued worker tasks")
    || text.includes("standing by")
    || text.includes("standby")
  );
}

function agentIsWorking(status: AgentStatus) {
  const value = String(status.status || "").toLowerCase();
  if (isReadyHeartbeatStatus(status)) return false;
  return ["active", "working", "running", "queued"].includes(value) || Boolean(status.active);
}

function statusWorkState(status: AgentStatus): WorkState {
  const value = String(status.status || "").toLowerCase();
  if (value === "blocked" || value === "error") return "blocked";
  if (agentIsWorking(status)) return "working";
  if (value === "ready" || value === "ok" || value === "approved") return "ready";
  if (value === "done") return "done";
  return "quiet";
}

function jobWorkState(job: MissionControlState["jobs"][number], jobs: MissionControlState["jobs"] = []): WorkState {
  const rawStatus = String(job.status || "").toLowerCase();
  const value = String(job.runStatus || job.status || "").toLowerCase();
  if (rawStatus === "paused") return "quiet";
  if (jobNeedsAttention(job, jobs)) return "blocked";
  if (value === "missed") return jobIsSoftMissedAutomation(job) ? "ready" : "blocked";
  if ((value === "active" || value === "running" || value === "queued") && jobIsFreshActive(job)) return "working";
  if (value === "due" || value === "upcoming" || value === "scheduled") return "ready";
  if (value === "done" || value === "completed" || job.verifiedToday || sameLocalDay(job.lastRun || job.completed_at || job.updated_at)) return "done";
  return "quiet";
}

function jobIsRoutineActivity(job: MissionControlState["jobs"][number]) {
  const text = missionText(`${job.title} ${job.detail} ${job.tool} ${job.sourceLabel} ${job.schedule}`).toLowerCase();
  return textIsRoutineActivity(text);
}

function textIsRoutineActivity(text: string) {
  const routineMatch = /context sync|brain feed server|control tower refresh|watchdog|heartbeat|health check|agent control checks|automation checks|silence detector|error rate monitor|invite sync|calendar sync|appointment sync|chiro invite/.test(text);
  const priorityMatch = /gmail|inbox|sorare|fantasy|waiver|lineup|daily mission|breaking news|x watchlist|wallet|crypto/.test(text);
  return routineMatch && !priorityMatch;
}

function workItemIsRoutineActivity(work: WorkItem | undefined, status?: AgentStatus) {
  const text = missionText(`${work?.title || ""} ${work?.detail || ""} ${work?.source || ""} ${status?.objective || ""} ${status?.detail || ""} ${status?.current_tool || ""}`).toLowerCase();
  return textIsRoutineActivity(text);
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
    .sort((a, b) => {
      const rank = (state: WorkState) => state === "blocked" ? 0 : state === "working" ? 1 : state === "ready" ? 2 : 3;
      const aState = jobWorkState(a, state.jobs);
      const bState = jobWorkState(b, state.jobs);
      const stateDelta = rank(aState) - rank(bState);
      if (stateDelta) return stateDelta;
      const priorityDelta = (priorityJobKey(b) !== "general" ? 1 : 0) - (priorityJobKey(a) !== "general" ? 1 : 0);
      if (priorityDelta) return priorityDelta;
      return timeValue(b.updated_at) - timeValue(a.updated_at);
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
        detail: compactText(status.detail || status.current_tool || "Current Brain Feed objective", 72),
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

function jobSupersedeKey(text: string) {
  if (/ops gmail|shared ops gmail|gmail monitor/.test(text)) return "ops-gmail";
  if (/base mcp|base account|mcp oauth/.test(text)) return "base-mcp";
  return "";
}

function jobFailureSuperseded(job: MissionControlState["jobs"][number], jobs: MissionControlState["jobs"] = []) {
  const text = `${job.title} ${job.detail} ${job.tool}`.toLowerCase();
  const updated = timeValue(job.updated_at);
  const key = jobSupersedeKey(text);
  if (!key || !updated) return false;
  return jobs.some((other) => {
    if (other.id === job.id || other.agent_id !== job.agent_id) return false;
    const otherText = `${other.title} ${other.detail} ${other.tool}`.toLowerCase();
    const otherStatus = String(other.runStatus || other.status || "").toLowerCase();
    return timeValue(other.updated_at) > updated
      && jobSupersedeKey(otherText) === key
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

function updatedFreshnessLabel(value?: string | null) {
  const label = ageLabel(value);
  if (label === "no update") return "No update";
  if (label === "just now") return "Updated just now";
  return `Updated ${label} ago`;
}

function latestTimestamp(values: Array<string | null | undefined>) {
  return values
    .filter((value): value is string => Boolean(value && timeValue(value)))
    .sort((a, b) => timeValue(a) - timeValue(b))
    .pop();
}

function dashboardFreshnessTimestamp(state: MissionControlState) {
  return latestTimestamp([
    state.lastUpdated,
    ...state.statuses.map((row) => row.updated_at),
    ...state.events.map((row) => row.created_at),
    ...state.jobs.flatMap((row) => [row.updated_at, row.lastRun, row.completed_at, row.started_at]),
    state.signalHealth?.generatedAt,
    ...(state.signals || []).map((row) => row.time),
    state.agenticCrypto?.updatedAt,
    state.agenticCrypto?.summary?.lastRefreshed,
    state.modelUsage?.lastUpdated,
    state.capabilityWatch?.updatedAt,
    state.capabilityWatch?.checkedAt,
    state.runtimeLayout?.checkedAt,
    state.reliabilityUpgrades?.updatedAt,
  ]);
}

function checkedFreshnessLabel(value?: string | null) {
  const label = ageLabel(value);
  if (label === "no update") return "not checked";
  if (label === "just now") return "checked just now";
  return `checked ${label} ago`;
}

function freshnessClass(value?: string | null) {
  const minutes = ageMinutes(value);
  if (!Number.isFinite(minutes) || minutes >= 30) return "is-stale";
  if (minutes >= 5) return "is-aging";
  return "is-fresh";
}

function agentCardFreshnessClass(status: AgentStatus) {
  const minutes = ageMinutes(status.updated_at);
  if (!Number.isFinite(minutes)) return "is-stale";
  if (isReadyHeartbeatStatus(status)) {
    if (minutes >= 120) return "is-stale";
    if (minutes >= 5) return "is-aging";
    return "is-fresh";
  }
  return freshnessClass(status.updated_at);
}

function dataQualityIssues(state: MissionControlState): AttentionItem[] {
  const trackedJobs = operatorTrackedJobs(state.jobs);
  const runtimeLayout = state.runtimeLayout;
  const runtimeIssues = runtimeLayout?.issues || [];
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
      why: "Control Tower is missing at least one core Brain Feed status row.",
      means: "A visible agent card may be stale even if the agent itself is healthy.",
      action: "Repair Brain Feed visibility and regenerate Control Tower data.",
      tone: "watch",
      target: "brain-feed",
    });
  }
  if (runtimeLayout && (!runtimeLayout.ok || runtimeIssues.length)) {
    issues.push({
      id: "data-runtime-layout",
      label: "Display",
      title: "Josh 2.0 screen layout needs attention",
      detail: runtimeIssues.slice(0, 2).join("; ") || runtimeLayout.summary || "The rendered kiosk layout did not pass its live fit check.",
      why: "Control Tower measured the actual Chrome kiosk and found a fit, overlap, or row-visibility issue.",
      means: "The data may still be fresh, but the wall display may be hiding or crowding important sections.",
      action: "Run the runtime layout guard, refresh Control Tower, and inspect the Josh 2.0 display.",
      tone: "risk",
      target: "brain-feed",
    });
  } else if (runtimeLayout?.checkedAt && ageMinutes(runtimeLayout.checkedAt) >= 45) {
    issues.push({
      id: "data-runtime-layout-stale",
      label: "Display",
      title: "Josh 2.0 screen check is stale",
      detail: `Last rendered-layout check was ${ageLabel(runtimeLayout.checkedAt)} ago.`,
      why: "The dashboard has not recently verified that all modules still fit the live wall display.",
      means: "Control Tower may still be usable, but the first-view fit guarantee is older than expected.",
      action: "Run the runtime layout guard and refresh Control Tower.",
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
  const healthStatus = String(state.signalHealth?.status || "").toLowerCase();
  const quietHours = Boolean(state.signalHealth?.quietHours);
  const nextBreakingRun = state.signalHealth?.nextBreakingRun;
  const staleSources = state.signalHealth?.staleSources || [];
  const fallbackFresh = Boolean(state.signalHealth?.fallbackFresh && (state.signalHealth?.counts?.publicRssFallbackItems || 0) >= 5);
  const staleSourceLabel = staleSources
    .map((source) => source
      .replace(/Minutes$/i, "")
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .toLowerCase())
    .filter(Boolean)
    .join(", ");
  const staleSourceShortLabel = staleSources
    .map((source) => source
      .replace(/Minutes$/i, "")
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .toLowerCase()
      .replace(/^breaking$/, "breaking")
      .replace(/^newsfeed$/, "newsfeed")
      .replace(/^newsletter$/, "newsletter"))
    .filter(Boolean)
    .join(", ");
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
      detail: `Signal Feed last checked ${ageLabel(timestamp)} ago.`,
    };
  }
  if (healthStatus === "quiet" || quietHours) {
    const detail = nextBreakingRun
      ? `Quiet hours; next breaking scan ${nextBreakingRun}. ${liveCount} live and ${newsletterCount} newsletter rows loaded.`
      : state.signalHealth?.summary || `Breaking scanner is paused overnight; checked ${ageLabel(timestamp)} ago.`;
    return {
      tone: "clear" as const,
      label: "Quiet-hours watch",
      detail,
    };
  }
  if (healthStatus === "attention" || staleSources.length) {
    return {
      tone: "watch" as const,
      label: staleSourceShortLabel ? `${staleSourceShortLabel} stale` : "Source watch",
      detail: staleSourceLabel
        ? `Scanner checked ${ageLabel(timestamp)} ago; ${staleSourceLabel} source is stale. ${liveCount} live and ${newsletterCount} newsletter rows loaded.`
        : state.signalHealth?.summary || `${liveCount} live and ${newsletterCount} newsletter rows loaded; checked ${ageLabel(timestamp)} ago.`,
    };
  }
  if (fallbackFresh) {
    return {
      tone: "clear" as const,
      label: "Fresh sources",
      detail: state.signalHealth?.summary || `${liveCount} live public rows and ${newsletterCount} newsletter rows loaded; checked ${ageLabel(timestamp)} ago.`,
    };
  }
  if (minutes > 15 || liveCount < 5 || newsletterCount < 5) {
    return {
      tone: "watch" as const,
      label: minutes > 15 ? "Scan aging" : "Coverage watch",
      detail: `${liveCount} live and ${newsletterCount} newsletter rows loaded; checked ${ageLabel(timestamp)} ago.`,
    };
  }
  return {
    tone: "clear" as const,
    label: "Signal Feed fresh",
    detail: `${liveCount} live and ${newsletterCount} newsletter rows loaded; checked ${ageLabel(timestamp)} ago.`,
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

function agentCadenceDetail(idleContext?: AgentIdleContext) {
  if (idleContext?.nextAt) {
    const next = countdownShortText(countdownLabel(idleContext.nextAt));
    if (next === "now") return "Checking now";
    return next ? `Next check ${next}` : "Next check soon";
  }
  return "Checks every 2h";
}

function agentSla(status: AgentStatus, idleContext?: AgentIdleContext) {
  const minutes = ageMinutes(status.updated_at);
  const expected = 120;
  const cadence = agentCadenceDetail(idleContext);
  if (!Number.isFinite(minutes)) {
    return { tone: "late", label: "No check-in found", detail: cadence };
  }
  const age = ageLabel(status.updated_at);
  if (minutes > expected) {
    return { tone: "late", label: age === "just now" ? "Late · checked just now" : `Late · checked ${age} ago`, detail: cadence };
  }
  if (minutes > expected * 0.75) {
    return { tone: "watch", label: age === "just now" ? "Refresh due · checked just now" : `Refresh due · checked ${age} ago`, detail: cadence };
  }
  return { tone: "ok", label: age === "just now" ? "Checked just now" : `Checked ${age} ago`, detail: cadence };
}

function agentClass(agent: AgentId) {
  return `agent-${agent}`;
}

function agentVisualState(status: AgentStatus, activeFocus: boolean, activeWork?: WorkItem, routineFocus = false): AgentVisualState {
  const value = String(status.status || "").toLowerCase();
  if (value === "blocked" || value === "error" || activeWork?.state === "blocked") return "blocked";
  if (activeWork?.state === "waiting") return "waiting";
  if (routineFocus) return "routine";
  if (activeFocus && activeWork?.state === "working") return "working";
  if (!isReadyHeartbeatStatus(status) && activeFocus) return "working";
  if (agentCardFreshnessClass(status) === "is-stale") return "stale";
  return "ready";
}

function agentHeaderStateLabel(visualState: AgentVisualState, routineFocus: boolean, activeFocus: boolean) {
  if (routineFocus) return "Current";
  if (activeFocus) return "Working";
  if (visualState === "blocked") return "Needs focus";
  if (visualState === "waiting") return "Needs Josh";
  if (visualState === "stale") return "Quiet";
  return "Ready";
}

function agentHeaderDotClass(visualState: AgentVisualState, routineFocus: boolean, activeFocus: boolean) {
  if (routineFocus) return "is-routine";
  if (activeFocus) return "is-active";
  if (visualState === "blocked" || visualState === "waiting") return "is-risk";
  if (visualState === "stale") return "is-muted";
  return "is-done";
}

function stepTrailForAgent(status: AgentStatus, activeFocus: boolean, activeWork?: WorkItem, routineFocus = false): Array<{ label: string; state: StepTrailState }> {
  const hasUpdate = Boolean(status.updated_at && timeValue(status.updated_at));
  const blocked = activeWork?.state === "blocked" || ["blocked", "error"].includes(String(status.status || "").toLowerCase());
  return [
    { label: "Start", state: hasUpdate ? "done" : "pending" },
    { label: blocked ? "Hold" : routineFocus ? "Sync" : "Work", state: activeFocus || blocked ? "current" : hasUpdate ? "done" : "pending" },
    { label: "Report", state: activeFocus || blocked ? "pending" : hasUpdate ? "current" : "pending" },
  ];
}

function textMentionsAgent(text: string, agent: AgentId) {
  const normalized = text.toLowerCase();
  if (agent === "joshex") return /\bjoshex\b|\bcodex\b/.test(normalized);
  if (agent === "josh") return /josh\s*2\.0|\bjosh\b|openclaw|host/.test(normalized);
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
      runtimeLayout: [state.runtimeLayout?.ok, state.runtimeLayout?.checkedAt, state.runtimeLayout?.issues],
    }),
    signal: compactSignature({
      health: [
        state.signalHealth?.status,
        state.signalHealth?.generatedAt,
        state.signalHealth?.counts?.live,
        state.signalHealth?.counts?.newsletter,
        state.signalHealth?.counts?.total,
        state.signalHealth?.staleSources,
      ],
      rows: state.signals.slice(0, 10).map((row) => [
        row.id,
        row.title,
        row.reason,
        row.source,
        row.score,
        row.time,
        row.section,
      ]),
    }),
    crypto: compactSignature({
      status: state.agenticCrypto?.status,
      mode: state.agenticCrypto?.walletMode,
      updatedAt: state.agenticCrypto?.updatedAt,
      summary: state.agenticCrypto?.summary,
      tokens: state.agenticCrypto?.tokens?.map((row) => [
        row.chain,
        row.symbol,
        row.amount,
        row.valueUsd,
        row.classification,
      ]),
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
  state.signals.slice(0, 10).forEach((row) => {
    rows[cueRowKey("signal", row.id || row.title)] = compactSignature([
      row.title,
      row.reason,
      row.impact,
      row.source,
      row.score,
      row.time,
      row.section,
    ]);
  });
  if (state.agenticCrypto) {
    const tokens = [...(state.agenticCrypto.tokens || [])].sort((a, b) => (b.valueUsd || 0) - (a.valueUsd || 0));
    rows[cueRowKey("crypto", "balance")] = compactSignature([
      state.agenticCrypto.status,
      state.agenticCrypto.walletMode,
      state.agenticCrypto.updatedAt,
      state.agenticCrypto.summary?.totalEstimatedUsd,
      state.agenticCrypto.summary?.liquidEstimatedUsd,
    ]);
    tokens.slice(0, 5).forEach((token) => {
      const tokenId = `${token.chain}-${token.symbol}-${token.contractMasked || token.mintMasked || token.source || ""}`;
      rows[cueRowKey("crypto-token", tokenId)] = compactSignature([
        token.amount,
        token.valueUsd,
        token.classification,
        token.source,
      ]);
    });
    const hiddenTokens = tokens.slice(5);
    if (hiddenTokens.length) {
      rows[cueRowKey("crypto", "smaller-tokens")] = compactSignature([
        hiddenTokens.length,
        hiddenTokens.reduce((sum, token) => sum + (token.valueUsd || 0), 0),
        hiddenTokens.map((token) => [token.chain, token.symbol, token.amount, token.valueUsd]),
      ]);
    }
  }
  return rows;
}

function focusSection(state: MissionControlState): SectionCueKey | null {
  if (state.approvals.some((row) => row.status === "pending") || state.jobs.some((job) => jobNeedsAttention(job, state.jobs))) return "jobs";
  const activeAgent = state.statuses.some((row) => agentIsWorking(row) && isFreshActiveTimestamp(row.updated_at));
  const activeWork = buildWorkItems(state).some((item) => item.state === "working" && isFreshActiveTimestamp(item.updated_at));
  if (activeAgent || activeWork) return "brain";
  const signalStatus = String(state.signalHealth?.status || "").toLowerCase();
  if (["watch", "stale", "error"].some((term) => signalStatus.includes(term)) || (state.signalHealth?.staleSources || []).length) return "signal";
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

type TowerLane = "active" | "needs-josh" | "complete" | "planned";
type TowerTone = "active" | "watch" | "risk" | "done" | "planned";
type TowerActivity = {
  id: string;
  lane: TowerLane;
  tone: TowerTone;
  agent: AgentId;
  title: string;
  detail: string;
  meta: string;
  time?: string;
  sortAt: number;
};
type ControlTowerModel = {
  active: TowerActivity[];
  needsJosh: TowerActivity[];
  complete: TowerActivity[];
  planned: TowerActivity[];
  counts: {
    agentsReady: number;
    agentsTotal: number;
    active: number;
    needsJosh: number;
    complete: number;
    planned: number;
    trackedJobs: number;
    systemQuiet: number;
    meaningfulComplete: number;
  };
};

const TOWER_AGENT_ORDER: AgentId[] = ["joshex", "josh", "jaimes", "jain"];
const TOWER_LANES: Array<{ key: TowerLane; label: string; empty: string }> = [
  { key: "active", label: "Active", empty: "No agent is actively working right now." },
  { key: "needs-josh", label: "Needs Josh", empty: "No decisions or repair items need Josh right now." },
  { key: "complete", label: "Recent", empty: "No recent completion has been reported yet." },
  { key: "planned", label: "Next Up", empty: "No upcoming planned work is loaded yet." },
];

function towerLaneItems(model: ControlTowerModel, lane: TowerLane) {
  if (lane === "active") return model.active;
  if (lane === "needs-josh") return model.needsJosh;
  if (lane === "complete") return model.complete;
  return model.planned;
}

function towerLaneCount(model: ControlTowerModel, lane: TowerLane) {
  return towerLaneItems(model, lane).length;
}

function towerLaneLabel(lane: TowerLane) {
  return TOWER_LANES.find((item) => item.key === lane)?.label || lane;
}

function activityLedgerRows(model: ControlTowerModel) {
  const meaningfulComplete = model.complete.filter((row) => !activityIsRoutineSystem(row));
  const rows = [
    ...activityRankedRows(model.needsJosh).slice(0, 3),
    ...activityRankedRows(model.active).slice(0, 4),
    ...activityRankedRows(model.planned).slice(0, 8),
    ...activityRankedRows(meaningfulComplete).slice(0, 5),
  ];
  const seen = new Set<string>();
  return rows.filter((row) => {
    const key = `${row.lane}-${row.agent}-${missionText(row.title).toLowerCase()}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 8);
}

function activityText(row: TowerActivity) {
  return missionText(`${row.title} ${row.detail} ${row.meta}`).toLowerCase();
}

function activityIsRoutineFocus(row?: TowerActivity) {
  if (!row) return false;
  const text = activityText(row);
  return activityIsRoutineSystem(row) || textIsRoutineActivity(text) || [
    "agent context sync",
    "context sync",
    "live kiosk health",
    "status check",
    "heartbeat",
    "control tower refresh",
    "scheduled jobs healthy",
    "rows, sidecars, live path",
    "agent state current",
  ].some((term) => text.includes(term));
}

function activityIsPriorityFocus(row: TowerActivity) {
  const text = activityText(row);
  return /gmail|inbox|sorare|fantasy|waiver|lineup|daily mission|breaking news|x watchlist|wallet|crypto|control tower|ui|ux|handoff|deploy|build|verify|approval|josh needs/.test(text);
}

function activityIsUserFacingFocus(row: TowerActivity) {
  const text = activityText(row);
  return activityIsPriorityFocus(row) || /telegram|josh|user|request|asked|approved|handoff|screen|kiosk|dashboard|control tower/.test(text);
}

function activityFocusRank(row: TowerActivity) {
  const ageMs = Date.now() - row.sortAt;
  const isFresh = Number.isFinite(ageMs) && ageMs <= ACTIVE_FOCUS_FRESH_MINUTES * 60_000;
  const isVeryStale = Number.isFinite(ageMs) && ageMs > 24 * 60 * 60_000;
  const laneBase = row.lane === "active"
    ? 980
    : row.lane === "needs-josh"
      ? (isFresh ? 900 : 520)
      : row.lane === "planned" ? 360 : 140;
  const priorityBoost = activityIsPriorityFocus(row) ? 130 : 0;
  const userFacingBoost = activityIsUserFacingFocus(row) ? 90 : 0;
  const agentBoost = row.agent === "joshex" || row.agent === "jaimes" || row.agent === "josh" ? 24 : 12;
  const freshActiveBoost = row.lane === "active" && isFresh ? 360 : 0;
  const stalePenalty = isVeryStale ? 720 : Number.isFinite(ageMs) && ageMs > 6 * 60 * 60_000 ? 340 : 0;
  const routinePenalty = activityIsRoutineFocus(row) ? 260 : 0;
  const recencyBoost = Math.max(0, Math.min(80, Math.floor((Date.now() - row.sortAt) / -60_000)));
  return laneBase + priorityBoost + userFacingBoost + agentBoost + freshActiveBoost + recencyBoost - routinePenalty - stalePenalty;
}

function activityRankedRows(rows: TowerActivity[]) {
  return [...rows].sort((a, b) => activityFocusRank(b) - activityFocusRank(a) || b.sortAt - a.sortAt);
}

function activityIsRoutineSystem(row: TowerActivity) {
  if (row.lane !== "complete") return false;
  const text = activityText(row);
  return [
    "live kiosk health",
    "kiosk health",
    "status check",
    "heartbeat",
    "codexbar",
    "session spend",
    "scheduled jobs healthy",
    "brain feed idle",
    "rows, sidecars, live path",
    "agent context sync",
    "live agent cards",
    "j.a.i.n medic",
  ].some((term) => text.includes(term));
}

function activitySystemQuietCount(model: ControlTowerModel) {
  return model.counts.systemQuiet || model.complete.filter(activityIsRoutineSystem).length;
}

function activityFocusRows(model: ControlTowerModel) {
  // The Live Work Board is the real-time source of truth: fresh active agent
  // status must beat stale scheduled/needs-Josh rows such as old Daily Missions.
  const rawActive = [...model.needsJosh, ...model.active].filter(row => {
    const text = ((row.title || "") + " " + (row.detail || "")).toLowerCase();
    const ageMs = Date.now() - row.sortAt;
    const isStaleFocus = Number.isFinite(ageMs) && ageMs > 6 * 60 * 60_000;
    // Filter out unhelpful stuck routine jobs.
    if (text.includes("gmail morninginbox triage")) return false;
    if (text.includes("routine triage")) return false;
    // Old attention rows can stay in Priority Queue, but they should never own
    // the live hero while a fresh agent is actively broadcasting work.
    if (row.lane === "needs-josh" && isStaleFocus) return false;

    // Filter out routine health checks and heartbeats so they do not hijack the hero.
    if (text.includes("heartbeat")) return false;
    if (text.includes("kiosk health")) return false;
    if (text.includes("status check")) return false;
    if (text.includes("rows, sidecars, live path")) return false;
    if (text.includes("agent context sync")) return false;

    return true;
  });

  const ordered = activityRankedRows(rawActive);
  const seen = new Set<string>();
  return ordered.filter((row) => {
    const key = `${row.lane}-${row.agent}-${missionText(row.title).toLowerCase()}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 3);
}

// Multi-agent concurrency: how many DISTINCT agents are genuinely active at once.
// Used to switch the Live Work Board hero from single-focus to a co-equal grid.
function concurrentActiveAgents(model: ControlTowerModel): AgentId[] {
  const seen = new Set<AgentId>();
  const order: AgentId[] = [];
  [...model.needsJosh, ...model.active].forEach((row) => {
    if (row.lane !== "active" && row.lane !== "needs-josh") return;
    if (activityIsRoutineFocus(row)) return;
    if (!row.time || isFreshActiveTimestamp(row.time)) {
      if (!seen.has(row.agent)) {
        seen.add(row.agent);
        order.push(row.agent);
      }
    }
  });
  return order;
}

// One representative focus row per active agent, ranked — for the concurrent hero grid.
function perAgentFocusRows(model: ControlTowerModel, agents: AgentId[]): TowerActivity[] {
  const rawActive = activityRankedRows(
    [...model.needsJosh, ...model.active].filter((row) => !activityIsRoutineFocus(row)),
  );
  const out: TowerActivity[] = [];
  agents.forEach((agent) => {
    const row = rawActive.find((r) => r.agent === agent);
    if (row) out.push(row);
  });
  return out.slice(0, 3);
}

// Contention: two or more agents whose current work targets the same thing.
function focusContentionKey(row: TowerActivity): string {
  return missionText(row.title).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().split(" ").slice(0, 4).join(" ");
}
function detectFocusContention(rows: TowerActivity[]): Set<string> {
  const byKey = new Map<string, Set<AgentId>>();
  rows.forEach((row) => {
    const key = focusContentionKey(row);
    if (!key) return;
    if (!byKey.has(key)) byKey.set(key, new Set());
    byKey.get(key)!.add(row.agent);
  });
  const contended = new Set<string>();
  byKey.forEach((agents, key) => {
    if (agents.size >= 2) contended.add(key);
  });
  return contended;
}

function focusEyebrow(row?: TowerActivity) {
  if (!row) return "Live status";
  if (row.lane === "needs-josh") return "Needs Josh";
  if (row.lane === "active") return "Happening now";
  if (row.lane === "planned") return "Next focus";
  return "Latest complete";
}

function towerActivityKey(seed: string, agent: AgentId, title: string) {
  return `${seed}-${agent}-${missionText(title).toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
}

function pushUniqueActivity(rows: TowerActivity[], row: TowerActivity, seen: Set<string>) {
  const key = `${row.lane}-${row.agent}-${missionText(row.title).toLowerCase()}`;
  if (seen.has(key)) return;
  seen.add(key);
  rows.push(row);
}

function statusIsClear(status?: string | null) {
  return /ready|ok|done|complete|idle|clear/i.test(String(status || ""));
}

function statusIsProblem(status?: string | null) {
  return /blocked|error|failed|missed|waiting|attention|needs/i.test(String(status || ""));
}

function towerActivityFromWork(item: WorkItem): TowerActivity {
  const lane: TowerLane = item.state === "blocked" || item.state === "waiting" ? "needs-josh" : "active";
  const tone: TowerTone = item.state === "blocked" ? "risk" : item.state === "waiting" ? "watch" : "active";
  return {
    id: item.id,
    lane,
    tone,
    agent: item.agent_id,
    title: headlineTitle(item.title, 54),
    detail: readoutSummary(item.detail, "Working through the current step.", 104),
    meta: item.source === "job" ? "scheduled job" : item.source === "approval" ? "approval flow" : "Brain Feed",
    time: item.updated_at,
    sortAt: timeValue(item.updated_at),
  };
}

function towerActivityFromJob(job: JobRow, lane: TowerLane, tone: TowerTone): TowerActivity {
  const run = jobRunCells(job);
  const next = nextRunTime(job);
  const last = timeValue(job.lastRun || job.completed_at || job.updated_at);
  const sortAt = lane === "planned" ? next || last || timeValue(job.updated_at) : last || next || timeValue(job.updated_at);
  const bullets = expectedNextBullets(job, job.agent_id);
  const checks = bullets.find((item) => item.label.toLowerCase() === "checks")?.text;
  const output = bullets.find((item) => item.label.toLowerCase() === "output")?.text;
  const detail = lane === "planned"
    ? readoutSummary(checks || job.detail || job.tool, "Checks the next scheduled task.", 104)
    : lane === "complete"
      ? readoutSummary(output || job.detail || job.tool, "Reported completion status.", 104)
      : readoutSummary(job.detail || job.tool, "Agent job status.", 104);
  const metaParts = [
    compactCategoryLabel(jobCategory(job)),
    run.next ? `next ${run.next}` : "",
    run.last ? `last ${run.last}` : "",
  ].filter(Boolean);
  return {
    id: job.id || towerActivityKey("job", job.agent_id, job.title),
    lane,
    tone,
    agent: job.agent_id,
    title: compactJobTitle(job),
    detail,
    meta: metaParts.join(" · ") || (job.sourceLabel || "scheduled job"),
    time: job.updated_at || job.lastRun || job.nextRun,
    sortAt,
  };
}

function buildControlTowerModel(state: MissionControlState, statuses: Map<AgentId, AgentStatus>, nowMs = Date.now()): ControlTowerModel {
  const trackedJobs = operatorTrackedJobs(state.jobs);
  const seen = new Set<string>();
  const active: TowerActivity[] = [];
  const needsJosh: TowerActivity[] = [];
  const complete: TowerActivity[] = [];
  const planned: TowerActivity[] = [];

  buildWorkItems(state)
    .filter((item) => ["working", "waiting", "blocked"].includes(item.state))
    .forEach((item) => {
      const row = towerActivityFromWork(item);
      pushUniqueActivity(row.lane === "needs-josh" ? needsJosh : active, row, seen);
    });

  state.approvals
    .filter((approval) => approval.status === "pending")
    .slice(0, 4)
    .forEach((approval) => pushUniqueActivity(needsJosh, {
      id: `approval-${approval.id}`,
      lane: "needs-josh",
      tone: String(approval.risk_tier || "").toLowerCase().includes("high") ? "risk" : "watch",
      agent: approval.requested_by || approval.agent_id || "joshex",
      title: headlineTitle(approval.title, 54),
      detail: readoutSummary(approval.detail, "Approval is waiting for Josh.", 104),
      meta: "approval required",
      time: approval.created_at,
      sortAt: timeValue(approval.created_at),
    }, seen));

  dataQualityIssues(state)
    .filter((issue) => issue.tone !== "clear")
    .slice(0, 3)
    .forEach((issue) => pushUniqueActivity(needsJosh, {
      id: `data-${issue.id}`,
      lane: "needs-josh",
      tone: issue.tone === "risk" ? "risk" : "watch",
      agent: issue.target === "signal-feed" ? "jain" : issue.target === "today-jobs" ? "josh" : "joshex",
      title: headlineTitle(issue.title, 54),
      detail: readoutSummary(issue.detail || issue.why, "Dashboard needs attention.", 104),
      meta: issue.label,
      time: state.lastUpdated,
      sortAt: timeValue(state.lastUpdated),
    }, seen));

  trackedJobs
    .filter((job) => jobNeedsAttention(job, trackedJobs))
    .slice(0, 5)
    .forEach((job) => pushUniqueActivity(needsJosh, towerActivityFromJob(job, "needs-josh", statusIsProblem(job.runStatus || job.status) ? "risk" : "watch"), seen));

  trackedJobs
    .filter((job) => jobIsFreshActive(job) || jobWorkState(job, trackedJobs) === "working")
    .slice(0, 5)
    .forEach((job) => pushUniqueActivity(active, towerActivityFromJob(job, "active", jobIsRoutineActivity(job) ? "planned" : "active"), seen));

  state.events
    .filter((event) => /done|complete|ready|ok|info/i.test(event.status || event.event_type || ""))
    .slice(0, 6)
    .forEach((event) => pushUniqueActivity(complete, {
      id: event.id || towerActivityKey("event", event.agent_id, event.title),
      lane: "complete",
      tone: statusIsProblem(event.status) ? "watch" : "done",
      agent: event.agent_id || "joshex",
      title: headlineTitle(event.title, 54),
      detail: readoutSummary(event.detail || event.tool, "Recent ecosystem update.", 104),
      meta: event.tool || "Brain Feed",
      time: event.created_at,
      sortAt: timeValue(event.created_at),
    }, seen));

  trackedJobs
    .filter((job) => /done|complete|ok|ready/i.test(`${job.status} ${job.runStatus}`))
    .sort((a, b) => timeValue(b.lastRun || b.completed_at || b.updated_at) - timeValue(a.lastRun || a.completed_at || a.updated_at))
    .slice(0, 7)
    .forEach((job) => pushUniqueActivity(complete, towerActivityFromJob(job, "complete", "done"), seen));

  const calendarBlocks = buildCalendarJobBlocks(trackedJobs)
    .filter((block) => block.startsAt.getTime() >= nowMs - 15 * 60_000)
    .sort((a, b) => a.startsAt.getTime() - b.startsAt.getTime());
  calendarBlocks.slice(0, 8).forEach((block) => {
    const row = towerActivityFromJob(block.job, "planned", block.startsAt.getTime() <= nowMs ? "active" : "planned");
    row.id = `planned-${block.id}`;
    row.title = block.synthetic && block.count && block.count > 1 ? `${block.count} ${block.title}` : block.title;
    row.detail = readoutSummary(block.detail, row.detail, 104);
    row.time = block.startsAt.toISOString();
    row.sortAt = block.startsAt.getTime();
    row.meta = `${calendarBlockTimeLabel(block.startsAt)} · ${AGENTS[block.agent]?.label || block.agent}`;
    pushUniqueActivity(planned, row, seen);
  });

  // Always let fresh explicit agent Brain Feed rows surface as active work.
  // Previously this only ran when no active jobs existed; routine active jobs
  // could fill `active`, get filtered from the hero, and leave the board saying
  // "No active work right now" while JAIMES/JOSHeX/J.A.I.N were working.
  state.statuses
    .filter((status) => agentIsWorking(status) && isFreshActiveTimestamp(status.updated_at))
    .forEach((status) => pushUniqueActivity(active, {
      id: `status-${status.agent_id}-${status.updated_at}`,
      lane: "active",
      tone: "active",
      agent: status.agent_id,
      title: headlineTitle(status.objective || AGENTS[status.agent_id].role, 54),
      detail: readoutSummary(status.detail || status.current_tool, "Agent is reporting active work.", 104),
      meta: "agent status",
      time: status.updated_at,
      sortAt: timeValue(status.updated_at),
    }, seen));

  const visibleStatuses = TOWER_AGENT_ORDER.map((agent) => statuses.get(agent)).filter(Boolean) as AgentStatus[];
  const agentsReady = visibleStatuses.length;
  const systemQuiet = complete.filter(activityIsRoutineSystem).length;
  const meaningfulComplete = Math.max(0, complete.length - systemQuiet);
  return {
    active: active.sort((a, b) => b.sortAt - a.sortAt).slice(0, 6),
    needsJosh: needsJosh.sort((a, b) => b.sortAt - a.sortAt).slice(0, 6),
    complete: complete.sort((a, b) => b.sortAt - a.sortAt).slice(0, 8),
    planned: planned.sort((a, b) => a.sortAt - b.sortAt).slice(0, 8),
    counts: {
      agentsReady,
      agentsTotal: TOWER_AGENT_ORDER.length,
      active: active.length,
      needsJosh: needsJosh.length,
      complete: complete.length,
      planned: planned.length,
      trackedJobs: trackedJobs.length,
      systemQuiet,
      meaningfulComplete,
    },
  };
}

function ControlTower({
  state,
  statuses,
  onNavigate,
  liveCues,
  loading,
  onCryptoRefresh,
}: {
  state: MissionControlState;
  statuses: Map<AgentId, AgentStatus>;
  onNavigate: (target: AttentionTarget) => void;
  liveCues: LiveCueState;
  loading: boolean;
  onCryptoRefresh: () => void;
}) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  const model = useMemo(() => buildControlTowerModel(state, statuses, nowMs), [state, statuses, nowMs]);
  return (
    <section className="control-tower-grid" aria-label="Josh 2.0 Control Tower">
      {/* Column 1 (Left): Live Work Board + Agent Flight Deck */}
      <section id="brain-feed" className={`tower-column tower-left-column${sectionCueClass("brain", liveCues)}`} aria-label="Ecosystem hero and flight deck">
        <SectionCue label={liveCues.focus === "brain" ? "focus" : "updated"} />
        <ActivityLedger model={model} />
        <AgentFlightDeck state={state} statuses={statuses} model={model} nowMs={nowMs} liveCues={liveCues} />
      </section>

      {/* Column 2 (Center): Priority Queue + Resource Stack */}
      <section className="tower-column tower-center-column" aria-label="Priority work and resources">
        <PriorityQueuePanel state={state} model={model} onNavigate={onNavigate} />
        <ResourceStack state={state} loading={loading} onCryptoRefresh={onCryptoRefresh} liveCues={liveCues} />
      </section>

      {/* Column 3 (Right): Scheduled Jobs / Daily Calendar */}
      <aside className="right-rail tower-jobs-rail">
        <JobsRail jobs={state.jobs} liveCues={liveCues} />
      </aside>
    </section>
  );
}

function TowerCommandStrip({ model, truth, state }: { model: ControlTowerModel; truth: ReturnType<typeof missionTruthSummary>; state: MissionControlState }) {
  const walletTotal = state.agenticCrypto?.summary?.totalEstimatedUsd;
  return (
    <header className="tower-command-strip">
      <div>
        <p>Agent Ecosystem Control Tower</p>
        <h2>Executive activity view</h2>
      </div>
      <article className={model.counts.needsJosh ? "is-risk" : "is-clear"}>
        <span>Needs Josh</span>
        <strong>{model.counts.needsJosh}</strong>
      </article>
      <article className={model.counts.active ? "is-active" : "is-clear"}>
        <span>Active</span>
        <strong>{model.counts.active}</strong>
      </article>
      <article>
        <span>Complete</span>
        <strong>{model.counts.complete}</strong>
      </article>
      <article>
        <span>Planned</span>
        <strong>{model.counts.planned}</strong>
      </article>
      <article>
        <span>Agents</span>
        <strong>{model.counts.agentsReady}/{model.counts.agentsTotal}</strong>
      </article>
      <article>
        <span>Source</span>
        <strong>Josh 2.0</strong>
      </article>
      <article className="is-resource">
        <span>Wallet</span>
        <strong>{fmtCurrencyExact(walletTotal)}</strong>
      </article>
      <article className={`is-${truth.tone}`}>
        <span>Truth</span>
        <strong>{truth.label}</strong>
      </article>
    </header>
  );
}

function AgentFlightDeck({
  state,
  statuses,
  model,
  nowMs,
  liveCues,
}: {
  state: MissionControlState;
  statuses: Map<AgentId, AgentStatus>;
  model: ControlTowerModel;
  nowMs: number;
  liveCues: LiveCueState;
}) {
  return (
    <section className="tower-agent-deck" aria-label="Agent flight deck">
      <header>
        <div>
          <p>Agent Flight Deck</p>
          <h3>JOSHeX · Josh 2.0 · JAIMES · J.A.I.N</h3>
        </div>
        <span>{model.counts.agentsReady}/{model.counts.agentsTotal} visible</span>
      </header>
      <div className="tower-agent-list">
        {TOWER_AGENT_ORDER.map((agent) => {
          const status = statuses.get(agent) || offlineStatus(agent);
          const idleContext = buildAgentIdleContext(agent, state, nowMs);
          return <TowerAgentRow key={agent} agent={agent} status={status} idleContext={idleContext} changed={Boolean(liveCues.rows[cueRowKey("agent", agent)])} />;
        })}
      </div>
    </section>
  );
}

function TowerAgentRow({ agent, status, idleContext, changed }: { agent: AgentId; status: AgentStatus; idleContext: AgentIdleContext; changed?: boolean }) {
  const visualState = agentNeedsFocus(status)
    ? "blocked"
    : agentIsWorking(status) && isFreshActiveTimestamp(status.updated_at)
      ? "working"
      : statusIsClear(status.status)
        ? "ready"
        : "waiting";
  const freshness = agentSla(status, idleContext);
  const current = agentIsWorking(status) && isFreshActiveTimestamp(status.updated_at)
    ? headlineTitle(status.objective || status.current_tool || AGENTS[agent].role, 44)
    : idleContext.nextTitle && !/awaiting instruction/i.test(idleContext.nextTitle)
      ? `Up next: ${headlineTitle(idleContext.nextTitle, 42)}`
      : "Awaiting instruction";
  const complete = readoutSummary(idleContext.complete, "No completed task reported yet.", 72);
  const next = idleContext.countdown
    ? `${countdownShortText(idleContext.countdown)} · ${idleContext.nextTitle}`
    : idleContext.nextTitle;
  return (
    <article className={`tower-agent-row ${agentClass(agent)} is-${visualState}${changedRowClass(changed)}`}>
      <span className="row-change-dot" aria-hidden="true" />
      <div className="tower-agent-id">
        <span className={`dot ${agentHeaderDotClass(visualState as AgentVisualState, false, visualState === "working")}`} />
        <div>
          <strong>{AGENTS[agent].label}</strong>
          <em>{AGENTS[agent].role}</em>
        </div>
      </div>
      <div className="tower-agent-now">
        <span>{visualState === "working" ? "Now" : "Focus"}</span>
        <strong title={missionText(status.objective)}>{current}</strong>
      </div>
      <div className="tower-agent-readouts">
        <p title={idleContext.complete}><b>Complete</b>{complete}</p>
        <p title={next}><b>Next</b>{compactText(next, 76)}</p>
      </div>
      <div className={`tower-agent-fresh is-${freshness.tone}`}>
        <strong>{freshness.label}</strong>
        <span>{freshness.detail}</span>
      </div>
    </article>
  );
}

function ActivityLedger({ model }: { model: ControlTowerModel }) {
  const rows = activityLedgerRows(model);
  const systemQuiet = activitySystemQuietCount(model);
  const focusRows = activityFocusRows(model);
  const primaryFocus = focusRows[0];
  // Multi-agent: when 2+ distinct agents are active at once, show a co-equal grid
  // instead of a single hero so concurrent workflows are all visible.
  const activeAgents = concurrentActiveAgents(model);
  const concurrent = activeAgents.length >= 2;
  const concurrentRows = concurrent ? perAgentFocusRows(model, activeAgents) : [];
  const contended = concurrent ? detectFocusContention(concurrentRows) : new Set<string>();
  return (
    <section className="tower-module activity-ledger" aria-label="Unified activity ledger">
      <header>
        <div>
          <p>Unified Activity Ledger</p>
          <h2>Live Work Board</h2>
        </div>
        <span>{model.counts.trackedJobs} tracked jobs</span>
      </header>
      {concurrent ? (
        <section
          className={`ledger-live-focus is-concurrent cols-${Math.min(concurrentRows.length, 3)}`}
          aria-label={`Live ecosystem focus — ${concurrentRows.length} agents working`}
        >
          {concurrentRows.map((row) => {
            const inContention = contended.has(focusContentionKey(row));
            return (
              <article
                key={row.id}
                className={`ledger-focus-primary ledger-focus-concurrent is-${row.tone} ${agentClass(row.agent)} ${activityIsRoutineFocus(row) ? "is-routine-focus" : "is-priority-focus"}${inContention ? " is-contended" : ""}`}
              >
                <span className="ledger-now-label">
                  {AGENTS[row.agent]?.label || row.agent}
                  {inContention ? <em className="focus-contention-badge" title="Two or more agents are working the same target">shared target</em> : null}
                </span>
                <h3 className="ledger-now-title" title={missionText(row.title)}>{row.title}</h3>
                <p className="ledger-now-detail" title={missionText(row.detail)}>{row.detail}</p>
                <footer>
                  <strong>{focusEyebrow(row)}</strong>
                  <em>{row.time ? ageLabel(row.time) : "live"}</em>
                </footer>
              </article>
            );
          })}
        </section>
      ) : (
      <section className="ledger-live-focus" aria-label="Live ecosystem focus">
        <article className={`ledger-focus-primary ${primaryFocus ? `is-${primaryFocus.tone} ${agentClass(primaryFocus.agent)} ${activityIsRoutineFocus(primaryFocus) ? "is-routine-focus" : "is-priority-focus"}` : "is-done"}`}>
          <span className="ledger-now-label">{focusEyebrow(primaryFocus)}</span>
          <h3 className="ledger-now-title" title={missionText(primaryFocus?.title)}>
            {primaryFocus ? primaryFocus.title : "No active work right now"}
          </h3>
          <p className="ledger-now-detail" title={missionText(primaryFocus?.detail)}>
            {primaryFocus ? primaryFocus.detail : "Agents are standing by; next scheduled work will surface here."}
          </p>
          <footer>
            <strong>{primaryFocus ? AGENTS[primaryFocus.agent]?.label || primaryFocus.agent : "Agent ecosystem"}</strong>
            <em>{primaryFocus?.time ? ageLabel(primaryFocus.time) : "live"}</em>
          </footer>
        </article>
        <div className="ledger-focus-secondary">
          {focusRows.slice(1, 3).map((row) => (
            <article key={row.id} className={`ledger-focus-mini is-${row.tone} ${agentClass(row.agent)} ${activityIsRoutineFocus(row) ? "is-routine-focus" : "is-priority-focus"}`}>
              <span>{focusEyebrow(row)}</span>
              <strong title={missionText(row.title)}>{row.title}</strong>
              <em>{AGENTS[row.agent]?.label || row.agent}</em>
            </article>
          ))}
        </div>
      </section>
      )}
      <div className="ledger-summary-strip">
        {TOWER_LANES.map((lane) => (
          <article key={lane.key} className={`ledger-summary-card lane-${lane.key}`}>
            <span>{lane.label}</span>
            <strong>{towerLaneCount(model, lane.key)}</strong>
          </article>
        ))}
      </div>
      <div className="ledger-system-summary" aria-label="System status summary">
        <strong>System OK</strong>
        <span>{systemQuiet ? `${systemQuiet} routine confirmations collapsed` : "Routine checks quiet"}</span>
      </div>
      <div className="ledger-row-list is-flat" role="list">
        {rows.length ? rows.map((row) => (
          <ActivityLedgerRow key={row.id} row={row} />
        )) : <p className="ledger-empty">No live activity rows are loaded yet.</p>}
      </div>
    </section>
  );
}

function ActivityLedgerRow({ row }: { row: TowerActivity }) {
  return (
    <article className={`ledger-row is-${row.tone} ${agentClass(row.agent)}`}>
      <span className={`ledger-lane-pill lane-${row.lane}`}>{towerLaneLabel(row.lane)}</span>
      <span className="ledger-agent-dot" aria-hidden="true" />
      <div>
        <strong title={missionText(row.title)}>{row.title}</strong>
        <p title={missionText(row.detail)}>{row.detail}</p>
      </div>
      <footer>
        <span>{AGENTS[row.agent]?.label || row.agent}</span>
        <em>{row.time ? ageLabel(row.time) : row.meta}</em>
      </footer>
    </article>
  );
}

function PriorityQueuePanel({ state, model, onNavigate }: { state: MissionControlState; model: ControlTowerModel; onNavigate: (target: AttentionTarget) => void }) {
  const trackedJobs = operatorTrackedJobs(state.jobs);
  const { byPriority } = priorityJobGroups(trackedJobs);
  const priorityRows = [
    { label: "Gmail", key: "gmail" as PriorityJobKey, agent: "joshex" as AgentId },
    { label: "Sorare", key: "sorare" as PriorityJobKey, agent: "jaimes" as AgentId },
    { label: "Fantasy", key: "fantasy" as PriorityJobKey, agent: "jain" as AgentId },
  ].map((row) => {
    const items = byPriority.get(row.key) || [];
    const sample = representativeJob(items, trackedJobs);
    const next = sample ? jobRunCells(sample).next || sample.nextRun : "";
    const status = compactJobStatus(items);
    return { ...row, items, sample, next, status };
  });
  return (
    <section className="tower-priority-queue" aria-label="Priority queue">
      <header>
        <div>
          <p>Priority Queue</p>
          <h3>What matters most</h3>
        </div>
        <button type="button" onClick={() => onNavigate("today-jobs")}>Timeline</button>
      </header>
      {model.needsJosh.length ? (
        <div className="tower-decision-band">
          <strong>{model.needsJosh.length} needs Josh</strong>
          <span>{model.needsJosh[0]?.title}</span>
        </div>
      ) : (
        <div className="tower-decision-band is-clear">
          <strong>No blocking decision</strong>
          <span>Only new alerts should interrupt Josh.</span>
        </div>
      )}
      <div className="priority-row-list">
        {priorityRows.map((row) => (
          <article key={row.key} className={`priority-control-row ${agentClass(row.agent)}`}>
            <span>{row.label}</span>
            <strong>{row.status}</strong>
            <p title={row.sample?.title || ""}>{row.sample ? compactJobTitle(row.sample) : "No tracked job loaded"}</p>
            <em>{row.next ? `next ${row.next}` : `${row.items.length} tracked`}</em>
          </article>
        ))}
      </div>
    </section>
  );
}

function ResourceStack({ state, loading, onCryptoRefresh, liveCues }: { state: MissionControlState; loading: boolean; onCryptoRefresh: () => void; liveCues: LiveCueState }) {
  const wallet = state.agenticCrypto;
  const walletTotal = wallet?.summary?.totalEstimatedUsd;
  const liquid = wallet?.summary?.liquidEstimatedUsd;
  const tokenCount = wallet?.tokens?.length || 0;
  const walletMode = String(wallet?.walletMode || wallet?.refreshMode || "").toLowerCase();
  const walletIsPlaceholder = walletMode.includes("placeholder") || walletMode.includes("not-connected") || (tokenCount === 0 && (walletTotal || 0) === 0 && (wallet?.errors || []).length > 0);
  const walletHeadline = walletIsPlaceholder ? "Read-only" : fmtCurrencyExact(walletTotal);
  const walletDetail = walletIsPlaceholder
    ? "No connected balance · proposals only"
    : `${tokenCount} tokens · ${fmtCurrencyExact(liquid)} liquid`;
  const subscriptionFee = state.modelUsage?.subscription?.monthlyFee;
  const meteredMonthly = state.modelUsage?.metered?.monthly ?? 0;
  const meteredDaily = state.modelUsage?.metered?.daily ?? state.modelUsage?.aggregate?.daily ?? state.modelUsage?.daily;
  const usageEquivalentMonthly = state.modelUsage?.usageEquivalent?.monthly ?? state.modelUsage?.subscription?.usageEquivalentMonthly;
  const modelHeadline = typeof subscriptionFee === "number"
    ? `${fmtCurrencyExact(subscriptionFee)} sub + ${fmtCurrencyExact(meteredMonthly)}`
    : fmtCurrencyExact(state.modelUsage?.aggregate?.monthly ?? state.modelUsage?.monthly);
  const modelDetail = typeof subscriptionFee === "number"
    ? `OpenAI usage equiv ${fmtCurrencyExact(usageEquivalentMonthly)} · metered today ${fmtCurrencyExact(meteredDaily)}`
    : `Today · xAI ${fmtCurrencyExact(state.modelUsage?.xai?.daily)} · GPT-5.5 ready`;
  const runtimeOk = state.runtimeLayout?.ok !== false;
  const visibleAgents = new Set(state.statuses.map((row) => row.agent_id)).size;
  const freshAgents = state.statuses.filter((row) => {
    const value = String(row.status || "").toLowerCase();
    return !["blocked", "error", "offline"].includes(value) && ageMinutes(row.updated_at) <= 120;
  }).length;
  const trackedAgents = Math.max(4, state.statuses.length);
  const missingAgents = Math.max(0, trackedAgents - visibleAgents);
  const visibilityDetail = missingAgents
    ? `${freshAgents} fresh · ${missingAgents} missing source${missingAgents === 1 ? "" : "s"}`
    : `${freshAgents} fresh · ${sourceTruthLabel(state.source)}`;
  return (
    <section className="tower-resource-stack" aria-label="Resources and live sources">
      <header>
        <div>
          <p>Resources</p>
          <h3>Wallet · Models · Display · Visibility</h3>
        </div>
        <button type="button" onClick={onCryptoRefresh} disabled={loading} title="Refresh read-only wallet inventory">
          <RefreshCw size={12} className={loading ? "spin" : ""} /> Wallet
        </button>
      </header>
      <div className="resource-card-grid">
        <article className={`resource-card is-${walletIsPlaceholder ? "watch" : "clear"}${changedRowClass(Boolean(liveCues.rows[cueRowKey("crypto", "balance")]))}`}>
          <span className="row-change-dot" aria-hidden="true" />
          <b>Agentic wallet</b>
          <strong>{walletHeadline}</strong>
          <p>{walletDetail}</p>
        </article>
        <article className="resource-card is-clear">
          <b>Model usage</b>
          <strong>{modelHeadline}</strong>
          <p>{modelDetail}</p>
        </article>
        <article className={`resource-card is-${runtimeOk ? "clear" : "risk"}`}>
          <b>Display fit</b>
          <strong>{runtimeOk ? "Ready" : "Review"}</strong>
          <p>{runtimeOk ? "Kiosk layout measured" : (state.runtimeLayout?.issues || []).slice(0, 1).join(", ")}</p>
        </article>
        <article className={`resource-card is-${visibleAgents >= trackedAgents ? "clear" : "watch"}`}>
          <b>Visibility</b>
          <strong>{visibleAgents}/{trackedAgents} visible</strong>
          <p>{visibilityDetail}</p>
        </article>
      </div>
    </section>
  );
}

function App() {
  const [state, setState] = useState<MissionControlState>(EMPTY_STATE);
  const [loading, setLoading] = useState(true);
  const [liveMode, setLiveMode] = useState<"connected" | "polling">("polling");
  const [quietMode, setQuietMode] = useState(true);
  const liveCues = useLiveCues(state);

  const refresh = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    try {
      const next = await loadMissionControl();
      setState(next);
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  const refreshAgenticCrypto = useCallback(async () => {
    setLoading(true);
    try {
      await fetch("/actions/agentic-crypto-refresh?mode=lightweight", { method: "POST", cache: "no-store" });
    } catch (error) {
      console.warn(error);
    } finally {
      await refresh(false);
      setLoading(false);
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

  const statusByAgent = useMemo(() => {
    return new Map(state.statuses.map((row) => [row.agent_id, row]));
  }, [state.statuses]);

  const decisionCount = state.approvals.filter((row) => row.status === "pending").length;
  const trackedJobs = operatorTrackedJobs(state.jobs);
  const jobsCount = trackedJobs.length;
  const needsFocusCount = missionFocusCount(state);
  const activeJobs = trackedJobs.filter((job) => jobWorkState(job, trackedJobs) === "working");
  const activeJobCount = activeJobs.length;
  const activeRoutineJobCount = activeJobs.filter(jobIsRoutineActivity).length;
  const activeFocusJobCount = Math.max(0, activeJobCount - activeRoutineJobCount);
  const activeAgentCount = state.statuses.filter((row) => agentIsWorking(row) && isFreshActiveTimestamp(row.updated_at)).length;
  const workingCount = activeJobCount + activeAgentCount;
  const priorityLiveWorkCount = activeAgentCount + activeFocusJobCount;
  const liveConnectionLabel = liveMode === "connected" ? priorityLiveWorkCount ? "Running" : activeRoutineJobCount ? "Live check" : "Live" : "Checking 10s";
  const liveActivityParts = [
    activeAgentCount ? `${activeAgentCount} agent${activeAgentCount === 1 ? "" : "s"} working` : "",
    activeFocusJobCount ? `${activeFocusJobCount} priority job${activeFocusJobCount === 1 ? "" : "s"}` : "",
    activeRoutineJobCount ? `${activeRoutineJobCount} live check${activeRoutineJobCount === 1 ? "" : "s"} running` : "",
  ].filter(Boolean);
  const liveActivityLabel = liveActivityParts.length ? liveActivityParts.join(" · ") : "all clear";
  const liveActivityTitle = workingCount
    ? priorityLiveWorkCount
      ? `Running now: ${liveActivityParts.join(", ")}. Live checks keep agent rows, sidecars, and Brain Feed aligned; it is not an alert.`
      : `Live checks running: ${liveActivityParts.join(", ")}. This keeps agent rows, sidecars, and Brain Feed aligned; it is not an alert.`
    : "No active agent or job is currently running";
  const nextVisibleBlock = nextVisibleCalendarBlock(trackedJobs, quietMode);
  const nextRunLabel = nextHeaderRunLabel(nextVisibleBlock);
  const nextRunValue = nextHeaderRunValue(nextVisibleBlock);
  const actionLabel = decisionCount ? "Needs Josh" : "Decisions";
  const needsJoshValue = decisionCount ? `${decisionCount} review${decisionCount === 1 ? "" : "s"}` : "Clear";
  const systemValue = needsFocusCount ? `${needsFocusCount} needs focus` : "All clear";
  const jobsValue = `${jobsCount} tracked`;
  const lastUpdate = dashboardFreshnessTimestamp(state);
  const liveFreshnessLabel = updatedFreshnessLabel(lastUpdate);
  const liveSummaryLabel = liveActivityLabel === "all clear" ? liveConnectionLabel.toLowerCase() : liveActivityLabel;
  const liveChipTitle = `${liveFreshnessLabel}. ${liveConnectionLabel}: ${liveActivityTitle}`;
  const navigateToPanel = useCallback((target: AttentionTarget) => {
    document.getElementById(target)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  return (
    <main className="app-shell hero-shell">
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
            <p>Control Tower for the agent ecosystem</p>
          </div>
        </div>
        <section className="status-ribbon header-status-ribbon" aria-label="Control Tower summary">
          <Metric icon={<UserRoundCheck size={18} />} label={actionLabel} value={needsJoshValue} tone={decisionCount ? "risk" : "clear"} />
          <Metric icon={<AlertTriangle size={18} />} label="System" value={systemValue} tone={needsFocusCount ? "watch" : "clear"} />
          <Metric icon={<ClipboardList size={18} />} label="Jobs" value={jobsValue} tone={workingCount ? "info" : "clear"} />
          <Metric icon={<Timer size={18} />} label={nextRunLabel} value={nextRunValue} tone="clear" wide />
        </section>
        <div className="mission-actions">
          <span className="source-chip" title={state.source || "Local live source"}><ShieldCheck size={15} />{sourceTruthLabel(state.source)}</span>
          <span className={`source-chip live-chip ${workingCount ? "is-working" : "is-idle"}`} title={liveChipTitle}>
            <Radio size={15} /> {liveFreshnessLabel} · {liveSummaryLabel}
          </span>
          <button
            type="button"
            className={quietMode ? "mode-button selected" : "mode-button"}
            onClick={() => setQuietMode((value) => !value)}
            aria-pressed={quietMode}
            title="Show only active work, warnings, missed jobs, and pending approvals"
          >
            <EyeOff size={15} /> {quietMode ? "Focus" : "All jobs"}
          </button>
          <button type="button" onClick={refresh} aria-label="Refresh">
            <RefreshCw size={16} className={loading ? "spin" : ""} />
          </button>
        </div>
      </header>

      <ControlTower
        state={state}
        statuses={statusByAgent}
        onNavigate={navigateToPanel}
        liveCues={liveCues}
        loading={loading}
        onCryptoRefresh={refreshAgenticCrypto}
      />
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
  const truth = missionTruthSummary(state);
  return (
    <section
      className="brain-hero is-flight-deck"
      aria-label="Brain Feed"
      style={{
        "--laser-speed": `${laserSpeed}s`,
        "--laser-opacity": laserOpacity,
      } as React.CSSProperties}
    >
      <div className="brain-hero-title">
        <div>
          <p>Live agent updates</p>
          <h2>Brain Feed</h2>
        </div>
        <BrainAttentionStrip state={state} quietMode={quietMode} onNavigate={onNavigate} />
        <div className="brain-hero-controls">
          <span className={`brain-truth-pill is-${truth.tone}`} title={truth.detail}>
            {truth.label} · {truth.short}
          </span>
          {!quietMode ? <span>{`${events.slice(0, 6).length} updates`}</span> : null}
          <button
            type="button"
            className={showDetails ? "selected" : ""}
            onClick={() => setShowDetails((value) => !value)}
            aria-pressed={showDetails}
            aria-label={showDetails ? "Hide Brain Feed details" : "Show Brain Feed details"}
          >
            {showDetails ? "Hide" : "Details"}
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
      </div>

      {showDetails ? (
        <BrainOperationsSummary state={state} workItems={workItems} quietMode={quietMode} onNavigate={onNavigate} liveCues={liveCues} />
      ) : null}
    </section>
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

function signalUsesPublicFallback(signal: SignalItem) {
  return String(signal.kind || signal.label || "").toLowerCase().includes("public_rss")
    || String(signal.label || "").toLowerCase().includes("live rss");
}

function signalCategoryText(signal: SignalItem, rawImpact?: string | null) {
  return missionText(`${signal.title || ""} ${signal.source || ""} ${signal.reason || ""} ${rawImpact || ""}`)
    .replace(/\s+/g, " ")
    .toLowerCase();
}

function signalCategoryKey(signal: SignalItem, rawImpact?: string | null) {
  if (signalUsesPublicFallback(signal)) return "fresh";
  const lower = signalCategoryText(signal, rawImpact);
  if (/(cisa|cve|vulnerability|exploit|nvd|kev)/.test(lower)) return "security";
  if (/(sanction|ofac|iran military oil|restricted party)/.test(lower)) return "sanctions";
  if (/(blue origin|new glenn|rocket|launch|space)/.test(lower)) return "space";
  if (/(anthropic|openai|microsoft|nvidia|blackwell|semiconductor|chip|chips|\bai\b|model)/.test(lower)) return "ai";
  if (/(bitcoin|ethereum|solana|stablecoin|crypto|virtual|vvv|token)/.test(lower)) return "crypto";
  if (/(tariff|trade policy|china|export control|import|supply chain)/.test(lower)) return "trade";
  if (/(treasury|irs|federal reserve|regulation|sovereign investor|state bank of viet nam|policy)/.test(lower)) return "policy";
  if (/(market|rate|rates|bank|valuation|funding|acquisition|merger|stock|equity)/.test(lower)) return "market";
  if (/(immediate|significant|material|critical)/.test(lower)) return "material";
  if (/(context|no new break|isolated|limited)/.test(lower)) return "context";
  return signalIsNewsletter(signal) ? "context" : "watch";
}

function signalImpactLabel(signal: SignalItem, rawImpact?: string | null) {
  switch (signalCategoryKey(signal, rawImpact)) {
    case "fresh": return "Fresh";
    case "security": return "Security";
    case "sanctions": return "Sanctions";
    case "space": return "Space";
    case "ai": return "AI";
    case "crypto": return "Crypto";
    case "trade": return "Trade";
    case "policy": return "Policy";
    case "market": return "Market";
    case "material": return "Material";
    case "context": return "Context";
    default: {
      const text = missionText(rawImpact || "").replace(/\s+/g, " ").trim();
      return text ? compactText(text.replace(/^(high|medium|med|low):\s*/i, ""), 34) : "Watch";
    }
  }
}

function signalImpactTone(signal: SignalItem, rawImpact?: string | null) {
  switch (signalCategoryKey(signal, rawImpact)) {
    case "fresh": return "tone-signal";
    case "security": return "tone-security";
    case "sanctions": return "tone-sanctions";
    case "space": return "tone-space";
    case "ai": return "tone-ai";
    case "crypto": return "tone-crypto";
    case "trade": return "tone-trade";
    case "policy": return "tone-policy";
    case "market": return "tone-market";
    case "material": return "tone-signal";
    case "context": return "tone-context";
    default: return "tone-watch";
  }
}

function signalRowClass(signal: SignalItem) {
  const score = Number(signal.score);
  const classes = [signalIsNewsletter(signal) ? "is-newsletter" : "is-strong"];
  if (Number.isFinite(score) && score < 8) classes.push("is-watch");
  if (freshnessClass(signal.time) === "is-stale") classes.push("freshness-stale");
  if (freshnessClass(signal.time) === "is-aging") classes.push("freshness-aging");
  return classes.join(" ");
}

function signalFreshnessLabel(signal: SignalItem, newsletter: boolean) {
  const age = ageLabel(signal.time);
  if (!newsletter) return freshnessClass(signal.time) === "is-fresh" ? "live" : age;
  if (age === "no update") return "scan pending";
  if (age === "just now") return "latest scan";
  return `scan ${age}`;
}

function signalStoryAgeLabel(signal: SignalItem, newsletter: boolean) {
  if (newsletter) return signalFreshnessLabel(signal, true);
  const age = ageLabel(signal.time);
  if (age === "no update") return "story pending";
  if (age === "just now") return "story now";
  return `story ${age}`;
}

function signalDisplayTitle(signal: SignalItem, newsletter: boolean) {
  const title = missionText(signal.title)
    .replace(/\$(\d+(?:\.\d+)?)\s+billion\b/gi, (_match, amount: string) => `$${amount}B`)
    .replace(/\$(\d+(?:\.\d+)?)\s+million\b/gi, (_match, amount: string) => `$${amount}M`)
    .replace(/^Joint Statement by the U\.S\. Department of the Treasury and the State Bank of Viet Nam\b.*$/i, "Treasury/Vietnam joint statement")
    .replace(/^Treasury,\s*IRS Issue Section 892 Proposed Regulations\b.*$/i, "Treasury/IRS sovereign-investor tax rules")
    .replace(/^U\.\s*S\.\s+imposes fresh sanctions on Iran military oil sales,\s*says Treasury\b.*$/i, "U.S. sanctions Iran military oil sales")
    .replace(/\bvaluation after raising\b/gi, "valuation after")
    .replace(/\s+/g, " ")
    .trim();
  if (!newsletter) return title;
  const match = title.match(/^(.+?\bwatch):\s*(.+)$/i);
  if (!match) return title;
  const topic = match[1].replace(/\s+watch$/i, "").trim().toLowerCase();
  const detail = match[2].trim().toLowerCase();
  if (!topic || !detail) return title;
  if (detail === topic || detail.split(/,\s*/).some((part) => part === topic)) {
    return match[1];
  }
  return title;
}

function signalDisplayReason(signal: SignalItem) {
  const raw = missionText(signal.reason);
  const newsletterMatch = raw.match(/^Newsletter cluster from\s+(\d+)\s+items?\s*,\s*confidence\s+(\d+)%/i);
  if (newsletterMatch) {
    const count = newsletterMatch[1];
    const confidence = newsletterMatch[2];
    return `Digest trend from ${count} newsletter source${count === "1" ? "" : "s"} · ${confidence}% confidence`;
  }
  const withoutLinks = raw.replace(/https?:\/\/\S+/gi, " ").replace(/\s+/g, " ").trim();
  const readable = withoutLinks || "Source-backed signal crossing the relevance filter.";
  return readable.replace(/\b(\d+)\s+item\(s\)/gi, (_match, count: string) => {
    return `${count} item${count === "1" ? "" : "s"}`;
  });
}

function signalSourceScanLabel(value?: string | null) {
  const label = checkedFreshnessLabel(value);
  if (label === "not checked") return "source not checked";
  return `source ${label}`;
}

function signalDedupeKey(signal: SignalItem, newsletter: boolean) {
  const title = signalDisplayTitle(signal, newsletter).toLowerCase();
  const money = [...title.matchAll(/\$\d+(?:\.\d+)?\s*[bmk]?/g)].map((match) => match[0].replace(/\s+/g, ""));
  const stopwords = new Set(["after", "latest", "raising", "valued", "valuation", "secures", "says", "said", "the", "and", "with", "from", "into"]);
  const words = title
    .replace(/[^a-z0-9$]+/g, " ")
    .split(/\s+/)
    .filter((word) => word && !stopwords.has(word));
  if (!newsletter && money.length >= 2 && words[0]) return `${words[0]}|${money.slice(0, 3).join("|")}`;
  return words.slice(0, 7).join(" ");
}

function signalRows(signals: SignalItem[], newsletter: boolean) {
  const seenDedupeKeys = new Set<string>();
  const rows: SignalItem[] = [];
  const sorted = signals
    .filter((signal) => signalIsNewsletter(signal) === newsletter)
    .sort((a, b) => {
      const rankDelta = (a.rank || 999) - (b.rank || 999);
      if (rankDelta) return rankDelta;
      return timeValue(b.time) - timeValue(a.time);
    });
  for (const signal of sorted) {
    const key = signalDedupeKey(signal, newsletter);
    if (key && seenDedupeKeys.has(key)) continue;
    seenDedupeKeys.add(key);
    rows.push(signal);
    if (rows.length >= 5) break;
  }
  return rows;
}

function cryptoFreshness(wallet?: AgenticCryptoWallet) {
  if (!wallet?.updatedAt) return { label: "Not loaded", status: "stale", tone: "watch" };
  const age = Date.now() - timeValue(wallet.updatedAt);
  if (String(wallet.status).toLowerCase() === "error") return { label: "Error", status: "error", tone: "risk" };
  if (age > 60 * 60 * 1000) return { label: "Stale", status: "stale", tone: "watch" };
  return { label: "Fresh", status: "fresh", tone: "clear" };
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
  liveCues,
}: {
  wallet?: AgenticCryptoWallet;
  loading: boolean;
  onRefresh: () => void;
  liveCues: LiveCueState;
}) {
  const freshness = cryptoFreshness(wallet);
  const summary = wallet?.summary || {};
  const tokens = [...(wallet?.tokens || [])].sort((a, b) => (b.valueUsd || 0) - (a.valueUsd || 0));
  const visibleTokens = tokens.slice(0, 5);
  const hiddenTokens = tokens.slice(5);
  const hiddenTokenValue = hiddenTokens.reduce((sum, token) => sum + (token.valueUsd || 0), 0);
  const balanceChanged = Boolean(liveCues.rows[cueRowKey("crypto", "balance")]);
  const hiddenChanged = Boolean(liveCues.rows[cueRowKey("crypto", "smaller-tokens")]);
  const liquidValue = summary.liquidEstimatedUsd;
  const errors = wallet?.errors || [];
  return (
    <section id="agentic-crypto" className={`agentic-crypto-panel is-${freshness.tone}${sectionCueClass("crypto", liveCues)}`} aria-label="Agentic Crypto wallet status">
      <SectionCue label="updated" />
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

      <section className={`crypto-balance-card${changedRowClass(balanceChanged)}`}>
        <span className="row-change-dot" aria-hidden="true" />
        <span>Full balance</span>
        <strong>{fmtCurrencyExact(summary.totalEstimatedUsd)}</strong>
        <div>
          <em>{tokens.length} token{tokens.length === 1 ? "" : "s"} listed</em>
          {typeof liquidValue === "number" ? <em>{fmtCurrencyExact(liquidValue)} liquid value</em> : null}
          <em>Updated {ageLabel(wallet?.updatedAt)}</em>
        </div>
      </section>

      <section className="crypto-wallet-section">
        <header><Coins size={13} /> Tokens</header>
        <div className="crypto-wallet-list is-tokens-only">
          {visibleTokens.map((token) => {
            const tokenId = `${token.chain}-${token.symbol}-${token.contractMasked || token.mintMasked || token.source || ""}`;
            const changed = Boolean(liveCues.rows[cueRowKey("crypto-token", tokenId)]);
            return (
              <article key={tokenId} className={changedRowClass(changed)}>
                <span className="row-change-dot" aria-hidden="true" />
                <span className="crypto-token-icon" aria-hidden="true">{String(token.symbol || "?").slice(0, 1)}</span>
                <div className="crypto-token-main">
                  <strong title={token.name || token.symbol}>{token.symbol}</strong>
                  <span>{chainLabel(token.chain)} · {amountLabel(token.amount, token.symbol)}</span>
                </div>
                <em className="crypto-token-value">{fmtCurrencyExact(token.valueUsd)}</em>
              </article>
            );
          })}
          {hiddenTokens.length ? (
            <article className={`crypto-token-summary${changedRowClass(hiddenChanged)}`}>
              <span className="row-change-dot" aria-hidden="true" />
              <span className="crypto-token-icon is-more" aria-hidden="true">+</span>
              <div className="crypto-token-main">
                <strong>+{hiddenTokens.length} smaller token{hiddenTokens.length === 1 ? "" : "s"}</strong>
                <span>Included in total balance</span>
              </div>
              <em className="crypto-token-value">{fmtCurrencyExact(hiddenTokenValue)}</em>
            </article>
          ) : null}
          {!tokens.length ? <p>No token rows loaded yet.</p> : null}
        </div>
      </section>

      {errors.length ? (
        <footer className="crypto-errors" title="One or more read-only sources were unavailable during refresh.">
          {errors.length} refresh note{errors.length === 1 ? "" : "s"} · wallet view remains read-only.
        </footer>
      ) : (
        <footer className="crypto-errors is-clear">Read-only. Actions require approval.</footer>
      )}
    </section>
  );
}

function SignalFeed({ state, quietMode, liveCues }: { state: MissionControlState; quietMode: boolean; liveCues: LiveCueState }) {
  const freshness = signalFreshnessSummary(state);
  const topFive = signalRows(state.signals, false);
  const lastFive = signalRows(state.signals, true);
  const rowsShown = topFive.length + lastFive.length;
  const rowCapacity = 10;
  const watchSlots = Math.max(0, rowCapacity - Math.min(rowsShown, rowCapacity));
  const visibleStoryCount = Math.min(rowsShown, rowCapacity);
  const storyLabel = `${visibleStoryCount} ${visibleStoryCount === 1 ? "story" : "stories"}`;
  const visibleRowsLabel = watchSlots
    ? `${storyLabel} · ${watchSlots} slot${watchSlots === 1 ? "" : "s"} watching`
    : storyLabel;
  const signalUpdatedAt = state.signalHealth?.generatedAt || state.signals
    .map((signal) => signal.time)
    .filter(Boolean)
    .sort()
    .pop();
  const scanLabel = signalUpdatedAt ? signalSourceScanLabel(signalUpdatedAt) : freshness.label.toLowerCase();
  const nextBreakingHeader = state.signalHealth?.nextBreakingRun?.replace(":00 ", " ");
  const fallbackFresh = Boolean(state.signalHealth?.fallbackFresh && (state.signalHealth?.counts?.publicRssFallbackItems || 0) >= 5);
  const quietHeaderLabel = freshness.label === "Quiet-hours watch"
    ? nextBreakingHeader
      ? `quiet hours · next ${nextBreakingHeader}`
      : `quiet hours · ${scanLabel}`
    : null;
  const statusLabel =
    quietHeaderLabel
      ? quietHeaderLabel
      : freshness.tone === "clear"
      ? scanLabel
      : freshness.label.toLowerCase();
  const signalHeaderLabel = freshness.tone === "watch" && freshness.label.includes(" stale")
    ? `${storyLabel} · ${statusLabel}`
    : freshness.label === "Coverage watch" && watchSlots
      ? visibleRowsLabel
    : `${visibleRowsLabel} · ${statusLabel}`;
  return (
    <section id="signal-feed" className={`signal-feed${sectionCueClass("signal", liveCues)}`} aria-label="Live intelligence signal feed">
      <SectionCue label={liveCues.focus === "signal" ? "focus" : "updated"} />
      <header className="panel-title compact">
        <div>
          <p>Live intelligence</p>
          <h2>Signal Feed</h2>
        </div>
        <span className={`signal-freshness is-${freshness.tone}`} title={freshness.detail}>
          <Radio size={14} /> {signalHeaderLabel}
        </span>
      </header>
      <div className="signal-table">
        <div className="signal-section-label">
          <strong>Top five</strong>
          <span>{fallbackFresh ? "Fresh public source coverage" : "Breaking and developing stories"}</span>
        </div>
        <SignalFeedRows rows={topFive} liveCues={liveCues} emptyLabel="No live breaking rows loaded." />
        <div className="signal-section-label">
          <strong>Last 5</strong>
          <span>Newsletter trends from the agent inbox</span>
        </div>
        <SignalFeedRows rows={lastFive} liveCues={liveCues} emptyLabel="No newsletter trend rows loaded." newsletter />
      </div>
    </section>
  );
}

function SignalFeedRows({ rows, liveCues, emptyLabel, newsletter = false }: { rows: SignalItem[]; liveCues: LiveCueState; emptyLabel: string; newsletter?: boolean }) {
  const reserveCount = Math.max(0, 5 - rows.length);
  const reserveTitle = newsletter ? "Watching for another newsletter trend" : "Watching for the next breaking signal";
  const reserveDetail = newsletter
    ? "Duplicates stay hidden until a distinct trend appears."
    : "No stronger live item is ranking above the signal threshold right now.";
  const reserveImpact = newsletter
    ? "Waiting for a distinct source-backed trend."
    : "No action unless a fresh material break appears.";
  if (!rows.length) {
    const quietLabel = newsletter ? "Digest clear" : "Live clear";
    const quietDetail = newsletter
      ? "No newsletter trend rows are ready yet."
      : "No live breaking rows right now.";
    return (
      <>
        <article className="signal-live-empty">
          <span>{quietLabel}</span>
          <div className="signal-story">
            <strong>{quietDetail}</strong>
            <p>{newsletter ? "Waiting for the next newsletter aggregate." : "Latest newsletter trends remain visible below."}</p>
          </div>
          <p className="signal-impact">{emptyLabel}</p>
          <div className="signal-meta">
            <em className="signal-source">Control Tower</em>
            <time>clear</time>
          </div>
        </article>
        {Array.from({ length: 4 }).map((_, index) => (
          <article key={`${newsletter ? "newsletter" : "live"}-empty-reserve-${index}`} className="signal-reserve-row">
            <span>Open slot</span>
            <div className="signal-story">
              <strong>{reserveTitle}</strong>
              <p>{reserveDetail}</p>
            </div>
            <p className="signal-impact">{reserveImpact}</p>
            <div className="signal-meta">
              <em className="signal-source">Control Tower</em>
              <time>watching</time>
            </div>
          </article>
        ))}
      </>
    );
  }
  return (
    <>
      {rows.map((signal) => {
        const changed = Boolean(liveCues.rows[cueRowKey("signal", signal.id || signal.title)]);
        const impact = signal.impact || signal.impactScenarios?.medium || signal.impactScenarios?.med || signal.reason;
        const impactLabel = signalImpactLabel(signal, impact);
        const sourceLabel = compactText(signal.source, 34) || "Source pending";
        const displayTitle = signalDisplayTitle(signal, newsletter);
        const displayReason = signalDisplayReason(signal);
        return (
          <article key={signal.id || signal.title} className={`${signalRowClass(signal)}${changedRowClass(changed)}`}>
            <span className="row-change-dot" aria-hidden="true" />
            <span>{signalScoreLabel(signal)}</span>
            <div className="signal-story">
              <strong title={missionText(signal.title)}>{displayTitle}</strong>
              <p title={missionText(signal.reason)}>{displayReason}</p>
            </div>
            <p className={`signal-impact ${signalImpactTone(signal, impact)}`} title={`${impactLabel} · ${missionText(signal.source)} · ${missionText(impact)}`}>
              <b>{impactLabel}</b>
              <span>{sourceLabel}</span>
            </p>
            <div className="signal-meta">
              <time title={fmtTime(signal.time)}>
                <b>{signalStoryAgeLabel(signal, newsletter)}</b>
                <span>{fmtTime(signal.time)}</span>
              </time>
            </div>
          </article>
        );
      })}
      {Array.from({ length: reserveCount }).map((_, index) => (
        <article key={`${newsletter ? "newsletter" : "live"}-reserve-${index}`} className="signal-reserve-row">
          <span>Open slot</span>
          <div className="signal-story">
            <strong>{reserveTitle}</strong>
            <p>{reserveDetail}</p>
          </div>
          <p className="signal-impact">{reserveImpact}</p>
          <div className="signal-meta">
            <em className="signal-source">Control Tower</em>
            <time>watching</time>
          </div>
        </article>
      ))}
    </>
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
    const isHardDecision = ["high", "critical", "risk"].includes(String(approval.risk_tier || "").toLowerCase());
    const tone: AttentionItem["tone"] = isHardDecision ? "risk" : "watch";
    items.push({
      id: `approval-${approval.id}`,
      label: isHardDecision ? "Decision" : "Focus",
      title: missionText(approval.title),
      detail: approvalAlertReason(approval),
      why: approvalAlertReason(approval),
      means: isHardDecision
        ? "An agent is waiting before it should continue. This alert needs a Josh decision."
        : "Control Tower is highlighting a current setup or follow-up item. This is not an agent outage.",
      action: isHardDecision
        ? "Open the related job or approval lane, then approve, hold, or deny from the source that requested it."
        : "Open Today's Jobs when you are ready to finish the setup or clear the follow-up.",
      tone,
      target: "today-jobs",
    });
  });
  if (riskJobs.length) {
    const firstJob = riskJobs[0];
    items.push({
      id: `jobs-${firstJob.id}`,
      label: "Focus",
      title: riskJobs.length === 1
        ? compactJobTitle(firstJob)
        : `${riskJobs.length} jobs need focus`,
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
      action: "Open Brain Feed, check the agent tile, and send a test message if the last check-in is late.",
      tone: "risk",
      target: "brain-feed",
    });
  }
  const visibleItems = (quietMode ? items.filter((item) => item.tone !== "clear") : items).slice(0, 1);
  const overflowCount = Math.max(0, items.length - visibleItems.length);
  const selected = visibleItems.find((item) => item.id === selectedId) || null;

  if (!visibleItems.length) {
    return <div className="brain-attention-strip is-empty" aria-hidden="true" />;
  }

  return (
    <section className="brain-attention-strip" aria-label="What needs attention">
      {visibleItems.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`brain-attention-item is-${item.tone} ${selected?.id === item.id ? "selected" : ""}`}
            onClick={() => setSelectedId((current) => current === item.id ? null : item.id)}
            title={overflowCount ? `Show why this alert is here. ${overflowCount} more focus item${overflowCount === 1 ? "" : "s"} are tracked in the related sections.` : "Show why this alert is here"}
          >
            <span>{item.label}</span>
            <strong>{item.title}</strong>
            <p>{item.detail}</p>
          </button>
        ))}
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
  const layoutIssues = state.runtimeLayout?.issues || [];
  const layoutOk = state.runtimeLayout ? state.runtimeLayout.ok !== false && layoutIssues.length === 0 : false;
  const layoutAgeLabel = state.runtimeLayout?.checkedAt ? ageLabel(state.runtimeLayout.checkedAt) : "not checked";
  const signalTotal = state.signalHealth?.counts?.total || state.signals.length;
  const signalOk = ["ok", "fresh", "ready", "quiet"].includes(String(state.signalHealth?.status || "").toLowerCase()) && signalTotal >= 10;
  const walletOk = !state.agenticCrypto || ["fresh", "ok", "ready"].includes(String(state.agenticCrypto.status || "").toLowerCase());
  const truthWatchItems = [
    layoutOk ? null : "screen fit",
    signalOk ? null : "signals",
    walletOk ? null : "wallet",
    dataIssues.length ? "data guard" : null,
  ].filter((item): item is string => Boolean(item));
  const truthTitle = truthWatchItems.length ? `${truthWatchItems.length} item${truthWatchItems.length === 1 ? "" : "s"} to review` : "All current";
  const truthDetail = truthWatchItems.length
    ? `Check ${truthWatchItems.join(", ")}`
    : `${sourceTruthLabel(state.source)} · kiosk ${layoutAgeLabel} · ${signalTotal || 0} signals`;
  return (
    <section className={`brain-summary-strip is-${decision.tone}${sectionCueClass("system", liveCues)}`} aria-label="Brain Feed mission snapshot">
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
      <article className={`summary-chip summary-confidence truth-chip is-${truthWatchItems.length ? "watch" : "clear"}`} title={`${freshnessLabelText} · ${overall}% confidence. ${confidenceReason}`}>
        <span>Live check</span>
        <strong>{truthTitle}</strong>
        <p>{truthDetail}</p>
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
      title={`Open ${item.target === "today-jobs" ? "Today's Jobs" : "Brain Feed"}`}
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
    { agent: "josh", x: 20, y: 68 },
    { agent: "jaimes", x: 58, y: 72 },
    { agent: "jain", x: 84, y: 46 },
  ];
  const links = [
    ["joshex", "josh"],
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
        <span>{fmtCurrency(modelUsage?.metered?.daily ?? modelUsage?.daily)} metered today</span>
      </div>
      <div className="cost-snapshot">
        <article>
          <span>Metered week</span>
          <strong>{fmtCurrency(modelUsage?.metered?.weekly ?? modelUsage?.weekly)}</strong>
        </article>
        <article>
          <span>Sub/month</span>
          <strong>{fmtCurrency(modelUsage?.subscription?.monthlyFee ?? modelUsage?.monthly)}</strong>
        </article>
        <article>
          <span>Usage equiv</span>
          <strong>{fmtCurrency(modelUsage?.usageEquivalent?.monthly ?? modelUsage?.weeklyRunRate?.subscriptionUsageEquivalentProjectedMonthly)}</strong>
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

function compactTaskText(value?: string | null, fallback = "No completed task reported yet") {
  return compactText(missionText(value || fallback), 74);
}

function classifierText(value: string) {
  return value.toLowerCase().replace(/\bhigh[-\s]signal\b/g, "high value");
}

function expectedNextBullets(job?: MissionControlState["jobs"][number] | null, agent?: AgentId): AgentIdleContext["nextBullets"] {
  if (!job) {
    return [
      { label: "Input", text: "Direct instruction or the next scheduled agent event." },
      { label: "Checks", text: "Confirm owner, objective, and whether approval is needed." },
      { label: "Output", text: "A visible Brain Feed update with the next concrete step." },
      { label: "Alert", text: "Only if a blocker, missing route, or decision appears." },
    ];
  }

  const title = missionText(job.title);
  const text = classifierText(`${title} ${job.detail} ${job.tool} ${job.schedule}`);
  const titleTopic = classifierText(title);
  const owner = AGENTS[job.agent_id || agent || "joshex"]?.label || "Agent";

  if (/daily agent readiness|daily readiness|system-health|system health|health pass|agent readiness/.test(text)) {
    return [
      { label: "Input", text: "JAIMES/Hermes heartbeat, recent run state, and handoff queue." },
      { label: "Checks", text: "Confirms agent readiness and flags handoff or system issues." },
      { label: "Output", text: "Ready/watch status plus any needed repair or handoff." },
      { label: "Alert", text: "Only if the agent route, auth, or scheduled job is blocked." },
    ];
  }

  if (/context sync|agent context|j\.?a\.?i\.?n context/.test(text)) {
    return [
      { label: "Input", text: "Latest agent memory, task state, and shared context sidecars." },
      { label: "Checks", text: "Confirms each agent has a fresh plain-English status." },
      { label: "Output", text: "Keeps Brain Feed, Telegram, and Control Tower reading the same state." },
      { label: "Alert", text: "Only if an agent state file is stale or disagrees with the kiosk." },
    ];
  }

  if (/gmail|inbox|email|mail triage|unread/.test(text)) {
    return [
      { label: "Input", text: "Dashboard-safe inbox counts and the last-24-hour triage window." },
      { label: "Checks", text: "Looks for human requests, urgent ops, approvals, and newsletter noise." },
      { label: "Output", text: "A concise triage status plus any items Josh actually needs to see." },
      { label: "Alert", text: "Only if a reply, approval, or access issue needs attention." },
    ];
  }

  if (/signal feed|intelligence|news|newsletter|breaking|scanner|x watchlist/.test(titleTopic)) {
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

  if (/control tower|kiosk|dashboard|react|watchdog|refresh|live display|visual|readability|layout|ui/.test(text)) {
    return [
      { label: "Input", text: "Dashboard data, sidecars, kiosk server, and the live display." },
      { label: "Checks", text: "Verifies data freshness, layout fit, readable labels, and alerts." },
      { label: "Output", text: "A cleaner Control Tower surface with current live status." },
      { label: "Alert", text: "Only if the display, data, or refresh path needs repair." },
    ];
  }

  if (/fantasy|waiver|roster|injury|pitcher|player|baseball/.test(text)) {
    return [
      { label: "Input", text: "Roster state, injury news, waiver pool, and matchup context." },
      { label: "Checks", text: "Compares add/drop edge, ownership, role changes, and claim timing." },
      { label: "Output", text: "Candidate, reason, and decision timing." },
      { label: "Alert", text: "Only if a roster move looks time-sensitive or high-confidence." },
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

  if (/brain feed|heartbeat|visibility|agent status|status check/.test(text)) {
    return [
      { label: "Input", text: "Local live Brain Feed rows, sidecars, and agent heartbeats." },
      { label: "Checks", text: "Confirms each agent is fresh, readable, and mapped to the right tile." },
      { label: "Output", text: "Updated agent cards with current Complete, Next, and live status." },
      { label: "Alert", text: "Only if a visible row is stale or an agent stops reporting." },
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
      { label: "Output", text: "Ready/watch result, next owner, and any needed handoff." },
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
    const windowed = nextWindowedInterval(schedule, interval, last, nowMs);
    if (windowed) return windowed;
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

function parseClockMinutes(value?: string | null) {
  const match = missionText(value || "").match(/(\d{1,2})(?::(\d{2}))?\s*(AM|PM)/i);
  if (!match) return null;
  const hour12 = Number(match[1]);
  const minute = Number(match[2] || 0);
  const ampm = match[3].toUpperCase();
  if (!hour12 || minute < 0 || minute > 59) return null;
  let hour = hour12 % 12;
  if (ampm === "PM") hour += 12;
  return hour * 60 + minute;
}

function scheduleWindowMinutes(schedule: string) {
  const match = schedule.match(/\(([^()]+?)\s*(?:to|-|\u2013|\u2014)\s*([^()]+?)(?:\s+ET|\s+EST|\s+EDT)?\)/i);
  if (!match) return null;
  const start = parseClockMinutes(match[1]);
  const end = parseClockMinutes(match[2]);
  if (start === null || end === null) return null;
  return { start, end };
}

function dateAtClockMinutes(baseMs: number, minutes: number, dayOffset = 0) {
  const date = new Date(baseMs);
  date.setDate(date.getDate() + dayOffset);
  date.setHours(Math.floor(minutes / 60), minutes % 60, 0, 0);
  return date.getTime();
}

function nextWindowedInterval(schedule: string, interval: number, last: number, nowMs: number) {
  const window = scheduleWindowMinutes(schedule);
  if (!window) return null;
  const startToday = dateAtClockMinutes(nowMs, window.start);
  let endToday = dateAtClockMinutes(nowMs, window.end);
  if (endToday <= startToday) endToday += 24 * 60 * 60_000;

  if (nowMs < startToday) return startToday;
  if (nowMs > endToday) return dateAtClockMinutes(nowMs, window.start, 1);

  if (!last || last < startToday || last > endToday) {
    const firstCandidate = nowMs + interval;
    return firstCandidate <= endToday ? firstCandidate : dateAtClockMinutes(nowMs, window.start, 1);
  }

  const elapsed = Math.max(0, nowMs - last);
  const candidate = last + (Math.floor(elapsed / interval) + 1) * interval;
  if (candidate < startToday) return startToday;
  if (candidate > endToday) return dateAtClockMinutes(nowMs, window.start, 1);
  return candidate;
}

function countdownLabel(targetMs?: number, nowMs = Date.now()) {
  if (!targetMs) return "";
  const diff = targetMs - nowMs;
  if (diff <= 0) return "now";
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
  if (/feedback loop|intel_feedback_loop/.test(text)) return false;
  if (/health check|medic|silence detector/.test(text)) return false;
  return true;
}

function buildAgentIdleContext(agent: AgentId, state: MissionControlState, nowMs: number): AgentIdleContext {
  const agentJobs = state.jobs.filter((job) => job.agent_id === agent);
  const agentStatus = state.statuses.find((status) => status.agent_id === agent);
  const agentStatusValue = String(agentStatus?.status || "").toLowerCase();
  const agentStatusDone = ["done", "complete", "completed"].includes(agentStatusValue);
  const completedStatusStep = agentStatus?.steps.find((step) => {
    const stepStatus = String(step.status || "").toLowerCase();
    const stepKind = String(step.kind || "").toLowerCase();
    const stepTitle = missionText(step.label || step.title || "");
    const genericReadyStep = /online and ready|status check|visibility heartbeat/i.test(stepTitle);
    return ["done", "complete", "completed"].includes(stepStatus)
      && (agentStatusDone || stepKind === "complete" || agentStatusValue === "idle")
      && !genericReadyStep;
  });
  const completedJob = [...agentJobs]
    .filter((job) => ["done", "completed", "ok"].includes(String(job.status || "").toLowerCase()) || Boolean(job.completed_at))
    .sort((a, b) => timeValue(b.completed_at || b.lastRun || b.updated_at) - timeValue(a.completed_at || a.lastRun || a.updated_at))[0];
  const completedEvent = [...state.events]
    .filter((event) => event.agent_id === agent && ["done", "complete", "completed"].includes(String(event.status || event.event_type || "").toLowerCase()))
    .sort((a, b) => timeValue(b.created_at) - timeValue(a.created_at))[0];
  const completedJobTime = timeValue(completedJob?.completed_at || completedJob?.lastRun || completedJob?.updated_at);
  const completedEventTime = timeValue(completedEvent?.created_at);
  const completedStatusStepTime = completedStatusStep ? timeValue(agentStatus?.updated_at) : 0;
  const latestCompletionTitle = [
    { title: completedStatusStep?.label || completedStatusStep?.title, time: completedStatusStepTime },
    { title: completedEvent?.title, time: completedEventTime },
    { title: completedJob?.title, time: completedJobTime },
  ]
    .filter((candidate) => Boolean(candidate.title))
    .sort((a, b) => b.time - a.time)[0]?.title;
  const complete = compactTaskText(
    latestCompletionTitle,
    "No completed task reported yet",
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
    nextTitle: compactJobTitle(next.job),
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
    ? `${countdownShortText(idleContext.countdown)} + ${idleContext.nextTitle}`
    : idleContext.nextTitle;
  const issue = looseStatus.blocker || looseStatus.issue || looseStatus.error || "None";
  const steps = liveStepRows(status);
  const stepRows = steps.length ? steps.slice(-2) : [{ label: "Step", text: compactText(current, 96) }];
  const rows: AgentBriefRow[] = [
    { label: "Now", text: compactText(current, 96) },
    ...stepRows,
  ];
  if (String(route).trim() && String(route).trim() !== "Agent runtime") {
    rows.push({ label: "Tool", text: compactText(cleanHeadlineText(String(route)), 84) });
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

function nextCountdownClockLabel(nextAt?: number) {
  const clock = nextClockLabel(nextAt);
  if (!nextAt || !clock) return "";
  const countdown = nextAt > Date.now()
    ? countdownShortText(countdownLabel(nextAt))
    : "";
  return countdown ? `${countdown} · ${clock}` : clock;
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

function cleanHeadlineText(value: string) {
  return missionText(value)
    .replace(/\s*\([^)]*\)/g, "")
    .replace(/^(?:JOSHeX|Josh 2\.0|JAIMES|J\.A\.I\.N)\s+scheduled:\s*/i, "")
    .replace(/^make\s+/i, "")
    .replace(/^fix\s+/i, "")
    .replace(/^improve\s+/i, "")
    .replace(/\bbreaking news scanner\b/i, "Breaking News Scanner")
    .replace(/\bx watchlist monitor\b/i, "X Watchlist Monitor")
    .replace(/distance-readable/i, "readability")
    .replace(/information density/i, "info density")
    .replace(/\bMorning Inbox Triage\b/i, "Inbox Triage")
    .replace(/\bFantasy Waiver Review\b/i, "Fantasy Waivers")
    .replace(/\bControl Tower Refresh\b/i, "Control Tower Refresh")
    .replace(/\bBrain Feed Server\b/i, "Brain Feed Refresh")
    .replace(/\bintelligence feed\b/i, "Intelligence Feed")
    .replace(/\bintelligence feedback loop\b/i, "Intelligence Feedback Loop")
    .replace(/\s+/g, " ")
    .trim();
}

function headlineTitle(value: string, maxLength = 46) {
  return headlineShortText(cleanHeadlineText(value), maxLength);
}

function activeHeadlineTitle(value: string) {
  return cleanHeadlineText(value) || "Active work";
}

function briefOutputForHeadline(title: string, rows: AgentBriefRow[]) {
  const titleTopic = classifierText(title);
  const text = classifierText(`${title} ${rows.map((row) => row.text).join(" ")}`);
  if (/signal feed|intelligence|news|newsletter|breaking|scanner|x watchlist/.test(titleTopic)) return "Source-backed signals.";
  if (/control tower|kiosk|dashboard|react|watchdog|refresh|live display|visual|readability|layout|ui/.test(titleTopic)) return "Dashboard health check.";
  if (/gmail|inbox|email|mail triage|unread/.test(text)) return "Urgent-only inbox review.";
  if (/fantasy|waiver|roster|injury|pitcher|player|baseball/.test(text)) return "Injuries, waivers, roster risk.";
  if (/daily mission|missions|claim|reward|sorare/.test(text)) return "Mission choices or blockers.";
  if (/lineup|lineups|gw|game-week|pre-lock|rp|champion|challenger|deadline|submit/.test(text)) return "Slots, deadlines, risk.";
  if (/brain feed|heartbeat|visibility|agent status|status check/.test(text)) return "Live agent status.";
  if (/context sync|agent context|shared context/.test(text)) return "Keeps agent state aligned.";
  if (/control tower|kiosk|dashboard|react|watchdog|refresh/.test(text)) return "Dashboard health.";
  if (/signal|intelligence|news|newsletter|breaking/.test(text)) return "Source-backed signals.";
  if (/memory|backup|sync|manifest|recovery/.test(text)) return "Confirms sync status or repair note.";
  if (/hermes|jaimes|jain|agent control|openclaw|route|capability/.test(text)) return "Checks routes, ownership, and handoffs.";
  const row = rows.find((item) => item.label.toLowerCase() === "output")
    || rows.find((item) => item.label.toLowerCase() === "checks")
    || rows[0];
  return headlineShortText(row?.text.replace(/^a\s+/i, "").replace(/\.$/, "") || "Reports the result.", 76);
}

function readoutFit(value: string, maxLength = 68) {
  const text = missionText(value)
    .replace(/\s+/g, " ")
    .replace(/^(?:last|complete|completed):\s*/i, "")
    .replace(/\.$/, "")
    .trim();
  if (!text) return "";
  return headlineShortText(text, maxLength);
}

function readoutSummary(value?: string | null, fallback = "Scheduled check.", maxLength = 68) {
  const text = compactText(value || fallback, 160).replace(/\.\.\.$/, "").trim();
  const lower = classifierText(text);
  if (!text) return fallback;
  if (/concise triage status|items josh actually needs/i.test(lower)) return "Triage summary.";
  if (/mission choices|submission\/claim status|blocked action/i.test(lower)) return "Mission status.";
  if (/updated agent cards|current complete, next|live status/i.test(lower)) return "Live agent cards.";
  if (/refreshed control tower surface|clean kiosk health/i.test(lower)) return "Dashboard health.";
  if (/high-confidence item|source is broken|worth attention/i.test(lower)) return "Source-backed signals.";
  if (/confirmed backup state|clear repair note|sync fails/i.test(lower)) return "Confirmed sync status or repair note.";
  if (/telegram push|archived source data|source data/i.test(lower)) return "Publishes or archives source updates.";
  if (/gmail|inbox|email|mail triage|unread/.test(lower)) return "Urgent Gmail + Josh asks.";
  if (/fantasy|waiver|roster|injury|pitcher|player|baseball/.test(lower)) return "Waivers, injuries, roster risk.";
  if (/daily mission|missions|claim|reward|sorare/.test(lower)) return "Missions, rewards, blockers.";
  if (/lineup|lineups|gw|game-week|pre-lock|rp|champion|challenger|deadline|submit/.test(lower)) return "Lineups, deadlines, risk.";
  if (/brain feed|heartbeat|visibility|agent status|status check/.test(lower)) return "Rows, sidecars, live path.";
  if (/agent card|agent row|complete, next|readout|text fit|objective/.test(lower)) return "Readable live rows.";
  if (/context sync|agent context|shared context/.test(lower)) return "Shared agent status.";
  if (/daily readiness|system-health|system health|health pass|agent readiness/.test(lower)) return "Readiness + handoff risks.";
  if (/next readout|next job|top next|calendar block|daily calendar|visible calendar/.test(lower)) return "Next job and owner.";
  if (/today'?s jobs|today jobs|daily calendar|calendar fit|scheduler inventory/.test(lower)) return "Daily calendar fit.";
  if (/(?:live|current)(?: josh 2\.0)? display|wall display|screen balance|visual hierarchy|visual audit|kiosk audit|display balance|scanability|readability improvement|clutter reduction|visual noise|minimal, high value cleanup|source-of-truth|source of truth|trust at a glance|professional personal assistant/.test(lower)) return "Live layout cleanup.";
  if (/signal|intelligence|news|newsletter|breaking/.test(lower)) return "Breaking + newsletter signals.";
  if (/control tower|kiosk|dashboard|react|watchdog|ui/.test(lower)) return "Live kiosk health.";
  if (/memory|backup|sync|manifest|recovery/.test(lower)) return "Memory + sync health.";
  if (/hermes|jaimes|jain|agent control|openclaw|route|capability/.test(lower)) return "Routes and handoffs.";
  if (/google|calendar|drive|auth|oauth|scope/.test(lower)) return "Google auth health.";
  if (/xai|grok|gemini|model|provider|usage|cost/.test(lower)) return "Tracks model route, usage, and provider health.";
  return readoutFit(text, maxLength);
}

function countdownShortText(value: string) {
  const text = value.trim();
  if (/^now$/i.test(text)) return "now";
  return text
    .replace(/^t-(\d+)mins?$/i, "in $1m")
    .replace(/^t-(\d+)h\s+(\d+)m$/i, "in $1h $2m")
    .replace(/^t-(\d+)h$/i, "in $1h");
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

function idleAgentTopic(idleContext: AgentIdleContext, rows: AgentBriefRow[]) {
  const title = classifierText(idleContext.nextTitle);
  if (title && !/no upcoming|awaiting instruction/.test(title)) return title;
  return classifierText(rows.map((row) => `${row.label} ${row.text}`).join(" "));
}

function idleChecksSummary(agent: AgentId, idleContext: AgentIdleContext, rows: AgentBriefRow[]) {
  const text = idleAgentTopic(idleContext, rows);
  if (/gmail|inbox|email|mail triage|unread/.test(text)) return "Urgent Gmail + Josh asks.";
  if (/daily mission|missions|claim|reward|sorare/.test(text)) return "Missions, rewards, blockers.";
  if (/lineup|lineups|gw|game-week|pre-lock|deadline|submit/.test(text)) return "Lineups, deadlines, risk.";
  if (/fantasy|waiver|roster|injury|pitcher|player|baseball/.test(text)) return "Waivers, injuries, roster risk.";
  if (/signal feed|intelligence|news|newsletter|breaking|scanner|x watchlist/.test(text)) return "Source-backed signals.";
  if (/brain feed|heartbeat|visibility|agent status|status check/.test(text)) return "Rows, sidecars, live path.";
  if (/context sync|agent context|shared context/.test(text)) return "Shared agent status.";
  if (/control tower|kiosk|dashboard|react|watchdog|refresh|layout|ui/.test(text)) return "Live kiosk health.";
  if (/memory|backup|sync|manifest|recovery/.test(text)) return "Memory + sync health.";
  if (/hermes|jaimes|jain|agent control|openclaw|route|capability/.test(text)) return "Routes and handoffs.";
  return briefText(rows, ["Checks", "Input"], `${AGENTS[agent].label} schedule, sidecars, and latest state.`, 72);
}

function idleOutputSummary(agent: AgentId, idleContext: AgentIdleContext, rows: AgentBriefRow[], fallback: string) {
  const text = idleAgentTopic(idleContext, rows);
  if (/gmail|inbox|email|mail triage|unread/.test(text)) return "Triage summary.";
  if (/daily mission|missions|claim|reward|sorare/.test(text)) return "Mission status.";
  if (/lineup|lineups|gw|game-week|pre-lock|deadline|submit/.test(text)) return "Draft/submit status.";
  if (/fantasy|waiver|roster|injury|pitcher|player|baseball/.test(text)) return "Move, reason, timing.";
  if (/signal feed|intelligence|news|newsletter|breaking|scanner|x watchlist/.test(text)) return "Source-backed updates.";
  if (/brain feed|heartbeat|visibility|agent status|status check/.test(text)) return "Current live cards.";
  if (/context sync|agent context|shared context/.test(text)) return "Aligned agent state.";
  if (/control tower|kiosk|dashboard|react|watchdog|refresh|layout|ui/.test(text)) return "Dashboard health.";
  if (/memory|backup|sync|manifest|recovery/.test(text)) return "Sync or repair note.";
  if (/hermes|jaimes|jain|agent control|openclaw|route|capability/.test(text)) return "Ready/watch status.";
  return briefText(rows, ["Output"], fallback, 72);
}

function buildAgentInsights(
  activeFocus: boolean,
  briefRows: AgentBriefRow[],
  idleContext: AgentIdleContext,
  activeWork: WorkItem | undefined,
  status: AgentStatus,
  currentStep: string,
  nextOutput: string,
  routineFocus = false,
): AgentInsightRow[] {
  const currentSource = activeWork?.title || status.objective || currentStep || activeWork?.detail || status.detail;
  const currentSummary = readoutSummary(currentSource, "Working through the current step.", 72);
  const activeOutputSummary = activeWork?.title
    ? briefOutputForHeadline(activeWork.title, briefRows)
    : readoutSummary(
        status.detail || currentStep || nextOutput,
        "Reports the result.",
        72,
      );
  const outputSummary = activeOutputSummary.toLowerCase() === currentSummary.toLowerCase()
    ? routineFocus
      ? "Aligned; only reports mismatches."
      : "Result or blocker."
    : activeOutputSummary;
  if (activeFocus) {
    const actionLabel = routineFocus ? "Routine" : "Doing";
    const actionTone: AgentInsightRow["tone"] = routineFocus ? "default" : "active";
    const issue = String((status as AgentStatus & Record<string, unknown>).blocker || (status as AgentStatus & Record<string, unknown>).issue || "").trim();
    if (issue) {
      return [
        { label: actionLabel, text: currentSummary, tone: actionTone },
        { label: "Watch", text: readoutSummary(issue, "Needs review.", 72), tone: "watch" },
      ];
    }
    return [
      { label: actionLabel, text: currentSummary, tone: actionTone },
      { label: "Output", text: outputSummary, tone: "good" },
    ];
  }
  return [
    { label: "Checks", text: idleChecksSummary(status.agent_id, idleContext, briefRows) },
    { label: "Output", text: idleOutputSummary(status.agent_id, idleContext, briefRows, nextOutput), tone: "good" },
  ];
}

function agentInsightDisplayLabel(label: string) {
  if (label === "Checks") return "Watches";
  if (label === "Output") return "Reports";
  if (label === "Routine") return "Watches";
  return label;
}

function agentHeadline(
  activeFocus: boolean,
  objectiveText: string,
  idleContext: AgentIdleContext,
  idleRows: AgentBriefRow[],
  routineFocus = false,
  activeDetail = "",
): AgentHeadline {
  const nextTitle = headlineTitle(idleContext.nextTitle);
  const nextOutput = briefOutputForHeadline(nextTitle, idleRows);
  if (activeFocus) {
    const current = activeHeadlineTitle(objectiveText || "Active work");
    const routineDescription = readoutSummary(
      activeDetail || objectiveText,
      "Keeps background status current.",
      86,
    );
    return {
      eyebrow: routineFocus ? "Keeping current" : "Active now",
      title: current,
      description: routineFocus ? routineDescription : `Next: ${nextTitle} - ${nextOutput}`,
    };
  }
  if (!idleContext.nextAt && /awaiting instruction/i.test(idleContext.nextTitle)) {
    return {
      eyebrow: "Ready",
      title: "Awaiting instruction",
      description: "Next task will publish progress to Brain Feed.",
    };
  }
  const when = nextCountdownClockLabel(idleContext.nextAt) || idleContext.countdown || "soon";
  return {
    eyebrow: "Up next",
    time: when,
    title: nextTitle,
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
  const freshness = agentCardFreshnessClass(status);
  const sla = agentSla(status, idleContext);
  const objectiveText = missionText(status.objective);
  const activeWorkFresh = activeWork?.state === "working" && isFreshActiveTimestamp(activeWork.updated_at);
  const statusWorkingFresh = agentIsWorking(status) && isFreshActiveTimestamp(status.updated_at);
  const activeFocus = activeWorkFresh || statusWorkingFresh;
  const currentStep = status.steps?.find((step) => step.label || step.title)?.label
    || status.steps?.find((step) => step.label || step.title)?.title
    || status.current_tool
    || status.detail
    || AGENTS[agent].role;
  const statusHasFreshObjective = Boolean(objectiveText)
    && isFreshActiveTimestamp(status.updated_at)
    && !isReadyHeartbeatStatus(status);
  const statusActiveWork: WorkItem | undefined = (statusWorkingFresh || (activeFocus && statusHasFreshObjective)) ? {
    id: `status-${agent}`,
    agent_id: agent,
    title: compactText(status.objective || currentStep || AGENTS[agent].role, 72),
    detail: compactText(status.detail || status.current_tool || status.objective || currentStep || "Current Brain Feed objective", 96),
    state: "working",
    updated_at: status.updated_at,
    source: "agent",
    target: "brain-feed",
    priority: 90,
  } : undefined;
  const activeWorkDetail = statusActiveWork || (activeWorkFresh ? activeWork : undefined);
  const routineFocus = activeFocus && workItemIsRoutineActivity(activeWorkDetail, status);
  const idleBriefRows = [
    ...idleContext.nextBullets.slice(0, 4),
    { label: "Last", text: compactText(idleContext.complete, 84) },
    { label: "Next", text: compactText(idleContext.countdown ? `${countdownShortText(idleContext.countdown)} + ${idleContext.nextTitle}` : idleContext.nextTitle, 84) },
  ];
  const briefRows = activeFocus
    ? buildActiveAgentBrief(status, activeWorkDetail, idleContext, missionText(currentStep))
    : idleBriefRows;
  const headline = agentHeadline(
    activeFocus,
    activeWorkDetail?.title || objectiveText,
    idleContext,
    idleBriefRows,
    routineFocus,
    activeWorkDetail?.detail || status.detail || currentStep,
  );
  const insights = buildAgentInsights(
    activeFocus,
    briefRows,
    idleContext,
    activeWorkDetail,
    status,
    missionText(currentStep),
    headline.description.replace(/^Next:\s*/i, ""),
    routineFocus,
  );
  const nextSupport = compactText(idleContext.countdown ? `${countdownShortText(idleContext.countdown)} + ${idleContext.nextTitle}` : idleContext.nextTitle, 70);
  const lastSupport = readoutFit(idleContext.complete || "No completed task reported yet", 68);
  const nowSupport = readoutSummary(activeWorkDetail?.detail || activeWorkDetail?.title || currentStep, "Working through the current step.", 58);
  const supportNote = activeFocus
    ? routineFocus
      ? `Now: keeping live status current · Next: ${nextSupport}`
      : `Now: ${nowSupport} · Next: ${nextSupport}`
    : `Last: ${lastSupport} · Next: ${nextSupport}`;
  const visualState = agentVisualState(status, activeFocus, activeWork, routineFocus);
  const stepTrail = stepTrailForAgent(status, activeFocus, activeWork, routineFocus);
  const showStepTrail = activeFocus || visualState === "waiting" || visualState === "blocked";
  const updateAgeMs = Math.max(0, Date.now() - timeValue(status.updated_at));
  const hotness = Math.max(0, 1 - Math.min(updateAgeMs, 12 * 60_000) / (12 * 60_000));
  const pulseSpeed = routineFocus
    ? Math.max(2.2, 3.1 - hotness * 0.35)
    : activeFocus
    ? Math.max(1.05, 1.75 - hotness * 0.45)
    : visualState === "waiting" || visualState === "blocked"
      ? 2.35
      : 0;
  const railSpeed = routineFocus ? 3.4 : activeFocus ? Math.max(1.6, 2.8 - hotness * 0.7) : 2.8;
  const headerStateLabel = agentHeaderStateLabel(visualState, routineFocus, activeFocus);
  const headerDotClass = agentHeaderDotClass(visualState, routineFocus, activeFocus);
  const freshCheckin = ageMinutes(status.updated_at) < 5;
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
      className={`agent-hero-card ${agentClass(agent)} ${freshness} ${statusClass(status.status)} is-state-${visualState} ${routineFocus ? "is-routine-focus" : activeFocus ? "is-working-focus" : "is-up-next-focus"}${changedRowClass(changed)}`}
      style={{
        "--agent-pulse-speed": `${pulseSpeed}s`,
        "--agent-rail-speed": `${railSpeed}s`,
      } as React.CSSProperties}
    >
      <span className="row-change-dot" aria-hidden="true" />
      <span className="agent-pulse-ring" aria-hidden="true" />
      <span className="agent-live-rail" aria-hidden="true" />
      <header>
        <span className={`dot ${headerDotClass}`} />
        <strong>{AGENTS[agent].label}</strong>
        <em>{headerStateLabel}</em>
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
          <span className="agent-objective-meta">
            <b>{headline.eyebrow}</b>
            {headline.time ? <em>{headline.time}</em> : null}
          </span>
          <span className="agent-objective-main">{headline.title}</span>
          <span className="agent-objective-description">{headline.description}</span>
        </span>
      </h3>
      <p title={supportNote}>{supportNote}</p>
      <aside className="agent-insight-panel" aria-label={`${AGENTS[agent].label} operating readout`}>
        <div className={`agent-freshness-pill is-${sla.tone}${freshCheckin ? " is-hot" : ""}`}>
          <span>{sla.label}</span>
          <em>{sla.detail}</em>
        </div>
        <ul className="agent-insight-grid">
          {insights.map((item) => (
            <li key={item.label} className={item.tone ? `is-${item.tone}` : ""}>
              <b>{agentInsightDisplayLabel(item.label)}</b>
              <span title={item.text}>{item.text}</span>
            </li>
          ))}
        </ul>
      </aside>
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
          <p>Brain Feed</p>
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
  { key: "mission-control", label: "Control Tower", matcher: (_job, text) => /control tower|dashboard|brain feed|react|kiosk|v2/.test(text) },
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

function compactCategoryLabel(category: { key: string; label: string }) {
  const labels: Record<string, string> = {
    "mission-control": "Mission",
    inbox: "Inbox",
    sorare: "Sorare",
    fantasy: "Fantasy",
    "agent-control": "Agents",
    automation: "Auto",
    other: "Ops",
  };
  return labels[category.key] || category.label;
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

  if (lower.includes("keeps j.a.i.n alert state") || lower.includes("agent context")) {
    detail = "Keeps agent status aligned";
  } else if (lower.includes("backing up recovery memory bundle") || lower.includes("scheduled memory/context backup")) {
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
    detail = "Brain Feed alert layout polish";
  } else if (lower.includes("post-waiver scan")) {
    detail = "Post-waiver review window";
  } else if (lower.includes("agent readiness") || lower.includes("system-health") || lower.includes("system health")) {
    detail = "Checks agent readiness and handoffs";
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
  if (lower.includes("j.a.i.n context sync") || lower.includes("agent context sync")) return "Agent Context Sync";
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
  if (lower.includes("moving brain feed")) return "Brain Feed alert strip";
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
  if (isTomorrow) return "Tomorrow";
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

function todayRunLabel(job: JobRow) {
  const status = jobStatusValue(job);
  const rawStatus = String(job.status || "").toLowerCase();
  const next = nextRunTime(job);
  const evidenceTime = job.lastRun || job.completed_at || (["done", "completed"].includes(status) ? job.updated_at : undefined);
  if (status === "missed") return jobIsSoftMissedAutomation(job) ? "Ready" : "Missed today";
  if (job.verifiedToday || sameLocalDay(evidenceTime)) return "Ran today";
  if (status === "active" || status === "running" || status === "queued") return "Now";
  if (status === "due") return rawStatus === "paused" ? "Ready" : "Ready";
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
  const day = next.getTime() > Date.now() ? "Today" : "Tomorrow";
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

function calendarBlockRunLabel(block: CalendarJobBlock, run: ReturnType<typeof jobRunCells>) {
  if (block.tone === "working") return "Now";
  if (block.tone === "attention") return "Focus";
  if (block.tone === "done") return block.synthetic ? `${block.count} done` : "Done";
  const status = String(block.job.runStatus || block.job.status || "").toLowerCase();
  if (status === "missed") return "Overdue";
  if (block.tone === "planned") return block.synthetic ? `${block.count} scheduled` : "Planned";
  if (block.tone === "ready") return block.synthetic ? `${block.count} ready` : "Ready";
  return run.today;
}

function nextRunTime(job: JobRow) {
  const explicit = timeValue(job.nextRun);
  if (explicit) return explicit;
  const schedule = missionText(job.schedule || "");
  const intervalRun = nextIntervalWindowRunTime(schedule);
  if (intervalRun) return intervalRun;
  const simpleIntervalRun = nextSimpleIntervalRunTime(job, schedule);
  if (simpleIntervalRun) return simpleIntervalRun;
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

function nextSimpleIntervalRunTime(job: JobRow, schedule: string) {
  const intervalMatch = schedule.match(/\bevery\s+(\d+)\s*(min|mins|minute|minutes|hour|hours|hr|hrs)\b/i);
  if (!intervalMatch) return 0;
  const amount = Math.max(1, Number(intervalMatch[1]));
  const unit = intervalMatch[2].toLowerCase();
  const intervalMs = /hour|hr/.test(unit) ? amount * 60 * 60 * 1000 : amount * 60 * 1000;
  const now = Date.now();
  const last = timeValue(job.lastRun || job.completed_at);
  if (!last) return now + intervalMs;
  let next = last + intervalMs;
  while (next < now - 15 * 60 * 1000) next += intervalMs;
  return next;
}

function clockMatchToMinutes(match: RegExpMatchArray) {
  const hour12 = Number(match[1]);
  const minute = Number(match[2] || 0);
  const ampm = match[3].toUpperCase();
  let hour = hour12 % 12;
  if (ampm === "PM") hour += 12;
  return hour * 60 + minute;
}

function dateAtLocalMinute(day: Date, minuteOfDay: number) {
  const date = new Date(day);
  date.setHours(Math.floor(minuteOfDay / 60), minuteOfDay % 60, 0, 0);
  return date;
}

function nextIntervalWindowRunTime(schedule: string) {
  const intervalMatch = schedule.match(/\bevery\s+(\d+)\s*min/i);
  if (!intervalMatch) return 0;
  const clocks = [...schedule.matchAll(/(\d{1,2})(?::(\d{2}))?\s*(AM|PM)/gi)];
  if (clocks.length < 2) return 0;
  const interval = Math.max(1, Number(intervalMatch[1]));
  const startMinute = clockMatchToMinutes(clocks[0]);
  const endMinute = clockMatchToMinutes(clocks[1]);
  const now = new Date();
  const nowMinute = now.getHours() * 60 + now.getMinutes();
  if (nowMinute < startMinute) return dateAtLocalMinute(now, startMinute).getTime();
  if (nowMinute > endMinute) {
    const tomorrow = new Date(now);
    tomorrow.setDate(tomorrow.getDate() + 1);
    return dateAtLocalMinute(tomorrow, startMinute).getTime();
  }
  const elapsed = Math.max(0, nowMinute - startMinute);
  const nextMinute = startMinute + Math.ceil(elapsed / interval) * interval;
  if (nextMinute <= endMinute) return dateAtLocalMinute(now, nextMinute).getTime();
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  return dateAtLocalMinute(tomorrow, startMinute).getTime();
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

type CalendarBlockTone = "working" | "attention" | "done" | "planned" | "ready";

type CalendarJobBlock = {
  id: string;
  job: JobRow;
  startsAt: Date;
  hourKey: number;
  title: string;
  detail: string;
  agent: AgentId;
  category: ReturnType<typeof jobCategory>;
  tone: CalendarBlockTone;
  count?: number;
  synthetic?: boolean;
  agents?: AgentId[];
  groupKind?: "system" | "routine";
};

function startOfLocalDay(date = new Date()) {
  const value = new Date(date);
  value.setHours(0, 0, 0, 0);
  return value;
}

function calendarWindow() {
  const start = startOfLocalDay();
  start.setHours(5, 0, 0, 0);
  const end = new Date(start);
  end.setHours(23, 59, 0, 0);
  return { start, end };
}

function dateFromTimestamp(value?: string | null) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date : null;
}

function parseScheduleClockMinutes(schedule?: string | null) {
  const match = missionText(schedule || "").match(/(\d{1,2})(?::(\d{2}))?\s*(AM|PM|A|P)\b/i);
  if (!match) return null;
  const hour12 = Number(match[1]);
  const minute = Number(match[2] || 0);
  const ampm = match[3].toUpperCase()[0];
  if (!hour12 || hour12 > 12 || minute < 0 || minute > 59) return null;
  let hour = hour12 % 12;
  if (ampm === "P") hour += 12;
  return hour * 60 + minute;
}

function scheduledDateToday(job: JobRow) {
  if (!job.todayRelevant) return null;
  const schedule = missionText(job.schedule || "");
  if (!schedule || /on boot|on demand/i.test(schedule)) return null;
  const minuteOfDay = parseScheduleClockMinutes(schedule);
  if (minuteOfDay === null) return null;
  const date = startOfLocalDay();
  date.setHours(Math.floor(minuteOfDay / 60), minuteOfDay % 60, 0, 0);
  return date;
}

function calendarDateForJob(job: JobRow, now = new Date()) {
  const status = jobStatusValue(job);
  if (jobIsFreshActive(job) || ["active", "running", "queued"].includes(status)) return now;
  const scheduled = scheduledDateToday(job);
  if (scheduled) return scheduled;
  const last = dateFromTimestamp(job.lastRun || job.completed_at);
  if (last && sameLocalDay(last.toISOString())) return last;
  const next = nextRunTime(job);
  if (next) return new Date(next);
  const updated = dateFromTimestamp(job.updated_at);
  if (updated && sameLocalDay(updated.toISOString())) return updated;
  return null;
}

function calendarHourKey(date: Date) {
  const day = startOfLocalDay(date).getTime();
  const today = startOfLocalDay().getTime();
  const dayOffset = Math.round((day - today) / (24 * 60 * 60 * 1000));
  return dayOffset * 24 + date.getHours();
}

function calendarHourDate(hourKey: number) {
  const date = startOfLocalDay();
  date.setDate(date.getDate() + Math.floor(hourKey / 24));
  date.setHours(((hourKey % 24) + 24) % 24, 0, 0, 0);
  return date;
}

function compactCalendarDayLabel(date: Date, dayLabel: string) {
  if (dayLabel === "Today") return "";
  return date.toLocaleDateString([], { weekday: "short" });
}

function calendarHourLabel(hourKey: number) {
  const date = calendarHourDate(hourKey);
  const dayLabel = localDayLabel(date.toISOString());
  const timeLabel = date.toLocaleTimeString([], { hour: "numeric" }).replace(/\s/g, "");
  const compactDayLabel = compactCalendarDayLabel(date, dayLabel);
  const compactTimeLabel = dayLabel === "Today" ? timeLabel : timeLabel.replace(/[AP]M$/i, (suffix) => suffix[0].toLowerCase());
  return dayLabel === "Today" ? compactTimeLabel : `${compactDayLabel} ${compactTimeLabel}`;
}

function calendarTone(job: JobRow, allJobs: JobRow[]): CalendarBlockTone {
  if (jobNeedsAttention(job, allJobs)) return "attention";
  if (jobIsFreshActive(job)) return "working";
  if (job.verifiedToday || sameLocalDay(job.lastRun || job.completed_at || "")) return "done";
  const scheduled = scheduledDateToday(job);
  if (scheduled) {
    return scheduled.getTime() >= Date.now() - 15 * 60 * 1000 ? "planned" : "ready";
  }
  if (nextRunTime(job) >= Date.now() - 15 * 60 * 1000) return "planned";
  return "ready";
}

function groupedRoutineDetail(items: CalendarJobBlock[]) {
  const category = items[0]?.category;
  const count = items.length;
  const noun = count === 1 ? "check" : "checks";
  const groupedText = missionText(items.map((item) => `${item.title} ${item.detail} ${item.job.title} ${item.job.detail}`).join(" ")).toLowerCase();
  if (items.some((item) => routineCalendarGroupKey(item).endsWith("-system-checks"))) {
    const agentCount = new Set(items.map((item) => item.agent)).size;
    const agentLabel = agentCount > 1 ? `${agentCount} agents` : (AGENTS[items[0]?.agent]?.label || "system");
    return `${count} routine ${noun} · ${agentLabel}`;
  }
  if (/breaking news|x watchlist|briefing|feedback loop/.test(groupedText)) {
    return `${count} signal ${noun} · J.A.I.N`;
  }
  const labels: Record<string, string> = {
    "mission-control": "dashboard checks",
    "agent-control": "agent readiness checks",
    automation: "automation checks",
    inbox: "inbox checks",
    sorare: "Sorare checks",
    fantasy: "fantasy checks",
  };
  return `${count} ${labels[category?.key || ""] || `${category?.label || "routine"} ${noun}`}`;
}

function routineCalendarGroupKey(block: CalendarJobBlock) {
  const text = missionText(`${block.title} ${block.detail} ${block.job.title} ${block.job.detail} ${block.job.tool}`).toLowerCase();
  const systemRoutine = jobIsRoutineActivity(block.job) && (
    ["mission-control", "agent-control", "automation"].includes(block.category.key)
    || /context sync|brain feed server|control tower refresh|agent control checks|automation checks|watchdog|heartbeat|health check|silence detector|error rate|invite sync|calendar sync|appointment sync|chiro invite/.test(text)
  );
  if (systemRoutine) return `${block.hourKey}-system-checks`;
  return `${block.hourKey}-${block.category.key}-${block.agent}`;
}

function groupedRoutineTitle(items: CalendarJobBlock[], firstBlock: CalendarJobBlock, isSystemGroup: boolean) {
  if (items.length === 1) return firstBlock.title;
  if (isSystemGroup) return "System checks";
  const groupedText = missionText(items.map((item) => `${item.title} ${item.detail} ${item.job.title} ${item.job.detail}`).join(" ")).toLowerCase();
  if (/breaking news|x watchlist|briefing|feedback loop/.test(groupedText)) return "Signal checks";
  return `${firstBlock.category.label} checks`;
}

function buildCalendarJobBlocks(jobs: JobRow[]) {
  const { start, end } = calendarWindow();
  const startMs = start.getTime();
  const endMs = end.getTime();
  const raw = operatorSortedJobs(jobs, jobs)
    .map((job) => {
      const startsAt = calendarDateForJob(job);
      if (!startsAt) return null;
      const stamp = startsAt.getTime();
      if (!Number.isFinite(stamp) || stamp < startMs || stamp > endMs) return null;
      const category = jobCategory(job);
      return {
        id: job.id || `${job.agent_id}-${job.title}-${stamp}`,
        job,
        startsAt,
        hourKey: calendarHourKey(startsAt),
        title: compactJobTitle(job),
        detail: compactJobDetail(job, undefined, category.label),
        agent: job.agent_id,
        category,
        tone: calendarTone(job, jobs),
      } satisfies CalendarJobBlock;
    })
    .filter(Boolean) as CalendarJobBlock[];

  const important: CalendarJobBlock[] = [];
  const routine = new Map<string, CalendarJobBlock[]>();
  for (const block of raw) {
    const isPriority = priorityJobKey(block.job) !== "general";
    const isFocus = block.tone === "attention" || (block.tone === "working" && !jobIsRoutineActivity(block.job));
    if (isPriority || isFocus) {
      important.push(block);
      continue;
    }
    const key = routineCalendarGroupKey(block);
    const rows = routine.get(key) || [];
    rows.push(block);
    routine.set(key, rows);
  }

  const groupedRoutine = [...routine.values()].map((items) => {
    const first = operatorSortedJobs(items.map((item) => item.job), jobs)[0];
    const firstBlock = items.find((item) => item.job === first) || items[0];
    const isSystemGroup = items.some((item) => routineCalendarGroupKey(item).endsWith("-system-checks"));
    return {
      ...firstBlock,
      id: isSystemGroup
        ? `routine-${firstBlock.hourKey}-system-checks`
        : `routine-${firstBlock.hourKey}-${firstBlock.category.key}-${firstBlock.agent}`,
      title: groupedRoutineTitle(items, firstBlock, isSystemGroup),
      detail: items.length === 1 ? firstBlock.detail : groupedRoutineDetail(items),
      tone: items.some((item) => item.tone === "working")
        ? "working"
        : items.some((item) => item.tone === "done") ? "done" : "ready",
      count: items.length,
      synthetic: items.length > 1,
      agents: Array.from(new Set(items.map((item) => item.agent))),
      groupKind: isSystemGroup ? "system" : "routine",
    } satisfies CalendarJobBlock;
  });

  return [...important, ...groupedRoutine]
    .sort((a, b) => a.startsAt.getTime() - b.startsAt.getTime() || priorityScore(b.job) - priorityScore(a.job))
    .slice(0, 48);
}

function calendarJobsForMode(jobs: JobRow[], quietMode: boolean) {
  if (!quietMode) return jobs;
  return jobs.filter((job) => (
    priorityJobKey(job) !== "general"
    || jobIsActiveOrNeedsAttention(job, jobs)
    || jobIsSoon(job, 8)
    || job.todayRelevant
  ));
}

function nextVisibleCalendarBlock(jobs: JobRow[], quietMode: boolean) {
  const now = Date.now();
  const blocks = buildCalendarJobBlocks(calendarJobsForMode(jobs, quietMode));
  const running = blocks.find((block) => (
    block.tone === "working"
    && !jobIsRoutineActivity(block.job)
    && block.startsAt.getTime() >= now - 15 * 60 * 1000
    && block.startsAt.getTime() <= now + 5 * 60 * 1000
  ));
  if (running) return running;
  const nextPriority = blocks.find((block) => block.startsAt.getTime() >= now && !jobIsRoutineActivity(block.job));
  if (nextPriority) return nextPriority;
  const runningRoutine = blocks.find((block) => (
    block.tone === "working"
    && jobIsRoutineActivity(block.job)
    && block.startsAt.getTime() >= now - 15 * 60 * 1000
    && block.startsAt.getTime() <= now + 5 * 60 * 1000
  ));
  if (runningRoutine) return runningRoutine;
  return blocks.find((block) => block.startsAt.getTime() >= now)
    || blocks.find((block) => block.startsAt.getTime() >= now - 15 * 60 * 1000)
    || null;
}

function nextCalendarClockValue(block?: CalendarJobBlock | null) {
  if (!block) return "None";
  const startsAt = block.startsAt;
  const day = localDayLabel(startsAt.toISOString());
  const clock = startsAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return day === "Today" ? clock : `${day} ${clock}`;
}

function headerJobTitle(block: CalendarJobBlock) {
  const title = block.synthetic && block.count && block.count > 1
    ? `${block.count} ${block.title}`
    : block.title;
  return headlineTitle(title, 32);
}

function nextHeaderRunValue(block?: CalendarJobBlock | null) {
  if (!block) return "No jobs";
  const clock = nextCalendarClockValue(block);
  const target = block.startsAt.getTime();
  const title = headerJobTitle(block);
  if (target <= Date.now()) return title || `Now · ${clock}`;
  const countdown = target > Date.now()
    ? countdownShortText(countdownLabel(target))
    : "";
  return countdown ? `${countdown} · ${title}` : `${clock} · ${title}`;
}

function nextHeaderRunLabel(block?: CalendarJobBlock | null) {
  if (!block) return "Next up";
  if (jobIsRoutineActivity(block.job)) {
    return block.startsAt.getTime() <= Date.now() ? "Background" : "Next sync";
  }
  return block.startsAt.getTime() <= Date.now() ? "Job focus" : "Next up";
}

function calendarBlockTimeLabel(date: Date) {
  return date
    .toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    .replace(/\s/g, " ")
    .toUpperCase();
}

function calendarClearUntilLabel(hourKey: number) {
  return `Clear until ${calendarHourLabel(hourKey)}`;
}

function calendarSlots(blocks: CalendarJobBlock[]) {
  const nowKey = calendarHourKey(new Date());
  const keys = Array.from(new Set([...blocks.map((block) => block.hourKey), nowKey]))
    .sort((a, b) => a - b);
  return keys.map((key, index) => ({
    key,
    label: calendarHourLabel(key),
    gapBefore: index > 0 ? Math.max(0, key - keys[index - 1] - 1) : 0,
    isCurrent: key === nowKey,
    blocks: blocks.filter((block) => block.hourKey === key),
  }));
}

function DailyJobsCalendar({ jobs, liveCues }: { jobs: JobRow[]; liveCues: LiveCueState }) {
  const calendarJobs = jobs;
  const blocks = buildCalendarJobBlocks(calendarJobs);
  const todayBlocks = blocks.filter((block) => sameLocalDay(block.startsAt.toISOString()));
  const futureBlocks = blocks.filter((block) => !sameLocalDay(block.startsAt.toISOString()));
  const rawVisibleBlocks = todayBlocks.length >= 6
    ? todayBlocks
    : [...todayBlocks, ...futureBlocks.slice(0, Math.max(0, 6 - todayBlocks.length))];
  const visibleBlocks = rawVisibleBlocks
    .sort((a, b) => a.startsAt.getTime() - b.startsAt.getTime())
    .slice(0, 20);
  const slots = calendarSlots(visibleBlocks);
  const nowMs = Date.now();
  const nextBlock = blocks.find((block) => block.startsAt.getTime() > nowMs + 5 * 60 * 1000)
    || blocks.find((block) => block.startsAt.getTime() >= nowMs - 15 * 60 * 1000)
    || null;
  const workingBlocks = visibleBlocks.filter((block) => block.tone === "working");
  const routineRunning = workingBlocks.filter((block) => jobIsRoutineActivity(block.job)).length;
  const priorityRunning = Math.max(0, workingBlocks.length - routineRunning);
  const attention = visibleBlocks.filter((block) => block.tone === "attention").length;
  const completed = visibleBlocks.filter((block) => block.tone === "done").length;
  const planned = visibleBlocks.filter((block) => block.tone === "planned").length;
  const attentionValue = attention ? `${attention} review${attention === 1 ? "" : "s"}` : "Clear";
  const activeLabel = priorityRunning ? "Now" : routineRunning ? "Background" : "Now";
  const activeValue = priorityRunning
    ? `${priorityRunning} priority active`
    : routineRunning
      ? `${routineRunning} normal check${routineRunning === 1 ? "" : "s"}`
      : "Idle";
  const activeClass = priorityRunning ? "is-active" : routineRunning ? "is-routine" : "is-idle";
  const now = new Date();
  const nowLabel = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return (
    <section className="daily-calendar-view" aria-label="Today's Jobs daily calendar">
      <div className="calendar-control-strip">
        <article className={attention ? "is-risk" : "is-clear"}>
          <span>Focus</span>
          <strong>{attentionValue}</strong>
        </article>
        <article className={activeClass}>
          <span>{activeLabel}</span>
          <strong>{activeValue}</strong>
        </article>
        <article>
          <span>Done</span>
          <strong>{completed}</strong>
        </article>
        <article>
          <span>Planned</span>
          <strong>{planned}</strong>
        </article>
      </div>
      <div className="calendar-legend" aria-label="Calendar legend">
        <span><i className="agent-joshex" />JOSHeX</span>
        <span><i className="agent-josh" />Josh 2.0</span>
        <span><i className="agent-jaimes" />JAIMES</span>
        <span><i className="agent-jain" />J.A.I.N</span>
      </div>
      <div className="jobs-table-head" aria-hidden="true">
        <span>Time</span>
        <span>State</span>
        <span>Job</span>
        <span>Owner</span>
      </div>
      <div className="calendar-day-axis">
        {slots.map((slot) => {
          const nowBlockLabel = slot.blocks.length
            ? `${slot.blocks.length} job${slot.blocks.length === 1 ? "" : "s"}`
            : "clear";
          const currentHourLabel = slot.blocks.length
            ? `${slot.blocks.length} scheduled`
            : "clear";
          return (
            <section key={slot.key} className={`calendar-hour-slot ${slot.isCurrent ? "is-current" : ""}`}>
              {slot.gapBefore > 0 ? <div className="calendar-gap">{calendarClearUntilLabel(slot.key)}</div> : null}
              <time>{slot.label}</time>
              <div className="calendar-hour-content">
                {slot.isCurrent ? (
                  <div className="calendar-now-marker" title={`Now: ${nowBlockLabel}`}>
                    <span />
                    <strong>Now · {currentHourLabel}</strong>
                    <em>{nowLabel}</em>
                  </div>
                ) : null}
                {slot.blocks.length ? slot.blocks.map((block) => (
                  <CalendarJobBlockCard
                    key={block.id}
                    block={block}
                    liveCues={liveCues}
                    isNextUp={Boolean(nextBlock && block.id === nextBlock.id)}
                  />
                )) : (
                  <article className="calendar-empty-block">
                    <strong>Current hour clear</strong>
                    <p>No scheduled work in this hour.</p>
                  </article>
                )}
              </div>
            </section>
          );
        })}
      </div>
      <CalendarNextBrief block={nextBlock} />
    </section>
  );
}

function calendarBlockStateLabel(block: CalendarJobBlock, run: ReturnType<typeof jobRunCells>) {
  const status = String(block.job.runStatus || block.job.status || "").toLowerCase();
  if (block.tone === "working" || /running|active|in.progress/.test(status)) return "IN PROGRESS";
  if (block.tone === "attention" || /missed|overdue|failed|error|blocked/.test(status)) return /missed|overdue/.test(status) ? "OVERDUE" : "NEEDS JOSH";
  if (block.tone === "done" || /done|complete|ok|success/.test(status)) return "COMPLETE";
  if (block.tone === "ready" || /ready|due/.test(status)) return "READY";
  if (block.tone === "planned" || block.startsAt.getTime() > Date.now()) return "UPCOMING";
  return calendarBlockRunLabel(block, run).toUpperCase();
}

function CalendarNextBrief({ block }: { block: CalendarJobBlock | null }) {
  if (!block) {
    return (
      <article className="calendar-next-brief is-clear">
        <p>Next up</p>
        <strong>No remaining scheduled job</strong>
        <span>Agents are monitoring; new work will surface in Brain Feed.</span>
      </article>
    );
  }
  const owner = AGENTS[block.agent]?.label || block.agent;
  const headline = nextHeaderRunValue(block);
  const bullets = expectedNextBullets(block.job, block.agent);
  const checks = bullets.find((item) => item.label.toLowerCase() === "checks")?.text;
  const output = bullets.find((item) => item.label.toLowerCase() === "output")?.text;
  return (
    <article className={`calendar-next-brief tone-${block.tone} ${agentClass(block.agent)} ${categoryClass(block.job)}`}>
      <p>Next up</p>
      <strong title={`${headline} · ${missionText(block.job.title)}`}>{headline}</strong>
      <span>{owner} · {readoutSummary(checks || block.detail, "Checks the next scheduled task.", 74)}</span>
      <em>{readoutSummary(output || block.detail, "Publishes a status update when complete.", 74)}</em>
    </article>
  );
}

function CalendarJobBlockCard({ block, liveCues, isNextUp }: { block: CalendarJobBlock; liveCues: LiveCueState; isNextUp?: boolean }) {
  const run = jobRunCells(block.job);
  const changed = Boolean(liveCues.rows[cueRowKey("job", block.job.id || block.job.title)]);
  const time = calendarBlockTimeLabel(block.startsAt);
  const owner = block.synthetic && block.agents && block.agents.length > 1
    ? `${block.agents.length} agents`
    : AGENTS[block.agent]?.label || block.agent;
  return (
    <article className={`calendar-job-block tone-${block.tone} ${isNextUp ? "is-next-up" : ""} ${block.synthetic ? "is-synthetic" : ""} ${agentClass(block.agent)} ${categoryClass(block.job)}${changedRowClass(changed)}`}>
      <span className="row-change-dot" aria-hidden="true" />
      <div className="calendar-block-time">
        <strong>{time}</strong>
        <em>{calendarBlockRunLabel(block, run)}</em>
      </div>
      <div className="calendar-block-state">
        <strong>{calendarBlockStateLabel(block, run)}</strong>
      </div>
      <div className="calendar-block-main">
        <strong title={missionText(block.job.title)}>{block.title}</strong>
        <p title={missionText(block.job.detail || block.job.tool)}>{block.detail}</p>
      </div>
      <div className="calendar-block-meta">
        <span>{owner}</span>
        <em title={block.category.label}>{compactCategoryLabel(block.category)}</em>
      </div>
    </article>
  );
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

function JobsRail({ jobs, liveCues }: { jobs: MissionControlState["jobs"]; liveCues: LiveCueState }) {
  const trackedJobs = operatorTrackedJobs(jobs);
  const inventoryGroups = groupedJobs(trackedJobs, "category");
  return (
    <aside id="today-jobs" className={`jobs-rail${sectionCueClass("jobs", liveCues)}`}>
      <SectionCue label={liveCues.focus === "jobs" ? "focus" : "updated"} />
      <div className="panel-title compact calendar-title">
        <div>
          <p>Daily calendar</p>
          <h2>Today's Jobs</h2>
        </div>
      </div>
      <div className="job-list">
        <DailyJobsCalendar jobs={trackedJobs} liveCues={liveCues} />
        <SchedulerInventoryDisclosure groups={inventoryGroups} total={trackedJobs.length} surfaced={trackedJobs.length} liveCues={liveCues} />
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
          <span>{quietMode ? "priority view" : "Gmail · Sorare · Fantasy"}</span>
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
              <p>{quietMode ? "Routine maintenance is summarized below." : "Only priority work is active right now."}</p>
            </article>
          )}
          {readyCount > 0 ? (
            <article className="quiet-jobs-row">
              <strong>{readyCount} additional routine jobs ready</strong>
              <p>{quietMode ? "Focus view shows priority, active, missed, or blocked work." : "Completed or low-signal maintenance is collapsed from the focus view."}</p>
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
  const hidden = Math.max(0, total - surfaced);
  return (
    <details className="scheduler-inventory-section">
      <summary>
        <strong>All scheduled jobs</strong>
        <span>{hidden ? `${hidden} hidden · ` : ""}{total} tracked · by category</span>
      </summary>
      <div className="scheduler-inventory-note">
        <strong>All scheduled jobs</strong>
        <p>Every tracked job grouped by category. The calendar above shows what matters first.</p>
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
  return (
    <section className="model-usage-card">
      <div className="panel-title compact">
        <h2>Model Cost & Usage</h2>
        <span><DollarSign size={14} />{codexMode === "normal" ? `${fmtCurrency(modelUsage?.metered?.daily ?? modelUsage?.daily)} metered` : `Codex ${codexMode}`}</span>
      </div>
      <div className="cost-grid">
        <MetricMini label="Sub/month" value={fmtCurrency(modelUsage?.subscription?.monthlyFee ?? 200)} />
        <MetricMini label="Metered week" value={fmtCurrency(modelUsage?.metered?.weekly ?? modelUsage?.weekly)} />
        <MetricMini label="Metered month" value={fmtCurrency(modelUsage?.metered?.monthly ?? 0)} />
        <MetricMini label="Usage equiv" value={fmtCurrency(modelUsage?.usageEquivalent?.monthly ?? modelUsage?.monthly)} />
      </div>
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

createRoot(document.getElementById("root")!).render(<App />);
