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
            "schemaVersion": 2,
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
                "label": "Refresh Control Tower health",
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


def valid_memory_operations() -> dict:
    generated = "2026-07-18T16:00:00Z"
    retrieval_at = "2026-07-18T15:59:00Z"
    return {
        "updatedAt": generated,
        "status": "watch",
        "summary": "Two governed candidates are awaiting review.",
        "registry": {"active": 10, "superseded": 1, "expired": 2, "sources": 4},
        "review": {
            "pending": 2,
            "disputed": 0,
            "lastRun": "2026-07-18T15:00:00Z",
            "lastStatus": "ok",
        },
        "retrieval": {
            "queries7d": 100,
            "hits7d": 90,
            "hitRate": 90.0,
            "avgLatencyMs": 1.2,
            "feedback30d": 10,
            "helpful30d": 6,
            "ignored30d": 3,
            "corrected30d": 1,
            "harmful30d": 0,
            "qualityRate": 60.0,
            "qualityDefinition": "helpful feedback divided by all feedback, including ignored, corrected, and harmful",
            "preflights7d": 2,
            "selected30d": 5,
            "used30d": 4,
            "reuseIgnored30d": 1,
            "selectedUseRate": 80.0,
        },
        "governance": {
            "sourceOfTruth": "Checked-in operating rules",
            "autoPromote": "Verified low-risk facts only",
            "manualReview": "Sensitive changes require review",
            "privacy": "Owner-scoped by default",
        },
        "agentAccess": {
            "josh2": "local CLI",
            "jaimes": "shared SSH client",
            "jain": "shared SSH client",
            "joshex": "oversight SSH client",
        },
        "activity": {
            "schemaVersion": 2,
            "generatedAt": generated,
            "windowMinutes": 30,
            "motionWindowSeconds": 90,
            "source": {"name": "governed-memory-registry", "verified": True},
            "privacy": {
                "queryIncluded": False,
                "contentIncluded": False,
                "rawIdentifiersIncluded": False,
                "reasonsIncluded": False,
                "countsOnly": True,
            },
            "counts": {
                "retrievals": 1,
                "hits": 1,
                "misses": 0,
                "selected": 1,
                "used": 1,
                "crossAgentUsed": 1,
                "reuseIgnored": 0,
                "feedback": 1,
                "helpful": 1,
                "feedbackIgnored": 0,
                "corrected": 0,
                "harmful": 0,
                "proposed": 0,
                "promoted": 0,
            },
            "lastObservedAt": {
                "retrieval": retrieval_at,
                "hit": retrieval_at,
                "miss": None,
                "selected": "2026-07-18T15:59:05Z",
                "used": "2026-07-18T15:59:10Z",
                "crossAgentUsed": "2026-07-18T15:59:10Z",
                "reuseIgnored": None,
                "feedback": "2026-07-18T15:59:10Z",
                "corrected": None,
                "proposed": None,
                "promoted": None,
            },
            "agents": [
                {
                    "agent": "joshex",
                    "retrievals": 1,
                    "hits": 1,
                    "misses": 0,
                    "selected": 1,
                    "used": 1,
                    "crossAgentUsed": 1,
                    "lastRetrievalAt": retrieval_at,
                    "lastSelectedAt": "2026-07-18T15:59:05Z",
                    "lastUsedAt": "2026-07-18T15:59:10Z",
                    "lastCrossAgentUsedAt": "2026-07-18T15:59:10Z",
                },
                {"agent": "josh2", "retrievals": 0, "hits": 0, "misses": 0, "selected": 0, "used": 0, "crossAgentUsed": 0, "lastRetrievalAt": None, "lastSelectedAt": None, "lastUsedAt": None, "lastCrossAgentUsedAt": None},
                {"agent": "jaimes", "retrievals": 0, "hits": 0, "misses": 0, "selected": 0, "used": 0, "crossAgentUsed": 0, "lastRetrievalAt": None, "lastSelectedAt": None, "lastUsedAt": None, "lastCrossAgentUsedAt": None},
                {"agent": "jain", "retrievals": 0, "hits": 0, "misses": 0, "selected": 0, "used": 0, "crossAgentUsed": 0, "lastRetrievalAt": None, "lastSelectedAt": None, "lastUsedAt": None, "lastCrossAgentUsedAt": None},
            ],
            "reuseLinks": [
                {"sourceAgent": "jaimes", "consumerAgent": "joshex", "uses": 1, "lastUsedAt": "2026-07-18T15:59:10Z"},
            ],
        },
        "privacy": {
            "checkedAt": generated,
            "ok": True,
            "policy": "deny-by-default",
            "publicLabels": ["dashboard-safe", "public"],
            "activePublic": 8,
            "activeOwnerPrivate": 2,
            "unknownLabelsOwnerScoped": 0,
            "crossOwnerPrivateLeaks": 0,
        },
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
        self.assertEqual("Refresh Control Tower health", clean["nodes"][1]["label"])
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

    def test_work_label_boundary_accepts_plain_titles_and_rejects_opaque_or_private_content(self) -> None:
        safe = valid_atlas()
        safe["nodes"][1]["label"] = "Verify Telegram handoff receipts"

        clean = self.update.sanitize_brain_atlas(safe, "2026-07-18T16:01:00Z")

        self.assertEqual("ready", clean["status"])
        self.assertEqual("Verify Telegram handoff receipts", clean["nodes"][1]["label"])

        unsafe_labels = (
            "Work 11111111",
            "operator@example.com",
            "/Users/operator/private/account.json",
            "api_key:supersecretvalue123",
        )
        for unsafe_label in unsafe_labels:
            with self.subTest(unsafe_label=unsafe_label):
                source = valid_atlas()
                source["nodes"][1]["label"] = unsafe_label
                rejected = self.update.sanitize_brain_atlas(
                    source,
                    "2026-07-18T16:01:00Z",
                )

                self.assertEqual("unavailable", rejected["status"])
                self.assertEqual("generated-payload-invalid", rejected["emptyReason"])
                self.assertEqual([], rejected["nodes"])
                self.assertNotIn(unsafe_label, json.dumps(rejected))

    def test_inferred_or_dangling_edges_fail_closed(self) -> None:
        source = valid_atlas()
        source["edges"][0]["evidenceReceipt"] = "receipt:" + "9" * 24

        clean = self.update.sanitize_brain_atlas(source, "2026-07-18T16:01:00Z")

        self.assertEqual("unavailable", clean["status"])
        self.assertEqual([], clean["edges"])

    def test_verified_route_requires_the_receipt_verified_bit(self) -> None:
        source = valid_atlas()
        source["nodes"][2]["routeVerified"] = False

        clean = self.update.sanitize_brain_atlas(source, "2026-07-18T16:01:00Z")

        self.assertEqual("unavailable", clean["status"])
        self.assertEqual([], clean["edges"])

    def test_each_work_receipt_has_exactly_one_canonical_owner(self) -> None:
        source = valid_atlas()
        source["nodes"].append({
            "id": "agent:jaimes",
            "kind": "agent",
            "label": "JAIMES",
            "observedAt": source["generatedAt"],
            "receiptCount": 1,
        })
        source["edges"].append({
            "id": "edge:" + "7" * 24,
            "kind": "owns",
            "source": "agent:jaimes",
            "target": source["nodes"][1]["id"],
            "evidenceReceipt": source["nodes"][2]["id"],
            "observedAt": source["generatedAt"],
        })
        source["counts"]["nodes"] += 1
        source["counts"]["agents"] += 1
        source["counts"]["edges"] += 1

        clean = self.update.sanitize_brain_atlas(source, "2026-07-18T16:01:00Z")

        self.assertEqual("unavailable", clean["status"])
        self.assertEqual([], clean["edges"])

    def test_duplicate_emitted_or_verified_paths_fail_closed(self) -> None:
        for edge_index in (1, 2):
            with self.subTest(kind=valid_atlas()["edges"][edge_index]["kind"]):
                source = valid_atlas()
                duplicate = copy.deepcopy(source["edges"][edge_index])
                duplicate["id"] = "edge:" + ("7" if edge_index == 1 else "8") * 24
                source["edges"].append(duplicate)
                source["counts"]["edges"] += 1

                clean = self.update.sanitize_brain_atlas(
                    source,
                    "2026-07-18T16:01:00Z",
                )

                self.assertEqual("unavailable", clean["status"])
                self.assertEqual([], clean["edges"])

    def test_secret_shaped_model_id_fails_closed(self) -> None:
        source = valid_atlas()
        secret_model = "sk-proj-ABCDEFGHIJKLMN123456"
        source["nodes"][3]["modelId"] = secret_model
        source["nodes"][3]["label"] = f"codex/{secret_model}"

        clean = self.update.sanitize_brain_atlas(source, "2026-07-18T16:01:00Z")

        self.assertEqual("unavailable", clean["status"])
        self.assertNotIn(secret_model, json.dumps(clean))

    def test_scope_and_cap_are_hard_validation_boundaries(self) -> None:
        source = valid_atlas()
        source["window"]["days"] = 30
        source["limits"]["maxNodes"] = 101

        clean = self.update.sanitize_brain_atlas(source, "2026-07-18T16:01:00Z")

        self.assertEqual("unavailable", clean["status"])
        self.assertEqual(7, clean["window"]["days"])
        self.assertEqual(100, clean["limits"]["hardMaxNodes"])

    def test_declared_one_to_seven_day_windows_round_trip_exactly(self) -> None:
        for days in range(1, 8):
            source = valid_atlas()
            source["window"]["days"] = days
            source["window"]["start"] = f"2026-07-{18 - days:02d}T16:00:00Z"

            with self.subTest(days=days):
                clean = self.update.sanitize_brain_atlas(source, "2026-07-18T16:01:00Z")
                self.assertEqual("ready", clean["status"])
                self.assertEqual(days, clean["window"]["days"])
                self.assertEqual(source["window"], clean["window"])

    def test_declared_window_days_must_match_exact_duration(self) -> None:
        for days in (0, 8):
            source = valid_atlas()
            source["window"]["days"] = days
            clean = self.update.sanitize_brain_atlas(source, "2026-07-18T16:01:00Z")
            self.assertEqual("unavailable", clean["status"])

        mismatched = valid_atlas()
        mismatched["window"]["days"] = 1
        clean = self.update.sanitize_brain_atlas(mismatched, "2026-07-18T16:01:00Z")
        self.assertEqual("unavailable", clean["status"])

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
            self.update.atomic_write_json(target, live, compact=True)
            written = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual("ready", written["brainAtlas"]["status"])
        self.assertNotIn("policy", written["brainAtlas"])
        self.assertNotIn("privateAuditPayload", written)

    def test_live_dashboard_is_compact_while_audit_json_stays_readable(self) -> None:
        payload = {
            "lastUpdated": "2026-07-18T16:01:00Z",
            "brainAtlas": valid_atlas(),
            "metrics": {"latencyMs": 12.0, "label": "retrieval → applied"},
        }

        with tempfile.TemporaryDirectory(prefix="brain-atlas-json-format-") as directory:
            live_target = Path(directory) / "control-tower-live.json"
            audit_target = Path(directory) / "dashboard-data.json"
            self.update.atomic_write_json(live_target, payload, compact=True)
            self.update.atomic_write_json(audit_target, payload)
            live_text = live_target.read_text(encoding="utf-8")
            audit_text = audit_target.read_text(encoding="utf-8")

        self.assertEqual(payload, json.loads(live_text))
        self.assertEqual(payload, json.loads(audit_text))
        self.assertTrue(live_text.endswith("\n"))
        self.assertNotIn("\n  ", live_text)
        self.assertIn('"latencyMs":12.0', live_text)
        self.assertIn("\\u2192", live_text)
        self.assertIn("\n  ", audit_text)
        self.assertIn('"latencyMs": 12.0', audit_text)
        self.assertIn("\\u2192", audit_text)
        self.assertLess(len(live_text.encode("utf-8")), len(audit_text.encode("utf-8")))

    def test_compact_live_serialization_keeps_large_payload_under_hard_max(self) -> None:
        payload = {
            "rows": [
                {"label": "x" * 20, "value": 1.0}
                for _ in range(4_000)
            ],
        }

        with tempfile.TemporaryDirectory(prefix="brain-atlas-json-size-") as directory:
            live_target = Path(directory) / "control-tower-live.json"
            audit_target = Path(directory) / "dashboard-data.json"
            self.update.atomic_write_json(live_target, payload, compact=True)
            self.update.atomic_write_json(audit_target, payload)
            live_text = live_target.read_text(encoding="utf-8")
            audit_text = audit_target.read_text(encoding="utf-8")

        self.assertEqual(payload, json.loads(live_text))
        self.assertGreater(len(audit_text.encode("utf-8")), 250_000)
        self.assertLess(len(live_text.encode("utf-8")), 250_000)

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

    def test_memory_operations_projects_only_bounded_counts_and_timestamps(self) -> None:
        source = valid_memory_operations()
        clean = self.update.sanitize_memory_operations(
            source,
            "2026-07-18T16:01:00Z",
        )
        second_pass = self.update.sanitize_memory_operations(
            clean,
            "2026-07-18T16:01:00Z",
        )

        self.assertEqual("ready", clean["status"])
        self.assertEqual(
            {
                "schemaVersion", "updatedAt", "status", "source", "privacy",
                "registry", "review", "retrieval", "activity",
            },
            set(clean),
        )
        self.assertEqual(
            {"name": "governed-memory-registry", "verified": True},
            clean["source"],
        )
        self.assertEqual(
            ["joshex", "josh2", "jaimes", "jain"],
            [row["agent"] for row in clean["activity"]["agents"]],
        )
        self.assertEqual(clean, second_pass)
        serialized = json.dumps(clean)
        for private_field in ("summary", "governance", "agentAccess", "qualityDefinition", "publicLabels"):
            self.assertNotIn(private_field, serialized)

    def test_memory_operations_rejects_content_and_unknown_fields_without_echo(self) -> None:
        secret = "private-memory-content-never-publish"
        mutations = []

        top_level = valid_memory_operations()
        top_level["memoryContent"] = secret
        mutations.append(top_level)

        activity_content = valid_memory_operations()
        activity_content["activity"]["query"] = secret
        mutations.append(activity_content)

        agent_identifier = valid_memory_operations()
        agent_identifier["activity"]["agents"][0]["memoryId"] = secret
        mutations.append(agent_identifier)

        for source in mutations:
            with self.subTest(keys=sorted(source)):
                clean = self.update.sanitize_memory_operations(
                    source,
                    "2026-07-18T16:01:00Z",
                )
                self.assertEqual("unavailable", clean["status"])
                self.assertFalse(clean["source"]["verified"])
                self.assertNotIn(secret, json.dumps(clean))

    def test_memory_operations_rejects_forged_times_or_unverified_source(self) -> None:
        forged = valid_memory_operations()
        forged["activity"]["lastObservedAt"]["retrieval"] = "2026-07-18T18:00:00Z"
        forged["activity"]["agents"][0]["lastRetrievalAt"] = "2026-07-18T18:00:00Z"

        unverified = valid_memory_operations()
        unverified["activity"]["source"]["verified"] = False

        for source in (forged, unverified):
            clean = self.update.sanitize_memory_operations(
                source,
                "2026-07-18T16:01:00Z",
            )
            self.assertEqual("unavailable", clean["status"])
            self.assertEqual(0, clean["activity"]["counts"]["retrievals"])
            self.assertIsNone(clean["activity"]["lastObservedAt"]["retrieval"])

    def test_memory_operations_requires_each_canonical_agent_and_privacy_flag(self) -> None:
        duplicate_agent = valid_memory_operations()
        duplicate_agent["activity"]["agents"][3]["agent"] = "joshex"

        unsafe_privacy = valid_memory_operations()
        unsafe_privacy["activity"]["privacy"]["contentIncluded"] = True

        for source in (duplicate_agent, unsafe_privacy):
            clean = self.update.sanitize_memory_operations(
                source,
                "2026-07-18T16:01:00Z",
            )
            self.assertEqual("unavailable", clean["status"])

    def test_memory_operations_enforces_count_and_motion_window_caps(self) -> None:
        excessive_count = valid_memory_operations()
        excessive_count["activity"]["counts"]["retrievals"] = 100_001
        excessive_count["activity"]["counts"]["hits"] = 100_001
        excessive_count["activity"]["agents"][0]["retrievals"] = 100_001
        excessive_count["activity"]["agents"][0]["hits"] = 100_001

        excessive_window = valid_memory_operations()
        excessive_window["activity"]["windowMinutes"] = 121

        for source in (excessive_count, excessive_window):
            clean = self.update.sanitize_memory_operations(
                source,
                "2026-07-18T16:01:00Z",
            )
            self.assertEqual("unavailable", clean["status"])

    def test_live_dashboard_resanitizes_memory_operations(self) -> None:
        source = valid_memory_operations()
        clean = self.update.sanitize_memory_operations(
            source,
            "2026-07-18T16:01:00Z",
        )
        clean["activity"]["query"] = "injected-after-initial-load"

        live = self.update.build_live_dashboard({
            "lastUpdated": "2026-07-18T16:01:00Z",
            "memoryOperations": clean,
        })

        self.assertEqual("unavailable", live["memoryOperations"]["status"])
        self.assertNotIn("injected-after-initial-load", json.dumps(live))

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
            self.update.atomic_write_json(target, live, compact=True)
            written = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual(source["status"], clean["status"])
        self.assertEqual(source["counts"]["nodes"], clean["counts"]["nodes"])
        self.assertLessEqual(clean["counts"]["nodes"], 100)
        self.assertNotIn("policy", clean)
        self.assertTrue(all(edge["kind"] in {"owns", "emitted", "verified-route"} for edge in clean["edges"]))
        self.assertEqual(clean, written["brainAtlas"])

    def test_ui_contract_is_one_unified_observable_graph_with_exact_static_proof(self) -> None:
        main = (MISSION_CONTROL / "v2-react" / "src" / "main.tsx").read_text(encoding="utf-8")
        styles = (MISSION_CONTROL / "v2-react" / "src" / "styles.css").read_text(encoding="utf-8")
        component = main[main.index("const BRAIN_ATLAS_LAYER_ORDER"):main.index("function AgentWorkBoard")]
        unified_svg = component[component.index("<svg"):component.index("</svg>")]

        self.assertIn('["agent", "work", "receipt", "model"]', component)
        self.assertEqual(1, component.count('data-atlas-region="unified"'))
        self.assertEqual(1, component.count('data-atlas-view-panel="unified"'))
        self.assertIn('data-atlas-view="unified"', component)
        self.assertNotIn('data-atlas-region="memory"', component)
        self.assertNotIn('data-atlas-region="receipts"', component)
        self.assertNotIn('role="tablist"', component)
        self.assertNotIn('data-atlas-view-option=', component)
        self.assertNotIn("setAtlasView", component)
        self.assertIn('id="brain-atlas-unified-panel"', component)
        self.assertIn('aria-labelledby="brain-atlas-unified-heading"', component)
        self.assertIn('aria-describedby="brain-atlas-unified-description"', component)
        self.assertIn('data-atlas-layer="memory"', unified_svg)
        self.assertIn('data-atlas-layer="proof"', unified_svg)
        self.assertIn('data-memory-source="governed-memory-registry"', unified_svg)
        self.assertIn('data-proof-source="exact-receipt-ledger"', unified_svg)
        self.assertIn('data-proof-animated="false"', unified_svg)
        self.assertGreaterEqual(unified_svg.count('data-proof-animated="false"'), 2)
        self.assertIn('data-atlas-view-tone={selectedTone}', component)
        self.assertIn("Live activity + exact proof", component)
        self.assertIn("Governed memory receipts and static proof show live work—not private reasoning", component)
        self.assertIn("This visualization shows observable operations and evidence, not private model reasoning or memory contents", component)
        self.assertIn('aria-label="Brain Atlas unified observable agent activity and exact execution evidence"', component)
        self.assertIn("Only governed memory receipt paths move when a recent exact registry timestamp exists", component)
        self.assertIn("static, exact agent to named work to timestamped receipt to verified model paths", component)
        self.assertIn("LIVE AGENTS + GOVERNED MEMORY", component)
        self.assertIn("EXACT EXECUTION PROOF · STATIC AUDIT PATHS", component)
        self.assertIn("sanitizedMemoryActivity(memoryOperations?.activity)", component)
        self.assertIn("memorySignalIsRecent", component)
        self.assertIn("ageMs >= -5_000", component)
        self.assertIn("Math.min(100", component)
        self.assertIn("data-observed-at", component)
        self.assertIn("data-agent-working", component)
        self.assertIn("data-work-state", component)
        self.assertIn("data-memory-state", component)
        self.assertIn("is-work-active", component)
        self.assertIn("is-memory-live", component)
        self.assertIn("latestAgentMemorySignal", component)
        self.assertIn('data-operation="cross-agent-used"', component)
        self.assertIn("data-source-agent", component)
        self.assertIn("data-consumer-agent", component)
        self.assertIn('`Quiet · ${row.retrievals} retrieval', component)
        self.assertIn('const emittedEdges = receiptEdges.filter((edge) => edge.kind === "emitted")', component)
        self.assertIn('const ownsEdges = receiptEdges.filter((edge) => edge.kind === "owns")', component)
        self.assertIn('const verifiedEdges = receiptEdges.filter((edge) => edge.kind === "verified-route")', component)
        self.assertIn("emittedEdges.length !== 1 || ownsEdges.length !== 1 || verifiedEdges.length !== 1", component)
        self.assertIn("owns.target !== work.id || verified.source !== receipt.id", component)
        self.assertIn("edge.evidenceReceipt === receipt.id", component)
        self.assertIn("data-proof-row={row.id}", component)
        self.assertIn("data-work-label={row.workLabel}", component)
        self.assertIn("data-receipt={row.receipt.id}", component)
        self.assertIn("data-model={row.model.id}", component)
        self.assertIn('data-route-verified="true"', component)
        self.assertIn('return unsafe ? "Verified work" : label', component)
        self.assertIn("const opaqueWorkId = /^Work [a-f0-9]{8}$/i.test(label)", component)
        self.assertIn("compactText(row.workLabel, 54)", component)
        self.assertIn("Static audit evidence, not reasoning", component)
        self.assertIn("const agentY = agentIndex >= 0 ? 41 + agentIndex * 52 : 119", component)
        self.assertIn('d={`M ${brainAtlasWideX(168)} ${agentY}', component)
        self.assertIn('d={`M ${brainAtlasWideX(168)} ${y + 19}', component)
        self.assertIn('"ACTIVE · WORKING"', component)
        self.assertIn("brain-atlas-agent-copy-", component)
        self.assertIn("brain-atlas-proof-work-copy-", component)
        self.assertIn("brain-atlas-proof-model-copy-", component)
        self.assertIn("Shared agent nodes show", component)
        self.assertEqual(1, component.count("memory-flow-node is-agent"))
        self.assertIn("liveWorkPresentationForAgent(agent, statusByAgent.get(agent), workItems)", component)
        self.assertIn("workingAgentCount > 0 && activity && !latestSignalRecent", component)
        self.assertIn('"Memory quiet"', component)
        self.assertIn("statuses={state.statuses}", main)
        self.assertIn("const liveWorkItems = useMemo(() => buildWorkItems(state), [state])", main)
        self.assertGreaterEqual(main.count("workItems={liveWorkItems}"), 2)
        self.assertIn('recent("used") ? " is-live" : ""', component)
        self.assertIn("const BRAIN_ATLAS_VISIBLE_RECEIPTS = 3", main)
        self.assertIn("const BRAIN_ATLAS_WIDE_VIEWBOX_WIDTH = 1464", component)
        self.assertIn('viewBox={`0 0 ${BRAIN_ATLAS_WIDE_VIEWBOX_WIDTH} 376`}', component)
        self.assertIn("not learned", component)
        self.assertNotIn("forceSimulation", component)
        self.assertNotIn("d3-force", component)
        self.assertNotIn("Math.random", component)

        self.assertIn(".memory-flow-edge.is-live {", styles)
        self.assertIn("stroke-width: 4.4;", styles)
        self.assertIn(".memory-flow-edge.is-retrieval.is-live", styles)
        self.assertIn(".memory-flow-edge.is-cross-agent", styles)
        self.assertIn("stroke: rgba(101, 217, 255, 0.96);", styles)
        self.assertIn("animation: memory-flow-travel 1.05s linear infinite;", styles)
        self.assertIn("@keyframes memory-flow-travel", styles)
        self.assertIn("@keyframes memory-agent-presence-halo", styles)
        self.assertIn("@keyframes memory-agent-presence-dot", styles)
        self.assertIn("transform-box: fill-box;", styles)
        self.assertIn(".memory-flow-node.is-work-active .memory-flow-node-aura", styles)
        live_map_css = styles[
            styles.index("#brain-atlas.has-live-memory-flow .memory-flow-map {"):
            styles.index(".memory-flow-map:focus-visible")
        ]
        self.assertIn("box-shadow:", live_map_css)
        self.assertNotIn("animation:", live_map_css)
        live_edge_css = styles[
            styles.index(".memory-flow-edge.is-live {"):
            styles.index(".memory-flow-node rect")
        ]
        self.assertIn("animation: memory-flow-travel 1.05s linear infinite;", live_edge_css)
        self.assertNotIn("filter:", live_edge_css)
        live_node_css = styles[
            styles.index(".memory-flow-node.is-live > rect:not(.memory-flow-node-aura) {"):
            styles.index(".memory-flow-node .memory-flow-node-aura")
        ]
        self.assertIn("stroke-width: 2.3;", live_node_css)
        self.assertIn("filter: drop-shadow", live_node_css)
        self.assertNotIn("animation:", live_node_css)
        self.assertNotIn("@keyframes memory-node-live-pulse", styles)
        self.assertNotIn("@keyframes memory-map-live-breathe", styles)
        proof_edge_css = styles[
            styles.index(".brain-atlas-proof-edge {"):
            styles.index(".brain-atlas-proof-work rect")
        ]
        self.assertIn("stroke-width: 1.25;", proof_edge_css)
        self.assertNotIn("animation:", proof_edge_css)
        self.assertNotIn("brain-atlas-proof-edge is-live", component)
        self.assertIn("#brain-atlas .brain-atlas-section.is-unified", styles)
        reduced_motion = styles[styles.index("@media (prefers-reduced-motion: reduce)", styles.index("@keyframes memory-flow-travel")) :]
        self.assertIn(".memory-flow-edge.is-live", reduced_motion)
        self.assertIn(".memory-flow-node.is-work-active .memory-flow-node-aura", reduced_motion)
        self.assertIn(".memory-flow-node.is-work-active .memory-flow-presence-dot", reduced_motion)
        self.assertIn("#brain-atlas.has-active-work .brain-atlas-state", reduced_motion)
        self.assertIn("animation: none !important;", reduced_motion)

    def test_atlas_and_finops_are_always_visible_in_matched_grid_cells(self) -> None:
        main = (MISSION_CONTROL / "v2-react" / "src" / "main.tsx").read_text(encoding="utf-8")
        styles = (MISSION_CONTROL / "v2-react" / "src" / "styles.css").read_text(encoding="utf-8")
        dashboard = main[main.index('<section className="kiosk-grid">'):main.index("function BrainHero")]
        desktop_layout = styles[styles.index("/* Control Tower v11") : styles.index("#brain-atlas.brain-atlas-panel")]

        self.assertIn("<BrainAtlasPanel", dashboard)
        self.assertIn("<MemoizedFinOpsDashboard", dashboard)
        self.assertLess(dashboard.index("<BrainAtlasPanel"), dashboard.index("<MemoizedFinOpsDashboard"))
        self.assertNotIn("supportView", main)
        self.assertNotIn("data-support-view", main)
        self.assertNotIn('useState<"finops" | "atlas">', main)

        self.assertIn('"live jobs"', desktop_layout)
        self.assertIn('"atlas finops"', desktop_layout)
        self.assertIn("grid-template-rows: minmax(0, 44fr) minmax(0, 56fr)", desktop_layout)
        self.assertIn(".kiosk-grid > #brain-atlas { grid-area: atlas; }", desktop_layout)
        self.assertIn(".kiosk-grid > #finops-dashboard { grid-area: finops; }", desktop_layout)
        self.assertIn("height: 100% !important;", desktop_layout)
        self.assertIn(".brain-atlas-section.is-unified", styles)
        self.assertIn("flex: 1 1 auto;", styles)
        self.assertIn(".memory-flow-map {", styles)

    def test_kiosk_respects_user_motion_preference_and_guard_protects_launcher(self) -> None:
        launcher = (MISSION_CONTROL / "scripts" / "open_mission_control_kiosk.sh").read_text(encoding="utf-8")
        guard = (MISSION_CONTROL / "scripts" / "control_tower_change_guard.py").read_text(encoding="utf-8")

        self.assertNotIn("--force-prefers-reduced-motion", launcher)
        self.assertIn("control-tower-kiosk-launch.lock", launcher)
        self.assertIn('/bin/ln "$LAUNCH_OWNER_FILE" "$LAUNCH_LOCK"', launcher)
        self.assertIn("trap 'release_launch_lock; exit 130' INT", launcher)
        self.assertIn("trap 'release_launch_lock; exit 143' TERM", launcher)
        self.assertIn("LOCK_WAS_CONTENDED", launcher)
        self.assertIn("dedicated_kiosk_pids", launcher)
        self.assertIn("wait_for_kiosk_cdp", launcher)
        self.assertIn("refusing a duplicate launch", launcher)
        self.assertNotIn("pgrep -f", launcher)
        self.assertNotIn('rm -rf "$PROFILE/SingletonLock"', launcher)
        self.assertIn('"scripts/open_mission_control_kiosk.sh"', guard)
        self.assertIn('"scripts/control_tower_foreground.py"', guard)

    def test_handoff_receipt_bridge_support_is_preserved(self) -> None:
        source = UPDATE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("from handoff_receipt_bridge import receipt_state, terminal_result_receipt", source)
        self.assertIn('"brainAtlas"', source)


if __name__ == "__main__":
    unittest.main()
