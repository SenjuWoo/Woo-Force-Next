"""Static roster health scan: finds characters likely to look or play broken in Ikemen GO.

Reads woo/roster/chars/*.json (from roster_analyzer.py) plus each character's SFF/ACT files.
Writes woo/roster/health_static.csv: one row per character, `issues` = ;-separated codes.

Codes
  portrait_missing     no 9000,0 sprite (select screen and lifebar face will be blank)
  sff1_linked_base     SFF v1 whose sprite 0,0 is a link: Ikemen's v1 loader reads the shared
                       base palette from 0,0, so faces/palettes can come out wrong (Cell Max)
  act_bad              pal1 .act is missing, the wrong size, or nearly one colour
  giant                xscale/yscale >= 1.5 (boss-sized; camera and spacing look odd)
  no_moves             no usable move found (the brain cannot pilot it; own AI only)
  no_ai_no_moves       no AI and no moves: it will stand still
usage: python health_scan.py [--root "S:\\Woo Force Next"]
"""
import argparse
import csv
import json
import struct
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
a = ap.parse_args()
ROOT = Path(a.root)


def def_files(defp: Path):
    out, on = {}, False
    for ln in defp.read_bytes().splitlines():
        s = ln.split(b";", 1)[0].strip()
        if s.startswith(b"["):
            on = s.lower().startswith(b"[files]")
            continue
        if on and b"=" in s:
            k, v = s.split(b"=", 1)
            out.setdefault(k.strip().lower().decode("latin-1"), v.strip().strip(b'"').decode("utf-8", "replace"))
    return out


def sff_info(p: Path):
    """-> (version, has_portrait, base_is_link)"""
    with open(p, "rb") as f:
        h = f.read(64)
        if h[:12] != b"ElecbyteSpr\x00":
            return None, False, False
        ver = h[15]
        if ver == 1:
            nimg, first = struct.unpack("<II", h[20:28])
            off, portrait, base_link = first, False, False
            for _ in range(nimg):
                if not off:
                    break
                f.seek(off)
                sh = f.read(19)
                if len(sh) < 19:
                    break
                nxt, ln, _, _, grp, img, link, _ = struct.unpack("<IIhhHHHB", sh)
                if grp == 9000 and img == 0:
                    portrait = True
                if grp == 0 and img == 0 and ln == 0:
                    base_link = True
                off = nxt
            return 1, portrait, base_link
        # v2: sprite node table
        f.seek(0)
        h = f.read(64)
        spr_off, nspr = struct.unpack("<II", h[36:44])
        f.seek(spr_off)
        portrait = False
        for _ in range(nspr):
            node = f.read(28)
            if len(node) < 28:
                break
            grp, img = struct.unpack("<HH", node[:4])
            if grp == 9000 and img == 0:
                portrait = True
                break
        return 2, portrait, False


def act_bad(p: Path):
    if not p.is_file():
        return True
    b = p.read_bytes()
    if len(b) < 768:
        return True
    cols = {b[i:i + 3] for i in range(0, 768, 3)}
    return len(cols) < 6


rows = []
for jf in sorted((ROOT / "woo" / "roster" / "chars").glob("*.json")):
    prof = json.loads(jf.read_text(encoding="utf-8"))
    defp = ROOT / prof["def"]
    issues = []
    try:
        files = def_files(defp)
        sff = defp.parent / files.get("sprite", "")
        ver, portrait, base_link = sff_info(sff) if sff.is_file() else (None, False, False)
        if not portrait:
            issues.append("portrait_missing")
        if ver == 1 and base_link:
            issues.append("sff1_linked_base")
        if files.get("pal1") and act_bad(defp.parent / files["pal1"]):
            issues.append("act_bad")
    except Exception as ex:  # report, don't stop the scan
        ver = None
        issues.append(f"scan_error:{type(ex).__name__}")
    if max(prof.get("xscale", 1) or 1, 1) >= 1.5:
        issues.append("giant")
    usable = [m for m in prof["moves"] if not m.get("shadow") and not m.get("charge")]
    if not usable:
        issues.append("no_ai_no_moves" if prof["ai"]["quality"] == "none" else "no_moves")
    rows.append([prof["def"], prof["display"], prof["series"], ver, ";".join(issues)])

out = ROOT / "woo" / "roster" / "health_static.csv"
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["def", "display", "series", "sff", "issues"])
    w.writerows(rows)
from collections import Counter
c = Counter(i.split(":")[0] for r in rows for i in r[4].split(";") if i)
print(f"{len(rows)} characters; flagged {sum(1 for r in rows if r[4])}")
for k, v in c.most_common():
    print(f"  {k}: {v}")
