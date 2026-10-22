"""In-fight colour check for chars_fix overlays: overlay (P1) vs the original def (P2), same palette.

Screenshot at fight tick 2 (both in their start stance), crop each fighter and the lifebar face, and tile
the pairs into woo/test/compare/tiles_N.png for review. Runs 3 matches at a time (separate sandboxes).

usage: python overlay_compare.py [--kind sff1|sff2|all] [--only SUBSTR]
"""
import argparse
import queue
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "woo" / "test" / "compare"
MARKS = {"sff1": "; woo-overlay: sff1", "sff2": "; woo-overlay: sff2", "act": "; woo-overlay: act",
         "cache": "; woo-overlay: cache", "cns": "; woo-overlay: cns"}

ap = argparse.ArgumentParser()
ap.add_argument("--kind", default="all")
ap.add_argument("--only", default="")
ap.add_argument("--jobs", type=int, default=3)
ap.add_argument("--tick", default="2", help="fight tick to shoot (later avoids intro flashes)")
a = ap.parse_args()

pairs = []
for ov in sorted((ROOT / "chars_fix").rglob("*.def")):
    head = ov.read_text(encoding="utf-8", errors="replace")[:80]
    kinds = [k for k, m in MARKS.items() if head.startswith(m)]
    if not kinds or a.kind not in ("all", kinds[0]) or not any(o.lower() in str(ov).lower() for o in a.only.split(",")):
        continue
    rel = ov.relative_to(ROOT).as_posix()
    orig = "chars/" + rel.split("/", 1)[1]
    if (ROOT / orig).is_file():
        pairs.append((kinds[0], rel, orig))
print(len(pairs), "overlay/original pairs")
OUT.mkdir(parents=True, exist_ok=True)


SLOTS = queue.Queue()
for k in range(a.jobs):
    SLOTS.put(k)


def shoot(job):
    slot = SLOTS.get()  # one sandbox per running match
    try:
        return _shoot(job, slot)
    finally:
        SLOTS.put(slot)


def _shoot(job, slot):
    i, (kind, ov, orig) = job
    log = f"woo/logs/cmp{slot}.log"
    shots = ROOT / "woo" / "test" / f"cmp{slot}" / "shots"
    for p in shots.glob("*.png") if shots.exists() else []:
        p.unlink()
    subprocess.run([sys.executable, str(ROOT / "woo" / "tools" / "run_match.py"), "--p1", ov, "--p2", orig,
                    "--rounds", "1", "--time", "10", "--shot1", a.tick, "--endafter", str(int(a.tick) + 10), "--timeout", "150",
                    "--log", log], cwd=ROOT, capture_output=True)
    got = sorted(shots.glob("*.png")) if shots.exists() else []
    if not got:
        return i, kind, ov, None
    img = Image.open(got[-1]).convert("RGB")
    dst = OUT / f"{i:03d}.png"
    img.save(dst)
    return i, kind, ov, dst


with ThreadPoolExecutor(a.jobs) as ex:
    results = sorted(ex.map(shoot, enumerate(pairs)))

# tiles: per pair one row = P1 face | P1 body | P2 body | P2 face (overlay left, original right)
rows = []
for i, kind, ov, dst in results:
    row = Image.new("RGB", (1180, 300), (40, 40, 40))
    if dst:
        im = Image.open(dst)
        row.paste(im.crop((258, 20, 378, 135)), (0, 0))
        row.paste(im.crop((400, 480, 960, 1080)).resize((280, 300)), (125, 0))
        row.paste(im.crop((960, 480, 1520, 1080)).resize((280, 300)), (410, 0))
        row.paste(im.crop((1543, 20, 1662, 135)), (695, 0))
    ImageDraw.Draw(row).text((820, 10), f"{i} {kind}\n{Path(ov).parent.name[:40]}", fill=(255, 255, 0))
    rows.append(row)
for t in range(0, len(rows), 8):
    tile = Image.new("RGB", (1180, 300 * len(rows[t:t + 8])))
    for k, r in enumerate(rows[t:t + 8]):
        tile.paste(r, (0, 300 * k))
    tile.save(OUT / f"tiles_{t // 8:02d}.png")
print("tiles:", len(range(0, len(rows), 8)), "in", OUT)
