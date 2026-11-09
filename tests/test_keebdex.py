import re
import unittest
from datetime import date
from pathlib import Path

from fakes import FakeKeebdex
from radar import keebdex
from radar.http import NoConnection, Response, Timeout
from radar.log_rows import Source
from radar.match import KitRow, SetRow, build_wanted

FIX = Path(__file__).resolve().parent / "fixtures" / "keebdex"
HOME = (FIX / "home.html").read_text(encoding="utf-8")
LIVE = (FIX / "live.html").read_text(encoding="utf-8")
TODAY = date(2026, 11, 8)
LIVE_URL = "https://keebdex.org/keyset/group-buy?status=live"
EXPECTED = [
    ("GMK Testbed Nº3", "https://keebdex.org/keyset/gmk/testbed-no3", date(2026, 11, 9), 1),
    ("GMK Fauxwave", "https://keebdex.org/keyset/gmk/fauxwave", date(2026, 11, 10), 2),
    ("GMK Placeholder", "https://keebdex.org/keyset/gmk/placeholder", date(2026, 11, 16), 8),
    ("MT3 Sample Dusk", "https://keebdex.org/keyset/mt3/sample-dusk", date(2026, 12, 6), 28),
]
WANTED = build_wanted(
    [SetRow("00000000-0000-4000-8000-000000000001", "GMK Testbed Nº3", "Fauxmaker", "testbed n3; testbed no3; testbed 3")],
    [KitRow("00000000-0000-4000-8000-000000000011", "Light Base", ["00000000-0000-4000-8000-000000000001"])],
)


def as_tuples(gbs):
    return [(g.name, g.url, g.ends, g.days_left) for g in gbs]


def source(url=LIVE_URL):
    return Source("00000000-0000-4000-8000-000000000032", "Keebdex live", "Keebdex page", url)


class Parse(unittest.TestCase):
    def test_home_page(self):
        self.assertEqual(as_tuples(keebdex.parse(HOME, TODAY)), EXPECTED)

    def test_live_page(self):
        self.assertEqual(as_tuples(keebdex.parse(LIVE, TODAY)), EXPECTED)

    def test_passed_end_date_is_dropped(self):
        names = [g.name for g in keebdex.parse(LIVE, date(2026, 11, 10))]
        self.assertEqual(names, ["GMK Fauxwave", "GMK Placeholder", "MT3 Sample Dusk"])

    def test_layout_changed(self):
        no_script = re.sub(r'<script[^>]*id="__NUXT_DATA__".*?</script>', "", LIVE, flags=re.S)
        broken = LIVE.replace('id="__NUXT_DATA__">[', 'id="__NUXT_DATA__">[{oops', 1)
        no_gbs = LIVE.replace('"profile_keyset_id"', '"renamed_field"')
        for page in (no_script, broken, no_gbs, "<html></html>"):
            with self.assertRaises(keebdex.LayoutChanged):
                keebdex.parse(page, TODAY)


class Fetch(unittest.TestCase):
    def test_ok(self):
        fake = FakeKeebdex({LIVE_URL: LIVE})
        r = keebdex.fetch(source(), fake, TODAY)
        self.assertEqual((r.kind, r.error), ("ok", ""))
        self.assertEqual(len(r.group_buys), 4)
        url, headers, timeout = fake.calls[0]
        self.assertEqual(url, LIVE_URL)
        self.assertEqual(headers["User-Agent"], "Radar/1.0 (+https://github.com/kkharrigan-ship-it/radar)")
        self.assertEqual(timeout, 15)

    def test_errors(self):
        broken = LIVE.replace('id="__NUXT_DATA__">[', 'id="__NUXT_DATA__">[{oops', 1)
        cases = [(Response(503), "Keebdex error 503"), (Timeout(), "Keebdex unreachable"),
                 (NoConnection(), "Keebdex unreachable"),
                 (Response(200, {}, broken.encode()), "Keebdex layout changed")]
        for item, words in cases:
            with self.subTest(words=words):
                fake = FakeKeebdex()
                fake.fail_next(item)
                r = keebdex.fetch(source(), fake, TODAY)
                self.assertEqual((r.kind, r.error, r.group_buys), ("error", words, []))


class MatchGroupBuys(unittest.TestCase):
    def gb(self, name, days):
        return keebdex.GroupBuy(name, "https://keebdex.org/keyset/gmk/testbed-no3",
                                date.fromordinal(TODAY.toordinal() + days), days)

    def test_live_and_closing_keys(self):
        live, = keebdex.match_group_buys([self.gb("GMK Testbed Nº3", 5)], WANTED)
        self.assertEqual((live.key, live.closing, live.set.name), ("keebdex:gmk testbed no3:live", False, "GMK Testbed Nº3"))
        closing, = keebdex.match_group_buys([self.gb("GMK Testbed Nº3", 2)], WANTED)
        self.assertEqual((closing.key, closing.closing), ("keebdex:gmk testbed no3:closing", True))
        edge, = keebdex.match_group_buys([self.gb("GMK Testbed Nº3", 3)], WANTED)
        self.assertTrue(edge.closing)

    def test_no_match(self):
        self.assertEqual(keebdex.match_group_buys([self.gb("GMK Fauxwave", 5)], WANTED), [])

    def test_saved_page_matches_testbed_only(self):
        matches = keebdex.match_group_buys(keebdex.parse(HOME, TODAY), WANTED)
        self.assertEqual([m.key for m in matches], ["keebdex:gmk testbed no3:closing"])
