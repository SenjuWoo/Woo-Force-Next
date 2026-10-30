"""Find every character that crashes Ikemen GO when its fight loads.

Ikemen compiles a character's CNS/ST/CMD/AIR when the match starts and aborts the whole game on the
first error MUGEN would have tolerated. This loads 8 characters per boot (4v4 Simul), ends the match
a few ticks into the fight (-woo.endafter), and on an engine panic names the culprit from the
error's file path (else bisects the batch). Uses Ikemen's own loader, so a pass here means the
character loads in the real game.

Startup failures that are not a character's fault (GL context/window creation fails while the
display sleeps or the session ends) are retried, never recorded.

Writes woo/roster/crash_scan.csv (def, status ok|crash|hang, error). Resumable.
usage: python crash_scan.py [--only SUBSTR] [--batch 8] [--defs-from FILE] [--retest]
"""
import argparse
import csv
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "woo" / "roster" / "crash_scan.csv"
LOG = ROOT / "woo" / "logs" / "crash.log"
SPARRING = "chars/Kiryu/Kiryu.def"  # known to load: opponent for a lone suspect
ENV_FAIL = re.compile(r"glActiveTexture|exit (3221226091|1073807364)\b", re.I)

ap = argparse.ArgumentParser()
ap.add_argument("--only", default="")
ap.add_argument("--batch", type=int, default=8)
ap.add_argument("--defs-from", default="", help="text file, one def per line (re-test after fixes)")
ap.add_argument("--retest", action="store_true", help="ignore earlier results for the selected defs")
a = ap.parse_args()


def select_defs():
    """Character defs exactly as the game's data/select.def lists them (overlays included)."""
    out, section = [], ""
    for ln in (ROOT / "data" / "select.def").read_text(encoding="utf-8").splitlines():
        s = ln.split(";", 1)[0].strip()
        if s.startswith("["):
            section = s.lower()
            continue
        if section != "[characters]" or not s:
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


def culprit_of(msg: str, batch):
    low = msg.lower().replace("\\", "/")
    for d in batch:  # the error names a file inside the culprit's folder (or its overlay def)
        if d.rsplit("/", 1)[0].lower() + "/" in low:
            return d
    for d in batch:  # overlay defs point back into chars/<folder>/
        if d.lower().startswith("chars_fix/") and \
                "chars/" + d.split("/", 1)[1].rsplit("/", 1)[0].lower() + "/" in low:
            return d
    return None


def run(batch):
    """-> ('ok', {def: note}) | ('crash'|'hang', (culprit_or_None, message)) | ('env', message)"""
    start = LOG.stat().st_size if LOG.exists() else 0
    fighters = batch + [SPARRING] if len(batch) == 1 else batch
    cmd = [sys.executable, str(ROOT / "woo" / "tools" / "run_match.py"), "--rounds", "1", "--speed", "10",
           "--time", "20", "--endafter", "20", "--timeout", "300", "--log", "woo/logs/crash.log"]
    for i, d in enumerate(fighters, 1):
        cmd += [f"--p{i}", d]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, errors="replace")
    text = ""
    if LOG.exists():
        with open(LOG, "rb") as f:
            f.seek(start)
            text = f.read().decode("utf-8", "replace")
    # pass = the fight actually started (loadcheck), or the round already ended (an OP character)
    if res.returncode == 0 and "ENGINE PANIC:" not in res.stdout and (
            all(f"loadcheck ok P{i} " in text for i in range(1, len(fighters) + 1)) or "round 1 end" in text):
        stalls = {}  # intros the brain's watchdog had to cut (they hang the round in MUGEN)
        for m in re.finditer(r"intro watchdog: P(\d) .*? stuck in state (-?\d+) anim (-?\d+)", text):
            k = int(m.group(1)) - 1
            if k < len(batch):
                stalls[batch[k]] = f"intro stall: state {m.group(2)} anim {m.group(3)}"
        return "ok", stalls
    m = re.search(r"^ENGINE PANIC: (.*)$", res.stdout, re.M)
    msg = re.sub(r"^external/script/main\.lua:\d+: ", "", m.group(1).strip()) if m else ""
    summary = " | ".join(ln for ln in res.stdout.splitlines()[:8] if not ln.startswith("Check "))[:300]
    if ENV_FAIL.search(res.stdout) or "Loading module" not in res.stdout:
        return "env", msg or summary
    if m:
        return "crash", (culprit_of(msg, batch), msg[:400])
    return ("hang" if "TIMEOUT" in res.stdout else "crash"), (None, summary)


defs = [ln.strip() for ln in open(a.defs_from, encoding="utf-8")] if a.defs_from else select_defs()
defs = [d for d in defs if d and a.only.lower() in d.lower()]
done = {}
if OUT.exists() and not a.retest:
    done = {r["def"]: r["status"] for r in csv.DictReader(open(OUT, encoding="utf-8"))}
queue = [d for d in defs if d not in done]
new_file = not OUT.exists()
out = open(OUT, "a", newline="", encoding="utf-8")
w = csv.writer(out)
if new_file:
    w.writerow(["def", "status", "error"])
print(f"{len(queue)} characters to check ({len(defs) - len(queue)} done before)", flush=True)

work = [queue[i:i + a.batch] for i in range(0, len(queue), a.batch)]
n_ok = n_bad = env_fails = 0
while work:
    batch = work.pop(0)
    t0 = time.time()
    status, info = run(batch)
    if status == "env":  # retry the same batch; stop if the machine cannot start Ikemen at all
        env_fails += 1
        print(f"env   {len(batch)} chars: {info[:140]} (retry {env_fails})", flush=True)
        if env_fails >= 6:
            print("Ikemen cannot start (GL/window). Stopping; rerun to resume.", flush=True)
            break
        work.insert(0, batch)
        time.sleep(10 * env_fails)
        continue
    env_fails = 0
    if status == "ok":
        for d in batch:
            w.writerow([d, "ok", info.get(d, "")])
        n_ok += len(batch)
    elif info[0]:
        culprit, msg = info
        w.writerow([culprit, status, msg])
        n_bad += 1
        print(f"  CRASH {culprit}: {msg[:160]}", flush=True)
        rest = [d for d in batch if d != culprit]
        if rest:
            work.insert(0, rest)
    elif len(batch) == 1:
        w.writerow([batch[0], status, info[1]])
        n_bad += 1
        print(f"  {status.upper()} {batch[0]}: {info[1][:160]}", flush=True)
    else:  # unattributed: bisect
        h = len(batch) // 2
        work[:0] = [batch[:h], batch[h:]]
    out.flush()
    print(f"{status:5} {len(batch)} chars ({time.time() - t0:.0f}s) ok={n_ok} bad={n_bad} left~{sum(map(len, work))}",
          flush=True)
out.close()
