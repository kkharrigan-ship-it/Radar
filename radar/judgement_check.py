"""Check one of Claude's judgements in code (step F9). Pure: no network, no Notion, no clock.

    python -m radar.judgement_check JUDGEMENT_FILE [--wanted WANTED_JSON]

JUDGEMENT_FILE may be "-" for stdin. WANTED_JSON is {"<set name>": ["<kit name>", ...]}.
Prints one line, "<result> <stage or -> <error or ->", and exits 0; 2 on a usage or file error.
Error strings are fixed words and field names only, so they are safe to print and save.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Collection, Literal, Mapping

Result = Literal["pass", "reject", "fail"]

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "judgement.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

_FENCE = re.compile(r"\A```(?:json)?[ \t]*\n?(.*?)\n?```\Z", re.DOTALL)


class _NotJSON(Exception):
    pass


def _reject_constant(_name):
    raise _NotJSON()


def _unwrap(text: str) -> str:
    text = text.strip()
    m = _FENCE.match(text)
    return m.group(1).strip() if m else text


def parse(text: str) -> dict | None:
    """Rule 1: one JSON object, optionally inside one code fence. None if not."""
    body = _unwrap(text)
    try:
        value = json.loads(body, parse_constant=_reject_constant)
    except (ValueError, _NotJSON):
        return None
    return value if isinstance(value, dict) else None


def _type_ok(value, kind: str) -> bool:
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "string":
        return isinstance(value, str)
    if kind == "object":
        return isinstance(value, dict)
    return True


def _schema_error(data: dict) -> str | None:
    required = SCHEMA["required"]
    props = SCHEMA["properties"]
    for name in required:
        if name not in data:
            return f"{name}: missing"
    for name in sorted(data):
        if name not in props:
            return f"{name}: not allowed"
    for name in required:
        kind = props[name].get("type")
        if kind and not _type_ok(data[name], kind):
            return f"{name}: should be {kind}"
    for name in required:
        allowed = props[name].get("enum")
        if allowed is not None and data[name] not in allowed:
            return f"{name}: not an allowed value"
    return None


def _contradiction(d: dict) -> str | None:
    if d["status"] == "unusable" and (d["alert"] or d["match_type"] != "none"):
        return "contradiction: unusable but alerting"
    if d["match_type"] == "none" and d["alert"]:
        return "contradiction: no match but alerting"
    if not d["price_known"] and d["price_usd"] != 0:
        return "contradiction: price unknown but price_usd set"
    if d["price_basis"] == "none" and d["fair_price_usd"] != 0:
        return "contradiction: no price basis but fair_price_usd set"
    return None


def _off_wanted_list(d: dict, wanted: Mapping[str, Collection[str]]) -> bool:
    if d["match_type"] == "wanted":
        return d["set_name"] not in wanted or d["kit_name"] not in wanted[d["set_name"]]
    if d["match_type"] == "group_buy":
        return d["set_name"] not in wanted
    return False


def _stage(d: dict) -> str:
    if d["status"] == "ok" and d["alert"] and d["match_type"] != "none":
        if d["match_type"] != "great_deal" or (
            d["confidence"] != "low" and d["price_known"] and d["fair_price_usd"] > 0
        ):
            return "Push pending"
    if d["status"] == "unusable":
        return "Unreadable"
    return "No alert"


def check(text: str, wanted: Mapping[str, Collection[str]]) -> tuple[Result, str | None, str]:
    if not _unwrap(text or ""):
        return "fail", None, "empty"
    data = parse(text)
    if data is None:
        return "fail", None, "not JSON"
    problem = _schema_error(data) or _contradiction(data)
    if problem:
        return "fail", None, problem
    if _off_wanted_list(data, wanted):
        return "reject", "No alert", "set not on wanted list"
    return "pass", _stage(data), ""


def main(argv: list[str]) -> int:
    args = list(argv)
    wanted_path = None
    if "--wanted" in args:
        i = args.index("--wanted")
        if i + 1 >= len(args):
            print("usage: python -m radar.judgement_check JUDGEMENT_FILE [--wanted WANTED_JSON]", file=sys.stderr)
            return 2
        wanted_path = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        print("usage: python -m radar.judgement_check JUDGEMENT_FILE [--wanted WANTED_JSON]", file=sys.stderr)
        return 2
    try:
        text = sys.stdin.read() if args[0] == "-" else Path(args[0]).read_text(encoding="utf-8")
        wanted = json.loads(Path(wanted_path).read_text(encoding="utf-8")) if wanted_path else {}
    except (OSError, ValueError):
        print("file error", file=sys.stderr)
        return 2
    if not isinstance(wanted, dict):
        print("file error", file=sys.stderr)
        return 2
    result, stage, error = check(text, wanted)
    print(f"{result} {stage or '-'} {error or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
