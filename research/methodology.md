# Methodology

How the system works as an experimental object, and how it is evaluated. Commands are in
[experiments.md](experiments.md); environment and installation in [reproduction.md](reproduction.md).

## 1. System under study

```
 game (SHOGUN 2)                                  server (Python)                          LLM
 ─────────────────                                ───────────────                          ───
 engine patch (native/) gives every battle a script
 bridge (lua/s2ai_bridge.lua), every 0.5 s ──state──>  report.py: text battle report ──>  Claude
                                                       plan.py: validate JSON plan  <──   plan (JSON)
                                            <─orders── executor.py: plan -> unit orders,
                                                       reflexes, every 0.5 s
```

- **Access to the game.** The engine only creates its battle-script Lua state when a battle names a script, so
  custom and campaign battles had none. `native/s2ai_native.c` changes two instructions in the running engine
  (found by byte signature, nothing written on mismatch) so a battle without a script loads ours. Files on disk
  are never modified. Details: [`docs/engine-notes.md`](../docs/engine-notes.md).
- **Observation.** Every 0.5 s the bridge writes each unit's position, facing, frontage, men, state (moving,
  running, routing, shattered, leaving), ammunition; ships' hull and sail damage; fortifications' owner, damage
  and occupancy. The API has no terrain, morale or fatigue getters.
- **Report.** `s2ai/report.py` turns the state into a compact text report: own and enemy units with distances and
  bearings, unit-type stats read from the game's database at run time (melee, defence, charge, armour, morale,
  speed, range; ships' shot types), matchups, enemy formation and flank routes, fortifications, learned terrain
  heights, an estimated morale risk, recent events, and the previous plan.
- **Plan.** Claude answers with JSON (schema in `s2ai/plan.py`): an intent, short reasoning, and groups of units,
  each with a task (`attack`, `move`, `hold`, `withdraw`, `garrison`, `climb`, `attack_building`, `occupy`), a
  target, building or position, optional waypoints (`via`), facing, run, reflex settings (`engage_within_m`,
  `keep_range_m`), a shot type for ships, and special abilities to use; plus when to plan next (4-30 s).
  Invalid parts are dropped with a problem message fed back in the next report.
- **Executor.** `s2ai/executor.py` carries the latest plan out every 0.5 s without calling the model: lines by
  frontage, waypoints, retargeting by distance and matchup when a target breaks, engaging what comes close,
  missile units keeping their distance, cavalry pulling back after a charge (sooner against spears) to charge
  again, abilities only when ready.
- **Planning rate.** A new plan when the previous one asked for it (4-30 s), at most every 15 s while the armies
  are over 300 m apart. One conversation spans up to 6 plans, then a fresh one starts with a full briefing.
- **Safety.** If the server goes quiet for 15 s, or three plans fail in a row, or the usage limit is reached,
  the army goes back to the game's own AI.

## 2. Opponent

The human player is not a controlled opponent. In every research run the human's side is handed to the game's
AI planner (`alliance:create_ai_unit_planner()`, the call the game's historical battles use to script armies):
`--opponent gameai`. In field battles it is told every 10 s to attack the commanded side's unit nearest to its
own centre; in sieges it is given one objective for the whole battle (take the castle, or hold a circle over all
its walls). The engine's AI does the rest: formation, approach, missiles, melee. The human starts the battle
and does not touch the units.

Note: the planner is not identical to the game's built-in battle AI that commands the computer's side in a normal
battle (`--commander none`). Both are the game's own code; the study compares conditions against the *same*
planner opponent, so this difference does not bias the comparison, but it must be stated.

## 3. Conditions

| ID | Server flags | Commanded side |
|---|---|---|
| **B0** | `--commander none` | the game's built-in battle AI (baseline) |
| **L1** | `--commander general` | LLM general, full system (Sonnet, effort low) |
| **L2** | `--commander general` + reflexes off | ablation: plans carried out literally, no executor reflexes *(switch to build)* |
| **L3** | `--commander general` + no game data | ablation: report without unit stats and matchups *(switch to build)* |
| **L4h / L4o** | `--model haiku` / `--model opus` | model size |

All conditions add `--opponent gameai --condition <ID>-<scenario>`.

## 4. Scenarios

3-4 custom battles, each saved once and replayed with identical settings: map, armies, funds, weather, season,
time of day, battle time limit, unit size. Mirror armies where the scenario allows, so the side is the only
difference. Sides are not swapped between runs unless a scenario is asymmetric by design (siege); then both
directions are separate scenarios. Planned:

| ID | Era | Type | Why |
|---|---|---|---|
| SC1 | Sengoku | field, mirror (spears, bows, swords, cavalry) | classic rock-paper-scissors matchups |
| SC2 | Boshin (Fall of the Samurai) | field, mirror (line infantry, guns, cavalry) | ranged firepower, artillery |
| SC3 | Sengoku | siege, LLM defends | fortifications, walls |
| SC4 | Boshin | naval, mirror *(optional: the opponent planner is not probed at sea)* | ships, shot types |

Exact settings go into [experiments/EXP-S001.md](experiments/EXP-S001.md) once fixed.

## 5. Measures

From `python -m s2ai.summary` (one row per battle; `runtime/summary-<id>.json`):

| Measure | Definition |
|---|---|
| **Result** | win / loss / draw (time limit reached) for the commanded side |
| **Surviving strength** | share of each side's starting men still fighting (not routing, not gone) at the end |
| **Exchange ratio** | opponent casualties per own casualty |
| **Time to decision** | battle time until one side has no fighting units, or the time limit |
| **Plans** | number of plans; plan latency (model time per plan); plans that failed |
| **Cost** | input and output tokens; API-equivalent USD |
| **Plan problems** | invalid unit ids, targets gone, invalid buildings (from plan validation) |
| **Order errors** | orders the engine refused (from the bridge's `applied` messages) |

Primary outcome for RQ2-RQ4: **result** (win rate), with **surviving strength difference** (own minus opponent)
as the continuous secondary outcome. Everything else is descriptive.

## 6. Statistics

- Win rates with Wilson 95% intervals; between conditions Fisher's exact test.
- Surviving strength difference and exchange ratio: medians with bootstrap 95% intervals (10 000 resamples);
  Mann-Whitney U between conditions.
- Pre-specified comparisons only: B0-L1 (RQ2), L1-L2 and L1-L3 (RQ3), L1-L4h and L1-L4o (RQ4), per scenario and
  pooled. Holm correction across them.
- Sample size from the pilot's variance; target 15-20 runs per condition and scenario. Reported as is if the
  budget (subscription usage limits) cuts a cell short.

## 7. Controls

- **Code version:** one frozen commit for all main runs; `meta-<id>.json` records commit (with `-dirty` if the
  working tree differed), model, effort, planning settings, opponent and condition.
- **Game:** same build, same mod pack, same graphics settings, speed 1x (no fast-forward: the model plans in real
  time, and faster game speed shortens its effective thinking time).
- **Order:** conditions interleaved (e.g. B0, L1, L2, L3, L1, B0, ...), not blocked, so drift over days (game
  patches, service load, time of day) hits all conditions alike.
- **Human:** starts the battle, never commands; any accidental input is noted in the run log and the run dropped.
- **Exclusions:** runs where the bridge did not start, the server crashed, or the game crashed are excluded and
  counted; their recordings are kept.

## 8. Threats to validity

- **Internal.** LLM non-determinism (no seed control in the CLI): addressed by repetition, not by fixing a
  seed. The game's own randomness: likewise. Usage-limit hand-backs mid-battle: logged; such runs are flagged.
- **Construct.** Win rate against one game AI measures strength against that AI only. Surviving strength
  depends on the game's routing rules.
- **External.** One game, one LLM family; the API's missing terrain and morale information limits what any
  general can know. The opponent planner differs from the built-in battle AI.
- **Reproducibility.** Rerunning needs a copy of the game and a Claude subscription; recordings, plans and
  analysis are released so that the analysis is reproducible without either.
