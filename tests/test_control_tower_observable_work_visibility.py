import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "v2-react" / "src" / "main.tsx"
STYLES = ROOT / "v2-react" / "src" / "styles.css"


class ObservableWorkVisibilityTests(unittest.TestCase):
    def test_live_work_board_adapts_row_density_from_verified_state(self) -> None:
        source = MAIN.read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")
        density = source[source.index("type AgentExpansionMap"):source.index("function workerObjectiveForRoute")]
        board = source[source.index("function BrainHero("):source.index("type LiveRunInspectorRow")]
        card = source[source.index("function AgentHeroCard("):source.index("type MetricTone")]

        self.assertIn("AGENT_ROW_COLLAPSE_DELAY_MS = 1_800", density)
        self.assertIn("function agentRowRequestsExpansion", density)
        self.assertIn("activityMode", density)
        self.assertIn("hasFreshActiveStatus", density)
        self.assertIn("isFreshActiveTimestamp(liveWork.status.updated_at)", density)
        self.assertIn('liveWork.visualState === "waiting"', density)
        self.assertIn('liveWork.visualState === "blocked"', density)
        self.assertIn("hasActiveWorker", density)
        self.assertIn("hasActiveLease", density)
        self.assertNotIn("awaiting instruction", density.lower())
        self.assertIn("function useAdaptiveAgentExpansion", density)
        self.assertIn("window.setTimeout", density)
        self.assertIn("function adaptiveAgentGridRows", density)
        self.assertIn('"minmax(44px, 0.5fr)"', density)
        self.assertIn('className="brain-agent-grid is-adaptive-density"', board)
        self.assertIn('"--agent-grid-rows": agentGridRows', board)
        self.assertIn('density={expandedRows[agent] ? "expanded" : "compact"}', board)
        self.assertIn('data-density={density}', card)
        self.assertIn(".brain-agent-grid.is-adaptive-density", styles)
        self.assertIn("transition: grid-template-rows 360ms", styles)
        self.assertIn(".agent-hero-card.is-density-compact", styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)

    def test_run_inspector_is_bounded_and_reuses_existing_detail_disclosure(self) -> None:
        source = MAIN.read_text(encoding="utf-8")
        details = source[source.index("{showDetails ? ("):source.index("function objectRows")]
        inspector = source[source.index("type LiveRunInspectorRow"):source.index("function objectRows")]

        self.assertIn("<LiveRunInspector state={state} statuses={statuses} />", details)
        self.assertIn("const LiveRunInspector = memo", inspector)
        self.assertIn(".slice(0, 6)", inspector)
        self.assertIn('role="tree"', inspector)
        self.assertIn('role="treeitem"', inspector)
        self.assertIn("controllerWorkId === controllerWorkId", inspector)
        self.assertIn("controllerRunId === controllerRunId", inspector)
        self.assertIn("const statusIdentifiesRun", inspector)
        self.assertIn("!statusIdentifiesRun ? ownedControllers[0] : null", inspector)
        self.assertIn("never private reasoning", inspector)

    def test_live_refresh_transitions_only_non_loading_updates(self) -> None:
        source = MAIN.read_text(encoding="utf-8")
        refresh = source[source.index("const refresh = useCallback"):source.index("const refreshAgenticCrypto")]

        self.assertIn("if (refreshLoadingThroughVersionRef.current > 0)", refresh)
        self.assertIn("setState(next);", refresh)
        self.assertIn("startTransition(() => setState(next));", refresh)

    def test_brain_atlas_agent_nodes_join_work_and_memory_evidence_without_reasoning(self) -> None:
        source = MAIN.read_text(encoding="utf-8")
        atlas_nodes = source[source.index("{flowAgents.map((row, index) => {"):source.index("<g className={`memory-flow-node is-recall")]

        self.assertIn("const observedPhase", atlas_nodes)
        self.assertIn("const observedTool", atlas_nodes)
        self.assertIn("const memoryState", atlas_nodes)
        self.assertIn('data-observable-state-label={promptReceived ? "RECEIVED" : activityMode ? `ACTIVE · ${activityMode.toUpperCase()}` : "QUIET"}', atlas_nodes)
        self.assertIn('data-agent-activity={activityMode || "quiet"}', atlas_nodes)
        self.assertIn("Observable execution metadata only, not private reasoning", atlas_nodes)

    def test_live_work_and_brain_atlas_share_verified_activity_and_worker_lanes(self) -> None:
        source = MAIN.read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")
        card = source[source.index("function AgentHeroCard("):source.index("type MetricTone")]
        atlas = source[source.index("function BrainAtlasPanel("):source.index("function AgentWorkBoard(")]

        self.assertIn("function agentActivityMode", source)
        self.assertIn("verifiedWorkerRoutesForAgent", source)
        self.assertIn('className={`agent-activity-indicator is-${activityMode}`}', card)
        self.assertIn('role="status"', card)
        self.assertIn('data-agent-activity={activityMode || "quiet"}', card)
        self.assertIn("workerObjectiveForRoute", card)
        self.assertIn("workerRoutesByAgent", atlas)
        self.assertIn('className="memory-flow-worker-lane"', atlas)
        self.assertIn('data-worker-count={workerRoutes.length}', atlas)
        self.assertIn(".agent-activity-indicator", styles)
        self.assertIn("@keyframes agent-activity-wave", styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)

    def test_new_prompt_receipt_is_immediate_but_does_not_claim_active_work(self) -> None:
        source = MAIN.read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")
        card = source[source.index("function AgentHeroCard("):source.index("type MetricTone")]
        atlas = source[source.index("function BrainAtlasPanel("):source.index("function AgentWorkBoard(")]

        self.assertIn('type AgentActivityMode = "received" | "thinking" | "working";', source)
        self.assertIn('const PROMPT_RECEIPT_STATUSES = new Set(["queued", "accepted", "planned", "routed", "pending"]);', source)
        self.assertIn("PROMPT_RECEIPT_FRESH_MINUTES = 10", source)
        self.assertIn("agentPromptReceiptIsFresh", source)
        self.assertIn('title: `Received: ${headlineTitle(activeReadout.objective, 58)}`', source)
        self.assertIn('data-agent-working={visualState === "working" ? "true" : "false"}', card)
        self.assertIn('promptReceived ? "is-prompt-received"', card)
        self.assertIn('activityMode === "received" ? "Received"', card)
        self.assertIn('promptReceived ? " is-prompt-received"', atlas)
        self.assertIn('data-work-state={working ? "working" : promptReceived ? "received" : "quiet"}', atlas)
        self.assertIn("no work or memory flow implied", atlas)
        self.assertIn(".agent-activity-indicator.is-received", styles)
        self.assertIn("@keyframes agent-received-wave", styles)
        self.assertIn(".memory-flow-node.is-prompt-received", styles)

    def test_run_inspector_has_render_and_overflow_guards(self) -> None:
        styles = STYLES.read_text(encoding="utf-8")
        inspector = styles[styles.index("/* Bounded work visibility"):styles.index("@media (max-width: 900px)")]

        self.assertIn("contain: layout paint style;", inspector)
        self.assertIn("max-height: 286px;", inspector)
        self.assertIn("overflow: auto;", inspector)
        self.assertIn(".brain-hero > .live-run-inspector", styles)
        self.assertIn("inset: 58px 10px 10px;", styles)

    def test_live_work_cards_render_one_distance_readable_headline(self) -> None:
        source = MAIN.read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")
        card = source[source.index("function AgentHeroCard("):source.index("type MetricTone")]

        self.assertIn('className="agent-objective-main is-headline-entry">{headline.title}</span>', card)
        self.assertEqual(card.count('className="agent-objective-main is-headline-entry"'), 1)
        self.assertIn("title={headline.description}", card)
        self.assertNotIn("agent-objective-description", card)
        self.assertNotIn("agent-support-note", card)
        self.assertIn("font-size: clamp(24px, 1.75vw, 36px) !important;", styles)
        self.assertIn('grid-template-areas: "agent objective route" !important;', styles)

    def test_brain_atlas_adds_static_reuse_and_provenance_visuals(self) -> None:
        source = MAIN.read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")
        atlas = source[source.index("function BrainAtlasPanel("):source.index("function AgentWorkBoard(")]

        self.assertIn("const historicalReuseLinks", atlas)
        self.assertIn("diagnostics?.reuseMatrix.cells", atlas)
        self.assertIn('data-use-count={link.uses}', atlas)
        self.assertIn("memory-flow-node-provenance-track", atlas)
        self.assertIn("memory-flow-node-provenance-value", atlas)
        self.assertIn('pathLength="100"', atlas)
        self.assertIn("VERIFIED REUSE / 30D", atlas)
        self.assertIn(".memory-flow-edge.is-historical-reuse", styles)
        self.assertIn("animation: none !important;", styles)


if __name__ == "__main__":
    unittest.main()
