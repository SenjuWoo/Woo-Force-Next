"""Render every palette slot of one character in a real fight and tile them into a contact sheet.

Watch mode and Random picks give each fighter a random palette slot (main.lua f_getUniquePalette), so a
character can look fine on pal 1 and be broken on pal 7. color_scan.py only ever shot the default slot.

usage: python pal_sheet.py "chars_fix/Senju_Tsunade/Senju_Tsunade.def" [--pals 1-10] [--tick 20]
Output: woo/test/palsheet/<name>.png (one tile per slot, slot number in the corner).
"""
import argparse
import queue
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "woo" / "test" / "palsheet"

ap = argparse.ArgumentParser()
ap.add_argument("def_path")
ap.add_argument("--pals", default="1-10", help="slots: 1-10, 1,3 or 1,4-6")
ap.add_argument("--tick", default="20")
ap.add_argument("--jobs", type=int, default=3)
ap.add_argument("--crop", default="580,440,1140,1000", help="x0,y0,x1,y1 of the 1920x1080 frame (P1 starts near x=870)")
ap.add_argument("--foe", default="chars_fix/Zoro (Wano)/Zoro (Wano).def")
a = ap.parse_args()
pals = []
for part in a.pals.split(","):  # "1-10", "1,3", "1,4-6"
    lo, _, hi = part.partition("-")
    pals += range(int(lo), int(hi or lo) + 1)
name = Path(a.def_path).stem
OUT.mkdir(parents=True, exist_ok=True)
slots = queue.Queue()
for k in range(a.jobs):
    slots.put(k)


def shoot(pal):
    k = slots.get()
    try:
        box = f"pal{k}"
        shots = ROOT / "woo" / "test" / box / "shots"
        for f in shots.glob("*.png"):
            f.unlink()
        subprocess.run([sys.executable, str(ROOT / "woo/tools/run_match.py"), "--p1", a.def_path, "--p2", a.foe,
                        "--pal1", str(pal), "--shot1", a.tick, "--endafter", str(int(a.tick) + 40),
                        "--log", f"woo/logs/{box}.log", "--timeout", "150"],
                       cwd=ROOT, capture_output=True, text=True)
        png = sorted(shots.glob("*.png"))
        if not png:
            return pal, None
        im = Image.open(png[-1]).convert("RGB")
        return pal, im.crop(tuple(int(v) for v in a.crop.split(","))).resize((360, 360))
    finally:
        slots.put(k)


with ThreadPoolExecutor(a.jobs) as ex:
    res = list(ex.map(shoot, pals))
w = h = 360
cols = 5
rows = (len(res) + cols - 1) // cols
sheet = Image.new("RGB", (cols * w, rows * h), (30, 30, 30))
d = ImageDraw.Draw(sheet)
for i, (pal, im) in enumerate(res):
    x, y = (i % cols) * w, (i // cols) * h
    if im:
        sheet.paste(im, (x, y))
    d.text((x + 6, y + 6), f"pal {pal}" + ("" if im else " NO SHOT"), fill=(255, 255, 0))
dst = OUT / f"{name}.png"
sheet.save(dst)
print("wrote", dst)
