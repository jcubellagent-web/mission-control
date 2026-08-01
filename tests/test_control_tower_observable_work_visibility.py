import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "v2-react" / "src" / "main.tsx"
STYLES = ROOT / "v2-react" / "src" / "styles.css"


class ObservableWorkVisibilityTests(unittest.TestCase):
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
        self.assertIn('data-observable-state-label={working ? "ACTIVE · WORKING" : "QUIET"}', atlas_nodes)
        self.assertIn("Observable execution metadata only, not private reasoning", atlas_nodes)

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

        self.assertIn('<span className="agent-objective-main">{headline.title}</span>', card)
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
