from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


stress = load("telegram_response_contract_stress_under_test", ROOT / "scripts" / "telegram_response_contract_stress.py")
work_card_path = ROOT / "scripts" / "josh_work_card.py"
work_card = load("josh_work_card_for_stress", work_card_path) if work_card_path.exists() else None


class FakeLiveModule:
    def __init__(
        self,
        *,
        fail_stage: str = "",
        fail_error: str = "",
        live_indeterminate_id: bool = False,
        final_failure_id: bool = False,
    ) -> None:
        self.fail_stage = fail_stage
        self.fail_error = fail_error
        self.live_indeterminate_id = live_indeterminate_id
        self.final_failure_id = final_failure_id
        self.next_id = 101
        self.calls: list[tuple] = []
        self.card_statuses: list[str] = []
        self.delete_behaviors: dict[str, list[object]] = {}

    @staticmethod
    def display_width(value: str) -> int:
        return len(value)

    @staticmethod
    def delivery_indeterminate(result: dict) -> bool:
        return "timeout" in str(result.get("error") or "").lower()

    def _sent(self) -> dict:
        message_id = self.next_id
        self.next_id += 1
        return {"ok": True, "result": {"message_id": message_id}}

    def telegram_cooldown_active(self):
        return None

    @staticmethod
    def task_headers_enabled(_chat_id, _thread_id) -> bool:
        return False

    def build_task_header(self, **_kwargs) -> str:
        return "<pre>Objective | synthetic QA\nOwner     | Josh 2.0\nAgent     | system\nModels    | transport canary</pre>"

    def build_card(self, *, status: str, **_kwargs) -> str:
        self.card_statuses.append(status)
        if status == "done":
            return "<pre>JOSH 2.0 · Complete\nProgress [██████████] 6/6\n\nNow\nResult verified and ready.</pre>"
        return "<pre>JOSH 2.0 · Verifying\nProgress [████████░░] 5/6\n\nNow\nVerifying the result.</pre>"

    def build_rich_card(self, *, status: str, **_kwargs) -> str:
        terminal = status == "done"
        phase = "COMPLETE" if terminal else "LIVE WORK"
        progress = "██████████ 100% · stage 6/6" if terminal else "████████░░ 83% · stage 5/6"
        labels = ("Accepted", "Planned", "Routed", "Working", "Verifying", "Delivered")
        checklist = "".join(
            f'<li><input type="checkbox"{" checked" if terminal else ""}>{label}</li>'
            for label in labels
        )
        return (
            f"<h3>JOSH 2.0 · {phase}</h3>"
            "<p><b>Objective</b><br>Verify the Telegram response contract</p>"
            "<p><code>system/transport-canary</code> · Josh 2.0 owns delivery</p>"
            f"<pre>{progress}\n{'complete' if terminal else 'verifying'}</pre>"
            f"<blockquote><b>Now</b><br>{'Result verified and ready.' if terminal else 'Verifying the result.'}</blockquote>"
            f"<h4>Progress</h4><ul>{checklist}</ul>"
            "<h4>Active work</h4><ul><li>Josh 2.0 · owner/coordinator</li></ul>"
            "<details><summary>Recent activity (1)</summary><ul><li>Telegram response contract verified.</li></ul></details>"
            "<footer>elapsed 1s · updated 00:00 EDT</footer>"
        )

    def build_completion_summary(self, **_kwargs) -> str:
        return """<b>JOSH 2.0 · COMPLETE</b>
<code>Model: system/test | Route: qa | Why: transport test</code>

<blockquote><b>Complete:</b> Yes - QA complete</blockquote>

<b>What was done:</b>
• Confirmed the eyes reaction reached the exact Telegram topic.
• Verified the native live card closed at all six stages.
• Confirmed exactly one final delivery passed with no remaining work.

<b>Issues:</b>
• None

<b>Appropriate next steps:</b>
• No action needed.

<b>Approval needed:</b>
• None"""

    def send_card(self, text, *_args, **_kwargs) -> dict:
        stage = "header" if "Objective" in text else "anchor"
        self.calls.append(("send_card", stage, text))
        if self.fail_stage == stage:
            return {"ok": False, "error": self.fail_error or f"{stage} failed"}
        return self._sent()

    def send_rich_message(self, rich, legacy, *_args, **_kwargs) -> dict:
        self.calls.append(("send_rich", rich, legacy))
        if self.fail_stage == "live":
            result = {"ok": False, "error": "live failed", "delivery_indeterminate": True}
            if self.live_indeterminate_id:
                result["result"] = {"message_id": self.next_id}
                self.next_id += 1
            return result
        result = self._sent()
        result["native_rich_message"] = True
        return result

    def edit_rich_card(self, message_id, rich, legacy, *_args, **_kwargs) -> dict:
        self.calls.append(("edit_rich", str(message_id), rich, legacy))
        if self.fail_stage == "edit":
            return {"ok": False, "error": "edit failed"}
        return {"ok": True, "result": {"message_id": int(message_id)}, "native_rich_message": True}

    def send_final_summary(self, text, *_args, **_kwargs) -> dict:
        self.calls.append(("send_final", text))
        if self.fail_stage == "final":
            result = {"ok": False, "error": "final failed"}
            if self.final_failure_id:
                result["result"] = {"message_id": self.next_id}
                self.next_id += 1
            return result
        return self._sent()

    def api_call(self, method: str, payload: dict, timeout: int = 15) -> dict:
        del timeout
        if method == "setMessageReaction":
            self.calls.append(("reaction", str(payload["message_id"])))
            return {"ok": self.fail_stage != "reaction"}
        if method == "deleteMessage":
            target = str(payload["message_id"])
            self.calls.append(("delete", target))
            plan = self.delete_behaviors.get(target) or []
            if plan:
                behavior = plan.pop(0)
                if isinstance(behavior, Exception):
                    raise behavior
                assert isinstance(behavior, dict)
                return behavior
            return {"ok": True}
        return {"ok": False, "error": f"unexpected API method {method}"}


class FakeBasicModule:
    def __init__(
        self,
        *,
        edit_raises: bool = False,
        delete_allowed: bool = True,
        send_error: str = "",
    ) -> None:
        self.edit_raises = edit_raises
        self.delete_allowed = delete_allowed
        self.send_error = send_error
        self.calls: list[tuple] = []

    def send_card(self, *_args, **_kwargs) -> dict:
        self.calls.append(("send",))
        if self.send_error:
            return {"ok": False, "error": self.send_error}
        return {"ok": True, "result": {"message_id": 201}}

    def edit_card(self, *_args, **_kwargs) -> dict:
        self.calls.append(("edit",))
        if self.edit_raises:
            raise RuntimeError("edit exploded")
        return {"ok": True, "result": {"message_id": 201}}

    def api_call(self, method: str, payload: dict, timeout: int = 15) -> dict:
        del timeout
        assert method == "deleteMessage"
        self.calls.append(("delete", str(payload["message_id"])))
        return {"ok": self.delete_allowed, "error": "retention policy" if not self.delete_allowed else ""}


def test_render_stress_covers_optional_header_progress_rich_terminal_and_final_contracts() -> None:
    if work_card is None:
        pytest.skip("full staged tree is required for the renderer integration test")
    result = stress.render_stress(work_card, 5)

    assert result["ok"] is True
    assert result["iterations"] == 5
    assert result["renderedCards"] == 55
    assert result["milestoneSequences"][0][-1] == 6
    assert result["problems"] == []


def test_validate_rejects_overwidth_final_summary() -> None:
    module = work_card or FakeLiveModule()
    text = """<pre>Model: system/test | Route: qa | Why: test

Complete: Yes - done

What was done:
- one
- two
- three

Issues:
- n/a

Appropriate next steps:
- No action needed.

Approval needed:
- n/a
- this line is intentionally much wider than the thirty eight column Telegram contract permits</pre>"""

    assert "a rendered line exceeds 38 display columns" in stress.validate(text, module)


def test_validate_accepts_polished_final_and_rejects_unformatted_plain_text() -> None:
    text = FakeLiveModule().build_completion_summary()

    assert stress.validate(text, FakeLiveModule()) == []
    plain = stress.final_plain_text(text)
    assert "final summary must use the polished proportional contract or its pre fallback" in stress.validate(
        plain,
        FakeLiveModule(),
    )


def test_validate_rejects_the_weak_agent_rh_status_only_card() -> None:
    text = """<pre>Model: unverified
   | Route: unverified
   | Why: reported work-card outcome

Complete: Yes - assessment complete

What was done:
- Reviewing product claims, code,
  and trade risks.
- Read-only Robinhood Chain signal
  source; not brokerage trading.
  Do not connect credentials.
- Assessment complete.

Issues:
- n/a

Appropriate next steps:
- No action needed.

Approval needed:
- n/a</pre>"""

    problems = stress.validate(text, FakeLiveModule())

    assert "Complete Yes requires verified model, route, and why values" in problems
    assert "Complete Yes requires at least 3 unique substantive findings" in problems
    assert "Complete Yes cannot use status or process filler as findings" in problems
    assert "Complete Yes requires at least 2 concrete findings or outcomes" in problems
    assert "risk or limitation requires a substantive Issues entry" in problems
    assert "No action needed conflicts with issues or recommendations" in problems


def test_validate_accepts_a_substantive_agent_rh_assessment() -> None:
    text = """<pre>Model: openai/gpt-5.6
   | Route: Josh 2.0 Inbox
   | Why: product assessment

Complete: Yes - assessment completed

What was done:
- Confirmed Agent RH only monitors
  Robinhood Chain signals.
- Found it cannot trade a Robinhood
  brokerage account.
- Identified credential and trade
  control risks.

Issues:
- Credentials could expose wallets.

Appropriate next steps:
- Keep signals read-only; avoid keys.

Approval needed:
- n/a</pre>"""

    assert stress.validate(text, FakeLiveModule()) == []


def test_validate_accepts_negative_telegram_health_findings() -> None:
    text = """<b>JOSH 2.0 · COMPLETE</b>
<code>Model: openai/gpt-5.6-luna | Route: Josh 2.0 Inbox | Why: read-only host assessment</code>

<blockquote><b>Complete:</b> Yes - Telegram health assessed.</blockquote>

<b>What was done:</b>
• The local gateway is running and listening on port 18790, but the sandbox could not probe loopback.
• The inspected launchd domain has no registered Telegram fast-ack entry.
• The available Telegram logs are empty and last modified May 5.

<b>Issues:</b>
• Sandbox-local service checks are unverified.

<b>Appropriate next steps:</b>
• Use the host-native read-only probe for current service state.

<b>Approval needed:</b>
• None"""

    assert stress.validate(text, FakeLiveModule()) == []


def test_validate_rejects_negative_operational_findings_without_issues() -> None:
    text = """<b>JOSH 2.0 · COMPLETE</b>
<code>Model: openai/gpt-5.6-luna | Route: Josh 2.0 Inbox | Why: read-only host assessment</code>
<blockquote><b>Complete:</b> Yes - Telegram health assessed.</blockquote>
<b>What was done:</b>
• The local gateway service is not running at its configured endpoint.
• The Telegram Fast Ack watcher service is stopped in the launchd runtime.
• The available Telegram delivery logs are empty and stale on the service host.
<b>Issues:</b>
• None
<b>Appropriate next steps:</b>
• No action needed.
<b>Approval needed:</b>
• None"""
    problems = stress.validate(text, FakeLiveModule())
    assert "risk or limitation requires a substantive Issues entry" in problems


def test_validate_rejects_generic_state_words_as_concrete_findings() -> None:
    text = """<b>JOSH 2.0 · COMPLETE</b>
<code>Model: openai/gpt-5.6-luna | Route: Josh 2.0 Inbox | Why: read-only host assessment</code>
<blockquote><b>Complete:</b> Yes - Telegram health assessed.</blockquote>
<b>What was done:</b>
• The gateway health assessment remains active while the requested work is being discussed.
• The service status review is running while the requested work remains pending.
• The runtime report was last modified May 5 while the request remained pending.
<b>Issues:</b>
• None
<b>Appropriate next steps:</b>
• No action needed.
<b>Approval needed:</b>
• None"""
    problems = stress.validate(text, FakeLiveModule())
    assert "Complete Yes requires at least 2 concrete findings or outcomes" in problems


def test_validate_no_missing_helpers_as_a_positive_finding() -> None:
    text = """<b>JOSH 2.0 · COMPLETE</b>
<code>Model: openai/gpt-5.6-luna | Route: Josh 2.0 Inbox | Why: read-only host assessment</code>
<blockquote><b>Complete:</b> Yes - Telegram health assessed.</blockquote>
<b>What was done:</b>
• The gateway service has no remaining issues after its current health check.
• The runtime has no missing helpers in the canonical Telegram delivery path.
• There are no service failures in the current host snapshot.
<b>Issues:</b>
• None
<b>Appropriate next steps:</b>
• No action needed.
<b>Approval needed:</b>
• None"""
    assert stress.validate(text, FakeLiveModule()) == []


def test_validate_accepts_concrete_topic17_repair_outcomes() -> None:
    text = """<pre>Model: openai-codex/gpt-5.6-sol
   | Route: JAIMES execution
   | Why: origin-route repair

Complete: Yes - routing fixed

What was done:
- Missing topic metadata caused
  edits to enter the wrong chat.
- 26 misplaced card records were
  repaired without deleting history.
- Duplicate fast-ack cards were
  disabled; one owner remains.

Issues:
- n/a

Appropriate next steps:
- Keep a Topic 17 route canary.

Approval needed:
- n/a</pre>"""

    assert stress.validate(text, FakeLiveModule()) == []


def test_validate_rejects_duplicate_why_header() -> None:
    text = """<pre>Model: openai-codex/gpt-5.6-sol
   | Route: JAIMES execution
   | Why: primary | Why: duplicate

Complete: No - malformed header

What was done:
- Preserved the source response.
- Identified a duplicate field.
- Kept the result fail closed.

Issues:
- Header is malformed.

Appropriate next steps:
- Regenerate one verified header.

Approval needed:
- n/a</pre>"""

    assert "final summary header must contain exactly one Model, Route, and Why field" in stress.validate(
        text,
        FakeLiveModule(),
    )


def test_validate_allows_truthful_complete_no_deficiency_cards() -> None:
    text = """<pre>Model: unverified
   | Route: Josh 2.0 Inbox
   | Why: format recovery

Complete: No - findings incomplete

What was done:
- The source lacked three findings.
- Missing facts were not invented.
- A detailed result was not captured.

Issues:
- Detailed findings were not captured.

Appropriate next steps:
- Retry with evidence and findings.

Approval needed:
- Retry with evidence and findings.
- Adjust the plan.
- Cancel this task.</pre>"""

    assert stress.validate(text, FakeLiveModule()) == []


def test_live_target_requires_numeric_ids_and_explicit_production_confirmation() -> None:
    assert "--chat-id must be a numeric Telegram chat ID" in stress.live_target_problems("@channel", "1", True)
    assert "--thread-id must be a positive numeric Telegram topic ID" in stress.live_target_problems("-1001", "topic", True)
    assert "--confirm-production-canary is required for the production Inbox topic" in stress.live_target_problems(
        stress.PRODUCTION_CHAT_ID,
        stress.PRODUCTION_INBOX_THREAD_ID,
        False,
    )
    assert "--confirm-production-canary is required for the production Inbox topic" in stress.live_target_problems(
        stress.PRODUCTION_CHAT_ID,
        "01",
        False,
    )
    assert "--confirm-production-canary is required for the production Telegram chat" in stress.live_target_problems(
        stress.PRODUCTION_CHAT_ID,
        stress.PRODUCTION_JAIMES_THREAD_ID,
        False,
    )
    assert stress.live_target_problems(
        stress.PRODUCTION_CHAT_ID,
        stress.PRODUCTION_INBOX_THREAD_ID,
        True,
    ) == []


def test_jaimes_live_canary_reuses_secure_launcher_credential(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    launcher = tmp_path / "jaimes_telegram_fast_ack_launcher.py"
    launcher.write_text("# test launcher\n", encoding="utf-8")

    class FakeLauncher:
        @staticmethod
        def resolve_telegram_token() -> str:
            return "managed-test-token"

    monkeypatch.setattr(stress, "load_module", lambda path: FakeLauncher())
    stress.ensure_live_telegram_credential("jaimes", tmp_path / "jaimes_work_card.py")
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "managed-test-token"


def test_josh_live_canary_does_not_resolve_jaimes_credential(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(stress, "load_module", lambda _path: pytest.fail("must not load launcher"))
    stress.ensure_live_telegram_credential("josh2", tmp_path / "josh_work_card.py")
    assert "TELEGRAM_BOT_TOKEN" not in os.environ


def test_live_canary_closes_card_at_6_of_6_then_sends_exactly_one_final(monkeypatch) -> None:
    monkeypatch.setattr(stress.time, "sleep", lambda _seconds: None)
    module = FakeLiveModule()

    result = stress.live_canary(module, "-1001", "1")

    assert result["ok"] is True
    assert module.card_statuses[-1] == "done"
    terminal_edit = [call for call in module.calls if call[0] == "edit_rich"][-1]
    assert "██████████ 100% · stage 6/6" in terminal_edit[2]
    assert terminal_edit[2].count(" checked") == 6
    assert not terminal_edit[2].startswith("<pre>")
    assert "Progress [██████████] 6/6" in terminal_edit[3]
    assert result["timing"]["checks"]["terminalLiveCard100Percent"] is True
    assert result["final"] == {
        "attempts": 1,
        "successes": 1,
        "messageIds": ["103"],
        "exactlyOne": True,
    }
    assert len([call for call in module.calls if call[0] == "send_final"]) == 1
    assert result["cleanup"]["attempted"] == 3
    assert result["cleanup"]["deleted"] == 3
    assert "synthetic cumulative response timing" in result["scope"]
    assert "after the canary anchor receipt" in result["scope"]
    assert "never p95 or inbound-path evidence" in result["scope"]


def test_live_canary_uses_named_transport_arguments_for_jaimes_signature(monkeypatch) -> None:
    monkeypatch.setattr(stress.time, "sleep", lambda _seconds: None)

    class JaimesSignatureLiveModule(FakeLiveModule):
        def build_card(self, *, status: str, **_kwargs) -> str:
            self.card_statuses.append(status)
            if status == "done":
                return "<pre>✅ JAIMES — Complete\nProgress\n██████████ 100% · Complete\n\nNow\nSummary ready</pre>"
            return "<pre>✅ JAIMES — Verifying\nProgress\n████████░░ 83% · Verifying</pre>"

        def build_completion_summary(
            self,
            *,
            title,
            status,
            model="",
            now="",
            done=None,
            next_step="",
            blocker="None",
        ) -> str:
            return super().build_completion_summary(
                title=title,
                status=status,
                model=model,
                now=now,
                done=done,
                next_step=next_step,
                blocker=blocker,
            )

        def send_rich_message(
            self,
            rich_html,
            fallback_text,
            buttons,
            timeout,
            chat_id=None,
            thread_id=None,
        ) -> dict:
            assert buttons is None
            assert timeout == 15
            assert chat_id == "-1001"
            assert thread_id == "17"
            return super().send_rich_message(rich_html, fallback_text)

    result = stress.live_canary(JaimesSignatureLiveModule(), "-1001", "17")

    assert result["ok"] is True
    assert result["cleanup"]["deleted"] == result["cleanup"]["attempted"]


def test_live_canary_excludes_anchor_setup_from_response_slo(monkeypatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(stress.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(stress.time, "sleep", lambda _seconds: None)

    class TimedLiveModule(FakeLiveModule):
        def send_card(self, text, *args, **kwargs) -> dict:
            clock[0] += 3.0 if "Objective" not in text else 0.2
            return super().send_card(text, *args, **kwargs)

        def send_rich_message(self, rich, legacy, *args, **kwargs) -> dict:
            clock[0] += 0.2
            return super().send_rich_message(rich, legacy, *args, **kwargs)

        def send_final_summary(self, text, *args, **kwargs) -> dict:
            clock[0] += 0.1
            return super().send_final_summary(text, *args, **kwargs)

        def api_call(self, method: str, payload: dict, timeout: int = 15) -> dict:
            if method == "setMessageReaction":
                clock[0] += 0.1
            return super().api_call(method, payload, timeout)

    result = stress.live_canary(TimedLiveModule(), "-1001", "1")

    assert result["ok"] is True
    assert result["timing"]["setupMs"] == 3_000.0
    assert result["timing"]["cumulativeMs"]["eyes"] == 100.0
    assert "header" not in result["timing"]["cumulativeMs"]
    assert result["timing"]["cumulativeMs"]["liveCard"] == 300.0


@pytest.mark.parametrize(
    ("stage", "expected_send_stages", "expect_live_send"),
    [
        ("reaction", ["anchor"], False),
        ("live", ["anchor"], True),
        ("edit", ["anchor"], True),
    ],
)
def test_live_canary_fails_closed_and_cleans_up_after_structural_failure(
    monkeypatch,
    stage: str,
    expected_send_stages: list[str],
    expect_live_send: bool,
) -> None:
    monkeypatch.setattr(stress.time, "sleep", lambda _seconds: None)
    module = FakeLiveModule(fail_stage=stage)

    result = stress.live_canary(module, "-1001", "1")

    assert result["ok"] is False
    assert [call[1] for call in module.calls if call[0] == "send_card"] == expected_send_stages
    assert bool([call for call in module.calls if call[0] == "send_rich"]) is expect_live_send
    assert [call for call in module.calls if call[0] == "send_final"] == []
    assert result["final"]["attempts"] == 0
    assert result["cleanup"]["deleted"] == result["cleanup"]["attempted"]
    if stage == "live":
        assert result["cleanup"]["indeterminateStages"] == ["live-card"]
        assert "temporary canary cleanup is incomplete or indeterminate" in result["failures"]


def test_live_canary_reports_and_deletes_an_indeterminate_known_id(monkeypatch) -> None:
    monkeypatch.setattr(stress.time, "sleep", lambda _seconds: None)
    module = FakeLiveModule(fail_stage="live", live_indeterminate_id=True)

    result = stress.live_canary(module, "-1001", "1")

    assert result["ok"] is False
    assert result["cleanup"]["indeterminateIds"] == ["102"]
    assert result["cleanup"]["indeterminateStages"] == []
    assert ("delete", "102") in module.calls
    assert result["final"]["attempts"] == 0


def test_live_canary_reports_raw_timeout_as_unknown_indeterminate_stage(monkeypatch) -> None:
    monkeypatch.setattr(stress.time, "sleep", lambda _seconds: None)

    class HeaderEnabledModule(FakeLiveModule):
        @staticmethod
        def task_headers_enabled(_chat_id, _thread_id) -> bool:
            return True

    module = HeaderEnabledModule(fail_stage="header", fail_error="socket timeout after write")

    result = stress.live_canary(module, "-1001", "1")

    assert result["ok"] is False
    assert result["cleanup"]["indeterminateIds"] == []
    assert result["cleanup"]["indeterminateStages"] == ["task-header"]
    assert result["final"]["attempts"] == 0


def test_live_canary_counts_one_failed_final_attempt_and_returned_id_without_retry(monkeypatch) -> None:
    monkeypatch.setattr(stress.time, "sleep", lambda _seconds: None)
    module = FakeLiveModule(fail_stage="final", final_failure_id=True)

    result = stress.live_canary(module, "-1001", "1")

    assert result["ok"] is False
    assert result["final"] == {
        "attempts": 1,
        "successes": 0,
        "messageIds": ["103"],
        "exactlyOne": False,
    }
    assert len([call for call in module.calls if call[0] == "send_final"]) == 1
    assert ("delete", "103") in module.calls
    assert "exactly-one-final contract failed" in result["failures"]


def test_cleanup_uses_retry_after_and_continues_after_one_id_raises(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(stress.time, "sleep", sleeps.append)
    module = FakeLiveModule()
    module.delete_behaviors["103"] = [
        {"ok": False, "cooldown": {"retry_after_seconds": 2}},
        {"ok": True},
    ]
    module.delete_behaviors["102"] = [RuntimeError("delete exploded")] * 3

    result = stress.live_canary(module, "-1001", "1")

    assert result["ok"] is False
    assert result["cleanup"]["attempted"] == 3
    assert result["cleanup"]["deleted"] == 2
    assert result["cleanup"]["failedIds"] == ["102"]
    assert result["cleanup"]["records"][0]["messageId"] == "103"
    assert result["cleanup"]["records"][0]["waitsSeconds"] == [2.05]
    assert ("delete", "101") in module.calls
    assert 2.05 in sleeps


def test_basic_canary_cleans_up_when_edit_raises(monkeypatch) -> None:
    monkeypatch.setattr(stress.time, "sleep", lambda _seconds: None)
    module = FakeBasicModule(edit_raises=True)

    result = stress.live_canary(module, "-1001", "17")

    assert result["ok"] is False
    assert ("delete", "201") in module.calls
    assert result["cleanup"]["deleted"] == 1
    assert "basic edit failed: RuntimeError: edit exploded" in result["failures"]


def test_basic_canary_reports_retention_policy_cleanup_failure(monkeypatch) -> None:
    monkeypatch.setattr(stress.time, "sleep", lambda _seconds: None)
    module = FakeBasicModule(delete_allowed=False)

    result = stress.live_canary(module, "-1001", "17")

    assert result["ok"] is False
    assert result["cleanup"]["failedIds"] == ["201"]
    assert len([call for call in module.calls if call[0] == "delete"]) == 3
    assert "temporary canary cleanup is incomplete or indeterminate" in result["failures"]


def test_basic_canary_without_module_classifier_reports_raw_timeout_indeterminate(monkeypatch) -> None:
    monkeypatch.setattr(stress.time, "sleep", lambda _seconds: None)
    module = FakeBasicModule(send_error="socket timeout after request body was written")

    result = stress.live_canary(module, "-1001", "17")

    assert result["ok"] is False
    assert result["cleanup"]["indeterminateIds"] == []
    assert result["cleanup"]["indeterminateStages"] == ["basic-send"]


def test_live_canary_journals_intent_before_each_message_send_and_clears_after_cleanup(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(stress.time, "sleep", lambda _seconds: None)
    journal = tmp_path / "private" / "pending.json"
    monkeypatch.setenv(stress.CANARY_JOURNAL_ENV, str(journal))

    class JournalCheckingModule(FakeLiveModule):
        def _assert_intent(self, stage: str) -> None:
            payload = json.loads(journal.read_text(encoding="utf-8"))
            assert payload["stage"] == f"{stage}-intent"
            assert stage in payload["indeterminateStages"]

        def send_card(self, text, *args, **kwargs) -> dict:
            self._assert_intent("anchor")
            return super().send_card(text, *args, **kwargs)

        def send_rich_message(self, rich, legacy, *args, **kwargs) -> dict:
            self._assert_intent("live-card")
            return super().send_rich_message(rich, legacy, *args, **kwargs)

        def send_final_summary(self, text, *args, **kwargs) -> dict:
            self._assert_intent("structured-final")
            return super().send_final_summary(text, *args, **kwargs)

    result = stress.live_canary(JournalCheckingModule(), "-1001", "1")

    assert result["ok"] is True
    assert not journal.exists()
    assert journal.parent.stat().st_mode & 0o777 == 0o700


def test_live_canary_retains_private_journal_when_cleanup_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(stress.time, "sleep", lambda _seconds: None)
    journal = tmp_path / "private" / "pending.json"
    monkeypatch.setenv(stress.CANARY_JOURNAL_ENV, str(journal))
    module = FakeLiveModule()
    module.delete_behaviors["102"] = [{"ok": False, "error": "retention policy"}] * 3

    result = stress.live_canary(module, "-1001", "1")
    payload = json.loads(journal.read_text(encoding="utf-8"))

    assert result["ok"] is False
    assert payload["stage"] == "cleanup-pending"
    assert payload["messageIds"] == ["102"]
    assert payload["indeterminateStages"] == []
    assert journal.stat().st_mode & 0o777 == 0o600


def test_cleanup_treats_a_previously_deleted_journal_message_as_absent() -> None:
    module = FakeLiveModule()
    module.delete_behaviors["901"] = [{"ok": False, "error": "Bad Request: message to delete not found"}]

    result = stress.delete_with_retry(module, "-1001", "901")

    assert result["deleted"] is True
    assert result["alreadyAbsent"] is True
    assert result["attempts"] == 1


def test_negative_delivery_without_receipt_or_definitive_error_is_indeterminate() -> None:
    assert stress.delivery_is_indeterminate(object(), {"ok": False}) is True
    assert stress.delivery_is_indeterminate(object(), {}) is True
    assert stress.delivery_is_indeterminate(
        object(), {"ok": False, "error": "Bad Request: chat not found"}
    ) is False
