"""Match-tool regressions run without Ikemen, using isolated files and fake child processes.

Run with unittest discovery; no game files or player settings are touched.
"""
import contextlib
import io
import json
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "woo" / "tools"


class Engine:
    """A controllable child that exposes kill/reap behavior without opening a window."""

    def __init__(self, code=0, stderr=b"", hang=False):
        self.returncode = code
        self.stderr = io.BytesIO(stderr)
        self.hang = hang
        self.killed = False
        self.reaped = False

    def wait(self, timeout=None):
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("fake engine", timeout)
        self.reaped = True
        return self.returncode

    def poll(self):
        return None if self.hang and not self.killed else self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class MatchTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "woo/tools").mkdir(parents=True)
        for name in ("run_match", "finisher_check", "ladder", "ab_test", "stall_ab", "crash_scan"):
            shutil.copyfile(TOOLS / f"{name}.py", self.root / f"woo/tools/{name}.py")
        (self.root / "data/ikemen1").mkdir(parents=True)
        (self.root / "save").mkdir()
        (self.root / "save/config.ini").write_text("Motif = default\n", encoding="utf-8")
        (self.root / "woo/roster").mkdir()
        (self.root / "woo/logs").mkdir()
        (self.root / "woo/roster/summary.csv").write_text(
            "def,display,ai,moves,tier\nchars/A/A.def,A,yes,10,1\nchars/B/B.def,B,yes,10,2\n",
            encoding="utf-8")
        self.log = self.root / "woo/logs/brain.log"
        self.log.write_text("", encoding="utf-8")

    def script(self, name, args):
        """Run an unchanged CLI entry point against the temporary project."""
        with patch.object(sys, "argv", [name, *args]), patch.object(sys, "path", [str(TOOLS), *sys.path]), warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)  # legacy CLI readers are not context-managed
            with contextlib.redirect_stdout(io.StringIO()) as output:
                try:
                    runpy.run_path(str(self.root / f"woo/tools/{name}.py"), run_name="__main__")
                except SystemExit as exc:
                    return exc.code or 0, output.getvalue()
                except RuntimeError as exc:
                    return 1, output.getvalue() + str(exc)
        return 0, output.getvalue()

    def runner(self, engine, extra=(), log="", stats=""):
        def launch(*args, **kwargs):
            with self.log.open("a", encoding="utf-8") as fh:
                fh.write(log)
            if stats:
                (self.root / "woo/test/brain/last_match.txt").write_text(stats, encoding="utf-8")
            return engine

        with patch.object(sys, "path", [str(TOOLS), *sys.path]), patch("subprocess.Popen", side_effect=launch), patch("winpark.park"):
            return self.script("run_match", ["--p1", "chars/A/A.def", "--p2", "chars/B/B.def", *extra])

    def test_engine_failure_is_failure(self):
        self.assertNotEqual(self.runner(Engine(7))[0], 0)

    def test_zero_exit_without_round_is_failure(self):
        self.assertNotEqual(self.runner(Engine())[0], 0)

    def test_module_override_changes_only_sandbox_config(self):
        config = self.root / "save/config.ini"
        original = b"[Common]\r\nModules = player/custom.lua\r\n[Config]\r\nMotif = default\r\n"
        config.write_bytes(original)
        code, output = self.runner(Engine(), ["--module", "tests/fixtures/timeout_result.lua"], self.events(), self.stats())
        self.assertEqual(code, 0, output)
        copied = (self.root / "woo/test/brain/config.ini").read_text(encoding="utf-8")
        self.assertIn("Modules = tests/fixtures/timeout_result.lua", copied)
        self.assertNotIn("player/custom.lua", copied)
        self.assertEqual(config.read_bytes(), original)

    def test_default_keeps_configured_modules(self):
        config = self.root / "save/config.ini"
        original = "[Common]\nModules = player/custom.lua\n[Config]\nMotif = default\n"
        config.write_text(original, encoding="utf-8")
        code, output = self.runner(Engine(), [], self.events(), self.stats())
        self.assertEqual(code, 0, output)
        self.assertIn("Modules = player/custom.lua", (self.root / "woo/test/brain/config.ini").read_text(encoding="utf-8"))
        self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_learned_data_is_copied_per_box_and_child_writes_preserve_player_data(self):
        baseline = self.root / "woo/roster/gate_learned.tsv"
        original = b"chars/A/A.def\tfree\t1000\r\n"
        baseline.write_bytes(original)
        paths = []

        def launch(args, **kwargs):
            self.assertIn("-woo.learned", args)
            path = self.root / args[args.index("-woo.learned") + 1]
            paths.append(path)
            self.assertEqual(path.read_bytes(), original)
            path.write_bytes(original + b"chars/B/B.def\t1000\t2000\n")
            with self.log.open("a", encoding="utf-8") as fh:
                fh.write("loadcheck ok P1 a\nloadcheck ok P2 b\n")
            return Engine()

        with patch.object(sys, "path", [str(TOOLS), *sys.path]), patch("subprocess.Popen", side_effect=launch), patch("winpark.park"):
            for box in ("alpha", "beta", "alpha"):
                code, output = self.script("run_match", ["--p1", "chars/A/A.def", "--p2", "chars/B/B.def",
                                                         "--box", box, "--endafter", "20"])
                self.assertEqual(code, 0, output)
                self.assertEqual(baseline.read_bytes(), original)
        self.assertNotEqual(paths[0], paths[1])
        self.assertEqual(paths[0], paths[2])
        self.assertTrue(all(path.is_relative_to(self.root / "woo/test") for path in paths))

    def test_missing_learned_data_starts_empty_in_sandbox(self):
        code, output = self.runner(Engine(), ["--endafter", "20"], "loadcheck ok P1 a\nloadcheck ok P2 b\n")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.root / "woo/test/brain/gate_learned.tsv").read_bytes(), b"")
        self.assertFalse((self.root / "woo/roster/gate_learned.tsv").exists())

    def test_single_line_panic_is_failure_and_child_is_reaped(self):
        engine = Engine(stderr=b"Panic: character failed\n", hang=True)
        code, output = self.runner(engine, ["--timeout", "0"])
        self.assertNotEqual(code, 0)
        self.assertIn("ENGINE PANIC:", output)
        self.assertTrue(engine.killed)
        self.assertTrue(engine.reaped)

    def test_timeout_is_failure_and_child_is_reaped(self):
        engine = Engine(hang=True)
        self.assertNotEqual(self.runner(engine, ["--timeout", "0"])[0], 0)
        self.assertTrue(engine.killed)
        self.assertTrue(engine.reaped)

    def test_bounded_loadcheck_remains_success(self):
        code, _ = self.runner(Engine(), ["--endafter", "20"],
                              "loadcheck ok P1 chars/A/A.def\nloadcheck ok P2 chars/B/B.def\n")
        self.assertEqual(code, 0)

    def test_bounded_failure_after_loadcheck_still_fails(self):
        code, _ = self.runner(Engine(7), ["--endafter", "20"],
                              "loadcheck ok P1 chars/A/A.def\nloadcheck ok P2 chars/B/B.def\n")
        self.assertNotEqual(code, 0)

    def test_bounded_missing_player_is_failure(self):
        self.assertNotEqual(self.runner(Engine(), ["--endafter", "20"], "loadcheck ok P1 chars/A/A.def\n")[0], 0)

    def test_brain_runtime_error_invalidates_completed_and_bounded_samples(self):
        self.assertNotEqual(self.runner(Engine(), [],
                            self.events() + "ERROR (brain off; fighters handed back to their own AI): fault\n", self.stats())[0], 0)
        self.assertNotEqual(self.runner(Engine(), ["--endafter", "20"],
                            "loadcheck ok P1 a\nloadcheck ok P2 b\nERROR (brain off; fault\n")[0], 0)

    @staticmethod
    def stats(last=1, wins=(1, 0)):
        return (f'table: 0x1 {{\n  ["LastRound"] => {last}\n  ["Wins"] => table: 0x2 {{\n'
                f'    [1] => {wins[0]}\n    [2] => {wins[1]}\n  }}\n}}\n')

    @staticmethod
    def events(rn=1, result=("win", "loss")):
        # P2 has higher raw life, but P1's 400/500 share beats P2's 600/1000 on time.
        return (f'round {rn} end P1 chars/A/A.def mode=overmind life=400 result={result[0]}\n'
                f'round {rn} end P2 chars/B/B.def mode=coach life=600 result={result[1]}\n')

    def test_time_over_and_woo_off_use_engine_winner(self):
        for args in ([], ["--woo", "off"]):
            with self.subTest(args=args):
                code, output = self.runner(Engine(), args, self.events(), self.stats())
                self.assertEqual(code, 0, output)
                result = json.loads(next(ln[len("MATCH_RESULT: "):] for ln in output.splitlines()
                                         if ln.startswith("MATCH_RESULT: ")))
                self.assertEqual(result["rounds"], [1])
                self.assertFalse(result["bounded"])

    def test_first_to_two_accepts_three_rounds_and_preserves_draw(self):
        log = self.events(1, ("draw", "draw")) + self.events(2) + self.events(3)
        code, output = self.runner(Engine(), ["--rounds", "2"], log, self.stats(3, (2, 0)))
        self.assertEqual(code, 0, output)
        self.assertIn('"rounds": [0, 1, 1]', output)

    def test_draw_limit_can_complete_match_with_both_native_win_counts(self):
        code, output = self.runner(Engine(), [], self.events(result=("draw", "draw")), self.stats(1, (1, 1)))
        self.assertEqual(code, 0, output)
        self.assertIn('"rounds": [0]', output)

    def test_simul_and_turns_thresholds_use_team_sides(self):
        team = self.events() + 'round 1 end P3 chars/C/C.def mode=coach life=0 result=win\n'
        code, output = self.runner(Engine(), ["--p3", "chars/C/C.def"], team, self.stats())
        self.assertEqual(code, 0, output)
        # The opponent has one member, so a Turns team may win after one victory despite --rounds 2.
        code, output = self.runner(Engine(), ["--rounds", "2", "--tmode2", "2"], self.events(), self.stats())
        self.assertEqual(code, 0, output)

    def test_legacy_turns_reuses_active_slot_on_either_side(self):
        (self.root / "save/config.ini").write_text("Motif = default\nTurnsLoading = 0\n", encoding="utf-8")
        for side, reserve in ((1, 3), (2, 4)):
            with self.subTest(side=side):
                args = [f"--tmode{side}", "2", f"--p{reserve}", "chars/C/C.def"]
                result = ("loss", "win") if side == 1 else ("win", "loss")
                wins = (0, 2) if side == 1 else (2, 0)
                # The second member replaces P1/P2, rather than appearing as P3/P4.
                events = self.events(1, result) + self.events(2, result).replace("chars/A/A.def" if side == 1 else "chars/B/B.def", "chars/C/C.def")
                code, output = self.runner(Engine(), args, events, self.stats(2, wins))
                self.assertEqual(code, 0, output)
                code, output = self.runner(Engine(), [*args, "--endafter", "20"],
                                           "loadcheck ok P1 a\nloadcheck ok P2 b\n")
                self.assertEqual(code, 0, output)

    def test_simul_tag_and_preloaded_turns_require_reserve_slot_results(self):
        for mode, loading in (("1", 0), ("3", 0), ("2", 1)):
            with self.subTest(mode=mode, loading=loading):
                (self.root / "save/config.ini").write_text(f"Motif = default\nTurnsLoading = {loading}\n", encoding="utf-8")
                args = ["--tmode1", mode, "--p3", "chars/C/C.def"]
                self.assertNotEqual(self.runner(Engine(), args, self.events(), self.stats())[0], 0)
                self.assertNotEqual(self.runner(Engine(), [*args, "--endafter", "20"],
                                                "loadcheck ok P1 a\nloadcheck ok P2 b\n")[0], 0)

    def test_incomplete_or_contradictory_rounds_fail(self):
        cases = ((self.events(), self.stats(1, (1, 0)), ["--rounds", "2"]),
                 (self.events().splitlines()[0] + "\n", self.stats(), []),
                 (self.events(result=("win", "win")), self.stats(), []),
                 (self.events().replace(" result=win", ""), self.stats(), []),
                 (self.events(2), self.stats(2, (2, 0)), []))
        for log, stats, args in cases:
            with self.subTest(log=log, args=args):
                self.assertNotEqual(self.runner(Engine(), args, log, stats)[0], 0)

    def test_crash_scan_does_not_accept_loadcheck_before_failure(self):
        defs = self.root / "defs.txt"
        defs.write_text("chars/A/A.def\n", encoding="utf-8")

        def child(*args, **kwargs):
            (self.root / "woo/logs/crash.log").write_text(
                "loadcheck ok P1 chars/A/A.def\nloadcheck ok P2 chars/Kiryu/Kiryu.def\n", encoding="utf-8")
            return subprocess.CompletedProcess([], 1, stdout="Loading module\nENGINE PANIC: chars/A/states.cns: broken\n", stderr="")

        with patch("subprocess.run", side_effect=child):
            self.script("crash_scan", ["--defs-from", str(defs)])
        self.assertIn("chars/A/A.def,crash,", (self.root / "woo/roster/crash_scan.csv").read_text(encoding="utf-8"))

    def test_reporters_reject_failed_and_empty_children(self):
        for name, args in (("finisher_check", ["--n", "1"]), ("ladder", ["--matches", "1"]),
                           ("ab_test", ["--n", "1"]), ("stall_ab", ["--n", "1", "--modes", "off"])):
            for returncode in (1, 0):
                with self.subTest(script=name, returncode=returncode):
                    child = subprocess.CompletedProcess([], returncode, stdout=b"", stderr=b"")
                    with patch("subprocess.run", return_value=child):
                        code, _ = self.script(name, args)
                    self.assertNotEqual(code, 0)

    def test_reporters_reject_zero_sample_requests(self):
        for name, args in (("finisher_check", ["--n", "0"]), ("ladder", ["--matches", "0"]),
                           ("ab_test", ["--n", "0"]), ("stall_ab", ["--n", "0"])):
            with self.subTest(script=name), contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(self.script(name, args)[0], 0)

    def test_reporters_reject_bounded_malformed_and_ambiguous_envelopes(self):
        bodies = (b'MATCH_RESULT: {"bounded":true,"rounds":[1]}\n', b'MATCH_RESULT: {}\n',
                  b'MATCH_RESULT: {broken}\n', self.envelope() * 2,
                  b'MATCH_RESULT: {"bounded":false,"rounds":[true]}\n')
        for body in bodies:
            with self.subTest(body=body), patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, body, b"")):
                self.assertNotEqual(self.script("ab_test", ["--n", "1"])[0], 0)

    def test_finisher_violation_fails(self):
        def child(*args, **kwargs):
            (self.root / "woo/logs/fin/0.log").write_text(
                'super P1 chars/A/A.def st=1000 "Super" cost=1000 atk=1 foe_low=90% need=20% self=90% allies=1 shut=true/true\n',
                encoding="utf-8")
            return subprocess.CompletedProcess([], 0, stdout=self.envelope(), stderr=b"")

        with patch("subprocess.run", side_effect=child):
            code, output = self.script("finisher_check", ["--n", "1"])
        self.assertIn("violations: 1", output)
        self.assertNotEqual(code, 0)

    def test_finisher_rejects_no_qualifying_supers(self):
        for text in (self.events(),
                     'super P1 chars/A/A.def st=1000 "Flash" cost=500 atk=1 foe_low=20% need=20% self=90% allies=1 shut=false/false\n'):
            with self.subTest(text=text):
                def child(*args, **kwargs):
                    (self.root / "woo/logs/fin/0.log").write_text(text, encoding="utf-8")
                    return subprocess.CompletedProcess([], 0, stdout=self.envelope(), stderr=b"")

                with patch("subprocess.run", side_effect=child):
                    code, output = self.script("finisher_check", ["--n", "1"])
                self.assertNotEqual(code, 0)
                self.assertIn("no qualifying supers", output)

    def test_finisher_accepts_observed_qualifying_super(self):
        def child(*args, **kwargs):
            (self.root / "woo/logs/fin/0.log").write_text(
                'super P1 chars/A/A.def st=1000 "Super" cost=1000 atk=1 foe_low=20% need=20% self=90% allies=1 shut=false/false\n',
                encoding="utf-8")
            return subprocess.CompletedProcess([], 0, stdout=self.envelope(), stderr=b"")

        with patch("subprocess.run", side_effect=child):
            code, output = self.script("finisher_check", ["--n", "1"])
        self.assertEqual(code, 0, output)
        self.assertIn("supers: 1;", output)
        self.assertIn("violations: 0", output)

    def test_ladder_and_ab_count_engine_results_and_draws(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, self.envelope((1, 0, 2)), b"")):
            code, output = self.script("ladder", ["--matches", "1"])
            self.assertEqual(code, 0, output)
            self.assertIn("3 rounds", output)
            self.assertEqual([ln.split("\t")[2] for ln in (self.root / "woo/roster/ladder_rounds.tsv").read_text().splitlines()],
                             ["1", "0", "2"])
            code, output = self.script("ab_test", ["--n", "2"])
            self.assertEqual(code, 0, output)
            self.assertIn("TOTAL rounds brain 2 - own AI 2", output)

    @staticmethod
    def envelope(rounds=(1,)):
        return ("MATCH_RESULT: " + json.dumps({"rounds": list(rounds), "bounded": False, "elapsed": 1}) + "\n").encode()


if __name__ == "__main__":
    unittest.main()
