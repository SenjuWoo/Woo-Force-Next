"""Balance ladder: watch many AI-vs-AI matches across power tiers, measure who beats whom.

Samples characters per tier (profiles' `tier`, 1-6), pairs them randomly across tiers, plays 2-round
matches at --speed (2 at a time), takes each round's winner from the engine result flags,
then reports Elo per character and the tier-vs-tier round win rate. The lore order should hold (higher
tier wins more) while upsets still happen: the TIER atk/def multipliers in woo_brain.lua are tuned
against this table. Resumable: results append to woo/roster/ladder_rounds.tsv.

usage: python ladder.py [--per-tier 8] [--matches 120] [--seed 3] [--speed 4] [--jobs 2] [--out FILE] [--report]
"""
import argparse
import csv
import random
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from match_results import checked_result

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "woo" / "roster" / "ladder_rounds.tsv"
ap = argparse.ArgumentParser()
ap.add_argument("--per-tier", type=int, default=8)
ap.add_argument("--matches", type=int, default=120)
ap.add_argument("--seed", type=int, default=3)
ap.add_argument("--speed", default="4")
ap.add_argument("--report", action="store_true", help="only print the report from earlier results")
ap.add_argument("--out", default=str(OUT), help="results file (keep runs of different brain versions apart)")
ap.add_argument("--jobs", type=int, default=2)
a = ap.parse_args()
if not a.report and a.matches < 1:
    ap.error("--matches must be positive")
OUT = Path(a.out)

rows = [r for r in csv.DictReader(open(ROOT / "woo" / "roster" / "summary.csv", encoding="utf-8"))
        if r.get("display") != "FAILED" and r.get("tier", "").isdigit()]
tier_of = {r["def"]: int(r["tier"]) for r in rows}


def play(job):
    i, p1, p2 = job
    log = f"woo/logs/ladder/{i}.log"  # one log per job: two shared logs mixed overlapping jobs' rounds
    lp = ROOT / log
    lp.parent.mkdir(exist_ok=True)
    lp.unlink(missing_ok=True)
    result = checked_result(subprocess.run([sys.executable, str(ROOT / "woo" / "tools" / "run_match.py"), "--p1", p1, "--p2", p2,
                    "--rounds", "2", "--speed", a.speed, "--time", "99", "--timeout", "420", "--log", log,
                    "--box", "ladder" + threading.current_thread().name.rsplit("_", 1)[-1]],
                   cwd=ROOT, capture_output=True))
    return [(p1, p2, winner) for winner in result["rounds"]]


if not a.report:
    rng = random.Random(a.seed)
    by_tier = defaultdict(list)
    for r in rows:
        by_tier[int(r["tier"])].append(r["def"])
    pool = []
    for t, defs in sorted(by_tier.items()):
        pool += rng.sample(defs, min(a.per_tier, len(defs)))
    jobs = []
    for i in range(a.matches):
        p1, p2 = rng.sample(pool, 2)
        jobs.append((i, p1, p2))
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(a.jobs) as ex, open(OUT, "a", encoding="utf-8") as fh:
        for res in ex.map(play, jobs):
            for p1, p2, w in res:
                fh.write(f"{p1}\t{p2}\t{w}\n")
            fh.flush()
            done += 1
            if done % 10 == 0:
                print(f"{done}/{len(jobs)} matches ({time.time() - t0:.0f}s)", flush=True)

# report
games = [ln.rstrip("\n").split("\t") for ln in OUT.read_text(encoding="utf-8").splitlines()] if OUT.exists() else []
if not games:
    raise SystemExit("no completed rounds to report")
elo = defaultdict(lambda: 1500.0)
for _ in range(20):  # iterate to convergence on a small sample
    for p1, p2, w in games:
        e1 = 1 / (1 + 10 ** ((elo[p2] - elo[p1]) / 400))
        s1 = 0.5 if w == "0" else float(w == "1")
        elo[p1] += 8 * (s1 - e1)
        elo[p2] -= 8 * (s1 - e1)
mat = defaultdict(lambda: [0, 0])  # (tier_a, tier_b) -> [wins of a, rounds]
for p1, p2, w in games:
    t1, t2 = tier_of.get(p1), tier_of.get(p2)
    if t1 is None or t2 is None or t1 == t2:
        continue
    hi, lo = (p1, p2) if t1 > t2 else (p2, p1)
    gap = abs(t1 - t2)
    win_hi = 0.5 if w == "0" else (w == "1") == (hi == p1)
    mat[gap][0] += win_hi
    mat[gap][1] += 1
print(f"{len(games)} rounds")
for gap in sorted(mat):
    w, n = mat[gap]
    print(f"  tier gap {gap}: higher tier wins {w}/{n} rounds = {100 * w / n:.0f}%")
tiers = defaultdict(list)
for d, v in elo.items():
    if d in tier_of:
        tiers[tier_of[d]].append(v)
for t in sorted(tiers):
    v = tiers[t]
    print(f"  tier {t}: mean Elo {sum(v) / len(v):.0f} over {len(v)} characters")
