import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AlertTriangle, CheckCircle2, ClipboardList, DollarSign, EyeOff, GitBranch, Radio, RefreshCw, ShieldCheck, Timer, UserRoundCheck } from "lucide-react";
import { loadMissionControl, subscribeMissionControlRealtime } from "./data";
import { PRIORITY_JOB_RULES, SORARE_DAILY_GROUPS, SORARE_GENERAL_PATTERN, type PriorityJobKey, type SorareGroupKey } from "./priorityJobs";
import type { AgentId, AgentStatus, MissionControlState } from "./types";
import "./styles.css";

const AGENTS: Record<AgentId, { label: string; role: string }> = {
  joshex: { label: "JOSHeX", role: "Private coordination" },
  josh: { label: "Josh 2.0", role: "Host operations" },
  jaimes: { label: "JAIMES", role: "Hermes reports" },
  jain: { label: "J.A.I.N", role: "Monitors" },
};
const HERO_AGENT_ORDER: AgentId[] = ["joshex", "josh", "jaimes"];
const CONTROL_TOWER_AGENT_ORDER: AgentId[] = ["joshex", "josh", "jaimes", "jain"];

type AttentionTarget = "brain-feed" | "today-jobs";
type WorkState = "working" | "waiting" | "blocked" | "ready" | "done" | "quiet";
type AgentVisualState = "working" | "ready" | "waiting" | "blocked" | "stale";
type StepTrailState = "done" | "current" | "pending";
type HeatTone = "quiet" | "fresh" | "working" | "handoff" | "done" | "alert";
type AgentIdleContext = {
  complete: string;
  nextTitle: string;
  nextAt?: number;
  countdown: string;
};
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

type UsageRow = {
  label: string;
  value: string;
  detail: string;
  explanation: string;
  status: string;
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
  time?: string;
  detail?: string;
};

type AgentFlowLane = {
  agent: AgentId;
  state: AgentVisualState;
  activeCount: number;
  readyCount: number;
  doneCount: number;
  handoffCount: number;
  lastLabel: string;
  nextLabel: string;
  freshness: string;
};
type SectionCueKey = "brain" | "jobs" | "signals" | "usage" | "system";
type LiveCueState = {
  sections: Partial<Record<SectionCueKey, number>>;
  rows: Record<string, number>;
  focus: SectionCueKey | null;
};

const CHANGE_CUE_MS = 3200;

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
    .replace(/React v2 Mission Control/gi, "current Mission Control")
    .replace(/Mission Control v2/gi, "Mission Control")
    .replace(/React v2/gi, "React Mission Control")
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
  if (value === "blocked" || value === "error") return "Needs attention";
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

function agentOperatingState(status: AgentStatus) {
  const value = String(status.status || "").toLowerCase();
  if (value === "blocked" || value === "error") return "Needs attention";
  if (value === "active" || value === "queued" || status.active) return "Working";
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
  if (value === "active" || value === "running" || value === "queued") return "working";
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

function isHighSpend(modelUsage?: MissionControlState["modelUsage"]) {
  const daily = modelUsage?.daily || 0;
  return daily >= 100;
}

function jobNeedsAttention(job: MissionControlState["jobs"][number], jobs: MissionControlState["jobs"] = []) {
  const status = String(job.status || "").toLowerCase();
  if (status !== "blocked" && status !== "error") return false;

  const text = `${job.title} ${job.detail} ${job.tool}`.toLowerCase();
  if (text.includes("gemini") && text.includes("auth-required")) return false;

  const updated = timeValue(job.updated_at);
  const allJobs = Array.isArray(jobs) ? jobs : [];
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

function freshnessLabel(value?: string | null) {
  const minutes = ageMinutes(value);
  if (!Number.isFinite(minutes)) return "stale";
  if (minutes < 3) return "live";
  if (minutes < 60) return `${minutes}m old`;
  const hours = Math.round(minutes / 60);
  return `${hours}h old`;
}

function freshnessTone(value?: string | null) {
  const minutes = ageMinutes(value);
  if (!Number.isFinite(minutes) || minutes >= 120) return "stale";
  if (minutes >= 30) return "aging";
  return "live";
}

function agentSla(status: AgentStatus) {
  const minutes = ageMinutes(status.updated_at);
  const expected = 120;
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

function agentClass(agent: AgentId) {
  return `agent-${agent}`;
}

function agentVisualState(status: AgentStatus, activeFocus: boolean, activeWork?: WorkItem): AgentVisualState {
  const value = String(status.status || "").toLowerCase();
  if (value === "blocked" || value === "error" || activeWork?.state === "blocked") return "blocked";
  if (activeWork?.state === "waiting") return "waiting";
  if (activeFocus || value === "active" || value === "queued" || status.active) return "working";
  if (freshnessClass(status.updated_at) === "is-stale") return "stale";
  return "ready";
}

function stepTrailForAgent(status: AgentStatus, activeFocus: boolean, activeWork?: WorkItem): Array<{ label: string; state: StepTrailState }> {
  const hasUpdate = Boolean(status.updated_at && timeValue(status.updated_at));
  const blocked = activeWork?.state === "blocked" || ["blocked", "error"].includes(String(status.status || "").toLowerCase());
  return [
    { label: "Received", state: hasUpdate ? "done" : "pending" },
    { label: blocked ? "Blocked" : "Working", state: activeFocus || blocked ? "current" : hasUpdate ? "done" : "pending" },
    { label: "Reported", state: activeFocus || blocked ? "pending" : hasUpdate ? "current" : "pending" },
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
  const cutoff = Date.now() - 90 * 60 * 1000;
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
    if (beams.length >= 3) return;
    const text = `${event.title} ${event.detail} ${event.tool} ${JSON.stringify(event.metadata || {})}`;
    const from = CONTROL_TOWER_AGENT_ORDER.includes(event.agent_id) ? event.agent_id : "joshex";
    const to = CONTROL_TOWER_AGENT_ORDER.find((agent) => agent !== from && textMentionsAgent(text, agent));
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
      time: event.created_at,
      detail: compactText(missionText(event.title || event.detail || "handoff"), 64),
    });
  });
  return beams;
}

function buildAgentFlowLanes(state: MissionControlState, nowMs = Date.now()): AgentFlowLane[] {
  const todayStart = new Date(nowMs);
  todayStart.setHours(0, 0, 0, 0);
  const handoffs = buildHandoffBeams(state);
  return CONTROL_TOWER_AGENT_ORDER.map((agent) => {
    const status = state.statuses.find((row) => row.agent_id === agent) || offlineStatus(agent);
    const jobs = state.jobs.filter((job) => job.agent_id === agent);
    const activeJobs = jobs.filter((job) => ["active", "running", "queued"].includes(String(job.runStatus || job.status || "").toLowerCase()));
    const readyJobs = jobs.filter((job) => jobWorkState(job, state.jobs) === "ready");
    const doneJobs = jobs.filter((job) => {
      const stamp = timeValue(job.completed_at || job.lastRun || job.updated_at);
      return stamp >= todayStart.getTime() && ["done", "completed", "ok", "ready"].includes(String(job.status || job.runStatus || "").toLowerCase());
    });
    const handoffCount = handoffs.filter((handoff) => handoff.from === agent || handoff.to === agent).length;
    const lastEvent = state.events
      .filter((event) => event.agent_id === agent)
      .sort((a, b) => timeValue(b.created_at) - timeValue(a.created_at))[0];
    const lastJob = jobs.sort((a, b) => timeValue(b.updated_at || b.completed_at || b.lastRun) - timeValue(a.updated_at || a.completed_at || a.lastRun))[0];
    const idle = buildAgentIdleContext(agent, state, nowMs);
    const visualState = agentVisualState(status, Boolean(activeJobs.length || status.active), undefined);
    return {
      agent,
      state: visualState,
      activeCount: activeJobs.length + (status.active ? 1 : 0),
      readyCount: readyJobs.length,
      doneCount: doneJobs.length,
      handoffCount,
      lastLabel: compactText(missionText(lastEvent?.title || lastJob?.title || status.detail || "quiet"), 54),
      nextLabel: idle.countdown ? `${idle.countdown} + ${idle.nextTitle}` : idle.nextTitle,
      freshness: ageLabel(status.updated_at),
    };
  });
}

function pushHeatTone(current: HeatTone, next: HeatTone): HeatTone {
  const rank: Record<HeatTone, number> = {
    quiet: 0,
    fresh: 1,
    done: 2,
    working: 3,
    handoff: 4,
    alert: 5,
  };
  return rank[next] > rank[current] ? next : current;
}

function heatToneFromStatus(status?: string) {
  const value = String(status || "").toLowerCase();
  if (value === "blocked" || value === "error" || value === "missed") return "alert";
  if (value === "active" || value === "running" || value === "queued" || value === "working") return "working";
  if (value === "done" || value === "complete" || value === "completed" || value === "ready" || value === "ok") return "done";
  return "fresh";
}

function activityHeatRows(state: MissionControlState, agents: AgentId[]) {
  const now = Date.now();
  const bucketMs = 5 * 60 * 1000;
  const starts = Array.from({ length: 12 }, (_, index) => now - (11 - index) * bucketMs);
  const rows = agents.map((agent) => ({
    agent,
    cells: starts.map((start) => ({
      start,
      end: start + bucketMs,
      tone: "quiet" as HeatTone,
      title: `${AGENTS[agent].label} quiet`,
    })),
  }));
  const apply = (agent: AgentId, time: string | undefined | null, tone: HeatTone, title: string) => {
    const timestamp = timeValue(time);
    if (!timestamp || timestamp < now - 60 * 60 * 1000) return;
    const row = rows.find((item) => item.agent === agent);
    const cell = row?.cells.find((item) => timestamp >= item.start && timestamp < item.end);
    if (!cell) return;
    cell.tone = pushHeatTone(cell.tone, tone);
    cell.title = title;
  };
  state.statuses.forEach((status) => {
    apply(status.agent_id, status.updated_at, heatToneFromStatus(status.status), `${AGENTS[status.agent_id]?.label || status.agent_id}: ${displayStatus(status.status)}`);
  });
  state.events.forEach((event) => {
    const text = `${event.event_type} ${event.title} ${event.detail} ${event.tool}`;
    const tone = /handoff|route|delegate/i.test(text) ? "handoff" : heatToneFromStatus(event.status || event.event_type);
    apply(event.agent_id, event.created_at, tone, `${AGENTS[event.agent_id]?.label || event.agent_id}: ${missionText(event.title)}`);
  });
  state.jobs.forEach((job) => {
    const tone = jobIsSoftMissedAutomation(job) ? "done" : heatToneFromStatus(job.runStatus || job.status);
    apply(job.agent_id, job.started_at || job.updated_at, tone, `${AGENTS[job.agent_id]?.label || job.agent_id}: ${compactJobTitle(job)}`);
  });
  state.approvals
    .filter((approval) => approval.status === "pending")
    .forEach((approval) => {
      apply(approval.agent_id, approval.created_at, "alert", `${AGENTS[approval.agent_id]?.label || approval.agent_id}: decision waiting`);
    });
  return rows;
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
    signals: compactSignature(state.signals.slice(0, 12).map((row) => [row.id, row.title, row.score, row.time, row.source, row.reason])),
    usage: compactSignature({
      daily: state.modelUsage?.daily,
      weekly: state.modelUsage?.weekly,
      lastUpdated: state.modelUsage?.lastUpdated,
      providers: (state.modelRouter?.providers || state.modelUsage?.providerBudgets || []).map((row: any) => [
        row.id,
        row.status,
        row.dailySpendUsd,
        row.lastModelUsed,
        row.lastTestStatus,
      ]),
    }),
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
  state.signals.slice(0, 16).forEach((row) => {
    rows[cueRowKey("signal", row.id || row.title)] = compactSignature([row.title, row.score, row.time, row.source, row.reason, row.impact]);
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
  if (state.signals.some((signal) => freshnessTone(signal.time) === "live" && (signal.score || 0) >= 8)) return "signals";
  if (isHighSpend(state.modelUsage)) return "usage";
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

function App() {
  const [state, setState] = useState<MissionControlState>(EMPTY_STATE);
  const [loading, setLoading] = useState(true);
  const [liveMode, setLiveMode] = useState<"connected" | "polling">("polling");
  const [quietMode, setQuietMode] = useState(false);
  const refreshInFlightRef = useRef(false);
  const liveCues = useLiveCues(state);

  const refresh = useCallback(async (showLoading = true) => {
    if (refreshInFlightRef.current) return;
    refreshInFlightRef.current = true;
    if (showLoading) setLoading(true);
    try {
      const next = await loadMissionControl();
      setState(next);
    } catch (error) {
      console.warn("Mission Control refresh failed", error);
    } finally {
      refreshInFlightRef.current = false;
      if (showLoading) setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    let cancelled = false;
    let timer: number | null = null;
    const schedule = () => {
      if (cancelled) return;
      const delay = document.hidden ? 30_000 : 10_000;
      timer = window.setTimeout(async () => {
        await refresh(false);
        schedule();
      }, delay);
    };
    const onVisibility = () => {
      if (!document.hidden) refresh(false);
    };
    schedule();
    document.addEventListener("visibilitychange", onVisibility);
    const unsubscribe = subscribeMissionControlRealtime(
      () => refresh(false),
      setLiveMode,
    );
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      unsubscribe();
    };
  }, [refresh]);

  const statusByAgent = useMemo(() => {
    return new Map(state.statuses.map((row) => [row.agent_id, row]));
  }, [state.statuses]);

  const attentionCount = state.approvals.filter((row) => row.status === "pending").length
    + state.statuses.filter((row) => row.status === "blocked" || row.status === "error").length
    + state.jobs.filter((row) => jobNeedsAttention(row, state.jobs)).length;
  const jobsCount = state.jobs.length;
  const lastUpdate = [...state.statuses.map((row) => row.updated_at), ...state.events.map((row) => row.created_at)]
    .filter(Boolean)
    .sort()
    .pop();
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
            <h1>Josh 2.0 | Mission Control</h1>
            <p>Brain Feed command view for the agent ecosystem</p>
          </div>
        </div>
        <section className="status-ribbon header-status-ribbon" aria-label="Mission Control summary">
          <Metric icon={<AlertTriangle size={18} />} label="Action" value={String(attentionCount)} tone={attentionCount ? "risk" : "clear"} />
          <Metric icon={<ClipboardList size={18} />} label="Jobs" value={String(jobsCount)} tone="clear" />
          <Metric icon={<Timer size={18} />} label="Updated" value={fmtTime(lastUpdate)} tone="clear" wide />
        </section>
        <div className="mission-actions">
          <span className="source-chip"><ShieldCheck size={15} />{state.source}</span>
          <span className="source-chip live-chip">{liveMode === "connected" ? "Realtime" : "Live • 10s"}</span>
          <button
            type="button"
            className={quietMode ? "mode-button selected" : "mode-button"}
            onClick={() => setQuietMode((value) => !value)}
            aria-pressed={quietMode}
            title="Show only active work, warnings, missed jobs, high-score signals, and pending approvals"
          >
            <EyeOff size={15} /> Quiet
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
          <section className="support-grid">
            <SignalFeed signals={state.signals} quietMode={quietMode} liveCues={liveCues} />
          </section>
        </section>
        <aside className="right-rail">
          <JobsRail jobs={state.jobs} quietMode={quietMode} liveCues={liveCues} />
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
  const { events, approvals, signals } = state;
  const heroAgents = HERO_AGENT_ORDER;
  const pendingApprovals = approvals.filter((row) => row.status === "pending");
  const recentCutoff = Date.now() - 60 * 60 * 1000;
  const recentActivity = events.filter((event) => timeValue(event.created_at) > recentCutoff).length;
  const activeAgents = Array.from(statuses.values()).filter((row) => row.active || row.status === "active").length;
  const activeJobs = state.jobs.filter((job) => job.status === "active" || job.status === "queued").length;
  const workItems = buildWorkItems(state);
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
      className="brain-hero"
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
        <span>{quietMode ? "Quiet mode" : `${events.slice(0, 6).length} recent updates`}</span>
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

      <AgentFlowTower state={state} liveCues={liveCues} />

      <ActivityHeatStrip state={state} agents={CONTROL_TOWER_AGENT_ORDER} />

      <BrainUsageStrip modelUsage={state.modelUsage} modelRouter={state.modelRouter} liveCues={liveCues} />

      <RuntimeCapabilityPanel state={state} />

      <BrainOperationsSummary state={state} workItems={workItems} quietMode={quietMode} onNavigate={onNavigate} liveCues={liveCues} />
    </section>
  );
}

function BrainAttentionStrip({ state, quietMode, onNavigate }: { state: MissionControlState; quietMode: boolean; onNavigate: (target: AttentionTarget) => void }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const pendingApprovals = state.approvals.filter((row) => row.status === "pending");
  const riskJobs = state.jobs.filter((job) => jobNeedsAttention(job, state.jobs));
  const blockedAgents = state.statuses.filter((row) => row.status === "blocked" || row.status === "error");
  const highSpend = isHighSpend(state.modelUsage);
  const items: AttentionItem[] = [];

  pendingApprovals.slice(0, 2).forEach((approval) => {
    items.push({
      id: `approval-${approval.id}`,
      label: "Action",
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
      label: "Jobs",
      title: `${riskJobs.length} job${riskJobs.length === 1 ? "" : "s"} need attention`,
      detail: jobAlertReason(firstJob),
      why: jobAlertReason(firstJob),
      means: "A scheduled or priority job reported blocked, error, or missed. Mission Control is surfacing the first affected row so you can inspect it quickly.",
      action: "Open Today's Jobs and handle the red or purple row first.",
      tone: "risk",
      target: "today-jobs",
    });
  }
  if (blockedAgents.length) {
    const firstAgent = blockedAgents[0];
    items.push({
      id: `agent-${firstAgent.agent_id}`,
      label: "Agents",
      title: `${blockedAgents.length} agent${blockedAgents.length === 1 ? "" : "s"} need attention`,
      detail: agentAlertReason(firstAgent),
      why: agentAlertReason(firstAgent),
      means: `${AGENTS[firstAgent.agent_id]?.label || firstAgent.agent_id} reported a blocked state or stopped giving a healthy status signal.`,
      action: "Open Brain Feed, check the agent tile, and send a test message if the last check-in is late.",
      tone: "risk",
      target: "brain-feed",
    });
  }
  if (highSpend) {
    items.push({
      id: "usage-high",
      label: "Usage",
      title: `${fmtCurrency(state.modelUsage?.daily)} estimated today`,
      detail: usageAlertReason(state.modelUsage),
      why: usageAlertReason(state.modelUsage),
      means: "This is an estimate from tracked model lanes, not a direct bill. It is shown when the projected run rate is unusually high.",
      action: "Open Model Usage details and shift non-urgent work to subscription or quota-backed lanes.",
      tone: "watch",
      target: "brain-feed",
    });
  }

  const visibleItems = (quietMode ? items.filter((item) => item.tone !== "clear") : items).slice(0, 3);
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
            <strong>{item.title}</strong>
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

function AgentFlowTower({ state, liveCues }: { state: MissionControlState; liveCues: LiveCueState }) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  const lanes = buildAgentFlowLanes(state, nowMs);
  const beams = buildHandoffBeams(state);
  const activeJobs = state.jobs.filter((job) => ["active", "running", "queued"].includes(String(job.runStatus || job.status || "").toLowerCase())).length;
  const completedToday = lanes.reduce((sum, lane) => sum + lane.doneCount, 0);
  const routingCount = beams.length;
  const latestFlow = [...state.events]
    .sort((a, b) => timeValue(b.created_at) - timeValue(a.created_at))
    .slice(0, 1)[0];
  return (
    <section className={`agent-flow-tower${sectionCueClass("brain", liveCues)}`} aria-label="Agent flow control tower">
      <header>
        <div>
          <span><GitBranch size={13} />Control tower</span>
          <strong>Jobs flowing across agents</strong>
        </div>
        <div className="flow-tower-metrics" aria-label="Flow metrics">
          <span><b>{activeJobs}</b> running</span>
          <span><b>{completedToday}</b> done today</span>
          <span><b>{routingCount}</b> handoff beams</span>
        </div>
      </header>
      <div className="flow-lane-grid">
        {lanes.map((lane) => (
          <article key={lane.agent} className={`flow-lane ${agentClass(lane.agent)} is-state-${lane.state}`}>
            <span className="flow-lane-node" aria-hidden="true" />
            <header>
              <strong>{AGENTS[lane.agent].label}</strong>
              <em>{lane.activeCount ? `${lane.activeCount} running` : lane.readyCount ? `${lane.readyCount} queued` : "standing by"}</em>
            </header>
            <div className="flow-lane-bars" aria-hidden="true">
              <i className={lane.activeCount ? "is-hot" : ""} />
              <i className={lane.handoffCount ? "is-handoff" : ""} />
              <i className={lane.doneCount ? "is-done" : ""} />
            </div>
            <p>{lane.lastLabel}</p>
            <small>{lane.freshness} · next: {lane.nextLabel}</small>
          </article>
        ))}
      </div>
      <div className="handoff-flow-strip" aria-label="Recent handoff flow">
        {beams.length ? beams.slice(0, 4).map((beam) => (
          <article key={beam.id} className={`handoff-flow-row is-${beam.tone}`}>
            <span>{AGENTS[beam.from].label}</span>
            <i aria-hidden="true" />
            <span>{AGENTS[beam.to].label}</span>
            <strong>{beam.detail || beam.label}</strong>
            <em>{ageLabel(beam.time)}</em>
          </article>
        )) : (
          <article className="handoff-flow-row is-quiet">
            <span>Routing bus</span>
            <i aria-hidden="true" />
            <span>Agents</span>
            <strong>No recent handoffs; jobs are staying with owners.</strong>
            <em>standby</em>
          </article>
        )}
        {latestFlow ? <p className="latest-flow-line">Latest event: {AGENTS[latestFlow.agent_id]?.label || latestFlow.agent_id} · {missionText(latestFlow.title)}</p> : null}
      </div>
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

function ActivityHeatStrip({ state, agents }: { state: MissionControlState; agents: AgentId[] }) {
  const rows = activityHeatRows(state, agents);
  return (
    <section className="activity-heat-strip" aria-label="Agent activity over the last hour">
      <header>
        <span>Last 60m</span>
        <em>Quiet</em>
        <em>Work</em>
        <em>Handoff</em>
        <em>Alert</em>
      </header>
      <div className="activity-heat-rows">
        {rows.map((row) => (
          <article key={row.agent} className={`activity-heat-row ${agentClass(row.agent)}`}>
            <strong>{AGENTS[row.agent].label}</strong>
            <div>
              {row.cells.map((cell, index) => (
                <span
                  key={`${row.agent}-${cell.start}`}
                  className={`heat-cell is-${cell.tone}${index === 11 ? " is-now" : ""}`}
                  title={`${cell.title} · ${index === 11 ? "now" : `${(11 - index) * 5}m ago`}`}
                />
              ))}
            </div>
          </article>
        ))}
      </div>
    </section>
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

function usageAlertReason(modelUsage?: MissionControlState["modelUsage"]) {
  const projected = fmtCurrency(modelUsage?.weeklyRunRate?.projectedMonthly);
  return `Why: estimated usage is elevated; projected value is ${projected}.`;
}

function BrainUsageStrip({
  modelUsage,
  modelRouter,
  liveCues,
}: {
  modelUsage?: MissionControlState["modelUsage"];
  modelRouter?: MissionControlState["modelRouter"];
  liveCues: LiveCueState;
}) {
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);
  const providers = modelRouter?.providers || modelUsage?.providerBudgets || [];
  const codexProvider = providers.find((provider: any) => provider.id === "codex");
  const geminiProvider = providers.find((provider: any) => provider.id === "gemini");
  const xaiProvider = providers.find((provider: any) => provider.id === "xai");
  const xaiUsage = modelUsage?.xai;
  const topModels = modelUsage?.breakdown?.length ? modelUsage.breakdown : modelUsage?.topModels || [];
  const xaiBreakdown = topModels.find((model: any) =>
    /xai|grok/i.test(`${model.name || ""} ${model.source || ""}`),
  );
  const xaiCallsToday = xaiUsage?.callsToday ?? xaiBreakdown?.callsToday ?? 0;
  const xaiStatus = xaiUsage?.lastStatus || xaiBreakdown?.lastStatus || xaiProvider?.status || "tracked";
  const topModel = topModels.find((model: any) => (model.weeklyCost ?? model.cost ?? 0) > 0) || topModels[0];
  const codexRouteModel = topModels.find((model: any) => /gpt-5\.5/i.test(String(model.name || "")))?.name;
  const codexModel = displayModelName(codexRouteModel || codexProvider?.lastModelUsed || topModels.find((model: any) => String(model.source || "").toLowerCase() === "codex")?.name || topModel?.name);
  const jainDaily = modelUsage?.jain?.daily || 0;
  const aggregateDaily = modelUsage?.aggregate?.daily ?? ((modelUsage?.daily || 0) + jainDaily);
  const weeklyValue = modelUsage?.weeklyRunRate?.total ?? modelUsage?.weekly;
  const geminiStatus = missionText(geminiProvider?.status || "ready");
  const xaiPlainStatus = /fail|error|blocked|auth/i.test(String(xaiStatus || ""))
    ? "needs setup"
    : missionText(xaiStatus || "ready");
  const xaiSummary = xaiCallsToday
    ? `${xaiCallsToday} xAI run${xaiCallsToday === 1 ? "" : "s"} today`
    : `xAI ${xaiPlainStatus}`;
  const sourceLabel = codexProvider?.budgetType === "subscription" ? "subscription usage value" : "metered estimate";
  const rows: UsageRow[] = [
    {
      label: "Codex",
      value: fmtCurrency(modelUsage?.daily),
      detail: sourceLabel,
      explanation: codexProvider?.budgetType === "subscription"
        ? "Codex is shown as subscription usage value. This helps compare workload, but it is not a separate pay-per-call bill."
        : "Codex is shown as metered estimated usage from tracked model rows.",
      status: codexProvider?.lastModelUsed || codexModel || "tracked",
    },
    {
      label: "Total",
      value: fmtCurrency(aggregateDaily),
      detail: jainDaily ? `incl. J.A.I.N ${fmtCurrency(jainDaily)}` : "all tracked lanes",
      explanation: "Total combines the visible model lanes Mission Control can currently track. It may include subscription-value estimates and automation usage.",
      status: "dashboard estimate",
    },
    {
      label: "Weekly",
      value: fmtCurrency(modelUsage?.weeklyRunRate?.total ?? modelUsage?.weekly),
      detail: `${fmtCurrency(modelUsage?.weeklyRunRate?.projectedMonthly)} projected value`,
      explanation: "Weekly compares recent tracked usage against a monthly projection so unusual run rates are easy to spot early.",
      status: "projection",
    },
    {
      label: "Gemini",
      value: fmtCurrency(geminiProvider?.dailySpendUsd || 0),
      detail: geminiProvider?.status || "quota lane",
      explanation: "Gemini is quota-backed when available. Mission Control shows spend as zero unless a metered Gemini cost is logged.",
      status: geminiProvider?.lastModelUsed || geminiProvider?.status || "ready",
    },
    {
      label: "xAI",
      value: fmtCurrency(xaiProvider?.dailySpendUsd || xaiUsage?.daily || xaiBreakdown?.dailyCost || 0),
      detail: xaiCallsToday
        ? `${xaiCallsToday} run${xaiCallsToday === 1 ? "" : "s"} today · ${xaiStatus}`
        : xaiProvider?.status || xaiBreakdown?.source || "specialist lane",
      explanation: xaiCallsToday
        ? "xAI/Grok specialist runs are now tracked from the secure broker metadata. Current failed runs usually mean the host key is missing or the broker returned an error."
        : "xAI/Grok is wired as a specialist lane. It will show run count, status, tokens, and cost metadata when the broker records usage.",
      status: xaiStatus,
    },
  ];
  const expanded = selectedLabel === "summary";

  return (
    <section className={`brain-usage-strip is-compact${sectionCueClass("usage", liveCues)}`} aria-label="Model usage estimate">
      <SectionCue label={liveCues.focus === "usage" ? "watch" : "updated"} />
      <button
        type="button"
        className={`usage-summary-button ${expanded ? "selected" : ""}`}
        onClick={() => setSelectedLabel((current) => current === "summary" ? null : "summary")}
        title="Open model usage details"
      >
        <span><DollarSign size={13} />Model usage</span>
        <strong>{fmtCurrency(aggregateDaily)} today</strong>
        <p>{fmtCurrency(weeklyValue)} weekly · {codexModel || "Codex"} · Gemini {geminiStatus} · {xaiSummary}</p>
      </button>
      {expanded ? (
        <div className="usage-detail-grid" aria-label="Model usage details">
        {rows.map((row) => (
          <article
            key={row.label}
            title={row.explanation}
          >
            <span>{row.label}</span>
            <strong>{row.value}</strong>
            <p>{row.detail}</p>
          </article>
        ))}
      </div>
      ) : null}
    </section>
  );
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
  const visibleRuntimeItems = runtimeItems
    .sort((a, b) => {
      const rank = (item: any) => reliabilityTone(item.status) === "is-risk" ? 0 : reliabilityTone(item.status) === "is-watch" ? 1 : 2;
      return rank(a) - rank(b);
    })
    .slice(0, 4);
  return (
    <section className="runtime-capability-panel is-simplified" aria-label="Agent runtime capability inventory">
      <header className="panel-title compact">
        <div>
          <p>Runtime capability</p>
          <h2>Agent Stack</h2>
        </div>
        <span className={attention ? "is-risk" : watchCount ? "is-watch" : "is-done"}>
          <ShieldCheck size={14} /> {headline}
        </span>
      </header>
      <div className="runtime-snapshot-row" aria-label="Runtime stack summary">
        <article><span>Nodes</span><strong>{nodes.length}</strong></article>
        <article><span>Tools</span><strong>{readyTools}</strong></article>
        <article><span>Watch</span><strong>{watchCount}</strong></article>
        <article><span>Risk</span><strong>{attention}</strong></article>
      </div>
      <div className="runtime-capability-grid">
        {visibleRuntimeItems.map((item) => (
          <article key={item.id} className={reliabilityTone(item.status)} title={missionText(item.detail || item.summary || "Dashboard-safe inventory")}>
            <header>
              <strong>{missionText(item.name)}</strong>
              <em>{missionText(item.status || "tracked")}</em>
            </header>
            <p>{missionText(item.summary || item.detail || "Capability tracked")}</p>
          </article>
        ))}
      </div>
      <footer>
        <span>{nodes.length} nodes · {readyTools} tool lanes</span>
        <em>Watch {fmtTime(watch?.checkedAt || watch?.updatedAt || inventory?.updatedAt)}</em>
      </footer>
    </section>
  );
}

function MissionTimeline({
  events,
  jobs,
  approvals,
  signals,
}: {
  events: MissionControlState["events"];
  jobs: MissionControlState["jobs"];
  approvals: MissionControlState["approvals"];
  signals: MissionControlState["signals"];
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
    ...signals.map((signal) => ({ id: signal.id, time: signal.time, type: "signal", agent: "jain" as AgentId, label: signal.title, lane: "signals" })),
  ]
    .map((row) => ({ ...row, at: timeValue(row.time) }))
    .filter((row) => Number.isFinite(row.at) && row.at > now - windowMs)
    .sort((a, b) => a.at - b.at)
    .slice(-36);
  const lanes = [
    { key: "priority", label: "Priority Jobs", rows: rows.filter((row) => row.lane === "priority" || row.type === "risk").slice(-12) },
    { key: "updates", label: "Agent Updates", rows: rows.filter((row) => row.lane === "updates").slice(-12) },
    { key: "signals", label: "Signals", rows: rows.filter((row) => row.lane === "signals").slice(-10) },
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
        <span><i className="is-signal" />Signals</span>
        <span><i className="is-handoff" />Approvals</span>
      </footer>
    </section>
  );
}

function MissionHealthPanel({ state }: { state: MissionControlState }) {
  const pendingApprovals = state.approvals.filter((row) => row.status === "pending").length;
  const riskJobs = state.jobs.filter((job) => jobNeedsAttention(job, state.jobs)).length;
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
  const riskJobs = state.jobs.filter((job) => jobNeedsAttention(job, state.jobs)).length;
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
  const reliabilityLine = reliabilityItems.length
    ? `Reliability: ${reliabilityReady} ready${reliabilityWatch ? ` / ${reliabilityWatch} watch` : ""}${reliabilityAttention ? ` / ${reliabilityAttention} attention` : ""}`
    : "Reliability: probes quiet";
  const reliabilityWhy = reliabilityFocus
    ? `${reliabilityAttention ? "Attention" : "Watch"}: ${missionText(reliabilityFocus.label)} - ${missionText(reliabilityFocus.signal)}`
    : "Reliability: all tracked probes ready";
  const overall = Math.round((freshness + Math.min(100, Math.round((readyAgents / trackedAgents) * 100)) + (riskJobs ? 58 : 100) + (pendingApprovals ? 70 : 100)) / 4);
  const confidenceReason = riskJobs
    ? "Lower because a job is blocked."
    : pendingApprovals
      ? "Lower because a decision is waiting."
      : freshnessNeedsWatch
        ? "Lower because feed check-ins are aging."
        : readyAgents < trackedAgents
          ? "Lower because not every agent is ready."
          : overall >= 100
            ? "All tracked signals are fresh and ready."
            : "Not 100% because live feeds can lag.";
  const decision = riskJobs
    ? {
        tone: "risk",
        title: `${riskJobs} job${riskJobs === 1 ? "" : "s"} blocked`,
        detail: "Open Today's Jobs and handle the red row first.",
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
            title: "Feed check-in aging",
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
            detail: workingNow ? "Agents are reporting in the tower." : "Only new alerts need Josh right now.",
            icon: <CheckCircle2 size={22} />,
          };
  const operatorSummary = pendingApprovals
    ? `${pendingApprovals} decision${pendingApprovals === 1 ? "" : "s"} waiting. Priority jobs remain visible.`
    : riskJobs
      ? `${riskJobs} priority job${riskJobs === 1 ? "" : "s"} need attention. Open Today's Jobs first.`
      : freshnessNeedsWatch
        ? `No decision needed. Feed freshness is aging; latest check-in was ${ageLabel(lastUpdate)} ago.`
        : "All clear. Agent flow and priority jobs are current.";

  return (
    <section className={`brain-ops-panel${sectionCueClass("system", liveCues)}`} aria-label="Brain Feed mission snapshot">
      <SectionCue label={liveCues.focus === "system" ? "focus" : "updated"} />
      <div className={`brain-decision-card is-${decision.tone}`} style={{ "--score": overall } as React.CSSProperties}>
        <header>
          <span>System readout</span>
          <div className="decision-icon">{decision.icon}</div>
        </header>
        <strong>{decision.title}</strong>
        <p className="operator-summary">{operatorSummary}</p>
        <div className="system-readout-lines">
          <span>{reliabilityLine}</span>
          <span>{reliabilityWhy}</span>
          <small>{overall}% live signal confidence · {confidenceReason}</small>
        </div>
      </div>
      <div className="brain-ops-main">
        <AgentWorkBoard items={workItems} quietMode={quietMode} onNavigate={onNavigate} liveCues={liveCues} />
      </div>
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
      countdown: "",
    };
  }
  return {
    complete,
    nextTitle: compactTaskText(next.job.title, "scheduled task"),
    nextAt: next.nextAt,
    countdown: countdownLabel(next.nextAt, nowMs),
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
  const sla = agentSla(status);
  const objectiveText = missionText(status.objective);
  const activeWorkFresh = activeWork?.state === "working" && isFreshActiveTimestamp(activeWork.updated_at);
  const statusWorkingFresh = ["active", "working"].includes(String(status.status || "").toLowerCase()) && isFreshActiveTimestamp(status.updated_at);
  const activeFocus = activeWorkFresh || statusWorkingFresh;
  const activeWorkDetail = activeWorkFresh ? activeWork : undefined;
  const upNextText = idleContext.countdown
    ? `Up next: ${idleContext.countdown} + ${idleContext.nextTitle}`
    : `Up next: ${idleContext.nextTitle}`;
  const focusText = activeFocus ? objectiveText : missionText(upNextText);
  const currentStep = status.steps?.find((step) => step.label || step.title)?.label
    || status.steps?.find((step) => step.label || step.title)?.title
    || status.current_tool
    || status.detail
    || AGENTS[agent].role;
  const visualState = agentVisualState(status, activeFocus, activeWork);
  const stepTrail = stepTrailForAgent(status, activeFocus, activeWork);
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
  }, [focusText]);
  return (
    <article className={`agent-hero-card ${agentClass(agent)} ${freshness} ${statusClass(status.status)} is-state-${visualState} ${activeFocus ? "is-working-focus" : "is-up-next-focus"}${changedRowClass(changed)}`}>
      <span className="row-change-dot" aria-hidden="true" />
      <span className="agent-pulse-ring" aria-hidden="true" />
      <header>
        <span className={`dot ${statusClass(status.status)}`} />
        <strong>{AGENTS[agent].label}</strong>
        <em>{agentOperatingState(status)} · {ageLabel(status.updated_at)}</em>
      </header>
      <div className="agent-step-trail" aria-label={`${AGENTS[agent].label} live step trail`}>
        {stepTrail.map((step) => (
          <span key={step.label} className={`is-${step.state}`}>
            <i aria-hidden="true" />
            <b>{step.label}</b>
          </span>
        ))}
      </div>
      <h3
        ref={objectiveRef}
        className={`agent-objective ${objectiveScroll.active ? "is-scrollable" : ""}`}
        style={{
          "--objective-scroll-distance": `${objectiveScroll.distance}px`,
          "--objective-scroll-duration": `${objectiveScroll.duration}s`,
        } as React.CSSProperties}
      >
        <span className="agent-objective-text">{focusText}</span>
      </h3>
      <p>
        {activeFocus
          ? activeWorkDetail
            ? `${workStateLabel(activeWorkDetail.state)}: ${missionText(activeWorkDetail.detail || activeWorkDetail.title)}`
            : missionText(currentStep)
          : `Standing by after: ${idleContext.complete}`}
      </p>
      <div className="agent-idle-readout" aria-label={`${AGENTS[agent].label} completion and next scheduled work`}>
        <p><b>Complete:</b> <span>{idleContext.complete}</span></p>
        <p><b>Next:</b> <span>{idleContext.countdown ? `${idleContext.countdown} + ${idleContext.nextTitle}` : idleContext.nextTitle}</span></p>
      </div>
      <footer className={`agent-response-sla is-${sla.tone}`}>
        <span>{sla.label}</span>
        <em>{sla.detail}</em>
      </footer>
    </article>
  );
}

function signalStrengthClass(score?: number | null) {
  if (typeof score !== "number" || Number.isNaN(score)) return "is-muted";
  if (score >= 8) return "is-strong";
  if (score >= 5) return "is-watch";
  return "is-muted";
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
                <span>{missionText(event.tool || "Mission Control")}</span>
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
  { key: "mission-control", label: "Mission Control", matcher: (_job, text) => /mission control|dashboard|brain feed|react|kiosk|v2/.test(text) },
  { key: "signals", label: "Signals & Inbox", matcher: (_job, text) => /signal|intelligence|inbox|approval|handoff|ledger/.test(text) },
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
  const active = items.filter((job) => job.status === "active" || job.status === "queued").length;
  const risk = items.filter((job) => jobNeedsAttention(job, items)).length;
  if (risk) return `${risk} need attention`;
  if (active) return `${active} active`;
  return `${items.length} tracked`;
}

function jobIsActiveOrNeedsAttention(job: JobRow, jobs: JobRow[] = []) {
  return job.status === "active" || job.status === "queued" || jobNeedsAttention(job, jobs);
}

function jobStatusValue(job: JobRow) {
  return String(job.runStatus || job.status || "").toLowerCase();
}

function jobStatusClass(job: JobRow, jobs: JobRow[] = []) {
  const status = jobStatusValue(job);
  const softMiss = jobIsSoftMissedAutomation(job);
  if (jobNeedsAttention(job, jobs) || ["blocked", "error", "failed"].includes(status) || (status === "missed" && !softMiss)) return "is-risk";
  if (["active", "running", "queued", "due"].includes(status)) return "is-active";
  return "is-done";
}

function jobStatusLabel(job: JobRow, jobs: JobRow[] = []) {
  const status = jobStatusValue(job);
  const softMiss = jobIsSoftMissedAutomation(job);
  if (jobNeedsAttention(job, jobs) || ["blocked", "error", "failed"].includes(status) || (status === "missed" && !softMiss)) return "Needs attention";
  if (["active", "running", "queued", "due"].includes(status)) return "Working";
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
    detail = "Brain Feed alert layout polish";
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
  if (isTomorrow) return "Tmrw";
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

function todayRunLabel(job: JobRow) {
  const status = jobStatusValue(job);
  if (status === "missed") return jobIsSoftMissedAutomation(job) ? "Ready" : "Missed today";
  if (job.verifiedToday || sameLocalDay(job.lastRun || job.completed_at || job.updated_at)) return "Ran today";
  if (status === "upcoming") return "Planned today";
  if (status === "due") return "Due now";
  if (status === "active" || status === "running" || status === "queued") return "Now";
  return job.todayRelevant ? "Not verified today" : "Not scheduled today";
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
  if (!last) return "Last: not found";
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
    .replace("Not verified today", "Unverified");
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

function priorityJobKey(job: JobRow): PriorityJobKey | "general" {
  const text = `${job.title} ${job.tool} ${job.agent_id}`.toLowerCase();
  return PRIORITY_JOB_RULES.find((rule) => rule.pattern.test(text))?.key || "general";
}

function priorityJobGroups(jobs: JobRow[]) {
  const ordered = [...jobs].sort((a, b) => timeValue(b.updated_at) - timeValue(a.updated_at));
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
  const sorareJobs = jobs
    .filter((job) => priorityJobKey(job) === "sorare")
    .sort((a, b) => timeValue(b.updated_at) - timeValue(a.updated_at));
  return SORARE_DAILY_GROUPS.map((group) => ({
    ...group,
    items: sorareJobs.filter((job) => sorareGroupKey(job) === group.key),
  }));
}

function compactJobStatus(items: JobRow[]) {
  if (!items.length) return "Ready";
  if (items.some((job) => jobNeedsAttention(job, items))) return "Needs attention";
  if (items.some((job) => ["active", "running", "queued", "due"].includes(jobStatusValue(job)))) return "Working";
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

function JobsRail({ jobs, quietMode, liveCues }: { jobs: MissionControlState["jobs"]; quietMode: boolean; liveCues: LiveCueState }) {
  const [view, setView] = useState<"focus" | "detailed">("focus");
  const visibleJobs = quietMode
    ? jobs.filter((job) => jobIsActiveOrNeedsAttention(job, jobs) || priorityJobKey(job) !== "general")
    : jobs;
  const groups = groupedJobs(visibleJobs, "category");
  const attentionJobs = jobs.filter((job) => jobNeedsAttention(job, jobs)).length;
  const priorityCount = jobs.filter((job) => priorityJobKey(job) !== "general").length;
  const activeCount = jobs.filter((job) => job.status === "active" || job.status === "queued").length;
  return (
    <aside id="today-jobs" className={`jobs-rail${sectionCueClass("jobs", liveCues)}`}>
      <SectionCue label={liveCues.focus === "jobs" ? "focus" : "updated"} />
      <div className="panel-title compact">
        <h2>Today's Jobs</h2>
        <span>{quietMode ? "Quiet mode" : attentionJobs ? `${attentionJobs} need focus` : `${priorityCount} priority · ${activeCount} active`}</span>
      </div>
      <div className="jobs-stats-strip" aria-label="Today's Jobs summary">
        <article className={attentionJobs ? "is-risk" : "is-clear"}>
          <span>Needs focus</span>
          <strong>{attentionJobs}</strong>
        </article>
        <article>
          <span>Priority</span>
          <strong>{priorityCount}</strong>
        </article>
        <article>
          <span>Total</span>
          <strong>{jobs.length}</strong>
        </article>
      </div>
      <div className="jobs-view-toggle" aria-label="Today jobs view">
        <button type="button" className={view === "focus" ? "selected" : ""} onClick={() => setView("focus")}>Focus</button>
        <button type="button" className={view === "detailed" ? "selected" : ""} onClick={() => setView("detailed")}>Detailed</button>
      </div>
      <div className="job-list">
        {view === "focus"
          ? <JobFocusView jobs={visibleJobs} allJobs={jobs} quietMode={quietMode} liveCues={liveCues} />
          : <JobCategoryView groups={groups} liveCues={liveCues} />}
      </div>
    </aside>
  );
}

function JobFocusView({ jobs, allJobs, quietMode, liveCues }: { jobs: JobRow[]; allJobs: JobRow[]; quietMode: boolean; liveCues: LiveCueState }) {
  const { byPriority, general } = priorityJobGroups(jobs);
  const sorareGroups = sorareDailyGroups(jobs);
  const visibleGeneral = general.filter((job) => jobIsActiveOrNeedsAttention(job, allJobs)).slice(0, quietMode ? 8 : 5);
  const upcoming = upcomingTodayJobs(allJobs, quietMode ? 6 : 8);
  const runningGeneral = visibleGeneral.filter((job) => jobWorkState(job, allJobs) === "working").length;
  const readyGeneral = Math.max(0, general.length - visibleGeneral.length);
  const readyCount = Math.max(0, allJobs.length - visibleGeneral.length - PRIORITY_JOB_RULES.reduce((sum, rule) => {
    if (rule.key === "sorare") return sum + sorareGroups.reduce((count, group) => count + group.items.length, 0);
    return sum + (byPriority.get(rule.key)?.length || 0);
  }, 0));
  return (
    <section className="job-focus-view">
      <div className="priority-jobs">
        <header>
          <strong>Priority Jobs</strong>
          <span>Gmail · Sorare · Fantasy</span>
        </header>
        <JobTableHeader />
        <div className="priority-job-list">
          {PRIORITY_JOB_RULES.map((rule) => {
            if (rule.key === "sorare") return <SorareDailyJobsPanel key={rule.key} groups={sorareGroups} liveCues={liveCues} />;
            const rows = byPriority.get(rule.key) || [];
            const job = rows[0];
            if (job) return <JobFocusRow key={rule.key} job={job} label={rule.label} count={rows.length} priority liveCues={liveCues} />;
            return (
              <article key={rule.key} className="job-focus-row is-priority is-quiet">
                <span className="status-dot is-muted" aria-hidden="true" />
                <div className="job-table-title">
                  <strong>{rule.label}</strong>
                  <p>{rule.agent} · No current focus item</p>
                </div>
                <span className="job-table-owner">{rule.agent}</span>
                <span className="job-run-cell job-run-indicator is-quiet">Untracked</span>
                <span className="job-run-cell">Not found</span>
                <span className="job-run-cell">Awaiting schedule</span>
                <span className="job-status is-done">Ready</span>
              </article>
            );
          })}
        </div>
      </div>
      <div className="upcoming-jobs-section">
        <header>
          <strong>Upcoming Today</strong>
          <span>{upcoming.length ? `${upcoming.length} scheduled` : "No remaining scheduled jobs"}</span>
        </header>
        <div className="upcoming-job-list">
          {upcoming.length ? upcoming.map((job) => <UpcomingJobRow key={`upcoming-${job.id}`} job={job} allJobs={allJobs} liveCues={liveCues} />) : (
            <article className="maintenance-summary-card">
              <strong>No upcoming jobs found</strong>
              <p>Priority and active work remain surfaced above.</p>
            </article>
          )}
        </div>
      </div>
      <div className="general-jobs-section">
        <header>
          <strong>General / Maintenance</strong>
          <span>{runningGeneral ? `${runningGeneral} running` : "No active maintenance"} · {readyGeneral} ready</span>
        </header>
        <div className="general-job-list">
          {visibleGeneral.length ? visibleGeneral.map((job) => <JobFocusRow key={job.id} job={job} liveCues={liveCues} />) : (
            <article className="maintenance-summary-card">
              <strong>Routine jobs ready</strong>
              <p>{quietMode ? "Quiet mode is hiding routine maintenance." : "Only priority work is active right now."}</p>
            </article>
          )}
        </div>
      </div>
      {readyCount > 0 ? (
        <article className="quiet-jobs-row">
          <strong>{readyCount} routine jobs ready</strong>
          <p>{quietMode ? "Quiet mode is showing only priority, active, missed, or blocked work." : "Completed or low-signal maintenance is collapsed from the focus view."}</p>
        </article>
      ) : null}
    </section>
  );
}

function UpcomingJobRow({ job, allJobs, liveCues }: { job: JobRow; allJobs: JobRow[]; liveCues: LiveCueState }) {
  const category = jobCategory(job);
  const run = jobRunCells(job);
  const owner = AGENTS[job.agent_id]?.label || job.agent_id;
  const changed = Boolean(liveCues.rows[cueRowKey("job", job.id || job.title)]);
  return (
    <article className={`upcoming-job-row ${agentClass(job.agent_id)} ${categoryClass(job)} ${jobNeedsAttention(job, allJobs) ? "needs-focus" : ""}${changedRowClass(changed)}`}>
      <span className="row-change-dot" aria-hidden="true" />
      <span className={`status-dot ${jobStatusClass(job, allJobs)} ${agentClass(job.agent_id)}`} aria-hidden="true" />
      <time title={run.next}>{run.next}</time>
      <div>
        <strong title={missionText(job.title)}>{compactJobTitle(job)}</strong>
        <p title={missionText(job.detail || job.tool)}>By {owner} · {category.label} · Last {run.last}</p>
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
  return (
    <article className={`job-focus-row ${priority ? "is-priority" : ""} ${agentClass(job.agent_id)} ${categoryClass(job)}${changedRowClass(changed)}`}>
      <span className="row-change-dot" aria-hidden="true" />
      <span className={`status-dot ${jobStatusClass(job)} ${agentClass(job.agent_id)}`} aria-hidden="true" />
      <div className="job-table-title">
        <strong title={missionText(job.title)}>{compactJobTitle(job)}</strong>
        <p title={missionText(job.detail || job.tool || meta)}>{detail}</p>
      </div>
      <span className="job-table-owner" title={meta}>{compactOwnerLabel(job, count)}</span>
      <span className={`job-run-cell job-run-indicator ${run.todayClass}`}>{run.today}</span>
      <span className="job-run-cell">{run.last}</span>
      <span className="job-run-cell">{run.next}</span>
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
        <JobTableHeader />
        {groups.map((group) => {
          const latest = group.items[0];
          const run = latest ? jobRunCells(latest) : null;
          const detail = sorareGroupSummary(group);
          if (!latest) {
            return (
              <article key={group.key} className="sorare-subgroup is-empty">
                <div className="sorare-subgroup-summary is-quiet">
                  <span className="status-dot is-muted" aria-hidden="true" />
                  <div className="job-table-title">
                    <strong>{group.label}</strong>
                    <p>Waiting for the next scheduled Sorare daily job.</p>
                  </div>
                  <span className="job-table-owner">JAIMES</span>
                  <span className="job-run-cell job-run-indicator is-quiet">Untracked</span>
                  <span className="job-run-cell">Not found</span>
                  <span className="job-run-cell">Awaiting schedule</span>
                  <span className="job-status is-done">Ready</span>
                </div>
              </article>
            );
          }
          return (
            <details key={group.key} className="sorare-subgroup">
              <summary className="sorare-subgroup-summary">
                  <span className={`status-dot ${jobStatusClass(latest, group.items)} ${agentClass(latest.agent_id)}`} aria-hidden="true" />
                  <div className="job-table-title">
                    <strong title={group.label}>{group.label}</strong>
                    <p title={missionText(latest.detail || latest.tool)}>{detail}</p>
                  </div>
                  <span className="job-table-owner">JAIMES · {group.items.length}</span>
                  <span className={`job-run-cell job-run-indicator ${run?.todayClass || "is-quiet"}`}>{run?.today}</span>
                  <span className="job-run-cell">{run?.last}</span>
                  <span className="job-run-cell">{run?.next}</span>
                  <span className={`job-status ${jobStatusClass(latest, group.items)}`}>{compactJobStatus(group.items)}</span>
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
    <article className={`sorare-job-line ${agentClass(job.agent_id)} ${categoryClass(job)}${changedRowClass(changed)}`}>
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
                <article key={job.id} className={`job-row compact ${agentClass(job.agent_id)} ${categoryClass(job)}${changedRowClass(Boolean(liveCues.rows[cueRowKey("job", job.id || job.title)]))}`}>
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
        <span><DollarSign size={14} />{codexMode === "normal" ? `${fmtCurrency(modelUsage?.daily)} daily` : `Codex ${codexMode}`}</span>
      </div>
      <div className="cost-grid">
        <MetricMini label="Session" value={fmtCurrency(modelUsage?.session)} />
        <MetricMini label="Weekly" value={fmtCurrency(modelUsage?.weekly)} />
        <MetricMini label="Monthly" value={fmtCurrency(modelUsage?.monthly)} />
        <MetricMini label="Projected" value={fmtCurrency(modelUsage?.weeklyRunRate?.projectedMonthly)} />
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

function SignalFeed({ signals, quietMode, liveCues }: { signals: MissionControlState["signals"]; quietMode: boolean; liveCues: LiveCueState }) {
  const filteredSignals = quietMode
    ? signals.filter((signal) => signal.kind === "newsletter_context" || (signal.score || 0) >= 8 || freshnessTone(signal.time) === "live")
    : signals;
  const liveSignals = filteredSignals.filter((signal) => signal.section !== "newsletter_digest" && signal.kind !== "newsletter_context");
  const newsletterSignals = filteredSignals.filter((signal) => signal.section === "newsletter_digest" || signal.kind === "newsletter_context");
  const topFive = liveSignals.slice(0, 5);
  const liveFallback = !topFive.length && Boolean(newsletterSignals.length);
  const newsletterLimit = liveFallback ? 9 : Math.max(0, 10 - topFive.length);
  const lastRows = newsletterSignals.slice(0, newsletterLimit);
  const overflow = filteredSignals
    .filter((signal) => !topFive.includes(signal) && !lastRows.includes(signal))
    .slice(0, Math.max(0, 10 - topFive.length - lastRows.length - (liveFallback ? 1 : 0)));
  const groups = [
    {
      key: "top-five",
      label: "Top five",
      detail: "Developing or breaking stories",
      rows: topFive,
    },
    {
      key: "last-five",
      label: liveFallback ? "Trend 9" : "Last 5",
      detail: liveFallback ? "Newsletter and aggregate trend rows" : "Newsletter subscription trends",
      rows: lastRows.length ? lastRows : overflow,
    },
  ].filter((group) => group.rows.length);
  const dataRowCount = groups.reduce((sum, group) => sum + group.rows.length, 0) + (liveFallback ? 1 : 0);
  const reserveRows = dataRowCount ? Array.from({ length: Math.max(0, 10 - dataRowCount) }) : [];
  const visibleCount = dataRowCount + reserveRows.length;
  return (
    <section className={`signal-feed${sectionCueClass("signals", liveCues)}`}>
      <SectionCue label={liveCues.focus === "signals" ? "focus" : "updated"} />
      <div className="panel-title compact">
        <h2>Intelligence / Signal Feed</h2>
        <span><Radio size={14} />{quietMode ? "Quiet mode" : `${visibleCount} showing`} · live + newsletters</span>
      </div>
      <div className="signal-table" role="table" aria-label="Latest intelligence signals">
        {visibleCount ? (
          <>
            {liveFallback ? (
              <article className="signal-live-empty" role="row">
                <span role="cell">Live</span>
                <div className="signal-story" role="cell">
                  <strong>No live breaking stories currently above threshold</strong>
                  <p>Newsletter trends are still updating below.</p>
                </div>
                <p role="cell" className="signal-impact">No urgent breakout is ranking ahead of the newsletter digest right now.</p>
                <em role="cell" className="signal-source">Mission Control</em>
                <time role="cell">now</time>
              </article>
            ) : null}
            {groups.map((group) => (
              <React.Fragment key={group.key}>
                <div className="signal-section-label" role="row" aria-hidden="true">
                  <strong>{group.label}</strong>
                  <span>{group.detail}</span>
                </div>
                {group.rows.map((signal, index) => {
                  const strength = signalStrengthClass(signal.score);
                  const freshness = freshnessTone(signal.time);
                  const impact = signal.impact || [
                    signal.impactScenarios?.low ? `Low: ${signal.impactScenarios.low}` : "",
                    signal.impactScenarios?.medium || signal.impactScenarios?.med ? `Med: ${signal.impactScenarios.medium || signal.impactScenarios.med}` : "",
                    signal.impactScenarios?.high ? `High: ${signal.impactScenarios.high}` : "",
                  ].filter(Boolean).join(" ");
                  const ordinal = signal.rank || index + 1;
                  const changed = Boolean(liveCues.rows[cueRowKey("signal", signal.id || signal.title)]);
                  return (
                    <article key={signal.id || `${group.key}-${signal.title}-${index}`} className={`${strength} freshness-${freshness} ${signal.kind === "newsletter_context" ? "is-newsletter" : ""}${changedRowClass(changed)}`} role="row">
                      <span className="row-change-dot" aria-hidden="true" />
                      <span role="cell">{signal.score ? `${Math.round(signal.score)}/10` : String(ordinal).padStart(2, "0")}</span>
                      <div className="signal-story" role="cell">
                        <strong title={missionText(signal.title)}>{missionText(signal.title)}</strong>
                        <p title={missionText(signal.reason || signal.source)}>{missionText(signal.reason || signal.source)}</p>
                      </div>
                      <p role="cell" className="signal-impact" title={missionText(impact || "Impact summary pending")}>{missionText(impact || "Impact summary pending")}</p>
                      <em role="cell" className="signal-source" title={signal.source || signal.label || "Signal"}>{signal.source || signal.label || "Signal"}</em>
                      <time role="cell" dateTime={signal.time || undefined}>
                        <b>{freshnessLabel(signal.time)}</b>
                        {fmtTime(signal.time)}
                      </time>
                    </article>
                  );
                })}
              </React.Fragment>
            ))}
            {reserveRows.map((_, index) => (
              <article key={`signal-reserve-${index}`} className="signal-reserve-row freshness-live" role="row">
                <span role="cell">Watch</span>
                <div className="signal-story" role="cell">
                  <strong>No additional trend above threshold</strong>
                  <p>Reserved space for the next live or newsletter signal.</p>
                </div>
                <p role="cell" className="signal-impact">Mission Control is monitoring; this row will be replaced when a stronger signal arrives.</p>
                <em role="cell" className="signal-source">Signal coverage</em>
                <time role="cell">standby</time>
              </article>
            ))}
          </>
        ) : <EmptyRow title="No signal rows" detail="J.A.I.N intelligence feed will appear here." />}
      </div>
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
    objective: "No current Mission Control status has been published yet",
    detail: "This agent has not reported a dashboard-safe status row.",
    current_tool: "",
    active: false,
    updated_at: "",
    steps: [],
  };
}

createRoot(document.getElementById("root")!).render(<App />);
