"""A/B test: Woo brain vs each character's own AI, mirror matches, sides alternating.

usage: python ab_test.py [--n 12] [--seed 1] [--speed 4] [--rounds 2] [--chars "chars/A/A.def,..."]
Prints per-match round wins and a total. Appends results to woo/logs/ab_results.tsv.
"""
import argparse
import csv
import random
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "woo" / "logs" / "brain.log"

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=12)
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--speed", default="4")
ap.add_argument("--rounds", default="2")
ap.add_argument("--time", default="99", help="round timer (config has Time = -1: turtling mirrors never end)")
ap.add_argument("--chars", default="")
ap.add_argument("--mode", default="overmind", choices=["overmind", "pilot"])
a = ap.parse_args()

rows = [r for r in csv.DictReader(open(ROOT / "woo" / "roster" / "summary.csv", encoding="utf-8"))
        if r["display"] != "FAILED" and r["ai"] != "none" and int(r["moves"]) >= 5]
picks = a.chars.split(",") if a.chars else [r["def"] for r in random.Random(a.seed).sample(rows, a.n)]

tot_brain = tot_own = 0
out = open(ROOT / "woo" / "logs" / "ab_results.tsv", "a", encoding="utf-8")
for i, d in enumerate(picks):
    brain_side = 1 if i % 2 == 0 else 2
    own = 2 if brain_side == 1 else 1
    start = LOG.stat().st_size if LOG.exists() else 0
    cmd = [sys.executable, str(ROOT / "woo" / "tools" / "run_match.py"), "--p1", d, "--p2", d, f"--woo-p{brain_side}", a.mode,
           f"--woo-p{own}", "own", "--rounds", a.rounds, "--speed", a.speed, "--timeout", "300", "--time", a.time]
    t0 = time.time()
    subprocess.run(cmd, cwd=ROOT, capture_output=True)
    with open(LOG, "rb") as f:
        f.seek(start)
        text = f.read().decode("utf-8", "replace")
    life = {}
    bw = ow = 0
    for m in re.finditer(r"round (\d+) end P(\d) \S.*? mode=(\w+) life=(-?\d+)", text):
        life.setdefault(m.group(1), {})[m.group(3)] = int(m.group(4))
    for rn, v in life.items():
        if a.mode in v and "coach" in v:
            if v[a.mode] > v["coach"]:
                bw += 1
            elif v["coach"] > v[a.mode]:
                ow += 1
    tot_brain += bw
    tot_own += ow
    print(f"{d:70.70} brain P{brain_side}: {bw}-{ow}  ({time.time() - t0:.0f}s)", flush=True)
    out.write(f"{time.strftime('%Y-%m-%d %H:%M')}\t{d}\t{brain_side}\t{bw}\t{ow}\n")
out.close()
print(f"TOTAL rounds brain {tot_brain} - own AI {tot_own}")
