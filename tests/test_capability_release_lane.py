from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "capability_release_lane.py"
SPEC = importlib.util.spec_from_file_location("capability_release_lane", MODULE_PATH)
assert SPEC and SPEC.loader
lane = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lane)


def config() -> dict:
    return {
        "enabled": True,
        "automaticCandidatePreparation": True,
        "thresholds": {"fastTrack": 7, "test": 4},
        "slas": {"fastTrackCandidateHours": 12, "testCandidateHours": 24, "routineReviewHours": 72},
        "channels": {"stableAutoPrepare": True, "previewAutoPrepare": True},
        "allowedPrerequisiteExecutables": ["node"],
        "signals": [
            {"id": "reliability", "weight": 4, "patterns": ["durable", "recovery"]},
            {"id": "security", "weight": 4, "patterns": ["security", "credential"]},
        ],
        "products": {"openclaw": {"requirements": []}, "hermes": {"requirements": []}},
    }


def watch(target: str = "2026.7.2-beta.7") -> dict:
    return {
        "sources": {
            "openclawUpdate": {"currentVersion": "2026.7.1-2"},
            "openclawNpm": {"distTags": {"latest": "2026.7.1-2", "beta": target}},
            "openclawLatestRelease": {"ok": True, "tag": "v2026.7.1-2", "name": "stable", "notes": "plugin fix"},
            "openclawPreviewRelease": {"ok": True, "tag": f"v{target}", "name": "preview", "notes": "durable recovery and security fixes"},
        }
    }


def test_meaningful_preview_enters_fast_track_without_production_eligibility() -> None:
    state, _events = lane.assess(watch(), config(), {}, [], prepare=False)
    preview = next(row for row in state["assessments"] if row["channel"] == "preview")
    assert preview["status"] == "fast-track"
    assert preview["score"] == 8
    assert preview["productionPromotion"] == "manual-only"


def test_current_stable_is_adopted_not_prepared() -> None:
    with mock.patch.object(lane, "prepare_candidate") as prepare:
        state, _events = lane.assess(watch(), config(), {}, [], prepare=True)
    stable = next(row for row in state["assessments"] if row["channel"] == "stable")
    assert stable["status"] == "adopted"
    prepare.assert_called_once()  # Only the meaningful preview is prepared.


def test_exact_release_metadata_mismatch_fails_closed() -> None:
    payload = watch()
    payload["sources"]["openclawPreviewRelease"]["tag"] = "v2026.7.2-beta.6"
    state, _events = lane.assess(payload, config(), {}, [], prepare=False)
    preview = next(row for row in state["assessments"] if row["channel"] == "preview")
    assert preview["status"] == "metadata-mismatch"


def test_prepared_release_is_idempotent(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    manifest = tmp_path / "candidate.json"
    manifest.write_text('{"sandbox": "' + str(sandbox) + '"}')
    history = [{
        "event": "candidate-prepared", "product": "openclaw", "channel": "preview",
        "release": "2026.7.2-beta.7", "manifest": str(manifest),
    }]
    with mock.patch.object(lane, "prepare_candidate") as prepare:
        state, _events = lane.assess(watch(), config(), {}, history, prepare=True)
    preview = next(row for row in state["assessments"] if row["channel"] == "preview")
    assert preview["status"] == "candidate-prepared"
    prepare.assert_not_called()


def test_new_release_appends_supersession_without_deleting_history() -> None:
    previous = {"assessments": [{"product": "openclaw", "channel": "preview", "release": "2026.7.2-beta.6", "status": "fast-track"}]}
    _state, events = lane.assess(watch(), config(), previous, [], prepare=False)
    superseded = next(row for row in events if row["event"] == "candidate-superseded")
    assert superseded["release"] == "2026.7.2-beta.6"
    assert superseded["supersededBy"] == "2026.7.2-beta.7"


def test_runtime_prerequisite_blocks_incompatible_release() -> None:
    cfg = config()
    cfg["products"]["hermes"]["requirements"] = [{
        "id": "node-26", "whenPatterns": ["node 26 required"],
        "executable": "node", "minimumMajor": 26,
    }]
    with mock.patch.object(lane.shutil, "which", return_value="/safe/node"), mock.patch.object(lane, "run", return_value={"ok": True, "detail": "v24.16.0"}):
        failures = lane.unmet_requirements("hermes", "Node 26 required", cfg)
    assert failures == [{"id": "node-26", "minimumMajor": 26, "observedMajor": 24}]


def test_unapproved_prerequisite_executable_is_never_run() -> None:
    cfg = config()
    cfg["products"]["hermes"]["requirements"] = [{
        "id": "unsafe", "whenPatterns": ["required"],
        "executable": "sh", "minimumMajor": 1,
    }]
    with mock.patch.object(lane, "run") as run:
        failures = lane.unmet_requirements("hermes", "required", cfg)
    assert failures[0]["observedMajor"] is None
    run.assert_not_called()


def test_missing_candidate_sandbox_is_reprepared(tmp_path: Path) -> None:
    manifest = tmp_path / "candidate.json"
    manifest.write_text('{"sandbox": "' + str(tmp_path / "missing") + '"}')
    history = [{
        "event": "candidate-prepared", "product": "openclaw", "channel": "preview",
        "release": "2026.7.2-beta.7", "manifest": str(manifest),
    }]
    assert lane.already_prepared(history, "openclaw", "preview", "2026.7.2-beta.7") is False


def test_hermes_patch_conflict_is_blocked_and_idempotent(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    manifest = tmp_path / "candidate.json"
    manifest.write_text(
        '{"sandbox": "' + str(sandbox) + '", "localPatchReplay": '
        '{"ok": false, "status": "conflict", "detail": "carried patch conflict"}}'
    )
    history = [{
        "event": "candidate-prepared", "product": "hermes", "channel": "stable",
        "release": "2026.8.3", "manifest": str(manifest),
    }]
    payload = {
        "sources": {
            "hermesUpdate": {"version": "Hermes Agent v0.19.0 (2026.7.20)"},
            "hermesLatestRelease": {
                "ok": True, "tag": "v2026.8.3", "name": "Hermes v0.20.0",
                "notes": "durable recovery and security improvements",
            },
        }
    }
    with mock.patch.object(lane, "prepare_candidate") as prepare:
        state, _events = lane.assess(payload, config(), {}, history, prepare=True)
    hermes = next(row for row in state["assessments"] if row["product"] == "hermes")
    assert hermes["status"] == "blocked-carried-patches"
    assert hermes["failure"] == "carried patch conflict"
    assert hermes["idempotent"] is True
    assert state["status"] == "attention"
    prepare.assert_not_called()


def test_config_never_allows_automatic_production_promotion() -> None:
    real = lane.read_json(MODULE_PATH.parents[1] / "config" / "capability-release-lane.json", {})
    assert real["automaticProductionPromotion"] is False
    assert real["productionMutation"] == "manual-only"
