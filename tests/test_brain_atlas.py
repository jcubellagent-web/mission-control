from __future__ import annotations

import datetime as dt
import hashlib
import json
import importlib.util
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import brain_atlas as subject  # noqa: E402
import brain_atlas_contract as contract  # noqa: E402

if importlib.util.find_spec("todays_jobs_projection") is None:
    projection_stub = types.ModuleType("todays_jobs_projection")
    for name in (
        "default_launchd_plist_paths", "discover_codex_automations",
        "discover_hermes_definitions", "discover_launchd_definitions",
        "discover_qa_definitions", "materialize_today_jobs",
        "parse_crontab_definitions",
    ):
        setattr(projection_stub, name, lambda *args, **kwargs: [])
    sys.modules["todays_jobs_projection"] = projection_stub
if importlib.util.find_spec("handoff_receipt_bridge") is None:
    handoff_stub = types.ModuleType("handoff_receipt_bridge")
    handoff_stub.receipt_state = lambda *args, **kwargs: {}
    handoff_stub.terminal_result_receipt = lambda *args, **kwargs: {}
    sys.modules["handoff_receipt_bridge"] = handoff_stub
import update_mission_control as dashboard  # noqa: E402


AS_OF = dt.datetime(2026, 7, 18, 12, 0, tzinfo=dt.timezone.utc)


class BrainAtlasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="brain-atlas-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "work.sqlite3"
        self.create_database(self.database)

    @staticmethod
    def create_database(path: Path, *, schema_version: int = 1, revision: int = 1000) -> None:
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE store_meta (
              singleton INTEGER PRIMARY KEY,
              schema_version INTEGER NOT NULL,
              revision INTEGER NOT NULL,
              updated_at TEXT
            );
            CREATE TABLE work_events (
              event_id TEXT PRIMARY KEY,
              work_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              generation INTEGER NOT NULL,
              sequence INTEGER NOT NULL,
              kind TEXT NOT NULL,
              status TEXT NOT NULL,
              owner_agent TEXT NOT NULL,
              objective TEXT NOT NULL,
              phase TEXT NOT NULL,
              tool TEXT NOT NULL,
              detail TEXT NOT NULL,
              origin TEXT NOT NULL,
              origin_claim_hash TEXT NOT NULL,
              model_family TEXT NOT NULL,
              model_id TEXT NOT NULL,
              route_verified INTEGER NOT NULL,
              lease_until TEXT,
              occurred_at TEXT NOT NULL,
              accepted_revision INTEGER NOT NULL,
              UNIQUE(work_id,generation,sequence)
            );
            """
        )
        connection.execute(
            "INSERT INTO store_meta VALUES (1,?,?,?)",
            (schema_version, revision, subject.iso(AS_OF)),
        )
        connection.commit()
        connection.close()

    def insert_event(self, index: int, **overrides: object) -> None:
        payload: dict[str, object] = {
            "event_id": f"event-{index}",
            "work_id": f"work-{index}",
            "run_id": f"run-{index}",
            "generation": 1,
            "sequence": 1,
            "kind": "start",
            "status": "active",
            "owner_agent": "josh2",
            "objective": "Refresh Control Tower health",
            "phase": "working",
            "tool": "test",
            "detail": "private detail must never be read",
            "origin": "private-origin",
            "origin_claim_hash": hashlib.sha256(f"claim-{index}".encode()).hexdigest(),
            "model_family": "codex",
            "model_id": f"gpt-5.6-terra-{index}",
            "route_verified": 1,
            "lease_until": None,
            "occurred_at": subject.iso(AS_OF - dt.timedelta(hours=index % 24)),
            "accepted_revision": index + 1,
        }
        payload.update(overrides)
        columns = list(payload)
        placeholders = ",".join("?" for _ in columns)
        connection = sqlite3.connect(self.database)
        connection.execute(
            f"INSERT INTO work_events({','.join(columns)}) VALUES ({placeholders})",
            [payload[column] for column in columns],
        )
        connection.commit()
        connection.close()

    def test_output_excludes_private_content_and_raw_operational_ids(self) -> None:
        self.insert_event(
            1,
            event_id="event-raw-secret-marker",
            work_id="work-private-account-marker",
            run_id="run-private-session-marker",
            objective="raw prompt marker sk-abcdefghijklmnopqrstuvwxyz",
            detail="private-account@example.com",
            origin="private-browser-account-marker",
        )
        before_hash = hashlib.sha256(self.database.read_bytes()).hexdigest()
        before_files = {path.name for path in self.root.iterdir()}
        atlas = subject.generate_atlas(self.database, as_of=AS_OF)
        after_hash = hashlib.sha256(self.database.read_bytes()).hexdigest()
        after_files = {path.name for path in self.root.iterdir()}
        rendered = json.dumps(atlas)

        for marker in (
            "event-raw-secret-marker",
            "work-private-account-marker",
            "run-private-session-marker",
            "raw prompt marker",
            "sk-abcdefghijklmnopqrstuvwxyz",
            "private-account@example.com",
            "private-browser-account-marker",
        ):
            self.assertNotIn(marker, rendered)
        self.assertEqual(before_hash, after_hash)
        self.assertEqual(before_files, after_files)
        self.assertEqual(atlas["status"], "ready")
        self.assertTrue(atlas["source"]["verified"])
        self.assertEqual(atlas["counts"]["receipts"], 1)
        work = next(node for node in atlas["nodes"] if node["kind"] == "work")
        self.assertEqual(work["label"], "JOSH 2.0 task")

    def test_newest_exact_objective_becomes_readable_label_without_changing_identity(self) -> None:
        self.insert_event(
            1,
            work_id="shared-work",
            objective="Older dashboard title",
            occurred_at=subject.iso(AS_OF - dt.timedelta(hours=1)),
        )
        self.insert_event(
            2,
            work_id="shared-work",
            sequence=2,
            objective="  Polish   Telegram response flow  ",
            occurred_at=subject.iso(AS_OF),
        )
        atlas = subject.generate_atlas(self.database, as_of=AS_OF)
        work = next(node for node in atlas["nodes"] if node["kind"] == "work")

        self.assertEqual(work["id"], subject.stable_id("work", "shared-work"))
        self.assertEqual(work["label"], "Polish Telegram response flow")
        self.assertNotIn("objective", work)
        self.assertNotIn("phase", work)
        self.assertTrue(any(edge["target"] == work["id"] for edge in atlas["edges"] if edge["kind"] == "owns"))

    def test_label_contract_normalizes_caps_and_uses_meaningful_fallbacks(self) -> None:
        self.assertEqual(
            contract.safe_work_label("Ｒｅｐａｉｒ   Inbox routing", "working", "JOSHeX"),
            "Repair Inbox routing",
        )
        long_title = (
            "Improve Telegram response presentation while preserving exact "
            "routing evidence across every agent"
        )
        capped = contract.safe_work_label(long_title, "working", "JOSHeX")
        self.assertLessEqual(len(capped), contract.WORK_LABEL_MAX_LENGTH)
        self.assertTrue(capped.endswith("..."))
        self.assertNotEqual(capped[-4], " ")
        self.assertTrue(contract.work_label_is_safe(capped))
        self.assertEqual(
            contract.safe_work_label("password=hunter2", "Repair Telegram routing", "JAIMES"),
            "Repair Telegram routing",
        )
        self.assertEqual(
            contract.safe_work_label("password=hunter2", "working", "JAIMES"),
            "JAIMES task",
        )
        self.assertEqual(
            contract.safe_work_label("password=hunter2", "working", "unknown"),
            "Agent task",
        )
        self.assertEqual(
            contract.safe_work_label("Handle /new", "working", "JOSH 2.0"),
            "Handle /new",
        )

    def test_label_contract_rejects_private_and_opaque_material(self) -> None:
        unsafe = (
            "Repair\u202e dashboard",
            "Email private-account@example.com",
            "Open https://private.example.test/account",
            "Inspect /Users/josh/private/report.txt",
            r"Inspect C:\\Users\\josh\\private.txt",
            "Trace work-private-account-marker",
            "Use work:0123456789abcdef01234567",
            "Token sk-proj-abcdefghijklmnopqrstuvwxyz",
            "Set api_key: abcdefghijklmnopqrstuvwxyz",
            "Use bearer abcdefghijklmnopqrstuvwxyz",
            "Trace 4f0c8d771234567890abcdef",
            "Decode q9Ws8Mx2Na7Lp4Vr6Tc1Hy5Kz0Bj3Df8",
        )
        for value in unsafe:
            with self.subTest(value=value):
                self.assertEqual(
                    contract.safe_work_label(value, "working", "JOSHeX"),
                    "JOSHeX task",
                )
                self.assertFalse(contract.work_label_is_safe(value))

    def test_label_contract_fails_closed_on_pii_financial_and_command_bypasses(self) -> None:
        unsafe = (
            "Call +1 (212) 555-0198 about the rollout",
            "Text 555-0198 after deployment",
            "Customer SSN 123-45-6789",
            "Card 4111 1111 1111 1111",
            "Account # 987654321",
            "Account 123456",
            "IBAN GB82 WEST 1234 5698 7654 32",
            "Card ending in 4242",
            "Routing: 021000021",
            "Balance $117.03",
            "Portfolio 12.5 BTC",
            "DOB 07/18/1980",
            "Visit 123 Main Street",
            "Customer name: Jane Doe",
            "Passport: A1234567",
            "Probe host 192.168.1.10",
            "Inspect /new/private",
            "Use /new to inspect logs",
            "Handle /new then inspect /Users/josh/private.txt",
            "Trace task:01J2Z3Y4X5W6V7U8T9S0R1Q2P3",
            "Trace deadbeef",
            "Token glpat-abcdefghijklmnopqrstuvwxyz",
            "Telegram 123456789:AAabcdefghijklmnopqrstuvwxyz",
            "Google key AIzaabcdefghijklmnopqrstuvwxyz1234",
        )
        for value in unsafe:
            with self.subTest(value=value):
                self.assertEqual(
                    contract.safe_work_label(value, "working", "JOSHeX"),
                    "JOSHeX task",
                )
                self.assertFalse(contract.work_label_is_safe(value))

        self.assertEqual(
            contract.safe_work_label("Handle /new", "working", "JOSH 2.0"),
            "Handle /new",
        )
        self.assertTrue(contract.work_label_is_safe("Handle /new"))

    def test_model_route_contract_is_family_specific_and_private_data_safe(self) -> None:
        safe_routes = (
            ("codex", "gpt-5.6-terra"),
            ("codex", "gpt-5.6-sol"),
            ("codex", "gpt-5.1-codex"),
            ("codex", "openai-codex/gpt-5.6-sol"),
            ("codex", "openai/gpt-5.6-terra"),
            ("codex", "codex/gpt-5.6-terra"),
            ("antigravity", "gemini-2.5-flash"),
            ("antigravity", "google/gemini-2.5-pro"),
            ("ollama", "qwen2.5-coder:32b"),
            ("ollama", "llama3.2:3b"),
            ("ollama", "ollama/qwen2.5-coder:32b"),
            ("grok", "grok-4.20-reasoning"),
            ("grok", "xai/grok-4.20-reasoning"),
        )
        for family, model_id in safe_routes:
            with self.subTest(family=family, model_id=model_id):
                self.assertEqual(
                    contract.safe_model_route_candidate(family, model_id),
                    (family, model_id),
                )
                self.assertTrue(
                    contract.model_route_node_is_safe(
                        family, model_id, f"{family}/{model_id}"
                    )
                )

        unsafe_routes = (
            ("codex", "gpt-sk-proj-abcdefghijklmnopqrstuvwxyz"),
            ("codex", "gpt-private@example.com"),
            ("codex", "gpt-4111111111111111"),
            ("codex", "gpt-deadbeefdeadbeef"),
            ("codex", "gpt-5.6-terra/token"),
            ("codex", "gpt-5.6-terra "),
            ("codex", "Gpt-5.6-terra"),
            ("antigravity", "gpt-5.6-terra"),
            ("ollama", "private-model:latest"),
            ("grok", "grok-1234567890"),
            ("unknown", "gpt-5.6-terra"),
        )
        for family, model_id in unsafe_routes:
            with self.subTest(family=family, model_id=model_id):
                self.assertIsNone(
                    contract.safe_model_route_candidate(family, model_id)
                )
                self.assertFalse(
                    contract.model_route_node_is_safe(
                        family, model_id, f"{family}/{model_id}"
                    )
                )

    def test_generator_excludes_private_or_unknown_verified_model_routes(self) -> None:
        unsafe_ids = (
            "gpt-sk-proj-abcdefghijklmnopqrstuvwxyz",
            "gpt-private@example.com",
            "gpt-4111111111111111",
            "gpt-deadbeefdeadbeef",
            "gpt-5.6-terra/token",
        )
        for index, model_id in enumerate(unsafe_ids, start=1):
            self.insert_event(index, model_id=model_id)
        self.insert_event(20, model_family="unknown", model_id="gpt-5.6-terra")
        atlas = subject.generate_atlas(self.database, as_of=AS_OF)
        rendered = json.dumps(atlas)

        self.assertEqual(atlas["counts"]["excluded"]["unsafeVerifiedRoutes"], 6)
        self.assertEqual(atlas["counts"]["models"], 0)
        self.assertFalse(any(edge["kind"] == "verified-route" for edge in atlas["edges"]))
        for model_id in unsafe_ids:
            self.assertNotIn(model_id, rendered)

    def test_generator_and_dashboard_sanitizer_share_work_label_contract(self) -> None:
        self.insert_event(1, objective="Polish Brain Atlas evidence labels")
        atlas = subject.generate_atlas(self.database, as_of=AS_OF)
        clean = dashboard.sanitize_brain_atlas(
            atlas, subject.iso(AS_OF + dt.timedelta(minutes=1))
        )
        self.assertEqual(clean["status"], "ready")
        self.assertIn(
            "Polish Brain Atlas evidence labels",
            {node["label"] for node in clean["nodes"]},
        )

        unsafe = json.loads(json.dumps(atlas))
        next(node for node in unsafe["nodes"] if node["kind"] == "work")["label"] = (
            "private-account@example.com"
        )
        rejected = dashboard.sanitize_brain_atlas(
            unsafe, subject.iso(AS_OF + dt.timedelta(minutes=1))
        )
        self.assertEqual(rejected["status"], "unavailable")
        self.assertEqual(rejected["emptyReason"], "generated-payload-invalid")

    def test_ids_and_full_payload_are_deterministic_for_fixed_receipts(self) -> None:
        self.insert_event(1, owner_agent="jaimes")
        self.insert_event(2, owner_agent="jain", route_verified=0, model_family="", model_id="")
        first = subject.generate_atlas(self.database, as_of=AS_OF)
        second = subject.generate_atlas(self.database, as_of=AS_OF)
        self.assertEqual(first, second)
        self.assertEqual(subject.validate_atlas(first), [])
        self.assertEqual(
            [row["id"] for row in first["nodes"]],
            [row["id"] for row in second["nodes"]],
        )
        self.assertEqual(len({row["id"] for row in first["nodes"]}), len(first["nodes"]))
        self.assertEqual(len({row["id"] for row in first["edges"]}), len(first["edges"]))

    def test_node_cap_is_hard_and_graph_has_no_dangling_edges(self) -> None:
        for index in range(60):
            self.insert_event(index)
        atlas = subject.generate_atlas(self.database, as_of=AS_OF, max_nodes=10)
        node_ids = {row["id"] for row in atlas["nodes"]}
        self.assertLessEqual(len(node_ids), 10)
        self.assertEqual(atlas["limits"]["hardMaxNodes"], 100)
        self.assertGreater(atlas["counts"]["excluded"]["capacityReceipts"], 0)
        for edge in atlas["edges"]:
            self.assertIn(edge["source"], node_ids)
            self.assertIn(edge["target"], node_ids)
            self.assertIn(edge["evidenceReceipt"], node_ids)
        with self.assertRaises(ValueError):
            subject.generate_atlas(self.database, as_of=AS_OF, max_nodes=101)

    def test_stale_legacy_and_unverified_relationships_are_excluded(self) -> None:
        self.insert_event(1)
        self.insert_event(2, route_verified=0, model_family="codex", model_id="unverified-model")
        self.insert_event(3, route_verified=1, model_family="codex", model_id="unsafe model id")
        self.insert_event(4, occurred_at=subject.iso(AS_OF - dt.timedelta(days=8)))
        self.insert_event(5, owner_agent="josh")
        self.insert_event(6, accepted_revision=0)
        self.insert_event(7, origin_claim_hash="legacy-not-a-hash")
        atlas = subject.generate_atlas(self.database, as_of=AS_OF)
        exclusions = atlas["counts"]["excluded"]

        self.assertEqual(atlas["window"]["days"], 7)
        self.assertEqual(atlas["counts"]["sourceRowsInWindow"], 6)
        self.assertEqual(atlas["counts"]["receipts"], 3)
        self.assertEqual(exclusions["timeOutOfWindow"], 1)
        self.assertEqual(exclusions["legacyOrInvalid"], 3)
        self.assertEqual(exclusions["unverifiedRoutes"], 1)
        self.assertEqual(exclusions["unsafeVerifiedRoutes"], 1)
        route_edges = [edge for edge in atlas["edges"] if edge["kind"] == "verified-route"]
        self.assertEqual(len(route_edges), 1)

    def test_edges_are_only_exact_receipt_relationships(self) -> None:
        self.insert_event(1, owner_agent="joshex")
        atlas = subject.generate_atlas(self.database, as_of=AS_OF)
        nodes = {row["id"]: row for row in atlas["nodes"]}
        for edge in atlas["edges"]:
            self.assertEqual(nodes[edge["evidenceReceipt"]]["kind"], "receipt")
            if edge["kind"] == "owns":
                self.assertEqual(nodes[edge["source"]]["kind"], "agent")
                self.assertEqual(nodes[edge["target"]]["kind"], "work")
            elif edge["kind"] == "emitted":
                self.assertEqual(nodes[edge["source"]]["kind"], "work")
                self.assertEqual(nodes[edge["target"]]["kind"], "receipt")
            else:
                self.assertEqual(edge["kind"], "verified-route")
                self.assertEqual(nodes[edge["source"]]["kind"], "receipt")
                self.assertEqual(nodes[edge["target"]]["kind"], "model")
        self.assertIn("no inferred or fuzzy relationships", atlas["policy"]["edges"])

    def test_empty_and_unavailable_states_are_truthful(self) -> None:
        empty = subject.generate_atlas(self.database, as_of=AS_OF)
        self.assertEqual(empty["status"], "empty")
        self.assertTrue(empty["empty"])
        self.assertEqual(empty["emptyReason"], "no-receipts-in-window")
        self.assertEqual(empty["nodes"], [])
        self.assertEqual(empty["edges"], [])
        self.assertTrue(empty["source"]["verified"])

        unsupported = self.root / "unsupported.sqlite3"
        sqlite3.connect(unsupported).close()
        unavailable = subject.generate_safely(unsupported, as_of=AS_OF)
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertTrue(unavailable["empty"])
        self.assertEqual(unavailable["emptyReason"], "unsupported-source-schema")
        self.assertFalse(unavailable["source"]["verified"])
        self.assertEqual(unavailable["nodes"], [])
        self.assertNotIn(str(unsupported), json.dumps(unavailable))

        missing = subject.generate_safely(self.root / "missing.sqlite3", as_of=AS_OF)
        self.assertEqual(missing["emptyReason"], "source-missing")

        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not a sqlite database")
        unavailable_corrupt = subject.generate_safely(corrupt, as_of=AS_OF)
        self.assertEqual(unavailable_corrupt["status"], "unavailable")
        self.assertEqual(unavailable_corrupt["emptyReason"], "source-unavailable")
        self.assertNotIn(str(corrupt), json.dumps(unavailable_corrupt))

    def test_schema_contract_is_bounded_and_excludes_content_fields(self) -> None:
        schema = json.loads((ROOT / "schemas" / "brain-atlas.schema.json").read_text())
        self.assertEqual(schema["properties"]["nodes"]["maxItems"], 100)
        self.assertEqual(schema["properties"]["limits"]["properties"]["hardMaxNodes"]["const"], 100)
        node_properties = schema["$defs"]["node"]["properties"]
        for field in ("objective", "detail", "origin", "workId", "runId", "eventId", "memoryContent"):
            self.assertNotIn(field, node_properties)
        work_rule = schema["$defs"]["node"]["allOf"][0]
        self.assertEqual(
            work_rule["then"]["properties"]["label"]["maxLength"],
            contract.WORK_LABEL_MAX_LENGTH,
        )

    def test_internal_validator_fails_closed_and_validate_only_is_content_free(self) -> None:
        self.insert_event(1)
        atlas = subject.generate_atlas(self.database, as_of=AS_OF)
        private_mutation = json.loads(json.dumps(atlas))
        private_mutation["nodes"][0]["objective"] = "raw prompt"
        self.assertIn("node-fields", subject.validate_atlas(private_mutation))
        unsafe_label = json.loads(json.dumps(atlas))
        next(node for node in unsafe_label["nodes"] if node["kind"] == "work")["label"] = "Work deadbeef"
        self.assertIn("work-label", subject.validate_atlas(unsafe_label))
        dangling = json.loads(json.dumps(atlas))
        dangling["edges"][0]["target"] = "work:missing"
        self.assertIn("dangling-edge", subject.validate_atlas(dangling))

        output = self.root / "must-not-be-written.json"
        process = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "brain_atlas.py"),
                "--db", str(self.database),
                "--as-of", subject.iso(AS_OF),
                "--output", str(output),
                "--validate-only",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        gate = json.loads(process.stdout)
        self.assertEqual(set(gate), {"ok", "status", "sourceVerified", "windowDays", "nodes", "edges", "problems", "emptyReason"})
        self.assertTrue(gate["ok"])
        self.assertEqual(gate["problems"], [])
        self.assertFalse(output.exists())

    def test_internal_validator_rejects_unsafe_models_and_ambiguous_proof_paths(self) -> None:
        self.insert_event(1, owner_agent="joshex")
        self.insert_event(2, owner_agent="jaimes")
        atlas = subject.generate_atlas(self.database, as_of=AS_OF)

        unsafe_model = json.loads(json.dumps(atlas))
        model = next(node for node in unsafe_model["nodes"] if node["kind"] == "model")
        model["modelId"] = "gpt-sk-proj-abcdefghijklmnopqrstuvwxyz"
        model["label"] = f"codex/{model['modelId']}"
        self.assertIn("model-route", subject.validate_atlas(unsafe_model))

        false_route_claim = json.loads(json.dumps(atlas))
        route = next(
            edge for edge in false_route_claim["edges"]
            if edge["kind"] == "verified-route"
        )
        receipt = next(
            node for node in false_route_claim["nodes"]
            if node["id"] == route["source"]
        )
        receipt["routeVerified"] = False
        self.assertIn("route-proof", subject.validate_atlas(false_route_claim))

        duplicate = json.loads(json.dumps(atlas))
        copied_edge = dict(next(edge for edge in duplicate["edges"] if edge["kind"] == "owns"))
        copied_edge["id"] = "edge:000000000000000000000000"
        duplicate["edges"].append(copied_edge)
        duplicate["counts"]["edges"] += 1
        duplicate_problems = subject.validate_atlas(duplicate)
        self.assertIn("duplicate-edge", duplicate_problems)
        self.assertIn("edge-id", duplicate_problems)
        self.assertIn("ambiguous-path", duplicate_problems)

        missing_owner = json.loads(json.dumps(atlas))
        owns_index = next(
            index for index, edge in enumerate(missing_owner["edges"])
            if edge["kind"] == "owns"
        )
        missing_owner["edges"].pop(owns_index)
        missing_owner["counts"]["edges"] -= 1
        self.assertIn("ambiguous-path", subject.validate_atlas(missing_owner))

        wrong_receipt_owner = json.loads(json.dumps(atlas))
        owns_edges = [edge for edge in wrong_receipt_owner["edges"] if edge["kind"] == "owns"]
        owns_edges[0]["evidenceReceipt"] = owns_edges[1]["evidenceReceipt"]
        owns_edges[0]["id"] = subject.edge_id(
            owns_edges[0]["kind"], owns_edges[0]["source"], owns_edges[0]["target"],
            owns_edges[0]["evidenceReceipt"],
        )
        self.assertIn("ambiguous-path", subject.validate_atlas(wrong_receipt_owner))


if __name__ == "__main__":
    unittest.main()
