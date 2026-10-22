"""Repository checks that need no game: version in step everywhere, the BETA notice is there, nothing private is
tracked, documentation links resolve, and the data files keep their shape.

usage: python -m unittest discover -s tests -v        (run from the repository root; needs git)
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [ROOT / p for p in out.decode("utf-8").split("\0") if p]


def text(p):
    return (ROOT / p).read_text(encoding="utf-8", errors="replace")


class Version(unittest.TestCase):
    def test_version_is_the_same_everywhere(self):
        brain = re.search(r"local VERSION = '([^']+)'", text("external/mods/woo_brain.lua"))
        self.assertIsNotNone(brain, "woo_brain.lua has no VERSION constant")
        v = brain.group(1)
        self.assertRegex(v, r"^\d+\.\d+\.\d+(-beta\.\d+)?$")
        self.assertRegex(text("CHANGELOG.md"), rf"## \[{re.escape(v)}\] - \d{{4}}-\d\d-\d\d")
        self.assertIn(v.replace("-", "--"), text("README.md"), "README version badge")
        self.assertIn(f"woo_brain {v} (BETA) loaded", text("docs/INSTALL.md"))
        self.assertIn(f"woo_brain {v} (BETA) loaded", text("external/mods/woo_brain.lua").replace("' .. VERSION .. '", v))


class BetaNotice(unittest.TestCase):
    def test_readme_says_beta_up_front(self):
        top = "\n".join(text("README.md").splitlines()[:30])
        self.assertIn("status-BETA", top, "BETA badge must be in the first 30 lines")
        self.assertIn("This is BETA software", top)

    def test_docs_repeat_it(self):
        for doc in ("docs/INSTALL.md", "docs/TESTING.md", "docs/KNOWN-ISSUES.md"):
            self.assertIn("BETA", text(doc), doc)


class Privacy(unittest.TestCase):
    PATTERNS = {
        "a Windows user-profile path": re.compile(r"[A-Za-z]:\\Users\\", re.I),
        "an email address": re.compile(r"\b[\w.+-]+@(?!users\.noreply\.github\.com)[\w-]+\.[a-z]{2,}\b", re.I),
        "a GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        # (SFF file-format versions such as "SFF 2.0.1.0" look like addresses and are not)
        "an IP address": re.compile(r"(?<!SFF )\b(?!127\.0\.0\.1\b)(?!0\.0\.0\.0\b)(?:\d{1,3}\.){3}\d{1,3}\b"),
    }

    def test_no_private_data_is_tracked(self):
        bad = []
        for p in tracked():
            if p.suffix.lower() in (".png", ".jpg", ".zip", ".exe"):
                continue
            body = p.read_text(encoding="utf-8", errors="replace")
            for what, rx in self.PATTERNS.items():
                m = rx.search(body)
                if m:
                    bad.append(f"{p.relative_to(ROOT).as_posix()}: {what}")
        self.assertEqual(bad, [])

    def test_only_original_work_is_tracked(self):
        for p in tracked():
            rel = p.relative_to(ROOT).as_posix()
            self.assertFalse(rel.startswith(("chars/", "chars_fix/", "stages/", "sound/", "font/", "save/")), rel)
            self.assertNotIn(p.suffix.lower(), (".sff", ".snd", ".act", ".exe", ".dll", ".zip", ".7z"), rel)
            self.assertLess(p.stat().st_size, 2_000_000, f"{rel} is unexpectedly large")


class Links(unittest.TestCase):
    def test_relative_markdown_links_resolve(self):
        missing = []
        for md in [p for p in tracked() if p.suffix == ".md"]:
            for target in re.findall(r"\]\(([^)\s]+)\)", md.read_text(encoding="utf-8")):
                if target.startswith(("http://", "https://", "#", "mailto:", "../../")):
                    continue  # ../../issues/... are GitHub-relative links
                path = target.split("#")[0]
                if path and not (md.parent / path).exists():
                    missing.append(f"{md.relative_to(ROOT).as_posix()} -> {target}")
        self.assertEqual(missing, [])


class DataFiles(unittest.TestCase):
    def test_lore_lines_have_six_fields(self):
        n = 0
        for ln in text("woo/lore.txt").splitlines():
            s = ln.strip()
            if s and not s.startswith("#"):
                n += 1
                self.assertEqual(s.count("|"), 5, s[:60])
        self.assertGreater(n, 100)

    def test_profiles_and_duos(self):
        self.assertIn("[analyzer]", text("woo/profiles.ini"))
        self.assertTrue(any(ln.strip() and not ln.startswith("#") for ln in text("woo/duos.txt").splitlines()))

    def test_required_files(self):
        for f in ("LICENSE", "SECURITY.md", "CHANGELOG.md", "requirements.txt", "data/woo_brain.zss",
                  "patches/start-lua-palette-default.patch", ".github/workflows/ci.yml", ".github/dependabot.yml"):
            self.assertTrue((ROOT / f).is_file(), f)


if __name__ == "__main__":
    unittest.main()
