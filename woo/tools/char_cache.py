"""Per-character overlay defs with a select-screen cache, portrait sizing and fight-size normalization.

Why: Ikemen preloads portraits 9000,0 / 9000,1 for all 1,637 characters by walking every full SFF
(~4 CPU-minutes per boot). The engine looks for `<def>_preload.sff` next to the def first
(system.go AddChar) -- a small SFF holding only the preload sprites. The def lives in the read-only
MUGEN folder, so every character gets an overlay def in chars_fix/ (paths point back at chars/) and
the cache file next to it.

The same overlay carries two size fixes, measured per character:
  * portraits: the select screen draws portraits at scale * 1280 / localcoord (start.lua, motif.go),
    so 320-localcoord characters with big portrait art come out up to 15x the roster's typical size.
    The cached copy of 9000,0 / 9000,1 is resampled to the roster median (fights keep the real SFF:
    the lifebar face comes from the loaded character, fightscreen.go).
  * fight height: characters drawn far taller/shorter than the roster's majority get a localcoord
    change, which scales the whole character consistently (sprites, boxes, effects, speeds).
    portraitscale is pinned to the old 320/localcoord so lifebar faces stay the same size.

Sprites are stored as lossless RGBA PNG32 (format 12) with the palette Ikemen's v1 preload logic would
pick (image.go preloadSff/sffv1PalState); v2 palettes are copied so the selectable palette list is
unchanged (system.go preloadCharAssets merges SFF 1,N palettes).

usage: python char_cache.py --measure            measure only -> woo/roster/sizes.csv
       python char_cache.py [--only SUBSTR]      write overlays + caches (then rerun setup_ikemen.py)
"""
import argparse
import csv
import io
import re
import statistics
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "chars_fix"
MARK = "; woo-overlay: cache (woo/tools/char_cache.py)"
TAG = "; woo-cache"
MOTIF_X, FACE_SCALE, CELL_SCALE, CELL_BOX = 1280, 0.98, 0.18, 20  # data/mugen1/system.def
FACE_POS = (70, 170)                 # p1.face.offset: the portrait's axis lands here
PANEL = (15, 60, 253, 429)           # inside of the left portrait panel (motif px, from a screenshot)
PRELOAD = [(9000, 0), (9000, 1)]  # every char preload sprite this motif + lifebars use

# Canon giants keep their authored size (user: "titans and such ... should still remain tall"); only
# normal-height characters drawn at the wrong scale are brought to the roster's majority height. Matched against the folder name, lowercase, substring.
GIANTS = ["colossal", "titan", "marugori", "kaido", "bigmom", "big mom", "whitebeard", "shirohige",
          "moria", "king one piece", "motherdemon", "muzan demon", "orochi", "rey-de-los-mostros",
          "cell max", "urouge", "zeus", "queen", "kuma", "evaunit", "escanor", "mancubus", "pica",
          "katakuri", "cracker", "ornn", "bowser", "franky", "hody", "gyomei", "baryon",
          # big by design in their series (JUS sprites draw them taller than the cast)
          "broly", "mahoraga", "sukuna heian", "sukuna era heian", "kisame shark", "arlong", "magellan",
          "brook", "sengoku", "oven", "diamante", "vaccineman", "snorlax", "muk", "zapdos", "pig-god",
          "nightmare", "kakuzu jiongu", "overgrown", "gonadult", "adultgon", "gon_tp", "zephyr", "enel",
          "hachishakusama", "rey-del-mar", "spidermastermind", "oden", "shiki", "metal-knight"]
# Small sprites are mostly small characters (Chopper, Squirtle, Gengar; Toru Hagakure is invisible),
# so only these are scaled up: chibi sprites of normal-height characters.
UPSCALE = ["tc_gojosatoru"]


# ---------------------------------------------------------------- decoders (ports of image.go)
def rle8(d: bytes, n: int):
    p, i = bytearray(), 0
    while i < len(d) and len(p) < n:
        c = d[i]
        i += 1
        if c & 0xC0 == 0x40 and i < len(d):
            p += bytes([d[i]]) * (c & 0x3F)
            i += 1
        else:
            p.append(c)
    return bytes(p[:n].ljust(n, b"\0"))


def rle5(rle: bytes, n: int):
    p, i, j = bytearray(n), 0, 0
    if not rle:
        return bytes(p)
    last = len(rle) - 1
    while j < n:
        rl = rle[i]
        i = min(i + 1, last)
        dl = rle[i] & 0x7F
        c = 0
        if rle[i] >> 7:
            i = min(i + 1, last)
            c = rle[i]
        i = min(i + 1, last)
        while True:
            if j < n:
                p[j] = c
                j += 1
            rl -= 1
            if rl < 0:
                dl -= 1
                if dl < 0:
                    break
                c = rle[i] & 0x1F
                rl = rle[i] >> 5
                i = min(i + 1, last)
    return bytes(p)


def lz5(rle: bytes, n: int):
    p = bytearray(n)
    if not rle:
        return bytes(p)
    last = len(rle) - 1
    i, j = 0, 0
    ct, cts, rb, rbc = rle[0], 0, 0, 0
    i = min(i + 1, last)
    while j < n:
        d = rle[i]
        i = min(i + 1, last)
        if ct & (1 << cts):
            if d & 0x3F == 0:
                d = ((d << 2) | rle[i]) + 1
                i = min(i + 1, last)
                m = rle[i] + 2
                i = min(i + 1, last)
            else:
                rb |= (d & 0xC0) >> rbc
                rbc += 2
                m = d & 0x3F
                if rbc < 8:
                    d = rle[i] + 1
                    i = min(i + 1, last)
                else:
                    d = rb + 1
                    rb, rbc = 0, 0
            while True:
                if j < n:
                    p[j] = p[j - d] if j - d >= 0 else 0
                    j += 1
                m -= 1
                if m < 0:
                    break
        else:
            if d & 0xE0 == 0:
                m = rle[i] + 8
                i = min(i + 1, last)
            else:
                m = d >> 5
                d &= 0x1F
            for _ in range(m):
                if j < n:
                    p[j] = d
                    j += 1
        cts += 1
        if cts >= 8:
            ct, cts = rle[i], 0
            i = min(i + 1, last)
    return bytes(p)


def pcx(d: bytes):
    """-> (w, h, indices) for an 8-bit PCX."""
    x0, y0, x1, y1 = struct.unpack("<HHHH", d[4:12])
    bpl = struct.unpack("<H", d[66:68])[0]
    w, h = x1 - x0 + 1, y1 - y0 + 1
    end = len(d) - 769 if len(d) > 769 and d[-769] == 0x0C else len(d)
    px, i = bytearray(), 128
    while len(px) < bpl * h and i < end:
        c = d[i]
        i += 1
        if c >= 0xC0 and i < end:
            px += bytes([d[i]]) * (c & 0x3F)
            i += 1
        else:
            px.append(c)
    px = bytes(px[:bpl * h]).ljust(bpl * h, b"\0")
    return w, h, b"".join(px[r * bpl:r * bpl + w] for r in range(h))


def rgba(w, h, idx: bytes, pal):
    """Indexed -> RGBA; index 0 transparent (MUGEN/Ikemen indexed sprite rule)."""
    lut = [(pal[k][0], pal[k][1], pal[k][2], 0 if k == 0 else 255) for k in range(256)]
    im = Image.new("RGBA", (w, h))
    im.putdata([lut[k] for k in idx])
    return im


# ---------------------------------------------------------------- SFF readers
class Spr:
    """img: RGBA view (measuring, truecolour sources). pimg/pal: the indexed original, kept so the cache
    stores PNG8 + palette and Ikemen draws it exactly like before (nearest; RGBA would get bilinear)."""
    def __init__(self, img: Image.Image, ax: int, ay: int, pimg=None, pal=None):
        self.img, self.ax, self.ay, self.pimg, self.pal = img, ax, ay, pimg, pal


def indexed(w, h, idx: bytes, pal, ax, ay) -> Spr:
    pimg = Image.frombytes("P", (w, h), bytes(idx))
    pimg.putpalette([c for t in pal for c in t])
    return Spr(rgba(w, h, idx, pal), ax, ay, pimg, pal)


def read_v1(b: bytes, want):
    """Sprites in `want` decoded with Ikemen's v1 preload palette rules (sffv1PalState)."""
    nimg, off = struct.unpack("<II", b[20:28])
    subs = []
    for _ in range(nimg):
        if not off or off + 32 > len(b):
            break
        nxt, ln, ax, ay, g, n, link, same = struct.unpack("<IIhhHHHB", b[off:off + 19])
        subs.append((g, n, ax, ay, ln, link, same, off + 32))
        off = nxt
    out, state, current, loc, init, prev_pal = {}, 0, 0, None, None, None
    own_pal = {}  # subfile index -> its effective palette (unique rule)

    def file_pal(k):
        d = b[subs[k][7]:subs[k][7] + subs[k][4]]
        return d[-768:] if len(d) > 769 and d[-769] == 0x0C else None

    for i, (g, n, ax, ay, ln, link, same, data) in enumerate(subs):
        if i == 0:
            loc = init = 0
        elif g == 0 and n == 0:
            state, loc = 2, init
        elif same == 0 and ln > 0:
            if state == 2:
                state = 3
            elif current == 0:
                state = 1
            current += 1
            loc = i
        fp = file_pal(i) if ln > 0 else None
        pal = fp if (same == 0 and fp) else prev_pal
        if ln == 0 and link < i:
            pal = own_pal.get(link, prev_pal)
        own_pal[i] = pal
        if ln > 0:
            prev_pal = pal
        if (g, n) in want and (g, n) not in out:
            k = link if ln == 0 and link < i else i
            d = b[subs[k][7]:subs[k][7] + subs[k][4]]
            if len(d) < 128:
                continue
            shared = (g == 0 and n == 0) or state % 2 == 0
            use = file_pal(loc) if shared and loc is not None and file_pal(loc) else own_pal.get(k)
            if use is None:
                use = bytes(768)
            p = [tuple(use[3 * c:3 * c + 3]) for c in range(256)]
            w, h, idx = pcx(d)
            out[(g, n)] = indexed(w, h, idx, p, ax, ay)
    return out


def read_v2(b: bytes, want):
    spo, nspr, pao, npal, ldo, ldl, tdo, tdl = struct.unpack("<IIIIIIII", b[36:68])
    pnodes = [struct.unpack("<HHHHII", b[pao + 16 * i:pao + 16 * i + 16]) for i in range(npal)]

    def pal(i):
        seen = 0
        while 0 <= i < len(pnodes) and pnodes[i][5] == 0 and seen < 8:
            i, seen = pnodes[i][3], seen + 1
        if not 0 <= i < len(pnodes):
            return [(0, 0, 0)] * 256
        g, it, nc, ln, off, L = pnodes[i]
        d = b[ldo + off:ldo + off + L]
        p = [tuple(d[4 * k:4 * k + 3]) for k in range(len(d) // 4)]
        return (p + [(0, 0, 0)] * 256)[:256]

    nodes = [struct.unpack("<HHHHhhHBBIIHH", b[spo + 28 * i:spo + 28 * i + 28]) for i in range(nspr)]
    out = {}
    for g, n, w, h, ax, ay, link, fmt, cd, off, L, pi, fl in nodes:
        if (g, n) not in want or (g, n) in out:
            continue
        if L == 0 and link < len(nodes):  # linked sprite
            _, _, w, h, _, _, _, fmt, cd, off, L, pi, fl = nodes[link]
        start = (tdo if fl & 1 else ldo) + off
        d = b[start:start + L]
        if fmt in (10, 11, 12):
            im = Image.open(io.BytesIO(d[4:]))
            if im.mode == "P" and fmt == 10:
                idx = bytes(im.getdata())
                out[(g, n)] = indexed(im.width, im.height, idx, pal(pi), ax, ay)
            else:
                out[(g, n)] = Spr(im.convert("RGBA"), ax, ay)
            continue
        if fmt == 0:
            idx = d[:w * h].ljust(w * h, b"\0")
        elif fmt == 2:
            idx = rle8(d[4:], w * h)
        elif fmt == 3:
            idx = rle5(d[4:], w * h)
        elif fmt == 4:
            idx = lz5(d[4:], w * h)
        else:
            continue
        out[(g, n)] = indexed(w, h, idx, pal(pi), ax, ay)
    return out, pnodes, (ldo, b)


# ---------------------------------------------------------------- defs
def ik_decode(b: bytes) -> str:
    """How Ikemen reads a def: UTF-8, else Shift-JIS (common.go decodeShiftJIS)."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("shift_jis", errors="replace")


def lines_of(b: bytes):
    # other woo tools wrote CR CR LF (text-mode write of CRLF); normalise so blank lines don't multiply
    t = ik_decode(b).replace('\r\r\n', '\n').replace('\r\n', '\n')
    return t.replace('\r', '\n').split('\n')


def ini(text: str, section: str):
    out, on = {}, False
    for ln in text.splitlines():
        s = ln.split(";", 1)[0].strip()
        if s.startswith("["):
            on = s.lower() == f"[{section}]"
            continue
        if on and "=" in s:
            k, v = s.split("=", 1)
            out.setdefault(k.strip().lower(), v.strip().strip('"'))
    return out


def roster():
    """[(routed_def, original_def)] from data/select.def."""
    out, sec = [], ""
    for ln in (ROOT / "data" / "select.def").read_text(encoding="utf-8").splitlines():
        s = ln.split(";", 1)[0].strip()
        if s.startswith("["):
            sec = s.lower()
            continue
        if sec != "[characters]" or not s:
            continue
        e = s.split(",")[0].strip().replace("\\", "/")
        if e.lower() in ("randomselect", "skipslot", "dummyslot", "empty"):
            continue
        if not e.lower().endswith(".def"):
            e = f"{e}/{Path(e).name}.def"
        if not e.lower().startswith(("chars/", "chars_fix/")):
            e = "chars/" + e
        orig = "chars/" + e.split("/", 1)[1] if e.lower().startswith("chars_fix/") else e
        out.append((e, orig))
    return list(dict.fromkeys(out))


def stand_sprite(air: Path):
    if air.is_file():
        on = False
        for ln in air.read_bytes().decode("latin-1").splitlines():
            t = ln.split(";", 1)[0].strip()
            if t.lower().startswith("[begin action"):
                on = re.sub(r"\s+", " ", t.lower()).rstrip("]").split(" ")[-1] == "0"
                continue
            if on and re.match(r"^-?\d+\s*,\s*-?\d+\s*,", t):
                g, n = (int(x) for x in t.split(",")[:2])
                return (g, n) if g >= 0 and n >= 0 else (0, 0)
    return (0, 0)


def load(orig_def: str):
    dp = ROOT / orig_def
    text = '\n'.join(lines_of(dp.read_bytes()))
    info, files = ini(text, "info"), ini(text, "files")
    lc = [int(x) for x in re.findall(r"-?\d+", info.get("localcoord", ""))[:2]] or [320, 240]
    if len(lc) == 1:
        lc.append(lc[0] * 3 // 4)
    ps = info.get("portraitscale")
    xs = ys = 1.0
    cns = dp.parent / files.get("cns", "")
    if cns.is_file():
        size = ini(cns.read_bytes().decode("latin-1"), "size")
        for key in ("xscale", "yscale"):
            m = re.findall(r"-?[\d.]+", size.get(key, "1"))
            try:
                v = float(m[0]) if m else 1.0
            except ValueError:
                v = 1.0
            xs, ys = (v, ys) if key == "xscale" else (xs, v)
    sg = stand_sprite(dp.parent / files.get("anim", ""))
    sff = dp.parent / files.get("sprite", "")
    sprs, pnodes, v2 = {}, [], None
    if sff.is_file():
        b = sff.read_bytes()
        if b[:12] == b"ElecbyteSpr\0":
            if b[15] == 1:
                sprs = read_v1(b, set(PRELOAD) | {sg})
            else:
                sprs, pnodes, v2 = read_v2(b, set(PRELOAD) | {sg})
    return {"dp": dp, "text": text, "lc": lc, "ps": float(ps) if ps else None, "ys": ys, "sg": sg,
            "sprs": sprs, "pnodes": pnodes, "v2": v2}


def fight_height(c):
    s = c["sprs"].get(c["sg"])
    if not s:
        return 0.0
    a = s.img.getchannel("A")
    box = a.getbbox()
    if not box:
        return 0.0
    top = box[1]
    above = max(0, s.ay - top)  # opaque pixels above the feet (axis)
    return above * c["ys"] * 320 / c["lc"][0]


# ---------------------------------------------------------------- outputs
def write_sff(path: Path, sprites, pnodes, v2):
    """SFF v2.01: PNG32 sprites + the source's palettes (v2) or one base palette (v1)."""
    ldata, pal_nodes = bytearray(), bytearray()
    if pnodes:
        ldo, b = v2
        # selectable palettes are the group-1 ones; links point backwards, so a prefix stays consistent
        last = max((i for i, nd in enumerate(pnodes) if nd[0] == 1), default=0)
        for g, it, nc, ln, off, L in pnodes[:last + 1]:
            pal_nodes += struct.pack("<HHHHII", g, it, nc, ln, len(ldata) if L else 0, L)
            ldata += b[ldo + off:ldo + off + L]
    else:
        pal_nodes += struct.pack("<HHHHII", 1, 1, 256, 0, 0, 1024)
        ldata += bytes(1024)
    nodes = bytearray()
    for (g, n), s in sprites.items():
        buf = io.BytesIO()
        if s.pimg is not None:  # PNG8 + its own palette (key g,n: never a selectable 1,N palette)
            pi = len(pal_nodes) // 16
            pal_nodes += struct.pack("<HHHHII", g, n, 256, 0, len(ldata), 1024)
            ldata += bytes(v for t in s.pal for v in (t[0], t[1], t[2], 255))
            s.pimg.save(buf, "PNG", optimize=True)
            blob = struct.pack("<I", s.pimg.width * s.pimg.height) + buf.getvalue()
            fmt, depth = 10, 8
        else:
            pi = 0
            s.img.save(buf, "PNG", optimize=True)
            blob = struct.pack("<I", s.img.width * s.img.height * 4) + buf.getvalue()
            fmt, depth = 12, 32
        nodes += struct.pack("<HHHHhhHBBIIHH", g, n, s.img.width, s.img.height, s.ax, s.ay, 0, fmt, depth,
                             len(ldata), len(blob), pi, 0)
        ldata += blob
    npal = len(pal_nodes) // 16
    spo, pao = 512, 512 + len(nodes)
    ldo = pao + len(pal_nodes)
    head = bytearray(b"ElecbyteSpr\0" + bytes([0, 0, 0, 2]) + b"\0" * 8 + bytes([0, 0, 0, 2]) + b"\0" * 8)
    head += struct.pack("<IIIIIIII", spo, len(sprites), pao, npal, ldo, len(ldata), ldo + len(ldata), 0)
    head += b"\0" * 8 + b"woo char_cache.py preload".ljust(436, b"\0")
    path.write_bytes(bytes(head + nodes + pal_nodes + ldata))


def content(s: Spr):
    """Opaque bounding box (l, t, r, b); many portraits sit on a mostly empty canvas."""
    return s.img.getchannel("A").getbbox() or (0, 0, s.img.width, s.img.height)


def panel_fit(s: Spr, k: float) -> float:
    """Largest scale factor (<= 1) that keeps the visible portrait, drawn at its axis on FACE_POS, inside PANEL."""
    x0, y0, x1, y1 = PANEL
    fx, fy = FACE_POS
    l, t, r_, b = content(s)
    r = 1.0
    for need, have in ((fx - x0, (s.ax - l) * k), (x1 - fx, (r_ - s.ax) * k),
                       (fy - y0, (s.ay - t) * k), (y1 - fy, (b - s.ay) * k)):
        if have > 0 and need > 0:
            r = min(r, need / have)
    return r


def to_palette(img: Image.Image, pal):
    """RGBA -> indexed with `pal`: nearest colour among entries 1..255, alpha < 128 -> index 0."""
    a = np.asarray(img.convert("RGBA"), dtype=np.int32)
    rgb, alpha = a[..., :3].reshape(-1, 3), a[..., 3].reshape(-1)
    pals = np.array(pal[1:], dtype=np.int32)
    out = np.zeros(len(rgb), dtype=np.uint8)
    for i in range(0, len(rgb), 32768):
        d = ((rgb[i:i + 32768, None, :] - pals[None, :, :]) ** 2).sum(axis=2)
        out[i:i + 32768] = d.argmin(axis=1) + 1
    out[alpha < 128] = 0
    pimg = Image.frombytes("P", img.size, out.tobytes())
    pimg.putpalette([c for t in pal for c in t])
    return pimg


def resample(s: Spr, r: float) -> Spr:
    if abs(r - 1) < 0.02:
        return s
    w, h = max(1, round(s.img.width * r)), max(1, round(s.img.height * r))
    if s.pimg is not None:
        if abs(r - round(r)) < 0.01:  # whole-number upscale: exact pixel blocks, like the engine's nearest
            pimg = s.pimg.resize((w, h), Image.NEAREST)
        else:
            src = s.img
            if r > 1:  # blocks first, then the fraction, so pixel art stays sharp
                up = int(np.ceil(r))
                src = src.resize((src.width * up, src.height * up), Image.NEAREST)
            small = src.convert("RGBa").resize((w, h), Image.LANCZOS).convert("RGBA")
            pimg = to_palette(small, s.pal)
        return Spr(rgba(w, h, pimg.tobytes(), s.pal), round(s.ax * r), round(s.ay * r), pimg, s.pal)
    if r < 1:  # premultiplied so transparent pixels don't bleed dark fringes
        img = s.img.convert("RGBa").resize((w, h), Image.LANCZOS).convert("RGBA")
    else:
        img = s.img.resize((w, h), Image.NEAREST)
    return Spr(img, round(s.ax * r), round(s.ay * r))


def overlay_text(routed: str, orig: str, info_lines):
    """Overlay def text: existing overlay (keeps its tool's MARK and paths) or a path-rewritten copy."""
    rp, op = ROOT / routed, ROOT / orig
    if routed != orig and rp.is_file():
        lines = lines_of(rp.read_bytes())
        if not lines or not lines[0].startswith("; woo-overlay"):
            lines.insert(0, MARK)
    else:
        rel_dir = Path(orig).parent
        up = "../" * len(rel_dir.parts)
        lines, sec = [MARK], ""
        for ln in lines_of(op.read_bytes()):
            s = ln.strip()
            if s.startswith("["):
                sec = s.lower().split("]")[0] + "]"
            elif sec in ("[files]", "[arcade]") or sec.endswith(".files]") or sec.endswith(".arcade]"):
                if "=" in s and not s.startswith(";"):
                    k, v = s.split("=", 1)
                    v = v.split(";", 1)[0].strip().strip('"')
                    if v and (op.parent / v).is_file():
                        ln = f"{k.strip()} = {up}{rel_dir.as_posix()}/{v}"
            lines.append(ln)
    lines = [ln[len(";woo-cache "):] if ln.startswith(";woo-cache ") else ln
             for ln in lines if TAG not in ln]  # undo a previous run before applying this one
    if info_lines:
        keys = {ln.split("=")[0].strip().lower() for ln in info_lines}
        out, done, sec = [], False, ""
        for ln in lines:
            s = ln.strip()
            if s.startswith("["):
                sec = s.lower()
            elif sec == "[info]" and "=" in s and not s.startswith(";") and s.split("=")[0].strip().lower() in keys:
                ln = f";woo-cache {ln}"
            out.append(ln)
            if not done and s.lower().startswith("[info]"):
                out += info_lines
                done = True
        if not done:
            out[1:1] = ["[Info]"] + info_lines
        lines = out
    return "\r\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    chars = roster()  # always measure all: the medians are roster-wide
    data = []
    for routed, orig in chars:
        try:
            c = load(orig)
        except Exception as ex:  # noqa: BLE001 - one bad file must not stop the roster
            print(orig, "-> ERROR", type(ex).__name__, ex)
            continue
        c["routed"], c["orig"], c["fh"] = routed, orig, fight_height(c)
        big, cell = c["sprs"].get((9000, 1)), c["sprs"].get((9000, 0))
        c["bigh"] = (content(big)[3] - content(big)[1]) * FACE_SCALE * MOTIF_X / c["lc"][0] * (c["ps"] or 1) if big else 0
        data.append(c)
        if len(data) % 200 == 0:
            print(len(data), "measured", flush=True)
    M = statistics.median([c["fh"] for c in data if c["fh"] > 0])
    TB = statistics.median([c["bigh"] for c in data if c["bigh"] > 0])
    rows = []
    for c in data:
        name = Path(c["orig"]).parent.name.lower()
        fh, f = c["fh"], 1.0
        giant = any(g in name for g in GIANTS)
        if fh > 0:
            if giant:
                pass  # titans, kaiju, Big Mom...: authored tall, they stay tall
            elif fh > 1.4 * M:
                f = M / fh
            elif fh < 0.6 * M and any(u in name for u in UPSCALE):
                f = 0.8 * M / fh
        if f < 1.0:
            f = 1.0 / max(2, round(1 / f))
        elif f > 1.0:
            f = float(max(2, round(f)))
        lc0 = c["lc"]
        lc1 = [round(lc0[0] / f), round(lc0[1] / f)] if f != 1.0 else lc0
        ps = c["ps"] if c["ps"] is not None else (320 / lc0[0] if f != 1.0 else None)
        sel0 = (c["ps"] if c["ps"] is not None else 1) * MOTIF_X / lc0[0]  # select scale before
        sel1 = (ps if ps is not None else 1) * MOTIF_X / lc1[0]            # ... and with the new def
        comp = sel0 / sel1  # undo the localcoord change on the select screen (whole number for 1/n)
        sprites, rb, rc = {}, 1.0, 1.0
        big, cell = c["sprs"].get((9000, 1)), c["sprs"].get((9000, 0))
        if big:
            k = FACE_SCALE * sel0
            box = content(big)
            hb = (box[3] - box[1]) * k
            fit = panel_fit(big, k)
            adj = 1.0
            if hb < 0.55 * TB and int(min(0.85 * TB / hb, fit)) >= 2:
                adj = float(int(min(0.85 * TB / hb, fit)))  # tiny: whole-number enlargement only
            elif fit < 0.9:  # spills >10% out of the portrait panel over the grid; small spills: keep pixels crisp
                adj = fit
            if fit < 0.6:
                # authored for another screen layout (anchor far off the picture): re-anchor so the picture
                # sits centred in the panel, bottom-aligned like the roster's sprites, scaled to fit
                x0, y0, x1, y1 = PANEL
                cw, ch = box[2] - box[0], box[3] - box[1]
                adj = min(1.0, 0.92 * (x1 - x0) / (cw * k), 0.92 * (y1 - y0) / (ch * k))
            rb = comp * adj
            sp = resample(big, rb)
            if fit < 0.6:
                k1 = FACE_SCALE * sel1
                l, t, r_, b = content(sp)
                sp.ax = round((l + r_) / 2 - ((PANEL[0] + PANEL[2]) / 2 - FACE_POS[0]) / k1)
                sp.ay = round(b - (PANEL[3] - 12 - FACE_POS[1]) / k1)
            sprites[(9000, 1)] = sp
        if cell:
            # the visible face, not the canvas: icons often sit on a big empty canvas (Buutenks: 25 px face
            # on 256x256), and sizing by canvas shrank those to a few pixels
            l, t, r_, b = content(cell)
            m = max(r_ - l, b - t) * CELL_SCALE * sel0
            adj = 1.0
            if m > CELL_BOX * 1.2:
                adj = (CELL_BOX - 1) / m
            elif 3 <= m < 0.75 * CELL_BOX:  # small face: fill the cell, whole-number steps when they fit
                adj = (CELL_BOX - 2) / m
                adj = float(int(adj)) if adj >= 2 else adj
                # enlarge the face only, not its empty canvas (a 256x256 canvas x4 is 1024x1024 for nothing)
                box = (l, t, r_, b)
                cell = Spr(cell.img.crop(box), cell.ax - l, cell.ay - t,
                           cell.pimg.crop(box) if cell.pimg is not None else None, cell.pal)
            rc = comp * adj
            sp = resample(cell, rc)
            if adj > 1:  # centre the enlarged face in its cell (authors anchor small icons top-left)
                k1 = CELL_SCALE * sel1
                l, t, r_, b = content(sp)
                sp.ax = round((l + r_) / 2 - (CELL_BOX - 1) / 2 / k1)
                sp.ay = round((t + b) / 2 - (CELL_BOX - 1) / 2 / k1)
            sprites[(9000, 0)] = sp
        info = []
        if f != 1.0:
            info.append(f"localcoord = {lc1[0]},{lc1[1]} {TAG} (was {lc0[0]},{lc0[1]}: fight height {fh:.0f} -> {fh * f:.0f})")
            if c["ps"] is None:
                info.append(f"portraitscale = {ps:.6g} {TAG} (lifebar face size unchanged)")
        rows.append([c["orig"], f"{fh:.1f}", "giant" if giant else "", f"{f:.3f}", f"{lc0[0]}", f"{lc1[0]}",
                     f"{rb:.3f}", f"{rc:.3f}", ",".join(f"{g},{n}" for g, n in sprites)])
        if a.measure or not any(o.strip().lower() in c["orig"].lower() for o in a.only.split(",")):
            continue
        out_def = FIX / Path(*Path(c["orig"]).parts[1:])
        out_def.parent.mkdir(parents=True, exist_ok=True)
        out_def.write_bytes(overlay_text(c["routed"], c["orig"], info).encode("utf-8"))
        if sprites:
            write_sff(out_def.with_name(out_def.stem + "_preload.sff"), sprites, c["pnodes"], c["v2"])
    with open(ROOT / "woo" / "roster" / "char_cache.tsv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["def", "fight_h", "giant", "size_factor", "lc_x", "new_lc_x", "big_resample", "cell_resample", "cached"])
        w.writerows(rows)
    resized = [r for r in rows if r[3] != "1.000"]
    print(f"median fight height {M:.0f}, median big portrait {TB:.0f}px; {len(rows)} chars, "
          f"{len(resized)} resized for fights, {sum(r[6] != '1.000' for r in rows)} big portraits and "
          f"{sum(r[7] != '1.000' for r in rows)} cell portraits resampled -> woo/roster/char_cache.tsv")


if __name__ == "__main__":
    sys.exit(main())
