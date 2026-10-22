# Changelog

All notable changes to Woo Force Next. Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow [Semantic Versioning](https://semver.org/)
with `-beta.N` pre-releases. While the project is BETA (0.x), any release may change behaviour, balance and file formats.

## [0.9.0-beta.1] - 2026-10-22

First public BETA: the brain, the roster knowledge files and the tools, as a source repository. Not a finished product; see
[docs/KNOWN-ISSUES.md](docs/KNOWN-ISSUES.md).

### Added
- The brain (`external/mods/woo_brain.lua`) and its input/power bridge (`data/woo_brain.zss`) for Ikemen GO v1.0.0: six power tiers, eight
  personas, overmind / pilot / coach modes, lore-driven partners and rivals, familiarity and between-round learning, team relays and unison supers.
- Finisher discipline: supers wait for a hurt enemy; early starts are cancelled and per-character ceilings are learned at runtime.
- Comeback systems: second wind (scaled by tier gap and by how badly the round was lost) and fighting spirit.
- Watch-mode stall breaker (2 minutes, less than 10% life lost in the last 60 s).
- The version is logged at start (`woo_brain 0.9.0-beta.1 (BETA) loaded`); `gate OPEN` log lines name the reason a gate opened.
- Tools under `woo/tools/`: roster analyzer, match runner, finisher / stall / ladder / A-B test harnesses, health, crash and palette scanners,
  `pal_judge.py` (judges palette candidates from the decoded sprites).
- `patches/start-lua-palette-default.patch`: stock Ikemen GO v1.0.0 picks a random palette for an AI fighter whose `pal.defaults` is blank; this uses the first free slot.
- CI (Lua syntax, Python lint, repository checks, patch check), Dependabot, issue form.

### Development notes: bugs the test harnesses found and fixed before this release
- A cancelled super left the fighter frozen in idle (control was not given back); rounds stalled for the full time.
- Supers that start with almost no meter were freed too eagerly and then fired at full enemy life; the free rule now needs a cancel loop.
- A 3-bar super could start under a 1-bar clamp: the clamp now takes the lowest learned ceiling of every shut level.
- The gate leaked a full meter in round-2 intros: it is now shut during every intro.
- Fighters that kept a charge state forever starved the round: the clamp releases during ctrl-less charging; Zoro's power charge exits again.
- 2+ bar finishers retried by the character's AI every few ticks fired early once freed: the brain now pilots that fighter until the gate opens.
- A fighter guarded forever against an enemy that sat in one charge state for seconds (Shadow V2 vs Gohan_Beast stalled 5 of 12 rounds, now 0 of 13).
- A nil hit ledger at the start of round 2 switched the brain off for the match.
- The comeback boost did not make comebacks more likely; it now scales with how badly the lost round was lost.
- `roster_analyzer.py`: a regex that could backtrack exponentially on odd command names (CodeQL `py/redos`) is now a linear tokenizer with identical results.

### Known limits
- Tuned and tested only on the author's roster, Windows 11 and Ikemen GO v1.0.0, with automated AI-vs-AI runs.
- The human play-test of this build is pending.

## Before 0.9.0-beta.1

Internal development from 2026-10-08 (not released).
