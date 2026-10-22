<h1 align="center">Woo Force Next</h1>

<p align="center"><strong>An AI brain for Ikemen GO that makes AI-vs-AI fights worth watching.</strong></p>

<p align="center">
  <img src="https://img.shields.io/badge/status-BETA-e23d2b?labelColor=140b07" alt="Status: BETA">
  <img src="https://img.shields.io/badge/version-0.9.0--beta.1-ff7a18?labelColor=140b07" alt="Version 0.9.0-beta.1">
  <img src="https://img.shields.io/badge/engine-Ikemen%20GO%20v1.0.0-53d7ff?labelColor=140b07" alt="Ikemen GO v1.0.0">
  <img src="https://img.shields.io/badge/platform-Windows-8f9aa6?labelColor=140b07" alt="Windows">
  <img src="https://img.shields.io/badge/characters-not%20included-8f9aa6?labelColor=140b07" alt="Characters not included">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-3fb950?labelColor=140b07" alt="MIT license"></a>
  <a href="https://github.com/SenjuWoo/Woo-Force-Next/actions/workflows/ci.yml"><img src="https://github.com/SenjuWoo/Woo-Force-Next/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

<p align="center">
  <a href="docs/INSTALL.md">Install</a> ·
  <a href="docs/HOW-IT-WORKS.md">How it works</a> ·
  <a href="docs/TESTING.md">Testing</a> ·
  <a href="docs/KNOWN-ISSUES.md">Known issues</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

> [!WARNING]
> **This is BETA software (0.9.0-beta.1).** It is unfinished, it has bugs, and it has only ever run on one machine with one roster.
> Fights, balance and file formats will change between betas without notice. Setup is manual. Please read
> [what BETA means here](#beta-what-that-means-here) before you try it, and report what you see.

## What it is

Woo Force Next began as an Ikemen GO remake of a very large MUGEN roster (Woo Force, about 1,640 characters). This repository is the
original work from that project: the **AI brain** (Lua + ZSS mod), the **roster knowledge** it reads (power tiers, personas, lore, canon
partners and rivals) and the **tools** used to build, check and test it. It is made for Watch mode and AI-vs-AI matches.

The goals, in the author's words: every character fights as itself, strong ones win but weak ones can come back, fighters from the same
universe know each other, canon partners team up, and nothing looks broken.

## BETA: what that means here

- **Unfinished.** Behaviour, balance and data formats change between betas. A 0.9.x release can break your settings or generated data.
- **One machine, one roster.** Built and tested on Windows 11 with Ikemen GO v1.0.0 and the author's own roster. Other rosters, other
  engine versions and other systems are untested.
- **Automated tests only so far.** The numbers below come from headless AI-vs-AI matches at 4x speed. A human play-test of this exact build
  in Watch mode is still pending.
- **Known problems exist.** See [docs/KNOWN-ISSUES.md](docs/KNOWN-ISSUES.md). Some characters stall, deadlock or look odd; a few are
  character bugs the brain cannot fix.
- **Manual setup.** There is no installer. [docs/INSTALL.md](docs/INSTALL.md) lists every step, and none of it is verified outside the author's install.

## What is in this repository (and what is not)

| In this repo (MIT) | Not in this repo |
|---|---|
| `external/mods/woo_brain.lua`, the brain | **Ikemen GO** itself: download v1.0.0 from the [official release](https://github.com/ikemen-engine/Ikemen-GO/releases/tag/v1.0.0) (MIT) |
| `data/woo_brain.zss`, the input and power-scaling bridge | **Characters, stages, sounds, screenpacks**: they belong to their authors, bring your own |
| `woo/profiles.ini`, `lore.txt`, `lore_extra.txt`, `duos.txt`, `series.txt`: tiers, personas, lore, partners | The generated per-character profiles (`woo/roster/chars/*.json`): built from your roster by the analyzer |
| `woo/tools/`: analyzer, test harness, health and palette tools | Screenshots: they would show third-party character art |
| `patches/`: one small fix for stock Ikemen GO v1.0.0 | |

## Features

- **Six power tiers** set how fast a fighter reacts, how well it decides, its AI level and its attack and defence multipliers.
- **Eight personas** (effortless, tactician, zoner, rushdown, warrior, berserker, trickster, brawler) decide spacing, aggression, guarding,
  jumping, projectile and meter habits.
- **Three modes per fighter.** *Overmind* (the default) keeps the character's own AI and adds a decision layer on top; *pilot* drives
  characters that have no AI at all through a virtual controller; *coach* only sets the AI level and power scaling (forced, for A/B tests).
- **Finisher discipline.** Supers wait until the enemy is hurt, the bigger the move the longer it waits. A super that starts too early is
  cancelled, and the brain learns per-character ceilings.
- **Comebacks.** A *second wind* buffs the underdog after a lost round; *fighting spirit* gives a one-time swing at 25% life.
- **Familiarity and learning.** Strangers scout each other's moves; fighters adapt between rounds.
- **Team play.** Canon partners relay combos, rescue each other and open supers together.
- **Watch-mode stall breaker.** A round that stops making progress ends as a time over after 2 minutes.
- **Live controls.** `F7` cycles the brain overlay, `F8` switches the brain on and off, `-woo off` starts a match without it.

Details: [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).

## How well does it work?

Automated results on the author's roster, headless AI-vs-AI at 4x speed, 2026-10-22, brain 0.9.0-beta.1. These measure the brain against
its own goals; they are not a claim about how it feels to watch.

| Check | Result |
|---|---|
| Supers starting before the enemy is hurt, 1v1 | **0 of 110** |
| Supers starting before the enemy is hurt, 2v2 | **0 of 124** |
| 1-round matches that stalled (20 matches) | **1 of 20** (a character deadlock that also happens with the brain off) |
| Higher tier wins a round, tier gap 1 / 2 / 3 / 4 | 36% / 85% / 79% / 100% |
| Underdog that lost round 1 takes round 2 | about 19-21%, and wins the match about 4 times in 37 |
| Brain cost | 0.41 ms per tick on average, 4.7 ms worst fight tick (2v2, 1x speed) |

How these were measured, and what they do not cover: [docs/TESTING.md](docs/TESTING.md).

## Install

Manual, Windows, and untested outside the author's setup. Short version: install Ikemen GO v1.0.0, copy `woo_brain.lua`, `woo_brain.zss` and
the `woo/` data files in, add the ZSS file to `States` in `save/config.ini`, run `woo/tools/roster_analyzer.py` on your roster to build the
profiles, then start a Watch mode match. Full steps: [docs/INSTALL.md](docs/INSTALL.md).

The brain writes `woo/logs/brain.log` (version at start, one summary per round, every cancelled or held super, and any error).

## Reporting problems

Use the [bug report form](../../issues/new/choose) and attach the relevant part of `woo/logs/brain.log`. Security reports: see [SECURITY.md](SECURITY.md).

## Credits and licensing

- **Ikemen GO** is MIT-licensed, by the [Ikemen GO contributors](https://github.com/ikemen-engine/Ikemen-GO). This project is a mod for it and is not affiliated with it.
- **Characters, stages, sounds and screenpacks** used by the author's roster belong to their creators and are not distributed here.
  The characters portrayed belong to their respective rights holders; this project is not affiliated with or endorsed by any of them.
- The original code and data files in this repository are under the [MIT license](LICENSE). `patches/` modifies an MIT-licensed Ikemen GO file and is offered under the same terms.
- By SenjuWoo.
