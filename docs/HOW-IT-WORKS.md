# How it works

The brain is one Lua mod (`external/mods/woo_brain.lua`, hooked on Ikemen GO's per-frame `loop`) plus one ZSS file (`data/woo_brain.zss`)
that runs inside every fighter. Lua cannot press buttons or set a fighter's variables directly, so the two talk through character *maps*:
Lua writes `map(_woo_keys)`, `map(_woo_atk)` and friends, and the ZSS turns them into `assertInput`, `attackMulSet` and `ctrlSet`.

```text
roster_analyzer.py  --reads-->  your characters' move lists and AI   --writes-->  woo/roster/chars/<slug>.json
profiles.ini + lore.txt + duos.txt + series.txt  --merged by the analyzer into-->  tier, persona, partners, rivals

every fight tick:   observe -> power gate -> fighting spirit -> super watch -> decide (overmind or pilot) -> AI hold -> keys
```

## Modes

| Mode | Used when | What the brain does |
|---|---|---|
| **overmind** (default) | The character has any AI of its own | Keeps that AI fighting and layers decisions on top: guarding, spacing, defence against grabs, teamwork, identity moments. It holds the character's AI off for a few ticks when it wants to perform a move itself. (A/B tests showed hand-tuned MUGEN AIs beat a from-scratch pilot on their own moves.) |
| **pilot** | The character has no AI at all but has moves | Plays the character through a virtual controller by performing its real command inputs from the parsed move catalog, so the character's own State -1 rules decide what comes out. The brain also pilots a fighter temporarily to hold back a looping finisher (see below). |
| **coach** | Only when forced with `-woo.p<N> own` | Only sets the AI level by tier and applies power scaling (used for A/B tests). |
| *off* | The character has no generated profile, or `-woo off` | The brain does nothing for this fighter. |

## Tiers, personas, lore

- **Tier 1-6** (`profiles.ini` rules, `[override.*]` per character): reaction frames 16 down to 3, skill 0.35 up to 0.96, AI level 4 to 8,
  attack multiplier 0.80 up to 1.30, defence multiplier 0.85 up to 1.25.
- **Persona** (eight): numbers for aggression, guard chance, preferred range, jump, dash, projectile appetite, meter appetite, combo
  continuation, punishing, taunting and how much it helps teammates. `lore.txt` can override the persona per character.
- **Lore** (`lore.txt`, `duos.txt`): factions, canon partners and canon rivals. Partners relay each other's combos, rescue each other and
  open supers together; rivals hunt each other. Strangers start with little knowledge of each other's moves (*familiarity*) and scout; the
  clever ones adapt between rounds.

## Finisher discipline

A super of 1, 2, 3 or 4 bars may start once an enemy is under a share of its life: `(0.24 + 0.20 x the persona's super appetite, adjusted
by traits and matchup)` times 1.0 / 0.82 / 0.66 / 0.55. Also allowed: the fighter itself is in trouble, or a canon partner opens one.
The gate stays open 1.5 s after its reason ends.

How it is enforced:

1. While the gate is shut the fighter's power is **clamped** just under the cost, and what it really earned is banked and paid back later.
2. Some supers start anyway (their true entry threshold is lower than their cost). The **super watch** cancels them on the first tick, hands
   control back, and learns a lower ceiling for that character (`woo/roster/gate_learned.tsv`, written at runtime).
3. A 1-bar move that is cancelled three times in 3 s is a spammed kit move, not a super: it is freed for good.
4. A 2+ bar finisher the character's AI keeps retrying is not freed. From the second cancel the brain **pilots** that fighter until the gate opens.
5. The gate is forced shut during every intro, and the clamp is released while a ctrl-less state is charging power.

## Comebacks

Round results come from the engine's per-player `win()`, `lose()` and `drawGame()` flags once round state reaches 4, after hit resolution settles. Remaining health does not identify a winner across fighters with different maximum health. The result feeds both the adaptation rate and the count of lost rounds; draws do not increment that count.

- **Second wind:** after losing a round, the weaker fighter gets `min(0.50, (0.10 x min(tier gap, 3) + 0.25 x how badly it lost) x min(rounds lost, 2))`
  on top of its attack and defence.
- **Fighting spirit:** once per round, at 25% life, a swing of 4-16% to attack and defence, bigger for the underdog.

## Watch-mode stall breaker

Watch matches run with infinite round time. After 2 minutes, if less than 10% of all fighters' life was lost in the last 60 s (or after 6 minutes),
the round ends as a time over decided on life.

## The log

`woo/logs/brain.log` (change it with `-woo.log`): the version at start; per round, a `round N start` line per fighter (mode, tier, persona),
a `perf` line (brain cost) and the identity and adapt summaries; and per super `super ...`, `super VETO`, `super FREE`, `super HOLD`,
`gate OPEN ... why=...`, plus `STALL` and any `ERROR`. Test flags: `-woo.trace N`, `-woo.verbose`, `-woo.p<N> own|pilot`.
