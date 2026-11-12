# Experiments plan

Exact commands for every experiment. One-page conclusions: [experiments/](experiments/README.md).
Interpretation: [research_analysis.md](research_analysis.md).

## Recommended order

1. **EXP-D000 (development log, done):** the system works end to end in land, siege and naval battles.
2. **EXP-P001 / EXP-P002 (opponent probes):** the game's AI fights the human's side in field battles (done) and
   sieges (open).
3. **EXP-S001 (scenarios):** fix the battles every condition is run on.
4. **EXP-PILOT:** 3 runs per condition on SC1, to check the pipeline and size the main runs.
5. **EXP-M-*** (main runs): interleaved conditions on one frozen commit.

Each step changes one axis (opponent, scenario, condition) before the next.

## Before any research battle

```powershell
cd D:\Develop\shogun2-battle-ai
git status                     # clean working tree: meta records "-dirty" otherwise
.\.venv\Scripts\python tools\test_logic.py
.\.venv\Scripts\python tools\test_bridge.py
powershell -ExecutionPolicy Bypass -File .\tools\install.ps1
.\.venv\Scripts\python tools\check_installed.py
```

Game: speed 1x, same graphics settings, no other heavy programs. Never touch your own units.

## EXP-D000 - development log (done)

25 battles, Claude vs the author, 2026-11-09 to 2026-11-10, varying code versions. No commands to rerun:
recordings are `runtime/battle-2026110[9|10]-*.jsonl` up to 20261110-190434.

```powershell
.\.venv\Scripts\python -m s2ai.summary          # table of every recorded battle (runtime\battles.csv)
.\.venv\Scripts\python research\figures.py all 20261110-141245 --at 150
```

## EXP-P001 - game AI as the opponent, field battles (done)

```powershell
.\.venv\Scripts\python -m s2ai.server --opponent gameai --commander none --condition probe
```

Custom land battle, any armies; watch. Check `runtime\bridge.log` for `ai planner: alliance 1 handed to the
game's AI` and `ai planner attack: attack_unit ok`.

## EXP-P002 - game AI as the opponent, sieges (open)

Same command as P001. Custom siege battle, the human **attacks**; then one where the human defends.
Check `bridge.log` for `ai planner capture: ...` (which form of `capture_settlement` the engine took, or the
fallback) and `ai planner defend: defend_position ok`; check the attackers move to the walls:

```powershell
.\.venv\Scripts\python research\figures.py all <battle id> --at 60 120 200
```

## EXP-S001 - scenarios

For each scenario (SC1-SC4 in [methodology.md](methodology.md#4-scenarios)): set it up in the custom battle
menu, save the setup if the game allows it, screenshot every settings screen into `research/scenarios/<SCn>/`
(gitignored if it shows game art; describe it in text in the sheet), and write the exact settings into
[experiments/EXP-S001.md](experiments/EXP-S001.md). Run each once with B0 to check it plays out.

## EXP-PILOT

On SC1, interleaved, 3 runs each:

```powershell
.\.venv\Scripts\python -m s2ai.server --opponent gameai --commander none --condition PILOT-B0-SC1
.\.venv\Scripts\python -m s2ai.server --opponent gameai --condition PILOT-L1-SC1
```

Record each run in [runlog.md](runlog.md). Output: variance of the surviving strength difference per
condition -> runs needed per cell (written into the pilot sheet).

## EXP-M - main runs

One frozen commit (tag it: `git tag study-v1`). For every scenario, interleave:

```powershell
# B0  baseline: the game's built-in battle AI
.\.venv\Scripts\python -m s2ai.server --opponent gameai --commander none --condition B0-SC1
# L1  LLM general, full system
.\.venv\Scripts\python -m s2ai.server --opponent gameai --condition L1-SC1
# L2  reflexes off            (switch to build)
# L3  no game data in report  (switch to build)
# L4  model size
.\.venv\Scripts\python -m s2ai.server --opponent gameai --model haiku --condition L4h-SC1
.\.venv\Scripts\python -m s2ai.server --opponent gameai --model opus --condition L4o-SC1
```

After each battle: the summary is written at battle end or at the next battle's start; add a line to
[runlog.md](runlog.md).

## Analysis

```powershell
.\.venv\Scripts\python -m s2ai.summary                 # all battles -> runtime\battles.csv
.\.venv\Scripts\python research\analyze.py             # (to write) tables with intervals and tests per condition
```
