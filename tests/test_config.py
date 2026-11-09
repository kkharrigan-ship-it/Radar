import unittest
from zoneinfo import ZoneInfo

from radar import config

ALL = {
    "NOTION_TOKEN": "tok-notion-xyz",
    "DISCORD_BOT_TOKEN": "tok-discord-xyz",
    "PUSHOVER_APP_TOKEN": "tok-app-xyz",
    "PUSHOVER_USER_KEY": "tok-user-xyz",
    "NOTION_LOG_DS": "ds-log",
    "NOTION_SOURCES_DS": "ds-sources",
    "NOTION_SETS_DS": "ds-sets",
    "NOTION_KITS_DS": "ds-kits",
    "NOTION_COLLECTION_DS": "ds-collection",
    "RADAR_TZ": "America/Chicago",
}


class LoadSettings(unittest.TestCase):
    def test_all_ten_names_give_settings(self):
        s = config.load(ALL)
        self.assertEqual(s.notion_token, "tok-notion-xyz")
        self.assertEqual(s.discord_token, "tok-discord-xyz")
        self.assertEqual(s.pushover_app, "tok-app-xyz")
        self.assertEqual(s.pushover_user, "tok-user-xyz")
        self.assertEqual(s.log_ds, "ds-log")
        self.assertEqual(s.sources_ds, "ds-sources")
        self.assertEqual(s.sets_ds, "ds-sets")
        self.assertEqual(s.kits_ds, "ds-kits")
        self.assertEqual(s.collection_ds, "ds-collection")
        self.assertEqual(s.tz, ZoneInfo("America/Chicago"))

    def test_each_missing_or_empty_name_is_named_and_value_free(self):
        for name in ALL:
            for env in ({k: v for k, v in ALL.items() if k != name}, {**ALL, name: ""}):
                with self.subTest(name=name, empty=name in env):
                    with self.assertRaises(config.SettingsError) as cm:
                        config.load(env)
                    self.assertEqual(cm.exception.name, name)
                    msg = str(cm.exception)
                    self.assertEqual(msg, f"missing setting: {name}")
                    for value in ALL.values():
                        self.assertNotIn(value, msg)

    def test_unknown_time_zone(self):
        with self.assertRaises(config.SettingsError) as cm:
            config.load({**ALL, "RADAR_TZ": "Mars/Olympus_Mons"})
        self.assertEqual(cm.exception.name, "RADAR_TZ")
        self.assertEqual(str(cm.exception), "bad setting: RADAR_TZ")
        self.assertNotIn("Mars", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
