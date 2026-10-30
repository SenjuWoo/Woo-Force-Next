# Known issues (source 0.9.0-beta.2)

This is a BETA. These are the problems the author knows about; there will be others.

## Scope of this beta
- **First-cut roster.** The download holds 996 anime characters (the original Jump Force-style set, [docs/ROSTER.tsv](ROSTER.tsv)). The custom additions (non-anime, original and adult characters) are left out for now.
- **Community characters vary.** They were made by many authors over many years: a few crash, look wrong, have odd hitboxes or are badly balanced. The author's overlay fixes ship with the game; report anything else with the character's name.
- **Size.** About 58 GiB extracted, split into ZIP parts because a release file cannot exceed 2 GiB.

## Not yet verified
- **No human play-test of this build.** All evidence is automated AI-vs-AI at 4x speed (see [TESTING.md](TESTING.md)).
- **One machine.** Windows 11, Ikemen GO v1.0.0. Tiers, personas and lore were written for this roster.
- **Install steps are untested on a clean machine** ([INSTALL.md](INSTALL.md)).

## Fights that stall or look wrong
- **Brain-independent stalls.** Some characters deadlock or become unkillable by their own design; the brain cannot fix them (checked with `-woo off`).
  Examples: *platinum sperm vs Titan Colossal* (both transform at the start and one fighter sits in an untouchable state at 100 life);
  *Hakari Kinji*'s Jackpot (full power and full regeneration) outlasts a round. The stall breaker ends such a round as a time over after 2 minutes.
- **One-frame super flash.** A super that starts too early is cancelled on its first tick, but the character's own super pause has already begun.
  About 0.4 cancels per fighter per round in 2v2. Only an engine change would remove the flash; holding the AI instead would replace the character's
  own AI with the brain for long stretches, which costs more identity than it saves.
- **Early learning.** Per-character power ceilings are learned at runtime (`woo/roster/gate_learned.tsv`), so a character's first matches show more cancelled supers.
- **Supers missing from a character's parsed move list** are held back only by the power clamp, not by the cancel.
- **No profile, no brain.** A character without a generated profile fights with its own AI, unchanged.

## Balance
- **Balance needs a fresh baseline.** Earlier beta.1 tier and comeback percentages used raw-health scoring and could miss failed samples. Beta.2 corrects those tools and the time-over loss bug; its population win/comeback rates have not yet been established.

## Tools
- In a freshly extracted game, `run_match.py` and `crash_scan.py` can panic with "Unable to add character" (a quirk of the engine's command-line quick match, not of normal play); see [TESTING.md](TESTING.md).
- The palette judge (`pal_judge.py`) reads only the stand sprites, so a palette that breaks only other sprites is not caught.
- Several tools assume the author's layout (`chars_fix` overlays, a generated `select.def`). Some characters in that roster only load thanks to local
  overlay fixes that are not part of this repository.
- Windows only. Other systems are untested.
