"""Fix SFF v1 font glyphs that Ikemen draws with the wrong palette.

Ikemen draws every glyph of a font with one palette (font.go DrawText: the bank's palette, and for a v1
SFF that is the first glyph's palette). MUGEN font glyphs saved as PCX each carry their own palette, so
in Ikemen every glyph whose palette differs from the first one comes out garbled ("bleached" menu items:
data/Main_Menu.sff, one full-screen glyph per highlighted menu item).

Fix: rewrite the font SFF as SFF v2 with truecolour PNG32 glyphs (format 12). Truecolour glyphs skip the
palette lookup (font.go drawChar), and the conversion is lossless: index 0 -> transparent, any other index
-> its own palette colour. Every glyph is decoded back and compared pixel by pixel before the file is
replaced; the original goes to woo/rollback/.

usage: python font_rgba.py --scan            list fonts (system.def/fight.def) with differing glyph palettes
       python font_rgba.py FILE.sff [...]    convert in place (only files under <root>/data)
"""
import io
import re
import shutil
import struct
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "woo" / "rollback" / "2026-10-09_font_rgba"


def read_sff1(b: bytes):
    nimg, first = struct.unpack("<II", b[20:28])
    subs, off = [], first
    for _ in range(nimg):
        if not off or off + 32 > len(b):
            break
        nxt, ln, ax, ay, grp, img, link, same = struct.unpack("<IIhhHHHB", b[off:off + 19])
        subs.append({"grp": grp, "img": img, "ax": ax, "ay": ay, "link": link, "same": same,
                     "data": b[off + 32:off + 32 + ln]})
        off = nxt
    return subs


def pcx_rgba(d: bytes, pal: bytes) -> Image.Image:
    xmin, ymin, xmax, ymax = struct.unpack("<HHHH", d[4:12])
    bpl = struct.unpack("<H", d[66:68])[0]
    w, h = xmax - xmin + 1, ymax - ymin + 1
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
    px = px[:bpl * h].ljust(bpl * h, b"\0")
    idx = Image.frombytes("P", (bpl, h), bytes(px)).crop((0, 0, w, h))
    rgba = [(pal[3 * k], pal[3 * k + 1], pal[3 * k + 2], 0 if k == 0 else 255) for k in range(256)]
    out = Image.new("RGBA", (w, h))
    out.putdata([rgba[k] for k in idx.getdata()])
    return out


def glyphs(b: bytes):
    """-> [(grp, img, ax, ay, RGBA image)] resolving links and shared palettes like MUGEN."""
    out, prev_pal, by_index = [], None, []
    for s in read_sff1(b):
        d = s["data"]
        if not d and s["link"] < len(by_index):  # linked sprite: same image as the target
            out.append((s["grp"], s["img"], s["ax"], s["ay"], by_index[s["link"]]))
            by_index.append(by_index[s["link"]])
            continue
        own = len(d) > 769 and d[-769] == 0x0C and not s["same"]
        pal = d[-768:] if own else prev_pal
        if pal is None:
            raise ValueError("first glyph has no palette")
        prev_pal = pal
        im = pcx_rgba(d, pal)
        out.append((s["grp"], s["img"], s["ax"], s["ay"], im))
        by_index.append(im)
    return out


def write_sff2(path: Path, items, pal0: bytes):
    """SFF v2.01 with PNG32 sprites (format 12, coldepth 32) and one palette (unused by truecolour)."""
    ldata = bytearray(b"".join(pal0[3 * k:3 * k + 3] + b"\xff" for k in range(256)))
    nodes = bytearray()
    for grp, img, ax, ay, im in items:
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        blob = struct.pack("<I", im.width * im.height * 4) + buf.getvalue()
        nodes += struct.pack("<HHHHhhHBBIIHH", grp, img, im.width, im.height, ax, ay, 0, 12, 32,
                             len(ldata), len(blob), 0, 0)
        ldata += blob
    spo = 512
    pao = spo + len(nodes)
    ldo = pao + 16
    head = bytearray(b"ElecbyteSpr\0" + bytes([0, 1, 0, 2]) + b"\0" * 8 + bytes([0, 0, 0, 2]) + b"\0" * 8)
    head += struct.pack("<IIIIIIII", spo, len(items), pao, 1, ldo, len(ldata), ldo + len(ldata), 0)
    head += b"\0" * 8 + b"woo_brain font_rgba.py: PNG32 glyphs".ljust(436, b"\0")
    assert len(head) == 512
    pal_node = struct.pack("<HHHHII", 0, 0, 256, 0, 0, 1024)
    path.write_bytes(bytes(head + nodes + pal_node + ldata))


def read_back(path: Path):
    b = path.read_bytes()
    spo, nspr, pao, npal, ldo, ldl, tdo, tdl = struct.unpack("<IIIIIIII", b[36:68])
    out = []
    for i in range(nspr):
        g, it, w, h, ax, ay, ln, fmt, cd, off, L, pi, fl = struct.unpack("<HHHHhhHBBIIHH", b[spo + 28 * i:spo + 28 * i + 28])
        out.append((g, it, ax, ay, Image.open(io.BytesIO(b[ldo + off + 4:ldo + off + L])).convert("RGBA")))
    return out


def convert(path: Path):
    path = path.resolve()
    assert str(path).lower().startswith(str((ROOT / "data").resolve()).lower()), "only fonts under data/"
    b = path.read_bytes()
    if b[15] != 1:
        return f"{path.name}: not SFF v1, skipped"
    items = glyphs(b)
    first_pal = next(s["data"][-768:] for s in read_sff1(b) if len(s["data"]) > 769)
    BACKUP.mkdir(parents=True, exist_ok=True)
    bak = BACKUP / path.name
    if not bak.exists():
        shutil.copy2(path, bak)
    tmp = path.with_suffix(".woo_tmp")
    write_sff2(tmp, items, first_pal)
    back = read_back(tmp)
    assert len(back) == len(items)
    for (g, i, ax, ay, im), (g2, i2, ax2, ay2, im2) in zip(items, back):
        assert (g, i, ax, ay) == (g2, i2, ax2, ay2) and im.tobytes() == im2.tobytes(), f"mismatch {g},{i}"
    tmp.replace(path)
    return f"{path.name}: {len(items)} glyphs -> SFF v2 PNG32 (pixel-identical), backup {bak.relative_to(ROOT)}"


def font_files():
    """Bitmap font SFFs referenced by the motif and lifebars."""
    out = []
    for d in (ROOT / "data" / "mugen1" / "system.def", ROOT / "data" / "LIFEBARS" / "fight.def"):
        if not d.is_file():
            continue
        for m in re.finditer(r"(?im)^\s*font\d+\s*=\s*([^;\r\n]+)", d.read_text(encoding="latin-1")):
            name = m.group(1).strip().strip('"')
            for base in (d.parent, ROOT / "data", ROOT / "font", ROOT):
                f = base / name
                if f.is_file():
                    out.append(f)
                    break
    sffs = []
    for f in dict.fromkeys(out):
        if f.suffix.lower() == ".def":
            m = re.search(r"(?im)^\s*file\s*=\s*([^;\r\n]+)", f.read_text(encoding="latin-1"))
            t = re.search(r"(?im)^\s*type\s*=\s*(\w+)", f.read_text(encoding="latin-1"))
            if m and not (t and t.group(1).lower() == "truetype"):
                p = f.parent / m.group(1).strip().strip('"')
                if p.is_file():
                    sffs.append((f, p))
    return sffs


if __name__ == "__main__":
    if sys.argv[1:] == ["--scan"]:
        for d, s in font_files():
            b = s.read_bytes()
            if b[15] != 1:
                print(f"{d.relative_to(ROOT)}: SFF v2, ok")
                continue
            pals = {x["data"][-768:] for x in read_sff1(b) if len(x["data"]) > 769 and x["data"][-769] == 0x0C and not x["same"]}
            print(f"{d.relative_to(ROOT)} -> {s.relative_to(ROOT)}: SFF v1, {len(pals)} distinct glyph palettes"
                  + ("  <- WRONG COLOURS IN IKEMEN" if len(pals) > 1 else ""))
    else:
        for a in sys.argv[1:]:
            print(convert(Path(a)))
