"""Check the finisher discipline on a batch of random AI-vs-AI matches (woo_brain.lua gatePower).

Every super the brain logs ("super Pn ... cost= foe_low= need= self= allies=") must start when the enemy was
under `need` of its life, or the fighter itself was under ~34%, or a canon partner opened it (allies > 1 and enemy under min(80%, 1.6 x need)).
Anything else is a violation, except a super that started with the brain's own gate open (log "shut=false/false") after
a life swing (a heal): the gate stays open 1.5 s after its reason ended, so those are counted as "tail", not violations.
Also prints when supers happen (histogram of the enemy's life share). Old logs in woo/logs/fin are deleted first.

usage: python finisher_check.py [--n 12] [--seed 21] [--team 1]    log: woo/logs/fin/<i>.log
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
ap.add_argument("--n", type=int, default=12)
ap.add_argument("--seed", type=int, default=21)
ap.add_argument("--team", type=int, default=1)
ap.add_argument("--pairs", default="", help='forced matches "A.def|B.def;C.def|D.def" before the random ones')
a = ap.parse_args()

rows = [r for r in csv.DictReader(open(ROOT / "woo" / "roster" / "summary.csv", encoding="utf-8"))
        if r.get("display") != "FAILED" and r.get("ai") != "none"]
rng = random.Random(a.seed)
forced = [p.split("|") for p in a.pairs.split(";") if p]
for old in (ROOT / "woo" / "logs" / "fin").glob("*.log"):  # a shorter run must not score the previous run's leftovers
    old.unlink()
jobs = [[{"def": d} for d in f] for f in forced] + [rng.sample(rows, 2 * a.team) for _ in range(a.n)]


def run(i):
    picks = jobs[i]
    log = f"woo/logs/fin/{i}.log"
    lp = ROOT / log
    lp.parent.mkdir(exist_ok=True)
    lp.unlink(missing_ok=True)
    args = [sys.executable, str(ROOT / "woo" / "tools" / "run_match.py"), "--rounds", "2", "--speed", "4", "--timeout", "420",
            "--log", log, "--box", "fin" + threading.current_thread().name.rsplit("_", 1)[-1]]
    for k in range(len(picks) // 2):
        args += [f"--p{2 * k + 1}", picks[2 * k]["def"], f"--p{2 * k + 2}", picks[2 * k + 1]["def"]]
    subprocess.run(args, cwd=ROOT, capture_output=True)
    return picks, lp.read_text(encoding="utf-8", errors="replace") if lp.exists() else ""


bad, total, hist, vetoes, tail = [], 0, {}, [], []
# states the brain found need no meter (entered with < 100 power, or leaked twice): not supers, not gated
learned = ROOT / "woo" / "roster" / "gate_learned.tsv"
free = set()
with ThreadPoolExecutor(2) as ex:
    for picks, text in ex.map(run, range(len(jobs))):
        names = " vs ".join(Path(p["def"]).parent.name for p in picks)
        errs = re.findall(r"ERROR.*", text)
        sup = re.findall(r'super P(\d+) (\S.*?) st=(\d+) "(.*?)" cost=(\d+) atk=(\d) foe_low=(\d+)% need=(\d+)% self=(\d+)% allies=(\d+).*? shut=(\w+)/', text)
        for pn, d, st, name, cost, atk, foe, need, me, allies, shut in sup:
            if learned.exists():  # re-read for every super: a state freed during the run is not a violation
                free.update(tuple(ln.split("	")[::2]) for ln in learned.read_text(encoding="utf-8").splitlines()
                            if ln.count("	") == 2 and ln.split("	")[1] == "free")
            # cheap flash moves, buffs/summons, meter-free moves, big supers freed for this match (cancel loop)
            if int(cost) < 1000 or atk != "1" or (d, st) in free or (d, st) in set(re.findall(r'super FREE P\d+ (\S.*?) st=(\d+) ', text)):
                continue
            total += 1
            b = min(int(foe) // 10 * 10, 100)
            hist[b] = hist.get(b, 0) + 1
            team_ok = int(allies) >= 2 and int(foe) <= min(80, int(need) * 1.6) + 3  # partner relay / unison opening
            if int(foe) > int(need) + 3 and int(me) > 34 and not team_ok:
                if shut == "false":  # gate was open by the brain's own count: 1.5 s tail after a heal swing
                    tail.append(f"{names}: P{pn} {Path(d).parent.name} '{name}' cost {cost} at foe {foe}% (needs <= {need}%), self {me}%")
                    continue
                bad.append(f"{names}: P{pn} {Path(d).parent.name} '{name}' cost {cost} at foe {foe}% (needs <= {need}%), self {me}%")
        vt = re.findall(r'super VETO P(\d+) (\S.*?) st=\d+ "(.*?)" cost=(\d+) had=(\d+) .*?-> ceiling (\d+)', text)
        vetoes += [f"{Path(d).parent.name} '{n}' cost {c}: had {h} -> ceiling {k}" for _, d, n, c, h, k in vt]
        print(f"{names}: {len(sup)} supers, {len(vt)} vetoed{'  ERROR ' + errs[0] if errs else ''}")
print(f"supers: {total}; by enemy life share (%): {dict(sorted(hist.items()))}")
print(f"vetoed (cancelled at entry, ceiling learned into woo/roster/gate_learned.tsv): {len(vetoes)}")
for v in vetoes[:12]:
    print("  ", v)
print(f"tail (gate open, life swung back within 1.5 s): {len(tail)}")
for b in tail[:10]:
    print("  ", b)
print(f"violations: {len(bad)}")
for b in bad[:20]:
    print("  ", b)
