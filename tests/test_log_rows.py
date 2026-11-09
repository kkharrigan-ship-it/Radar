import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fakes import FakeClock, FakeNotion, ScriptedTransport, json_response
from radar import log_rows, notion as n
from radar.discord import Message
from radar.http import Response
from radar.keebdex import GroupBuy, GroupBuyMatch
from radar.log_rows import CreateFailed, Source, ensure_row
from radar.match import KitRow, SetRow, build_wanted, match_message

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 8, 9, 3, 12, tzinfo=TZ)
TESTBED = SetRow("00000000-0000-4000-8000-000000000001", "GMK Testbed Nº3", "Fauxmaker",
                 "testbed n3; testbed no3; testbed 3")
OTHER = SetRow("00000000-0000-4000-8000-000000000002", "GMK Elsewhere", "", "elsewhere")
LIGHT = KitRow("00000000-0000-4000-8000-000000000011", "Light Base", [TESTBED.page_id])
OTHER_KIT = KitRow("00000000-0000-4000-8000-000000000021", "Light Base", [OTHER.page_id])
TC1 = Message("9000000000000000101", 0, "GMK Testbed Nº3 restock: Light Base, Novelties, now live",
              "https://discord.com/channels/9000000000000000001/9000000000000000002/9000000000000000101",
              "Example Vendor #announcements")
WANTED = build_wanted([TESTBED, OTHER], [LIGHT, OTHER_KIT])


def props_text(props, name):
    return n.read_text(props[name]) if "rich_text" in props[name] else n.read_title(props[name])


class EnsureRow(unittest.TestCase):
    def setUp(self):
        self.f = FakeNotion(FakeClock(NOW))

    def test_new_key_is_queried_then_created(self):
        self.assertEqual(ensure_row(self.f, "log", {"Alert": n.title("x")}, "k1"), "created")
        self.assertEqual([e[0] for e in self.f.log], ["query", "create"])
        self.assertEqual(self.f.log[0][2], {"property": "Dedupe Key", "rich_text": {"equals": "k1"}})
        self.assertEqual(self.f.value(self.f.find("log", "Dedupe Key", "k1")[0], "Alert"), "x")

    def test_existing_key_is_skipped(self):
        self.f.add("log", {"Dedupe Key": n.text("k1")})
        self.assertEqual(ensure_row(self.f, "log", {}, "k1"), "exists")
        self.assertEqual([e[0] for e in self.f.log], ["query"])

    def test_create_failing_twice_raises(self):
        clock = FakeClock(NOW)
        t = ScriptedTransport(json_response(200, {"results": [], "has_more": False}),
                              Response(503), Response(503), clock=clock)
        with self.assertRaises(CreateFailed):
            ensure_row(n.NotionClient("tok", t, clock), "log", {}, "k1")
        self.assertEqual(len(t.calls), 3)


class WantedRow(unittest.TestCase):
    def test_tc1_row(self):
        m = match_message(TC1.text, WANTED, [])
        key, props = log_rows.wanted_row(TC1, m.wanted[0], NOW)
        self.assertEqual(key, "discord:9000000000000000101:gmk testbed no3")
        self.assertEqual(n.read_select(props["Stage"]), "Push pending")
        self.assertEqual(n.read_select(props["Match Type"]), "Wanted")
        self.assertEqual(n.read_select(props["Confidence"]), "High")
        self.assertEqual(n.read_select(props["Source"]), "Discord")
        self.assertEqual(props_text(props, "Item"), "Light Base of GMK Testbed Nº3")
        self.assertEqual(props_text(props, "Why"),
                         "Light Base of GMK Testbed Nº3 mentioned in Example Vendor #announcements")
        self.assertEqual(props_text(props, "Alert"),
                         "Wanted: Light Base of GMK Testbed Nº3 · Example Vendor #announcements")
        self.assertEqual(props_text(props, "Dedupe Key"), key)
        self.assertEqual(n.read_relation(props["Set"]), [TESTBED.page_id])
        self.assertEqual(n.read_relation(props["Kit"]), [LIGHT.page_id])
        self.assertEqual(n.read_url(props["Link"]), TC1.link)
        self.assertEqual(n.read_date(props["Found At"]), NOW)
        self.assertEqual(n.read_number(props["Push Attempts"]), 0)
        self.assertEqual(n.read_number(props["Judge Attempts"]), 0)

    def test_several_kits_one_row(self):
        two = build_wanted([TESTBED], [LIGHT, KitRow("00000000-0000-4000-8000-000000000012", "Novelties", [TESTBED.page_id])])
        m = match_message(TC1.text, two, [])
        _, props = log_rows.wanted_row(TC1, m.wanted[0], NOW)
        self.assertEqual(props_text(props, "Item"), "Light Base, Novelties of GMK Testbed Nº3")
        self.assertEqual(len(n.read_relation(props["Kit"])), 2)

    def test_two_sets_two_keys_one_candidate_key(self):
        msg = Message("9000000000000000102", 0, "testbed n3 and elsewhere light base", "", "Example Vendor")
        m = match_message(msg.text, WANTED, [])
        keys = {log_rows.wanted_row(msg, w, NOW)[0] for w in m.wanted}
        self.assertEqual(keys, {"discord:9000000000000000102:gmk testbed no3",
                                "discord:9000000000000000102:gmk elsewhere"})
        self.assertEqual(log_rows.candidate_row(msg, NOW)[0], "discord:9000000000000000102:candidate")

    def test_limits(self):
        long = Message("9000000000000000103", 0, "testbed n3 " + "y" * 3000, "", "Z" * 200)
        m = match_message(long.text, WANTED, [])
        for _, props in (log_rows.wanted_row(long, m.wanted[0], NOW), log_rows.candidate_row(long, NOW)):
            self.assertLessEqual(len(props_text(props, "Alert")), 120)
            self.assertLessEqual(len(props_text(props, "Post Text")), 1500)


class CandidateRow(unittest.TestCase):
    def test_contents(self):
        key, props = log_rows.candidate_row(TC1, NOW)
        self.assertEqual(n.read_select(props["Stage"]), "To judge")
        self.assertIsNone(n.read_select(props.get("Match Type")))
        self.assertEqual(props_text(props, "Alert"), "To judge: " + TC1.text[:60] + " · Example Vendor #announcements")
        self.assertNotIn("Item", props)
        self.assertNotIn("Why", props)


class GroupBuyRow(unittest.TestCase):
    def gb_match(self, days):
        gb = GroupBuy("GMK Testbed Nº3", "https://keebdex.org/keyset/gmk/testbed-no3",
                      date(2026, 11, 8 + days), days)
        key = f"keebdex:gmk testbed no3:{'closing' if days <= 3 else 'live'}"
        return GroupBuyMatch(WANTED.sets[0], gb, key, days <= 3)

    def test_live_and_closing(self):
        src = Source("00000000-0000-4000-8000-000000000032", "Keebdex live", "Keebdex page",
                     "https://keebdex.org/keyset/group-buy?status=live")
        key, props = log_rows.group_buy_row(src, self.gb_match(5), NOW)
        self.assertEqual(key, "keebdex:gmk testbed no3:live")
        self.assertEqual(props_text(props, "Why"), "Group buy for GMK Testbed Nº3 is live on Keebdex")
        self.assertEqual(n.read_select(props["Match Type"]), "Group buy")
        self.assertEqual(n.read_select(props["Source"]), "Keebdex")
        self.assertEqual(props_text(props, "Item"), "GMK Testbed Nº3")
        self.assertEqual(n.read_url(props["Link"]), "https://keebdex.org/keyset/gmk/testbed-no3")
        self.assertEqual(n.read_relation(props["Set"]), [TESTBED.page_id])
        _, props = log_rows.group_buy_row(src, self.gb_match(2), NOW)
        self.assertEqual(props_text(props, "Why"), "Group buy for GMK Testbed Nº3 closes in 2 days")


class SourceUpdates(unittest.TestCase):
    def setUp(self):
        self.f = FakeNotion(FakeClock(NOW))
        pid = self.f.add("sources", {"Name": n.title("Example Vendor"), "Cursor": n.text("9000000000000000100"),
                                     "Error Count": n.number(2), "Last Error": n.text("old words")})
        self.src = Source(pid, "Example Vendor", "Discord channel", "9000000000000000501",
                          cursor="9000000000000000100", error_count=2, last_error="old words")

    def test_error_raises_count_and_keeps_cursor(self):
        log_rows.source_error(self.f, self.src, "Discord unreachable")
        self.assertEqual(self.f.value(self.src.page_id, "Error Count"), 3)
        self.assertEqual(self.f.value(self.src.page_id, "Last Error"), "Discord unreachable")
        self.assertEqual(self.f.value(self.src.page_id, "Cursor"), "9000000000000000100")
        self.assertEqual(self.src.error_count, 3)

    def test_ok_resets_count_moves_cursor_keeps_last_error(self):
        log_rows.source_ok(self.f, self.src, NOW, "9000000000000000109")
        self.assertEqual(self.f.value(self.src.page_id, "Error Count"), 0)
        self.assertEqual(self.f.value(self.src.page_id, "Cursor"), "9000000000000000109")
        self.assertEqual(self.f.value(self.src.page_id, "Last OK"), NOW)
        self.assertEqual(self.f.value(self.src.page_id, "Last Error"), "old words")

    def test_ok_without_cursor_leaves_it(self):
        log_rows.source_ok(self.f, self.src, NOW)
        self.assertEqual(self.f.value(self.src.page_id, "Cursor"), "9000000000000000100")

    def test_from_page(self):
        page = self.f.rows_in("sources")[0]
        self.f.update(page.id, {"Kind": n.select("Discord channel"), "Address": n.text("9000000000000000501"),
                                "Enabled": n.checkbox(True)})
        src = Source.from_page(self.f.rows_in("sources")[0])
        self.assertEqual((src.name, src.kind, src.address, src.cursor, src.error_count, src.enabled),
                         ("Example Vendor", "Discord channel", "9000000000000000501", "9000000000000000100", 2, True))
