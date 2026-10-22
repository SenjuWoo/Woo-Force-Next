"""SFF v1/v2 + DEF readers shared by act_orient.py and pal_scan.py (moved verbatim out of act_orient.py).

v2_body / v1_body -> (base palette as 256 RGB tuples, {palette index: pixel count} over the stand sprites
0,0..0,9 that use the base palette). def_files -> the [Files] section of a def as a dict.
"""
import io
import struct
from pathlib import Path

from PIL import Image


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


# ---------------------------------------------------------------- SFF readers (indices + base palette)
def v2_body(b: bytes):
    spo, nspr, pao, npal, ldo, ldl, tdo, tdl = struct.unpack("<IIIIIIII", b[36:68])
    pals = [struct.unpack("<HHHHII", b[pao + 16 * i:pao + 16 * i + 16]) for i in range(npal)]
    base_i = next((i for i, p in enumerate(pals) if p[0] == 1 and p[1] == 1), 0)
    g, it, n, ln, off, L = pals[base_i]
    if L == 0:
        g, it, n, ln, off, L = pals[ln]
    d = b[ldo + off:ldo + off + L]
    base = [tuple(d[4 * k:4 * k + 3]) for k in range(len(d) // 4)] + [(0, 0, 0)] * (256 - len(d) // 4)
    sprs = [struct.unpack("<HHHHhhHBBIIHH", b[spo + 28 * i:spo + 28 * i + 28]) for i in range(nspr)]
    used = {}
    for s in sprs:
        g, it, w, h, ax, ay, ln, fmt, cd, off, L, pi, fl = s
        if g != 0 or it > 9 or L == 0 or pi != base_i:
            continue
        start = (tdo if fl & 1 else ldo) + off
        if fmt == 10:
            px = Image.open(io.BytesIO(b[start + 4:start + L])).getdata()
        elif fmt == 0:
            px = b[start:start + L]
        elif fmt == 2:  # RLE8 after a 4-byte length
            px, d, i = [], b[start + 4:start + L], 0
            while i < len(d):
                c = d[i]
                i += 1
                if c & 0xC0 == 0x40 and i < len(d):
                    px += [d[i]] * (c & 0x3F)
                    i += 1
                else:
                    px.append(c)
        else:
            continue  # RLE5/LZ5: rare for stand sprites, skip
        for k in px:
            if k:
                used[k] = used.get(k, 0) + 1
    return base, used


def read_sff1(p: Path):
    b = p.read_bytes()
    nimg, first = struct.unpack("<II", b[20:28])
    subs, off = [], first
    for _ in range(nimg):
        if not off or off + 32 > len(b):
            break
        nxt, ln, ax, ay, grp, img, link, same = struct.unpack("<IIhhHHHB", b[off:off + 19])
        subs.append({"grp": grp, "img": img, "link": link, "same": same, "data": b[off + 32:off + 32 + ln]})
        off = nxt
    return subs


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
    return px[:bpl * h], bpl, h


def v1_body(path: Path):
    subs = read_sff1(path)
    used, base, own = {}, None, None
    for s in subs:
        d = s["data"]
        if d and s["same"] == 0 and len(d) > 769 and d[-769] == 0x0C:
            own = d[-768:]
        if s["grp"] == 0 and s["img"] <= 9 and d:
            if base is None and own is not None:
                base = [tuple(own[3 * k:3 * k + 3]) for k in range(256)]
            px, bpl, h = pcx_decode(d)
            for k in px:
                if k:
                    used[k] = used.get(k, 0) + 1
    return base, used
