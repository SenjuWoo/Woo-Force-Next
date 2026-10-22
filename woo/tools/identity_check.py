"""Watch a batch of random AI-vs-AI matches and report what the identity layer did.

Per match: both fighters' matchup tag and identity moments (FINISHER, ANTI-AIR, ...) from the brain log,
and how many brain-issued moves actually came out (tries vs came_out). Runs 2 matches at a time.

usage: python identity_check.py [--n 8] [--seed 5] [--team 2]   (--team 2/4 = Simul 2v2 / 4v4)
"""
import argparse
import csv
import random
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=8)
ap.add_argument("--seed", type=int, default=5)
ap.add_argument("--team", type=int, default=1)
a = ap.parse_args()

rows = [r for r in csv.DictReader(open(ROOT / "woo" / "roster" / "summary.csv", encoding="utf-8"))
        if r.get("display") != "FAILED"]
rng = random.Random(a.seed)


def run(i):
    picks = rng.sample(rows, 2 * a.team)
    log = f"woo/logs/idc/{i}.log"  # one log per job: two shared logs mixed overlapping jobs' rounds
    args = [sys.executable, str(ROOT / "woo" / "tools" / "run_match.py"), "--rounds", "2", "--timeout", "400", "--log", log,
                    "--box", "idc" + threading.current_thread().name.rsplit("_", 1)[-1]]
    for k in range(a.team):
        args += [f"--p{2 * k + 1}" if a.team > 1 else "--p1", picks[2 * k]["def"]]
        args += [f"--p{2 * k + 2}" if a.team > 1 else "--p2", picks[2 * k + 1]["def"]]
    lp = ROOT / log
    lp.parent.mkdir(exist_ok=True)
    lp.unlink(missing_ok=True)
    start = 0
    subprocess.run(args, cwd=ROOT, capture_output=True)
    text = lp.read_text(encoding="utf-8", errors="replace")[start:] if lp.exists() else ""
    return picks, text


tot_tries = tot_out = 0
moments = {}
with ThreadPoolExecutor(2) as ex:
    for picks, text in ex.map(run, range(a.n)):
        names = " vs ".join(Path(p["def"]).parent.name for p in picks)
        tags = sorted(set(re.findall(r"identity P\d matchup=(\S+)", text)))
        errs = re.findall(r"ERROR.*", text)
        for t, o in re.findall(r"tries=(\d+) came_out=(\d+)", text):
            tot_tries, tot_out = tot_tries + int(t), tot_out + int(o)
        for chunk in re.findall(r"moments: (.*)", text):
            for k, v in re.findall(r"(\S+)=(\d+)", chunk):
                moments[k] = moments.get(k, 0) + int(v)
        print(f"{names}: matchups {','.join(tags) or '-'}{'  ERROR ' + errs[0] if errs else ''}")
print(f"identity moments: {moments}")
print(f"brain-issued moves: {tot_out}/{tot_tries} came out")
