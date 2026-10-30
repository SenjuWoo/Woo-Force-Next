"""Exercise upgrade packaging against real temporary Git histories and ZIP bytes.

No installed game or player files are changed; unittest discovery runs these checks.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "woo/tools"))
import make_upgrade_pack  # noqa: E402


class UpgradePack(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source with spaces"
        self.root.mkdir()
        self.files = {
            "external/mods/woo_brain.lua": b"local VERSION = '0.9.0-beta.2'\r\n",
            "data/woo_brain.zss": b"; bridge\r\n",
            "woo/tools/run_match.py": b"print('runner')\n",
            "woo/tools/select_shot.lua": b"print('shot')\r\n",
            "requirements.txt": b"Pillow\n",
            "LICENSE": b"MIT license fixture\n",
            "save/config.ini": b"personal settings\n",
            "woo/roster/gate_learned.tsv": b"personal learning\n",
            "STATE.md": b"private progress\n",
            "tests/private.py": b"private test\n",
        }
        for name, data in self.files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@users.noreply.github.com")
        self.git("config", "core.autocrlf", "false")
        self.git("add", ".")
        self.git("commit", "-qm", "Fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True,
                              text=True).stdout

    def test_payload_is_committed_allowlist_and_receipts_match(self):
        (self.root / "external/mods/woo_brain.lua").write_bytes(b"uncommitted runtime edit")
        (self.root / "woo/tools/untracked.py").write_bytes(b"private scratch")
        out = self.root / "artifacts with spaces"
        receipt = make_upgrade_pack.build_upgrade(self.root, out)
        self.assertEqual(receipt["commit"], self.commit)
        self.assertEqual(receipt["version"], "0.9.0-beta.2")
        expected = {
            "external/mods/woo_brain.lua": self.files["external/mods/woo_brain.lua"],
            "data/woo_brain.zss": self.files["data/woo_brain.zss"],
            "woo/tools/run_match.py": self.files["woo/tools/run_match.py"],
            "woo/tools/select_shot.lua": self.files["woo/tools/select_shot.lua"],
            "woo/tools/requirements.txt": self.files["requirements.txt"],
            "woo/LICENSE.txt": self.files["LICENSE"],
        }
        archive = out / receipt["archive"]["name"]
        with zipfile.ZipFile(archive) as packed:
            self.assertIsNone(packed.testzip())
            self.assertEqual(packed.namelist(), sorted(expected))
            self.assertEqual({name: packed.read(name) for name in packed.namelist()}, expected)
            extracted = self.root / "fresh extraction"
            packed.extractall(extracted)
        for name, data in expected.items():
            self.assertEqual((extracted / name).read_bytes(), data)
            self.assertEqual(receipt["files"][name], {
                "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        self.assertEqual(receipt["archive"]["size"], archive.stat().st_size)
        self.assertEqual(receipt["archive"]["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(json.loads((out / "release-manifest.json").read_text()), receipt)
        for line in (out / "SHA256SUMS.txt").read_text().splitlines():
            digest, name = line.split("  ")
            self.assertEqual(hashlib.sha256((out / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((self.root / "save/config.ini").read_bytes(), self.files["save/config.ini"])

    def test_builds_are_byte_identical(self):
        first = self.root / "first output"
        second = self.root / "second output"
        make_upgrade_pack.build_upgrade(self.root, first)
        make_upgrade_pack.build_upgrade(self.root, second)
        self.assertEqual({p.name: p.read_bytes() for p in first.iterdir()},
                         {p.name: p.read_bytes() for p in second.iterdir()})

    def test_missing_committed_runtime_and_invalid_version_leave_output_absent(self):
        for contents in (b"no version", b"local VERSION = '../../escape'\n"):
            with self.subTest(contents=contents):
                (self.root / "external/mods/woo_brain.lua").write_bytes(contents)
                self.git("add", ".")
                self.git("commit", "-qm", "Invalid version")
                out = self.root / "invalid output"
                with self.assertRaises(ValueError):
                    make_upgrade_pack.build_upgrade(self.root, out)
                self.assertFalse(out.exists())
        self.git("rm", "data/woo_brain.zss")
        self.git("commit", "-qm", "Missing bridge")
        with self.assertRaises(ValueError):
            make_upgrade_pack.build_upgrade(self.root, self.root / "missing output")
        self.assertFalse((self.root / "missing output").exists())

    def test_committed_symlink_is_rejected_without_following_its_target(self):
        blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=self.root,
                              input=b"../../save/config.ini", capture_output=True, check=True).stdout.decode().strip()
        self.git("update-index", "--add", "--cacheinfo", f"120000,{blob},woo/tools/linked.py")
        self.git("commit", "-qm", "Symlink fixture")
        out = self.root / "unsafe output"
        with self.assertRaisesRegex(ValueError, "unsafe source entry"):
            make_upgrade_pack.build_upgrade(self.root, out)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
