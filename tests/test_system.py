import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fakes import FakeClock, FakeNotion, FakePushover, make_settings
from radar import notion as n, system
from radar.http import Response
from radar.log_rows import Source
from radar.system import RunProblems

TZ = ZoneInfo("America/Chicago")
S = make_settings()
DAY = datetime(2026, 11, 8, 9, 3, tzinfo=TZ)


def src(name="Example Vendor", count=3, last="Discord unreachable", enabled=True):
    return Source(f"00000000-0000-4000-8000-0000000000{len(name):02d}", name, "Discord channel", "9000000000000000501",
                  enabled=enabled, error_count=count, last_error=last)


class SystemRows(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(DAY)
        self.f = FakeNotion(self.clock)

    def keys(self):
        return sorted(self.f.value(p.id, "Dedupe Key") for p in self.f.rows_in(S.log_ds))

    def test_failing_source_once_a_day(self):
        rows = system.make_system_rows(self.f, S, RunProblems([src()], False, False), self.clock)
        self.assertEqual(self.keys(), ["error:source:Example Vendor:2026-11-08"])
        row = rows[0]
        self.assertEqual(n.read_text(row.props["Why"]),
                         "Radar: Example Vendor has failed 3 runs in a row. Last error: Discord unreachable")
        self.assertEqual(n.read_select(row.props["Match Type"]), "System")
        self.assertEqual(n.read_select(row.props["Source"]), "Radar")
        self.assertEqual(n.read_select(row.props["Stage"]), "Push pending")
        self.assertIsNone(n.read_select(row.props.get("Confidence")))
        again = system.make_system_rows(self.f, S, RunProblems([src(count=4)], False, False), self.clock)
        self.assertEqual(again, [])
        self.assertEqual(len(self.keys()), 1)

    def test_pushes_and_judging(self):
        system.make_system_rows(self.f, S, RunProblems([], True, True), self.clock)
        system.make_system_rows(self.f, S, RunProblems([], True, True), self.clock)
        self.assertEqual(self.keys(), ["error:judging:2026-11-08", "error:pushes:2026-11-08"])
        why = {self.f.value(p.id, "Dedupe Key"): self.f.value(p.id, "Why") for p in self.f.rows_in(S.log_ds)}
        self.assertEqual(why["error:pushes:2026-11-08"],
                         "Radar: some alerts failed to send or may not have arrived. See Needs attention.")
        self.assertEqual(why["error:judging:2026-11-08"],
                         "Radar: a row couldn't be judged after 3 tries. See Needs attention.")

    def test_no_problems_no_rows(self):
        self.assertEqual(system.make_system_rows(self.f, S, RunProblems([], False, False), self.clock), [])
        self.assertEqual(self.f.rows_in(S.log_ds), [])

    def test_today_is_local(self):
        clock = FakeClock(datetime(2026, 11, 8, 5, 30, tzinfo=timezone.utc))
        system.make_system_rows(self.f, S, RunProblems([], True, False), clock)
        self.assertEqual(self.keys(), ["error:pushes:2026-11-07"])


class Heartbeat(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(DAY.replace(hour=7, minute=59))
        self.f = FakeNotion(self.clock)
        yesterday = DAY - timedelta(days=1)
        for i in range(3):
            self.f.add(S.log_ds, {"Stage": n.select("Pushed"), "Match Type": n.select("Wanted"),
                                  "Pushed At": n.date(yesterday.replace(hour=10 + i))})
        self.f.add(S.log_ds, {"Stage": n.select("Pushed"), "Match Type": n.select("System"),
                              "Pushed At": n.date(yesterday)})
        self.f.add(S.log_ds, {"Stage": n.select("Pushed"), "Match Type": n.select("Wanted"),
                              "Pushed At": n.date(DAY.replace(hour=1))})
        self.f.add(S.log_ds, {"Stage": n.select("Pushed"), "Match Type": n.select("Wanted"),
                              "Pushed At": n.date(yesterday.replace(hour=0, minute=0) - timedelta(minutes=1))})
        self.f.add(S.log_ds, {"Stage": n.select("Held (cap)"), "Match Type": n.select("Wanted"),
                              "Found At": n.date(yesterday)})
        self.sources = [src("Example Vendor", 1), src("Keebdex live", 0), src("Off", 5, enabled=False)]

    def beat(self):
        return system.make_heartbeat(self.f, S, self.sources, self.clock)

    def test_times_and_counts(self):
        self.assertIsNone(self.beat())
        self.clock.advance(minutes=4)  # 08:03
        page = self.beat()
        self.assertEqual(n.read_text(page.props["Why"]),
                         "Radar alive. Yesterday: 3 alerts, 1 held. Sources with errors: Example Vendor")
        self.assertEqual(n.read_text(page.props["Dedupe Key"]), "heartbeat:2026-11-08")
        self.clock.advance(minutes=5)  # 08:08
        self.assertIsNone(self.beat())
        self.assertEqual(len(self.f.find(S.log_ds, "Dedupe Key", "heartbeat:2026-11-08")), 1)

    def test_no_source_errors(self):
        self.clock.advance(minutes=4)
        self.sources = [src("Keebdex live", 0)]
        self.assertTrue(n.read_text(self.beat().props["Why"]).endswith("Sources with errors: none"))


class SendSystemRows(unittest.TestCase):
    def test_failed_send_goes_back_to_pending(self):
        clock = FakeClock(DAY)
        f = FakeNotion(clock)
        rows = system.make_system_rows(f, S, RunProblems([], True, False), clock)
        system.send_system_rows(f, S, FakePushover(lambda form: Response(500)), clock, rows)
        self.assertEqual(f.value(rows[0].id, "Stage"), "Push pending")
        self.assertEqual(len(f.rows_in(S.log_ds)), 1)
        fake = FakePushover()
        system.send_system_rows(f, S, fake, clock, rows)
        self.assertEqual(f.value(rows[0].id, "Stage"), "Pushed")
        self.assertEqual(fake.calls[0]["title"], "Radar: System")
