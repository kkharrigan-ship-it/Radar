"""Normalise, usable terms, the wanted list and the message matcher (G-F5)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

STOP_LIST: frozenset[str] = frozenset({
    "base", "kit", "kits", "set", "gmk", "keycap", "keycaps", "keyboard",
    "novelties", "alphas", "mods", "spacebars", "extension", "blue", "red",
    "black", "white", "round", "r1", "r2", "r3",
})

_SPACES = re.compile(r" +")


def normalise(text: str) -> str:
    text = text.replace("º", "o").replace("°", "o").lower()
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = "".join(c if c.isalnum() else " " for c in text)
    return _SPACES.sub(" ", text).strip()


def matches(term: str, text: str) -> bool:
    return f" {term} " in f" {text} "


def usable(term: str) -> bool:
    return len(term) >= 3 and term not in STOP_LIST


# --- rows read from Vault ---------------------------------------------------

@dataclass
class SetRow:
    page_id: str
    name: str
    maker: str
    radar_terms: str


@dataclass
class KitRow:
    page_id: str
    name: str
    set_ids: list[str]


@dataclass
class HoldingRow:
    maker: str


# --- the wanted list --------------------------------------------------------

@dataclass
class WantedKit:
    page_id: str
    name: str
    norm: str


@dataclass
class WantedSet:
    page_id: str
    name: str
    norm: str
    terms: list[str]
    kits: list[WantedKit]


@dataclass
class Wanted:
    sets: list[WantedSet]
    set_ids_by_name: dict[str, str]
    sets_without_terms: int = 0

    def as_check_map(self) -> dict[str, list[str]]:
        return {s.name: [k.name for k in s.kits] for s in self.sets}


def _dedupe(items):
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)


def _terms(row: SetRow) -> list[str]:
    terms = _dedupe(t for t in (normalise(p) for p in row.radar_terms.split(";")) if usable(t))
    if not terms:
        name = normalise(row.name)
        if usable(name):
            terms = [name]
    return terms


def build_wanted(sets: list[SetRow], wanted_kits: list[KitRow]) -> Wanted:
    kits_by_set: dict[str, list[WantedKit]] = {}
    for k in wanted_kits:
        for set_id in k.set_ids:
            kits_by_set.setdefault(set_id, []).append(WantedKit(k.page_id, k.name, normalise(k.name)))
    wanted_sets = []
    for row in sets:
        kits = kits_by_set.get(row.page_id)
        if not kits:
            continue
        wanted_sets.append(WantedSet(row.page_id, row.name, normalise(row.name), _terms(row),
                                     sorted(kits, key=lambda k: k.name)))
    return Wanted(
        sets=wanted_sets,
        set_ids_by_name={row.name: row.page_id for row in sets},
        sets_without_terms=sum(1 for s in wanted_sets if not s.terms),
    )


def build_taste_makers(sets: list[SetRow], holdings: list[HoldingRow]) -> list[str]:
    makers = (normalise(m) for m in [h.maker for h in holdings] + [s.maker for s in sets])
    return _dedupe(m for m in makers if usable(m))


# --- matching ---------------------------------------------------------------

@dataclass
class WantedMatch:
    set: WantedSet
    kits: list[WantedKit]


@dataclass
class MessageMatch:
    wanted: list[WantedMatch] = field(default_factory=list)
    candidate: bool = False


def match_message(text: str, wanted: Wanted, makers: list[str]) -> MessageMatch:
    norm = normalise(text)
    result = MessageMatch()
    for s in wanted.sets:
        if not any(matches(t, norm) for t in s.terms):
            continue
        kits = [k for k in s.kits if k.norm and matches(k.norm, norm)]
        if not kits and len(s.kits) == 1:
            kits = list(s.kits)
        if kits:
            result.wanted.append(WantedMatch(s, kits))
        else:
            result.candidate = True
    if any(matches(m, norm) for m in makers):
        result.candidate = True
    return result
