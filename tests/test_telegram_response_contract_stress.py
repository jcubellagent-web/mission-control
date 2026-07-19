from __future__ import annotations

import importlib.util
import json
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

    def build_task_header(self, **_kwargs) -> str:
        return "<pre>Objective | synthetic QA\nOwner     | Josh 2.0\nAgent     | system\nModels    | transport canary</pre>"

    def build_card(self, *, status: str, **_kwargs) -> str:
        self.card_statuses.append(status)
        if status == "done":
            return "<pre>JOSH 2.0 · Complete\nProgress [██████████] 6/6\n\nNow\nResult verified and ready.</pre>"
        return "<pre>JOSH 2.0 · Verifying\nProgress [████████░░] 5/6\n\nNow\nVerifying the result.</pre>"

    def build_rich_card(self, *, status: str, **_kwargs) -> str:
        if status == "done":
            return "<pre>JOSH 2.0 · Complete\nProgress [██████████] 6/6\n\nNow\nResult verified and ready.</pre>"
        return "<pre>JOSH 2.0 · Verifying\nProgress [████████░░] 5/6\n\nNow\nVerifying the result.</pre>"

    def build_completion_summary(self, **_kwargs) -> str:
        return """<pre>Model: x | Route: qa | Why: test

Complete: Yes - QA complete

What was done:
- Confirmed eyes reaction delivery.
- Verified that header rendered.
- 6 terminal checks passed.

Issues:
- n/a

Appropriate next steps:
- No action needed.

Approval needed:
- n/a</pre>"""

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


def test_render_stress_covers_header_progress_rich_terminal_and_final_contracts() -> None:
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


def test_validate_requires_one_preformatted_final_block() -> None:
    text = FakeLiveModule().build_completion_summary().removeprefix("<pre>").removesuffix("</pre>")

    assert "final summary must be one Telegram HTML pre block" in stress.validate(text, FakeLiveModule())


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


def test_live_canary_closes_card_at_6_of_6_then_sends_exactly_one_final(monkeypatch) -> None:
    monkeypatch.setattr(stress.time, "sleep", lambda _seconds: None)
    module = FakeLiveModule()

    result = stress.live_canary(module, "-1001", "1")

    assert result["ok"] is True
    assert module.card_statuses[-1] == "done"
    terminal_edit = [call for call in module.calls if call[0] == "edit_rich"][-1]
    assert "Progress [██████████] 6/6" in terminal_edit[2]
    assert "Progress [██████████] 6/6" in terminal_edit[3]
    assert result["timing"]["checks"]["terminalLiveCard100Percent"] is True
    assert result["final"] == {
        "attempts": 1,
        "successes": 1,
        "messageIds": ["104"],
        "exactlyOne": True,
    }
    assert len([call for call in module.calls if call[0] == "send_final"]) == 1
    assert result["cleanup"]["attempted"] == 4
    assert result["cleanup"]["deleted"] == 4
    assert "synthetic cumulative response timing" in result["scope"]
    assert "after the canary anchor receipt" in result["scope"]
    assert "never p95 or inbound-path evidence" in result["scope"]


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
    assert result["timing"]["cumulativeMs"]["header"] == 300.0
    assert result["timing"]["cumulativeMs"]["liveCard"] == 500.0


@pytest.mark.parametrize(
    ("stage", "expected_send_stages", "expect_live_send"),
    [
        ("reaction", ["anchor"], False),
        ("header", ["anchor", "header"], False),
        ("live", ["anchor", "header"], True),
        ("edit", ["anchor", "header"], True),
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
    assert result["cleanup"]["indeterminateIds"] == ["103"]
    assert result["cleanup"]["indeterminateStages"] == []
    assert ("delete", "103") in module.calls
    assert result["final"]["attempts"] == 0


def test_live_canary_reports_raw_timeout_as_unknown_indeterminate_stage(monkeypatch) -> None:
    monkeypatch.setattr(stress.time, "sleep", lambda _seconds: None)
    module = FakeLiveModule(fail_stage="header", fail_error="socket timeout after write")

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
        "messageIds": ["104"],
        "exactlyOne": False,
    }
    assert len([call for call in module.calls if call[0] == "send_final"]) == 1
    assert ("delete", "104") in module.calls
    assert "exactly-one-final contract failed" in result["failures"]


def test_cleanup_uses_retry_after_and_continues_after_one_id_raises(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(stress.time, "sleep", sleeps.append)
    module = FakeLiveModule()
    module.delete_behaviors["104"] = [
        {"ok": False, "cooldown": {"retry_after_seconds": 2}},
        {"ok": True},
    ]
    module.delete_behaviors["103"] = [RuntimeError("delete exploded")] * 3

    result = stress.live_canary(module, "-1001", "1")

    assert result["ok"] is False
    assert result["cleanup"]["attempted"] == 4
    assert result["cleanup"]["deleted"] == 3
    assert result["cleanup"]["failedIds"] == ["103"]
    assert result["cleanup"]["records"][0]["messageId"] == "104"
    assert result["cleanup"]["records"][0]["waitsSeconds"] == [2.05]
    assert ("delete", "102") in module.calls and ("delete", "101") in module.calls
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
            self._assert_intent("task-header" if "Objective" in text else "anchor")
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
    module.delete_behaviors["103"] = [{"ok": False, "error": "retention policy"}] * 3

    result = stress.live_canary(module, "-1001", "1")
    payload = json.loads(journal.read_text(encoding="utf-8"))

    assert result["ok"] is False
    assert payload["stage"] == "cleanup-pending"
    assert payload["messageIds"] == ["103"]
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
