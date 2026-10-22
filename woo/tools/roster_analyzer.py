"""Woo Force roster analyzer.

Reads every character listed in data/select.def (read-only) and writes one brain
profile per character: move catalog (command -> state -> hitbox/damage/cost),
AI grade, power tier, persona. The Lua brain loads these at match start.

usage: python roster_analyzer.py [--root "S:\\Woo Force Next"] [--only SUBSTR] [--selftest]
Output: <root>/woo/roster/chars/<slug>.json and <root>/woo/roster/summary.csv
"""
import argparse
import identity  # per-character style, signature roles, traits
import configparser
import csv
import json
import re
import sys
from pathlib import Path

# Input bits shared with data/woo_brain.zss (B/F are facing-relative).
BIT = {"U": 1, "D": 2, "B": 4, "F": 8, "a": 16, "b": 32, "c": 64, "x": 128,
       "y": 256, "z": 512, "s": 1024, "d": 2048, "w": 4096}
DIRS = {"U": "U", "D": "D", "B": "B", "F": "F", "UF": "UF", "UB": "UB", "DF": "DF", "DB": "DB"}
COMMON_RETURN = {0, 10, 11, 12, 20, 40, 45, 50, 51, 52, 100, 105, 106, 110}


# ---------------------------------------------------------------- text parsing
def read_text(p: Path) -> str:
    b = p.read_bytes()
    if b.startswith(b"\xef\xbb\xbf"):
        b = b[3:]
    try:  # chars_fix overlays and data/select.def are UTF-8; MUGEN files mostly Latin-1
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1")


def strip_comment(line: str) -> str:
    q = False
    for i, ch in enumerate(line):
        if ch == '"':
            q = not q
        elif ch == ";" and not q:
            return line[:i]
    return line


def sections(text: str):
    """[(header, [(key_lower, value)])] in file order, duplicates kept."""
    out, cur = [], None
    for raw in text.splitlines():
        line = strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("[") and "]" in line:
            cur = (line[1:line.index("]")].strip(), [])
            out.append(cur)
        elif cur is not None and "=" in line:
            k, v = line.split("=", 1)
            cur[1].append((k.strip().lower(), v.strip()))
        elif cur is not None and cur[0].lower().startswith("begin action"):
            cur[1].append(("", line))  # AIR frame lines have no '='
    return out


def first(kv, key, default=None):
    for k, v in kv:
        if k == key:
            return v
    return default


def unquote(v):
    return v.strip().strip('"').strip() if v else v


def num(v, default=None):
    if v is None:
        return default
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", v)
    return float(m.group(1)) if m else default


def find_file(char_dir: Path, name: str, game_root: Path):
    if not name:
        return None
    name = unquote(name).replace("\\", "/")
    for base in (char_dir, game_root / "data", game_root):
        p = base / name
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------- commands
def compile_command(cmd: str, remap: dict):
    """MUGEN command string -> [[bits, frames], ...] macro. None if unparseable."""
    steps = []
    elems = [e.strip() for e in cmd.split(",") if e.strip()]
    if not elems:
        return None
    for e in elems:
        e = e.replace(" ", "")
        hold = e.startswith("/")
        e = e.lstrip("/>")
        charge = 0
        m = re.match(r"~(\d*)", e)
        if m:
            charge = int(m.group(1) or 0)
            e = e[m.end():]
        e = e.lstrip("$/>")
        bits = 0
        for part in e.split("+"):
            part = part.strip().lstrip("$/~>")
            if part in DIRS:
                for ch in part:
                    bits |= BIT[ch]
            elif part.lower() in ("a", "b", "c", "x", "y", "z", "s", "d", "w") and len(part) == 1:
                bits |= BIT[remap.get(part.lower(), part.lower())]
            else:
                return None
        frames = charge + 2 if charge else 2
        steps.append([bits, frames, hold])
    # Same input twice in a row needs a release frame in between (F,F dash / a,a).
    out = []
    for bits, frames, hold in steps:
        if out and out[-1][0] == bits:
            out.append([0, 1])
        out.append([bits, frames])
    return out


def macro_len(m):
    return sum(f for _, f in m)


def tighten(m, limit):
    """Squeeze 2-frame steps to 1 frame when the command window is short."""
    if macro_len(m) <= limit:
        return m
    return [[b, 1 if f == 2 else f] for b, f in m]


def is_hold_only(cmd: str) -> bool:
    return all(e.strip().startswith("/") for e in cmd.split(",") if e.strip())


# ---------------------------------------------------------------- triggers
RX_CMD = re.compile(r'command\s*(!?=)\s*"([^"]+)"', re.I)
RX_AI_NEG = re.compile(r"!\s*ailevel\b|\bailevel\s*(?:=|==)\s*0\b|\bailevel\s*<\s*1\b|\bailevel\s*<=\s*0\b", re.I)
RX_AI = re.compile(r"\bailevel\b", re.I)
RX_POWER = re.compile(r"\bpower\s*(>=|>)\s*(\d+)", re.I)
# Conditions a direct state entry could violate (cooldown vars, helpers, timers, randomness...)
RX_IMPURE = re.compile(r"\b(?:f?var|sysf?var|map|helper|numhelper|numproj\w*|numexplod|numtarget|target|"
                       r"enemy\w*|partner|p2\w*|life|random|time|animtime|animelem\w*|prevstateno|"
                       r"gametime|teammode|numenemy|ishelper|parent|root|playerid\w*|const)\b", re.I)
RX_TIME = re.compile(r"(?<![\w,.])time\s*(>=|>)\s*(\d+)", re.I)
RX_POWERMAX = re.compile(r"\bpower\s*>=?\s*\(?\s*powermax\s*(?:/\s*(\d+))?", re.I)
RX_STYPE = re.compile(r"\bstatetype\s*(!?=)\s*([SCAL])\b", re.I)
# `(?<![\w,.])` skips redirected triggers such as helper(5),stateno / partner,ctrl.
RX_STATENO = re.compile(r"(?<![\w,.])stateno\s*=\s*(\[\s*\d+\s*,\s*\d+\s*\]|\d+(?:\s*\|\|\s*(?:stateno\s*=\s*)?\d+)*)", re.I)
RX_CTRL = re.compile(r"(?<![\w,.])ctrl\b(?!\s*=\s*0)", re.I)
RX_CONTACT = re.compile(r"\bmove(?:contact|hit|guarded)\b", re.I)
# AI that reads the situation (vs. "random < N && p2bodydist x = [a,b]" only).
RX_SITUATIONAL = re.compile(r"p2movetype|movetype\s*=\s*a|p2statetype|inguarddist|p2stateno|gethitvar|"
                            r"hitdefattr|numproj|frontedge|backedge|p2life|enemynear\s*,\s*(?:movetype|vel|stateno)|"
                            r"p2dist\s*y|vel\s*y", re.I)
RX_VAR = re.compile(r"(!?)\s*var\s*\(\s*(\d+)\s*\)\s*(=|!=|>=|>|<|<=)?\s*(-?\d+)?", re.I)


def positive_ai(cond: str, ai_vars) -> bool:
    if RX_AI.search(RX_AI_NEG.sub("", cond)):
        return True
    for neg, n, op, val in RX_VAR.findall(cond):
        if neg or int(n) not in ai_vars:
            continue
        if not op or op in (">", ">=") or (op == "=" and val != "0") or (op == "!=" and val == "0"):
            return True
    return False


def negative_ai(cond: str) -> bool:
    return bool(RX_AI_NEG.search(cond))


RX_COND_VALUE = re.compile(r"^\s*(?:cond|ifelse)\s*\((.*),\s*(\d+)\s*,\s*(\d+)\s*\)\s*$", re.I)
RX_AIR_TEST = re.compile(r"pos\s*y\s*(?:!=\s*0|<\s*0)|statetype\s*=\s*a\b", re.I)


def value_targets(val):
    """ChangeState value -> [(state, forced_statetype|None)]. Literal ints, plus
    cond(pos y != 0, 600, 200) / ifelse(statetype = A, 600, 200) air/ground splits."""
    if val is None:
        return []
    if re.fullmatch(r"\s*\d+\s*", val):
        return [(int(val), None)]
    m = RX_COND_VALUE.match(val)
    if not m:
        return []
    a, b = int(m.group(2)), int(m.group(3))
    if RX_AIR_TEST.search(m.group(1)):
        return [(a, "A"), (b, "G")]
    return [(a, None), (b, None)]


RX_BODYX_RANGE = re.compile(r"p2(?:body)?dist\s*x\s*=\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]", re.I)
RX_BODYX_CMP = re.compile(r"p2(?:body)?dist\s*x\s*(<=|<|>=|>)\s*(-?\d+)", re.I)
RX_P2STYPE = re.compile(r"p2statetype\s*(!?=)\s*([SCAL])\b", re.I)
RX_P2MTYPE = re.compile(r"p2movetype\s*(!?=)\s*([AIH])\b", re.I)
# Conditions that only steer an AI's choice; everything else in an AI route is a real rule
RX_AI_STEER = re.compile(r"\b(?:random|ailevel|p2bodydist|p2dist|p2statetype|p2movetype|p2stateno|"
                         r"enemynear\s*,\s*\w+|roundstate|alive|pos\s*y)\b", re.I)


def add_ai_hint(hints, target, vstype, text, powermax):
    lo, hi = None, None
    for a, b in RX_BODYX_RANGE.findall(text):
        lo, hi = int(a), int(b)
    for op, n in RX_BODYX_CMP.findall(text):
        n = int(n)
        if op in ("<", "<="):
            hi = n if hi is None else min(hi, n)
        else:
            lo = n if lo is None else max(lo, n)
    h = hints.setdefault(target, {"x": None, "p2s": set(), "p2m": set(), "power": 10 ** 9,
                                  "stype": vstype, "pure": True})
    if lo is not None or hi is not None:
        lo, hi = (lo if lo is not None else -40), (hi if hi is not None else 400)
        h["x"] = [lo, hi] if h["x"] is None else [min(h["x"][0], lo), max(h["x"][1], hi)]
    for op, st in RX_P2STYPE.findall(text):
        if op == "=":
            h["p2s"].add(st.upper())
    for op, mt in RX_P2MTYPE.findall(text):
        if op == "=":
            h["p2m"].add(mt.upper())
    power = max([int(p) + (1 if op == ">" else 0) for op, p in RX_POWER.findall(text)] or [0])
    pm = RX_POWERMAX.search(text)
    if pm:
        power = max(power, int(powermax / int(pm.group(1) or 1)))
    h["power"] = min(h["power"], power)
    stripped = RX_AI_STEER.sub("", RX_CMD.sub("", text))
    h["pure"] = h["pure"] and not RX_IMPURE.search(stripped)


def neg_controllers(sect_list):
    """[(stateno, controller)] for statedefs -1/-2/-3. Controllers belong to the statedef
    above them by position: authors write [State -1, x], [State 0] or anything else."""
    out, cur = [], None
    for head, kv in sect_list:
        m = re.match(r"statedef\s+(-?\d+)", head, re.I)
        if m:
            cur = int(m.group(1))
            continue
        if cur not in (-1, -2, -3) or not re.match(r"state\s", head, re.I):
            continue
        out.append((cur, controller(kv)))
    return out


def controller(kv):
    c = {"type": (first(kv, "type") or "").lower(), "all": [], "groups": {}, "kv": kv}
    for k, v in kv:
        if k == "triggerall":
            c["all"].append(v)
        else:
            t = re.match(r"trigger(\d+)$", k)
            if t:
                c["groups"].setdefault(int(t.group(1)), []).append(v)
    return c


def statedefs(sect_list):
    """{stateno: (statedef_kv, [controller dicts])} for non-negative states."""
    out, cur_no = {}, None
    for head, kv in sect_list:
        m = re.match(r"statedef\s+(-?\d+)", head, re.I)
        if m:
            cur_no = int(m.group(1))
            if cur_no not in out:
                out[cur_no] = (kv, [])
            continue
        m = re.match(r"state\s+(-?\d+)", head, re.I)
        if m and cur_no is not None and cur_no in out:
            out[cur_no][1].append(controller(kv))
    return out


# ---------------------------------------------------------------- AIR
def parse_air(text: str):
    """{action: {'frames': [(time, [clsn1 boxes])]}}"""
    acts = {}
    for head, kv in sections(text):
        m = re.match(r"begin\s+action\s+(-?\d+)", head, re.I)
        if not m:
            continue
        frames, c1_default, c1_next, pending, pending_default = [], [], None, [], False
        for k, v in kv:
            line = v if k == "" else f"{k}={v}"
            low = line.lower().replace(" ", "")
            if low.startswith("clsn1default:"):
                pending, pending_default = [], True
                continue
            if low.startswith("clsn1:"):
                pending, pending_default = [], False
                continue
            if low.startswith("clsn2"):
                pending = None if not low.startswith("clsn2[") else pending
                continue
            mb = re.match(r"clsn1\[\d+\]=(.+)", low)
            if mb:
                vals = [num(x, 0) for x in mb.group(1).split(",")[:4]]
                if len(vals) == 4 and pending is not None:
                    pending.append(vals)
                    if pending_default:
                        c1_default = pending
                    else:
                        c1_next = pending
                continue
            if low.startswith("loopstart") or low.startswith("interpolate"):
                continue
            parts = line.split(",")
            if len(parts) >= 5 and k == "":
                t = num(parts[4], 1)
                boxes = c1_next if c1_next is not None else c1_default
                frames.append((int(t), boxes or []))
                c1_next = None
        acts[int(m.group(1))] = frames
    return acts


RX_STATEDEF = re.compile(r"^\s*\[\s*statedef\s+(-?\d+)\s*[,\]]", re.I)
# command names that are just inputs ("QCF_x", "236a", "fwd_b", "x+y") say nothing about the move.
# A tokenizer, not a regex: overlapping tokens (f / ff / fwd) in a repeated group backtrack exponentially.
INPUT_TOKENS = ("qc", "hc", "dp", "rdp", "f", "b", "d", "u", "df", "db", "uf", "ub", "fwd", "back", "down", "up", "hold",
                "charge", "ff", "bb", "start", "taunt", "cpu", "ai", "com", "a", "c", "x", "y", "z", "s",
                "1", "2", "3", "4", "5", "6", "7", "8", "9")
RX_INPUT_SEP = re.compile(r"[\s_~+/\-]+")


def is_input_name(name: str) -> bool:
    """True when every separator-split chunk of `name` is made only of input tokens (at least one chunk)."""
    chunks = [c for c in RX_INPUT_SEP.split(name.lower()) if c]
    if not chunks:
        return False
    for c in chunks:
        ok = [True] + [False] * len(c)  # ok[i]: c[:i] splits into tokens (linear dynamic programme)
        for i in range(len(c)):
            if ok[i]:
                for tok in INPUT_TOKENS:
                    if c.startswith(tok, i):
                        ok[i + len(tok)] = True
        if not ok[len(c)]:
            return False
    return True


RX_JUNK_NAME = re.compile(r"^(state|statedef|anim|hit|attack|move|special|super|normal|cpu|ai)?[\s_\d.:-]*$", re.I)


def state_names(text: str):
    """{state: name} from the author's comments: '[Statedef 1000] ;Kamehameha' or a comment line right
    above/below. Rejects numbers, separators and generic words."""
    out, lines = {}, text.splitlines()
    for i, ln in enumerate(lines):
        m = RX_STATEDEF.match(ln)
        if not m:
            continue
        cands = []
        if ";" in ln:
            cands.append(ln.split(";", 1)[1])
        for j in (i - 1, i - 2, i + 1):
            if 0 <= j < len(lines) and lines[j].strip().startswith(";"):
                cands.append(lines[j].strip().lstrip(";"))
        for c in cands:
            c = re.sub(r"[-=*#_~]{2,}", " ", c).strip(" ;:-=*#[]()\t")
            if 3 <= len(c) <= 40 and re.search(r"[A-Za-z]{3}", c) and not RX_JUNK_NAME.match(c):
                out[int(m.group(1))] = c
                break
    return out


def anim_profile(frames, xscale):
    """startup = ticks before the first Clsn1 frame, active = first contiguous Clsn1 run."""
    if not frames:
        return None
    t, startup, active, active_open = 0, None, 0, True
    xs, ytop, ybot = [], [], []
    for time_, boxes in frames:
        dur = 60 if time_ < 0 else max(time_, 1)  # -1 = hold forever; cap it
        if boxes:
            if startup is None:
                startup = t
            if active_open:
                active += dur
            for x1, y1, x2, y2 in boxes:
                xs.append(max(x1, x2) * xscale)
                ytop.append(min(y1, y2))
                ybot.append(max(y1, y2))
        elif startup is not None:
            active_open = False
        t += dur
    total = t
    if startup is None:
        return {"total": total}
    return {"startup": startup, "active": active, "total": total,
            "reach": round(max(xs)), "ytop": round(min(ytop)), "ybot": round(max(ybot))}


# ---------------------------------------------------------------- one character
def analyze(def_path: Path, game_root: Path, rules):
    cdir = def_path.parent
    d = sections(read_text(def_path))
    info = next((kv for h, kv in d if h.lower() == "info"), [])
    files = next((kv for h, kv in d if h.lower() == "files"), [])
    name = unquote(first(info, "name", def_path.stem))
    display = unquote(first(info, "displayname", name)) or name
    lc = first(info, "localcoord")
    localcoord = int(num(lc.split(",")[0], 320)) if lc else 320

    state_paths, seen = [], set()
    for k, v in files:
        if k in ("cns", "st", "cmd", "stcommon") or re.match(r"st\d+$", k):
            p = find_file(cdir, v, game_root)
            # stcommon only when the char ships its own (some hide State -1 in it);
            # the shared data/common1.cns has no moves.
            if k == "stcommon" and (p is None or cdir not in p.parents):
                continue
            if p and p.resolve() not in seen:
                seen.add(p.resolve())
                state_paths.append(p)
    cmd_path = find_file(cdir, first(files, "cmd"), game_root)
    air_path = find_file(cdir, first(files, "anim"), game_root)

    file_sects = [sections(read_text(p)) for p in state_paths]
    snames = {}
    for p in state_paths:
        snames.update(state_names(read_text(p)))
    reused = {}
    for v in snames.values():
        reused[v] = reused.get(v, 0) + 1
    snames = {k: v for k, v in snames.items() if reused[v] < 3}  # a header comment, not a move name

    def move_name(st, cmds):
        if snames.get(st):
            return snames[st]
        for c in cmds:
            if not is_input_name(c) and len(c) > 3:
                return c
        return ""
    all_sects = [s for fs in file_sects for s in fs]

    # [Data] / [Size] from the cns
    data = next((kv for h, kv in all_sects if h.lower() == "data"), [])
    size = next((kv for h, kv in all_sects if h.lower() == "size"), [])
    xscale = num(first(size, "xscale"), 1.0) or 1.0
    powermax = int(num(first(data, "power"), 3000) or 3000)

    # Commands
    remap, defaults_time, commands = {}, 15, {}
    if cmd_path:
        for h, kv in sections(read_text(cmd_path)):
            hl = h.lower()
            if hl == "remap":
                for k, v in kv:
                    if k in BIT and v.strip().lower() in BIT and v.strip().lower() != k:
                        remap[v.strip().lower()] = k  # char sees v when player presses k
            elif hl == "defaults":
                defaults_time = int(num(first(kv, "command.time"), 15))
            elif hl == "command":
                nm = unquote(first(kv, "name"))
                cs = first(kv, "command")
                if nm and cs:
                    commands.setdefault(nm, []).append(
                        (cs, int(num(first(kv, "time"), defaults_time))))

    # AI flag vars (WinMUGEN style "CPU" commands -> VarSet)
    ai_cmd_names = {n for n, defs in commands.items()
                    if re.match(r"^(cpu|ai|com|cp)[\s_]*\d+$", n, re.I)
                    or any(len(cs.split(",")) >= 9 for cs, _ in defs)}
    neg = [x for fs in file_sects for x in neg_controllers(fs)]
    ai_vars = set()
    for _, c in neg:
        if c["type"] in ("varset", "varadd"):
            conds = " ".join(c["all"] + sum(c["groups"].values(), []))
            refs = {m[1] for m in RX_CMD.findall(conds) if m[0] == "="}
            if refs & ai_cmd_names or (RX_AI.search(conds) and not negative_ai(conds)):
                v = first(c["kv"], "v")
                vm = re.search(r"var\s*\(\s*(\d+)", " ".join(f"{k}={v}" for k, v in c["kv"]), re.I)
                if v is not None and num(v) is not None:
                    ai_vars.add(int(num(v)))
                elif vm:
                    ai_vars.add(int(vm.group(1)))

    ai_ctrls, ai_situational, ai_combo, ai_guard, gated, moves = 0, 0, 0, 0, False, {}
    routes = []  # ctrl routes in State -1 order, for shadow detection
    ai_hints = {}  # target state -> hints harvested from the character's own AI
    for s, c in neg:
        groups = list(c["groups"].values()) or [[]]
        ai_all = positive_ai(" ".join(c["all"]), ai_vars)
        ai_groups = [positive_ai(" ".join(g), ai_vars) for g in groups]
        if c["type"] in ("changestate", "selfstate") and (ai_all or any(ai_groups)):
            ai_ctrls += 1  # has an AI route (InSeph-style chars mix human and AI routes)
            ai_text = " ".join(c["all"] + [x for g, a in zip(groups, ai_groups) if a or ai_all for x in g])
            ai_situational += bool(RX_SITUATIONAL.search(ai_text))
            ai_combo += bool(RX_CONTACT.search(ai_text))
            v = num(first(c["kv"], "value"), -1)
            ai_guard += bool(120 <= v <= 155 or re.search(r"inguarddist", ai_text, re.I))
            # The author's own AI routes are expert hints: range windows and situations per move
            if c["type"] == "changestate":
                for target, vstype in value_targets(first(c["kv"], "value")):
                    if target in COMMON_RETURN or 120 <= target <= 155:
                        continue
                    for g, a in zip(groups, ai_groups):
                        if a or ai_all:
                            add_ai_hint(ai_hints, target, vstype, " ".join(c["all"] + g), powermax)
        if c["type"] != "changestate" or ai_all:
            continue
        for target, vstype in value_targets(first(c["kv"], "value")):
            for g in groups:
                conds = c["all"] + g
                text = " ".join(conds)
                if positive_ai(" ".join(g), ai_vars):
                    continue
                if negative_ai(text):
                    gated = True
                cmds = sorted({m[1] for m in RX_CMD.findall(text) if m[0] == "=" and m[1] in commands})
                if not cmds or target in COMMON_RETURN:
                    continue
                frm = []
                for m in RX_STATENO.findall(text):
                    rng = re.match(r"\[\s*(\d+)\s*,\s*(\d+)\s*\]", m)
                    if rng:
                        frm.append([int(rng.group(1)), int(rng.group(2))])
                    else:
                        frm += [[int(x), int(x)] for x in re.findall(r"\d+", m)]
                power = max([int(p) + (1 if op == ">" else 0) for op, p in RX_POWER.findall(text)] or [0])
                pm = RX_POWERMAX.search(text)  # power >= powermax / 2
                if pm:
                    power = max(power, int(powermax / int(pm.group(1) or 1)))
                stype = "any"
                for op, st in RX_STYPE.findall(text):
                    st = st.upper()
                    if op == "=":
                        stype = st
                    elif st == "A":
                        stype = "G"
                stype = vstype or stype
                key = (target, tuple(cmds))
                mv = moves.setdefault(key, {"st": target, "cmd": cmds, "from": [], "ctrl": False,
                                            "contact": False, "power": power, "stype": stype})
                mv["power"] = min(mv["power"], power)  # cheapest route counts
                # pure = the route checks only what the brain can check itself (command, power,
                # statetype, ctrl, cancel window). Pure moves may be entered directly.
                mv["pure"] = mv.get("pure", True) and not RX_IMPURE.search(RX_CMD.sub("", text))
                if RX_CTRL.search(text):
                    mv["ctrl"] = True
                    not_cmds = {m[1] for m in RX_CMD.findall(text) if m[0] == "!="}
                    routes.append((s, set(cmds), not_cmds, stype, power, not frm, key))
                if frm:
                    mv["from"] += frm
                    mv["contact"] = mv["contact"] or bool(RX_CONTACT.search(text))

    # Shadowing: State -1 fires the first matching ChangeState. A ctrl route that needs
    # only a subset of a later route's commands (plain "c" before "holddown"+"c"), with no
    # `command != ...` exclusion, makes the later move unreachable from neutral.
    def stype_covers(a, b):
        return a == "any" or a == b or (a == "G" and b in ("S", "C", "G"))
    for i, (s2, c2, _, st2, p2, _, key2) in enumerate(routes):
        for s1, c1, n1, st1, p1, plain1, key1 in routes[:i]:
            if (s1 == s2 and plain1 and c1 < c2 and not (n1 & c2) and p1 <= p2
                    and stype_covers(st1, st2) and key1 != key2):
                moves[key2]["shadow"] = True
                break

    # State and anim data for move targets
    sdefs = {}
    for fs in file_sects:  # first definition of a state wins
        for no, v in statedefs(fs).items():
            sdefs.setdefault(no, v)
    acts = parse_air(read_text(air_path)) if air_path else {}

    def macro_for(cmd_names):
        """Command names that must all be active -> one input macro. Hold-only commands
        (/$D "holddown") are OR-ed into every frame of the others."""
        parts = []
        for cname in cmd_names:
            cs, ctime = commands[cname][0]
            for alt, alt_time in commands[cname]:  # prefer the shortest variant
                if len(alt.split(",")) < len(cs.split(",")):
                    cs, ctime = alt, alt_time
            m = compile_command(cs, remap)
            if m is None:
                return None
            parts.append({"hold": m[0][0]} if is_hold_only(cs) else {"seq": tighten(m, ctime)})
        hold = sum(x.get("hold", 0) for x in parts)
        seq = [s for x in parts if "seq" in x for s in x["seq"]] or [[hold, 2]]
        seq = [[b | hold, f] for b, f in seq]
        # "holddown"-style commands need the direction already held, not pressed with the button
        return ([[hold, 3]] + seq) if hold and seq[0][0] != hold else seq

    def state_info(no, depth=0, seen_st=None):
        seen_st = seen_st or set()
        if no in seen_st or no not in sdefs or depth > 3:
            return None
        seen_st.add(no)
        kv, ctrls = sdefs[no]
        r = {"anim": None, "dmg": 0, "hits": 0, "proj": False, "helper": False, "super": False,
             "throw": False, "invuln": False, "guard": "", "cost": 0, "lunge": 0.0, "gain": 0,
             "stype": (first(kv, "type") or "S").strip().upper()[:1], "attr": "",
             "launch": False, "knock": False}
        a = first(kv, "anim")
        if a and re.fullmatch(r"\s*\d+\s*", a):
            r["anim"] = int(a)
        pa = num(first(kv, "poweradd"), 0)
        if pa < 0:
            r["cost"] = -pa
        for c in ctrls:
            t = c["type"]
            ckv = c["kv"]
            if t == "hitdef":
                dm = first(ckv, "damage")
                r["dmg"] += int(num(dm.split(",")[0], 0) or 0) if dm else 0
                r["hits"] += 1
                attr = (first(ckv, "attr") or "").replace(" ", "").upper()
                r["attr"] = r["attr"] or attr
                kind = attr.split(",")[1] if "," in attr else ""
                if kind[1:2] == "T":  # S,NT / S,HT = throw (p2stateno alone is just a custom hit state)
                    r["throw"] = True
                g = (first(ckv, "guardflag") or "").upper()
                r["guard"] = r["guard"] or g
                gv = first(ckv, "ground.velocity") or ""
                if "," in gv and (num(gv.split(",")[1], 0) or 0) * 320 / localcoord <= -5.5:
                    r["launch"] = True  # sends a grounded enemy high: a juggle starter
                if (first(ckv, "fall") or "").strip() == "1" or \
                        (first(ckv, "ground.type") or "").strip().lower() == "trip":
                    r["knock"] = True
            elif t == "projectile":
                r["proj"] = True
                dm = first(ckv, "damage")
                r["dmg"] += int(num(dm.split(",")[0], 0) or 0) if dm else 0
                r["hits"] += 1
            elif t == "helper":
                r["helper"] = True
            elif t == "superpause":
                r["super"] = True
            elif t == "poweradd":
                v = num(first(ckv, "value"), 0)
                if v < 0:
                    r["cost"] = max(r["cost"], -v)
                elif v > 0:
                    r["gain"] += v  # ki charge: a state that keeps adding power
            elif t in ("nothitby", "hitby"):
                r["invuln"] = True
            elif t == "changeanim" and r["anim"] is None:
                v = first(ckv, "value")
                if v and re.fullmatch(r"\s*\d+\s*", v):
                    r["anim"] = int(v)
            elif t == "velset":
                x = num(first(ckv, "x"), None)
                if x is None and first(ckv, "value"):
                    x = num(first(ckv, "value").split(",")[0], None)
                if x:
                    r["lunge"] = max(r["lunge"], x)
        if r["hits"] == 0 or r["anim"] is None:  # follow multi-state moves
            for c in ctrls:
                if c["type"] == "changestate":
                    v = first(c["kv"], "value")
                    if v and re.fullmatch(r"\s*\d+\s*", v) and int(v) not in COMMON_RETURN:
                        sub = state_info(int(v), depth + 1, seen_st)
                        if sub:
                            for k in ("dmg", "hits"):
                                r[k] += sub[k]
                            for k in ("proj", "helper", "super", "throw", "invuln", "launch", "knock"):
                                r[k] = r[k] or sub[k]
                            r["anim2"] = r.get("anim2") or sub["anim"]
                            r["cost"] = max(r["cost"], sub["cost"])
                            r["guard"] = r["guard"] or sub["guard"]
                            break
        return r

    def anim_of(si):
        for an in (si["anim"], si.get("anim2")):
            if an is not None and an in acts:
                ap = anim_profile(acts[an], xscale)
                if ap and "startup" in ap:
                    return ap
        return None

    def hint_fields(st):
        h = ai_hints.get(st)
        if not h:
            return {}
        return {"ai_x": h["x"], "ai_p2s": sorted(h["p2s"]), "ai_p2m": sorted(h["p2m"])}

    out_moves = []
    for mv in moves.values():
        si = state_info(mv["st"])
        if si is None:
            continue
        prof = anim_of(si)
        seq = macro_for(mv["cmd"])
        if not seq:
            continue
        e = {"st": mv["st"], "cmd": mv["cmd"], "in": seq, "ctrl": mv["ctrl"],
             "from": mv["from"], "contact": mv["contact"],
             "power": int(max(mv["power"], si["cost"])), "stype": mv["stype"],
             "dmg": si["dmg"], "hits": si["hits"], "proj": si["proj"],
             "helper": si["helper"] and si["hits"] == 0,  # attack (or just FX) lives in a helper
             "super": si["super"] or max(mv["power"], si["cost"]) >= 1000, "throw": si["throw"],
             "invuln": si["invuln"], "guard": si["guard"], "lunge": si["lunge"], "air": si["stype"] == "A",
             "shadow": mv.get("shadow", False), "pure": mv.get("pure", False),
             "charge": si["gain"] > 0 and si["hits"] == 0 and not si["proj"],
             "launch": si["launch"], "knock": si["knock"], "name": move_name(mv["st"], mv["cmd"])}
        if prof:
            e.update(prof)
        e.update(hint_fields(mv["st"]))
        out_moves.append(e)

    # AI-only moves: states the author's AI enters that no command reaches. Usable when the
    # route's real conditions (beyond AI steering: random, distance, enemy state) are safe.
    have = {m["st"] for m in out_moves}
    for st, h in ai_hints.items():
        if st in have or not h["pure"]:
            continue
        si = state_info(st)
        if si is None or (si["hits"] == 0 and not si["proj"] and not si["helper"]):
            continue
        e = {"st": st, "cmd": [], "in": [], "ctrl": True, "from": [], "contact": False,
             "power": int(max(h["power"], si["cost"])), "stype": h["stype"] or "G",
             "dmg": si["dmg"], "hits": si["hits"], "proj": si["proj"],
             "helper": si["helper"] and si["hits"] == 0,
             "super": si["super"] or max(h["power"], si["cost"]) >= 1000, "throw": si["throw"],
             "invuln": si["invuln"], "guard": si["guard"], "lunge": si["lunge"], "air": si["stype"] == "A",
             "shadow": False, "pure": True, "aionly": True,
             "launch": si["launch"], "knock": si["knock"], "name": move_name(st, [])}
        e.update(anim_of(si) or {})
        e.update(hint_fields(st))
        out_moves.append(e)

    out_moves.sort(key=lambda m: (m["power"], m["st"]))

    # Chains: attack states that change state on a command press inside the state itself
    # (state 200 + "a" after 10 ticks -> 210). The usual anime-MUGEN combo system.
    chains = {}
    for no, (kv, ctrls) in sdefs.items():
        if no < 0 or no in COMMON_RETURN:
            continue
        for c in ctrls:
            if c["type"] != "changestate":
                continue
            val = first(c["kv"], "value")
            if val is None or not re.fullmatch(r"\s*\d+\s*", val) or int(val) in COMMON_RETURN or int(val) == no:
                continue
            for g in (list(c["groups"].values()) or [[]]):
                text = " ".join(c["all"] + g)
                is_ai = positive_ai(" ".join(g), ai_vars) or positive_ai(" ".join(c["all"]), ai_vars)
                after = max([int(n) + (1 if op == ">" else 0) for op, n in RX_TIME.findall(text)] or [0])
                power = max([int(p) + (1 if op == ">" else 0) for op, p in RX_POWER.findall(text)] or [0])
                si = state_info(int(val)) or {}
                if is_ai:
                    # the author's AI combo route: entered directly when its rules are safe
                    rest = RX_TIME.sub("", RX_AI_STEER.sub("", RX_CMD.sub("", text)))
                    if RX_IMPURE.search(rest):
                        continue
                    seq, pure = [], True
                else:
                    cmds = sorted({m[1] for m in RX_CMD.findall(text) if m[0] == "=" and m[1] in commands})
                    if not cmds:
                        continue
                    seq = macro_for(cmds)
                    if not seq:
                        continue
                    pure = False
                edge = {"to": int(val), "in": seq, "after": after, "power": power,
                        "contact": bool(RX_CONTACT.search(text)), "dmg": si.get("dmg", 0),
                        "super": bool(si.get("super")) or power >= 1000, "pure": pure, "ai": is_ai}
                lst = chains.setdefault(str(no), [])
                old = next((x for x in lst if x["to"] == edge["to"]), None)
                if old is None:
                    lst.append(edge)
                elif edge["pure"] and not old["pure"]:
                    lst[lst.index(old)] = edge  # prefer the 0-frame AI route to the same state

    life = int(num(first(data, "life"), 1000) or 1000)
    attack = int(num(first(data, "attack"), 100) or 100)
    defence = int(num(first(data, "defence"), 100) or 100)
    # strong = enough AI that reads the opponent, chains combos and guards; the common
    # "random roll + distance window" AI is graded basic and gets piloted by the brain.
    strong = (ai_ctrls >= rules["strong_ai"] and ai_situational >= ai_ctrls * rules["strong_situational"]
              and ai_combo >= rules["strong_combo"] and ai_guard >= 1)
    ai_quality = "strong" if strong else ("basic" if ai_ctrls else "none")
    prof = {
        "def": None, "name": name, "display": display, "author": unquote(first(info, "author", "")),
        "mugenversion": unquote(first(info, "mugenversion", "")), "localcoord": localcoord,
        "xscale": xscale, "life": life, "attack": attack, "defence": defence, "powermax": powermax,
        "ai": {"quality": ai_quality, "ctrls": ai_ctrls, "situational": ai_situational,
               "combo": ai_combo, "guard": ai_guard, "gated": gated},
        "moves": out_moves,
        "chains": chains,
    }
    return prof


# ---------------------------------------------------------------- persona / tier
def norm(s: str) -> str:
    return " " + " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split()) + " "


def classify(prof, folder, rules):
    hay = norm(f"{folder} {prof['name']} {prof['display']}")

    def hit(words):  # whole words; a trailing * makes it a prefix ("ssj*" hits ssj4)
        return any((f" {w[:-1]}" if w.endswith("*") else f" {w} ") in hay for w in words)

    tier = 3
    for t in range(6, 0, -1):
        if hit(rules["tier"].get(t, [])):
            tier = t
            break
    # Stats nudge: absurd life/attack usually marks an OP edit.
    if prof["life"] >= 3000 or prof["attack"] >= 200:
        tier = max(tier, 5)
    persona = None
    for p, words in rules["persona"]:
        if hit(words):
            persona = p
            break
    if persona is None:
        mv = [m for m in prof["moves"] if not m["super"]]
        proj = sum(1 for m in mv if m["proj"])
        lunge = sum(1 for m in mv if m.get("lunge", 0) > 4)
        persona = "zoner" if proj >= 2 and proj > lunge else ("rushdown" if lunge >= 3 else "brawler")
    series = "other"
    for s, words in rules["series"]:
        if hit(words):
            series = s
            break
    return tier, persona, series


def load_lore(path: Path):
    """woo/lore.txt rows -> [(key, match_words, style, groups, partners, rivals)] in file order."""
    out = []
    if not path.exists():
        return out
    split = lambda v: [w.strip().lower() for w in v.split(",") if w.strip()]
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        cols = [c.strip() for c in raw.split("|")] + [""] * 6
        words = [norm(w).strip() + ("*" if w.strip().endswith("*") else "") for w in cols[1].split(",") if w.strip()]
        out.append((cols[0].lower(), words, cols[2].lower(), split(cols[3]), split(cols[4]), split(cols[5])))
    # woo/duos.txt: canon duos/teams, one clique per line, merged into everyone's partners (both ways)
    duos = path.with_name("duos.txt")
    if duos.exists():
        by_key = {}
        for row in out:
            by_key.setdefault(row[0], row)
        for raw in duos.read_text(encoding="utf-8").splitlines():
            keys = [k.strip().lower() for k in raw.split("#", 1)[0].split(",") if k.strip()]
            for k in keys:
                if k not in by_key:
                    print(f"duos.txt: unknown lore key {k!r} in: {raw.strip()}")
            for a in keys:
                for b in keys:
                    if a != b and a in by_key and b in by_key and b not in by_key[a][4]:
                        by_key[a][4].append(b)
    return out


# Lore power for characters the name rules left on the default tier 3: their faction's standing.
GROUP_TIER = [
    (5, ("gods", "god of destruction", "gods of destruction", "angels", "otsutsuki", "yonko", "anti-spirals")),
    (4, ("admirals", "warlords", "upper moons", "espada", "hashira", "gold_saints", "gold saints", "kage", "sannin",
         "akatsuki", "jinchuriki", "worst generation", "worst_generation", "curses", "wandenreich", "ten commandments",
         "ten_commandments", "homunculi", "phantom troupe", "seven deadly sins", "spriggan_12", "dio forces",
         "pride troopers", "visored", "beasts pirates", "whitebeard pirates", "roger pirates", "zodiac",
         "chimera ants")),  # roster's ants are Meruem and the Royal Guard only
]


def group_tier(lore):
    groups = set((lore or {}).get("groups", []))
    for tier, names in GROUP_TIER:
        if groups & set(names):
            return tier
    return 3


def load_series(root: Path):
    """woo/series.txt + lore.txt section headers -> (fandom order, {lore key: series}, {folder: series})."""
    order, keys, defs, part, last = [], {}, {}, None, None
    for raw in (root / "woo" / "series.txt").read_text(encoding="utf-8").splitlines():
        ln = raw.split("#", 1)[0].rstrip()
        if not ln.strip():
            continue
        if ln.startswith("["):
            part = ln.strip("[] ").lower()
            continue
        if part == "order":
            order.append(ln.strip().lower())
            continue
        if part not in ("keys", "defs"):  # [style] is select_order.py's
            continue
        if ln[0].isspace() and last:  # continuation line of the previous list
            series, names = last, ln
        else:
            series, names = (x.strip() for x in ln.split("=", 1))
            last = series = series.lower()
        for n in (x.strip() for x in names.split(",")):
            if n:
                (keys if part == "keys" else defs)[n.lower()] = series
    # a lore section named after one series ("Bleach", "One Piece (gaps)") gives its keys that series
    sec = None
    for raw in (root / "woo" / "lore.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"#\s*-{10,}\s*(.+)$", raw)
        if m:
            sec = re.sub(r"\s*\(.*\)$", "", m.group(1)).strip().lower()
        elif raw.strip() and not raw.startswith("#") and "|" in raw and sec in order:
            keys.setdefault(raw.split("|", 1)[0].strip().lower(), sec)
    return order, keys, defs


def apply_lore(prof, folder, lore):
    hay = norm(f"{folder} {prof['name']} {prof['display']}")
    for key, words, style, groups, partners, rivals in lore:
        if any((f" {w[:-1]}" if w.endswith("*") else f" {w} ") in hay for w in words):
            if style:
                prof["persona"] = style
            prof["lore"] = {"key": key, "groups": groups, "partners": partners, "rivals": rivals}
            return


def load_rules(path: Path):
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    cp.read(path, encoding="utf-8")
    def words(v):
        return [norm(w).strip() + ("*" if w.strip().endswith("*") else "") for w in v.split(",") if w.strip()]
    rules = {"tier": {}, "persona": [], "series": [], "override": {},
             "strong_ai": cp.getint("analyzer", "strong_ai_ctrls", fallback=12),
             "strong_situational": cp.getfloat("analyzer", "strong_situational_share", fallback=0.4),
             "strong_combo": cp.getint("analyzer", "strong_combo_ctrls", fallback=3)}
    for s in cp.sections():
        if s.startswith("tier."):
            rules["tier"][int(s[5:])] = words(cp[s].get("match", ""))
        elif s.startswith("persona."):
            rules["persona"].append((s[8:], words(cp[s].get("match", ""))))
        elif s.startswith("series."):
            rules["series"].append((s[7:], words(cp[s].get("match", ""))))
        elif s.startswith("override."):
            rules["override"][s[9:].lower()] = dict(cp[s])
    return rules


# ---------------------------------------------------------------- roster
def select_entries(select_def: Path):
    out, on = [], False
    for raw in read_text(select_def).splitlines():
        line = strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("["):
            on = line.lower().startswith("[characters]")
            continue
        if on:
            entry = line.split(",")[0].strip().replace("\\", "/")
            if entry.lower() not in ("randomselect", "empty", "skipslot", "dummyslot"):
                out.append(entry)
    return out


def resolve_def(root: Path, entry: str):
    cands = [f"chars/{entry}", entry] if entry.lower().endswith(".def") else \
        [f"chars/{entry}/{Path(entry).name}.def", f"chars/{entry}.def"]
    for c in cands:
        if (root / c).is_file():
            return c
    return None


def slug(def_rel: str) -> str:
    """Collision-free file name: ASCII letters lowered, every other byte -> _xx (matches woo_brain.lua)."""
    out = []
    for b in def_rel.replace("\\", "/").encode("utf-8"):
        c = chr(b)
        if "A" <= c <= "Z":
            out.append(c.lower())
        elif "a" <= c <= "z" or "0" <= c <= "9":
            out.append(c)
        else:
            out.append(f"_{b:02x}")
    return "".join(out)


def selftest():
    m = compile_command("~D, DF, F, x", {})
    assert m == [[2, 2], [10, 2], [8, 2], [128, 2]], m
    assert compile_command("F, F", {}) == [[8, 2], [0, 1], [8, 2]]
    assert compile_command("~30$B, F, a+b", {}) == [[4, 32], [8, 2], [48, 2]]
    assert compile_command("x", {"x": "y"}) == [[256, 2]]
    assert positive_ai("ailevel > 4", set()) and not positive_ai("ailevel = 0", set())
    assert not positive_ai("!ailevel", set()) and positive_ai("var(59) = 1", {59})
    air = parse_air("[Begin Action 200]\nClsn2: 1\n Clsn2[0] = 0,0,10,-60\n200,0, 0,0, 3\n"
                    "Clsn1: 1\n Clsn1[0] = 10,-50,40,-40\n200,1, 0,0, 4\n200,2, 0,0, 6\n")
    p = anim_profile(air[200], 1.0)
    assert p == {"startup": 3, "active": 4, "total": 13, "reach": 40, "ytop": -50, "ybot": -40}, p
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--only", default="")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    root = Path(a.root)
    rules = load_rules(root / "woo" / "profiles.ini")
    lore = load_lore(root / "woo" / "lore.txt")
    _, ser_keys, ser_defs = load_series(root)
    outdir = root / "woo" / "roster" / "chars"
    outdir.mkdir(parents=True, exist_ok=True)
    rows, fails, seen = [], [], set()
    entries = select_entries(root / "data" / "select.def")
    for entry in entries:
        if a.only and a.only.lower() not in entry.lower():
            continue
        rel = resolve_def(root, entry)
        if rel is None or rel.lower() in seen:
            if rel is None:
                fails.append((entry, "def not found"))
            continue
        seen.add(rel.lower())
        try:
            prof = analyze(root / rel, root, rules)
        except Exception as ex:  # one broken char must not stop the roster
            fails.append((entry, f"{type(ex).__name__}: {ex}"))
            continue
        folder = Path(rel).parent.name
        prof["def"] = rel
        prof["tier"], prof["persona"], prof["series"] = classify(prof, folder, rules)
        apply_lore(prof, folder, lore)
        if prof["tier"] == 3:
            prof["tier"] = group_tier(prof.get("lore"))
        if "/.chars/" in rel.lower():
            prof["series"] = rel.split("/")[2].lower()
        fandom = ser_defs.get(folder.lower()) or ser_keys.get((prof.get("lore") or {}).get("key", ""))
        if fandom:
            prof["series"] = fandom
        prof["mode"] = "coach" if prof["ai"]["quality"] == "strong" else "pilot"
        ov = rules["override"].get(folder.lower()) or rules["override"].get(prof["name"].lower())
        if ov:
            for k in ("tier", "persona", "mode", "series"):
                if k in ov:
                    prof[k] = int(ov[k]) if k == "tier" else ov[k]
        prof["identity"] = identity.build(prof)
        (outdir / f"{slug(rel)}.json").write_text(json.dumps(prof, separators=(",", ":")), encoding="utf-8")
        rows.append([rel, prof["display"], prof["series"], prof["tier"], prof["persona"], prof["mode"],
                     prof["ai"]["quality"], prof["ai"]["ctrls"], int(prof["ai"]["gated"]), len(prof["moves"]),
                     sum(1 for m in prof["moves"] if "reach" in m), prof["life"], prof["attack"],
                     prof["ai"]["situational"], prof["ai"]["combo"], prof["ai"]["guard"]])
    with open(root / "woo" / "roster" / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["def", "display", "series", "tier", "persona", "mode", "ai", "ai_ctrls", "gated",
                    "moves", "moves_with_hitbox", "life", "attack", "ai_situational", "ai_combo", "ai_guard"])
        w.writerows(rows)
        for e, why in fails:
            w.writerow([e, "FAILED", why])
    print(f"profiles: {len(rows)}  failed: {len(fails)}")
    for e, why in fails[:15]:
        print("  FAIL", e, why)


if __name__ == "__main__":
    sys.exit(main())
