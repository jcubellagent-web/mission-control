import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "jaimes_sorare_resource_manager.py"
SPEC = importlib.util.spec_from_file_location("jaimes_sorare_resource_manager", MODULE_PATH)
assert SPEC and SPEC.loader
resource = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resource)


def card(slug="card-1", *, xp=100, next_grade=125, grade=3, cooldown=None):
    return {
        "slug": slug,
        "xp": xp,
        "xpNeededForNextGrade": next_grade,
        "grade": grade,
        "nextLevelUpAvailableAt": cooldown,
        "powerBreakdown": {"xpBasisPoints": grade * 100},
        "baseballPlayer": {"displayName": slug},
    }


class ResourceManagerTests(unittest.TestCase):
    def test_apply_is_fail_closed_without_all_three_controls(self):
        with self.assertRaises(PermissionError):
            resource.ensure_apply_authorized(False, resource.APPROVAL_TOKEN, "telegram-123")
        with self.assertRaises(PermissionError):
            resource.ensure_apply_authorized(True, "wrong", "telegram-123")
        with self.assertRaises(PermissionError):
            resource.ensure_apply_authorized(True, resource.APPROVAL_TOKEN, "")
        resource.ensure_apply_authorized(True, resource.APPROVAL_TOKEN, "telegram-123")

    def test_validate_plan_checks_exact_gap_balance_and_cooldown(self):
        now = datetime.now(timezone.utc)
        plan = {"max_spend": 25, "cards": [{"slug": "card-1", "xp_needed": 25}]}
        verified = resource.validate_plan(plan, [card()], 25, now)
        self.assertEqual(verified[0]["xp_needed"], 25)

        with self.assertRaisesRegex(ValueError, "gap drift"):
            resource.validate_plan({"max_spend": 25, "cards": [{"slug": "card-1", "xp_needed": 24}]}, [card()], 25, now)
        with self.assertRaisesRegex(ValueError, "Insufficient"):
            resource.validate_plan(plan, [card()], 24, now)
        with self.assertRaisesRegex(ValueError, "cooldown"):
            resource.validate_plan(plan, [card(cooldown=(now + timedelta(hours=1)).isoformat())], 25, now)

    def test_new_card_detection_baselines_then_detects_only_additions(self):
        cards = [card("a"), card("b")]
        self.assertEqual(resource.detect_new_slugs(None, cards), [])
        previous = {"cards": [{"slug": "a"}]}
        self.assertEqual(resource.detect_new_slugs(previous, cards), ["b"])

    def test_fetch_inventory_and_balance_are_query_only(self):
        calls = []

        def gql(_headers, query, variables=None):
            calls.append((query, variables))
            if "ResourceCards" in query:
                return {"data": {"currentUser": {"cards": {"nodes": [card()], "pageInfo": {"hasNextPage": False}}}}}
            return {"data": {"currentUser": {"balances": [{"currency": "LIMITED_XP", "amount": 99}]}}}

        self.assertEqual(len(resource.fetch_cards(gql, {})), 1)
        self.assertEqual(resource.fetch_balance(gql, {}), 99)
        self.assertTrue(all("mutation" not in query.lower() for query, _ in calls))

    def test_fast_lane_includes_read_only_resource_audit(self):
        text = (Path(__file__).parents[1] / "scripts" / "jaimes_sorare_fast_lane.py").read_text()
        self.assertIn('"resource_audit"', text)
        self.assertIn('"--mode",\n                    "audit"', text)
        self.assertNotIn("--execute", text)


if __name__ == "__main__":
    unittest.main()
