import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fakes import FakeClock, FakeNotion, FakePushover, make_settings
from radar import notion as n, push
from radar.http import NoConnection, Response, Timeout
from radar.push import Outcome

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 8, 10, 3, tzinfo=TZ)
S = make_settings()


def alert(f, why="Light Base of GMK Testbed Nº3 mentioned in Example Vendor", found=NOW, match="Wanted",
          stage="Push pending", **extra):
    return f.add(S.log_ds, {
        "Alert": n.title(f"{match}: x · y"), "Stage": n.select(stage), "Match Type": n.select(match),
        "Why": n.text(why), "Link": n.url("https://discord.com/channels/9000000000000000001/9000000000000000002/9000000000000000003"),
        "Found At": n.date(found), "Push Attempts": n.number(0), **extra})


def pushed_today(f, k, stage="Pushed", match="Wanted"):
    for i in range(k):
        f.add(S.log_ds, {"Stage": n.select(stage), "Match Type": n.select(match),
                         "Pushed At": n.date(NOW.replace(hour=1) + timedelta(minutes=i))})


class SendPushover(unittest.TestCase):
    def test_outcomes(self):
        cases = [(Response(200, {}, b'{"status":1,"request":"r"}'), Outcome.ACCEPTED),
                 (Response(200, {}, b'{"status":0}'), Outcome.ERROR),
                 (Response(200, {}, b'nope'), Outcome.ERROR),
                 (Response(400, {}, b'{"status":0,"errors":["x"]}'), Outcome.ERROR),
                 (Response(429), Outcome.ERROR), (Response(500), Outcome.ERROR),
                 (Timeout(), Outcome.TIMEOUT), (NoConnection(), Outcome.ERROR)]
        for result, outcome in cases:
            with self.subTest(outcome=outcome, result=result):
                fake = FakePushover(lambda form, r=result: r)
                self.assertEqual(push.send_pushover(fake, "app", "user", "t", "m", ""), outcome)

    def test_last_problem_names_the_cause_in_plain_words(self):
        cases = [(Response(400, {}, b'{"token":"invalid","errors":["application token is invalid, see https://pushover.net/api"],"status":0}'),
                  "pushover 400 token invalid"),
                 (Response(400, {}, b'{"user":"invalid","Weird Key":"invalid","status":0}'), "pushover 400 user invalid"),
                 (Response(500), "pushover 500"), (Response(200, {}, b'{"status":0}'), "pushover 200"),
                 (Timeout(), "pushover timeout"), (NoConnection(), "pushover unreachable")]
        for result, words in cases:
            with self.subTest(words=words):
                push.last_problem = ""
                push.send_pushover(FakePushover(lambda form, r=result: r), "app-secret", "user-secret", "t", "m", "")
                self.assertEqual(push.last_problem, words)
        push.last_problem = ""
        push.send_pushover(FakePushover(lambda form: Response(200, {}, b'{"status":1}')), "a", "u", "t", "m", "")
        self.assertEqual(push.last_problem, "")

    def test_form_and_limits(self):
        fake = FakePushover()
        push.send_pushover(fake, "app", "user", "T" * 300, "M" * 2000, "https://x.invalid/" + "u" * 600)
        form = fake.calls[0]
        self.assertEqual(fake.urls[0], "https://api.pushover.net/1/messages.json")
        self.assertEqual((form["token"], form["user"], form["url_title"]), ("app", "user", "Open"))
        self.assertEqual((len(form["title"]), len(form["message"]), len(form["url"])), (250, 1024, 512))

    def test_empty_url_left_out(self):
        fake = FakePushover()
        push.send_pushover(fake, "app", "user", "t", "m", "")
        self.assertNotIn("url", fake.calls[0])
        self.assertNotIn("url_title", fake.calls[0])


class Queue(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(NOW)
        self.f = FakeNotion(self.clock)

    def run_queue(self, fake):
        return push.run_queue(self.f, S, fake, self.clock)

    def stage(self, pid):
        return self.f.value(pid, "Stage")

    def test_accepted(self):
        a = alert(self.f)
        fake = FakePushover()
        r = self.run_queue(fake)
        self.assertEqual(self.stage(a), "Pushed")
        self.assertEqual(self.f.value(a, "Pushed At"), NOW)
        self.assertEqual(fake.calls[0]["title"], "Radar: Wanted")
        self.assertEqual(fake.calls[0]["message"], "Light Base of GMK Testbed Nº3 mentioned in Example Vendor")
        self.assertEqual((r.pushed, r.held, r.failed, r.unknown, r.alert_problem), (1, 0, 0, 0, False))

    def test_message_falls_back_to_alert(self):
        alert(self.f, why="")
        fake = FakePushover()
        self.run_queue(fake)
        self.assertEqual(fake.calls[0]["message"], "Wanted: x · y")

    def test_500_three_times_fails(self):
        a = alert(self.f)
        fake = FakePushover(lambda form: Response(500))
        self.run_queue(fake)
        self.assertEqual((self.stage(a), self.f.value(a, "Push Attempts")), ("Push pending", 1))
        self.run_queue(fake)
        r = self.run_queue(fake)
        self.assertEqual((self.stage(a), self.f.value(a, "Push Attempts")), ("Push failed", 3))
        self.assertTrue(r.alert_problem)
        self.assertEqual(r.failed, 1)

    def test_timeout_is_unknown_and_not_resent(self):
        a = alert(self.f)
        fake = FakePushover(lambda form: Timeout())
        r = self.run_queue(fake)
        self.assertEqual(self.stage(a), "Push unknown")
        self.assertEqual(self.f.value(a, "Pushed At"), NOW)
        self.assertTrue(r.alert_problem)
        self.run_queue(fake)
        self.assertEqual(len(fake.calls), 1)

    def test_system_row_never_gives_up(self):
        s = alert(self.f, match="System", why="Radar: test")
        fake = FakePushover(lambda form: Timeout())
        for _ in range(3):
            r = self.run_queue(fake)
            self.assertEqual(self.stage(s), "Push pending")
            self.assertFalse(r.alert_problem)
        fake.responder = lambda form: Response(500)
        for _ in range(3):
            self.run_queue(fake)
        self.assertEqual(self.stage(s), "Push pending")
        self.assertEqual(len(fake.calls), 6)

    def test_cap(self):
        pushed_today(self.f, 19)
        rows = [alert(self.f, found=NOW - timedelta(minutes=3 - i)) for i in range(3)]
        fake = FakePushover()
        r = self.run_queue(fake)
        self.assertEqual([self.stage(p) for p in rows], ["Pushed", "Held (cap)", "Held (cap)"])
        self.assertEqual((r.pushed, r.held), (1, 2))
        self.assertEqual(len(self.f.find(S.log_ds, "Dedupe Key", "cap:2026-11-08")), 1)
        cap = self.f.find(S.log_ds, "Dedupe Key", "cap:2026-11-08")[0]
        self.assertEqual(self.f.value(cap, "Match Type"), "System")
        self.assertEqual(self.f.value(cap, "Why"), "Radar hit today's 20-push cap. Held alerts are in Notion.")
        self.assertEqual([p.id for p in r.system_rows], [cap])
        self.f.update(cap, {"Stage": n.select("Pushed"), "Pushed At": n.date(NOW)})
        alert(self.f)
        r2 = self.run_queue(fake)
        self.assertEqual(r2.held, 1)
        self.assertEqual(len(self.f.find(S.log_ds, "Dedupe Key", "cap:2026-11-08")), 1)
        self.assertEqual(r2.system_rows, [])

    def test_system_row_sent_at_cap(self):
        pushed_today(self.f, 20)
        s = alert(self.f, match="System", why="Radar: note")
        fake = FakePushover()
        self.run_queue(fake)
        self.assertEqual(self.stage(s), "Pushed")

    def test_unknown_counts_towards_cap_but_system_and_yesterday_do_not(self):
        pushed_today(self.f, 10)
        pushed_today(self.f, 10, stage="Push unknown")
        pushed_today(self.f, 5, match="System")
        self.f.add(S.log_ds, {"Stage": n.select("Pushed"), "Match Type": n.select("Wanted"),
                              "Pushed At": n.date(NOW - timedelta(days=1))})
        a = alert(self.f)
        self.run_queue(FakePushover())
        self.assertEqual(self.stage(a), "Held (cap)")

    def test_sending_mark_before_call(self):
        a = alert(self.f)
        seen = []
        self.run_queue(FakePushover(lambda form: seen.append(self.stage(a))))
        self.assertEqual(seen, ["Sending"])

    def test_sending_write_fails_no_call(self):
        a = alert(self.f)
        self.f.fail_next("update", when=lambda pid, props: props.get("Stage") == n.select("Sending"))
        fake = FakePushover()
        self.run_queue(fake)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.stage(a), "Push pending")

    def test_record_write_fails_row_stays_sending(self):
        a = alert(self.f)
        self.f.fail_next("update", when=lambda pid, props: props.get("Stage") == n.select("Pushed"))
        self.run_queue(FakePushover())
        self.assertEqual(self.stage(a), "Sending")

    def test_oldest_first(self):
        late = alert(self.f, why="late", found=NOW)
        early = alert(self.f, why="early", found=NOW - timedelta(hours=1))
        fake = FakePushover()
        self.run_queue(fake)
        self.assertEqual(fake.messages(), ["early", "late"])


class CleanSending(unittest.TestCase):
    def test_stale_sending(self):
        clock = FakeClock(NOW - timedelta(minutes=11))
        f = FakeNotion(clock)
        old = alert(f, stage="Sending")
        old_system = alert(f, stage="Sending", match="System")
        clock.advance(minutes=6)
        recent = alert(f, stage="Sending")
        clock.advance(minutes=5)
        self.assertEqual(push.clean_sending(f, S, clock), 1)
        self.assertEqual(f.value(old, "Stage"), "Push unknown")
        self.assertEqual(f.value(old, "Pushed At"), NOW)
        self.assertEqual(f.value(old_system, "Stage"), "Push pending")
        self.assertEqual(f.value(recent, "Stage"), "Sending")
