from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MISSION_CONTROL = Path(__file__).resolve().parents[1]
UPDATE_SCRIPT = MISSION_CONTROL / "scripts" / "update_mission_control.py"


def load_update_module():
    spec = importlib.util.spec_from_file_location("staged_update_mission_control", UPDATE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load staged dashboard generator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_atlas() -> dict:
    generated = "2026-07-18T16:00:00Z"
    agent = "agent:josh2"
    work = "work:" + "1" * 24
    receipt = "receipt:" + "2" * 24
    model = "model:" + "3" * 24
    return {
        "schemaVersion": 1,
        "generatedAt": generated,
        "status": "ready",
        "empty": False,
        "emptyReason": None,
        "source": {
            "name": "control-tower-work-ledger",
            "verified": True,
            "schemaVersion": 1,
            "revision": 14,
        },
        "window": {
            "days": 7,
            "start": "2026-07-11T16:00:00Z",
            "end": generated,
        },
        "limits": {"maxNodes": 100, "hardMaxNodes": 100},
        "counts": {
            "nodes": 4,
            "edges": 3,
            "agents": 1,
            "works": 1,
            "receipts": 1,
            "models": 1,
            "sourceRowsInWindow": 1,
            "excluded": {
                "timeOutOfWindow": 0,
                "legacyOrInvalid": 0,
                "capacityReceipts": 0,
                "capacityRoutes": 0,
                "unverifiedRoutes": 0,
                "unsafeVerifiedRoutes": 0,
            },
        },
        # The presentation boundary must never forward generator policy text.
        "policy": {"content": "not part of dashboard state"},
        "nodes": [
            {
                "id": agent,
                "kind": "agent",
                "label": "JOSH 2.0",
                "observedAt": generated,
                "receiptCount": 1,
            },
            {
                "id": work,
                "kind": "work",
                "label": "Work 11111111",
                "status": "done",
                "observedAt": generated,
                "receiptCount": 1,
                "generation": 1,
            },
            {
                "id": receipt,
                "kind": "receipt",
                "label": "Terminal receipt",
                "status": "done",
                "observedAt": generated,
                "receiptCount": 1,
                "generation": 1,
                "sequence": 1,
                "routeVerified": True,
            },
            {
                "id": model,
                "kind": "model",
                "label": "codex/gpt-5.6",
                "observedAt": generated,
                "receiptCount": 1,
                "family": "codex",
                "modelId": "gpt-5.6",
            },
        ],
        "edges": [
            {
                "id": "edge:" + "4" * 24,
                "kind": "owns",
                "source": agent,
                "target": work,
                "evidenceReceipt": receipt,
                "observedAt": generated,
            },
            {
                "id": "edge:" + "5" * 24,
                "kind": "emitted",
                "source": work,
                "target": receipt,
                "evidenceReceipt": receipt,
                "observedAt": generated,
            },
            {
                "id": "edge:" + "6" * 24,
                "kind": "verified-route",
                "source": receipt,
                "target": model,
                "evidenceReceipt": receipt,
                "observedAt": generated,
            },
        ],
    }


class BrainAtlasDashboardIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.update = load_update_module()

    def test_valid_exact_graph_is_sanitized_and_projected(self) -> None:
        source = valid_atlas()
        source["policy"]["privateNote"] = "never-forward-this"
        clean = self.update.sanitize_brain_atlas(source, "2026-07-18T16:01:00Z")

        self.assertEqual("ready", clean["status"])
        self.assertEqual(4, clean["counts"]["nodes"])
        self.assertNotIn("policy", clean)
        self.assertNotIn("never-forward-this", json.dumps(clean))
        self.assertEqual(
            ["owns", "emitted", "verified-route"],
            [edge["kind"] for edge in clean["edges"]],
        )
        self.assertEqual(
            ["id", "kind", "label", "observedAt", "receiptCount"],
            list(clean["nodes"][0]),
        )
        self.assertEqual(
            ["id", "kind", "source", "target", "evidenceReceipt", "observedAt"],
            list(clean["edges"][0]),
        )

    def test_arbitrary_content_fails_closed_without_echoing_content(self) -> None:
        source = valid_atlas()
        secret = "raw-objective-private-account-123"
        source["nodes"][1]["objective"] = secret
        source["nodes"][1]["label"] = secret

        clean = self.update.sanitize_brain_atlas(source, "2026-07-18T16:01:00Z")

        self.assertEqual("unavailable", clean["status"])
        self.assertEqual("generated-payload-invalid", clean["emptyReason"])
        self.assertEqual([], clean["nodes"])
        self.assertNotIn(secret, json.dumps(clean))

    def test_inferred_or_dangling_edges_fail_closed(self) -> None:
        source = valid_atlas()
        source["edges"][0]["evidenceReceipt"] = "receipt:" + "9" * 24

        clean = self.update.sanitize_brain_atlas(source, "2026-07-18T16:01:00Z")

        self.assertEqual("unavailable", clean["status"])
        self.assertEqual([], clean["edges"])

    def test_scope_and_cap_are_hard_validation_boundaries(self) -> None:
        source = valid_atlas()
        source["window"]["days"] = 30
        source["limits"]["maxNodes"] = 101

        clean = self.update.sanitize_brain_atlas(source, "2026-07-18T16:01:00Z")

        self.assertEqual("unavailable", clean["status"])
        self.assertEqual(7, clean["window"]["days"])
        self.assertEqual(100, clean["limits"]["hardMaxNodes"])

    def test_missing_source_has_visible_unavailable_state(self) -> None:
        clean = self.update.sanitize_brain_atlas(None, "2026-07-18T16:01:00Z")

        self.assertEqual("unavailable", clean["status"])
        self.assertEqual("source-missing", clean["emptyReason"])
        self.assertFalse(clean["source"]["verified"])

    def test_staged_live_dashboard_write_contains_sanitized_atlas(self) -> None:
        dashboard = {
            "lastUpdated": "2026-07-18T16:01:00Z",
            "brainAtlas": valid_atlas(),
            "privateAuditPayload": "not-live",
        }
        live = self.update.build_live_dashboard(dashboard)

        with tempfile.TemporaryDirectory(prefix="brain-atlas-dashboard-test-") as directory:
            target = Path(directory) / "control-tower-live.json"
            self.update.atomic_write_json(target, live)
            written = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual("ready", written["brainAtlas"]["status"])
        self.assertNotIn("policy", written["brainAtlas"])
        self.assertNotIn("privateAuditPayload", written)

    def test_sanitized_atlas_is_idempotent_for_the_live_projection(self) -> None:
        clean = self.update.sanitize_brain_atlas(
            valid_atlas(),
            "2026-07-18T16:01:00Z",
        )
        second_pass = self.update.sanitize_brain_atlas(
            clean,
            "2026-07-18T16:01:00Z",
        )
        live = self.update.build_live_dashboard({
            "lastUpdated": "2026-07-18T16:01:00Z",
            "brainAtlas": clean,
        })

        self.assertEqual(clean, second_pass)
        self.assertEqual(clean, live["brainAtlas"])
        self.assertEqual("ready", live["brainAtlas"]["status"])
        self.assertNotIn("policy", live["brainAtlas"])

    def test_generated_stage_atlas_round_trips_when_present(self) -> None:
        generated_path = MISSION_CONTROL / "data" / "brain-atlas.json"
        if not generated_path.is_file():
            self.skipTest("focused unit stage has no generated ledger fixture")
        source = json.loads(generated_path.read_text(encoding="utf-8"))
        clean = self.update.sanitize_brain_atlas(source, source["generatedAt"])
        live = self.update.build_live_dashboard({
            "lastUpdated": source["generatedAt"],
            "brainAtlas": source,
        })
        with tempfile.TemporaryDirectory(prefix="brain-atlas-real-stage-") as directory:
            target = Path(directory) / "control-tower-live.json"
            self.update.atomic_write_json(target, live)
            written = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual(source["status"], clean["status"])
        self.assertEqual(source["counts"]["nodes"], clean["counts"]["nodes"])
        self.assertLessEqual(clean["counts"]["nodes"], 100)
        self.assertNotIn("policy", clean)
        self.assertTrue(all(edge["kind"] in {"owns", "emitted", "verified-route"} for edge in clean["edges"]))
        self.assertEqual(clean, written["brainAtlas"])

    def test_ui_contract_is_fixed_layered_and_motionless(self) -> None:
        main = (MISSION_CONTROL / "v2-react" / "src" / "main.tsx").read_text(encoding="utf-8")
        styles = (MISSION_CONTROL / "v2-react" / "src" / "styles.css").read_text(encoding="utf-8")
        component = main[main.index("const BRAIN_ATLAS_LAYER_ORDER"):main.index("function AgentWorkBoard")]
        atlas_styles = styles[styles.index("/* Brain Atlas is a fixed evidence diagram") :]

        self.assertIn('["agent", "work", "receipt", "model"]', component)
        self.assertIn("Exact receipt edges only", component)
        self.assertIn("No inferred relationships are shown", component)
        self.assertNotIn("forceSimulation", component)
        self.assertNotIn("d3-force", component)
        self.assertNotIn("animation:", atlas_styles)

    def test_atlas_uses_an_operator_tab_without_compressing_finops(self) -> None:
        main = (MISSION_CONTROL / "v2-react" / "src" / "main.tsx").read_text(encoding="utf-8")
        styles = (MISSION_CONTROL / "v2-react" / "src" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('useState<"finops" | "atlas">("finops")', main)
        self.assertIn('data-support-view="atlas"', main)
        self.assertIn('supportView === "atlas" ? (', main)
        self.assertIn(".support-grid.is-support-tabbed", styles)
        self.assertNotIn(":has(.brain-atlas-panel):has(.finops-dashboard)", styles)

    def test_handoff_receipt_bridge_support_is_preserved(self) -> None:
        source = UPDATE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("from handoff_receipt_bridge import receipt_state, terminal_result_receipt", source)
        self.assertIn('"brainAtlas"', source)


if __name__ == "__main__":
    unittest.main()
