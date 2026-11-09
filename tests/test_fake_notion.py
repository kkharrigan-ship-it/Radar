import unittest
from datetime import datetime, timedelta, timezone

from fakes import FakeClock, FakeNotion
from radar import notion as n
from radar.notion import NotionUnavailable

T0 = datetime(2026, 11, 8, 15, 0, tzinfo=timezone.utc)


class FakeNotionFilters(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(T0)
        self.f = FakeNotion(self.clock)
        self.a = self.f.add("log", {"Stage": n.select("Pushed"), "Dedupe Key": n.text("k1"),
                                    "Found At": n.date(T0 + timedelta(hours=1))})
        self.b = self.f.add("log", {"Stage": n.select("Push unknown"), "Dedupe Key": n.text("k2"),
                                    "Found At": n.date(T0)})
        self.f.add("other", {"Stage": n.select("Pushed")})

    def ids(self, filter=None, sorts=None):
        return [p.id for p in self.f.query("log", filter, sorts)]

    def test_select_text_and_or(self):
        self.assertEqual(self.ids({"property": "Stage", "select": {"equals": "Pushed"}}), [self.a])
        self.assertEqual(self.ids({"property": "Dedupe Key", "rich_text": {"equals": "k2"}}), [self.b])
        self.assertEqual(len(self.ids({"or": [{"property": "Stage", "select": {"equals": "Pushed"}},
                                              {"property": "Stage", "select": {"equals": "Push unknown"}}]})), 2)
        self.assertEqual(self.ids({"and": [{"property": "Stage", "select": {"equals": "Pushed"}},
                                           {"property": "Dedupe Key", "rich_text": {"equals": "k2"}}]}), [])

    def test_dates_and_sorting(self):
        self.assertEqual(self.ids({"property": "Found At", "date": {"on_or_after": (T0 + timedelta(minutes=1)).isoformat()}}), [self.a])
        self.assertEqual(self.ids({"property": "Found At", "date": {"before": (T0 + timedelta(minutes=1)).isoformat()}}), [self.b])
        self.assertEqual(self.ids(sorts=[{"property": "Found At", "direction": "ascending"}]), [self.b, self.a])

    def test_last_edited_and_failures(self):
        self.clock.advance(minutes=20)
        self.f.update(self.a, {"Stage": n.select("Sending")})
        cutoff = (self.clock.now() - timedelta(minutes=10)).isoformat()
        self.assertEqual(self.ids({"timestamp": "last_edited_time", "last_edited_time": {"before": cutoff}}), [self.b])
        self.f.fail_next("update", when=lambda pid, props: "Cursor" in props)
        self.f.update(self.a, {"Stage": n.select("Pushed")})
        with self.assertRaises(NotionUnavailable):
            self.f.update(self.a, {"Cursor": n.text("x")})
        self.f.update(self.a, {"Cursor": n.text("x")})
        self.assertEqual(self.f.value(self.a, "Cursor"), "x")
