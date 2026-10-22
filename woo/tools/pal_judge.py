"""Judge every palette candidate of pal_scan.tsv by numbers, then render only the suspects for one look.

pal_scan.py only proposes candidates (a magenta/green key colour is also a legit recolour). Rendering each slot and
diffing it against pal 1 does not work (two shots of the SAME slot differ by 30k-300k pixels: the AI moves, the stage
animates), so this reads the pixels the stand sprites really use (sff_read) and compares what the slot's act paints on
them with what pal 1 paints:
  MAG    >= 10% of the body is pure magenta (255,0,255): the key colour, never a real recolour
  FLAT   the slot keeps <= 50% of pal 1's distinct colours (colours holding >= 0.5% of the body, 4 bits per channel):
         a flat silhouette, like millarca pal12; pal 1 must have >= 6 to compare
  BLACK  >= 60% of the body is near black where pal 1 is not
  NEON   >= 60% of the body is a neon / pure green flood AND the palette collapsed (flat <= 0.8): a shaded neon
         recolour keeps its colours (Muk pal3) and is real
Slots with any flag are suspects. --render shoots them (and pal 1 beside) into woo/test/paljudge/suspects_N.png.

usage: python pal_judge.py [--render] [--only SUBSTR] [--jobs 4]      result: woo/roster/pal_judge.tsv
"""
import argparse
import csv
import queue
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

from sff_read import def_files, v1_body, v2_body

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "woo" / "test" / "paljudge"
RES = ROOT / "woo" / "roster" / "pal_judge.tsv"
CROP = (580, 440, 1140, 1000)  # P1 starts near x=870 of the 1920x1080 frame (same as pal_sheet.py)
FOE = "chars_fix/Zoro (Wano)/Zoro (Wano).def"
MAGENTA = (255, 0, 255)

ap = argparse.ArgumentParser()
ap.add_argument("--render", action="store_true", help="shoot the suspects and tile them")
ap.add_argument("--only", default="")
ap.add_argument("--jobs", type=int, default=4)
ap.add_argument("--src", default=str(ROOT / "woo" / "roster" / "pal_scan.tsv"))
ap.add_argument("--def-root", default="chars", help="folder the pal_scan paths point into (originals, not overlays)")
a = ap.parse_args()


def act_colors(raw: bytes):
    raw = raw[:768].ljust(768, b"\0")
    return [tuple(raw[3 * (255 - k):3 * (255 - k) + 3]) for k in range(256)]  # what Ikemen draws


def neon(c):
    return max(c) >= 200 and min(c) <= 60 and max(c) - min(c) >= 190


def distinct(cols, used, tot):  # colours (4 bits per channel) holding >= 0.5% of the body
    n = Counter()
    for k, v in used.items():
        n[(cols[k][0] >> 4, cols[k][1] >> 4, cols[k][2] >> 4)] += v
    return sum(1 for v in n.values() if v >= 0.005 * tot)


cand = {}
for r in csv.DictReader(open(a.src, encoding="utf-8"), delimiter="\t"):
    if r["verdict"] == "JUNK" and a.only.lower() in r["def"].lower():
        cand.setdefault(r["def"], []).append(int(r["pal"][3:]))
rows = []
for d, pals in cand.items():
    dp = ROOT / d
    f = def_files(dp)
    sff = dp.parent / f.get("sprite", "")
    if not sff.is_file():
        continue
    b = sff.read_bytes()
    base, used = v2_body(b) if b[15] == 2 else v1_body(sff)
    if not base or not used:
        continue
    tot = sum(used.values())
    acts = {}
    for k, v in f.items():
        if k.startswith("pal") and k[3:].isdigit() and v and (dp.parent / v).is_file():
            acts[int(k[3:])] = act_colors((dp.parent / v).read_bytes())
    ref = acts.get(1) or base
    dref = max(1, distinct(ref, used, tot))
    for n in pals:
        cols = acts.get(n)
        if not cols:
            continue
        mag = sum(c for k, c in used.items() if cols[k] == MAGENTA and ref[k] != MAGENTA) / tot
        nn = sum(c for k, c in used.items() if neon(cols[k]) and not neon(ref[k])) / tot
        blk = sum(c for k, c in used.items() if max(cols[k]) < 40 and max(ref[k]) >= 40) / tot
        flat = distinct(cols, used, tot) / dref
        flags = [x for x, on in (("MAG", mag >= 0.10), ("FLAT", dref >= 6 and flat <= 0.50), ("BLACK", blk >= 0.60),
                                 ("NEON", nn >= 0.60 and flat <= 0.80)) if on]
        rows.append((d, n, mag, nn, blk, flat, " ".join(flags)))
with open(RES, "w", encoding="utf-8", newline="") as out:
    out.write("def\tpal\tmagenta\tneon\tblack\tflat_ratio\tflags\n")
    for d, n, mag, nn, blk, flat, fl in rows:
        out.write(f"{d}\t{n}\t{mag:.2f}\t{nn:.2f}\t{blk:.2f}\t{flat:.2f}\t{fl}\n")
sus = [r for r in rows if r[6]]
print(f"{len(rows)} candidate slots judged, {len(sus)} suspects -> {RES}")
for d, n, mag, nn, blk, flat, fl in sus[:60]:
    print(f"  {Path(d).stem[:30]:<30} pal{n:<2} {fl:<10} mag {mag:.2f} neon {nn:.2f} black {blk:.2f} flat {flat:.2f}")
if not a.render:
    sys.exit()

# ------------------------------------------------------------ render the suspects: pal 1 tile + slot tile each
OUT.mkdir(parents=True, exist_ok=True)
boxes = queue.Queue()
for k in range(a.jobs):
    boxes.put(k)


def shoot(job):
    d, pal = job
    d = "chars_fix/" + d.split("/", 1)[1] if d.startswith("chars/") else d
    k = boxes.get()
    try:
        box = f"pj{k}"
        shots = ROOT / "woo" / "test" / box / "shots"
        for f in shots.glob("*.png"):
            f.unlink()
        subprocess.run([sys.executable, str(ROOT / "woo/tools/run_match.py"), "--p1", d, "--p2", FOE, "--pal1", str(pal),
                        "--shot1", "20", "--endafter", "60", "--log", f"woo/logs/{box}.log", "--timeout", "150"],
                       cwd=ROOT, capture_output=True, text=True)
        png = sorted(shots.glob("*.png"))
        return Image.open(png[-1]).convert("RGB").crop(CROP).resize((240, 240)) if png else None
    finally:
        boxes.put(k)


jobs = sorted({(r[0], 1) for r in sus} | {(r[0], r[1]) for r in sus})
with ThreadPoolExecutor(a.jobs) as ex:
    shots = dict(zip(jobs, ex.map(shoot, jobs)))
per = 8  # suspects per image: 4 columns x 2 rows, each cell = pal 1 | slot
for i in range(0, len(sus), per):
    chunk = sus[i:i + per]
    sheet = Image.new("RGB", (4 * 480, 2 * 240), (30, 30, 30))
    dr = ImageDraw.Draw(sheet)
    for j, (d, n, mag, nn, blk, flat, fl) in enumerate(chunk):
        x, y = (j % 4) * 480, (j // 4) * 240
        for off, key in ((0, (d, 1)), (240, (d, n))):
            if shots.get(key):
                sheet.paste(shots[key], (x + off, y))
        dr.text((x + 4, y + 4), f"{Path(d).stem[:26]} 1", fill=(255, 255, 0))
        dr.text((x + 244, y + 4), f"pal{n} {fl}", fill=(255, 80, 80))
    sheet.save(OUT / f"suspects_{i // per}.png")
print("wrote", len(range(0, len(sus), per)), "sheets to", OUT)
