"""Fix black/blotchy lifebar portraits on SFF v2 characters whose pal1 .act is a partial recolor.

Some recolor edits ship a pal1 .act that only defines the body's colours (e.g. Goku Omni Good: 53 of 256).
The portrait (9000,0 / 9000,1) uses the character palette too, so its other indices come out black.
(Ikemen loads .act reversed, image.go "destIdx := 255 - i"; the same happens in MUGEN.)

Fix (Ikemen side only): chars_fix/<char>/<name>.sff, a copy where the portrait sprites point at an
unused palette slot holding a merged palette (act colour where the act defines one, the SFF's own 1,1
colour elsewhere), plus an overlay def. setup_ikemen.py routes select.def to the overlay.

usage: python sff2_portrait_fix.py --scan            (report only, whole roster)
       python sff2_portrait_fix.py --fix [defs...]   (defs default to every --scan hit)
"""
import argparse
import csv
import io
import struct
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "chars_fix"
MARK = "; woo-overlay: sff2 portrait palette (woo/tools/sff2_portrait_fix.py)"
# Visually checked 2026-10-09: the merged palette mis-coloured these portraits (their act re-uses the
# portrait's indices for other colours), so they get the SFF's own palette instead.
PURE = {"chars/Kaien/Kaien.def", "chars/Toguro/Toguro.def", "chars/King Vegeta/King Vegeta.def",
        "chars/Gyomei V2/Gyomei V2.def", "chars/Brandish/Brandish.def", "chars/Toji/Toji.def"}

ap = argparse.ArgumentParser()
ap.add_argument("--scan", action="store_true")
ap.add_argument("--fix", action="store_true")
ap.add_argument("--threshold", type=float, default=0.3, help="share of portrait pixels turned black")
ap.add_argument("--pure", action="store_true", help="portrait gets the SFF's own 1,1 palette, no act colours")
ap.add_argument("defs", nargs="*")
a = ap.parse_args()


def def_files(path: Path):
    out, on = {}, False
    for ln in path.read_bytes().decode("latin-1").splitlines():
        s = ln.split(";", 1)[0].strip()
        if s.startswith("["):
            on = s.lower().startswith("[files]")
            continue
        if on and "=" in s:
            k, v = s.split("=", 1)
            out.setdefault(k.strip().lower(), v.strip().strip('"'))
    return out


def read_v2(b: bytes):
    if b[:12] != b"ElecbyteSpr\0" or b[15] != 2:
        return None
    spo, nspr, pao, npal, ldo, ldl, tdo, tdl = struct.unpack("<IIIIIIII", b[36:68])
    sprs = [list(struct.unpack("<HHHHhhHBBIIHH", b[spo + 28 * i:spo + 28 * i + 28])) for i in range(nspr)]
    pals = [list(struct.unpack("<HHHHII", b[pao + 16 * i:pao + 16 * i + 16])) for i in range(npal)]
    return {"spo": spo, "pao": pao, "ldo": ldo, "tdo": tdo, "sprs": sprs, "pals": pals}


def pal_rgb(b, h, i):
    g, it, n, ln, off, L = h["pals"][i]
    if L == 0:
        g, it, n, ln, off, L = h["pals"][ln]
    d = b[h["ldo"] + off:h["ldo"] + off + L]
    return [tuple(d[4 * k:4 * k + 3]) for k in range(len(d) // 4)] + [(0, 0, 0)] * (256 - len(d) // 4)


def sprite_indices(b, h, s):
    """palette indices of a PNG8/RLE8/raw sprite, else None"""
    g, it, w, hh, ax, ay, ln, fmt, cd, off, L, pi, fl = s
    if L == 0:
        s = h["sprs"][ln]
        g, it, w, hh, ax, ay, ln, fmt, cd, off, L, pi, fl = s
    base = h["tdo"] if fl & 1 else h["ldo"]
    d = b[base + off + 4:base + off + L]  # 4-byte uncompressed length prefix
    if fmt == 10:
        return list(Image.open(io.BytesIO(d)).getdata())
    if fmt == 0:
        return list(b[base + off:base + off + L])
    if fmt == 2:
        px, i = [], 0
        while i < len(d):
            c = d[i]; i += 1
            if c & 0xC0 == 0x40:
                px += [d[i]] * (c & 0x3F); i += 1
            else:
                px.append(c)
        return px
    return None


def check(def_rel: str):
    """-> (share_black, detail) or None"""
    dp = ROOT / def_rel
    f = def_files(dp)
    sff, act = dp.parent / f.get("sprite", ""), dp.parent / f.get("pal1", "")
    if not sff.is_file() or not act.is_file():
        return None
    b = sff.read_bytes()
    h = read_v2(b)
    if not h:
        return None
    ar = act.read_bytes()[:768]
    if len(ar) < 768:
        return None
    actp = [tuple(ar[3 * (255 - k):3 * (255 - k) + 3]) for k in range(256)]  # Ikemen reverses .act
    base = pal_rgb(b, h, 0)
    worst = (0.0, "")
    for s in h["sprs"]:
        if s[0] != 9000 or s[1] > 1:
            continue
        g, it, *_, pi, fl = s
        if pi != 0:  # own palette: the act does not apply
            continue
        px = sprite_indices(b, h, s)
        if not px:
            continue
        used = [k for k in px if k]
        if not used:
            continue
        black = sum(1 for k in used if actp[k] == (0, 0, 0) and base[k] != (0, 0, 0))
        share = black / len(used)
        if share > worst[0]:
            worst = (share, f"9000,{it}: {share:.0%} of {len(used)} px turn black")
    return worst


def fix(def_rel: str) -> str:
    dp = ROOT / def_rel
    f = def_files(dp)
    sff, act = dp.parent / f["sprite"], dp.parent / f["pal1"]
    b = bytearray(sff.read_bytes())
    h = read_v2(bytes(b))
    users = {s[11] for s in h["sprs"]} | {p[3] for p in h["pals"] if p[5] == 0}  # used or link targets
    ar = act.read_bytes()[:768]
    actp = [tuple(ar[3 * (255 - k):3 * (255 - k) + 3]) for k in range(256)]
    g0 = h["pals"][0] if h["pals"][0][5] else h["pals"][h["pals"][0][3]]
    raw0 = bytes(b[h["ldo"] + g0[4]:h["ldo"] + g0[4] + g0[5]]).ljust(1024, b"\0")
    # SFF 2.0.1.0 palettes carry real alpha (Ikemen reads it): keep the original byte, opaque for act colours
    merged = b"".join(bytes(actp[k]) + bytes([raw0[4 * k + 3] or 255]) if k and actp[k] != (0, 0, 0) and not (a.pure or def_rel in PURE)
                      else raw0[4 * k:4 * k + 4] for k in range(256))
    # An unused palette slot with its own 1024-byte block. palN .act files overwrite slot N-1 at
    # runtime (Ikemen loadActPalettes) and PaletteMax is 100, so only slots >= 100 are safe.
    top = max([int(k[3:]) for k in f if k[:3] == "pal" and k[3:].isdigit()] + [1])
    free = [i for i in range(len(h["pals"]) - 1, top - 1, -1) if i not in users and h["pals"][i][5] >= 1024]
    # prefer >= 100; else any slot above the highest palN this def loads (Megumin: slot 5 works)
    slot = next((i for i in free if i >= 100), free[0] if free else None)
    if slot is None:
        return f"skip: no unused 256-colour palette slot above pal{top}"
    off = h["ldo"] + h["pals"][slot][4]
    b[off:off + 1024] = merged
    n = 0
    for i, s in enumerate(h["sprs"]):
        if s[0] == 9000 and s[1] <= 1 and s[11] == 0:
            struct.pack_into("<H", b, h["spo"] + 28 * i + 24, slot)
            n += 1
    rel_dir = Path(def_rel).parent
    out_dir = FIX / Path(*rel_dir.parts[1:])
    old = out_dir / dp.name
    if old.is_file() and not old.read_text(encoding="utf-8", errors="replace").startswith(MARK):
        return f"skip: {old.relative_to(ROOT).as_posix()} is another tool's overlay (merge by hand)"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / sff.name).write_bytes(bytes(b))
    up = "../" * len(rel_dir.parts)
    lines, in_files = [MARK], False
    for ln in dp.read_bytes().decode("latin-1").splitlines():
        s = ln.strip()
        if s.startswith("["):
            in_files = s.lower().startswith("[files]")
        elif in_files and "=" in s and not s.startswith(";"):
            k, v = s.split("=", 1)
            v = v.split(";", 1)[0].strip().strip('"')
            if k.strip().lower() == "sprite":
                ln = f"{k.strip()} = {sff.name}"
            elif v and (dp.parent / v).exists():
                ln = f"{k.strip()} = {up}{rel_dir.as_posix()}/{v}"
        lines.append(ln)
    (out_dir / dp.name).write_text("\r\n".join(lines), encoding="utf-8")
    return f"fixed {n} portrait sprite(s) -> palette slot {slot}: chars_fix/{(out_dir / dp.name).relative_to(FIX).as_posix()}"


defs = a.defs
if not defs:
    defs = [r["def"] for r in csv.DictReader(open(ROOT / "woo" / "roster" / "summary.csv", encoding="utf-8"))
            if r["def"].startswith("chars/")]
hits = []
for d in defs:
    try:
        r = check(d)
    except Exception as ex:  # unreadable SFF: not this tool's problem
        r = None
        if a.defs:
            print(d, "-> ERROR", ex)
    if r and r[0] >= a.threshold:
        hits.append(d)
        print(f"{d}: {r[1]}")
print(f"{len(hits)} character(s) with partial-recolor portraits")
if a.fix:
    for d in hits:
        print(d, "->", fix(d))
