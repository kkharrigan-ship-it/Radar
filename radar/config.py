"""Settings from the environment, and every Notion property name in one place.

Values are never printed. Errors name the setting only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class SettingsError(Exception):
    def __init__(self, name: str, problem: str = "missing"):
        self.name = name
        super().__init__(f"{problem} setting: {name}")


@dataclass(frozen=True)
class Settings:
    notion_token: str
    discord_token: str
    pushover_app: str
    pushover_user: str
    log_ds: str
    sources_ds: str
    sets_ds: str
    kits_ds: str
    collection_ds: str
    tz: ZoneInfo


_FIELDS = {
    "NOTION_TOKEN": "notion_token",
    "DISCORD_BOT_TOKEN": "discord_token",
    "PUSHOVER_APP_TOKEN": "pushover_app",
    "PUSHOVER_USER_KEY": "pushover_user",
    "NOTION_LOG_DS": "log_ds",
    "NOTION_SOURCES_DS": "sources_ds",
    "NOTION_SETS_DS": "sets_ds",
    "NOTION_KITS_DS": "kits_ds",
    "NOTION_COLLECTION_DS": "collection_ds",
    "RADAR_TZ": "tz",
}


def load(environ: Mapping[str, str]) -> Settings:
    values: dict[str, object] = {}
    for env_name, field in _FIELDS.items():
        value = (environ.get(env_name) or "").strip()
        if not value:
            raise SettingsError(env_name)
        values[field] = value
    try:
        values["tz"] = ZoneInfo(str(values["tz"]))
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise SettingsError("RADAR_TZ", "bad") from None
    return Settings(**values)  # type: ignore[arg-type]


# --- Radar Log -------------------------------------------------------------
LOG_ALERT = "Alert"
LOG_STAGE = "Stage"
LOG_MATCH_TYPE = "Match Type"
LOG_SOURCE = "Source"
LOG_SOURCE_NAME = "Source Name"
LOG_DEDUPE_KEY = "Dedupe Key"
LOG_LINK = "Link"
LOG_POST_TEXT = "Post Text"
LOG_ITEM = "Item"
LOG_PRICE = "Price"
LOG_PRICE_USD = "Price USD"
LOG_FAIR_PRICE_USD = "Fair Price USD"
LOG_PRICE_BASIS = "Price Basis"
LOG_SET = "Set"
LOG_KIT = "Kit"
LOG_WHY = "Why"
LOG_CONFIDENCE = "Confidence"
LOG_SUSPICIOUS = "Suspicious"
LOG_UNUSABLE_REASON = "Unusable Reason"
LOG_JUDGEMENT = "Judgement"
LOG_JUDGE_ERROR = "Judge Error"
LOG_FOUND_AT = "Found At"
LOG_PUSHED_AT = "Pushed At"
LOG_PUSH_ATTEMPTS = "Push Attempts"
LOG_JUDGE_ATTEMPTS = "Judge Attempts"

# Stage values
STAGE_TO_JUDGE = "To judge"
STAGE_JUDGED = "Judged"
STAGE_PUSH_PENDING = "Push pending"
STAGE_SENDING = "Sending"
STAGE_PUSHED = "Pushed"
STAGE_PUSH_UNKNOWN = "Push unknown"
STAGE_PUSH_FAILED = "Push failed"
STAGE_HELD_CAP = "Held (cap)"
STAGE_NO_ALERT = "No alert"
STAGE_UNREADABLE = "Unreadable"
STAGE_JUDGE_FAILED = "Judge failed"

# Match Type values
MATCH_WANTED = "Wanted"
MATCH_GROUP_BUY = "Group buy"
MATCH_GREAT_DEAL = "Great deal"
MATCH_NONE = "None"
MATCH_SYSTEM = "System"

# Source values
SOURCE_DISCORD = "Discord"
SOURCE_KEEBDEX = "Keebdex"
SOURCE_RADAR = "Radar"

# Confidence values
CONFIDENCE = {"low": "Low", "medium": "Medium", "high": "High"}
CONFIDENCE_HIGH = "High"

# Judgement value → select name
MATCH_TYPES = {
    "wanted": MATCH_WANTED,
    "group_buy": MATCH_GROUP_BUY,
    "great_deal": MATCH_GREAT_DEAL,
    "none": MATCH_NONE,
}
PRICE_BASES = {
    "value_snapshot": "Value snapshot",
    "deal_log": "Deal log",
    "launch_price": "Launch price",
    "none": "None",
}
UNUSABLE_REASONS = {
    "": None,
    "empty": "Empty",
    "not_a_listing": "Not a listing",
    "unreadable": "Unreadable",
    "other": "Other",
}

# --- Radar Sources ---------------------------------------------------------
SRC_NAME = "Name"
SRC_KIND = "Kind"
SRC_ADDRESS = "Address"
SRC_ENABLED = "Enabled"
SRC_CURSOR = "Cursor"
SRC_LAST_OK = "Last OK"
SRC_LAST_ERROR = "Last Error"
SRC_ERROR_COUNT = "Error Count"

KIND_DISCORD = "Discord channel"
KIND_KEEBDEX = "Keebdex page"
KIND_GMAIL = "Gmail label"

# --- Vault -----------------------------------------------------------------
KITS_NAME = "Name"
KITS_STATUS = "Status"
KITS_SET = "Set"
STATUS_WANTED = "Wanted"

SETS_NAME = "Name"
SETS_MAKER = "Maker"
SETS_RADAR_TERMS = "Radar Terms"

COLLECTION_HOLDING = "Holding"
COLLECTION_MAKER = "Maker"
HOLDING_OWNED = "Owned"
HOLDING_ON_ORDER = "On order"
