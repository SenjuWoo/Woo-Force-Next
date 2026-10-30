# Testing (BETA)

> [!WARNING]
> BETA. Historical batch results below are from automated AI-vs-AI testing at 4x speed on the earlier development roster. Their balance percentages need a fresh baseline with beta.2's corrected outcome/failure checks. Human play-testing remains incomplete.

## Source regressions (beta.2)

From the project root, run `python -m unittest discover -s tests -v` for repository and Python tool/data regressions. CI also runs `lua5.1 tests/test_brain.lua`, which exercises the actual Lua summary, learning and second-wind functions with KO, time-over, draw and team-result fixtures.

If the game download has generated profiles but no batch CSV, run `python woo/tools/roster_analyzer.py --summary-only` first. It reads selected profiles and writes only the CSV.

A real-engine, two-round time-over regression is available on the full install:

```text
python woo/tools/run_match.py --p1 chars_fix/Naruto/Naruto.def --p2 chars_fix/Luffy/Luffy.def --rounds 2 --time 30 --speed 1 --timeout 120 --module tests/fixtures/timeout_result.lua --box timeout-result --log woo/logs/timeout-result.log
```

The isolated fixture forces the living underdog to lose on time, then asserts that its loss count is one and second wind is positive in the next round. It does not change character files or player configuration. Normal runs copy the player's learned-gate baseline into each sandbox, so parallel tests cannot change or share that data.

`run_match.py` returns nonzero on engine error, timeout, brain error or incomplete match. Successful runs emit one `MATCH_RESULT:` JSON line with completed round winners (`1`/`2`, or `0` for a draw); intentional `--endafter` loader checks are marked `bounded` and cannot count as balance samples. `--rounds N` is first to N victories for normal single/simul/tag matches, not necessarily N rounds. Batch reporters reject failed, bounded, empty and malformed samples.

For controlled KO or double-KO checks, replace the module above with `tests/fixtures/ko_result.lua` or `tests/fixtures/draw_result.lua`. Turns matches require one victory per opposing member; with legacy `TurnsLoading = 0`, reserves replace the active P1/P2 slot. `finisher_check.py` requires at least one qualifying damaging super as well as zero violations; no qualifying observations means an inconclusive, failing check.

## The harness

`woo/tools/run_match.py` boots Ikemen GO in a throw-away sandbox with a tiny test motif (only the fighters of that match, so a test takes
seconds instead of preloading the whole roster), runs a match with the brain, and writes its own log. The scripts below call it in parallel.
They expect the **full install layout** the author uses (engine, a roster, generated profiles), so they are developer tools, not something that runs from a bare clone.

| Script | What it checks | Pass condition |
|---|---|---|
| `finisher_check.py --n 14` (`--team 2 --n 8` for 2v2) | Every logged super of 1 bar or more starts only when the enemy is under its `need`% of life, or the fighter itself is under about 34%, or a canon partner opened it (a super started with the brain's own gate open after a heal swing is counted as "tail") | 0 violations |
| `stall_ab.py --n 20 --seed 11 --modes on` | Share of 1-round, infinite-time matches (like Watch mode) that hit the stall breaker | 10% or less; compare with `--modes off` to see whether the brain causes a stall |
| `ladder.py --matches 60 --jobs 5` | Plays matches across power tiers; reports how often the higher tier wins a round and how often an underdog that lost round 1 comes back | Win rate rises with the tier gap; comebacks exist |
| `ab_test.py` | Brain against each character's own AI in mirror matches, sides alternating | Reports the brain's win rate (not re-run for 0.9.0-beta.1) |
| `health_scan.py`, `crash_scan.py`, `color_scan.py`, `pal_scan.py`, `pal_judge.py` | Roster health: characters that crash at load, draw as silhouettes or have junk palette slots | Reports only |

Keep the brain files unchanged while a suite runs: the checks are only meaningful for one exact build. To debug a stalling pair, run it with
`run_match.py --trace 60 --box x --log woo/logs/dbg.log` and then again with `--woo off`.

## Results for 0.9.0-beta.1 (2026-10-22)

Run on the build immediately before the version string was added (the logic is identical). Roster of about 1,640 characters, Windows 11.

| Check | Result |
|---|---|
| `finisher_check` 1v1, 14 random matches | 110 supers, **0 violations**, 1 cancelled at entry |
| `finisher_check` 2v2, 8 random matches | 124 supers, **0 violations**, 2 tail, 39 cancelled at entry |
| `stall_ab`, 20 matches | 1 stalled; mean round 22 s of real time at 4x |
| Rounds that stalled inside the finisher suites | 3 of 49, all one pair: Hakari Kinji vs Mikasa (his Jackpot heals him fully) |
| `ladder`, 60 matches, 134 rounds | higher tier wins a round 36% / 85% / 79% / 100% at tier gap 1 / 2 / 3 / 4; mean Elo rises with tier |
| Comebacks | the underdog that lost round 1 took round 2 in 8 of 37 cases (about 21%; earlier runs 17-20%) and won the match 4 times |
| Brain cost | 0.41 ms per tick on average, 4.7 ms worst fight tick (2v2, 1x speed, one instance) |

## The game download (0.9.0-beta.1)

Checked on 2026-10-22 by extracting every part into an empty folder and using that copy, not the development tree.

| Check | Result |
|---|---|
| Parts | 23 ZIP parts, 42,573,053,411 bytes in total, the largest 2,056,493,669 bytes (GitHub's limit is 2,147,483,648); SHA-256 of every part re-verified against `SHA256SUMS.txt` |
| Extraction | 19,525 files, as many as the manifest, no duplicate names, every CRC verified by the extractor; 58 GiB on disk |
| Select screen | boots in 19 s with all 996 characters; **0** characters failed to be added (the engine announces each one that does); about 800 warnings come from the characters' own scripts, as in the development tree |
| Matches | 1v1 and 2v2 AI-vs-AI matches ran to the end from the extracted copy with the brain loaded (`woo_brain 0.9.0-beta.1 (BETA) loaded`), profiles found, supers gated, team relays and rescues happening, no errors |
| Characters | all 996 were `ok` in the development tree's crash scan; the shipped files are byte-identical copies |
| Not checked | a match started by hand from the menu; the extracted game on any other machine |

Known tooling quirk: `run_match.py` (and so `crash_scan.py`, which uses the same command-line path) intermittently panics with "Unable to add character" on the first runs in a freshly extracted copy, never in the development tree. It only affects the developer quick-match shortcut (`-p1 -p2 -loadmotif`), where the engine looks a character up right after adding it; the select-screen boot and ordinary matches are unaffected. The cause was narrowed down but not identified.

## What these numbers do not tell you

- Samples are small. The comeback rate is the same as before the comeback boost was reworked (about 19-21%); only match wins rose. Gap-1 matchups are close to a coin flip.
- 4x speed with several matches in parallel is not 1x speed on a quiet machine. The brain's timing spikes seen in batches (75-480 ms) come from CPU contention, not from the brain.
- A match with no `ERROR` line in the log is not a match that looked right. Visual problems are only caught by the palette and colour scans, and only on the stand sprites.
