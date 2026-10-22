"""Measure every roster character's on-screen size: fight height and select-screen portraits.

Per select.def character: localcoord, [Size] xscale/yscale (cns), stand sprite 0,0 axis (feet) height,
portraits 9000,0 (cell) and 9000,1 (big) sizes. Converted to the units the engine draws in:
  fight height (320-wide stage units) = axis_y(0,0) * yscale * 320 / localcoord_x
  select big portrait (motif px)      = h(9000,1) * p1.face.scale * motif_x / localcoord_x   (start.lua 725)
  select cell portrait (motif px)     = h(9000,0) * portrait.scale * motif_x / localcoord_x
Output: woo/roster/sizes.csv (+ the medians printed).

usage: python size_scan.py
"""
import csv
import re
import statistics
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOTIF_X, FACE_SCALE, CELL_SCALE = 1280, 0.98, 0.18  # data/mugen1/system.def


def roster():
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
        out.append(e)
    return list(dict.fromkeys(out))


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


def sprite_dims(sff: Path, want):
    """{(g,n): (w, h, axis_x, axis_y)} for the wanted sprites (links resolved)."""
    b = sff.read_bytes()
    if b[:12] != b"ElecbyteSpr\0":
        return {}
    out = {}
    if b[15] == 1:
        nimg, off = struct.unpack("<II", b[20:28])
        subs = []
        for _ in range(nimg):
            if not off or off + 32 > len(b):
                break
            nxt, ln, ax, ay, g, n, link, same = struct.unpack("<IIhhHHHB", b[off:off + 19])
            if ln == 0 and link < len(subs):
                w, h = subs[link][0], subs[link][1]
            elif ln >= 12:
                x0, y0, x1, y1 = struct.unpack("<HHHH", b[off + 32 + 4:off + 32 + 12])
                w, h = x1 - x0 + 1, y1 - y0 + 1
            else:
                w = h = 0
            subs.append((w, h))
            if (g, n) in want and (g, n) not in out:
                out[(g, n)] = (w, h, ax, ay)
            off = nxt
    else:
        spo, nspr = struct.unpack("<II", b[36:44])
        for i in range(nspr):
            g, n, w, h, ax, ay = struct.unpack("<HHHHhh", b[spo + 28 * i:spo + 28 * i + 12])
            if (g, n) in want and (g, n) not in out:
                out[(g, n)] = (w, h, ax, ay)
    return out


def stand_sprite(air: Path):
    """First frame sprite of action 0 (the stand animation); falls back to 0,0."""
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


def measure(def_rel: str):
    dp = ROOT / def_rel
    t = dp.read_bytes().decode("latin-1")
    info, files = ini(t, "info"), ini(t, "files")
    lc = re.findall(r"-?\d+", info.get("localcoord", "320,240"))
    lcx = int(lc[0]) if lc else 320
    xs = ys = 1.0
    cns = dp.parent / files.get("cns", "")
    if cns.is_file():
        size = ini(cns.read_bytes().decode("latin-1"), "size")
        try:
            xs = float(re.findall(r"-?[\d.]+", size.get("xscale", "1"))[0])
            ys = float(re.findall(r"-?[\d.]+", size.get("yscale", "1"))[0])
        except (IndexError, ValueError):
            pass
    sff = dp.parent / files.get("sprite", "")
    sg = stand_sprite(dp.parent / files.get("anim", ""))
    d = sprite_dims(sff, {sg, (9000, 0), (9000, 1)}) if sff.is_file() else {}
    stand = d.get(sg, (0, 0, 0, 0))
    big = d.get((9000, 1), (0, 0, 0, 0))
    cell = d.get((9000, 0), (0, 0, 0, 0))
    return {
        "def": def_rel, "localcoord": lcx, "xscale": xs, "yscale": ys, "pixel_scale": info.get("portraitscale", ""),
        "stand_spr": f"{sg[0]},{sg[1]}", "stand_axis_y": stand[3], "fight_h": round(stand[3] * ys * 320 / lcx, 1),
        "big_w": big[0], "big_h": big[1], "big_screen_h": round(big[1] * FACE_SCALE * MOTIF_X / lcx, 1),
        "big_screen_w": round(big[0] * FACE_SCALE * MOTIF_X / lcx, 1),
        "cell_w": cell[0], "cell_h": cell[1], "cell_screen_w": round(cell[0] * CELL_SCALE * MOTIF_X / lcx, 1),
    }


if __name__ == "__main__":
    rows = []
    for d in roster():
        try:
            rows.append(measure(d))
        except Exception as ex:  # noqa: BLE001 - report and continue
            print(d, "->", type(ex).__name__, ex)
    with open(ROOT / "woo" / "roster" / "sizes.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for k in ("fight_h", "big_screen_h", "big_screen_w", "cell_screen_w"):
        v = [r[k] for r in rows if r[k] > 0]
        q = statistics.quantiles(v, n=20)
        print(f"{k}: n={len(v)} median={statistics.median(v):.0f} p5={q[0]:.0f} p95={q[-1]:.0f} min={min(v):.0f} max={max(v):.0f}")
