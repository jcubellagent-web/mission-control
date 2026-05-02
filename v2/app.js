const AGENTS = {
  joshex: "JOSHeX",
  josh: "Josh 2.0",
  jaimes: "JAIMES",
  jain: "J.A.I.N",
};

const CONFIG = window.MC_V2_CONFIG || {
  supabaseUrl: "",
  supabaseKey: "",
};

const state = {
  selectedAgent: "joshex",
  source: "loading",
  statuses: [],
  events: [],
  approvals: [],
};

const $ = (id) => document.getElementById(id);

function fmtTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function normalizeStatus(row) {
  if (!row) return null;
  if (row.agent_id) return row;
  return {
    agent_id: "joshex",
    status: row.status || "info",
    objective: row.objective || "No objective reported",
    detail: row.detail || row.summary || "",
    current_tool: row.currentTool || row.current_tool || "",
    active: Boolean(row.active),
    updated_at: row.updatedAt || row.checkedAt || row.updated_at,
    steps: row.steps || [],
  };
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

async function supabaseFetch(path) {
  const response = await fetch(`${CONFIG.supabaseUrl}/rest/v1/${path}`, {
    headers: {
      apikey: CONFIG.supabaseKey,
      Authorization: `Bearer ${CONFIG.supabaseKey}`,
    },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Supabase ${path}: ${response.status}`);
  return response.json();
}

async function loadFromSupabase() {
  const [statuses, events, approvals] = await Promise.all([
    supabaseFetch("mc_v2_agent_status?select=*&order=updated_at.desc"),
    supabaseFetch("mc_v2_events?select=*&order=created_at.desc&limit=20"),
    supabaseFetch("mc_v2_approvals?select=*&status=eq.pending&order=created_at.desc&limit=20"),
  ]);
  state.source = "Supabase v2";
  state.statuses = statuses.map(normalizeStatus).filter(Boolean);
  state.events = events;
  state.approvals = approvals;
}

async function loadFallback() {
  const [brain, personal, dashboard] = await Promise.all([
    fetchJson("../data/brain-feed.json").catch(() => null),
    fetchJson("../data/personal-codex.json").catch(() => null),
    fetchJson("../data/dashboard-data.json").catch(() => null),
  ]);
  const rows = [
    normalizeStatus(brain),
    normalizeStatus({
      agent_id: "joshex",
      status: personal?.status || "info",
      objective: personal?.objective || "Personal Codex",
      detail: personal?.summary || "",
      current_tool: "local sidecar",
      active: personal?.status === "active",
      updated_at: personal?.updatedAt,
      steps: (personal?.recentActivity || []).slice(0, 5).map((item) => ({
        label: item.event,
        status: "done",
        tool: "personal-codex.json",
      })),
    }),
    normalizeStatus(dashboard?.jaimesBrainFeed && { ...dashboard.jaimesBrainFeed, agent_id: "jaimes" }),
    normalizeStatus(dashboard?.jainBrainFeed && { ...dashboard.jainBrainFeed, agent_id: "jain" }),
  ].filter(Boolean);
  state.source = "Local v1 fallback";
  state.statuses = dedupeStatus(rows);
  state.events = (dashboard?.recentActivity || []).slice(0, 12).map((event, index) => ({
    id: `fallback-${index}`,
    title: event.event || event.title || "Recent activity",
    detail: event.detail || "",
    created_at: event.time || dashboard?.generatedAt,
    agent_id: "joshex",
  }));
  state.approvals = (dashboard?.actionRequired || []).slice(0, 8).map((item, index) => ({
    id: `approval-${index}`,
    title: item.title,
    detail: item.priority || "",
    created_at: dashboard?.generatedAt,
  }));
}

function dedupeStatus(rows) {
  const byAgent = new Map();
  for (const row of rows) {
    if (!byAgent.has(row.agent_id)) byAgent.set(row.agent_id, row);
  }
  return [...byAgent.values()];
}

async function loadData() {
  if (CONFIG.supabaseUrl && CONFIG.supabaseKey) {
    try {
      await loadFromSupabase();
      return;
    } catch (error) {
      console.warn(error);
    }
  }
  await loadFallback();
}

function selectedStatus() {
  return state.statuses.find((row) => row.agent_id === state.selectedAgent)
    || {
      agent_id: state.selectedAgent,
      status: "offline",
      objective: "No v2 state has been published yet",
      detail: "Run scripts/mc_v2_publish.py after the v2 Supabase schema is installed.",
      current_tool: "",
      active: false,
      updated_at: "",
      steps: [],
    };
}

function renderSummary() {
  const active = state.statuses.filter((row) => row.active || row.status === "active").length;
  const attention = state.approvals.length + state.statuses.filter((row) => ["blocked", "error"].includes(row.status)).length;
  const last = state.statuses.map((row) => row.updated_at).filter(Boolean).sort().pop();
  $("sourceLabel").textContent = state.source;
  $("activeCount").textContent = String(active);
  $("attentionCount").textContent = String(attention);
  $("lastUpdate").textContent = fmtTime(last);
}

function renderAgent() {
  const row = selectedStatus();
  $("agentName").textContent = AGENTS[state.selectedAgent] || row.agent_id;
  $("agentObjective").textContent = row.objective || "No objective reported";
  $("agentDetail").textContent = row.detail || "";
  const badge = $("agentStatus");
  badge.textContent = row.status || "info";
  badge.className = `status-badge ${row.status || "info"}`;
  const steps = Array.isArray(row.steps) ? row.steps.slice(0, 8) : [];
  $("stepList").innerHTML = steps.length
    ? steps.map((step) => `
      <div class="step-row">
        <span class="step-status">${escapeHtml(step.status || "info")}</span>
        <span class="step-label">${escapeHtml(step.label || step.title || "Step")}</span>
        <span class="step-tool">${escapeHtml(step.tool || step.kind || "")}</span>
      </div>
    `).join("")
    : `<div class="step-row"><span class="step-status">idle</span><span class="step-label">No steps reported</span><span class="step-tool"></span></div>`;
}

function renderLists() {
  $("approvalCount").textContent = String(state.approvals.length);
  $("eventCount").textContent = String(state.events.length);
  $("approvalList").innerHTML = renderCompactList(state.approvals, "No pending approvals");
  $("eventList").innerHTML = renderCompactList(state.events, "No recent events");
}

function renderCompactList(items, emptyText) {
  if (!items.length) {
    return `<div class="compact-item"><strong>${emptyText}</strong><span>v2 will show canonical rows here.</span></div>`;
  }
  return items.slice(0, 8).map((item) => `
    <div class="compact-item">
      <strong>${escapeHtml(item.title || "Untitled")}</strong>
      <span>${escapeHtml(item.detail || item.agent_id || "")}${item.created_at ? ` · ${fmtTime(item.created_at)}` : ""}</span>
    </div>
  `).join("");
}

function renderTabs() {
  document.querySelectorAll(".agent-tab").forEach((button) => {
    button.classList.toggle("selected", button.dataset.agent === state.selectedAgent);
  });
}

function render() {
  renderSummary();
  renderTabs();
  renderAgent();
  renderLists();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

async function refresh() {
  $("sourceLabel").textContent = "Loading";
  await loadData();
  render();
}

document.querySelectorAll(".agent-tab").forEach((button) => {
  button.addEventListener("click", () => {
    state.selectedAgent = button.dataset.agent;
    render();
  });
});

$("refreshButton").addEventListener("click", refresh);
refresh();
