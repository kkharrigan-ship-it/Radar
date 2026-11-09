import re
import unittest
from pathlib import Path

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"
SECRETS = ["NOTION_TOKEN", "DISCORD_BOT_TOKEN", "PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY",
           "NOTION_LOG_DS", "NOTION_SOURCES_DS", "NOTION_SETS_DS", "NOTION_KITS_DS",
           "NOTION_COLLECTION_DS", "RADAR_TZ"]


class FastLaneWorkflow(unittest.TestCase):
    text = (WF / "fast-lane.yml").read_text()

    def test_schedule_concurrency_timeout(self):
        self.assertIn('- cron: "3-59/5 * * * *"', self.text)
        self.assertRegex(self.text, r"(?m)^concurrency: radar$")
        self.assertRegex(self.text, r"(?m)^    timeout-minutes: 4$")
        self.assertRegex(self.text, r"(?m)^  contents: read$")

    def test_only_the_schedule_triggers_it(self):
        on = self.text.split("\non:\n", 1)[1].split("\nconcurrency:", 1)[0]
        self.assertEqual(on.strip().splitlines()[0].strip(), "schedule:")
        for trigger in ("push", "pull_request", "workflow_dispatch", "pull_request_target"):
            self.assertNotRegex(on, rf"(?m)^\s*{trigger}:")

    def test_ten_secrets_by_name_in_env(self):
        for name in SECRETS:
            self.assertIn(f"{name}: ${{{{ secrets.{name} }}}}", self.text)
        self.assertEqual(len(re.findall(r"secrets\.", self.text)), 10)

    def test_no_secret_in_a_run_line(self):
        for line in self.text.splitlines():
            if re.match(r"\s*-?\s*run:", line):
                self.assertNotIn("secrets.", line)
        self.assertIn("- run: python -m radar.fast_lane", self.text)


class KeepaliveWorkflow(unittest.TestCase):
    text = (WF / "keepalive.yml").read_text()

    def test_schedule_and_permission(self):
        self.assertIn('- cron: "17 3 1 * *"', self.text)
        self.assertRegex(self.text, r"(?m)^  contents: write$")
        self.assertIn("keepalive.txt", self.text)
