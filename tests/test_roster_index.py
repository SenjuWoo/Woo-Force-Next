"""Recover the batch-suite CSV from generated profiles without regenerating them.

Run with unittest discovery; fixtures and analyzer output stay in temporary folders.
"""
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "woo" / "tools"
sys.path.insert(0, str(TOOLS))
import roster_analyzer  # noqa: E402


class RosterIndex(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        self.roster = self.root / "woo" / "roster"
        self.chars = self.roster / "chars"
        self.chars.mkdir(parents=True)
        (self.root / "data" / "select.def").write_text(
            "[Characters]\nchars/B/B.def\nchars/A/A.def\nchars/B/B.def\nrandomselect\n",
            encoding="utf-8",
        )
        for name in ("A", "B"):
            rel = f"chars/{name}/{name}.def"
            definition = self.root / rel
            definition.parent.mkdir(parents=True)
            definition.write_text("; deliberately not analyzable\n", encoding="utf-8")
            profile = {"def": rel, "display": f"{name}, fighter", "series": "test series",
                       "tier": 4, "persona": "zoner", "mode": "coach", "life": 1200, "attack": 110,
                       "ai": {"quality": "strong", "ctrls": 12, "gated": True,
                              "situational": 7, "combo": 3, "guard": 1},
                       "moves": [{"reach": 50}, {}]}
            (self.chars / f"{roster_analyzer.slug(rel)}.json").write_text(
                json.dumps(profile, indent=2) + "\n", encoding="utf-8")
        (self.roster / "gate_learned.tsv").write_bytes(b"def\tstate\tceiling\r\nA\t200\t42\r\n")

    def run_analyzer(self, *args):
        return subprocess.run([sys.executable, str(TOOLS / "roster_analyzer.py"),
                               "--root", str(self.root), "--summary-only", *args],
                              capture_output=True, text=True)

    def test_summary_only_recovers_existing_profiles_in_select_order(self):
        before = {p.relative_to(self.roster): p.read_bytes() for p in self.roster.rglob("*") if p.is_file()}
        result = self.run_analyzer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with (self.roster / "summary.csv").open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
            self.assertEqual(reader.fieldnames, [
                "def", "display", "series", "tier", "persona", "mode", "ai", "ai_ctrls", "gated",
                "moves", "moves_with_hitbox", "life", "attack", "ai_situational", "ai_combo", "ai_guard"])
        self.assertEqual([row["def"] for row in rows], ["chars/B/B.def", "chars/A/A.def"])
        self.assertEqual(list(rows[0].values()), [
            "chars/B/B.def", "B, fighter", "test series", "4", "zoner", "coach", "strong", "12", "1",
            "2", "1", "1200", "110", "7", "3", "1"])
        for rel, contents in before.items():
            self.assertEqual((self.roster / rel).read_bytes(), contents, str(rel))
        self.assertEqual({p.relative_to(self.roster) for p in self.roster.rglob("*") if p.is_file()},
                         set(before) | {Path("summary.csv")})
        first = (self.roster / "summary.csv").read_bytes()
        self.assertEqual(self.run_analyzer().returncode, 0)
        self.assertEqual((self.roster / "summary.csv").read_bytes(), first)

    def test_bad_profile_preserves_existing_summary(self):
        profile = self.chars / f"{roster_analyzer.slug('chars/A/A.def')}.json"
        summary = self.roster / "summary.csv"
        for contents in (None, "{broken json", "{}", '{"def":"chars/Wrong/Wrong.def"}'):
            with self.subTest(contents=contents):
                if contents is None:
                    profile.unlink()
                else:
                    profile.write_text(contents, encoding="utf-8")
                summary.write_bytes(b"existing index\r\n")
                result = self.run_analyzer()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(summary.read_bytes(), b"existing index\r\n")

    def test_summary_only_rejects_partial_roster(self):
        result = self.run_analyzer("--only", "B")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.roster / "summary.csv").exists())


if __name__ == "__main__":
    unittest.main()
