"""Validate authoritative round events and the runner's JSON result for batch tools.

Only the Python standard library is needed; callers retain the full match log on failure.
"""
import json
import re

PREFIX = "MATCH_RESULT: "


def round_winners(text, players, last_round):
    """Reject missing or contradictory engine flags rather than compare raw fighter life."""
    rounds = {}
    for rn, pn, result in re.findall(r"round (\d+) end P(\d+) .*?\bresult=(win|loss|draw)\b", text):
        outcomes = rounds.setdefault(int(rn), {})
        pn = int(pn)
        if pn in outcomes and outcomes[pn] != result:
            raise ValueError(f"conflicting results for round {rn}, P{pn}")
        outcomes[pn] = result
    if last_round < 1 or set(rounds) != set(range(1, last_round + 1)):
        raise ValueError("missing completed round results")
    winners = []
    for rn in range(1, last_round + 1):
        outcomes = rounds[rn]
        if not set(players).issubset(outcomes):
            raise ValueError(f"incomplete player results in round {rn}")
        sides = [{outcomes[pn] for pn in players if (pn - 1) % 2 == side} for side in (0, 1)]
        if sides == [{"win"}, {"loss"}]:
            winners.append(1)
        elif sides == [{"loss"}, {"win"}]:
            winners.append(2)
        elif sides == [{"draw"}, {"draw"}]:
            winners.append(0)
        else:
            raise ValueError(f"incoherent side results in round {rn}: {sides}")
    return winners


def checked_result(child):
    """Require a successful, naturally completed sample before a reporter can score it."""
    stdout = child.stdout.decode("utf-8", "replace") if isinstance(child.stdout, bytes) else child.stdout or ""
    if child.returncode:
        stderr = child.stderr.decode("utf-8", "replace") if isinstance(child.stderr, bytes) else child.stderr or ""
        raise RuntimeError(f"match failed (exit {child.returncode}):\n{(stdout + stderr)[-3000:]}")
    lines = [ln[len(PREFIX):] for ln in stdout.splitlines() if ln.startswith(PREFIX)]
    try:
        result = json.loads(lines[0]) if len(lines) == 1 else None
        valid = (isinstance(result, dict) and result.get("bounded") is False
                 and isinstance(result.get("rounds"), list) and bool(result["rounds"])
                 and all(type(w) is int and w in (0, 1, 2) for w in result["rounds"]))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise RuntimeError(f"match has no complete result:\n{stdout[-3000:]}")
    return result
