# Research context

## Goal

Measure whether a **general-purpose large language model**, with no training on the game, can command an army
in a **commercial real-time tactics game** through the scripting interface the game already ships, and what it
takes to make that work: how often it must plan, what the fast layer under it must do, what it must be told,
and what it costs.

The study object is the system in this repository: Claude (through the Claude Code CLI) plans every few seconds
from a text battle report; a deterministic executor turns each plan into the game's own unit orders every 0.5 s
and handles what cannot wait for the model (lines, retargeting, cavalry charge-and-retreat, missile spacing,
abilities). The game is *Total War: SHOGUN 2* (Creative Assembly, 2011), Complete Edition.

The study aims to produce:

- reproducible results: a frozen code version, fixed scenarios, a fixed opponent, recorded battles;
- a fair comparison with the game's own battle AI under identical conditions;
- an honest account of failures, with the recordings to back each one.

## Research questions

| RQ | Question | Main evidence |
|---|---|---|
| RQ1 | **Feasibility.** Can an off-the-shelf LLM command in real time through the game's scripting interface at a playable latency and cost? | plan latency, plans per battle, tokens, cost, order errors (all conditions) |
| RQ2 | **Strength.** Against the same opponent and scenarios, does the LLM general do better than the game's own battle AI? | B0 vs L1 |
| RQ3 | **Components.** What do the reflex executor and the game knowledge in the report contribute? | L1 vs L2, L1 vs L3 |
| RQ4 | **Model size.** How do strength, latency and cost change from Haiku to Sonnet to Opus? | L1 vs L4h, L4o |
| RQ5 | **Failure modes.** How and why does it lose? | case studies from all phases, with recordings |

## Contribution type (draft)

We do not propose a new model or train one. The contribution is (1) a working method for putting an LLM in
command of a closed commercial real-time game through its existing scripting API, including an in-memory engine
patch that gives every battle a script; (2) a two-rate control design (slow LLM planning over a fast
deterministic executor) and its ablation; (3) a controlled comparison with the game's own AI on fixed
scenarios against a fixed opponent; and (4) the released system, recordings and analysis scripts.

## Scope and non-claims

- One game (SHOGUN 2), one LLM family (Claude), the battle layer only (no campaign map).
- The opponent is the game's AI, not human players; results do not claim strength against people.
- The game's API reports no terrain, morale or fatigue; the general works without them (terrain heights are
  learned from unit altitudes, morale is estimated). Results are conditional on that information.
- Campaign battles are excluded from the study: the game crashes after some of them end (see
  [research_analysis.md](research_analysis.md#open-engineering-issues)).

## Why the real game, and where a simulator fits

The claim is about a *commercial, closed* real-time game, with real latency and an opponent AI we did not write.
A simulator alone cannot support that: reviewers would ask whether results transfer, and a home-made combat model
is easy to tune, even unintentionally, in the method's favour.

| | SHOGUN 2 (main evidence) | Open simulator (optional complement) |
|---|---|---|
| Validity | real engine, real opponent AI | only as good as its combat model |
| Reproducible by others | needs a copy of the game | fully, from the repository |
| Runs | about 15-20 per condition, real time | hundreds, faster than real time, seeded |
| Experiments only it allows | - | latency sweep (pause the clock while the model thinks vs. not) |

The bridge's mock battle (`tools/test_bridge.py`) is a software test, not a combat model, and is never used for
results.

## How the study progresses (why we start where we start)

1. **Development phase (EXP-D000, done).** 25 battles of Claude against the author while the system was built.
   Each battle ran a different code version, scenario and army, so they are case studies, not results.
2. **Opponent probes (EXP-P001 done, EXP-P002 open).** Make the game's AI fight the human's side, so no
   condition depends on how a person plays.
3. **Scenarios (EXP-S001).** Fix 3-4 battles (mirror armies where possible) and describe them exactly.
4. **Pilot (EXP-PILOT).** A few runs per condition to check the pipeline end to end and to size the main runs
   from the observed variance.
5. **Main runs (EXP-M-B0, M-L1, M-L2, M-L3, M-L4).** One frozen commit, interleaved conditions.
6. **Analysis and manuscript.**

Each step changes one thing, so a surprise can be traced to its cause, the same rule the development followed
for engine-side changes ("change one thing per game test").

## Platform

- Windows 11, *Total War: SHOGUN 2 - Complete Edition* (Steam), optionally the community Remastered CE.
- Claude through the Claude Code CLI on a subscription (`sonnet`, effort `low`, unless a condition says
  otherwise). API-equivalent cost is computed from token counts.
- Python 3.14 server; the game's embedded Lua 5.1 runs the bridge; a small native module (Zig-built) patches the
  engine in memory.
