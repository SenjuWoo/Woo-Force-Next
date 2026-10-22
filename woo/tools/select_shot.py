"""Screenshot the real select screen: the grid, plus the big portrait of chosen characters.

Runs Ikemen in a sandbox (own config: all portraits loaded before the menu, screenshots into the sandbox,
harness module select_shot.lua) with the live data/select.def and motif. Writes
woo/test/selshot/shots/*.png (first = grid overview, then one per --only character, in select.def order).

usage: python select_shot.py [--only "Name1,Name2"] [--timeout 600]
"""
import argparse
import re
import subprocess
import threading
import time
from pathlib import Path

import winpark

ROOT = Path(__file__).resolve().parents[2]
BOX = ROOT / "woo" / "test" / "selshot"
COLUMNS = 66  # data/mugen1/system.def [Select Info]

ap = argparse.ArgumentParser()
ap.add_argument("--only", default="")
ap.add_argument("--timeout", type=int, default=600)
ap.add_argument("--teammenu", action="store_true", help="leave the team menu on (shoots it instead of the grid)")
a = ap.parse_args()

# cell of every [Characters] line: one line = one cell, row-major
cells, idx, sec = [], 0, ""
want = [w.strip().lower() for w in a.only.split(",") if w.strip()]
for ln in (ROOT / "data" / "select.def").read_text(encoding="utf-8").splitlines():
    s = ln.split(";", 1)[0].strip()
    if s.startswith("["):
        sec = s.lower()
        continue
    if sec != "[characters]" or not s:
        continue
    entry = s.split(",")[0].strip()
    if any(w in entry.lower() for w in want):
        cells.append((idx % COLUMNS, idx // COLUMNS, entry))
    idx += 1

shots = BOX / "shots"
shots.mkdir(parents=True, exist_ok=True)
for p in shots.glob("*.png"):
    p.unlink()
rel = BOX.relative_to(ROOT).as_posix()
cfg = (ROOT / "save" / "config.ini").read_text(encoding="utf-8")
cfg = re.sub(r"(?m)^BootLoadingMode\s*=.*$", "BootLoadingMode = 0", cfg)
# tests are independent of the player's display settings (crop boxes assume 1920x1080)
cfg = re.sub(r"(?m)^GameWidth\s*=.*$", "GameWidth = 1920", cfg)
cfg = re.sub(r"(?m)^GameHeight\s*=.*$", "GameHeight = 1080", cfg)
cfg = re.sub(r"(?m)^Framerate\s*=.*$", "Framerate = 60", cfg)
cfg = re.sub(r"(?m)^Fullscreen\s*=.*$", "Fullscreen = 0", cfg)
cfg = re.sub(r"(?m)^ScreenshotFolder\s*=.*$", f"ScreenshotFolder = {rel}/shots/", cfg)
cfg = re.sub(r"(?m)^Modules\s*=.*$", "Modules = woo/tools/select_shot.lua", cfg)
(BOX / "config.ini").write_text(cfg, encoding="utf-8")

t0 = time.time()
args = [str(ROOT / "Ikemen_GO.exe"), "-config", f"{rel}/config.ini", "-windowed", "-nosound",
        "-woo.log", f"{rel}/brain.log", "-woo.cells", ";".join(f"{x},{y}" for x, y, _ in cells)]
if a.teammenu:
    args += ["-woo.teammenu", "1"]
# Ikemen shows a modal dialog after an engine error; kill it as soon as stderr reports one.
proc = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        env=winpark.no_focus_env())
winpark.park(proc)  # off the user's desktop; screenshots still work
err = []
watch = threading.Thread(target=lambda: err.extend(proc.stderr), daemon=True)
watch.start()
while proc.poll() is None:
    if any(b"Panic:" in ln or b"Engine error" in ln or ln.startswith(b"Error") for ln in err) or time.time() - t0 > a.timeout:
        proc.kill()
        print("ENGINE ERROR / TIMEOUT:", b"".join(x for x in err if b"WARNING" not in x)[:1500].decode("utf-8", "replace"))
        break
    time.sleep(0.5)
got = sorted(shots.glob("*.png"))
print(f"{len(got)} shots in {time.time() - t0:.0f}s -> {shots}")
for (x, y, e), p in zip(cells, got[1:]):
    print(f"  {p.name}: {e} (cell {x},{y})")
