# Research analysis (living document)

Findings so far, with the battle that showed each one, and the caveats to carry into the paper. Newest phase
last. Numbers come from `runtime/summary-<id>.json` and the recordings unless stated otherwise.

## How to read this document

- **Development phase** findings are case studies: the code, scenario, army and opponent varied from battle to
  battle. They explain how the system came to be as it is and how the general fails; they are not evidence of
  strength.
- **Study** findings (from EXP-PILOT on) come from fixed scenarios, a fixed opponent and one frozen commit.
- "Kill ratio" in the summary table is the player's casualties per AI casualty (above 1: the AI side traded
  better).

## Development-phase numbers (EXP-D000, descriptive only)

| | Value |
|---|---|
| Battles with the LLM general | 25 (Claude Sonnet, effort low, vs the author) |
| Outcome | 11 won, 2 won on points, 3 lost, 1 lost on points, 8 left unfinished |
| Plans per battle | median 14 |
| Plan latency (model time per plan, 423 plans) | median 4.1 s, 90th percentile 5.9 s |
| API-equivalent cost per battle | median $0.90, max $3.31 |

**Interpretation (draft):** a plan every 5-15 s at about 4 s of model time is fast enough for the battle layer
when a deterministic executor handles everything below that time scale; the cost is low enough for repeated
experiments on a subscription.

## Development-phase case studies

Each case: what happened, what it shows about LLM command, and the change it led to (commit).

### C1 - Frontal cavalry charge into a rifle line (battle 20261110-085424, lost)

Samurai army against Boshin marines and guns. The general sent its cavalry straight at the rifle line and fired
`rapid_advance` 400 m out, so the ability ran out before contact. Shows: without geometry in the report, the
model takes the shortest path. Change: enemy formation and flank routes in the report, waypoints (`via`) in the
plan, ability durations from the game's database (083d3fe, 2ee361b). The next battle on the same setup
(20261110-091516) used a flank route and went clearly better.

### C2 - Executor retargets cavalry into spears (battle 20261110-120309, won)

The general targeted guns with its cavalry; when a gun broke, the executor's nearest-enemy retarget sent the
cavalry into the Kyoto Police spears guarding it. Shows: the fast layer can undo a good plan. Change:
matchup-aware retargeting (distance weighted by the unit-type matchup from game data, 528024f).

### C3 - Cavalry caught by the guard, never pulls back (battle 20261110-141245, won)

Cavalry sent at a gun 60 m away was held by the Kyoto Police 16 m away for 44 s and lost about 35 men. The
charge-and-retreat reflex never fired, because it only measured the distance to the *target*. Change: melee means
contact with any enemy, judged by the most dangerous one in contact (9682de8). The battle was still won: the AI
side kept 40% of its men fighting, kill ratio 0.63, 17 plans, $2.13.

### C4 - Fleet battle won (battle 20261110-175931, naval)

First custom naval battle with ship controllers: 5 ships against 6; all 6 of the author's ships lost, 2 of the
AI's; 13 plans, $0.50. The ship API (`attack_ship`, `goto_location(v, "full sail")`) was confirmed in game. A
bug: an attack on a ship sunk meanwhile went to the land handler (fixed, f62d3e5).

### C5 - The ironclad nobody shot at (battle 20261110-190434, naval, lost)

The fleet loaded armour-piercing shells (`change_shot_type` confirmed in game), concentrated fire and sank five
corvettes one by one, but never engaged the enemy ironclad (hull 8000), which sank its own ironclad and then
the fleeing corvettes; the ironclad ended with 5% damage. Two findings: (1) ships vanish from the battle at a
reported hull damage of 10-50%, most around 20-25%, with most of the crew alive, so the model read sinking ships
as healthy; (2) attacks ordered on a ship that sank while the model was thinking made the group hold still,
twice. Changes: "SINKING RISK" at 15%, guidance on big ironclads, and retargeting instead of holding (247f04b).

### C6 - Recurring: orders for units that are already gone

Across battles the model regularly names a target that broke, sank or left while it was thinking (latency about
4 s, plus the time the report took to reach it). Validation turns these into problem messages; since 247f04b an
attack on a gone target is carried out against the best enemy nearby. Shows the cost of planning latency
directly; a candidate measure for the study (plan problems per plan).

### C7 - Sieges: what the API can and cannot do

Castle walls cannot be garrisoned with `defend_building`; units are put on them with `occupy_zone` at the wall
piece. Building handles come from a small engine pool and must never be kept; only the garrisonable list may be
read (reading the full building list broke the battle when it reached scenery). The general defended a castle
successfully (20261110-065551, won), manning the walls with missile units and holding spears in reserve.

## Opponent probes (EXP-P001, EXP-P002)

- **Field battles work** (EXP-P001): the human's side under the game's AI planner advanced and fought on its own
  in all three battles; in the one even battle (27 vs 28 units) it kept 63% of its men against the built-in
  AI's 38%.
- **Sieges did not** (first attempt, 20261110-220312): told to attack a defender, the attackers marched away
  from the castle. A siege-specific objective is built (ca4fd56) and waits for a test (EXP-P002).

## Open engineering issues

- **Campaign battles crash** (0xc0000374, heap corruption) a few seconds after the battle ends; custom and
  historical battles do not. Campaign battles are excluded from the study.
- **Battle end:** most recordings stop at the victory screen without a `battle_end` message, so the result is
  inferred from the last state. To fix before the pilot: a result must be recorded for every run.
- The API has no terrain, morale or fatigue getters; ships have no "moving" state.

## Caveats to carry into the paper

- Development battles are not evidence of strength (varying code, scenario, opponent; the opponent was the
  author).
- The study's opponent is the game's AI planner, not the built-in battle AI and not people.
- The model's latency is part of the system: results at game speed 1x only.
