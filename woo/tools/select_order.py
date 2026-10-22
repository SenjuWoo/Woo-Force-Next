"""Order the select screen by fandom and power, keeping the grid's shape.

data/select.def keeps every skipslot where it is (the portrait panels and the grid outline). The other
cells are refilled: all random slots first (the top rows; one random per free slot, so they show how
many characters still fit), then the Jump-style characters, then the ones in another art style (big
CvS/KOF-size sprites, ports and edits: the user's "doesn't fit" block). Each block by fandom in
woo/series.txt [order] order. Jump style = the sprite's fight height (char_cache.tsv, before the size
fix): anime up to 85 px (canon giants any height), other fandoms up to 68 px (Pokemon 85), with the
woo/series.txt [style] fits/out lists for what the numbers get wrong. Inside a
fandom, each character's versions stay together (same woo/lore.txt key); characters go weakest first
by their weakest version's tier, versions weakest first. Ties keep the current order. Repeated entries
of the same .def are dropped (each removal frees a random slot).

usage: python select_order.py [--write]     (without --write: report only)
Run after setup_ikemen.py and roster_analyzer.py (it reads woo/roster/summary.csv and the profiles).
"""
import argparse
import csv
import json
import shutil
import time
from collections import Counter
from pathlib import Path

import roster_analyzer  # load_series, slug

ROOT = Path(__file__).resolve().parents[2]
SEL = ROOT / "data" / "select.def"
ap = argparse.ArgumentParser()
ap.add_argument("--write", action="store_true")
a = ap.parse_args()

order, _, _ = roster_analyzer.load_series(ROOT)
rank = {s: i for i, s in enumerate(order)}
last = rank.get("other anime", len(order))
cache = {r["def"].lower(): r for r in csv.DictReader(open(ROOT / "woo/roster/char_cache.tsv", encoding="utf-8"),
                                                    delimiter="\t")}
style, part, key = {"fits": set(), "out": set()}, None, None
for raw_ln in (ROOT / "woo/series.txt").read_text(encoding="utf-8").splitlines():
    ln = raw_ln.split("#", 1)[0].rstrip()
    if ln.startswith("["):
        part = ln.strip("[] ").lower()
    elif part == "style" and ln.strip():
        if not ln[0].isspace():
            key, ln = (x.strip() for x in ln.split("=", 1))
        style[key.lower()] |= {n.strip().lower() for n in ln.split(",") if n.strip()}


def fits_style(d, series):
    """Jump-style sprite? d: select.def entry path."""
    folder = Path(d).parent.name.lower()
    if folder in style["fits"]:
        return True
    if folder in style["out"]:
        return False
    r = cache.get("chars/" + d.split("/", 1)[1]) or cache.get(d)
    if not r:
        return True
    fh, anime = float(r["fight_h"]), rank.get(series, last + 1) <= last
    if anime:
        return fh <= 85 or bool(r["giant"])
    return 0 < fh <= (85 if series == "pokemon" else 68)
summ = {r["def"].lower(): r for r in csv.DictReader(open(ROOT / "woo/roster/summary.csv", encoding="utf-8"))}

raw = SEL.read_bytes().decode("utf-8")
lines = raw.splitlines()
start = next(i for i, ln in enumerate(lines) if ln.strip().lower() == "[characters]") + 1
end = next((i for i in range(start, len(lines)) if lines[i].strip().startswith("[")), len(lines))
# trailing comment/blank lines before the next section stay where they are
while end > start and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith(";")):
    end -= 1
body = lines[start:end]


def kind(ln):
    k = ln.split(";", 1)[0].split(",", 1)[0].strip().lower()
    return "skip" if k == "skipslot" else "random" if k == "randomselect" else "char" if k else "other"


seen, chars, dups = set(), [], []
for i, ln in enumerate(body):
    if kind(ln) != "char":
        continue
    d = ln.split(",", 1)[0].strip().lower()
    if d in seen:
        dups.append(ln.strip())
        continue
    seen.add(d)
    r = summ.get(d)
    prof = {}
    if r:
        p = ROOT / "woo/roster/chars" / f"{roster_analyzer.slug(r['def'])}.json"
        prof = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    series = r["series"] if r else "original"
    tier = int(r["tier"]) if r and r["tier"].isdigit() else 3
    fam = (prof.get("lore") or {}).get("key") or d
    chars.append({"line": ln, "series": series, "tier": tier, "fam": fam, "pos": i, "fits": fits_style(d, series)})

fam_min = {}
for c in chars:
    k = (c["series"], c["fam"])
    fam_min[k] = min(fam_min.get(k, (9, 1e9)), (c["tier"], c["pos"]))
chars.sort(key=lambda c: (not c["fits"], rank.get(c["series"], last + 0.5), c["series"] if c["series"] not in rank else "",
                          fam_min[(c["series"], c["fam"])], c["tier"], c["pos"]))

slots = sum(1 for ln in body if kind(ln) in ("char", "random"))
randoms = slots - len(chars)
fill = ["randomselect"] * randoms + [c["line"] for c in chars]
new_body, it = [], iter(fill)
for ln in body:
    new_body.append(next(it) if kind(ln) in ("char", "random") else ln)  # blank/comment lines take no cell
assert len(new_body) == len(body) and all(x == y for x, y in zip(new_body, body) if kind(y) in ("skip", "other"))
assert Counter(kind(x) for x in new_body)["char"] == len(chars)

print(f"{slots} slots: {len(chars)} characters, {randoms} random slots, {len(dups)} repeated entries dropped")
for d in dups:
    print("  dropped repeat:", d)
for block in (True, False):
    cs = [c for c in chars if c["fits"] == block]
    by = Counter(c["series"] for c in cs)
    print(f"{'Jump-style' if block else 'other art style'} {len(cs)}:",
          ", ".join(f"{s} {by[s]}" for s in dict.fromkeys(c["series"] for c in cs)))
if a.write:
    bak = ROOT / "woo" / "rollback" / f"select.def.{time.strftime('%Y-%m-%d_%H%M%S')}"
    shutil.copy2(SEL, bak)
    nl = "\r\n" if "\r\n" in raw else "\n"
    out = lines[:start] + new_body + lines[end:]
    SEL.write_bytes((nl.join(out) + (nl if raw.endswith(("\n", "\r")) else "")).encode("utf-8"))
    print(f"written {SEL.relative_to(ROOT)} (backup {bak.relative_to(ROOT)})")
