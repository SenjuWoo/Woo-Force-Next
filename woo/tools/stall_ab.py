"""Stall A/B: how often does a 1-round AI-vs-AI match (infinite round time, like Watch mode) stall,
with the Woo brain on vs every character on its own AI?

usage: python stall_ab.py [--n 8] [--seed 5] [--speed 4] [--jobs 3] [--off-only|--on-only]
Prints, per pair and mode, whether round 1 ended in a KO or a STALL, how long it took (real seconds at
the chosen speed) and the final life shares. Logs: woo/logs/stall/<i>_<on|off>.log.
"""
import argparse
import csv
import random
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=8)
ap.add_argument("--seed", type=int, default=5)
ap.add_argument("--speed", default="4")
ap.add_argument("--jobs", type=int, default=3)
ap.add_argument("--timeout", type=int, default=260)
ap.add_argument("--modes", default="on,off", help="comma list of on,off")
a = ap.parse_args()

rows = [r for r in csv.DictReader(open(ROOT / "woo" / "roster" / "summary.csv", encoding="utf-8"))
        if r["display"] != "FAILED" and r["ai"] != "none" and int(r["moves"]) >= 5]
rnd = random.Random(a.seed)
pairs = [tuple(r["def"] for r in rnd.sample(rows, 2)) for _ in range(a.n)]
out = ROOT / "woo" / "logs" / "stall"
out.mkdir(parents=True, exist_ok=True)
TS = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d):(\d\d):(\d\d) ")


def secs(line):
    m = TS.match(line)
    return int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4)) if m else None


def run(job):
    i, mode, (p1, p2) = job
    log = out / f"{i}_{mode}.log"
    log.unlink(missing_ok=True)
    cmd = [sys.executable, str(ROOT / "woo" / "tools" / "run_match.py"), "--p1", p1, "--p2", p2, "--rounds", "1",
           "--speed", a.speed, "--timeout", str(a.timeout), "--log", str(log.relative_to(ROOT)).replace("\\", "/")]
    if mode == "off":
        cmd += ["--woo", "off"]
    subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    txt = log.read_text(encoding="utf-8", errors="replace").splitlines() if log.exists() else []
    start = next((secs(l) for l in txt if " round 1 start P1" in l), None)
    end = next((secs(l) for l in txt if " round 1 end P1" in l or " round 1 STALL" in l), None)
    stall = any(" round 1 STALL" in l for l in txt)
    lives = {}
    for l in txt:
        m = re.search(r"round 1 end (P\d) \S.*?life=(\d+)", l)
        if m:
            lives[m.group(1)] = int(m.group(2))
    dur = (end - start) if start is not None and end is not None else None
    return i, mode, p1, p2, stall, dur, lives, bool(end)


jobs = [(i, m, p) for i, p in enumerate(pairs) for m in a.modes.split(",")]
with ThreadPoolExecutor(a.jobs) as ex:
    res = sorted(ex.map(run, jobs), key=lambda r: (r[0], r[1]))
name = lambda d: Path(d).stem
tot = {}
for i, mode, p1, p2, stall, dur, lives, ended in res:
    l1, l2 = lives.get("P1"), lives.get("P2")
    kind = "STALL" if stall else ("KO" if ended else "NO-END")
    print(f"{i:>2} {mode:<3} {kind:<6} {str(dur) + 's':>6}  P1 {name(p1)[:22]:<22} life {l1}  P2 {name(p2)[:22]:<22} life {l2}")
    t = tot.setdefault(mode, [0, 0, 0])
    t[0] += 1
    t[1] += stall
    t[2] += (dur or 0)
for mode, (n, st, d) in tot.items():
    print(f"{mode}: {n} matches, {st} stalled ({100 * st // max(1, n)}%), mean real seconds {d // max(1, n)}")
