"""Make characters that crash Ikemen GO at load time load, without touching the originals.

Ikemen's CNS compiler is stricter than MUGEN's. The usual culprit is a commented-out header
(";[State 0, PalFX]"): the block below it merges into the previous controller, and a parameter that
MUGEN parsed leniently (or ignored) becomes an error, e.g.
    chars/Goku SSJ10/.Files/CNS/states.cns:28656:  480,480,480  mul: Invalid data: ,
    chars/vegetaterror/IA.cns:464:  Explod  type: Invalid statetype: Explod

Loop per character: run it (vs a known-good opponent) through Ikemen, read the panic, patch a copy of
the named file in chars_fix/<char>/ (same relative path), point the overlay def at the copy, retest.
Rules (each comments lines out with ";woo:" so the change is visible and reversible):
  statedef type/movetype/physics invalid      -> drop that line from the statedef section
  "<param>: <error>" inside a controller      -> drop that parameter's lines from the section
  "Invalid state controller: X"               -> turn the controller into type = Null
Anything else is reported, not guessed.

usage: python cns_fix.py [--from-scan] [defs...]      (results: woo/roster/cns_fixes.tsv)
"""
import argparse
import csv
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "chars_fix"
MARK = "; woo-overlay: cns compat (woo/tools/cns_fix.py)"
SPARRING = "chars/Kiryu/Kiryu.def"
LOG = "woo/logs/cnsfix.log"

ap = argparse.ArgumentParser()
ap.add_argument("--from-scan", action="store_true", help="every crash row in woo/roster/crash_scan.csv")
ap.add_argument("--max-steps", type=int, default=25)
ap.add_argument("defs", nargs="*")
a = ap.parse_args()


def def_files(text: str):
    out, on = {}, False
    for ln in text.splitlines():
        s = ln.split(";", 1)[0].strip()
        if s.startswith("["):
            on = s.lower().startswith("[files]")
            continue
        if on and "=" in s:
            k, v = s.split("=", 1)
            out.setdefault(k.strip().lower(), v.strip().strip('"'))
    return out


def overlay_for(def_rel: str) -> Path:
    """The chars_fix overlay def for this character, created (pointing back at the originals) if needed."""
    if def_rel.startswith("chars_fix/"):
        p = ROOT / def_rel
    else:
        p = FIX / Path(*Path(def_rel).parts[1:])
    if p.is_file():
        text = p.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("; woo-overlay"):
            p.write_text(MARK + "\r\n" + text, encoding="utf-8")
        return p
    src = ROOT / def_rel
    rel_dir = Path(def_rel).parent
    up = "../" * len(rel_dir.parts)
    lines, in_files = [MARK], False
    for ln in src.read_bytes().decode("latin-1").splitlines():
        s = ln.strip()
        if s.startswith("["):
            in_files = s.lower().startswith("[files]")
        elif in_files and "=" in s and not s.startswith(";"):
            k, v = s.split("=", 1)
            v = v.split(";", 1)[0].strip().strip('"')
            if v and (src.parent / v).exists():
                ln = f"{k.strip()} = {up}{rel_dir.as_posix()}/{v}"
        lines.append(ln)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\r\n".join(lines), encoding="utf-8")
    return p


def original_dir(ov: Path) -> Path:
    """chars_fix/<x>/y.def -> chars/<x>/"""
    return ROOT / "chars" / ov.parent.relative_to(FIX)


def localize(ov: Path, target: Path) -> Path:
    """Make the overlay use a local copy of `target` (a file of the original character). -> copy path"""
    rel = Path(os.path.relpath(target, original_dir(ov)))  # case-insensitive on Windows, no junction resolving
    if rel.parts[0] == "..":
        raise ValueError(f"{target} is not inside the character folder")
    copy = ov.parent / rel
    if not copy.is_file():
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(target.read_bytes())
    text = ov.read_text(encoding="utf-8", errors="replace")
    out, in_files = [], False
    for ln in text.split("\r\n") if "\r\n" in text else text.split("\n"):
        s = ln.strip()
        if s.startswith("["):
            in_files = s.lower().startswith("[files]")
        elif in_files and "=" in s and not s.startswith(";"):
            k, v = s.split("=", 1)
            v = v.split(";", 1)[0].strip().strip('"')
            if v and (ov.parent / v).resolve() == target.resolve():
                ln = f"{k.strip()} = {rel.as_posix()}"
        out.append(ln)
    ov.write_text("\r\n".join(out), encoding="utf-8")
    return copy


def run_one(def_rel: str):
    """-> None if it loads and fights, else the panic message"""
    res = subprocess.run([sys.executable, str(ROOT / "woo" / "tools" / "run_match.py"), "--p1", def_rel,
                          "--p2", SPARRING, "--rounds", "1", "--speed", "10", "--time", "20", "--endafter", "20",
                          "--timeout", "240", "--log", LOG], cwd=ROOT, capture_output=True, text=True, errors="replace")
    m = re.search(r"^ENGINE PANIC: (.*)$", res.stdout, re.M)
    if m:
        return re.sub(r"^external/script/main\.lua:\d+: ", "", m.group(1).strip())
    if "loadcheck ok P1" in res.stdout or "round 1 end" in res.stdout:
        return None
    return "no fight: " + " | ".join(res.stdout.splitlines()[:6])


RX_ERR = re.compile(r"^(?P<file>.+?):(?P<line>\d+): \| (?P<body>.*)$")


def patch(ov: Path, msg: str) -> str:
    """Apply one rule for this panic. -> description, or '' when no rule fits."""
    m = RX_ERR.match(msg)
    if not m:
        return ""
    fpath = Path(os.path.normpath(ROOT / m["file"]))
    if not fpath.is_file():
        return ""
    if not os.path.relpath(fpath, FIX).startswith(".."):
        copy = fpath  # already our copy
    else:
        try:
            copy = localize(ov, fpath)
        except ValueError:  # a file outside the character folder (e.g. data/common1.cns)
            return ""
    parts = [p.strip() for p in m["body"].split(" | ")]
    last = parts[-1]
    pm = re.match(r"([A-Za-z0-9_.()]+): (.*)$", last)
    raw = copy.read_bytes()
    lines = raw.split(b"\n")  # Ikemen counts lines on \n only
    L = int(m["line"]) - 1
    head = next((i for i in range(min(L, len(lines) - 1), -1, -1) if lines[i].strip().startswith(b"[")), None)
    if head is None:
        return ""
    end = next((i for i in range(head + 1, len(lines)) if lines[i].strip().startswith(b"[")), len(lines))
    is_statedef = lines[head].strip().lower().startswith(b"[statedef")

    def key(i):
        s = lines[i].split(b";", 1)[0]
        return s.split(b"=", 1)[0].strip().lower().decode("latin-1") if b"=" in s else ""

    def drop(i):
        lines[i] = b";woo: " + lines[i]

    done = ""
    ctrl = re.search(r"Invalid state controller: (.*)$", last)
    if ctrl and not is_statedef:
        for i in range(head + 1, end):
            if key(i) == "type":
                drop(i)
                lines.insert(i + 1, b"type = Null ;woo: Ikemen has no controller " + ctrl.group(1).encode("latin-1", "replace"))
                done = f"controller '{ctrl.group(1)}' -> Null"
                break
    elif pm:
        param = pm.group(1).lower()
        hits = [i for i in range(head + 1, end) if key(i) == param]
        valid = {"type": b"scalu", "movetype": b"iahu", "physics": b"scanu"}
        if is_statedef and param in valid:
            bad = [i for i in hits if lines[i].split(b"=", 1)[1].split(b";", 1)[0].strip()[:1].lower()
                   not in valid[param]]
            hits = bad or hits
        trig = re.fullmatch(r"trigger(all|\d+)", param)
        for i in sorted(hits, reverse=True):  # inserts below shift later lines
            drop(i)
            if trig:  # never true, rather than dropping a condition (that would make it fire more)
                lines.insert(i + 1, param.encode() + b" = 0 ;woo: unparsable in Ikemen")
                end += 1
        if hits:
            done = f"{'statedef' if is_statedef else 'controller'} line {head + 1}: dropped {param} x{len(hits)} ({pm.group(2)[:60]})"
    if not done:
        return ""
    copy.write_bytes(b"\n".join(lines))
    return f"{copy.relative_to(ROOT).as_posix()}: {done}"


todo = list(a.defs)
if a.from_scan:
    todo += [r["def"] for r in csv.DictReader(open(ROOT / "woo" / "roster" / "crash_scan.csv", encoding="utf-8"))
             if r["status"] == "crash"]
report = ROOT / "woo" / "roster" / "cns_fixes.tsv"
rep = open(report, "a", encoding="utf-8")
for d in dict.fromkeys(todo):
    ov = overlay_for(d)
    target = ov.relative_to(ROOT).as_posix()
    steps, result = [], "unfixed"
    for _ in range(a.max_steps):
        err = run_one(target)
        if err is None:
            result = "loads"
            break
        what = patch(ov, err)
        print(f"{d}: {err[:150]}\n   -> {what or 'NO RULE'}", flush=True)
        if not what:
            steps.append("no rule for: " + err[:200])
            break
        steps.append(what)
    print(f"== {d}: {result} after {len(steps)} patch(es)", flush=True)
    rep.write(f"{d}\t{result}\t{target}\t{' || '.join(steps)}\n")
    rep.flush()
rep.close()
print("run setup_ikemen.py so select.def routes these characters to their overlays")
