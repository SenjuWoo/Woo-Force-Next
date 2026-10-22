"""Wire an Ikemen GO install to the Woo Force content and rebuild data/select.def.

Idempotent. Standalone by default: chars/stages/sound/data/font are real folders here and the roster
list is woo/pack_src/select.def (the pack's select.def, snapshotted 2026-10-21). To add a character,
copy it into chars/, add its line to woo/pack_src/select.def, re-run this.
--mugen DIR (optional) pulls from a MUGEN pack: it refreshes the snapshot from DIR/data/select.def,
re-copies the motif, lifebars and missing fonts, and links chars/stages/sound only if they are absent.
Never writes into the MUGEN folder.

usage: python setup_ikemen.py [--mugen DIR] [--next DIR]
"""
import argparse
import re
import sys
import shutil
import subprocess
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--mugen", default=None)  # optional source pack (e.g. a MUGEN install); the install runs without it
ap.add_argument("--next", default=str(Path(__file__).resolve().parents[2]))
args = ap.parse_args()
MUGEN, NEXT = (Path(args.mugen) if args.mugen else None), Path(args.next)
if MUGEN and not MUGEN.is_dir():
    raise SystemExit(f"MUGEN pack not found at {MUGEN}; leave out --mugen to rebuild from this folder alone")
SRC_SEL = NEXT / "woo" / "pack_src" / "select.def"  # roster list source (the pack's select.def)
ORIG = NEXT / "data" / "_ikemen_orig"  # Ikemen files we replaced, kept for rollback

# Loose MUGEN data files the motif/lifebars/characters reference.
DATA_FILES = ["Logo.sff", "logo.def", "Main_Menu.def", "Main_Menu.sff", "common.snd",
              "fightfx.air", "fightfx.sff", "system.snd", "menu.mp3", "select.mp3", "vs.mp3"]
DATA_DIRS = ["mugen1", "LIFEBARS"]


def is_junction(p: Path) -> bool:
    try:
        return p.is_junction()
    except AttributeError:  # Python < 3.12
        return p.is_symlink()


def junction(name: str) -> None:
    link, target = NEXT / name, MUGEN / name
    if is_junction(link):
        return
    if link.exists():
        if any(link.iterdir()) and (NEXT / f"{name}_samples").exists():
            # Standalone install: chars/stages/sound are real copies. Never swap them for a junction.
            print(f"{name}: real folder (standalone), left as is")
            return
        if any(link.iterdir()):
            keep = NEXT / f"{name}_samples"
            if not keep.exists():
                link.rename(keep)
                print(f"moved Ikemen {name} -> {keep.name}")
            else:
                raise SystemExit(f"{link} is a real folder and {keep} exists; resolve by hand")
        else:
            link.rmdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True,
                   stdout=subprocess.DEVNULL)
    print(f"junction {link} -> {target}")


def backup_then_copy(src: Path, dst: Path) -> None:
    if dst.exists() and not ORIG.joinpath(dst.name).exists():
        ORIG.mkdir(parents=True, exist_ok=True)
        if dst.is_dir():
            shutil.copytree(dst, ORIG / dst.name)
        else:
            shutil.copy2(dst, ORIG / dst.name)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


if MUGEN:
    for name in ("chars", "stages", "sound"):
        junction(name)
    for d in DATA_DIRS:
        backup_then_copy(MUGEN / "data" / d, NEXT / "data" / d)
    for f in DATA_FILES:
        backup_then_copy(MUGEN / "data" / f, NEXT / "data" / f)
    shutil.copy2(MUGEN / "data" / "select.def", SRC_SEL)
else:
    missing = [n for n in ("chars", "stages", "sound", "font", *(f"data/{x}" for x in DATA_DIRS + DATA_FILES), SRC_SEL)
               if not (NEXT / n).exists()]
    if missing:
        raise SystemExit(f"standalone rebuild needs {missing}; pass --mugen DIR to pull them from a pack")

# ---------------------------------------------------------------- motif: MUGEN vs Ikemen semantics
# MUGEN reads only the first value of cell.spacing and uses it for both axes; Ikemen reads x,y.
# This motif's "-1,5" made Ikemen space rows 25 px instead of 19, pushing rows 27-34 off screen.
sysdef = NEXT / "data" / "mugen1" / "system.def"
txt = sysdef.read_text(encoding="latin-1")
txt2 = re.sub(r"(?im)^(\s*cell\.spacing\s*=\s*)(-?\d+)\s*,\s*-?\d+", r"\g<1>\2,\2", txt)
if txt2 != txt:
    sysdef.write_text(txt2, encoding="latin-1")
    print("motif: cell.spacing set to MUGEN semantics (x used for both axes)")
# Team select: the motif spaces team members' names 945 px apart (off screen), so picking the 2nd member
# kept showing the 1st one's name. name.num = 1 (Ikemen) shows only the member under selection.
txt = sysdef.read_text(encoding="latin-1")
txt2 = txt
for side in ("p1", "p2"):
    if not re.search(rf"(?im)^\s*{side}\.name\.num\s*=", txt2):
        txt2 = re.sub(rf"(?im)^(\s*{side}\.name\.spacing\s*=[^\r\n]*)(\r?\n)",
                      rf"\g<1>\g<2>{side}.name.num = 1   ; woo: show the member being picked\g<2>", txt2, count=1)
if txt2 != txt:
    sysdef.write_text(txt2, encoding="latin-1")
    print("motif: team select shows the name of the member being picked (name.num = 1)")
# Ikemen draws every glyph of a font with one palette; SFF v1 menu glyphs each carry their own
# (woo/tools/font_rgba.py). Convert those to lossless truecolour SFF v2 after every copy.
sys.path.insert(0, str(Path(__file__).parent))
import font_rgba  # noqa: E402
import logo_en  # noqa: E402
# Startup splash: the pack's slides are Portuguese; use the English controls slide (woo/tools/logo_en.py).
logo_en.build()
txt = sysdef.read_text(encoding="latin-1")
txt2 = re.sub(r"(?im)^(\s*logo\.storyboard\s*=\s*)[^;\r\n]*", r"\g<1>logo_en.def ", txt)
if txt2 != txt:
    sysdef.write_text(txt2, encoding="latin-1")
    print("motif: logo.storyboard -> logo_en.def (English controls splash)")
for _fdef, fsff in font_rgba.font_files():
    b = fsff.read_bytes()
    if b[15] == 1 and len({x["data"][-768:] for x in font_rgba.read_sff1(b)
                           if len(x["data"]) > 769 and x["data"][-769] == 0x0C and not x["same"]}) > 1:
        print("motif font:", font_rgba.convert(fsff))

# Fonts: add MUGEN-only fonts; where both exist Ikemen's file stays (engine/debug use it).
for f in (MUGEN / "font").iterdir() if MUGEN else ():
    if f.is_file() and not (NEXT / "font" / f.name).exists():
        shutil.copy2(f, NEXT / "font" / f.name)

# ---------------------------------------------------------------- character validation
# Ikemen panics at boot if any listed character's sprite/anim cannot be opened, and it
# reads .def paths as UTF-8, else Shift-JIS (src/common.go decodeShiftJIS). MUGEN-era defs
# are often Latin-1 ("Animações.air"), which decodes to garbage. Each character either
# passes, gets a UTF-8 overlay def in chars_fix/ pointing back at the original files, or
# becomes a blank cell. The MUGEN folder is never written.
FIX = NEXT / "chars_fix"
CRITICAL = {"sprite", "anim", "cmd", "cns", "st", "stcommon"} | {f"st{i}" for i in range(10)}
PATH_KEYS = CRITICAL | {"sound"} | {f"pal{i}" for i in range(1, 13)}


def ikemen_decode(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("shift_jis", errors="replace")


def def_files(raw: bytes):
    """{key: value_bytes} from the first [Files] section. First duplicate key wins,
    like Ikemen's ReadIniSection and MUGEN."""
    out, on, done = {}, False, False
    for ln in raw.splitlines():
        s = ln.split(b";", 1)[0].strip()
        if s.startswith(b"["):
            if on:
                done = True
            on = s.lower().startswith(b"[files]") and not done
            continue
        if on and b"=" in s:
            k, v = s.split(b"=", 1)
            out.setdefault(k.strip().lower().decode("latin-1"), v.strip().strip(b'"').strip())
    return out


def resolves(name: str, def_dir: Path, key: str) -> bool:
    for base in (def_dir, NEXT, NEXT / "data"):
        p = base / name
        if p.is_file() or (key == "stcommon" and p.with_name(p.name + ".zss").is_file()):
            return True
    return False


def check_char(def_rel: str):
    """-> (status, detail): ok | fixed | broken"""
    defp = NEXT / def_rel
    raw = defp.read_bytes()
    files = def_files(raw)
    bad, latin_ok, swap = [], True, {}
    for k, v in files.items():
        if k not in PATH_KEYS or not v:
            continue
        if resolves(ikemen_decode(v), defp.parent, k):
            continue
        if k not in CRITICAL:
            continue  # missing sound/palette: Ikemen loads the char anyway
        bad.append(k)
        if resolves(v.decode("latin-1"), defp.parent, k):
            continue
        st = files.get("st", b"")
        if k == "cns" and st and resolves(ikemen_decode(st), defp.parent, "st"):
            swap["cns"] = st.decode("latin-1")  # constants file missing: st holds [Data] too
        else:
            latin_ok = False
    sff = files.get("sprite", b"")
    for name in (ikemen_decode(sff), sff.decode("latin-1")):
        p = defp.parent / name
        if name and p.is_file():
            with open(p, "rb") as f:
                if f.read(12) != b"ElecbyteSpr\x00":
                    return "broken", f"sprite {name} is not an SFF"
            break
    if not bad:
        return "ok", ""
    if not latin_ok:
        return "broken", "missing " + ",".join(bad)
    # UTF-8 overlay that points back at the original folder
    rel_dir = Path(def_rel).parent  # chars/<...>
    out_dir = FIX / Path(*rel_dir.parts[1:])
    up = "../" * (len(rel_dir.parts))  # back to NEXT from chars_fix/<...>
    text = raw.decode("latin-1")
    lines = []
    in_files = False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("["):
            in_files = s.lower().startswith("[files]")
        elif in_files and "=" in s and not s.startswith(";"):
            k, v = s.split("=", 1)
            v = swap.get(k.strip().lower(), v.split(";", 1)[0].strip().strip('"'))
            if k.strip().lower() in PATH_KEYS and v and (defp.parent / v).exists():
                ln = f"{k.strip()} = {up}{rel_dir.as_posix()}/{v}"  # else data/ or root: keep as is
        lines.append(ln)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / defp.name).write_text("\r\n".join(lines), encoding="utf-8")
    return "fixed", "chars_fix/" + (out_dir / defp.name).relative_to(FIX).as_posix()


def resolve_entry(entry: str):
    if entry.lower().endswith(".def"):
        cands = [f"chars/{entry}", entry]
    else:
        cands = [f"chars/{entry}/{Path(entry).name}.def", f"chars/{entry}.def"]
    for c in cands:
        if (NEXT / c).is_file():
            return c
    return None


# select.def: MUGEN file with `empty` -> skipslot (same grid layout), unloadable characters
# -> skipslot or their chars_fix overlay, written as UTF-8 so non-ASCII paths open.
src = SRC_SEL.read_bytes().decode("latin-1")
lines = src.split("\r\n")
n_empty, report, section = 0, [], ""
for i, ln in enumerate(lines):
    s = ln.split(";", 1)[0].strip()
    if s.startswith("["):
        section = s.lower()
        continue
    if section != "[characters]" or not s:
        continue
    entry = s.split(",")[0].strip().replace("\\", "/")
    low = entry.lower()
    if low == "empty":
        lines[i] = ln.lower().replace("empty", "skipslot")
        n_empty += 1
        continue
    if low in ("randomselect", "skipslot", "dummyslot"):
        continue
    rel = resolve_entry(entry)
    overlay = FIX / Path(*Path(rel).parts[1:]) if rel else None
    if rel is None:
        status, detail = "broken", "def not found"
    elif overlay.is_file() and overlay.read_text(encoding="utf-8", errors="replace").startswith("; woo-overlay"):
        # made by a fixer tool (e.g. sff1_fix.py): keep it, route the select entry to it
        status, detail = "fixed", "chars_fix/" + overlay.relative_to(FIX).as_posix()
    else:
        status, detail = check_char(rel)
    if status == "fixed":
        lines[i] = ln.replace(s.split(",")[0].strip(), detail, 1)
    elif status == "broken":
        lines[i] = "skipslot ;woo: " + ln.strip() + " -- " + detail
    if status != "ok":
        report.append(f"{status}\t{entry}\t{detail}")
sel = NEXT / "data" / "select.def"
if sel.exists() and not (ORIG / "select.def").exists():
    ORIG.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sel, ORIG / "select.def")
sel.write_bytes("\r\n".join(lines).encode("utf-8"))
(NEXT / "woo" / "roster").mkdir(parents=True, exist_ok=True)
(NEXT / "woo" / "roster" / "select_fixes.tsv").write_text("\n".join(report) + "\n", encoding="utf-8")
print(f"select.def written (UTF-8): {n_empty} empty -> skipslot, "
      f"{sum(r.startswith('fixed') for r in report)} overlay defs, "
      f"{sum(r.startswith('broken') for r in report)} unloadable -> skipslot (woo/roster/select_fixes.tsv)")
