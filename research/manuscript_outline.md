# Manuscript outline (draft)

Target: *Discover Artificial Intelligence* (Springer), as the author's previous paper; alternatives to consider:
IEEE Conference on Games, AIIDE, IEEE Transactions on Games. Structure follows the previous paper (Kushnir 2026,
Discover AI 6:1042): explicit contributions, a contribution-type paragraph, a scope paragraph, a study-design
table, and full declarations.

**Working title:** Slow planning, fast reflexes: a general-purpose LLM as a real-time army commander in a
commercial strategy game

## Abstract (write last)

Problem - method (LLM over a deterministic executor, through the game's own script API) - design (fixed
opponent, conditions, scenarios, runs) - headline numbers (win rate vs the game's AI with intervals, ablation
effect, latency, cost) - release - non-claims.

## 1 Introduction

- Real-time tactics needs decisions faster than an LLM answers; commercial games are closed.
- Question: can a general-purpose LLM command through the scripting interface a game already ships?
- **1.1 Contributions** (see [research_context.md](research_context.md#contribution-type-draft)).
- **Contribution type.** No new model, no training; a method, a controlled comparison and released artifacts.
- **Scope.** One game, one model family, battle layer, against the game's AI; what we do not claim.

## 2 Related work

2.1 RTS game AI - 2.2 LLM agents in games - 2.3 hierarchical / two-rate LLM control - 2.4 scripting and modding
interfaces as research platforms - 2.5 gap addressed - 2.6 prior work by the author.
Sources: [related_work.md](related_work.md).

## 3 System

3.1 Access: the battle-script API and the in-memory engine patch - 3.2 observation and the battle report -
3.3 the plan schema - 3.4 the executor and its reflexes - 3.5 planning rate, conversations, safety (hand-back).
**Figure 1:** architecture diagram (own drawing). **Figure 2:** planning loop (sequence diagram).
**Table 1:** what the API offers and lacks (terrain, morale, fatigue; ship state).

## 4 Methods

4.1 Opponent (EXP-P001, P002) - 4.2 conditions - 4.3 scenarios (**Table 2**, EXP-S001) - 4.4 measures -
4.5 statistics - 4.6 study design overview (**Table 3**: experiment groups and their role, as in the previous
paper) - 4.7 controls and exclusions.
Source: [methodology.md](methodology.md).

## 5 Results

- 5.1 Feasibility (RQ1): latency, plans, cost, errors. **Figure 3:** plan latency distribution.
- 5.2 Against the game's AI (RQ2): **Table 4** B0 vs L1 per scenario; **Figure 4** win rates with intervals.
- 5.3 Ablation (RQ3): **Table 5** L1 vs L2, L3.
- 5.4 Model size and cost (RQ4): **Figure 5** strength vs cost.
- 5.5 How it fails (RQ5): case studies with battle snapshots (**Figure 6**, from `figures.py`), from the
  development phase (C1-C7 in [research_analysis.md](research_analysis.md)) and the study.
- 5.6 Release: code, recordings, analysis.

## 6 Discussion

Why two rates; what latency costs (targets gone, C6); what the missing API information costs; transfer to other
Total War games (the game-specific parts sit behind interfaces); limitations (threats to validity).

## 7 Conclusion and future work

Simulator for latency sweeps; other games; other models.

## Declarations

- Author contributions - Funding (none) - Data availability (summaries, recordings) - Code availability
  (repository, tag `study-v1`) - Ethics (no human participants; the author only starts battles) - Competing
  interests - **Use of generative AI:** the system uses Claude as the studied component; state also any AI
  assistance in writing code and text, as the journal's policy requires.
- Trademarks: *Total War* and *SHOGUN 2* are trademarks of SEGA; no game files are distributed.
