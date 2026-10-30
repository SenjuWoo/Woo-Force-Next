"""Run one Ikemen GO test match with the Woo brain and print what happened.

Boots a tiny test motif (only the fighters in this match) so a test takes seconds
instead of preloading the whole 1,640-character roster.

usage: python run_match.py --p1 "chars/X/X.def" --p2 "chars/Y/Y.def" [--p3 ... --p8]
                           [--stage stages/S.def] [--rounds 1] [--speed 1] [--shot 10]
                           [--woo-p1 own|pilot] ... [--woo off] [--timeout 240]
Team sides: odd players = side 1, even = side 2 (2v2 = --p1 --p2 --p3 --p4).
"""
import argparse
import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import winpark
from match_results import PREFIX, round_winners

ROOT = Path(__file__).resolve().parents[2]
TEST = ROOT / "woo" / "test"

ap = argparse.ArgumentParser()
for i in range(1, 9):
    ap.add_argument(f"--p{i}")
    ap.add_argument(f"--woo-p{i}", choices=["own", "pilot", "overmind"])
    ap.add_argument(f"--pal{i}", help="palette slot for player N (palette checks)")
ap.add_argument("--stage", default="stages/Amegakure 1.0.def")
ap.add_argument("--rounds", default="1")
ap.add_argument("--speed", default="")
ap.add_argument("--shot", default="")
ap.add_argument("--woo", default="")
ap.add_argument("--timeout", type=int, default=240)
ap.add_argument("--probe", action="store_true")
ap.add_argument("--guardtest", action="store_true")
ap.add_argument("--trace", default="")
ap.add_argument("--time", default="")
ap.add_argument("--log", default="woo/logs/brain.log")
ap.add_argument("--module", default="", help="Lua test module loaded only in the sandbox config")
ap.add_argument("--shot1", nargs="?", const="1", default="", help="screenshot N ticks into the fight (default 120)")
ap.add_argument("--endafter", default="", help="end the match N ticks into the fight (crash scan)")
ap.add_argument("--visible", action="store_true", help="leave sound on and the window normal")
ap.add_argument("--box", default="", help="sandbox folder name (default: the log's stem)")
ap.add_argument("--tmode1", default="", help="side 1 team mode: 0 single, 1 simul, 2 turns, 3 tag")
ap.add_argument("--tmode2", default="", help="side 2 team mode")
a = ap.parse_args()
if not a.p1 or not a.p2:
    ap.error("--p1 and --p2 are required")

players = [(i, getattr(a, f"p{i}")) for i in range(1, 9) if getattr(a, f"p{i}")]
# one sandbox per log stream, so parallel runs (health sweep + A/B) never share files
TEST = TEST / (a.box or Path(a.log).stem)
TEST.mkdir(parents=True, exist_ok=True)
rel = TEST.relative_to(ROOT).as_posix()
SHOTS = TEST / "shots"
SHOTS.mkdir(exist_ok=True)
motif = TEST / "motif"
if not motif.exists():
    shutil.copytree(ROOT / "data" / "ikemen1", motif)
(motif / "select.def").write_text(
    "[Characters]\r\n" + "".join(f"{p}\r\n" for _, p in dict.fromkeys(players)) +
    "\r\n[ExtraStages]\r\n" + a.stage + "\r\n\r\n[Options]\r\n", encoding="utf-8")
cfg = (ROOT / "save" / "config.ini").read_text(encoding="utf-8")
# Legacy Turns replaces P1/P2 between rounds instead of loading reserve player slots.
legacy_turns = re.search(r"(?m)^TurnsLoading\s*=\s*0\s*(?:;.*)?$", cfg)
result_players = [i for i, _ in players if i <= 2 or not
                  (legacy_turns and getattr(a, f"tmode{2 - i % 2}") == "2")]
cfg = re.sub(r"(?m)^Motif\s*=.*$", f"Motif = {rel}/motif/system.def", cfg)
if a.module:
    cfg = re.sub(r"(?m)^Modules\s*=.*$", lambda _: f"Modules = {a.module}", cfg)
# tests are independent of the player's display settings (crop boxes assume 1920x1080)
cfg = re.sub(r"(?m)^GameWidth\s*=.*$", "GameWidth = 1920", cfg)
cfg = re.sub(r"(?m)^GameHeight\s*=.*$", "GameHeight = 1080", cfg)
cfg = re.sub(r"(?m)^Framerate\s*=.*$", "Framerate = 60", cfg)
cfg = re.sub(r"(?m)^Fullscreen\s*=.*$", "Fullscreen = 0", cfg)
cfg = re.sub(r"(?m)^ScreenshotFolder\s*=.*$", f"ScreenshotFolder = {rel}/shots/", cfg)
if not (a.shot or a.shot1 or a.visible):
    # log-only runs: a small window in the corner instead of a 1920x1080 one over the user's desktop
    # (screenshots read the window size, image.go captureScreen, so those keep the full window)
    cfg = re.sub(r"(?m)^WindowWidth\s*=.*$", "WindowWidth = 384", cfg)
    cfg = re.sub(r"(?m)^WindowHeight\s*=.*$", "WindowHeight = 216", cfg)
    cfg = re.sub(r"(?m)^WindowCentered\s*=.*$", "WindowCentered = 0", cfg)
(TEST / "config.ini").write_text(cfg, encoding="utf-8")

log = ROOT / a.log
start = log.stat().st_size if log.exists() else 0
stats = TEST / "last_match.txt"
stats.unlink(missing_ok=True)
# Each sample starts from the player's baseline and learns only inside its own sandbox.
learned = ROOT / "woo/roster/gate_learned.tsv"
(TEST / "gate_learned.tsv").write_bytes(learned.read_bytes() if learned.exists() else b"")
args = [str(ROOT / "Ikemen_GO.exe"), "-config", f"{rel}/config.ini", "-loadmotif",
        "-s", a.stage, "-rounds", a.rounds, "-log", f"{rel}/last_match.txt", "-windowed",
        "-woo.learned", f"{rel}/gate_learned.tsv"]
if not a.visible:
    args.append("-nosound")
for i, p in players:
    args += [f"-p{i}", p, f"-p{i}.ai", "8"]
    if getattr(a, f"pal{i}"):
        args += [f"-p{i}.pal", getattr(a, f"pal{i}")]
    if getattr(a, f"woo_p{i}"):
        args += [f"-woo.p{i}", getattr(a, f"woo_p{i}")]
for side in ("1", "2"):
    if getattr(a, f"tmode{side}"):
        args += [f"-tmode{side}", getattr(a, f"tmode{side}")]
if a.speed:
    args += ["-speedtest", a.speed]
if a.shot:
    args += ["-woo.shot", a.shot]
if a.woo:
    args += ["-woo", a.woo]
args += ["-woo.verbose", "1"]
if a.probe:
    args += ["-woo.probe", "1"]
if a.guardtest:
    args += ["-woo.guardtest", "1"]
if a.trace:
    args += ["-woo.trace", a.trace]
if a.time:
    args += ["-time", a.time]
args += ["-woo.log", a.log]
if a.shot1:
    args += ["-woo.shot1", a.shot1]
if a.endafter:
    args += ["-woo.endafter", a.endafter]

t0 = time.time()
# Ikemen prints "Panic: <error>" to stderr, then blocks on a modal error dialog: watch stderr and
# kill on panic instead of waiting out the timeout. Per process, so parallel runs never mix errors.
# don't take focus from whatever the user is doing (SDL 2.0.22+ hint, read from the environment)
env = winpark.no_focus_env()
proc = subprocess.Popen(args, cwd=ROOT, stderr=subprocess.PIPE, env=env)
panic = []
if not a.visible:
    winpark.park(proc)  # off the user's desktop; renders and screenshots as usual


def watch():
    for raw in proc.stderr:
        ln = raw.decode("utf-8", "replace").rstrip()
        if ln.startswith("Panic:") or panic and not ln.startswith(("Go stack", "goroutine")):
            panic.append(ln)
        if panic and ln.startswith(("Go stack", "goroutine")):
            break


watcher = threading.Thread(target=watch, daemon=True)
watcher.start()
status = None
try:
    while status is None:
        try:
            proc.wait(timeout=0.5)
            status = f"exit {proc.returncode}"
        except subprocess.TimeoutExpired:
            if panic:
                status = "PANIC (killed)"
            elif time.time() - t0 > a.timeout:
                status = "TIMEOUT (killed)"
finally:
    if proc.poll() is None:
        proc.kill()
    proc.wait()  # reap timeout/panic/interrupt children before the next sample starts
    watcher.join(timeout=2)
    proc.stderr.close()
print(f"match {status} after {time.time() - t0:.0f}s")
if panic:
    head = panic[:next((i for i, x in enumerate(panic) if x.startswith("stack traceback")), len(panic))]
    msg = " | ".join(x for x in head if x).replace("Panic: ", "", 1)
    print("ENGINE PANIC:", msg[:600])
text = ""
if log.exists():
    with open(log, "rb") as f:
        f.seek(start)
        text = f.read().decode("utf-8", "replace")
        print(text, end="")
stats_text = ""
if stats.exists():
    stats_text = stats.read_text(encoding="utf-8", errors="replace")
    print(stats_text[:3000])
for p in sorted((ROOT / "save" / "logs").glob("*.log")):
    if p.stat().st_mtime > t0:
        print("ENGINE ERROR LOG:", p)
        print(p.read_text(encoding="utf-8", errors="replace")[:1500])
if panic or proc.returncode != 0 or status != "exit 0":
    raise SystemExit(1)
try:
    if "ERROR (brain off;" in text:
        raise ValueError("brain disabled itself after a runtime error")
    # A bounded loader test may intentionally stop before any round completes.
    bounded = bool(a.endafter) and all(re.search(rf"loadcheck ok P{i} ", text) for i in result_players)
    if bounded:
        winners = []
    else:
        last = re.search(r'\["LastRound"\] => (\d+)', stats_text)
        if not last:
            raise ValueError("missing native match completion stats")
        winners = round_winners(text, result_players, int(last.group(1)))
        wins = re.search(r'\["Wins"\] => table:[^\n]*\{([^}]+)\}', stats_text)
        totals = {int(side): int(n) for side, n in re.findall(r"\[(\d+)\] => (\d+)", wins.group(1))} if wins else {}
        # Turns teams need one win per opposing member; other modes use --rounds.
        needed = {side: (sum(i % 2 != side % 2 for i, _ in players)
                         if getattr(a, f"tmode{3 - side}") == "2" else int(a.rounds)) for side in (1, 2)}
        if not any(totals.get(side, 0) >= needed[side] for side in (1, 2)):
            raise ValueError("match stopped before the required wins")
except ValueError as exc:
    print("MATCH FAILED:", exc)
    raise SystemExit(1) from None
print(PREFIX + json.dumps({"rounds": winners, "bounded": bounded, "elapsed": round(time.time() - t0, 3)}))
