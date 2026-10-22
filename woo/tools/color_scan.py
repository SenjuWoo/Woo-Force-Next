"""Roster-wide in-fight colour check: which characters draw as black/garbled silhouettes in Ikemen?

Phase 1 (--shoot): every select.def character in pairs, one screenshot at fight tick 2 (start stance,
fixed start positions), body crops saved to woo/test/colorscan/crops/. 3 matches run at once. Resumable.
Phase 2 (--judge): per side, the per-pixel median over all crops is the empty stage; pixels that differ
from it are the fighter. Flags fighters whose pixels are mostly near-black or have almost no colour, and
writes woo/roster/color_scan.csv plus a contact sheet of the flagged ones for review.

usage: python color_scan.py --shoot [--only SUBSTR]   then   python color_scan.py --judge
"""
import argparse
import csv
import queue
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "woo" / "test" / "colorscan"
CROPS = OUT / "crops"
BOX = {1: (400, 480, 960, 1080), 2: (960, 480, 1520, 1080)}  # 1920x1080, 4:3 stage, start positions

ap = argparse.ArgumentParser()
ap.add_argument("--shoot", action="store_true")
ap.add_argument("--judge", action="store_true")
ap.add_argument("--only", default="")
ap.add_argument("--jobs", type=int, default=3)
a = ap.parse_args()


def roster():
    out, section = [], ""
    for ln in (ROOT / "data" / "select.def").read_text(encoding="utf-8").splitlines():
        s = ln.split(";", 1)[0].strip()
        if s.startswith("["):
            section = s.lower()
            continue
        if section != "[characters]" or not s:
            continue
        e = s.split(",")[0].strip().replace("\\", "/")
        if e.lower() in ("randomselect", "skipslot", "dummyslot", "empty"):
            continue
        if not e.lower().endswith(".def"):
            e = f"{e}/{Path(e).name}.def"
        if not e.lower().startswith(("chars/", "chars_fix/")):
            e = "chars/" + e
        out.append(e)
    return list(dict.fromkeys(out))


defs = roster()
index = {d: i for i, d in enumerate(defs)}
SLOTS = queue.Queue()
for k in range(a.jobs):
    SLOTS.put(k)


def shoot(pair):
    slot = SLOTS.get()
    try:
        shots = ROOT / "woo" / "test" / f"cs{slot}" / "shots"
        for p in shots.glob("*.png") if shots.exists() else []:
            p.unlink()
        subprocess.run([sys.executable, str(ROOT / "woo" / "tools" / "run_match.py"), "--p1", pair[0], "--p2", pair[1],
                        "--rounds", "1", "--time", "10", "--shot1", "2", "--endafter", "10", "--timeout", "150",
                        "--log", f"woo/logs/cs{slot}.log"], cwd=ROOT, capture_output=True)
        got = sorted(shots.glob("*.png")) if shots.exists() else []
        if got:
            im = Image.open(got[-1]).convert("RGB")
            if im.size == (1920, 1080):
                for side, d in ((1, pair[0]), (2, pair[1])):
                    im.crop(BOX[side]).resize((280, 300)).save(CROPS / f"{index[d]:04d}_{side}.png")
        return pair
    finally:
        SLOTS.put(slot)


if a.shoot:
    CROPS.mkdir(parents=True, exist_ok=True)
    have = {int(p.stem.split("_")[0]) for p in CROPS.glob("*.png")}
    todo = [d for d in defs if index[d] not in have and a.only.lower() in d.lower()]
    pairs = [(todo[i], todo[i + 1] if i + 1 < len(todo) else "chars/Kiryu/Kiryu.def") for i in range(0, len(todo), 2)]
    if pairs and pairs[-1][1] not in index:
        index[pairs[-1][1]] = index.get(pairs[-1][1], -1)
    print(len(todo), "characters,", len(pairs), "matches", flush=True)
    with ThreadPoolExecutor(a.jobs) as ex:
        for n, p in enumerate(ex.map(shoot, pairs), 1):
            if n % 25 == 0:
                print(n, "/", len(pairs), flush=True)

if a.judge:
    rows, sheet = [], []
    for side in (1, 2):
        files = sorted(CROPS.glob(f"*_{side}.png"))
        stack = np.stack([np.asarray(Image.open(f), dtype=np.int16) for f in files[:400]])
        bg = np.median(stack, axis=0)
        for f in files:
            im = np.asarray(Image.open(f), dtype=np.int16)
            mask = np.abs(im - bg).sum(axis=2) > 90
            n = int(mask.sum())
            px = im[mask]
            if n < 300:
                rows.append((f.stem, n, 0, 0, "tiny"))
                continue
            luma = px @ np.array([0.299, 0.587, 0.114])
            sat = px.max(axis=1) - px.min(axis=1)
            black = float((luma < 28).mean())
            colorful = float((sat > 40).mean())
            verdict = "BLACK" if black > 0.55 else ("GREY" if colorful < 0.04 and black > 0.2 else "ok")
            rows.append((f.stem, n, round(black, 2), round(colorful, 2), verdict))
            if verdict != "ok":
                sheet.append(f)
    with open(ROOT / "woo" / "roster" / "color_scan.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["def", "side", "fighter_px", "black_share", "colour_share", "verdict"])
        for stem, n, black, colorful, verdict in rows:
            i, side = (int(x) for x in stem.split("_"))
            w.writerow([defs[i] if 0 <= i < len(defs) else "?", side, n, black, colorful, verdict])
    for t in range(0, len(sheet), 40):
        tile = Image.new("RGB", (280 * 8, 330 * 5), (30, 30, 30))
        for k, f in enumerate(sheet[t:t + 40]):
            tile.paste(Image.open(f), (280 * (k % 8), 330 * (k // 8)))
            from PIL import ImageDraw
            i = int(f.stem.split("_")[0])
            ImageDraw.Draw(tile).text((280 * (k % 8) + 4, 330 * (k // 8) + 302),
                                      Path(defs[i]).parent.name[:38], fill=(255, 255, 0))
        tile.save(OUT / f"flagged_{t // 40:02d}.png")
    print(len(sheet), "flagged of", len(rows), "-> woo/roster/color_scan.csv, contact sheets in", OUT)
