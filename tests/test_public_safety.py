"""Guard tests: this repository is public. Scans every tracked file (and any new file not yet
ignored) for real-looking IDs and tokens."""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UUID = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])")
MADE_UP_UUID = re.compile(r"00000000-0000-4000-8000-0000000000\d\d")
HEX32 = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")
MADE_UP_HEX32 = re.compile(r"000000000000400080000000000000\d\d")
SNOWFLAKE = re.compile(r"(?<!\d)\d{17,20}(?!\d)")
NOTION_TOKEN = re.compile(r"(?:ntn_|secret_)[A-Za-z0-9]{20,}")
DISCORD_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,40}(?![A-Za-z0-9_-])")


def repo_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.splitlines() if line and (ROOT / line).is_file()]


def problems(text: str) -> list[str]:
    found = []
    found += [f"uuid {m.start()}" for m in UUID.finditer(text) if not MADE_UP_UUID.fullmatch(m.group())]
    found += [f"hex32 {m.start()}" for m in HEX32.finditer(text) if not MADE_UP_HEX32.fullmatch(m.group())]
    found += [f"snowflake {m.start()}" for m in SNOWFLAKE.finditer(text) if not m.group().startswith("9000000000000000")]
    found += [f"notion token {m.start()}" for m in NOTION_TOKEN.finditer(text)]
    found += [f"discord token {m.start()}" for m in DISCORD_TOKEN.finditer(text)]
    return found


class Guard(unittest.TestCase):
    def test_patterns_catch_real_looking_values(self):
        # Built at run time, so this file never holds a real-looking value itself.
        self.assertTrue(problems("-".join(["1b2c3d4e", "5f60", "4a1b", "9c2d", "3e4f5a6b7c8d"])))
        self.assertTrue(problems("1b2c3d4e" * 4))
        self.assertTrue(problems("123456789" * 2))
        self.assertTrue(problems("ntn_" + "a1" * 12))
        self.assertTrue(problems("secret_" + "Zz" * 12))
        self.assertTrue(problems("MTIzNDU2Nzg5MDEyMzQ1Njc4OQ" + ".GaBcDe." + "x" * 38))
        self.assertEqual(problems("00000000-0000-4000-8000-000000000042 9000000000000000101"), [])

    def test_every_repo_file_is_clean(self):
        files = repo_files()
        self.assertTrue(files)
        bad = {}
        for path in files:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if found := problems(text):
                bad[str(path.relative_to(ROOT))] = found[:3]
        self.assertEqual(bad, {})

    def test_raw_pages_stay_ignored(self):
        names = [p.name for p in repo_files()]
        self.assertFalse([n for n in names if n.endswith(".raw.html")])

    def test_no_string_from_the_raw_pages_reaches_the_repo(self):
        """Runs only where the git-ignored raw Keebdex pages exist (Katie's machine, not CI)."""
        raws = sorted((ROOT / "tests" / "fixtures" / "keebdex").glob("*.raw.html"))
        if not raws:
            self.skipTest("no raw pages here")
        import json
        payload = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
        secret: set[str] = set()
        for raw in raws:
            data = json.loads(payload.search(raw.read_text(encoding="utf-8")).group(1))
            for obj in data:
                if isinstance(obj, dict):
                    for key in ("name", "slug", "profile_keyset_id", "designer", "description", "url", "img"):
                        v = obj.get(key)
                        if isinstance(v, int) and 0 <= v < len(data) and isinstance(data[v], str) and len(data[v]) >= 5:
                            secret.add(data[v])
        secret -= {"Keebdex", "keebdex", "https://keebdex.org"}  # the site itself, not data
        leaks = 0
        for path in repo_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            leaks += sum(1 for s in secret if s in text)
        self.assertEqual(leaks, 0)
