# Adding the brain to your own Ikemen GO install (BETA, manual)

> [!NOTE]
> **Just want to play?** You do not need this page: download the full game from the [releases](../../../releases) and follow the README. This page is for people who already have an Ikemen GO install with their own characters.

> [!WARNING]
> BETA. These steps are written from how the author's own install is built. **They have not been run on a clean machine or a different
> roster.** If a step fails, that is a bug in this document: please open an issue with your `woo/logs/brain.log`.

## What you need

These source instructions describe beta.2. The existing full-game and brain-kit release ZIPs remain beta.1 until a new release is published.

- Windows 10 or 11. Only Windows 11 has been tested.
- [Ikemen GO v1.0.0](https://github.com/ikemen-engine/Ikemen-GO/releases/tag/v1.0.0) (the `Ikemen_GO.exe` release). Other versions are untested.
- Your own characters and stages, listed in `data/select.def` the usual way. They are not part of this project.
- Python 3.12 for the analyzer and tools: `pip install -r requirements.txt` (Pillow, NumPy). The brain itself needs no Python.

## From the release zip

`Woo-Force-Next-Brain-<version>.zip` has the same layout as the install: extract it into your Ikemen GO folder and merge the folders, which does steps 2, 3 and 5 below
for you (it carries only the two tools step 6 needs). You still have to do steps 1, 4 and 6, and optionally 7.

## Steps

1. **Start Ikemen GO once** so it creates `save/config.ini`, then close it.
2. **Copy the brain.** `external/mods/woo_brain.lua` into your install's `external/mods/` (Ikemen loads every file in that folder).
3. **Copy the bridge.** `data/woo_brain.zss` into your install's `data/`.
4. **Register the bridge.** In `save/config.ini`, section `[Common]`, add the file to the end of `States`:

   ```ini
   States  = data/functions.zss, data/action.zss, data/demo.zss, data/dizzy.zss, data/guardbreak.zss, data/score.zss, data/system.zss, data/tag.zss, data/training.zss, data/woo_brain.zss
   ```

5. **Copy the knowledge files** `woo/profiles.ini`, `woo/lore.txt`, `woo/lore_extra.txt`, `woo/duos.txt` and `woo/series.txt` into a `woo/` folder in your install,
   and the `woo/tools/` folder next to them (from a clone; the release zip carries just `roster_analyzer.py` and `identity.py`).
   The lore files describe the author's roster; characters they do not mention still get a persona and tier from `profiles.ini`'s rules. Edit them freely.
   A character without a generated profile (step 6) is left alone: it fights with its own AI and the brain does nothing for it.
6. **Build the profiles** (one JSON per character, parsed from your roster's move lists and AI):

   ```text
   python woo/tools/roster_analyzer.py
   ```

   It reads `data/select.def` and writes `woo/roster/chars/*.json` and `woo/roster/summary.csv`. Re-run it after you edit the lore files or add characters. If the character profiles already exist and only the CSV is missing, use `python woo/tools/roster_analyzer.py --summary-only` to preserve those profiles.
7. **Optional: the palette fix.** Stock Ikemen GO v1.0.0 picks a random palette for an AI fighter whose `pal.defaults` is blank. `patches/start-lua-palette-default.patch`
   makes it use the first free slot instead. Apply it to `external/script/start.lua` with `patch -l -p0 < patches/start-lua-palette-default.patch`, or paste the 9 added lines by hand
   (in `start.f_selectPal`, in the "default palette for AI" branch, right after the `pal_defaults` loop). The brain does not need it.
8. **Play.** Start Ikemen GO, pick Watch mode (or any AI-controlled fight). For this source version, `woo/logs/brain.log` should start with `woo_brain 0.9.0-beta.2 (BETA) loaded`.

## Switching it off

- `Ikemen_GO.exe -woo off` runs a match without the brain, handy for comparing against the stock AI.
- In a match, `F8` turns the brain on and off, `F7` cycles its overlay between auto, on and off.
- To remove it, delete `external/mods/woo_brain.lua`, `data/woo_brain.zss` and its entry in `States`.

## If something does not work

- No `loaded` line in `brain.log`: the mod did not load. Check the file is in `external/mods/` and look at Ikemen's own log in `save/logs/`.
- An `ERROR` line in `brain.log`: the brain switched itself off for that match and handed every fighter back to its own AI. Please report it with the line.
- Fighters stand still or never fight: see [KNOWN-ISSUES.md](KNOWN-ISSUES.md), then try `-woo off` to find out whether the brain is involved.
