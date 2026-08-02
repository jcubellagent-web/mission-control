from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import shared_skill_drift


def test_shared_skill_drift_reports_current_missing_and_drifted(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    installed = tmp_path / "installed"
    for name, content in (("current", "same"), ("changed", "new"), ("missing", "only")):
        (canonical / name).mkdir(parents=True)
        (canonical / name / "SKILL.md").write_text(content, encoding="utf-8")
    for name, content in (("current", "same"), ("changed", "old")):
        (installed / name).mkdir(parents=True)
        (installed / name / "SKILL.md").write_text(content, encoding="utf-8")

    result = shared_skill_drift.compare(canonical, installed)

    assert result["counts"] == {"current": 1, "drifted": 1, "missing": 1}
    assert result["ok"] is False
