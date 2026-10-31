"""Find .act palettes that Ikemen applies upside down, and fix them with reversed copies.

Ikemen reads every .act reversed (image.go readActPalette: destIdx := 255 - i), the MUGEN convention.
Some characters ship acts saved in normal order. Ikemen then draws their sprites with the wrong end of
the palette: black bodies (a partial act) or scrambled "inverted" colours (a full act).

Detection per palN: decode the pixels of the stand sprites (0,0..0,9) to get the palette indices the
body really uses, then compare both readings of the act against the character's own base palette
(SFF v2: palette 1,1; SFF v1: the 0,0 sprite's palette). A recolor keeps outlines/skin/most colours,
so the right reading is the one closer to the base palette at those indices.

Fix: chars_fix/<char>/woo_actfix_palN.act (byte-reversed copy) + overlay def pointing palN at it. Only
palette files change. setup_ikemen.py routes select.def to the overlay.

usage: python act_orient.py [--fix] [--only SUBSTR] [defs...]   report: woo/roster/act_orient.tsv
"""
import argparse
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "chars_fix"
MARK = "; woo-overlay: act orientation (woo/tools/act_orient.py)"
# --fix applies only to folders whose result was checked on an in-fight screenshot (overlay_compare.py,
# 2026-10-09). The heuristic also flagged characters whose built-in palette is a placeholder, and
# "fixing" those made them worse (Tomioka, Hirako Shinji TYBW, GokuSS3, H-yuka, kump02, hachishakusama).
VERIFIED = {"E99_Kirie", "E99_Tohru", "E99_Tomioka Oni", "ElderToguro", "Basil2", "Gyomei V2", "JotaroPart6",
            "Leafa", "Megumin", "Pieck", "Rokuro", "Sasuke The Last", "smile man", "Toji", "Toguro",
            "Vegetto Xeno SSJ4", "Wamuu"}
# Found by color_scan.py (black silhouettes in fights) and fixed by dropping every act so the SFF's own
# palettes are used; checked at fight tick 150 (2026-10-09). The heuristic alone would have flipped two.
FORCE_DROP = {"E99_NarutoHokage", "Tokiko", "Android_17", "Goku Xeno SSJ3", "Gogeta_Blue",
              "Sanemi Oni"}  # 3.act scrambles the body either way it is read (user screenshot 2026-10-10)

ap = argparse.ArgumentParser()
ap.add_argument("--fix", action="store_true")
ap.add_argument("--unverified", action="store_true", help="also fix folders not in VERIFIED (check them!)")
ap.add_argument("--only", default="")
ap.add_argument("defs", nargs="*")
a = ap.parse_args()


from sff_read import def_files, v1_body, v2_body  # noqa: E402


def judge(act: bytes, base, used):
    """-> (verdict, dist_ikemen, dist_flipped, black_ikemen)"""
    act = act[:768].ljust(768, b"\0")
    ik = [tuple(act[3 * (255 - k):3 * (255 - k) + 3]) for k in range(256)]  # what Ikemen draws
    fl = [tuple(act[3 * k:3 * k + 3]) for k in range(256)]                  # normal-order reading
    tot = sum(used.values()) or 1

    def dist(p):
        return sum(n * sum(abs(p[k][c] - base[k][c]) for c in range(3)) for k, n in used.items()) / tot / 3

    black = sum(n for k, n in used.items() if ik[k] == (0, 0, 0) and base[k] != (0, 0, 0)) / tot
    black_f = sum(n for k, n in used.items() if fl[k] == (0, 0, 0) and base[k] != (0, 0, 0)) / tot
    di, df = dist(ik), dist(fl)
    if (df < di * 0.5 and di > 25 or black > 0.4 and df < di) and black_f < 0.25:
        return "FLIP", di, df, black
    if black > 0.25:  # incomplete act either way: the body would draw black -> use the SFF's own palette
        return "DROP", di, df, black
    return "ok", di, df, black


def check(def_rel: str):
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
    out = []
    for k, v in f.items():
        if not (k.startswith("pal") and k[3:].isdigit()) or not v:
            continue
        p = dp.parent / v
        if not p.is_file():
            continue
        verdict, di, df, black = judge(p.read_bytes(), base, used)
        out.append((k, v, verdict, di, df, black))
    return out


def fix(def_rel: str, flips) -> str:
    dp = ROOT / def_rel
    rel_dir = Path(def_rel).parent
    out_dir = FIX / Path(*rel_dir.parts[1:])
    old = out_dir / dp.name
    head = old.read_text(encoding="utf-8", errors="replace") if old.is_file() else MARK
    if not head.startswith((MARK, "; woo-overlay: cache")):  # cache overlays are rebuilt by char_cache.py
        return f"skip: {old.relative_to(ROOT).as_posix()} is another tool's overlay"
    out_dir.mkdir(parents=True, exist_ok=True)
    new = {}
    for key, v, verdict in flips:
        if verdict == "DROP":
            new[key] = None
            continue
        act = (dp.parent / v).read_bytes()[:768].ljust(768, b"\0")
        rev = b"".join(act[3 * (255 - k):3 * (255 - k) + 3] for k in range(256))
        (out_dir / f"woo_actfix_{key}.act").write_bytes(rev)
        new[key] = f"woo_actfix_{key}.act"
    up = "../" * len(rel_dir.parts)
    lines, in_files = [MARK], False
    for ln in dp.read_bytes().decode("latin-1").splitlines():
        s = ln.strip()
        if s.startswith("["):
            in_files = s.lower().startswith("[files]")
        elif in_files and "=" in s and not s.startswith(";"):
            k, v = s.split("=", 1)
            key = k.strip().lower()
            v = v.split(";", 1)[0].strip().strip('"')
            if key in new:
                ln = f"{k.strip()} = {new[key]}" if new[key] else f";woo: incomplete act, SFF palette used ;{ln}"
            elif v and (dp.parent / v).exists():
                ln = f"{k.strip()} = {up}{rel_dir.as_posix()}/{v}"
        lines.append(ln)
    old.write_text("\r\n".join(lines), encoding="utf-8")
    what = ",".join(f"{k}:{'drop' if v is None else 'flip'}" for k, v in new.items())
    return f"fixed {what} ->chars_fix/{old.relative_to(FIX).as_posix()}"


defs = a.defs or [r["def"] for r in csv.DictReader(open(ROOT / "woo" / "roster" / "summary.csv", encoding="utf-8"))
                  if r["def"].startswith(("chars/", "chars_fix/"))]
defs = ["chars/" + d.split("/", 1)[1] if d.startswith("chars_fix/") else d for d in defs]
defs = [d for d in defs if a.only.lower() in d.lower()]
rep = open(ROOT / "woo" / "roster" / "act_orient.tsv", "w", encoding="utf-8")
rep.write("def\tpal\tfile\tverdict\tdist_ikemen\tdist_flipped\tblack_share\n")
hits = 0
for d in defs:
    try:
        res = check(d)
    except Exception as ex:
        print(d, "-> ERROR", type(ex).__name__, ex)
        continue
    for k, v, verdict, di, df, black in res:
        rep.write(f"{d}\t{k}\t{v}\t{verdict}\t{di:.0f}\t{df:.0f}\t{black:.2f}\n")
    if Path(d).parent.name in FORCE_DROP:
        res = [(k, v, "DROP", di, df, black) for k, v, _, di, df, black in res]
    flips = [(k, v, verdict) for k, v, verdict, *_ in res if verdict in ("FLIP", "DROP")]
    if flips:
        hits += 1
        p1 = next((r for r in res if r[0] == "pal1"), res[0])
        kinds = " ".join(f"{k}:{verdict}" for k, _, verdict in flips)
        print(f"{d}: {kinds} of {len(res)} (pal1 dist {p1[3]:.0f}/{p1[4]:.0f} black {p1[5]:.0%})")
        if a.fix and (a.unverified or Path(d).parent.name in VERIFIED | FORCE_DROP):
            print("   ", fix(d, flips))
rep.close()
print(f"{hits} character(s) with wrong-order act palettes (woo/roster/act_orient.tsv)")
