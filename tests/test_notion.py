import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fakes import FakeClock, ScriptedTransport, json_response
from radar import notion
from radar.http import NoConnection, Response, Timeout

FIX = Path(__file__).resolve().parent / "fixtures" / "notion"
TOKEN = "fake-notion-token-abc"
DS = "00000000-0000-4000-8000-000000000041"
START = datetime(2026, 11, 8, 15, 3, tzinfo=timezone.utc)


def fixture(name):
    return Response(200, {}, (FIX / name).read_bytes())


def client(*script):
    clock = FakeClock(START)
    t = ScriptedTransport(*script, clock=clock)
    return notion.NotionClient(TOKEN, t, clock), t, clock


EMPTY = json_response(200, {"results": [], "has_more": False, "next_cursor": None})


class Query(unittest.TestCase):
    def test_follows_has_more(self):
        c, t, _ = client(fixture("query_page1.json"), fixture("query_page2.json"))
        pages = c.query(DS, {"property": "Enabled", "checkbox": {"equals": True}})
        self.assertEqual([p.id[-2:] for p in pages], ["31", "32"])
        self.assertEqual(len(t.calls), 2)
        first, second = (json.loads(call.body) for call in t.calls)
        self.assertEqual(t.calls[0].url, f"https://api.notion.com/v1/data_sources/{DS}/query")
        self.assertEqual(first["page_size"], 100)
        self.assertNotIn("start_cursor", first)
        self.assertEqual(second["start_cursor"], "00000000-0000-4000-8000-000000000099")
        self.assertEqual(second["filter"], first["filter"])
        self.assertEqual(pages[0].last_edited, datetime(2026, 11, 8, 14, 0, tzinfo=timezone.utc))
        self.assertEqual(c.calls, 2)

    def test_headers(self):
        c, t, _ = client(EMPTY, json_response(200, {"id": "p"}), json_response(200, {}))
        c.query(DS)
        c.create(DS, {"Name": notion.title("x")})
        c.update("p", {"Enabled": notion.checkbox(True)})
        for call in t.calls:
            self.assertEqual(call.headers["Notion-Version"], "2026-03-11")
            self.assertEqual(call.headers["Authorization"], f"Bearer {TOKEN}")
            self.assertEqual(call.headers["Content-Type"], "application/json")

    def test_create_and_update_shapes(self):
        c, t, _ = client(json_response(200, {"id": "new-page"}), json_response(200, {}))
        self.assertEqual(c.create(DS, {"Name": notion.title("x")}), "new-page")
        c.update("new-page", {"Enabled": notion.checkbox(False)})
        create, update = t.calls
        self.assertEqual((create.method, create.url), ("POST", "https://api.notion.com/v1/pages"))
        self.assertEqual(json.loads(create.body)["parent"], {"type": "data_source_id", "data_source_id": DS})
        self.assertEqual((update.method, update.url), ("PATCH", "https://api.notion.com/v1/pages/new-page"))
        self.assertEqual(json.loads(update.body), {"properties": {"Enabled": {"checkbox": False}}})


class Retry(unittest.TestCase):
    def test_503_then_200_waits_two_seconds(self):
        c, t, clock = client(Response(503), fixture("query_page2.json"))
        self.assertEqual(len(c.query(DS)), 1)
        self.assertEqual(clock.sleeps, [2])

    def test_two_503s_raise(self):
        c, _, clock = client(Response(503), Response(503))
        with self.assertRaises(notion.NotionUnavailable):
            c.query(DS)
        self.assertEqual(clock.sleeps, [2])

    def test_timeout_and_no_connection_retry(self):
        for err in (Timeout(), NoConnection()):
            c, t, _ = client(err, EMPTY)
            self.assertEqual(c.query(DS), [])
            self.assertEqual(len(t.calls), 2)

    def test_429_waits_retry_after(self):
        c, t, clock = client(Response(429, {"Retry-After": "5"}), EMPTY)
        c.query(DS)
        self.assertEqual(clock.sleeps, [5])
        self.assertEqual(len(t.calls), 2)

    def test_429_wait_is_at_most_60(self):
        c, _, clock = client(Response(429, {"Retry-After": "600"}), EMPTY)
        c.query(DS)
        self.assertEqual(clock.sleeps, [60])

    def test_400_raises_at_once(self):
        c, t, clock = client(Response(400, {}, b'{"message":"bad"}'))
        with self.assertRaises(notion.NotionUnavailable) as cm:
            c.query(DS)
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(str(cm.exception), "notion 400")

    def test_error_words_hold_no_url_id_or_token(self):
        for script in ((Response(400),), (Response(503), Response(500)), (Timeout(), NoConnection())):
            c, _, _ = client(*script)
            with self.assertRaises(notion.NotionUnavailable) as cm:
                c.query(DS)
            msg = str(cm.exception)
            for bad in ("notion.com", DS, TOKEN, "http"):
                self.assertNotIn(bad, msg)


class Spacing(unittest.TestCase):
    def test_calls_are_at_least_0_4_seconds_apart(self):
        c, t, _ = client(EMPTY, EMPTY, EMPTY)
        c.query(DS); c.query(DS); c.query(DS)
        gaps = [(b.at - a.at).total_seconds() for a, b in zip(t.calls, t.calls[1:])]
        self.assertTrue(all(g >= 0.4 for g in gaps), gaps)


class Properties(unittest.TestCase):
    def test_long_text_is_split_and_joined(self):
        prop = notion.text("x" * 4500)
        self.assertEqual([len(p["text"]["content"]) for p in prop["rich_text"]], [2000, 2000, 500])
        self.assertEqual(notion.read_text(prop), "x" * 4500)

    def test_readers_on_recorded_page(self):
        page = json.loads((FIX / "query_page1.json").read_text())["results"][0]["properties"]
        self.assertEqual(notion.read_title(page["Name"]), "Example Vendor #announcements")
        self.assertEqual(notion.read_select(page["Kind"]), "Discord channel")
        self.assertEqual(notion.read_text(page["Address"]), "9000000000000000501")
        self.assertEqual(notion.read_text(page["Cursor"]), "")
        self.assertTrue(notion.read_checkbox(page["Enabled"]))
        self.assertEqual(notion.read_number(page["Error Count"]), 2)
        self.assertEqual(notion.read_date(page["Last OK"]),
                         datetime(2026, 11, 8, 8, 3, tzinfo=ZoneInfo("America/Chicago")))
        self.assertEqual(notion.read_relation(page["Set"]), ["00000000-0000-4000-8000-000000000001"])

    def test_empty_values_and_missing_properties(self):
        page = json.loads((FIX / "query_page2.json").read_text())["results"][0]["properties"]
        self.assertIsNone(notion.read_select(page["Kind"]))
        self.assertIsNone(notion.read_number(page["Error Count"]))
        self.assertIsNone(notion.read_date(page["Last OK"]))
        self.assertEqual(notion.read_text(None), "")
        self.assertEqual(notion.read_relation(None), [])

    def test_writers_round_trip(self):
        dt = datetime(2026, 11, 8, 9, 3, 12, 500, tzinfo=ZoneInfo("America/Chicago"))
        self.assertEqual(notion.date(dt), {"date": {"start": "2026-11-08T09:03:12-06:00"}})
        self.assertEqual(notion.read_date(notion.date(dt)), dt.replace(microsecond=0))
        self.assertEqual(notion.date(None), {"date": None})
        self.assertEqual(notion.read_title(notion.title("Hi")), "Hi")
        self.assertEqual(notion.read_select(notion.select("Pushed")), "Pushed")
        self.assertEqual(notion.select(None), {"select": None})
        self.assertEqual(notion.read_number(notion.number(3)), 3)
        self.assertEqual(notion.url(""), {"url": None})
        self.assertEqual(notion.read_relation(notion.relation(["a", "b"])), ["a", "b"])
        self.assertEqual(notion.text(""), {"rich_text": []})
