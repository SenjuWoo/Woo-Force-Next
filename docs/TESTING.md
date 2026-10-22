# Testing (BETA)

> [!WARNING]
> BETA. Everything below is **automated, headless AI-vs-AI testing at 4x speed on the author's roster**. It measures whether the brain does what it
> was designed to do. It does not measure how the fights look or feel, and a human play-test of 0.9.0-beta.1 is still pending.

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

## What these numbers do not tell you

- Samples are small. The comeback rate is the same as before the comeback boost was reworked (about 19-21%); only match wins rose. Gap-1 matchups are close to a coin flip.
- 4x speed with several matches in parallel is not 1x speed on a quiet machine. The brain's timing spikes seen in batches (75-480 ms) come from CPU contention, not from the brain.
- A match with no `ERROR` line in the log is not a match that looked right. Visual problems are only caught by the palette and colour scans, and only on the stand sprites.
