# EXP-P001 - the game's AI as the opponent, field battles

**Status:** done (2026-11-10).

**Question:** can the human's side be handed to the game's own AI, so that no condition depends on how a person
plays?

**What we did:** the game's historical battles script armies with `alliance:create_ai_unit_planner()`
(`add_units`, `attack_unit`, `defend_position`). The bridge creates one planner for the human's alliance with all
its units; the server tells it every 10 s to attack the commanded side's unit nearest to its own centre
(`--opponent gameai`). The commanded side was left to the game's built-in battle AI (`--commander none`), so no
model was involved. Three custom field battles (Boshin armies), the author watching only.

**Conclusion (recorded):**

| Battle | Units (planner side vs built-in AI) | Planner side still fighting | Built-in AI side |
|---|---|---|---|
| 20261110-214912 | 13 vs 34 | 0% (lost, 360 s) | 74% |
| 20261110-215550 | 27 vs 28 | 63% | 38% (left at 666 s) |
| 20261110-220019 | 6 vs 31 | 0% (lost, 189 s) | 90% |

The engine accepted the planner every time (`ai planner attack: attack_unit ok`, no order errors). The planner
side advanced and fought on its own. Planner methods logged: `add_units attack_unit capture_settlement
clear_objective defend_position remove_units`. In the one even battle the planner side came out ahead, so the
opponent is not trivially weak.

**Caveats:** three battles, uneven armies in two of them; not a measure of the planner's strength. Land only.

**Paper role:** Methods, "Opponent".

**Key artifacts:** battles above; `runtime/bridge.log` lines from 21:50 to 22:03 on 2026-11-10; commit 9571487.
