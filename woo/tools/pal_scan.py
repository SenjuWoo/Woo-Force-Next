"""Find palette slots (palN = x.act) whose act is junk for the character: placeholder colours where the body is.

Why: watch mode and Random give every fighter a random palette slot (main.lua f_getUniquePalette), but
color_scan.py / act_orient.py only judged the default one. Senju Tsunade ships pal1 correct and Basic_2..10.act
copied from another character: magenta (255,0,255) / green (0,255,0) at the indices her body uses, so a random
slot draws silver hair, green skin and pink trousers.

Detection per palN (N > 1): over the stand sprites (0,0..0,9) of the base palette, the share of pixels whose
act colour is pure magenta (key colour, --min) or pure green (only when >= 30%: smaller shares are real
recolours) while the reference (pal1, else the SFF base palette) is not.
--fix (curated JUNK list) comments those palN lines out of the overlay def (chars_fix/<char>/<def>, same overlay mechanism as
act_orient.py), so the slot does not exist and the random pick can never choose it.

usage: python pal_scan.py [--fix] [--only SUBSTR] [--min 0.02]     report: woo/roster/pal_scan.tsv
"""
import argparse
import csv
from pathlib import Path

from sff_read import def_files, v1_body, v2_body

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "chars_fix"
MARK = "; woo-overlay: palette slots (woo/tools/pal_scan.py)"
MAGENTA, GREEN = (255, 0, 255), (0, 255, 0)
GREEN_MIN = 0.30  # pure green is also a real recolour (Noel_S pal8: 6%, Ryuko pal4: 3%): only a flood is a placeholder

# Render-verified junk slots (pal_sheet.py, 2026-10-15). The scan only proposes candidates: pure green/magenta
# is also a real recolour (O27_RiasG pal2 is a legit pink, Goku Black Migatte / Vegetto DBH JUS render fine).
# Gogeta_Blue is in act_orient.FORCE_DROP instead (every slot is a neon silhouette: acts dropped, SFF palette used).
JUNK = {"Senju_Tsunade": range(2, 11),  # Basic_2..10.act belong to another character: silver hair, pink trousers
        "EGX-Daishinkan": {2},          # pal2 draws a green/purple wireframe
        "millarca": {12}}               # pal12 is a flat yellow silhouette (2026-10-21 render; GG/pafio gold slots are shaded: kept)

ap = argparse.ArgumentParser()
ap.add_argument("--fix", action="store_true", help="apply the curated JUNK list (not the scan's candidates)")
ap.add_argument("--only", default="")
ap.add_argument("--min", type=float, default=0.02, help="placeholder pixel share that marks a slot as junk")
a = ap.parse_args()


def is_neon(c):  # a flood of these is a debug palette (Gogeta_Blue pal3-5: whole body one neon green)
    return max(c) >= 200 and min(c) <= 60 and max(c) - min(c) >= 190


def act_colors(raw: bytes):
    raw = raw[:768].ljust(768, b"\0")
    return [tuple(raw[3 * (255 - k):3 * (255 - k) + 3]) for k in range(256)]  # what Ikemen draws


def scan(def_rel: str):
    dp = ROOT / def_rel
    f = def_files(dp)
    sff = dp.parent / f.get("sprite", "")
    if not sff.is_file():
        return []
    b = sff.read_bytes()
    if b[:12] != b"ElecbyteSpr\0":
        return []
    base, used = v2_body(b) if b[15] == 2 else v1_body(sff)
    if not base or not used:
        return []
    tot = sum(used.values())
    pals = {}
    for k, v in f.items():
        if k.startswith("pal") and k[3:].isdigit() and v and (dp.parent / v).is_file():
            pals[int(k[3:])] = act_colors((dp.parent / v).read_bytes())
    ref = pals.get(1) or base
    out = []
    for n, cols in sorted(pals.items()):
        if n == 1:
            continue
        mag = sum(c for k, c in used.items() if cols[k] == MAGENTA and ref[k] != MAGENTA) / tot
        grn = sum(c for k, c in used.items() if cols[k] == GREEN and ref[k] != GREEN) / tot
        neon = sum(c for k, c in used.items() if is_neon(cols[k]) and not is_neon(ref[k])) / tot
        out.append((n, max(mag, neon, grn if grn >= GREEN_MIN else 0.0)))
    return out


def fix(def_rel: str, drop: set) -> str:
    dp = ROOT / def_rel
    rel_dir = Path(def_rel).parent
    out_dir = FIX / Path(*rel_dir.parts[1:])
    old = out_dir / dp.name
    head = old.read_text(encoding="utf-8", errors="replace") if old.is_file() else ""
    if not head.startswith("; woo-overlay"):
        return f"skip: {old.relative_to(ROOT).as_posix()} is another tool's overlay"
    # edit the existing overlay in place: comment out the junk palN lines (works on cache and act overlays)
    lines = []
    for ln in head.splitlines():
        key = ln.split("=", 1)[0].strip().lower() if "=" in ln and not ln.lstrip().startswith(";") else ""
        if key.startswith("pal") and key[3:].isdigit() and int(key[3:]) in drop:
            ln = f";woo: junk palette (placeholder colours on the body) ;{ln}"
        lines.append(ln)
    old.write_text("\r\n".join(lines), encoding="utf-8")
    return "fixed -> " + old.relative_to(ROOT).as_posix()


defs = [r["def"] for r in csv.DictReader(open(ROOT / "woo" / "roster" / "summary.csv", encoding="utf-8"))
        if r["def"].startswith(("chars/", "chars_fix/"))]
defs = ["chars/" + d.split("/", 1)[1] if d.startswith("chars_fix/") else d for d in defs]
defs = [d for d in defs if a.only.lower() in d.lower()]
rep = open(ROOT / "woo" / "roster" / "pal_scan.tsv", "w", encoding="utf-8")
rep.write("def\tpal\tplaceholder_share\tverdict\n")
hits = 0
for d in defs:
    try:
        res = scan(d)
    except Exception as ex:
        print(d, "-> ERROR", type(ex).__name__, ex)
        continue
    junk = {n for n, s in res if s >= a.min}
    for n, s in res:
        rep.write(f"{d}\tpal{n}\t{s:.3f}\t{'JUNK' if n in junk else 'ok'}\n")
    if junk:
        hits += 1
        print(f"{d}: junk {sorted(junk)} of {len(res) + 1} slots (" +
              ", ".join(f"{n}:{s:.0%}" for n, s in res if n in junk) + ")")
rep.close()
if a.fix:
    for folder, pals in JUNK.items():
        d = f"chars/{folder}/{folder}.def"
        print(folder, "->", fix(d, set(pals)))
print(f"{hits} character(s) with junk palette slots ({len(defs)} scanned; woo/roster/pal_scan.tsv)")
