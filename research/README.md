# Research

Working title: **Slow planning, fast reflexes: a general-purpose LLM as a real-time army commander in a
commercial strategy game.**

This folder is the research side of the project: why and how we evaluate the LLM general, what we ran, what we
found, and the manuscript draft. The code under `s2ai/`, `lua/` and `native/` is the system being studied.
Battle recordings stay in `runtime/` (gitignored, never committed); only derived tables and figures are added
here once frozen.

## Documents

| Document | What it holds |
|---|---|
| [research_context.md](research_context.md) | Goal, research questions, scope, why the real game, how the study progresses |
| [methodology.md](methodology.md) | The system as an experimental object, opponent, conditions, scenarios, measures, statistics, threats to validity |
| [experiments.md](experiments.md) | Experiment plan: IDs, recommended order, exact commands per experiment |
| [experiments/](experiments/README.md) | One short sheet per experiment: question, what we did, conclusion, paper role |
| [research_analysis.md](research_analysis.md) | Living analysis: findings so far, case studies, caveats to carry into the paper |
| [results.md](results.md) | Where every result lives and how tables and figures are produced |
| [reproduction.md](reproduction.md) | Environment, installation, running one condition, what to record |
| [runlog.md](runlog.md) | One line per research battle, filled in during runs |
| [related_work.md](related_work.md) | Candidate references to read and verify before citing |
| [manuscript_outline.md](manuscript_outline.md) | Paper skeleton, mapped to experiments, tables and figures |
| [figures.py](figures.py) | Paper figures from recordings (battle snapshots with plans, strength over time) |

## Status (2026-11-12)

- [x] System: land, siege and naval battles; per-battle recordings, plans and summaries
- [x] Fixed opponent: the human's side handed to the game's AI planner (`--opponent gameai`), field battles
      (EXP-P001)
- [x] Baseline condition (`--commander none`), condition labels, code version stamped per battle
- [x] Figures from recordings: battle snapshots, strength over time
- [ ] Opponent in sieges (`capture_settlement` / `defend_position`, EXP-P002: built, waiting for a game test)
- [ ] Reliable battle result at the end of every run (most recordings stop without `battle_end`)
- [ ] Ablation switches: reflexes off (L2), no game data (L3)
- [ ] Fixed scenarios (EXP-S001)
- [ ] Pilot (EXP-PILOT), then the main runs (EXP-M*)
- [ ] Analysis script: tables with intervals and tests; architecture and planning-loop diagrams
