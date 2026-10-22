"""Build the playable-game download: the install folder with only the characters listed in docs/ROSTER.tsv, as ZIP parts.

GitHub Releases take files up to 2 GiB, so the game is a set of independent ZIP parts (part01 holds the engine, motif, brain,
stages and sound; the rest hold characters). Extract every part into the same empty folder, in any order; no joining needed.

What goes in:
  * Ikemen GO, the motif, fonts, libraries, the brain (external/mods, data/woo_brain.zss), woo/ knowledge files and the two analyzer scripts
  * stages/ and sound/
  * for every character in the roster file: its chars/<folder> (and chars_fix/<folder> overlay when the roster entry uses one),
    plus any other folder its files point into (reported)
  * woo/roster/chars/<profile>.json for those characters, and gate_learned.tsv reduced to them
  * a release data/select.def (listed characters only; the freed cells at the end of the grid become blank), a release
    save/config.ini (MSAA 4 so it starts on any GPU), a first-read README.txt
What stays out: every other character, logs, tests, rollbacks, saves, the engine's moved-aside samples, generated overlays of other characters.

usage: python make_game_pack.py --version 0.9.0-beta.1 --out <folder> [--roster docs/ROSTER.tsv] [--part-mb 1900] [--dry-run]
"""
import argparse
import csv
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import roster_analyzer  # noqa: E402  (slug)
from sff_read import def_files  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("--version", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--roster", default=str(ROOT / "docs" / "ROSTER.tsv"))
ap.add_argument("--part-mb", type=int, default=1900, help="close a ZIP part once it holds this many MB (GitHub's limit is 2048)")
ap.add_argument("--dry-run", action="store_true", help="report only: counts, sizes, dependencies; write nothing")
a = ap.parse_args()

ROOT_FILES = ["Ikemen_GO.exe", "LICENSES.txt", "README.Ikemen-GO.md"]
ROOT_DIRS = ["data", "external", "font", "lib", "video", "stages", "sound"]
SKIP_DIRS = {"data/_ikemen_orig"}  # the engine files this project replaced, kept only for rollback
WOO_FILES = ["profiles.ini", "lore.txt", "lore_extra.txt", "duos.txt", "series.txt", "tools/roster_analyzer.py", "tools/identity.py"]


def norm(p: str) -> str:
    return p.replace("\\", "/").lower()


roster = list(csv.DictReader(open(a.roster, encoding="utf-8"), delimiter="\t"))
included = {norm(r["def"]) for r in roster}
print(f"roster file: {len(roster)} characters")

# ------------------------------------------------------------------ which folders
folders = set()  # relative dirs: chars/<x>, chars_fix/<x>
for r in roster:
    d = r["def"].replace("\\", "/")
    top, rest = d.split("/", 1)
    folder = "/".join(rest.split("/")[:-1])
    folders.add(f"chars/{folder}")
    if top == "chars_fix":
        folders.add(f"chars_fix/{folder}")
def canonical(fs):  # real on-disk casing, one entry per folder (the disk ignores case, the ZIP does not)
    out = {}
    for f in fs:
        if (ROOT / f).exists():
            c = (ROOT / f).resolve().relative_to(ROOT).as_posix()
            out[c.lower()] = c
    return set(out.values())


folders = canonical(folders)
extra = {}
for r in roster:  # files the def points at that live in another character's folder
    dp = ROOT / r["def"]
    own = {f for f in folders if (ROOT / f) == dp.parent or Path(f).name == dp.parent.name}
    for k, v in def_files(dp).items():
        if not v or not (k in ("sprite", "anim", "cmd", "cns", "stcommon", "sound") or re.fullmatch(r"st\d*|pal\d+", k)):
            continue
        name = v if isinstance(v, str) else v.decode("utf-8", "replace")
        t = (dp.parent / name).resolve()
        try:
            rel = t.relative_to(ROOT).as_posix()
        except ValueError:
            continue
        if not t.exists() or rel.startswith(("data/", "stages/", "sound/")) or "/" not in rel:
            continue
        top = "/".join(rel.split("/")[:-1])
        if rel.split("/")[0] in ("chars", "chars_fix") and not any(rel.lower().startswith(f.lower() + "/") for f in folders):
            extra.setdefault(top, set()).add(r["def"])
folders = canonical(folders | set(extra))
print(f"folders: {len(folders)} ({len(extra)} pulled in because an included character points into them)")
for top, who in list(extra.items())[:8]:
    print("   extra:", top, "<-", sorted(who)[:2])


def files_in(rel: str):
    base = ROOT / rel
    for p in sorted(base.rglob("*")):
        if p.is_file():
            yield p


# ------------------------------------------------------------------ release select.def
def release_select() -> bytes:
    raw = (ROOT / "data" / "select.def").read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().lower() == "[characters]") + 1
    end = next((i for i in range(start, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    while end > start and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith(";")):
        end -= 1
    body = lines[start:end]

    def kind(ln):
        k = ln.split(";", 1)[0].split(",", 1)[0].strip().lower()
        return "skip" if k == "skipslot" else "random" if k == "randomselect" else "char" if k else "other"

    keep = [ln for ln in body if kind(ln) == "char" and norm(ln.split(",", 1)[0].strip()) in included]
    randoms = sum(1 for ln in body if kind(ln) == "random")
    fill = ["randomselect"] * randoms + keep
    cells = sum(1 for ln in body if kind(ln) in ("char", "random"))
    assert len(fill) <= cells, (len(fill), cells)
    fill += ["skipslot"] * (cells - len(fill))  # the freed cells sit at the end of the grid: blank rows, not holes
    it = iter(fill)
    new_body = [next(it) if kind(ln) in ("char", "random") else ln for ln in body]
    print(f"select.def: {len(keep)} characters, {randoms} random slots, {cells - randoms - len(keep)} blank cells at the end")
    missing = included - {norm(ln.split(',', 1)[0].strip()) for ln in keep}
    if missing:
        print("   NOT in select.def (roster entries without a cell):", len(missing), sorted(missing)[:5])
    return (nl.join(lines[:start] + new_body + lines[end:]) + nl).encode("utf-8")


def release_config() -> bytes:
    t = (ROOT / "save" / "config.ini").read_bytes().decode("utf-8")
    t2, n = re.subn(r"(?m)^(MSAA\s*=\s*)\d+", r"\g<1>4", t)
    assert n == 1, "MSAA line not found"
    return t2.encode("utf-8")


def release_gate() -> bytes:
    out = []
    p = ROOT / "woo" / "roster" / "gate_learned.tsv"
    for ln in p.read_text(encoding="utf-8").splitlines():
        if norm(ln.split("\t", 1)[0]) in included:
            out.append(ln)
    return ("\n".join(out) + "\n").encode("utf-8")


def readme_txt() -> bytes:
    return f"""WOO FORCE NEXT {a.version}  (BETA)
================================

This is BETA software: unfinished, with known bugs, tested on one machine only. Behaviour and balance change between betas.
Known problems: https://github.com/SenjuWoo/Woo-Force-Next/blob/main/docs/KNOWN-ISSUES.md

HOW TO INSTALL
1. Make an empty folder anywhere (not inside Program Files), for example C:\\Games\\Woo Force Next.
2. Extract EVERY part of the download (Woo-Force-Next-Game-{a.version}-part01.zip, part02, ...) into that same folder, in any order.
   Windows Explorer can do it, so can 7-Zip. Let it merge folders.
3. Check the files against SHA256SUMS.txt if you like.
4. Start Ikemen_GO.exe. For AI-vs-AI pick Watch mode. The brain writes woo\\logs\\brain.log.

The game opens full screen at 1920x1080. Edit save\\config.ini for window, resolution and controls (MSAA is set to 4 so it starts on any GPU).
Characters, stages, sounds and the screenpack belong to their authors; the character list with authors is in ROSTER.tsv.
Ikemen GO is MIT licensed (LICENSES.txt). The brain and tools are MIT licensed (https://github.com/SenjuWoo/Woo-Force-Next).
If you are a rights holder and want something removed, open an issue at that address and it will be removed.
""".replace("\n", "\r\n").encode("utf-8")


# ------------------------------------------------------------------ plan the file list
plan = []  # (arcname, Path or bytes)
for f in ROOT_FILES:
    if (ROOT / f).exists():
        plan.append((f, ROOT / f))
for d in ROOT_DIRS:
    for p in files_in(d):
        rel = p.relative_to(ROOT).as_posix()
        if any(rel.startswith(s + "/") for s in SKIP_DIRS) or rel in ("data/select.def", "data/woo_brain.zss.bak") or rel.endswith(".bak"):
            continue
        plan.append((rel, p))
plan.append(("data/select.def", release_select()))
plan.append(("save/config.ini", release_config()))
for f in WOO_FILES:
    plan.append((f"woo/{f}", ROOT / "woo" / f))
plan.append(("woo/roster/gate_learned.tsv", release_gate()))
plan.append(("woo/logs/.keep", b""))
n_prof = 0
for r in roster:
    pj = ROOT / "woo" / "roster" / "chars" / f"{roster_analyzer.slug(r['def'])}.json"
    if pj.exists():
        plan.append((f"woo/roster/chars/{pj.name}", pj))
        n_prof += 1
print(f"profiles: {n_prof}/{len(roster)}")
plan.append(("ROSTER.tsv", Path(a.roster)))
plan.append(("README.txt", readme_txt()))
head_n = len(plan)
seen = {arc.lower() for arc, _ in plan}
for fol in sorted(folders):
    for p in files_in(fol):
        arc = p.relative_to(ROOT).as_posix()
        if arc.lower() not in seen:
            seen.add(arc.lower())
            plan.append((arc, p))
total = sum((len(x) if isinstance(x, bytes) else x.stat().st_size) for _, x in plan)
print(f"files: {len(plan)} (engine/data/stages/sound/woo: {head_n}); uncompressed {total / 2**30:.1f} GiB")
if a.dry_run:
    sys.exit(0)

# ------------------------------------------------------------------ write the parts
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
limit = a.part_mb * 1000 * 1000
parts, z, n = [], None, 0


def open_part():
    global z, n
    if z:
        z.close()
    n += 1
    path = out / f"Woo-Force-Next-Game-{a.version}-part{n:02d}.zip"
    parts.append(path)
    z = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True)


open_part()
done = 0
for arc, src in plan:
    if z.fp.tell() >= limit:
        open_part()
        print(f"  part {n - 1} closed at {parts[-2].stat().st_size / 2**30:.2f} GiB; {done}/{len(plan)} files", flush=True)
    if isinstance(src, bytes):
        zi = zipfile.ZipInfo(arc, date_time=(2026, 10, 22, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.external_attr = 0o644 << 16
        z.writestr(zi, src)
    else:
        z.write(src, arc)
    done += 1
z.close()
sums = []
for p in parts:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    sums.append(f"{h.hexdigest()}  {p.name}")
    assert p.stat().st_size < 2 * 1024**3, p
(out / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
json.dump({"version": a.version, "parts": [p.name for p in parts], "files": len(plan)}, open(out / "manifest.json", "w"))
print(f"wrote {len(parts)} parts, {sum(p.stat().st_size for p in parts) / 2**30:.1f} GiB, SHA256SUMS.txt")
