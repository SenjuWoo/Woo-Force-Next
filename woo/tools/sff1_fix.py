"""Fix the Ikemen "green face" class: SFF v1 characters whose sprite 0,0 is a link.

Ikemen's SFF v1 loader reads the shared base palette for sprite 0,0 from the file position of the
last sprite that carried its own palette. When 0,0 is a link (no data of its own) that position is
some unrelated sprite, so the base palette - and faces drawn with it - come out wrong. MUGEN does
not do this.

Fix (Ikemen side only): write chars_fix/<char>/<name>.sff, a copy whose 0,0 holds the linked
sprite's pixels plus the character palette (pal1 .act, reversed into PCX order), and an overlay def
pointing at it; every other file still comes from the original folder. setup_ikemen.py then routes
the select.def entry to the overlay.

usage: python sff1_fix.py [--root DIR] [--from-health] [defs...]
"""
import argparse
import csv
import struct
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
ap.add_argument("--from-health", action="store_true", help="every sff1_linked_base in health_static.csv")
ap.add_argument("--portraits", action="store_true",
                help="also remap 9000,x portraits listed in woo/roster/portrait_palette_mismatch.txt")
ap.add_argument("defs", nargs="*")
a = ap.parse_args()
ROOT = Path(a.root)
FIX = ROOT / "chars_fix"
MARK = "; woo-overlay: sff1 base palette (woo/tools/sff1_fix.py)"


def def_files(raw: bytes):
    out, on = {}, False
    for ln in raw.splitlines():
        s = ln.split(b";", 1)[0].strip()
        if s.startswith(b"["):
            on = s.lower().startswith(b"[files]")
            continue
        if on and b"=" in s:
            k, v = s.split(b"=", 1)
            out.setdefault(k.strip().lower().decode("latin-1"), v.strip().strip(b'"').decode("latin-1"))
    return out


def read_sff1(p: Path):
    b = p.read_bytes()
    nimg, first = struct.unpack("<II", b[20:28])
    subs, off = [], first
    for _ in range(nimg):
        if not off or off + 32 > len(b):
            break
        nxt, ln, ax, ay, grp, img, link, same = struct.unpack("<IIhhHHHB", b[off:off + 19])
        subs.append({"ax": ax, "ay": ay, "grp": grp, "img": img, "link": link, "same": same,
                     "comment": b[off + 19:off + 32], "data": b[off + 32:off + 32 + ln]})
        off = nxt
    return b[:512], subs


def write_sff1(p: Path, header: bytes, subs):
    out = bytearray(header)
    off = 512
    for i, s in enumerate(subs):
        nxt = off + 32 + len(s["data"]) if i < len(subs) - 1 else 0
        out += struct.pack("<IIhhHHHB", nxt, len(s["data"]), s["ax"], s["ay"], s["grp"], s["img"],
                           s["link"], s["same"]) + s["comment"][:13].ljust(13, b"\0") + s["data"]
        off += 32 + len(s["data"])
    struct.pack_into("<I", out, 24, 512)  # first subfile offset
    p.write_bytes(bytes(out))


def pcx_decode(d: bytes):
    xmin, ymin, xmax, ymax = struct.unpack("<HHHH", d[4:12])
    bpl = struct.unpack("<H", d[66:68])[0]
    h = ymax - ymin + 1
    end = len(d) - 769 if len(d) > 769 and d[-769] == 0x0C else len(d)
    px, i = bytearray(), 128
    while len(px) < bpl * h and i < end:
        c = d[i]
        i += 1
        if c >= 0xC0:
            px += bytes([d[i]]) * (c & 0x3F)
            i += 1
        else:
            px.append(c)
    return bytearray(px[:bpl * h]), bpl, h


def pcx_encode(px: bytes, bpl: int, h: int) -> bytes:
    out = bytearray()
    for y in range(h):
        row, x = px[y * bpl:(y + 1) * bpl], 0
        while x < bpl:
            v, n = row[x], 1
            while x + n < bpl and row[x + n] == v and n < 63:
                n += 1
            if n > 1 or v >= 0xC0:
                out += bytes([0xC0 | n, v])
            else:
                out.append(v)
            x += n
    return bytes(out)


def remap_to_palette(data: bytes, src_pal: bytes, dst_pal: bytes) -> bytes:
    """Re-index a PCX so it looks the same drawn with dst_pal (index 0 stays transparent)."""
    px, bpl, h = pcx_decode(data)
    dst = [dst_pal[j * 3:j * 3 + 3] for j in range(256)]
    lut = {0: 0}
    for k in set(px) - {0}:
        c = src_pal[k * 3:k * 3 + 3]
        lut[k] = min(range(1, 256), key=lambda j: sum((c[t] - dst[j][t]) ** 2 for t in range(3)))
    body = pcx_encode(bytes(lut[v] for v in px), bpl, h)
    return data[:128] + body + b"\x0c" + dst_pal


def fix(def_rel: str, portraits: bool = False) -> str:
    """Overlay SFF: un-link a linked 0,0 (base palette), and/or remap group-9000 portraits to
    the character palette (Ikemen draws the first v1 sprite with it)."""
    defp = ROOT / def_rel
    raw = defp.read_bytes()
    files = def_files(raw)
    sff = defp.parent / files["sprite"]
    header, subs = read_sff1(sff)
    act = (defp.parent / files.get("pal1", "")).read_bytes()[:768]
    if len(act) < 768:
        return "skip: no pal1 act"
    pcx_pal = b"".join(act[i:i + 3] for i in range(765, -1, -3))  # .act is stored reversed
    done = []
    base = next((i for i, s in enumerate(subs) if s["grp"] == 0 and s["img"] == 0), None)
    if base is not None and not subs[base]["data"]:
        tgt, hops = subs[base]["link"], 0
        while not subs[tgt]["data"] and hops < 50:  # follow link chains
            tgt, hops = subs[tgt]["link"], hops + 1
        pixels = subs[tgt]["data"]
        if subs[tgt]["same"] == 0 and len(pixels) > 769 and pixels[-769] == 0x0C:
            pixels = pixels[:-769]  # drop its own palette; 0,0 gets the character palette
        subs[base].update(data=pixels + b"\x0c" + pcx_pal, link=0, same=0)
        done.append("base")
    if portraits:
        own = None  # palette in effect while walking the file (samepal sprites inherit it)
        for s in subs:
            d = s["data"]
            if d and s["same"] == 0 and len(d) > 769 and d[-769] == 0x0C:
                own = d[-768:]
            if s["grp"] == 9000 and d and own is not None and own != pcx_pal:
                s.update(data=remap_to_palette(d, own, pcx_pal), same=0)
                done.append(f"9000,{s['img']}")
    if not done:
        return "skip: nothing to fix"

    rel_dir = Path(def_rel).parent  # chars/<...>
    out_dir = FIX / Path(*rel_dir.parts[1:])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_sff = out_dir / sff.name
    write_sff1(out_sff, header, subs)
    up = "../" * len(rel_dir.parts)
    lines, in_files = [MARK], False
    for ln in raw.decode("latin-1").splitlines():
        s = ln.strip()
        if s.startswith("["):
            in_files = s.lower().startswith("[files]")
        elif in_files and "=" in s and not s.startswith(";"):
            k, v = s.split("=", 1)
            v = v.split(";", 1)[0].strip().strip('"')
            if k.strip().lower() == "sprite":
                ln = f"{k.strip()} = {sff.name}"
            elif v and (defp.parent / v).exists():
                ln = f"{k.strip()} = {up}{rel_dir.as_posix()}/{v}"
            # else not in the char folder (e.g. stcommon = common1.cns): keep it, Ikemen finds it in data/
        lines.append(ln)
    (out_dir / defp.name).write_text("\r\n".join(lines), encoding="utf-8")
    return f"fixed {'+'.join(done)} -> chars_fix/{(out_dir / defp.name).relative_to(FIX).as_posix()} ({out_sff.stat().st_size >> 20} MB)"


todo = list(a.defs)
if a.from_health:
    for r in csv.DictReader(open(ROOT / "woo" / "roster" / "health_static.csv", encoding="utf-8")):
        if "sff1_linked_base" in r["issues"]:
            todo.append(r["def"])
mismatch = ROOT / "woo" / "roster" / "portrait_palette_mismatch.txt"
portrait_defs = set()
if a.portraits and mismatch.exists():
    portrait_defs = {ln.split("	")[0] for ln in mismatch.read_text(encoding="utf-8").splitlines() if ln.strip()}
    todo += sorted(portrait_defs)
for d in dict.fromkeys(todo):
    try:
        print(d, "->", fix(d, portraits=d in portrait_defs or a.portraits and not portrait_defs))
    except Exception as ex:
        print(d, "-> ERROR", type(ex).__name__, ex)
