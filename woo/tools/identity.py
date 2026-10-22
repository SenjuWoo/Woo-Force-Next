"""Per-character identity for the brain: personal style, signature roles and lore traits.

Every character starts from its archetype (persona), then its brain parameters are shifted by what its own
moveset says (projectile density, lunge speed, throws, reversals, supers, chains, air game, speed, stats)
and by the traits of its factions (woo/lore.txt groups). A small deterministic per-character offset keeps
even clones (the many Goku edits) from playing identically.

Roles pick the character's OWN moves for the moments that define it: finisher, comeback, anti-air,
reversal, zoning tool, opener, launcher, combo ender, taunt. Names come from the author's comments when
present (roster_analyzer.state_names), so the overlay/log can say "FINISHER Kamehameha".

Output (profile JSON `identity`):
  style  {axis: delta}  added to the persona's values in woo_brain.lua (axes as in its PERSONA table)
  traits [flag]         proud / ruthless / loyal / disciplined / aloof / feral / showoff
  roles  {role: state}  sig {role: name}   hold: preferred distance (px, char units) for the overmind
"""
import hashlib

AXES = ("agg", "range", "proj", "jump", "dash", "super", "combo", "punish", "guard", "risk", "taunt", "team")
LIMIT = 0.3  # no character drifts further than this from its archetype on any axis

# faction substrings -> (trait flag, style shifts). Flags change behaviour in the brain, shifts change odds.
TRAITS = [
    (("saiyan", "uchiha", "kara", "otsutsuki"), "proud", {"agg": .06, "super": .10, "taunt": .05}),
    (("god", "angel", "universe 6", "pride troopers"), "aloof", {"risk": .12, "taunt": .10, "agg": -.06}),
    (("akatsuki", "espada", "upper moon", "curse", "homunculi", "phantom troupe", "league of villains",
      "monster", "wandenreich", "ten commandments", "ten_commandments", "oracion", "majin", "red ribbon",
      "dio forces", "cp9", "donquixote", "beasts pirates", "germa", "demons", "taka", "otogakure"),
     "ruthless", {"punish": .10, "combo": .06, "guard": -.03}),
    (("straw hat", "z fighters", "team 7", "class 1a", "ua", "survey corps", "fairy", "black bulls", "black_bulls",
      "team guy", "team minato", "ino shika cho", "ash_team", "seven deadly sins", "joestar", "jujutsu high",
      "konoha", "heroes"), "loyal", {"team": .10}),
    (("marine", "admiral", "hashira", "gotei", "gold_saints", "kage", "sannin", "sorcerers", "hokage",
      "demon slayer", "visored", "hunters", "spriggan"), "disciplined", {"guard": .06, "punish": .06, "taunt": -.04}),
    (("titan", "chimera", "jinchuriki", "hollow", "beast"), "feral", {"agg": .08, "guard": -.08, "risk": -.05}),
    (("passione", "morioh", "duelists", "guilty_gear", "servants"), "showoff", {"taunt": .08, "combo": .04}),
]


def _clamp(v, lo=-LIMIT, hi=LIMIT):
    return max(lo, min(hi, v))


def _offset(key: str):
    """Deterministic +-0.04 per axis from the def path: two edits of one character still differ a little."""
    h = hashlib.sha1(key.encode("utf-8")).digest()
    return {a: (h[i] / 255 - 0.5) * 0.08 for i, a in enumerate(AXES)}


def build(prof):
    moves = [m for m in prof.get("moves", []) if not m.get("shadow") and not m.get("charge")]
    attacks = [m for m in moves if (m.get("hits", 0) > 0 or m.get("proj") or m.get("helper"))]
    normal = [m for m in attacks if not m.get("super")]
    n = max(1, len(normal))
    style = {a: 0.0 for a in AXES}

    proj_share = sum(1 for m in normal if m.get("proj")) / n
    style["proj"] += (proj_share - 0.15) * 1.6
    style["range"] += (proj_share - 0.15) * 1.0
    lunges = sorted((m.get("lunge", 0) for m in normal), reverse=True)[:3]
    if lunges and sum(lunges) / len(lunges) > 6:
        style["dash"] += 0.18
        style["agg"] += 0.08
    throws = sum(1 for m in normal if m.get("throw"))
    if throws >= 2:
        style["range"] -= 0.10
        style["agg"] += 0.06
    if any(m.get("invuln") and not m.get("super") for m in normal):
        style["punish"] += 0.12
        style["guard"] += 0.04
    supers = [m for m in attacks if m.get("super")]
    style["super"] += min(0.2, 0.06 * max(0, len(supers) - 1))
    links = sum(len(v) for v in prof.get("chains", {}).values())
    style["combo"] += min(0.2, links / max(1, n) * 0.15)
    air = sum(1 for m in normal if m.get("air"))
    style["jump"] += (air / n - 0.2) * 0.6
    fast = [m["startup"] for m in normal if m.get("startup") is not None and not m.get("air")]
    if fast:
        avg = sum(fast) / len(fast)
        if avg < 6:
            style["agg"] += 0.08
        elif avg > 12:
            style["range"] += 0.08
            style["risk"] += 0.05
    if prof.get("life", 1000) >= 1500 or prof.get("defence", 100) >= 130:
        style["guard"] -= 0.06  # tanky: trades hits instead of blocking
        style["risk"] -= 0.05

    traits = []
    groups = " | ".join((prof.get("lore") or {}).get("groups", [])).lower()
    for subs, flag, shift in TRAITS:
        if any(sub in groups for sub in subs):
            traits.append(flag)
            for a, v in shift.items():
                style[a] += v
    for a, v in _offset(prof.get("def", "")).items():
        style[a] += v
    style = {a: round(_clamp(v), 3) for a, v in style.items() if abs(v) >= 0.005}

    # signature roles: the character's own moves for the moments that define it
    def pick(cands, key):
        cands = [m for m in cands if m.get("in") or m.get("pure")]
        return max(cands, key=key) if cands else None

    pmax = prof.get("powermax", 3000)
    ground = [m for m in normal if not m.get("air")]
    roles = {
        "finisher": pick([m for m in supers if m.get("power", 0) <= pmax], lambda m: (m.get("dmg", 0), m.get("knock"))),
        "comeback": pick([m for m in supers if m.get("invuln") and m.get("power", 0) <= pmax], lambda m: m.get("dmg", 0)),
        "reversal": pick([m for m in ground if m.get("invuln") and (m.get("startup") or 99) <= 9],
                         lambda m: (-(m.get("startup") or 99), m.get("dmg", 0))),
        "antiair": pick([m for m in ground if "A" in (m.get("ai_p2s") or []) or (m.get("ytop", 0) < -70 and not m.get("proj"))],
                        lambda m: (-(m.get("startup") or 99), m.get("dmg", 0))),
        "zoner": pick([m for m in normal if m.get("proj") or (m.get("helper") and not m.get("lunge"))], lambda m: (m.get("dmg", 0), -(m.get("startup") or 99))),
        "opener": pick([m for m in ground if m.get("startup") is not None and (m.get("lunge", 0) > 3 or m.get("reach", 0) > 60)],
                       lambda m: (-(m["startup"]), m.get("reach", 0))),
        "launcher": pick([m for m in ground if m.get("launch")], lambda m: (-(m.get("startup") or 99), m.get("dmg", 0))),
        "ender": pick([m for m in normal if m.get("from") and m.get("knock")], lambda m: m.get("dmg", 0)),
        "taunt": next((m for m in moves if m.get("st") == 195 or "taunt" in (m.get("name") or "").lower()
                       or "provo" in (m.get("name") or "").lower()), None),
    }
    sig = {r: (m.get("name") or "") for r, m in roles.items() if m}
    roles = {r: m["st"] for r, m in roles.items() if m}

    # preferred engage distance: the author's own range windows when present, else by style
    wins = sorted(m["ai_x"][1] for m in attacks if m.get("ai_x"))
    hold = wins[len(wins) // 2] if wins else 0
    if proj_share >= 0.3 or prof.get("persona") == "zoner":
        hold = max(hold, 260)
    hold = int(max(100, min(hold or 130, 320)))
    return {"style": style, "traits": traits, "roles": roles, "sig": {k: v for k, v in sig.items() if v}, "hold": hold}


if __name__ == "__main__":  # self-check on a synthetic profile
    p = {"def": "chars/x/x.def", "persona": "zoner", "life": 1000, "powermax": 3000,
         "lore": {"groups": ["saiyans"]}, "chains": {"200": [{}, {}]},
         "moves": [{"st": 1000, "in": [[1, 1]], "hits": 1, "proj": True, "dmg": 60, "startup": 9, "name": "Beam"},
                   {"st": 1100, "in": [[1, 1]], "hits": 1, "invuln": True, "dmg": 90, "startup": 4},
                   {"st": 3000, "in": [[1, 1]], "hits": 3, "super": True, "power": 1000, "dmg": 250, "name": "Big Beam"},
                   {"st": 195, "in": [[1, 1]], "hits": 0}]}
    out = build(p)
    assert out["roles"] == {"finisher": 3000, "reversal": 1100, "zoner": 1000, "taunt": 195}, out["roles"]
    assert "proud" in out["traits"] and out["hold"] == 260 and out["sig"]["finisher"] == "Big Beam"
    assert all(abs(v) <= LIMIT for v in out["style"].values())
    print("identity self-check ok:", out)
