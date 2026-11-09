"""Radar's fast lane: `python -m radar.fast_lane`.

Wires steps F1 to F13 together. Holds no logic of its own beyond order and error routing.
Prints plain error words as they happen and one summary line at the end; never a URL, a
property value, a post, a term, a set name, an exception message or a traceback.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TextIO

from . import clock as clk
from . import config as c
from . import discord, keebdex, log_rows, push, system
from . import notion as n
from .http import UrllibTransport
from .judgement_check import check, parse
from .log_rows import CreateFailed, Source
from .match import HoldingRow, KitRow, SetRow, Wanted, build_taste_makers, build_wanted, match_message
from .notion import NotionClient, NotionUnavailable, Page

NOTION_DOWN_WORDS = "Radar can't reach Notion"
MAX_JUDGE_ATTEMPTS = 3


@dataclass
class Tally:
    sources_ok: int = 0
    sources_failed: int = 0
    rows_new: int = 0
    judged: int = 0
    pushed: int = 0
    held: int = 0
    failed: int = 0
    unknown: int = 0
    system: int = 0

    def line(self, notion_calls: int) -> str:
        return (f"sources ok {self.sources_ok} failed {self.sources_failed} | rows new {self.rows_new} | "
                f"judged {self.judged} | pushed {self.pushed} held {self.held} failed {self.failed} "
                f"unknown {self.unknown} | system {self.system} | notion calls {notion_calls}")


class Run:
    def __init__(self, settings, store, transport, clock, out: TextIO):
        self.s = settings
        self.store = store
        self.transport = transport
        self.clock = clk.RunClock(clock)
        self.out = out
        self.tally = Tally()
        self.alert_problem = False
        self.judging_problem = False
        self.calls_at_start = getattr(store, "calls", 0)

    def say(self, words: str) -> None:
        print(words, file=self.out)

    def now(self):
        return clk.now_local(self.clock, self.s.tz)

    # -- F2 ------------------------------------------------------------------

    def load(self) -> tuple[list[Source], Wanted, list[str]]:
        s, st = self.s, self.store
        sources = [Source.from_page(p) for p in st.query(
            s.sources_ds, {"property": c.SRC_ENABLED, "checkbox": {"equals": True}})]
        kits = [KitRow(p.id, n.read_title(p.props.get(c.KITS_NAME)), n.read_relation(p.props.get(c.KITS_SET)))
                for p in st.query(s.kits_ds, {"property": c.KITS_STATUS, "select": {"equals": c.STATUS_WANTED}})]
        sets = [SetRow(p.id, n.read_title(p.props.get(c.SETS_NAME)), n.read_text(p.props.get(c.SETS_MAKER)),
                       n.read_text(p.props.get(c.SETS_RADAR_TERMS))) for p in st.query(s.sets_ds)]
        holdings = [HoldingRow(n.read_text(p.props.get(c.COLLECTION_MAKER))) for p in st.query(
            s.collection_ds, {"or": [
                {"property": c.COLLECTION_HOLDING, "select": {"equals": c.HOLDING_OWNED}},
                {"property": c.COLLECTION_HOLDING, "select": {"equals": c.HOLDING_ON_ORDER}}]})]
        wanted = build_wanted(sets, kits)
        if wanted.sets_without_terms:
            self.say(f"sets without terms {wanted.sets_without_terms}")
        return sources, wanted, build_taste_makers(sets, holdings)

    def notion_down(self) -> int:
        self.say("notion unavailable")
        if clk.local(self.clock.start, self.s.tz).minute < 5:
            push.send_pushover(self.transport, self.s.pushover_app, self.s.pushover_user,
                               f"Radar: {c.MATCH_SYSTEM}", NOTION_DOWN_WORDS, "")
        return 1

    # -- F3 to F8 ------------------------------------------------------------

    def source_failed(self, src: Source, words: str) -> None:
        self.tally.sources_failed += 1
        self.say(words.lower())
        try:
            log_rows.source_error(self.store, src, words)
        except NotionUnavailable:
            self.say("notion unavailable")

    def source_ok(self, src: Source, cursor: str | None = None) -> None:
        self.tally.sources_ok += 1
        try:
            log_rows.source_ok(self.store, src, self.now(), cursor)
        except NotionUnavailable:
            self.say("notion unavailable")

    def save(self, key_props: tuple[str, dict]) -> None:
        key, props = key_props
        if log_rows.ensure_row(self.store, self.s.log_ds, props, key) == "created":
            self.tally.rows_new += 1

    def discord_source(self, src: Source, wanted: Wanted, makers: list[str]) -> None:
        r = discord.read_source(src, self.s.discord_token, self.transport, self.clock)
        if r.kind in ("error", "no_text"):
            return self.source_failed(src, r.error)
        if r.kind == "first_run":
            return self.source_ok(src, r.newest_id)
        try:
            for msg in r.messages:
                m = match_message(msg.text, wanted, makers)
                for wm in m.wanted:
                    self.save(log_rows.wanted_row(msg, wm, self.now()))
                if m.candidate:
                    self.save(log_rows.candidate_row(msg, self.now()))
        except CreateFailed as e:
            return self.source_failed(src, str(e))
        self.source_ok(src, r.newest_id)

    def keebdex_source(self, src: Source, wanted: Wanted) -> None:
        r = keebdex.fetch(src, self.transport, clk.today(self.clock, self.s.tz))
        if r.kind == "error":
            return self.source_failed(src, r.error)
        try:
            for m in keebdex.match_group_buys(r.group_buys, wanted):
                self.save(log_rows.group_buy_row(src, m, self.now()))
        except CreateFailed as e:
            return self.source_failed(src, str(e))
        self.source_ok(src)

    # -- F9 ------------------------------------------------------------------

    def judge(self, wanted: Wanted) -> None:
        rows = self.store.query(self.s.log_ds, {"property": c.LOG_STAGE, "select": {"equals": c.STAGE_JUDGED}})
        check_map = wanted.as_check_map()
        for row in rows:
            self.tally.judged += 1
            text = n.read_text(row.props.get(c.LOG_JUDGEMENT))
            result, stage, error = check(text, check_map)
            if result == "pass":
                props = self.mapped(row, parse(text), wanted, stage)
            elif result == "reject":
                props = {c.LOG_STAGE: n.select(stage), c.LOG_JUDGE_ERROR: n.text(error),
                         c.LOG_SUSPICIOUS: n.checkbox(parse(text)["suspicious_text"])}
            else:
                attempts = int(n.read_number(row.props.get(c.LOG_JUDGE_ATTEMPTS)) or 0) + 1
                failed = attempts >= MAX_JUDGE_ATTEMPTS
                props = {c.LOG_JUDGE_ATTEMPTS: n.number(attempts), c.LOG_JUDGE_ERROR: n.text(error),
                         c.LOG_STAGE: n.select(c.STAGE_JUDGE_FAILED if failed else c.STAGE_TO_JUDGE)}
            try:
                self.store.update(row.id, props)
            except NotionUnavailable:
                self.say("notion unavailable")
                continue
            if result == "fail" and n.read_select(props[c.LOG_STAGE]) == c.STAGE_JUDGE_FAILED:
                self.judging_problem = True

    @staticmethod
    def mapped(row: Page, d: dict, wanted: Wanted, stage: str) -> dict:
        match_type = c.MATCH_TYPES[d["match_type"]]
        set_id = wanted.set_ids_by_name.get(d["set_name"])
        kit_ids = [k.page_id for s in wanted.sets if s.name == d["set_name"]
                   for k in s.kits if k.name == d["kit_name"]]
        source_name = n.read_text(row.props.get(c.LOG_SOURCE_NAME))
        props = {
            c.LOG_MATCH_TYPE: n.select(match_type),
            c.LOG_CONFIDENCE: n.select(c.CONFIDENCE[d["confidence"]]),
            c.LOG_PRICE_BASIS: n.select(c.PRICE_BASES[d["price_basis"]]),
            c.LOG_UNUSABLE_REASON: n.select(c.UNUSABLE_REASONS[d["unusable_reason"]]),
            c.LOG_ITEM: n.text(d["item"]),
            c.LOG_PRICE: n.text(d["price_text"]),
            c.LOG_WHY: n.text(d["why"]),
            c.LOG_SUSPICIOUS: n.checkbox(d["suspicious_text"]),
            c.LOG_SET: n.relation([set_id] if set_id else []),
            c.LOG_KIT: n.relation(kit_ids[:1]),
            c.LOG_ALERT: n.title(log_rows.alert_title(match_type, d["item"], source_name)),
        }
        if d["price_known"]:
            props[c.LOG_PRICE_USD] = n.number(d["price_usd"])
        if d["price_basis"] != "none":
            props[c.LOG_FAIR_PRICE_USD] = n.number(d["fair_price_usd"])
        props[c.LOG_STAGE] = n.select(stage)
        return props

    # -- F10 to F13 ----------------------------------------------------------

    def pushes(self) -> list[Page]:
        report = push.run_queue(self.store, self.s, self.transport, self.clock)
        t = self.tally
        t.pushed += report.pushed
        t.held += report.held
        t.failed += report.failed
        t.unknown += report.unknown
        self.alert_problem |= report.alert_problem
        return report.system_rows

    def clean(self) -> None:
        stale = push.clean_sending(self.store, self.s, self.clock)
        self.tally.unknown += stale
        self.alert_problem |= stale > 0

    def system_rows(self, sources: list[Source], cap_rows: list[Page]) -> None:
        problems = system.RunProblems(
            failing_sources=[s for s in sources if s.error_count >= system.FAILING_RUNS],
            alert_problem=self.alert_problem, judging_problem=self.judging_problem)
        rows = system.make_system_rows(self.store, self.s, problems, self.clock) + cap_rows
        beat = system.make_heartbeat(self.store, self.s, sources, self.clock)
        if beat is not None:
            rows.append(beat)
        self.tally.system += len(rows)
        for row in rows:
            if push.send_row(self.store, self.s, self.transport, self.clock, row) is push.Outcome.ACCEPTED:
                self.tally.pushed += 1

    def step(self, fn, *args, default=None):
        """After F2, a Notion failure in one step is counted, printed, and the run moves on."""
        try:
            return fn(*args)
        except NotionUnavailable:
            self.say("notion unavailable")
            return default

    # -- the run -------------------------------------------------------------

    def go(self) -> int:
        try:
            sources, wanted, makers = self.load()
        except NotionUnavailable:
            return self.notion_down()
        for src in sources:
            if src.kind == c.KIND_DISCORD:
                self.step(self.discord_source, src, wanted, makers)
        for src in sources:
            if src.kind == c.KIND_KEEBDEX:
                self.step(self.keebdex_source, src, wanted)
        self.step(self.judge, wanted)
        cap_rows = self.step(self.pushes, default=[])
        self.step(self.clean)
        self.step(self.system_rows, sources, cap_rows)
        self.say(self.tally.line(getattr(self.store, "calls", 0) - self.calls_at_start))
        return 0


def run(settings, store, transport, clock, out: TextIO) -> int:
    try:
        return Run(settings, store, transport, clock, out).go()
    except Exception as e:  # never print the message or a traceback
        print(f"unexpected error: {type(e).__name__}", file=out)
        return 1


def main() -> None:
    try:
        settings = c.load(os.environ)
    except c.SettingsError as e:
        print(str(e))
        sys.exit(2)
    transport = UrllibTransport()
    real_clock = clk.SystemClock()
    store = NotionClient(settings.notion_token, transport, real_clock)
    sys.exit(run(settings, store, transport, real_clock, sys.stdout))


if __name__ == "__main__":
    main()
