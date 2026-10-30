"""Runtime roster health: short accelerated fights in pairs; checks what a player would see.

For each pair: a 1-round fight on a fixed 4:3 stage at high speed, one screenshot ~2 s in.
  portrait   lifebar face pixels: a one-colour silhouette or blank box = broken
  activity   attack ticks and distance moved per fighter (from the brain log): 0/0 = stands still
  engine     a new engine error log = crash
Writes woo/roster/health_runtime.csv (appends; re-run resumes where it stopped).

usage: python health_run.py [--only SUBSTR] [--limit N] [--speed 6]
"""
import argparse
import csv
import re
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "woo" / "logs" / "health.log"
OUT = ROOT / "woo" / "roster" / "health_runtime.csv"
STAGE = "stages/Amegakure 1.0.def"  # 4:3, so the face boxes below are fixed
FACE = {1: (258, 20, 378, 135), 2: (1543, 20, 1662, 135)}  # 1920x1080 screenshot

ap = argparse.ArgumentParser()
ap.add_argument("--only", default="")
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--speed", default="6")
a = ap.parse_args()


def portrait_state(img: Image.Image, box):
    crop = img.crop(box).convert("RGB").quantize(colors=64)
    hist = sorted(crop.getcolors(), reverse=True)
    total = sum(n for n, _ in hist)
    pal = crop.getpalette()
    # ignore the darkest colour (frame background) when judging the face itself
    rgb = lambda i: pal[i * 3:i * 3 + 3]
    face = [(n, i) for n, i in hist if sum(rgb(i)) > 60]
    if not face or sum(n for n, _ in face) < total * 0.15:
        return "blank"
    top = face[0][0] / sum(n for n, _ in face)
    if top > 0.6 or len(face) < 6:
        return "flat"  # e.g. the green Cell Max silhouette
    return "ok"


rows = [r for r in csv.DictReader(open(ROOT / "woo" / "roster" / "summary.csv", encoding="utf-8"))
        if r["display"] != "FAILED"]
defs = [r["def"] for r in rows if a.only.lower() in r["def"].lower()]
done = set()
if OUT.exists():
    done = {r["def"] for r in csv.DictReader(open(OUT, encoding="utf-8"))}
crashes = set()  # known load crashers (crash_scan.py): a pair with one only tells us that again
if (ROOT / "woo" / "roster" / "crash_scan.csv").exists():
    crashes = {r["def"] for r in csv.DictReader(open(ROOT / "woo" / "roster" / "crash_scan.csv", encoding="utf-8"))
               if r["status"] != "ok"}
todo = [d for d in defs if d not in done and d not in crashes]
if a.limit:
    todo = todo[:a.limit]
new_file = not OUT.exists()
out = open(OUT, "a", newline="", encoding="utf-8")
w = csv.writer(out)
if new_file:
    w.writerow(["def", "slot", "opponent", "portrait", "attack_ticks", "moved", "life_end", "engine_error"])

env_fails, i = 0, 0
while i < len(todo):
    pair = todo[i:i + 2]
    if len(pair) == 1:
        pair.append(defs[0])
    start = LOG.stat().st_size if LOG.exists() else 0
    t0 = time.time()
    SHOTS = ROOT / "woo" / "test" / "health" / "shots"
    shots_before = set(SHOTS.glob("*.png"))
    res = subprocess.run([sys.executable, str(ROOT / "woo" / "tools" / "run_match.py"), "--p1", pair[0], "--p2", pair[1],
                    "--stage", STAGE, "--rounds", "1", "--speed", a.speed, "--time", "30", "--shot1",
                    "--timeout", "120", "--log", "woo/logs/health.log"], cwd=ROOT, capture_output=True)
    with open(LOG, "rb") as f:
        f.seek(start)
        text = f.read().decode("utf-8", "replace")
    act = {}
    for m in re.finditer(r"activity P(\d) act=(\d+) moved=(\d+) life=(-?\d+)", text):
        act[int(m.group(1))] = (int(m.group(2)), int(m.group(3)), int(m.group(4)))
    err = res.returncode != 0  # per process, including panic, timeout and incomplete matches
    # GL/window creation failed (display asleep, session ending): not the characters' fault, retry
    if b"glActiveTexture" in res.stdout or b"Loading module" not in res.stdout:
        env_fails += 1
        print(f"env failure, retry {env_fails}", flush=True)
        if env_fails >= 6:
            print("Ikemen cannot start (GL/window). Stopping; rerun to resume.", flush=True)
            break
        time.sleep(10 * env_fails)
        continue
    env_fails = 0
    i += 2
    shots = sorted(set(SHOTS.glob("*.png")) - shots_before)
    faces = {1: "noshot", 2: "noshot"}
    if shots:
        img = Image.open(shots[-1])
        if img.size == (1920, 1080):
            faces = {k: portrait_state(img, b) for k, b in FACE.items()}
        for s in shots:
            s.unlink(missing_ok=True)
    for slot, d in ((1, pair[0]), (2, pair[1])):
        if d in done:
            continue
        at, mv, lf = act.get(slot, (-1, -1, -1))
        w.writerow([d, slot, pair[2 - slot], faces[slot], at, mv, lf, int(err)])
        done.add(d)
    out.flush()
    print(f"{pair[0][:40]:40} {faces[1]:6} {act.get(1)} | {pair[1][:40]:40} {faces[2]:6} {act.get(2)} "
          f"{'ENGINE ERROR ' if err else ''}({time.time() - t0:.0f}s)", flush=True)
out.close()
