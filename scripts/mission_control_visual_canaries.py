#!/usr/bin/env python3
"""Generate lightweight Mission Control visual/data canaries.

These are dashboard-facing guardrails: they summarize whether the pieces Josh
actually looks at are intact before a human has to notice a visual regression.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DASHBOARD_PATH = DATA_DIR / "dashboard-data.json"
OUT_PATH = DATA_DIR / "mission-control-canaries.json"
INDEX_PATH = ROOT / "index.html"
V2_MAIN_PATH = ROOT / "v2-react" / "src" / "main.tsx"
V2_STYLES_PATH = ROOT / "v2-react" / "src" / "styles.css"
V2_DATA_PATH = ROOT / "v2-react" / "src" / "data.ts"
V2_PRIORITY_JOBS_PATH = ROOT / "v2-react" / "src" / "priorityJobs.ts"
V2_DATA_ADAPTERS_PATH = ROOT / "v2-react" / "src" / "dataAdapters.ts"
V2_INDEX_PATH = ROOT / "v2-react" / "index.html"
V2_FAVICON_PATH = ROOT / "v2-react" / "public" / "favicon.svg"
UPDATE_SCRIPT_PATH = ROOT / "scripts" / "update_mission_control.py"
KIOSK_WATCHDOG_PATH = ROOT / "scripts" / "mission_control_kiosk_watchdog.py"
KIOSK_WATCHDOG_PLIST_PATH = ROOT / "launchagents" / "com.josh20.mission-control-kiosk-watchdog.plist"
KIOSK_SERVER_PATH = ROOT / "scripts" / "react_kiosk_server.mjs"
STATE_VISIBILITY_GUARD_PATH = ROOT / "scripts" / "state_visibility_guard.py"
RUNTIME_LAYOUT_CHECK_PATH = ROOT / "scripts" / "mission_control_runtime_layout_check.py"
SCREENSHOT_DIFF_PATH = ROOT / "scripts" / "mission_control_screenshot_diff.py"
RUN_WATCHDOG_PATH = ROOT / "scripts" / "run_mission_control_watchdog.sh"
JOSH_VISIBILITY_HEARTBEAT_PATH = ROOT / "scripts" / "josh_visibility_heartbeat.py"
RUNTIME_LAYOUT_STATUS_PATH = DATA_DIR / "mission-control-runtime-layout.json"
SIGNALS_PATH = DATA_DIR / "jain-daily-signals.json"
NEWSLETTER_TRENDS_PATH = DATA_DIR / "jain-newsletter-trends.json"
SIGNAL_HEALTH_PATH = DATA_DIR / "jain-signal-health.json"
BUILD_SIGNALS_PATH = ROOT / "scripts" / "build_jain_daily_signals.py"
AGENTIC_CRYPTO_PATH = DATA_DIR / "agentic-crypto-wallet.json"
RAW_JOB_LABEL_TOKENS = (
    "jaimes-model-efficiency-guard",
    "jaimes-ops-drift-check",
    "jaimes-brain-feed-self-test",
    "jaimes-brain-feed-stale-alert",
    "sorare_canonical_reflector",
    "sorare canonical reflector",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def status(ok: bool, name: str, detail: str, severity: str = "high") -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "detail": detail,
        "severity": "ok" if ok else severity,
    }


def main() -> int:
    data = load_json(DASHBOARD_PATH, {})
    html = INDEX_PATH.read_text(errors="replace") if INDEX_PATH.exists() else ""
    react_main = V2_MAIN_PATH.read_text(errors="replace") if V2_MAIN_PATH.exists() else ""
    react_styles = V2_STYLES_PATH.read_text(errors="replace") if V2_STYLES_PATH.exists() else ""
    react_data = V2_DATA_PATH.read_text(errors="replace") if V2_DATA_PATH.exists() else ""
    priority_jobs = V2_PRIORITY_JOBS_PATH.read_text(errors="replace") if V2_PRIORITY_JOBS_PATH.exists() else ""
    data_adapters = V2_DATA_ADAPTERS_PATH.read_text(errors="replace") if V2_DATA_ADAPTERS_PATH.exists() else ""
    v2_index = V2_INDEX_PATH.read_text(errors="replace") if V2_INDEX_PATH.exists() else ""
    update_script = UPDATE_SCRIPT_PATH.read_text(errors="replace") if UPDATE_SCRIPT_PATH.exists() else ""
    kiosk_watchdog = KIOSK_WATCHDOG_PATH.read_text(errors="replace") if KIOSK_WATCHDOG_PATH.exists() else ""
    kiosk_watchdog_plist = KIOSK_WATCHDOG_PLIST_PATH.read_text(errors="replace") if KIOSK_WATCHDOG_PLIST_PATH.exists() else ""
    kiosk_server = KIOSK_SERVER_PATH.read_text(errors="replace") if KIOSK_SERVER_PATH.exists() else ""
    state_guard = STATE_VISIBILITY_GUARD_PATH.read_text(errors="replace") if STATE_VISIBILITY_GUARD_PATH.exists() else ""
    runtime_layout_check = RUNTIME_LAYOUT_CHECK_PATH.read_text(errors="replace") if RUNTIME_LAYOUT_CHECK_PATH.exists() else ""
    screenshot_diff = SCREENSHOT_DIFF_PATH.read_text(errors="replace") if SCREENSHOT_DIFF_PATH.exists() else ""
    run_watchdog = RUN_WATCHDOG_PATH.read_text(errors="replace") if RUN_WATCHDOG_PATH.exists() else ""
    josh_visibility_heartbeat = JOSH_VISIBILITY_HEARTBEAT_PATH.read_text(errors="replace") if JOSH_VISIBILITY_HEARTBEAT_PATH.exists() else ""
    build_signals = BUILD_SIGNALS_PATH.read_text(errors="replace") if BUILD_SIGNALS_PATH.exists() else ""
    daily_signals = load_json(SIGNALS_PATH, {})
    newsletter_trends = load_json(NEWSLETTER_TRENDS_PATH, {})
    signal_health = load_json(SIGNAL_HEALTH_PATH, {})
    agentic_crypto = load_json(AGENTIC_CRYPTO_PATH, {})
    runtime_layout = load_json(RUNTIME_LAYOUT_STATUS_PATH, {})
    dashboard_text = json.dumps(data, ensure_ascii=False)
    agent_context_text = json.dumps(data.get("agentContextRegistry", {}), ensure_ascii=False)
    raw_job_leaks = sorted(
        token for token in RAW_JOB_LABEL_TOKENS
        if token in dashboard_text or token in agent_context_text
    )
    cron_manifest = update_script.split("# ── Sorare MLB", 1)[0]
    render_source = "\n".join([html, react_main, react_styles])

    live = data.get("liveObjectives") if isinstance(data.get("liveObjectives"), dict) else {}
    dual_agents = live.get("dualAgents") or []
    live_agents = live.get("agents") if isinstance(live.get("agents"), list) else []
    primary_agent = live.get("primaryAgent")
    agent_feeds = data.get("agentBrainFeeds") if isinstance(data.get("agentBrainFeeds"), dict) else {}
    josh_feed = agent_feeds.get("josh") if isinstance(agent_feeds.get("josh"), dict) else {}
    jaimes_feed = agent_feeds.get("jaimes") if isinstance(agent_feeds.get("jaimes"), dict) else {}
    calendar = data.get("calendarHealth") if isinstance(data.get("calendarHealth"), dict) else {}
    crons = data.get("crons") if isinstance(data.get("crons"), list) else []
    today = [c for c in crons if c and c.get("todayRelevant") is not False]
    active_crons = [
        c for c in crons
        if str(c.get("runStatus") or c.get("status") or "").lower() in ("active", "running", "queued")
    ]
    active_errors = [
        c for c in today
        if c.get("status") != "paused"
        and c.get("source") != "codex_automation"
        and ((c.get("errors") or 0) > 0 or c.get("runStatus") == "missed")
    ]
    action_required_raw = data.get("actionRequired") if isinstance(data.get("actionRequired"), list) else []
    personal_codex = data.get("personalCodex") if isinstance(data.get("personalCodex"), dict) else {}
    agent_control = data.get("agentControl") if isinstance(data.get("agentControl"), dict) else {}
    agent_control_summary = agent_control.get("summary") if isinstance(agent_control.get("summary"), dict) else {}
    agent_control_ts = parse_ts(agent_control.get("generatedAt") or agent_control.get("updatedAt"))
    agent_control_current = bool(
        agent_control_ts
        and (datetime.now(timezone.utc) - agent_control_ts).total_seconds() <= 6 * 3600
    )
    signal_items = daily_signals.get("items") if isinstance(daily_signals.get("items"), list) else []
    newsletter_items = newsletter_trends.get("items") if isinstance(newsletter_trends.get("items"), list) else []
    daily_newsletter_titles = [
        str(row.get("title") or "")
        for row in signal_items
        if isinstance(row, dict) and row.get("kind") == "newsletter_context"
    ]
    generic_newsletter_titles = [
        title for title in daily_newsletter_titles
        if title.strip().lower() in {"crypto watch: crypto", "ai & chips watch: ai"}
    ]
    generic_source_headlines = [
        str(row.get("title") or "")
        for row in signal_items
        if isinstance(row, dict)
        and str(row.get("source") or "").strip().lower() in {"ofac recent actions", "us treasury"}
        and str(row.get("title") or "").strip().lower().rstrip(" —-")
        in {"designations removals", "sanctions list updates"}
    ]
    signal_counts = signal_health.get("counts") if isinstance(signal_health.get("counts"), dict) else {}
    crypto_summary = agentic_crypto.get("summary") if isinstance(agentic_crypto.get("summary"), dict) else {}
    crypto_wallets = agentic_crypto.get("wallets") if isinstance(agentic_crypto.get("wallets"), dict) else {}
    crypto_tokens = agentic_crypto.get("tokens") if isinstance(agentic_crypto.get("tokens"), list) else []
    crypto_chains = agentic_crypto.get("chains") if isinstance(agentic_crypto.get("chains"), list) else []
    action_required = []
    x_posting_job_names = {
        "X Pre-Market",
        "X Market Open",
        "X Mover",
        "X Hot Take",
        "X Quote Tweets",
        "X Market Close",
        "X Prime Take",
        "X Nightcap",
        "X Strategic Replies",
    }
    x_posting_jobs = [row for row in crons if row.get("name") in x_posting_job_names]
    for item in action_required_raw:
        title = str(item.get("title", "")).lower()
        if "mission control canary issue" in title:
            continue
        if "due/unverified" in title:
            continue
        action_required.append(item)
    allowed_action_prefixes = (
        "base mcp login needed",
        "calendar issue:",
        "personal codex:",
        "shared layer needs attention:",
    )
    unknown_action_required = [
        item for item in action_required
        if not str(item.get("title", "")).lower().startswith(allowed_action_prefixes)
    ]

    live_objectives_ok = bool(primary_agent) and not (
        jaimes_feed.get("active") and jaimes_feed.get("capabilityBacked")
    )
    calendar_detail = calendar.get("message") or calendar.get("status") or "missing"
    objective_fn_start = html.find("function syncBrainFeedObjectiveScroller")
    objective_fn_end = html.find("function pickPrimaryBrainFeed")
    objective_fn = html[objective_fn_start:objective_fn_end] if objective_fn_start >= 0 and objective_fn_end > objective_fn_start else ""
    checks = [
        status(
            live_objectives_ok,
            "Live objectives",
            f"primary={primary_agent or 'missing'}; dualAgents={dual_agents or 'none'}; live={len(live_agents)}",
        ),
        status(
            calendar.get("status") == "ok"
            or calendar.get("status") == "optional"
            or "No auth" in str(calendar_detail)
            or "fetch failed" in str(calendar_detail).lower()
            or "gog cli missing" in str(calendar_detail).lower()
            or "local calendar helper" in str(calendar_detail).lower(),
            "Calendar tile",
            calendar_detail,
            severity="medium",
        ),
        status(
            bool(today) and not active_errors,
            "Today Jobs",
            f"{len(today)} today-relevant; {len(active_errors)} active issue(s)",
        ),
        status(
            not unknown_action_required and len(action_required) <= 4,
            "Action Required",
            "clear" if not action_required else f"{len(action_required)} visible alert(s)",
            severity="medium",
        ),
        status(
            (
                "pickDualLiveObjectiveFeeds" in html and "renderOpsCenter" in html
            ) or (
                "function BrainHero" in render_source and "BrainOperationsSummary" in render_source
            ),
            "Renderer wiring",
            "hero + ops renderers present" if html else "index.html missing",
        ),
        status(
            'const HERO_AGENT_ORDER: AgentId[] = ["joshex", "josh", "jaimes", "jain"]' in react_main
            and ".brain-hero.is-flight-deck .brain-agent-grid" in react_styles
            and "grid-template-rows: repeat(4, minmax(0, 1fr))" in react_styles,
            "All-agent Brain Feed",
            "JOSHeX, Josh 2.0, JAIMES, and J.A.I.N all render as first-class Brain Feed cards",
            severity="medium",
        ),
        status(
            (
                "ops-glance-strip" in html and "opsGlance" in html and "Agent ecosystem glance status" in html
            ) or (
                "brain-attention-strip" in render_source and "What needs attention" in render_source
            ),
            "Ops glance strip",
            "first-viewport agent attention strip present" if html else "index.html missing",
            severity="medium",
        ),
        status(
            bool(personal_codex) and (
                ("personal-codex-panel" in html and "renderPersonalCodex" in html)
                or bool(react_main)
            ),
            "Personal Codex lane",
            f"status={personal_codex.get('status', 'missing')}",
            severity="medium",
        ),
        status(
            agent_control_current and bool(agent_control_summary) and (
                ("agent-control-panel" in html and "renderAgentControlPanel" in html)
                or bool(react_main)
            ),
            "Agent Control lane",
            f"overall={agent_control_summary.get('overall', 'missing')}; ready={agent_control_summary.get('readyAgents', 0)}/{agent_control_summary.get('totalAgents', 0)}; source={agent_control.get('statusSource') or 'sidecar'}",
            severity="medium",
        ),
        status(
            (
                "Mission Control alignment pass" in html
                and "height: 66px;" in html
                and "-webkit-line-clamp: 2 !important" in html
                and "Mission Control alignment pass 2" in html
                and "grid-template-columns: repeat(2, minmax(0, 1fr)) !important;" in html
                and ".card-jobs-full .codex-job-title,\n        .card-jobs-full .codex-job-detail,\n        .card-jobs-full .shared-event-title" in html
                and "setInterval(() =>" not in objective_fn
            ) or (
                "brain-agent-grid" in render_source
                and "brain-attention-strip" in render_source
                and "timeline-lane" in render_source
                and "job-timeline-row" in render_source
            ),
            "Alignment stability",
            "stable text rails present; objective slow-scroll is contained; dense grids capped" if html else "index.html missing",
            severity="medium",
        ),
        status(
            "--module-rim" in react_styles
            and "brain-agent-stage" in react_styles
            and ".jobs-rail > .panel-title" in react_styles
            and ".support-grid .signal-feed > .panel-title" in react_styles
            and "var(--module-gutter)" in react_styles,
            "Module separation",
            "top-level modules have visible shells, gutters, and header dividers",
            severity="medium",
        ),
        status(
            "--module-blue-fill" in react_styles
            and "--module-blue-flat" in react_styles
            and "--module-blue-rim-strong" in react_styles
            and ".jobs-rail::before" in react_styles
            and ".support-grid .signal-feed::before" in react_styles,
            "Blue support modules",
            "Today Jobs and Signal Feed have a distinct flat blue shell while Brain Feed stays glassy",
            severity="medium",
        ),
        status(
            "background: var(--module-blue-flat);" in react_styles
            and "background: rgba(118, 190, 255, 0.035);" in react_styles,
            "Flat support module surfaces",
            "Today Jobs and Signal Feed use flat blue surfaces instead of gradient-heavy module shells",
            severity="medium",
        ),
        status(
            "ActivityHeatStrip" not in react_main
            and "BrainUsageStrip" not in react_main
            and "activity-heat-strip" not in react_styles
            and "brain-usage-strip" not in react_styles,
            "Brain Feed strip cleanup",
            "Last 60m and model usage strips are removed from Brain Feed",
            severity="medium",
        ),
        status(
            not x_posting_jobs
            and "x_post_agent.py" not in cron_manifest
            and "x_strategic_reply" not in cron_manifest
            and "X Watchlist Monitor" in cron_manifest,
            "X intelligence-only jobs",
            "X posting/reply jobs are out of Today's Jobs; X Watchlist Monitor remains for intelligence",
            severity="high",
        ),
        status(
            KIOSK_WATCHDOG_PATH.exists()
            and KIOSK_WATCHDOG_PLIST_PATH.exists()
            and "mission_control_kiosk_watchdog.py --repair" in kiosk_watchdog_plist
            and "Signal Feed check-in is stale" in kiosk_watchdog
            and "reopened Chrome kiosk" in kiosk_watchdog
            and "mission_control_runtime_layout_check.py" in kiosk_watchdog,
            "Kiosk self-healing watchdog",
            "Josh 2.0 watchdog can repair server, data, Signal Feed, Chrome kiosk drift, and keep live layout checks fresh",
            severity="high",
        ),
        status(
            all(
                name in kiosk_server
                for name in (
                    "brain-feed.json",
                    "joshex-brain-feed.json",
                    "jaimes-brain-feed.json",
                    "jain-brain-feed.json",
                    "agentic-crypto-wallet.json",
                    "modelUsage.json",
                    "jain-daily-signals.json",
                    "jain-signal-health.json",
                )
            ),
            "Local live sidecar watch",
            "kiosk live feed watches main agent, wallet, model, and Signal Feed sidecars",
            severity="high",
        ),
        status(
            "function dataQualityIssues" in react_main
            and "Job data needs refresh" in react_main
            and "Josh 2.0 screen layout needs attention" in react_main
            and "Signal Feed needs refresh" not in react_main,
            "Data freshness guardrails",
            "Brain Feed surfaces stale jobs, agent coverage, and live kiosk display fit without treating optional news as a blocker",
            severity="high",
        ),
        status(
            "LOCAL_BRAIN_FEED_PATH" in josh_visibility_heartbeat
            and "is_recent_real_josh_feed_row" in josh_visibility_heartbeat
            and '"Josh 2.0 heartbeat merge"' in state_guard
            and 'str(ROOT / "scripts" / "josh_visibility_heartbeat.py"), "--brain-feed"' in state_guard,
            "Josh heartbeat recovery publish",
            "healthy Josh 2.0 heartbeat recovery can clear stale status-check attention rows without overwriting real active work",
            severity="medium",
        ),
        status(
            "normalizeGeneratedAgentFeed" in react_data
            and "agentBrainFeeds?.josh" in react_data
            and "explicit_active" in update_script
            and "heartbeat_ts > explicit_ts" in update_script
            and bool(parse_ts(josh_feed.get("updatedAt")))
            and (datetime.now(timezone.utc) - parse_ts(josh_feed.get("updatedAt"))).total_seconds() <= 2 * 3600,
            "Brain Feed heartbeat freshness",
            "idle agent cards prefer fresh heartbeat-backed check-ins over old sidecars",
            severity="medium",
        ),
        status(
            "function sourceTruthLabel" in react_main
            and "function missionTruthSummary" in react_main
            and "riskDataIssues = dataIssues.filter" in react_main
            and "softDataIssues = dataIssues.filter" in react_main
            and "brain-truth-pill" in react_main
            and '"Live"' in react_main
            and "Watch" in react_main
            and "Needs review" in react_main
            and "riskItems.length ? \"Needs review\" : watchItems.length ? \"Watch\" : \"Live\"" in react_main
            and "`${readyAgents}/${trackedAgents} agents · kiosk ${layoutOk ? \"ready\" : \"watch\"}`" in react_main
            and "Truth OK" not in react_main
            and '{showDetails ? "Hide" : "Details"}' in react_main
            and "Live truth OK" not in react_main
            and "Live check" in react_main
            and "kiosk ${layoutAgeLabel}" in react_main
            and "signalTotal" not in react_main.split("function missionTruthSummary", 1)[1].split("function agentOperatingState", 1)[0]
            and ".brain-hero-controls .brain-truth-pill" in react_styles
            and ".brain-hero-controls .brain-truth-pill.is-risk" in react_styles
            and ".brain-hero-controls span,\n.brain-hero-controls button" in react_styles
            and "letter-spacing: 0.01em;\n  text-overflow: ellipsis;\n  text-transform: none;" in react_styles
            and ".truth-chip.is-clear" in react_styles
            and ".truth-chip.is-watch" in react_styles,
            "Brain Feed truth chip",
            "visible Brain Feed header separates clean, watch, and needs-review states in calm plain-English controls",
            severity="medium",
        ),
        status(
            "function jobIsVisibleMaintenance" in react_main
            and "upcomingTodayJobs(allJobs, quietMode ? 5 : 6)" in react_main
            and "visibleGeneral = general.filter((job) => jobIsVisibleMaintenance(job, allJobs)).slice(0, quietMode ? 4 : 3)" in react_main,
            "Quiet maintenance jobs",
            "Today Jobs focus view keeps routine maintenance collapsed unless active, soon, or needing focus",
            severity="medium",
        ),
        status(
            "function SchedulerInventoryDisclosure" in react_main
            and "All scheduled jobs" in react_main
            and "by category" in react_main
            and "Full job list" not in react_main
            and "grouped view" not in react_main
            and "0 background" not in react_main
            and "full day" not in react_main
            and "Scheduler inventory" not in react_main
            and "audit/debugging" not in react_main
            and "audit only" not in react_main
            and "function DailyJobsCalendar" in react_main
            and "Daily calendar" in react_main
            and "calendar-day-axis" in react_styles
            and "calendar-job-block" in react_styles
            and "const todayBlocks = blocks.filter((block) => sameLocalDay(block.startsAt.toISOString()));" in react_main
            and "const visibleBlocks = todayBlocks.length >= 6" in react_main
            and "jobs-view-toggle" not in react_main
            and ".scheduler-inventory-section" in react_styles
            and ".daily-calendar-view" in react_styles,
            "Today Jobs calendar separation",
            "Today Jobs renders as a today-first daily calendar while the complete grouped list remains a quiet drill-down",
            severity="medium",
        ),
        status(
            "Current hour clear" in react_main
            and "No scheduled work in this hour." in react_main
            and "function calendarClearUntilLabel" in react_main
            and "Clear until ${calendarHourLabel(hourKey)}" in react_main
            and "open hour" not in react_main
            and "No scheduled job block in this hour." not in react_main
            and "<span>Focus</span>" in react_main,
            "Today Jobs plain-English empty hour",
            "empty current-hour and gap labels use plain assistant language instead of planner/debug wording",
            severity="low",
        ),
        status(
            "Next job and owner." in react_main
            and "Ready/watch result, next owner, and any needed handoff." in react_main
            and react_main.find("if (/daily mission|missions|mission picks|claim|reward/.test(text))") != -1
            and react_main.find("if (/daily mission|missions|mission picks|claim|reward/.test(text))") < react_main.find("if (/mission control|kiosk|dashboard|react|watchdog|refresh|live display|visual|readability|layout|ui/.test(text))")
            and "Points the Next readout to the visible calendar." not in react_main
            and "Ready/watch status plus the next handoff or repair instruction." not in react_main,
            "Today Jobs Next Up plain language",
            "Next Up card explains the next job in operator language instead of renderer/debug wording",
            severity="low",
        ),
        status(
            "function nextHeaderRunValue(block?: CalendarJobBlock | null)" in react_main
            and "const headline = nextHeaderRunValue(block);" in react_main
            and "<strong title={`${headline} · ${missionText(block.job.title)}`}>{headline}</strong>" in react_main
            and "isNextUp={Boolean(nextBlock && block.id === nextBlock.id)}" in react_main
            and 'isNextUp ? "is-next-up" : ""' in react_main
            and ".calendar-job-block.is-next-up" in react_styles
            and 'content: "Next";' in react_styles
            and ".calendar-job-block.is-next-up .calendar-block-main" in react_styles
            and "{time} · {block.title}" not in react_main,
            "Today Jobs Next Up countdown",
            "Next Up card uses the same countdown treatment as the top header and highlights the matching calendar block",
            severity="low",
        ),
        status(
            '"Daily Agent Readiness Check"' in update_script
            and "Checks JAIMES/Hermes readiness and flags handoff or system issues" in update_script
            and '"Daily Health Check"' not in update_script
            and "Readiness + handoff risks." in react_main,
            "Today Jobs readiness label",
            "daily readiness work uses operator-specific language instead of a generic health-check label",
            severity="low",
        ),
        status(
            "Confirms agent readiness and flags handoff or system issues." in react_main
            and "Ready/watch status plus any needed repair or handoff." in react_main
            and "Checks agent readiness and handoffs" in react_main,
            "Today Jobs readiness detail",
            "Next Up explains what the readiness check does instead of describing the calendar renderer",
            severity="low",
        ),
        status(
            "function compactCalendarDayLabel(date: Date, dayLabel: string)" in react_main
            and 'return date.toLocaleDateString([], { weekday: "short" });' in react_main
            and 'const compactDayLabel = compactCalendarDayLabel(date, dayLabel);' in react_main
            and 'const compactTimeLabel = dayLabel === "Today" ? timeLabel : timeLabel.replace(/[AP]M$/i, (suffix) => suffix[0].toLowerCase());' in react_main
            and '`${compactDayLabel} ${compactTimeLabel}`' in react_main
            and "grid-template-columns: 52px minmax(0, 1fr)" in react_styles
            and "linear-gradient(90deg, rgba(105, 144, 181, 0.09) 0 66px, transparent 66px)" in react_styles,
            "Today Jobs time rail fit",
            "future calendar labels use compact weekday names and one-letter AM/PM markers so the time rail stays readable",
            severity="medium",
        ),
        status(
            "function calendarBlockTimeLabel(date: Date)" in react_main
            and ".replace(/\\s?[AP]M$/i, \"\")" in react_main
            and "const time = calendarBlockTimeLabel(block.startsAt);" in react_main,
            "Today Jobs row time fit",
            "calendar row times omit AM/PM because the hour rail already carries that context, preventing row-time ellipses",
            severity="medium",
        ),
        status(
            "const routineRunning = workingBlocks.filter((block) => jobIsRoutineActivity(block.job)).length;" in react_main
            and 'const activeLabel = priorityRunning ? "Now" : routineRunning ? "Background" : "Now";' in react_main
            and '? `${routineRunning} normal check${routineRunning === 1 ? "" : "s"}`' in react_main
            and '<span>{activeLabel}</span>' in react_main
            and ".calendar-control-strip .is-routine strong" in react_styles,
            "Today Jobs routine status label",
            "routine background checks read as normal background work instead of an alert-like checking state",
            severity="medium",
        ),
        status(
            'className="panel-title compact calendar-title"' in react_main
            and "railSummary" not in react_main
            and "{railSummary ? <span>" not in react_main,
            "Today Jobs header dedupe",
            "the Today Jobs title avoids duplicating the labeled running/focus metrics shown directly below it",
            severity="medium",
        ),
        status(
            "const nowBlockLabel = slot.blocks.length" in react_main
            and "const currentHourLabel = slot.blocks.length" in react_main
            and "Now: ${nowBlockLabel}" in react_main
            and "Now · {currentHourLabel}" in react_main
            and "grid-template-columns: 1fr minmax(0, auto) auto" in react_styles,
            "Today Jobs current-hour count",
            "current calendar cluster uses a concise Now marker while the summary shows only what is actively running",
            severity="medium",
        ),
        status(
            ".calendar-job-block.tone-working" in react_styles
            and "grid-template-columns: 48px minmax(0, 1fr) 58px;" in react_styles
            and "min-height: 40px;" in react_styles
            and ".calendar-job-block.tone-working .calendar-block-time,\n.calendar-job-block.tone-working .calendar-block-main" in react_styles
            and ".calendar-job-block.tone-working .calendar-block-meta em" in react_styles
            and "display: none;" in react_styles
            and "-webkit-line-clamp: 1;" in react_styles,
            "Today Jobs active row fit",
            "the current job row prioritizes readable title space while keeping time and owner in compact lanes",
            severity="medium",
        ),
        status(
            'if (block.synthetic) return `${block.count} check${block.count === 1 ? "" : "s"}`;' in react_main
            and "function groupedRoutineTitle(items: CalendarJobBlock[], firstBlock: CalendarJobBlock, isSystemGroup: boolean)" in react_main
            and 'if (isSystemGroup) return "System checks";' in react_main
            and 'return "Signal checks";' in react_main
            and 'const isSystemGroup = items.some((item) => routineCalendarGroupKey(item).endsWith("-system-checks"));' in react_main,
            "Today Jobs grouped labels",
            "grouped calendar rows show check counts while the row title stays focused on the category or system-check group",
            severity="medium",
        ),
        status(
            "ACTIVE_FOCUS_FRESH_MINUTES" in react_main
            and "activeWorkFresh" in react_main
            and "statusWorkingFresh" in react_main,
            "Brain Feed stale-active guard",
            "old working rows age out before driving the green active state",
            severity="medium",
        ),
        status(
            "jobIsRoutineActivity" in react_main
            and "activeRoutineJobCount" in react_main
            and "priority job${activeFocusJobCount" in react_main
            and "live check${activeRoutineJobCount === 1 ? \"\" : \"s\"} running" in react_main
            and "background sync" not in react_main
            and 'liveActivityParts.join(" · ")' in react_main
            and "routine check${visibleRoutineWorkingCount === 1 ? \"\" : \"s\"} active" not in react_main
            and "agent${activeAgentCount === 1 ? \"\" : \"s\"} working" in react_main
            and "const priorityLiveWorkCount = activeAgentCount + activeFocusJobCount;" in react_main
            and 'priorityLiveWorkCount ? "Running" : activeRoutineJobCount ? "Live check" : "Live"' in react_main
            and 'liveMode === "connected" ? "Realtime" : "Live 10s"' not in react_main
            and "function dashboardFreshnessTimestamp(state: MissionControlState)" in react_main
            and "state.lastUpdated" in react_main
            and "state.agenticCrypto?.updatedAt" in react_main
            and "state.signalHealth?.generatedAt" in react_main
            and "state.runtimeLayout?.checkedAt" in react_main
            and "state.modelUsage?.lastUpdated" in react_main
            and "state.capabilityWatch?.checkedAt" in react_main
            and "const lastUpdate = dashboardFreshnessTimestamp(state);" in react_main
            and "const liveFreshnessLabel = updatedFreshnessLabel(lastUpdate);" in react_main
            and 'const liveSummaryLabel = liveActivityLabel === "all clear" ? liveConnectionLabel.toLowerCase() : liveActivityLabel;' in react_main
            and 'title={liveChipTitle}' in react_main
            and 'title={`Updated ${fmtTime(lastUpdate)}`}>{updatedFreshnessLabel(lastUpdate)}' not in react_main
            and "Running now:" in react_main
            and "Live checks running:" in react_main
            and "it is not an alert" in react_main,
            "Header live activity language",
            "top-right live chip combines dashboard-wide freshness and current activity using plain live-check language instead of scheduler jargon",
            severity="medium",
        ),
        status(
            "Josh 2.0 live source" in react_main
            and "sourceTruthLabel(state.source)" in react_main
            and 'title={state.source || "Local live source"}' in react_main,
            "Header live source plain language",
            "top-right source chip uses operator-friendly wording while preserving the raw source value in the tooltip",
            severity="low",
        ),
        status(
            'label="Jobs" value={jobsValue}' in react_main
            and "const jobsValue = `${jobsCount} tracked`;" in react_main
            and "const trackedJobs = operatorTrackedJobs(state.jobs);" in react_main
            and "const jobsCount = trackedJobs.length;" in react_main,
            "Header tracked jobs label",
            "top header labels the job count as tracked inventory in plain English, while running work stays in the live chip",
            severity="low",
        ),
        status(
            'const actionLabel = decisionCount ? "Needs Josh" : "Decisions";' in react_main
            and 'const needsJoshValue = decisionCount ? `${decisionCount} review${decisionCount === 1 ? "" : "s"}` : "Clear";' in react_main
            and 'label={actionLabel}' in react_main,
            "Header action label",
            "clear state reads Decisions/Clear, while real pending reviews still call for Josh",
            severity="low",
        ),
        status(
            "activeCrons = crons.filter" in react_data
            and "for (const cron of [...activeCrons, ...priorityCrons, ...dailyCrons])" in react_data
            and "rank(aState) - rank(bState)" in react_main
            and "activeWorkDetail?.title || objectiveText" in react_main
            and "const headerStateLabel = agentHeaderStateLabel(visualState, routineFocus, activeFocus);" in react_main,
            "Brain Feed active-work truth",
            (
                f"{len(active_crons)} active scheduled job(s) can promote owning agent cards"
                if active_crons
                else "active scheduled jobs are wired to promote owning agent cards"
            ),
            severity="high",
        ),
        status(
            'type AgentVisualState = "working" | "routine" | "ready" | "waiting" | "blocked" | "stale";' in react_main
            and "function workItemIsRoutineActivity" in react_main
            and 'eyebrow: routineFocus ? "Keeping current" : "Active now"' in react_main
            and "const routineDescription = readoutSummary(" in react_main
            and 'const actionLabel = routineFocus ? "Routine" : "Doing";' in react_main
            and '{ label: "Start", state: hasUpdate ? "done" : "pending" }' in react_main
            and '{ label: blocked ? "Hold" : routineFocus ? "Sync" : "Work", state: activeFocus || blocked ? "current" : hasUpdate ? "done" : "pending" }' in react_main
            and '{ label: "Report", state: activeFocus || blocked ? "pending" : hasUpdate ? "current" : "pending" }' in react_main
            and '{ label: "In", state: hasUpdate ? "done" : "pending" }' not in react_main
            and '{ label: "Out", state: activeFocus || blocked ? "pending" : hasUpdate ? "current" : "pending" }' not in react_main
            and "description: routineFocus ? routineDescription : `Next:" in react_main
            and "Now: keeping live status current" in react_main
            and "Next: ${nextSupport}" in react_main
            and 'if (routineFocus) return "Current";' in react_main
            and 'if (label === "Routine") return "Watches";' in react_main
            and "Aligned; only reports mismatches." in react_main
            and "Keeps Mission Control aligned; reports only if a mismatch appears." not in react_main
            and "Status update will publish when this check finishes." not in react_main
            and "const headerStateLabel = agentHeaderStateLabel(visualState, routineFocus, activeFocus);" in react_main
            and 'routineFocus ? "is-routine-focus" : activeFocus ? "is-working-focus"' in react_main
            and ".agent-hero-card.is-routine-focus h3" in react_styles
            and ".agent-hero-card.is-state-routine .dot" in react_styles,
            "Brain Feed routine check state",
            "routine background checks keep a short status label while the main headline explains that live status is staying current",
            severity="medium",
        ),
        status(
            "Breaking + newsletter signals." in react_main
            and 'if (/signal|intelligence|news|newsletter|breaking/.test(lower)) return "Breaking + newsletter signals.";'
                in react_main
            and 'if (/mission control|kiosk|dashboard|react|watchdog|ui/.test(lower)) return "Live kiosk health.";'
                in react_main
            and react_main.find('if (/signal|intelligence|news|newsletter|breaking/.test(lower)) return "Breaking + newsletter signals.";')
                < react_main.find('if (/mission control|kiosk|dashboard|react|watchdog|ui/.test(lower)) return "Live kiosk health.";'),
            "Brain Feed active-detail classification",
            "signal/news refresh work stays described as signal work instead of generic kiosk verification",
            severity="medium",
        ),
        status(
            "function agentHeaderStateLabel(visualState: AgentVisualState, routineFocus: boolean, activeFocus: boolean)" in react_main
            and "function agentHeaderDotClass(visualState: AgentVisualState, routineFocus: boolean, activeFocus: boolean)" in react_main
            and "function agentCardFreshnessClass(status: AgentStatus)" in react_main
            and "if (isReadyHeartbeatStatus(status))" in react_main
            and "if (minutes >= 120) return \"is-stale\";" in react_main
            and "const freshness = agentCardFreshnessClass(status);" in react_main
            and "if (agentCardFreshnessClass(status) === \"is-stale\") return \"stale\";" in react_main
            and "if (!isReadyHeartbeatStatus(status) && activeFocus) return \"working\";" in react_main
            and "activeFocus || agentIsWorking(status)" not in react_main
            and "const headerDotClass = agentHeaderDotClass(visualState, routineFocus, activeFocus);" in react_main,
            "Brain Feed stale-active label guard",
            "stale active rows age out of working, while healthy idle agents stay Ready until the visibility SLA is actually missed",
            severity="high",
        ),
        status(
            "const freshCheckin = ageMinutes(status.updated_at) < 5;" in react_main
            and 'freshCheckin ? " is-hot" : ""' in react_main
            and ".agent-freshness-pill.is-hot" in react_styles
            and ".agent-freshness-pill.is-hot::before" in react_styles,
            "Brain Feed fresh check-in cue",
            "fresh agent check-ins get a subtle durable visual cue after refresh without adding more text or modules",
            severity="medium",
        ),
        status(
            "ACTIVE_JOB_FRESH_MINUTES" in react_main
            and "jobIsFreshActive" in react_main
            and "operatorVisibleJobs" in react_main
            and "jobIsScheduledInventory" in react_main,
            "Today Jobs stale-active guard",
            "historical job rows cannot inflate active counts or hide the scheduled inventory",
            severity="medium",
        ),
        status(
            "const trackedJobs = operatorTrackedJobs(state.jobs);" in react_main
            and "const jobsCount = trackedJobs.length;" in react_main
            and "next job ${nextRunValue}" not in react_main
            and "All clear · ${nextJobLabel}" not in react_main
            and "railSummary" not in react_main
            and "{railSummary ? <span>{railSummary}</span> : null}" not in react_main
            and '      : "All clear";' not in react_main,
            "Today Jobs header count",
            "top header owns tracked inventory while the Jobs rail avoids unlabeled duplicate header counts",
            severity="medium",
        ),
        status(
            "const nextVisibleBlock = nextVisibleCalendarBlock(trackedJobs, quietMode);" in react_main
            and "const nextRunLabel = nextHeaderRunLabel(nextVisibleBlock);" in react_main
            and "const nextRunValue = nextHeaderRunValue(nextVisibleBlock);" in react_main
            and "label={nextRunLabel}" in react_main
            and "const running = blocks.find((block) => (" in react_main
            and 'block.tone === "working"' in react_main
            and "&& !jobIsRoutineActivity(block.job)" in react_main
            and "if (running) return running;" in react_main
            and "const nextPriority = blocks.find((block) => block.startsAt.getTime() >= now && !jobIsRoutineActivity(block.job));" in react_main
            and "if (nextPriority) return nextPriority;" in react_main
            and "const runningRoutine = blocks.find((block) => (" in react_main
            and "block.startsAt.getTime() >= now)" in react_main
            and "function headerJobTitle(block: CalendarJobBlock)" in react_main
            and "function nextHeaderRunValue(block?: CalendarJobBlock | null)" in react_main
            and "function nextHeaderRunLabel(block?: CalendarJobBlock | null)" in react_main
            and "function nextIntervalWindowRunTime(schedule: string)" in react_main
            and "const intervalMatch = schedule.match(/\\bevery\\s+(\\d+)\\s*min/i);" in react_main
            and "schedule.matchAll(/(\\d{1,2})(?::(\\d{2}))?\\s*(AM|PM)/gi)" in react_main
            and "startMinute + Math.ceil(elapsed / interval) * interval" in react_main
            and 'return block.startsAt.getTime() <= Date.now() ? "Job focus" : "Next up";' in react_main
            and 'return block.startsAt.getTime() <= Date.now() ? "Background" : "Next sync";' in react_main
            and "jobIsRoutineActivity(block.job)" in react_main
            and "const title = headerJobTitle(block);" in react_main
            and "if (target <= Date.now()) return title || `Now · ${clock}`;" in react_main
            and "countdownShortText(countdownLabel(target))" in react_main
            and '`${countdown} · ${title}`' in react_main,
            "Header next-up countdown",
            "top header labels scheduled focus separately from live active agent work while routine upkeep stays in the live sync chip",
            severity="medium",
        ),
        status(
            ("grid-template-rows: auto 88px auto auto auto" in react_styles
             or "grid-template-rows: auto auto 68px auto auto auto" in react_styles
             or "grid-template-rows: auto auto minmax(82px, 1fr) auto auto auto" in react_styles
             or "grid-template-rows: 22px 14px minmax(88px, 1fr) 34px 28px 20px" in react_styles
             or "grid-template-rows: 22px 14px minmax(98px, 1fr) 34px 28px 20px" in react_styles
             or ("flex-direction: column;" in react_styles and "margin-top: auto;" in react_styles))
            and ("min-height: 292px" in react_styles or "min-height: 270px" in react_styles or "height: 100%;" in react_styles)
            and ".brain-hero .agent-idle-readout p" in react_styles,
            "Brain Feed text rails",
            "agent panels keep objective, complete, next, and footer in fixed lanes",
            severity="medium",
        ),
        status(
            "function buildAgentInsights" in react_main
            and "agent-insight-panel" in react_main
            and "agent-insight-grid" in react_main
            and ".agent-insight-panel" in react_styles
            and ".agent-insight-grid" in react_styles
            and "function agentCadenceDetail(idleContext?: AgentIdleContext)" in react_main
            and 'if (next === "now") return "Checking now";' in react_main
            and "return next ? `Next check ${next}` : \"Next check soon\";" in react_main
            and 'if (diff <= 0) return "now";' in react_main
            and "const sla = agentSla(status, idleContext);" in react_main
            and "Aligned; only reports mismatches." in react_main
            and "Result or blocker." in react_main
            and "Completion summary or blocker will publish here." not in react_main
            and "Result will publish here when complete." not in react_main
            and "Keeps Mission Control aligned; reports only if a mismatch appears." not in react_main
            and "Status update will publish when this check finishes." not in react_main
            and "grid-template-columns: repeat(2, minmax(0, 1fr));" in react_styles
            and "grid-template-rows: minmax(30px, 1fr);" in react_styles
            and "min-height: 18px;" in react_styles
            and '{ label: "Next", text: nextSummary }' not in react_main
            and "Checks" in react_main
            and "Output" in react_main,
            "Brain Feed next-job context",
            "agent cards keep readable checks and output rows while next timing stays in the header and footer",
            severity="medium",
        ),
        status(
            "<em>{headerStateLabel}</em>" in react_main
            and "{headerStateLabel} · {ageLabel" not in react_main,
            "Brain Feed status dedupe",
            "agent card headers keep only the state while the right-side freshness rail owns timing",
            severity="medium",
        ),
        status(
            '"Refresh due · checked just now"' in react_main
            and "`Refresh due · checked ${age} ago`" in react_main
            and '"Aging · checked just now"' not in react_main
            and "`Aging · checked ${age} ago`" not in react_main,
            "Brain Feed freshness wording",
            "watch-level agent check-ins use refresh-due language instead of warning-like aging language",
            severity="medium",
        ),
        status(
            "function nextCountdownClockLabel(nextAt?: number)" in react_main
            and "const when = nextCountdownClockLabel(idleContext.nextAt) || idleContext.countdown || \"soon\";" in react_main
            and "return countdown ? `${countdown} · ${clock}` : clock;" in react_main,
            "Brain Feed up-next countdown",
            "agent card Up Next pills show countdown plus clock time instead of static clock-only timing",
            severity="low",
        ),
        status(
            ".brain-hero.is-flight-deck .agent-objective-meta em" in react_styles
            and "text-transform: none;" in react_styles
            and "letter-spacing: 0.03em;" in react_styles,
            "Brain Feed countdown typography",
            "agent card countdown chips keep natural case while compact labels stay uppercase",
            severity="low",
        ),
        status(
            "No completed task reported yet" in react_main
            and "Standing by until the next scheduled task" not in react_main
            and "standing by until the next scheduled task" not in react_main
            and "No recent JOSHeX completion reported" not in react_main
            and "No recent ${AGENTS[agent].label} completion reported" not in react_main,
            "Brain Feed idle fallback wording",
            "idle agent cards distinguish missing completion reports from true completed work in plain English",
            severity="medium",
        ),
        status(
            ".brain-hero.is-flight-deck .agent-hero-card p" in react_styles
            and "font-size: 11.4px;" in react_styles
            and "font-weight: 700;" in react_styles
            and "rgba(218, 236, 245, 0.88)" in react_styles
            and "white-space: nowrap;" in react_styles
            and "overflow: hidden;" in react_styles
            and "Last: ${lastSupport}" in react_main
            and "Next: ${nextSupport}" in react_main
            and "Now: ${nowSupport}" in react_main
            and ".replace(/^(?:last|complete|completed):\\s*/i, \"\")" in react_main,
            "Brain Feed completion line readability",
            "agent cards keep a readable Last plus Next line without changing the card layout",
            severity="low",
        ),
        status(
            "const completedJobTime = timeValue(completedJob?.completed_at || completedJob?.lastRun || completedJob?.updated_at);" in react_main
            and "const completedEventTime = timeValue(completedEvent?.created_at);" in react_main
            and "const completedStatusStep = agentStatus?.steps.find" in react_main
            and "const agentStatusValue = String(agentStatus?.status || \"\").toLowerCase();" in react_main
            and "agentStatusValue === \"idle\"" in react_main
            and "genericReadyStep" in react_main
            and "const completedStatusStepTime = completedStatusStep ? timeValue(agentStatus?.updated_at) : 0;" in react_main
            and "{ title: completedStatusStep?.label || completedStatusStep?.title, time: completedStatusStepTime }" in react_main
            and "Last: ${lastSupport}" in react_main
            and "Next: ${nextSupport}" in react_main
            and "kind: latestJoshexEvent.type || latestJoshexEvent.event_type || \"shared-event\"" in react_data,
            "Brain Feed latest completion truth",
            "idle agent Last lines preserve the newest completed local Brain Feed step, event, or job title instead of generic job-category summaries",
            severity="medium",
        ),
        status(
            'replace(/\\bintelligence feed\\b/i, "Intelligence Feed")' in react_main
            and 'compactText(cleanHeadlineText(String(route)), 84)' in react_main,
            "Brain Feed active label polish",
            "active tool labels render as plain-English title case instead of raw lowercase source values",
            severity="medium",
        ),
        status(
            '"jaimes-ops-drift-check": "JAIMES ops drift check"' in update_script
            and '"jaimes-model-efficiency-guard": "JAIMES model efficiency guard"' in update_script
            and '.replace(/\\bjaimes-ops-drift-check\\b/gi, "JAIMES ops drift check")' in react_main
            and '.replace(/\\bjaimes-model-efficiency-guard\\b/gi, "JAIMES model efficiency guard")' in react_main
            and not raw_job_leaks,
            "Brain Feed raw job label cleanup",
            (
                "JAIMES scheduled job IDs render as plain-English work labels in code and current dashboard data"
                if not raw_job_leaks
                else f"raw job labels still present in dashboard data: {', '.join(raw_job_leaks)}"
            ),
            severity="low",
        ),
        status(
            "Top five" in react_main
            and "Last 5" in react_main
            and "signal-story" in react_main
            and "signal-impact" in react_styles,
            "Signal Feed split",
            "10-row feed keeps live/breaking and newsletter-derived rows separated",
            severity="medium",
        ),
        status(
            "function signalDedupeKey(signal: SignalItem, newsletter: boolean)" in react_main
            and "const seenDedupeKeys = new Set<string>();" in react_main
            and "if (key && seenDedupeKeys.has(key)) continue;" in react_main
            and "money.length >= 2" in react_main,
            "Signal Feed duplicate suppression",
            "near-identical signal rows collapse before the five-row live/newsletter slots are filled",
            severity="medium",
        ),
        status(
            "function signalCategoryKey" in react_main
            and 'case "market": return "Market";' in react_main
            and 'case "material": return "Material";' in react_main
            and "const sourceLabel = compactText(signal.source, 34)" in react_main
            and "<b>{impactLabel}</b>" in react_main
            and "signalSourceScanLabel" in react_main
            and "signalStoryAgeLabel(signal, newsletter)" in react_main
            and "grid-template-columns: 44px minmax(300px, 1.5fr) minmax(230px, 0.74fr) minmax(58px, 0.16fr);" in react_styles,
            "Signal Feed source/time scanability",
            "rows keep source, story age, and scan freshness distinct without reducing the 10-row view",
            severity="medium",
        ),
        status(
            ".signal-section-label strong" in react_styles
            and "min-height: 31px;" in react_styles
            and "font-size: 11.7px;" in react_styles
            and "font-size: 9.6px;" in react_styles,
            "Signal Feed readable row sizing",
            "10 visible rows use larger headline/detail text while preserving the first-screen table",
            severity="low",
        ),
        status(
            "function ControlTower(" in react_main
            and "function buildControlTowerModel" in react_main
            and "type TowerLane = \"active\" | \"needs-josh\" | \"complete\" | \"planned\";" in react_main
            and "Unified Activity Ledger" in react_main
            and "Agent Flight Deck" in react_main
            and "Priority Queue" in react_main
            and "Resources" in react_main
            and ".control-tower-grid" in react_styles
            and ".activity-ledger" in react_styles
            and ".tower-agent-deck" in react_styles,
            "Control Tower replacement",
            "Mission Control renders an executive control tower with active, needs-Josh, complete, and planned activity lanes",
            severity="high",
        ),
        status(
            "JOSHeX · Josh 2.0 · JAIMES · J.A.I.N" in react_main
            and 'const TOWER_AGENT_ORDER: AgentId[] = ["joshex", "josh", "jaimes", "jain"];' in react_main
            and "TOWER_AGENT_ORDER.map((agent)" in react_main
            and "JAIMES · J.A.I.N" in react_main,
            "Control Tower agent coverage",
            "the replacement surface explicitly includes JOSHeX, Josh 2.0, JAIMES, and J.A.I.N as first-class rows",
            severity="high",
        ),
        status(
            "<SignalFeed state={state}" not in react_main
            and "grid-template-rows: minmax(0, 1fr);" in react_styles
            and "function activityLedgerRows" in react_main
            and "ledger-summary-strip" in react_styles
            and "ledger-row-list is-flat" in react_main,
            "Control Tower center ledger",
            "the center tower uses a flat activity ledger instead of tall vertical lanes, while keeping active, needs-Josh, complete, and planned work prominent",
            severity="high",
        ),
        status(
            ".control-tower-grid" in react_styles
            and "grid-template-columns: minmax(600px, 0.95fr) minmax(640px, 1fr) minmax(600px, 0.95fr)" in react_styles
            and ".tower-command" in react_styles
            and ".tower-center-stack" in react_styles
            and ".tower-jobs-rail" in react_styles
            and "AgentFlightDeck" in react_main
            and "ActivityLedger" in react_main
            and "ResourceStack" in react_main
            and "<SignalFeed state={state}" not in react_main
            and "JobsRail" in react_main,
            "Kiosk viewport composition",
            "24-inch layout keeps the agent deck, expanded activity ledger, resources, and Today's Jobs in the first view",
            severity="medium",
        ),
        status(
            RUNTIME_LAYOUT_CHECK_PATH.exists()
            and "Chrome DevTools" in runtime_layout_check
            and "visibleAgentRows < 4" in runtime_layout_check
            and "visibleCounts" in runtime_layout_check
            and "ledgerRows" in runtime_layout_check
            and "visibleSignalRows < 10" not in runtime_layout_check
            and "visibleCalendarBlocks < 6" in runtime_layout_check
            and "visibleResourceCards < 4" in runtime_layout_check
            and "page has horizontal scroll" in runtime_layout_check
            and "visibleInternalTextLeaks" in runtime_layout_check
            and "mission_control_runtime_layout_check.py" in run_watchdog
            and "mission_control_runtime_layout_check.py" in kiosk_watchdog,
            "Runtime kiosk layout guard",
            "watchdog measures the live Chrome viewport for module fit, row visibility, page overflow, and visible debug text",
            severity="medium",
        ),
        status(
            isinstance(runtime_layout, dict)
            and isinstance(runtime_layout.get("textQuality"), dict)
            and not runtime_layout.get("textQuality", {}).get("visibleInternalTextLeaks"),
            "Runtime visible text quality",
            "live rendered Mission Control text has no internal host IDs, script names, raw job IDs, local URLs, or unresolved placeholders",
            severity="medium",
        ),
        status(
            "refresh_dashboard_data()" in run_watchdog
            and "python3 scripts/update_mission_control.py" in run_watchdog
            and 'watchdog_ok=0' in run_watchdog
            and 'if [[ "$watchdog_ok" != "1" ]]' in run_watchdog,
            "Runtime layout alert propagation",
            "watchdog refreshes dashboard data after runtime layout checks so display failures surface on Mission Control",
            severity="medium",
        ),
        status(
            isinstance(runtime_layout, dict)
            and bool(runtime_layout.get("checkedAt"))
            and "mission-control-runtime-layout.json" in kiosk_server
            and "RUNTIME_LAYOUT_PATH" in update_script
            and 'dashboard["runtimeLayout"] = fetch_runtime_layout_status()' in update_script
            and "runtimeLayout?: RuntimeLayoutHealth" in (V2_STYLES_PATH.parent / "types.ts").read_text(errors="replace")
            and 'fetchJson<MissionControlState["runtimeLayout"]>("/data/mission-control-runtime-layout.json")' in react_data
            and "state.runtimeLayout" in react_main
            and "Josh 2.0 screen layout needs attention" in react_main,
            "Runtime layout dashboard surfacing",
            "live layout guard is carried into dashboard data and only becomes visible when the kiosk fit check needs attention",
            severity="medium",
        ),
        status(
            "screenshot_diff skipped: optional Playwright/Pillow dependency missing" in run_watchdog
            and "mission_control_screenshot_diff.py" in run_watchdog
            and "PLAYWRIGHT_IMPORT_ERROR" in screenshot_diff
            and "PIL_IMPORT_ERROR" in screenshot_diff
            and "mission_control_screenshot_diff SKIPPED" in screenshot_diff
            and "screenshot_diff_skipped" in screenshot_diff
            and "push_jaimes()" in run_watchdog
            and "Regression or live layout check failed" in run_watchdog
            and "Regression, live layout, and dashboard refresh checks passed" in run_watchdog,
            "Watchdog dependency fallback",
            "screenshot diff stays optional at both the watchdog and helper level while live Chrome layout checks remain mandatory",
            severity="low",
        ),
        status(
            "python3 scripts/agent_publish.py" in run_watchdog
            and "--brain-feed" in run_watchdog
            and "--privacy dashboard-safe" in run_watchdog
            and 'event_type="complete"' in run_watchdog
            and "Mission Control watchdog" in run_watchdog,
            "Watchdog Brain Feed fallback",
            "scheduled Mission Control watchdog publishes through the local-first Brain Feed path if the older publisher is missing or fails",
            severity="medium",
        ),
        status(
            "<SignalFeed state={state}" not in react_main
            or (
                len(signal_items) >= 10
                and len(newsletter_items) >= 5
                and str(signal_health.get("status", "")).lower() in ("ok", "fresh", "ready", "quiet")
                and (not signal_health.get("staleSources") or bool(signal_health.get("quietHours")))
            ),
            "Signal Feed optional source",
            (
                f"{len(signal_items)} signal rows; {len(newsletter_items)} newsletter trend rows; "
                f"health={signal_health.get('status', 'missing')}; counts={signal_counts or 'missing'}"
            ),
            severity="low",
        ),
        status(
            "def low_information_source_title" in build_signals
            and "suppressed generic source headline" in build_signals
            and not generic_source_headlines,
            "Signal Feed generic source filter",
            "generic official-source titles without event details no longer crowd out clearer breaking rows",
            severity="medium",
        ),
        status(
            "function signalCategoryKey" in react_main
            and 'case "ai": return "AI";' in react_main
            and 'case "space": return "Space";' in react_main
            and 'case "sanctions": return "Sanctions";' in react_main
            and 'case "policy": return "Policy";' in react_main
            and 'case "crypto": return "Crypto";' in react_main
            and "tone-ai" in react_styles
            and "tone-policy" in react_styles
            and "tone-crypto" in react_styles,
            "Signal Feed category badges",
            "Signal Feed badges distinguish story domains instead of labeling almost every row as market",
            severity="medium",
        ),
        status(
            "function signalUsesPublicFallback" in react_main
            and 'if (signalUsesPublicFallback(signal)) return "fresh";' in react_main
            and 'case "fresh": return "Fresh";' in react_main
            and 'case "fresh": return "tone-signal";' in react_main
            and "Fresh public source coverage" in react_main
            and "Fresh public headlines while J.A.I.N catches up" not in react_main
            and bool(signal_health.get("fallbackFresh") or not signal_counts.get("publicRssFallbackItems")),
            "Signal Feed fallback semantics",
            "fresh public fallback rows render as Fresh and the section explains when J.A.I.N source coverage is being used",
            severity="medium",
        ),
        status(
            "const signalUpdatedAt = state.signalHealth?.generatedAt" in react_main
            and '["ok", "fresh", "ready", "quiet"].includes(String(state.signalHealth?.status || "").toLowerCase()) && signalTotal >= 10' in react_main
            and "function checkedFreshnessLabel" in react_main
            and 'if (label === "just now") return "checked just now";' in react_main
            and "function signalSourceScanLabel" in react_main
            and 'return `source ${label}`;' in react_main
            and "const scanLabel = signalUpdatedAt ? signalSourceScanLabel(signalUpdatedAt) : freshness.label.toLowerCase();" in react_main
            and "refreshed ${ageLabel(signalUpdatedAt)} ago" not in react_main
            and "Signal Feed last refreshed" not in react_main
            and "Scanner refreshed" not in react_main
            and "const nextBreakingHeader = state.signalHealth?.nextBreakingRun?.replace(\":00 \", \" \");" in react_main
            and "const quietHeaderLabel = freshness.label === \"Quiet-hours watch\"" in react_main
            and "quiet hours · next ${nextBreakingHeader}" in react_main
            and "scan ${ageLabel(signalUpdatedAt)}" not in react_main
            and 'tone: "clear" as const,\n      label: "Quiet-hours watch"' in react_main
            and '"after-hours watch"' not in react_main
            and '? "focused"' not in react_main,
            "Signal Feed header freshness",
            "Signal Feed header distinguishes quiet-hours watch from feed refresh timing without terse scan jargon",
            severity="medium",
        ),
        status(
            "function signalFreshnessLabel" in react_main
            and 'if (age === "no update") return "scan pending";' in react_main
            and 'return `scan ${age}`;' in react_main
            and "function signalStoryAgeLabel" in react_main
            and 'return `story ${age}`;' in react_main
            and "<b>{signalStoryAgeLabel(signal, newsletter)}</b>" in react_main
            and "grid-template-columns: 44px minmax(300px, 1.5fr) minmax(230px, 0.74fr) minmax(58px, 0.16fr);" in react_styles,
            "Signal Feed newsletter freshness wording",
            "rows distinguish story age from source scan freshness without implying stale scanner state",
            severity="medium",
        ),
        status(
            '"anthropic" in words' in build_signals
            and '"anthropic-funding-valuation"' in build_signals
            and "row[\"storyKey\"]" in build_signals
            and "ranked_unique([" in build_signals,
            "Signal Feed duplicate story guard",
            "near-identical funding/valuation headlines share one story key before the top-five rows are selected",
            severity="medium",
        ),
        status(
            "function signalDisplayTitle" in react_main
            and "title.match(/^(.+?\\bwatch):\\s*(.+)$/i)" in react_main
            and "const displayTitle = signalDisplayTitle(signal, newsletter);" in react_main
            and "<strong title={missionText(signal.title)}>{displayTitle}</strong>" in react_main,
            "Signal Feed newsletter title dedupe",
            "newsletter rows remove repeated watch labels like Crypto watch: Crypto while preserving full source title in the tooltip",
            severity="low",
        ),
        status(
            "def newsletter_keyword_label" in build_signals
            and "NEWSLETTER_TOPIC_FALLBACKS" in build_signals
            and "Broad digest theme" in build_signals
            and not generic_newsletter_titles,
            "Signal Feed newsletter specificity",
            "newsletter titles avoid repeated generic labels like Crypto watch: Crypto or AI & Chips watch: AI",
            severity="low",
        ),
        status(
            "function signalDisplayTitle" in react_main
            and ".replace(/\\$(\\d+(?:\\.\\d+)?)\\s+billion\\b/gi" in react_main
            and ".replace(/\\$(\\d+(?:\\.\\d+)?)\\s+million\\b/gi" in react_main
            and "Treasury/Vietnam joint statement" in react_main
            and "Treasury/IRS sovereign-investor tax rules" in react_main
            and "U.S. sanctions Iran military oil sales" in react_main
            and '.replace(/\\bvaluation after raising\\b/gi, "valuation after")' in react_main
            and "<strong title={missionText(signal.title)}>{displayTitle}</strong>" in react_main,
            "Signal Feed compact money headlines",
            "large dollar and verbose official-source headlines compress while preserving the full raw title in the tooltip",
            severity="low",
        ),
        status(
            "function signalDisplayReason(signal: SignalItem)" in react_main
            and "Digest trend from ${count} newsletter source" in react_main
            and "newsletterMatch" in react_main
            and "reason_prefix = \"Broad digest theme\" if generic_only else \"Digest trend\"" in build_signals
            and "{reason_prefix} from {source_label}" in build_signals
            and "Newsletter cluster from" not in build_signals
            and "Context only unless" not in build_signals
            and "withoutLinks = raw.replace(/https?:\\/\\/\\S+/gi" in react_main
            and "Source-backed signal crossing the relevance filter." in react_main
            and "item(s)" not in react_main.split("function signalDisplayReason(signal: SignalItem)", 1)[1].split("function signalSourceScanLabel", 1)[0]
            and "const displayReason = signalDisplayReason(signal);" in react_main
            and "<p title={missionText(signal.reason)}>{displayReason}</p>" in react_main,
            "Signal Feed plain-English reasons",
            "newsletter summaries render digest trend language in both data and UI instead of machine-style cluster/item text",
            severity="low",
        ),
        status(
            isinstance(agentic_crypto, dict)
            and str(agentic_crypto.get("status", "")).lower() in ("fresh", "ok", "ready")
            and str(agentic_crypto.get("walletMode", "")).lower() in ("read-only", "simulation-ready", "approval-required", "execution-enabled")
            and isinstance(crypto_summary.get("totalEstimatedUsd"), (int, float))
            and crypto_summary.get("totalEstimatedUsd", 0) >= 0
            and len(crypto_tokens) >= 4
            and len(crypto_chains) >= 2
            and bool(crypto_wallets.get("evmMasked"))
            and bool(crypto_wallets.get("solanaMasked"))
            and "<span>Full balance</span>" in react_main
            and 'token{tokens.length === 1 ? "" : "s"} listed' in react_main
            and "fmtCurrencyExact(liquidValue)} liquid value" in react_main
            and "smaller token" in react_main
            and "summary?.nftEstimatedUsd" not in react_main
            and "token value" not in react_main
            and "other assets" not in react_main,
            "Agentic Crypto summary",
            (
                f"status={agentic_crypto.get('status', 'missing')}; "
                f"mode={agentic_crypto.get('walletMode', 'missing')}; "
                f"{len(crypto_tokens)} token rows; {len(crypto_chains)} chain rows; masked wallets present"
            ),
            severity="high",
        ),
        status(
            'return { label: "Fresh", status: "fresh", tone: "clear" };' in react_main
            and "Read-only. Actions require approval." in react_main
            and ".agentic-crypto-panel .crypto-title-actions button,\n.agentic-crypto-panel .crypto-status" in react_styles
            and "text-transform: none;" in react_styles,
            "Agentic Crypto plain-language controls",
            "wallet status and action controls avoid shouty uppercase labels while keeping the read-only approval guard visible",
            severity="low",
        ),
        status(
            'className="crypto-token-icon"' in react_main
            and 'className="crypto-token-main"' in react_main
            and 'className="crypto-token-value"' in react_main
            and "grid-template-columns: 26px minmax(0, 1fr) minmax(64px, auto);" in react_styles
            and "amountLabel(token.amount, token.symbol)" in react_main,
            "Agentic Crypto token rows",
            "wallet token rows keep marker, token amount, and value in distinct readable lanes",
            severity="low",
        ),
        status(
            'type SectionCueKey = "brain" | "jobs" | "system" | "signal" | "crypto"' in react_main
            and "function ResourceStack(" in react_main
            and 'cueRowKey("crypto", "balance")' in react_main
            and ".tower-resource-stack" in react_styles
            and ".resource-card" in react_styles
            and ".resource-card.has-row-update" in react_styles
            and "Wallet · Models · Display · Visibility" in react_main,
            "Agentic Crypto live cues",
            "wallet balance remains live-cued inside the compact Resources stack without exposing full wallet details in the first view",
            severity="medium",
        ),
        status(
            'signal: compactSignature({' in react_main
            and "state.signals.slice(0, 10).forEach((row) => {" in react_main
            and 'rows[cueRowKey("signal", row.id || row.title)]' in react_main
            and 'sectionCueClass("signal", liveCues)' in react_main
            and 'liveCues.focus === "signal"' in react_main,
            "Signal Feed live cues",
            "Signal Feed section and story rows now flash subtly when live intelligence data changes",
            severity="medium",
        ),
        status(
            "module-change-sweep" in react_styles
            and ".brain-hero-panel.has-section-update > .section-update-cue" in react_styles
            and ".jobs-rail.has-section-update > .section-update-cue" in react_styles
            and ".agentic-crypto-panel.has-section-update > .section-update-cue" in react_styles
            and ".support-grid .signal-feed.has-section-update > .section-update-cue" in react_styles,
            "Module update sweep",
            "Brain Feed, Today's Jobs, Agentic Crypto, and Signal Feed show a thin top-edge sweep when their data changes",
            severity="low",
        ),
        status(
            V2_PRIORITY_JOBS_PATH.exists()
            and "PRIORITY_JOB_RULES" in priority_jobs
            and "SORARE_DAILY_GROUPS" in priority_jobs
            and "from \"./priorityJobs\"" in react_main,
            "Priority Jobs config",
            "focused job grouping is config-driven instead of embedded in the rail",
            severity="medium",
        ),
        status(
            V2_DATA_ADAPTERS_PATH.exists()
            and "function normalizeStatus(row: unknown" in react_data
            and "normalizeBrainFeedRow(row: unknown" in react_data
            and "isRecord" in data_adapters,
            "Typed data adapters",
            "Supabase and sidecar rows are normalized before rendering",
            severity="medium",
        ),
        status(
            "const JOB_ROW_LIMIT = 64" in react_data
            and "slice(0, JOB_ROW_LIMIT)" in react_data
            and "slice(0, 24)" not in react_data,
            "Today Jobs inventory depth",
            "job merge keeps the full tracked ecosystem inventory instead of the old 24-row cap",
            severity="medium",
        ),
        status(
            "selectLiveSupabaseJobs" in react_data
            and "mergeJobs(selectLiveSupabaseJobs(jobs), fallback.jobs)" in react_data,
            "Today Jobs live merge policy",
            "scheduled inventory stays authoritative while only recent live job rows are layered in",
            severity="medium",
        ),
        status(
            "blockedJobSuperseded" in react_data
            and "STALE_BLOCKER_WINDOW_MS" in react_data
            and "jobTopicsOverlap" in react_data
            and "row.get(\"source\") == \"codex_automation\"" in update_script,
            "Stale blocker suppression",
            "old blocked live rows and stale Codex automation misses cannot keep Mission Control red after a newer clear result",
            severity="medium",
        ),
        status(
            "function operatorTrackedJobs" in react_main
            and "dedupeJobsForView(operatorVisibleJobs(jobs))" in react_main
            and "priority-${job.id || job.agent_id}" in react_main,
            "Today Jobs view dedupe",
            "low-signal maintenance duplicates collapse while priority jobs stay itemized",
            severity="medium",
        ),
        status(
            'function routineCalendarGroupKey(block: CalendarJobBlock)' in react_main
            and 'return `${block.hourKey}-system-checks`;' in react_main
            and 'const isFocus = block.tone === "attention" || (block.tone === "working" && !jobIsRoutineActivity(block.job));' in react_main
            and "invite sync|calendar sync|appointment sync|chiro invite" in react_main
            and 'jobIsSoon(block.job, 5)' not in react_main.split("function buildCalendarJobBlocks", 1)[1].split("function calendarJobsForMode", 1)[0],
            "Today Jobs current-hour routine grouping",
            "same-hour background system and calendar sync checks group into one compact row while priority and attention work stays itemized",
            severity="medium",
        ),
        status(
            "function groupedRoutineDetail(items: CalendarJobBlock[])" in react_main
            and "agent readiness checks" in react_main
            and '"System checks"' in react_main
            and "routine ${noun} · ${agentLabel}" in react_main
            and "signal ${noun} · J.A.I.N" in react_main
            and "detail: items.length === 1 ? firstBlock.detail : groupedRoutineDetail(items)" in react_main
            and "items.slice(0, 3).map((item) => item.title).join" not in react_main,
            "Today Jobs grouped routine summaries",
            "grouped routine calendar rows use compact calendar summaries instead of raw internal job-name chains",
            severity="medium",
        ),
        status(
            "function jobMergeKey" in react_data
            and "live-${owner}-${title}" in react_data
            and "hasScheduledJobFingerprint" in react_data,
            "Today Jobs live dedupe",
            "duplicate live maintenance rows collapse without merging separate scheduled jobs",
            severity="medium",
        ),
        status(
            V2_FAVICON_PATH.exists()
            and 'rel="icon"' in v2_index
            and "favicon.svg" in v2_index,
            "React favicon",
            "kiosk has an explicit Mission Control icon and no default favicon.ico miss",
            severity="low",
        ),
    ]

    failed = [c for c in checks if not c["ok"]]
    out = {
        "ok": not failed,
        "status": "ok" if not failed else "attention",
        "summary": "All canaries passed" if not failed else f"{len(failed)} canary issue(s)",
        "checkedAt": utc_now(),
        "checks": checks,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
