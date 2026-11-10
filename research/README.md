# Research

Working title: **Slow planning, fast reflexes: a general-purpose LLM as a real-time army commander in a
commercial strategy game.**

This folder holds the study design, the run protocol and (later) the analysis scripts and frozen result tables.
Battle recordings stay in `runtime/` (gitignored); only derived tables go here.

## Research questions

1. **Feasibility.** Can an off-the-shelf LLM, with no game-specific training, command an army in real time
   through a game's scripting interface at a playable latency and cost?
2. **Strength.** Against the same fixed opponent and the same scenarios, does it beat the game's own battle AI?
3. **Components (ablation).** What do the layers contribute: the reflex executor (lines, retargeting, cavalry
   charge-and-retreat, missile spacing), and the game knowledge in the report (unit stats and matchups)?
4. **Cost.** How do strength, plan latency and cost change with the model size (Haiku, Sonnet, Opus)?
5. **Failure analysis.** How does it lose? Case studies from the development battles (cavalry into spears,
   ships left to an ironclad, targets that sank while the model was thinking).

## Why the real game, and where a simulator fits

The contribution is commanding a *commercial, closed* real-time game through the scripting interface it already
ships, with real latency and an opponent AI we did not write. A simulator alone cannot show that: reviewers would
ask whether the results transfer, and a home-made simulator is easy to tune in our own favour.

A simulator is still useful as a **second tier**, not a replacement:

| | Shogun 2 (main evidence) | Open simulator (complement) |
|---|---|---|
| Validity | real engine, real AI opponent | only as good as its combat model |
| Reproducible by others | needs a copy of the game | fully, from the repository |
| Runs | ~15-20 per condition, real time | hundreds, faster than real time, seeded |
| Experiments only it allows | - | latency sweep (pause the clock while the model thinks vs. not), many seeds |

Plan: run the main study in the game first. Add the simulator only if time allows, for the latency question
and as a reproducible benchmark others can run. The bridge's mock battle (`tools/test_bridge.py`) is a
software test, not a combat model, and is not used for results.

## Design

**Fixed opponent.** The human player is not a controlled opponent. In research runs the human's side is handed to
the game's own AI planner (`alliance:create_ai_unit_planner()`, as the game's historical battles script their
armies): `--opponent gameai`. The human only starts the battle and watches.

**Conditions** (the AI side; the opponent is always the game's AI planner):

| id | `--commander` | what commands the AI side |
|---|---|---|
| `B0` | `none` | the game's own battle AI (baseline) |
| `L1` | `general` (Sonnet) | the LLM general with the full system |
| `L2` | `general`, reflexes off | ablation: plans carried out without the executor's reflexes (to build) |
| `L3` | `general`, no game data | ablation: no unit stats or matchups in the report (to build) |
| `L4` | `general --model haiku` / `opus` | model size |

**Scenarios.** 3-4 custom battles saved once and replayed with identical settings (map, armies, funds, weather,
time of day), e.g. a balanced Sengoku army (spears, bows, cavalry), a gunpowder (Boshin) army, a castle siege
and a naval battle. Both sides get the same army where the scenario allows (mirror match).

**Runs.** A pilot of 3-5 runs per condition, then about 15-20 per condition and scenario, chosen from the
pilot's variance. Order of conditions interleaved, not blocked, so drift (patches, time of day of the
subscription) hits every condition alike.

**Measures** (from `python -m s2ai.summary`, one row per battle):

- result: win / loss / draw (time limit)
- surviving strength: share of the starting men still fighting, each side
- exchange ratio: opponent casualties per own casualty
- time to decision
- plans, plan latency, tokens and API-equivalent cost
- plan problems (invalid ids, targets gone) and order errors

**Statistics.** Win rates with Wilson 95% intervals and Fisher's exact test between conditions; exchange ratio
and surviving strength with bootstrap 95% intervals and Mann-Whitney U; Holm correction across comparisons.

**Reproducibility.** One frozen commit for all runs (each battle's `meta-<id>.json` records the commit, model,
settings, opponent and condition). Recordings, plans and the analysis scripts are released; the game itself is
needed to rerun.

## Run protocol

1. Close other heavy programs; the same game settings (speed 1x: no fast-forward, the general plans in real time).
2. Start the server for the condition, e.g.

   ```powershell
   .\.venv\Scripts\python -m s2ai.server --opponent gameai --commander none --condition B0-sengoku
   .\.venv\Scripts\python -m s2ai.server --opponent gameai --condition L1-sengoku
   ```

3. Load the scenario, start the battle, **do not touch your units**, and let it run to the end.
4. After the battle, check `runtime\bridge.log` for errors and note anything unusual in `research/runlog.md`.

## Status

- [x] Bridge and server for land, siege and naval battles; per-battle recordings and summaries
- [x] `--opponent gameai`, `--commander none`, `--condition`; commit and settings stamped per battle
- [x] Probe (battle 214912, 13 vs 34 units, Boshin): the human's side, handed to the game's AI planner, advanced
      and fought on its own (`attack_unit ok`; planner methods: add_units attack_unit capture_settlement
      clear_objective defend_position remove_units). Land only; naval not probed.
- [ ] Reliable battle result (most streams end without `battle_end`)
- [ ] Ablation switches (reflexes off, no game data)
- [ ] Scenario files fixed and described
- [ ] Pilot runs, then the full set
- [ ] Analysis scripts and tables
