import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fakes import FakeClock, FakeDiscord, discord_msg, json_response
from radar import discord
from radar.http import NoConnection, Response, Timeout
from radar.log_rows import Source

FIX = Path(__file__).resolve().parent / "fixtures" / "discord"
CH = "9000000000000000501"
GUILD = "9000000000000000001"
TOKEN = "fake-discord-token"


def load(name):
    return json.loads((FIX / name).read_text())


def source(cursor="9000000000000000050"):
    return Source("00000000-0000-4000-8000-000000000031", "Vendor news", "Discord channel", CH, cursor=cursor)


class ReadSource(unittest.TestCase):
    def read(self, fake, cursor="9000000000000000050"):
        self.clock = FakeClock(datetime(2026, 11, 8, 15, 3, tzinfo=timezone.utc))
        return discord.read_source(source(cursor), TOKEN, fake, self.clock)

    def test_first_run(self):
        fake = FakeDiscord({CH: [discord_msg(9000000000000000060, content="a"),
                                 discord_msg(9000000000000000061, type=12)]})
        r = self.read(fake, cursor="")
        self.assertEqual((r.kind, r.newest_id, r.messages), ("first_run", "9000000000000000061", []))
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("limit=1", fake.calls[0])
        self.assertNotIn("after", fake.calls[0])

    def test_first_run_empty_channel(self):
        r = self.read(FakeDiscord({CH: []}), cursor="")
        self.assertEqual((r.kind, r.newest_id), ("first_run", None))

    def test_headers_and_url(self):
        fake = FakeDiscord({CH: []})
        self.read(fake)
        self.assertEqual(fake.calls[0],
                         f"https://discord.com/api/v10/channels/{CH}/messages?after=9000000000000000050&limit=100")
        self.assertEqual(fake.last_headers["Authorization"], f"Bot {TOKEN}")
        self.assertEqual(fake.last_headers["User-Agent"],
                         "DiscordBot (https://github.com/kkharrigan-ship-it/radar, 1.0)")

    def test_follow_notice_is_skipped(self):
        r = self.read(FakeDiscord({CH: load("tc1_reply.json")}))
        self.assertEqual(r.kind, "ok")
        self.assertEqual([m.id for m in r.messages], ["9000000000000000101"])
        self.assertEqual(r.newest_id, "9000000000000000101")

    def test_only_follow_notices(self):
        fake = FakeDiscord({CH: [discord_msg(9000000000000000060, type=12),
                                 discord_msg(9000000000000000061, type=12)]})
        r = self.read(fake)
        self.assertEqual((r.kind, r.messages, r.newest_id, r.error), ("ok", [], "9000000000000000061", ""))

    def test_skipped_type_can_be_newest(self):
        fake = FakeDiscord({CH: [discord_msg(9000000000000000060, content="hi"),
                                 discord_msg(9000000000000000061, type=12)]})
        r = self.read(fake, cursor="9000000000000000050")
        self.assertEqual(r.newest_id, "9000000000000000061")

    def test_no_text(self):
        fake = FakeDiscord({CH: [discord_msg(9000000000000000060 + i) for i in range(4)]})
        r = self.read(fake)
        self.assertEqual(r.kind, "no_text")
        self.assertEqual(r.error, "Messages have no text: check the bot's Message Content setting")
        self.assertEqual(r.messages, [])

    def test_reply_type_19_is_kept(self):
        r = self.read(FakeDiscord({CH: [discord_msg(9000000000000000060, type=19, content="re: testbed")]}, {CH: GUILD}))
        self.assertEqual([m.type for m in r.messages], [19])

    def test_embed_text(self):
        r = self.read(FakeDiscord({CH: load("embeds.json")}, {CH: GUILD}))
        self.assertEqual(r.messages[0].text, "Fauxmaker drop\nCosmo Test v2 sculpts tonight\n$60\nExample Vendor")

    def test_message_text_content_first(self):
        raw = discord_msg(1, content="Hello", embeds=[{"title": "T", "description": ""}])
        self.assertEqual(discord.message_text(raw), "Hello\nT")

    def test_oldest_first(self):
        fake = FakeDiscord({CH: [discord_msg(9000000000000000060 + i, content=str(i)) for i in range(5)]}, {CH: GUILD})
        r = self.read(fake)
        self.assertEqual([m.text for m in r.messages], ["0", "1", "2", "3", "4"])

    def test_sorted_by_int_not_string(self):
        fake = FakeDiscord()
        fake.fail_next(json_response(200, [discord_msg(900000000000000010, content="b"),
                                           discord_msg(90000000000000009, content="a")]),
                       json_response(200, {"id": CH, "guild_id": GUILD}))
        r = self.read(fake, cursor="1")
        self.assertEqual([m.text for m in r.messages], ["a", "b"])
        self.assertEqual(r.newest_id, "900000000000000010")

    def test_100_then_3(self):
        fake = FakeDiscord({CH: [discord_msg(9000000000000000300 + i, content="x") for i in range(103)]}, {CH: GUILD})
        r = self.read(fake)
        message_calls = [u for u in fake.calls if "/messages" in u]
        self.assertEqual(len(message_calls), 2)
        self.assertIn("after=9000000000000000399", message_calls[1])
        self.assertEqual(len(r.messages), 103)
        self.assertEqual(r.newest_id, "9000000000000000402")

    def test_at_most_five_pages(self):
        fake = FakeDiscord({CH: [discord_msg(9000000000000000000 + i, content="x") for i in range(700)]}, {CH: GUILD})
        r = self.read(fake, cursor="1")
        self.assertEqual(len([u for u in fake.calls if "/messages" in u]), 5)
        self.assertEqual(len(r.messages), 500)
        self.assertEqual(r.newest_id, "9000000000000000499")

    def test_rate_limit_short_wait(self):
        fake = FakeDiscord({CH: [discord_msg(9000000000000000060, content="hi")]}, {CH: GUILD})
        fake.fail_next(json_response(429, {"retry_after": 2.5, "global": False}))
        r = self.read(fake)
        self.assertEqual(r.kind, "ok")
        self.assertEqual(self.clock.sleeps, [2.5])

    def test_rate_limit_long_wait(self):
        fake = FakeDiscord({CH: []})
        fake.fail_next(json_response(429, {"retry_after": 30}))
        r = self.read(fake)
        self.assertEqual((r.kind, r.error), ("error", "Discord rate limit"))
        self.assertEqual(self.clock.sleeps, [])

    def test_rate_limit_twice(self):
        fake = FakeDiscord({CH: []})
        fake.fail_next(json_response(429, {"retry_after": 1}), json_response(429, {"retry_after": 1}))
        self.assertEqual(self.read(fake).error, "Discord rate limit")

    def test_errors(self):
        cases = [(Response(403), "Bot can't read this channel"), (Response(401), "Bot can't read this channel"),
                 (Response(500), "Discord error 500"), (Timeout(), "Discord unreachable"),
                 (NoConnection(), "Discord unreachable")]
        for item, words in cases:
            with self.subTest(words=words):
                fake = FakeDiscord({CH: []})
                fake.fail_next(item)
                r = self.read(fake)
                self.assertEqual((r.kind, r.error, r.messages), ("error", words, []))

    def test_crosspost_links_to_original(self):
        r = self.read(FakeDiscord({CH: load("tc1_reply.json")}))
        m = r.messages[0]
        self.assertEqual(m.link, "https://discord.com/channels/9000000000000000801/9000000000000000802/9000000000000000803")
        self.assertEqual(m.source_name, "Example Vendor #announcements")

    def test_plain_post_links_through_channel_guild(self):
        fake = FakeDiscord({CH: [discord_msg(9000000000000000060, content="a"),
                                 discord_msg(9000000000000000061, content="b")]}, {CH: GUILD})
        r = self.read(fake)
        self.assertEqual(r.messages[0].link, f"https://discord.com/channels/{GUILD}/{CH}/9000000000000000060")
        self.assertEqual(r.messages[0].source_name, "Vendor news")
        self.assertEqual(len([u for u in fake.calls if u.endswith(f"/channels/{CH}")]), 1)

    def test_link_empty_when_guild_unknown(self):
        fake = FakeDiscord({CH: [discord_msg(9000000000000000060, content="a")]})
        fake.fail_next(json_response(200, [discord_msg(9000000000000000060, content="a")]), Response(403))
        r = self.read(fake)
        self.assertEqual((r.kind, r.messages[0].link), ("ok", ""))
