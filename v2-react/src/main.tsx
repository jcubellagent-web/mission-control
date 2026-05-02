import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Activity, AlertTriangle, CheckCircle2, ClipboardCheck, DollarSign, GitBranch, Radio, RefreshCw, ShieldCheck, Timer, UserRoundCheck } from "lucide-react";
import { loadMissionControl } from "./data";
import type { AgentId, AgentStatus, MissionControlState } from "./types";
import "./styles.css";

const AGENTS: Record<AgentId, { label: string; role: string }> = {
  joshex: { label: "JOSHeX", role: "Private coordination" },
  josh: { label: "Josh 2.0", role: "Host operations" },
  jaimes: { label: "JAIMES", role: "Hermes reports" },
  jain: { label: "J.A.I.N", role: "Monitors" },
};

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

function statusClass(status?: string) {
  if (status === "active" || status === "queued") return "is-active";
  if (status === "blocked" || status === "error") return "is-risk";
  if (status === "done" || status === "ready" || status === "approved" || status === "ok") return "is-done";
  return "is-muted";
}

function timeValue(value?: string | null): number {
  if (!value) return 0;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function agentClass(agent: AgentId) {
  return `agent-${agent}`;
}

function App() {
  const [state, setState] = useState<MissionControlState>(EMPTY_STATE);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    setLoading(true);
    const next = await loadMissionControl();
    setState(next);
    setLoading(false);
  }

  useEffect(() => {
    refresh();
  }, []);

  const statusByAgent = useMemo(() => {
    return new Map(state.statuses.map((row) => [row.agent_id, row]));
  }, [state.statuses]);

  const activeCount = state.statuses.filter((row) => row.active || row.status === "active").length;
  const attentionCount = state.approvals.filter((row) => row.status === "pending").length
    + state.statuses.filter((row) => row.status === "blocked" || row.status === "error").length;
  const lastUpdate = [...state.statuses.map((row) => row.updated_at), ...state.events.map((row) => row.created_at)]
    .filter(Boolean)
    .sort()
    .pop();

  return (
    <main className="app-shell hero-shell">
      <header className="mission-header">
        <div className="brand-lockup">
          <img src="/assets/josh-headshot.jpg" alt="" />
          <div>
            <h1>Josh 2.0 | Mission Control</h1>
            <p>Brain Feed command view for the agent ecosystem</p>
          </div>
        </div>
        <div className="mission-actions">
          <span className="source-chip"><ShieldCheck size={15} />{state.source}</span>
          <span className="source-chip live-chip">Live • 10s</span>
          <button type="button" onClick={refresh} aria-label="Refresh">
            <RefreshCw size={16} className={loading ? "spin" : ""} />
          </button>
          <a href="/index.html">v1</a>
        </div>
      </header>

      <section className="status-ribbon" aria-label="Mission Control summary">
        <Metric icon={<Activity size={18} />} label="Active" value={String(activeCount)} />
        <Metric icon={<AlertTriangle size={18} />} label="Action" value={String(attentionCount)} />
        <Metric icon={<ClipboardCheck size={18} />} label="Jobs" value={String(state.jobs.length)} />
        <Metric icon={<DollarSign size={18} />} label="Daily Spend" value={fmtCurrency(state.modelUsage?.daily)} />
        <Metric icon={<Timer size={18} />} label="Updated" value={fmtTime(lastUpdate)} wide />
      </section>

      <section className="kiosk-grid">
        <section className="brain-hero-panel">
          <BrainHero state={state} statuses={statusByAgent} />
          <section className="support-grid">
            <SignalFeed signals={state.signals} />
            <ModelUsageCard modelUsage={state.modelUsage} />
          </section>
        </section>
        <aside className="right-rail">
          <MissionTimeline events={state.events} jobs={state.jobs} approvals={state.approvals} signals={state.signals} />
          <JobsRail jobs={state.jobs} />
        </aside>
      </section>
    </main>
  );
}

function BrainHero({
  state,
  statuses,
}: {
  state: MissionControlState;
  statuses: Map<AgentId, AgentStatus>;
}) {
  const { events, approvals, signals } = state;
  const heroAgents: AgentId[] = ["joshex", "josh", "jaimes"];
  const featuredEvents = events.slice(0, 6);
  const pendingApprovals = approvals.filter((row) => row.status === "pending");
  return (
    <section className="brain-hero" aria-label="Brain Feed">
      <div className="brain-hero-title">
        <div>
          <p>Live agent updates</p>
          <h2>Brain Feed</h2>
        </div>
        <span>{featuredEvents.length} recent updates</span>
      </div>

      <div className="brain-agent-grid">
        {heroAgents.map((agent) => {
          const status = statuses.get(agent) || offlineStatus(agent);
          return <AgentHeroCard key={agent} agent={agent} status={status} />;
        })}
      </div>

      <div className="approval-strip" aria-label="Current approvals and signal">
        <span className={pendingApprovals.length ? "attention-chip is-risk" : "attention-chip is-done"}>
          {pendingApprovals.length ? `${pendingApprovals.length} approvals pending` : "No approval blockers"}
        </span>
        <span className="attention-chip">{signals.length} signal rows</span>
        <span className="attention-chip">{events.length} total feed rows</span>
      </div>

      <div className="brain-context-grid">
        <MissionHealthPanel state={state} />
        <AgentEcosystemMap statuses={statuses} />
      </div>

      <div className="brain-event-grid">
        {featuredEvents.length ? featuredEvents.map((event) => (
          <article key={event.id} className="brain-event-card">
            <header>
              <span className={`dot ${statusClass(event.status)}`} />
              <strong>{event.title}</strong>
              <time>{fmtTime(event.created_at)}</time>
            </header>
            <p>{event.detail || event.tool || event.event_type}</p>
            <footer>
              <span>{AGENTS[event.agent_id]?.label || event.agent_id}</span>
              <span>{event.tool || event.event_type}</span>
            </footer>
          </article>
        )) : <EmptyRow title="No Brain Feed rows yet" detail="Dashboard-safe agent updates will appear here." />}
      </div>
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
    ...events.map((event) => ({ id: event.id, time: event.created_at, type: "update", agent: event.agent_id, label: event.title })),
    ...jobs.map((job) => ({ id: job.id, time: job.updated_at, type: job.status === "error" || job.status === "blocked" ? "risk" : "job", agent: job.agent_id, label: job.title })),
    ...approvals.map((approval) => ({ id: approval.id, time: approval.created_at, type: "handoff", agent: approval.agent_id, label: approval.title })),
    ...signals.map((signal) => ({ id: signal.id, time: signal.time, type: "signal", agent: "jain" as AgentId, label: signal.title })),
  ]
    .map((row) => ({ ...row, at: timeValue(row.time) }))
    .filter((row) => Number.isFinite(row.at) && row.at > now - windowMs)
    .sort((a, b) => a.at - b.at)
    .slice(-28);

  return (
    <section className="mission-timeline calendar-card" aria-label="Mission timeline last 24 hours">
      <header>
        <strong>24h Calendar</strong>
        <span>{jobs.length} jobs mapped · {rows.length} events</span>
      </header>
      <div className="calendar-scale" aria-hidden="true">
        {scaleTicks.map((tick) => <span key={tick.label} style={{ left: `${tick.left}%` }}>{tick.label}</span>)}
      </div>
      <div className="timeline-track calendar-track">
        {rows.map((row) => {
          const left = Math.max(0, Math.min(100, ((row.at - (now - windowMs)) / windowMs) * 100));
          return (
            <span
              key={`${row.type}-${row.id}`}
              className={`timeline-mark is-${row.type} ${agentClass(row.agent)}`}
              style={{ left: `${left}%` }}
              title={`${row.label} · ${fmtTime(row.time)}`}
            />
          );
        })}
      </div>
      <footer>
        <span><i className="agent-joshex" />JOSHeX</span>
        <span><i className="agent-josh" />Josh</span>
        <span><i className="agent-jaimes" />JAIMES</span>
        <span><i className="agent-jain" />J.A.I.N</span>
      </footer>
    </section>
  );
}

function MissionHealthPanel({ state }: { state: MissionControlState }) {
  const pendingApprovals = state.approvals.filter((row) => row.status === "pending").length;
  const riskJobs = state.jobs.filter((job) => job.status === "blocked" || job.status === "error").length;
  const activeAgents = state.statuses.filter((row) => row.active || row.status === "active").length;
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
    { label: "Cost signal", value: costScore },
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

function AgentHeroCard({ agent, status }: { agent: AgentId; status: AgentStatus }) {
  return (
    <article className="agent-hero-card">
      <header>
        <span className={`dot ${statusClass(status.status)}`} />
        <strong>{AGENTS[agent].label}</strong>
        <em>{status.status}</em>
      </header>
      <h3>{status.objective}</h3>
      <p>{status.detail || AGENTS[agent].role}</p>
    </article>
  );
}

function Metric({ icon, label, value, wide = false }: { icon: React.ReactNode; label: string; value: string; wide?: boolean }) {
  return (
    <article className={wide ? "metric is-wide" : "metric"}>
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
          <h2>{selectedStatus.objective}</h2>
        </div>
        <span className={`status-pill ${statusClass(selectedStatus.status)}`}>{selectedStatus.status}</span>
      </div>
      <p className="objective-detail">{selectedStatus.detail || "No detail reported."}</p>
      <div className="timeline">
        {events.slice(0, 10).map((event) => (
          <article key={event.id} className="timeline-row">
            <span className={`rail ${statusClass(event.status)}`} />
            <div>
              <header>
                <strong>{event.title}</strong>
                <time>{fmtTime(event.created_at)}</time>
              </header>
              <p>{event.detail || event.tool || event.event_type}</p>
              <footer>
                <span>{AGENTS[event.agent_id]?.label || event.agent_id}</span>
                <span>{event.event_type}</span>
                <span>{event.tool || "v2"}</span>
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
  const risk = items.filter((job) => job.status === "blocked" || job.status === "error").length;
  if (risk) return `${risk} need attention`;
  if (active) return `${active} active`;
  return `${items.length} tracked`;
}

function JobsRail({ jobs }: { jobs: MissionControlState["jobs"] }) {
  const [view, setView] = useState<"timeline" | "categories">("timeline");
  const groups = groupedJobs(jobs, "category");
  return (
    <aside className="jobs-rail">
      <div className="panel-title compact">
        <h2>Today's Jobs</h2>
        <span>{jobs.length} jobs · {groups.length || 0} groups</span>
      </div>
      <div className="jobs-view-toggle" aria-label="Today jobs view">
        <button type="button" className={view === "timeline" ? "selected" : ""} onClick={() => setView("timeline")}>6h timeline</button>
        <button type="button" className={view === "categories" ? "selected" : ""} onClick={() => setView("categories")}>Categories</button>
      </div>
      <div className="job-list">
        {view === "timeline" ? <JobTimelineView jobs={jobs} /> : <JobCategoryView groups={groups} />}
      </div>
    </aside>
  );
}

function JobTimelineView({ jobs }: { jobs: JobRow[] }) {
  const now = Date.now();
  const windowMs = 6 * 60 * 60 * 1000;
  const start = now - windowMs;
  const ticks = [
    { label: "-6h", left: 0 },
    { label: "-4h", left: 33.3 },
    { label: "-2h", left: 66.6 },
    { label: "Now", left: 100 },
  ];
  const ordered = [...jobs].sort((a, b) => timeValue(b.updated_at) - timeValue(a.updated_at));

  return (
    <section className="job-timeline-view">
      <div className="job-calendar-header">
        {ticks.map((tick) => <span key={tick.label} style={{ left: `${tick.left}%` }}>{tick.label}</span>)}
      </div>
      {ordered.length ? ordered.map((job) => {
        const at = timeValue(job.updated_at);
        const left = Math.max(0, Math.min(100, ((at - start) / windowMs) * 100));
        const category = jobCategory(job);
        const outsideWindow = at < start;
        return (
          <article key={job.id} className={`job-timeline-row ${agentClass(job.agent_id)} ${categoryClass(job)}`}>
            <div className="job-row-main">
              <span className={`status-dot ${statusClass(job.status)} ${agentClass(job.agent_id)}`} aria-hidden="true" />
              <div>
                <strong title={job.title}>{job.title}</strong>
                <p>{AGENTS[job.agent_id]?.label || job.agent_id} · {category.label}</p>
              </div>
              <span className={`job-status ${statusClass(job.status)}`}>{job.status}</span>
            </div>
            <div className="job-calendar-line" title={`${fmtTime(job.updated_at)} · ${job.detail || job.tool}`}>
              <span className={`job-window-fill ${agentClass(job.agent_id)}`} style={{ width: outsideWindow ? "2%" : `${left}%` }} />
              <i style={{ left: `${left}%` }} />
            </div>
          </article>
        );
      }) : <EmptyRow title="No v2 jobs yet" detail="JAIMES jobs will appear here." />}
    </section>
  );
}

function JobCategoryView({ groups }: { groups: Array<{ key: string; label: string; items: JobRow[] }> }) {
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
            {group.items.map((job) => (
              <article key={job.id} className={`job-row compact ${agentClass(job.agent_id)} ${categoryClass(job)}`}>
                <span className={`status-dot ${statusClass(job.status)} ${agentClass(job.agent_id)}`} aria-hidden="true" />
                <div>
                  <strong title={job.title}>{job.title}</strong>
                  <p title={job.detail || job.tool}>{job.detail || job.tool || AGENTS[job.agent_id]?.label}</p>
                </div>
                <span className={`job-status ${statusClass(job.status)}`}>{job.status}</span>
                <time>{fmtTime(job.updated_at)}</time>
              </article>
            ))}
          </div>
        </details>
      )) : <EmptyRow title="No v2 jobs yet" detail="JAIMES jobs will appear here." />}
    </>
  );
}

function ModelUsageCard({ modelUsage }: { modelUsage?: MissionControlState["modelUsage"] }) {
  const topModels = modelUsage?.breakdown?.length ? modelUsage.breakdown : modelUsage?.topModels || [];
  return (
    <section className="model-usage-card">
      <div className="panel-title compact">
        <h2>Model Cost & Usage</h2>
        <span><DollarSign size={14} />{fmtCurrency(modelUsage?.daily)} daily</span>
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
      <footer className="card-footer">Updated {fmtTime(modelUsage?.lastUpdated)}</footer>
    </section>
  );
}

function SignalFeed({ signals }: { signals: MissionControlState["signals"] }) {
  return (
    <section className="signal-feed">
      <div className="panel-title compact">
        <h2>Intelligence / Signal Feed</h2>
        <span><Radio size={14} />{signals.length} signals</span>
      </div>
      <div className="signal-list">
        {signals.length ? signals.slice(0, 7).map((signal) => (
          <article key={signal.id}>
            <header>
              <span>{signal.label}</span>
              <em>{signal.score ? `${Math.round(signal.score)}/10` : fmtTime(signal.time)}</em>
            </header>
            <strong>{signal.title}</strong>
            <p>{signal.reason}</p>
            <footer>{signal.source}</footer>
          </article>
        )) : <EmptyRow title="No signal rows" detail="J.A.I.N intelligence feed will appear here." />}
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

function offlineStatus(agent: AgentId): AgentStatus {
  return {
    agent_id: agent,
    status: "offline",
    objective: "No v2 state has been published yet",
    detail: "This agent has not reported a dashboard-safe v2 row.",
    current_tool: "",
    active: false,
    updated_at: "",
    steps: [],
  };
}

createRoot(document.getElementById("root")!).render(<App />);
