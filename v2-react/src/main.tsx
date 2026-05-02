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
  if (status === "done" || status === "ready" || status === "approved") return "is-done";
  return "is-muted";
}

function App() {
  const [selectedAgent, setSelectedAgent] = useState<AgentId>("jaimes");
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

  const selectedStatus = useMemo(() => {
    return state.statuses.find((row) => row.agent_id === selectedAgent) || offlineStatus(selectedAgent);
  }, [selectedAgent, state.statuses]);

  const activeCount = state.statuses.filter((row) => row.active || row.status === "active").length;
  const attentionCount = state.approvals.filter((row) => row.status === "pending").length
    + state.statuses.filter((row) => row.status === "blocked" || row.status === "error").length;
  const lastUpdate = [...state.statuses.map((row) => row.updated_at), ...state.events.map((row) => row.created_at)]
    .filter(Boolean)
    .sort()
    .pop();

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>Mission Control v2</h1>
          <p>Agent operations console</p>
        </div>
        <div className="topbar-actions">
          <span className="source-chip"><ShieldCheck size={15} />{state.source}</span>
          <button type="button" onClick={refresh} aria-label="Refresh">
            <RefreshCw size={16} className={loading ? "spin" : ""} />
          </button>
          <a href="/index.html">v1</a>
        </div>
      </header>

      <section className="metric-strip" aria-label="Mission Control summary">
        <Metric icon={<Activity size={18} />} label="Active" value={String(activeCount)} />
        <Metric icon={<AlertTriangle size={18} />} label="Attention" value={String(attentionCount)} />
        <Metric icon={<ClipboardCheck size={18} />} label="Jobs" value={String(state.jobs.length)} />
        <Metric icon={<Timer size={18} />} label="Last Update" value={fmtTime(lastUpdate)} wide />
      </section>

      <HeroAgents
        statuses={state.statuses}
        selected={selectedAgent}
        onSelect={setSelectedAgent}
      />

      <section className="command-grid">
        <AgentRail selected={selectedAgent} statuses={state.statuses} onSelect={setSelectedAgent} />
        <BrainFeed events={state.events} selectedStatus={selectedStatus} />
        <JobsRail jobs={state.jobs} />
        <ModelUsageCard modelUsage={state.modelUsage} />
        <SignalFeed signals={state.signals} />
        <PipelineCard status={selectedStatus} />
        <ApprovalInbox approvals={state.approvals} />
      </section>
    </main>
  );
}

function HeroAgents({
  statuses,
  selected,
  onSelect,
}: {
  statuses: AgentStatus[];
  selected: AgentId;
  onSelect: (agent: AgentId) => void;
}) {
  const heroAgents: AgentId[] = ["josh", "jaimes", "jain"];
  return (
    <section className="agent-hero-grid" aria-label="Agent hero objectives">
      {heroAgents.map((agent) => {
        const status = statuses.find((row) => row.agent_id === agent) || offlineStatus(agent);
        const detail = status.detail || AGENTS[agent].role;
        return (
          <button
            key={agent}
            type="button"
            className={selected === agent ? "agent-hero-card selected" : "agent-hero-card"}
            onClick={() => onSelect(agent)}
          >
            <header>
              <span className={`dot ${statusClass(status.status)}`} />
              <strong>{AGENTS[agent].label}</strong>
              <em>{status.status}</em>
            </header>
            <h2>{status.objective}</h2>
            <p>{detail}</p>
            <footer>
              <span>{status.current_tool || "v2 status"}</span>
              <time>{fmtTime(status.updated_at)}</time>
            </footer>
          </button>
        );
      })}
    </section>
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

function JobsRail({ jobs }: { jobs: MissionControlState["jobs"] }) {
  return (
    <aside className="jobs-rail">
      <div className="panel-title compact">
        <h2>Today's Jobs</h2>
        <span>{jobs.length}</span>
      </div>
      <div className="job-list">
        {jobs.length ? jobs.slice(0, 9).map((job) => (
          <article key={job.id} className="job-row">
            <span className={`status-pill ${statusClass(job.status)}`}>{job.status}</span>
            <div>
              <strong>{job.title}</strong>
              <p>{job.detail}</p>
            </div>
            <time>{fmtTime(job.updated_at)}</time>
          </article>
        )) : <EmptyRow title="No v2 jobs yet" detail="JAIMES jobs will appear here." />}
      </div>
    </aside>
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
