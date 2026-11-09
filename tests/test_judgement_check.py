import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from radar.judgement_check import check, parse

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "judgements"
VALID_TEXT = (FIX / "valid_great_deal.json").read_text()
VALID = json.loads(VALID_TEXT)
WANTED = {"GMK Testbed Nº3": ["Light Base"]}


def j(**changes):
    d = dict(VALID)
    for k, v in changes.items():
        if v is ...:
            del d[k]
        else:
            d[k] = v
    return json.dumps(d)


class Read(unittest.TestCase):
    def test_valid_sample_passes_push_pending(self):
        self.assertEqual(check(VALID_TEXT, {}), ("pass", "Push pending", ""))

    def test_empty(self):
        for t in ("", "   ", "\n\t"):
            self.assertEqual(check(t, {}), ("fail", None, "empty"))

    def test_not_json(self):
        for t in ("hello", "[]", "{} {}", "NaN", "3", '"x"', VALID_TEXT + " trailing"):
            with self.subTest(t=t[:20]):
                self.assertEqual(check(t, {}), ("fail", None, "not JSON"))

    def test_fenced_json_passes(self):
        for fence in ("```json\n", "```\n"):
            self.assertEqual(check(fence + VALID_TEXT + "\n```", {}), ("pass", "Push pending", ""))

    def test_empty_fence_is_empty(self):
        self.assertEqual(check("```json\n\n```", {}), ("fail", None, "empty"))

    def test_parse_exposed(self):
        self.assertEqual(parse(VALID_TEXT), VALID)
        self.assertIsNone(parse("nope"))


class Schema(unittest.TestCase):
    def test_problems(self):
        cases = [
            (j(why=...), "why: missing"),
            (j(note="x"), "note: not allowed"),
            (j(price_usd="85"), "price_usd: should be number"),
            (j(alert=1), "alert: should be boolean"),
            (j(price_usd=True), "price_usd: should be number"),
            (j(confidence="very"), "confidence: not an allowed value"),
            (j(item=3), "item: should be string"),
        ]
        for text, error in cases:
            with self.subTest(error=error):
                self.assertEqual(check(text, WANTED), ("fail", None, error))

    def test_first_problem_in_order(self):
        self.assertEqual(check(j(status=..., why=...), {})[2], "status: missing")
        self.assertEqual(check(j(zeta=1, alpha=1), {})[2], "alpha: not allowed")
        self.assertEqual(check(j(alert="x", price_usd="x"), {})[2], "alert: should be boolean")

    def test_float_is_a_number(self):
        self.assertEqual(check(j(price_usd=85.5), {})[0], "pass")


class Consistency(unittest.TestCase):
    def test_four_contradictions(self):
        cases = [
            (j(status="unusable", alert=True), "contradiction: unusable but alerting"),
            (j(status="unusable", alert=False, match_type="great_deal"), "contradiction: unusable but alerting"),
            (j(match_type="none", alert=True), "contradiction: no match but alerting"),
            (j(price_known=False, price_usd=85), "contradiction: price unknown but price_usd set"),
            (j(price_basis="none", fair_price_usd=110), "contradiction: no price basis but fair_price_usd set"),
        ]
        for text, error in cases:
            with self.subTest(error=error):
                self.assertEqual(check(text, {}), ("fail", None, error))


class WantedList(unittest.TestCase):
    def wanted(self, kit, set_name="GMK Testbed Nº3"):
        return j(match_type="wanted", set_name=set_name, kit_name=kit,
                 price_known=False, price_usd=0, price_basis="none", fair_price_usd=0)

    def test_wanted_needs_set_and_kit(self):
        self.assertEqual(check(self.wanted(""), WANTED), ("reject", "No alert", "set not on wanted list"))
        self.assertEqual(check(self.wanted("Light Base"), WANTED), ("pass", "Push pending", ""))

    def test_names_are_exact(self):
        self.assertEqual(check(self.wanted("Light Base", "GMK Testbed No3"), WANTED)[0], "reject")
        self.assertEqual(check(self.wanted("light base"), WANTED)[0], "reject")

    def test_group_buy_needs_set(self):
        self.assertEqual(check(j(match_type="group_buy", set_name="GMK Elsewhere"), WANTED),
                         ("reject", "No alert", "set not on wanted list"))
        self.assertEqual(check(j(match_type="group_buy", set_name="GMK Testbed Nº3"), WANTED),
                         ("pass", "Push pending", ""))


class Stage(unittest.TestCase):
    def test_great_deal_needs_a_fair_price_and_confidence(self):
        self.assertEqual(check(j(fair_price_usd=0, price_basis="none"), {}), ("pass", "No alert", ""))
        self.assertEqual(check(j(fair_price_usd=0), {}), ("pass", "No alert", ""))
        self.assertEqual(check(j(confidence="low"), {}), ("pass", "No alert", ""))

    def test_unusable(self):
        text = j(status="unusable", unusable_reason="not_a_listing", alert=False, match_type="none")
        self.assertEqual(check(text, {}), ("pass", "Unreadable", ""))

    def test_no_alert(self):
        self.assertEqual(check(j(alert=False), {}), ("pass", "No alert", ""))


class SchemaFile(unittest.TestCase):
    def test_required_is_the_build_plan_fields_in_order(self):
        schema = json.loads((ROOT / "schemas" / "judgement.schema.json").read_text())
        self.assertEqual(schema["required"], [
            "status", "unusable_reason", "alert", "match_type", "item", "set_name",
            "kit_name", "price_text", "price_known", "price_usd", "fair_price_usd",
            "price_basis", "confidence", "why", "suspicious_text",
        ])


class CommandLine(unittest.TestCase):
    def run_cli(self, *args, stdin=None):
        return subprocess.run([sys.executable, "-m", "radar.judgement_check", *args],
                              cwd=ROOT, input=stdin, capture_output=True, text=True)

    def test_file_matches_check(self):
        r = self.run_cli(str(FIX / "valid_great_deal.json"))
        self.assertEqual((r.returncode, r.stdout), (0, "pass Push pending -\n"))

    def test_stdin_and_wanted(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        wanted = Path(tmp.name) / "wanted.json"
        wanted.write_text(json.dumps(WANTED), encoding="utf-8")
        text = j(match_type="wanted", set_name="GMK Testbed Nº3", kit_name="",
                 price_known=False, price_usd=0, price_basis="none", fair_price_usd=0)
        r = self.run_cli("-", "--wanted", str(wanted), stdin=text)
        self.assertEqual((r.returncode, r.stdout), (0, "reject No alert set not on wanted list\n"))

    def test_fail_line(self):
        r = self.run_cli("-", stdin="hello")
        self.assertEqual((r.returncode, r.stdout), (0, "fail - not JSON\n"))

    def test_usage_and_file_errors_exit_2(self):
        self.assertEqual(self.run_cli().returncode, 2)
        self.assertEqual(self.run_cli(str(FIX / "nope.json")).returncode, 2)
