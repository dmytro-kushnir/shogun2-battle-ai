# Results

Where every result lives and how it is produced. Recordings never go into git (they are the game's state as
recorded by our bridge, but they are large and tied to the author's machine); derived tables and figures are
committed only when frozen for the manuscript.

| Artifact | Where | Produced by |
|---|---|---|
| Battle stream (state every 0.5 s, events, applied orders) | `runtime/battle-<id>.jsonl` | the bridge, in game |
| Plans (report sent, raw and validated plan, latency, tokens, cost, problems) | `runtime/plans-<id>.jsonl` | the server |
| Failed plans | `runtime/failures-<id>.jsonl` | the server |
| Orders sent | `runtime/orders-<id>.jsonl` | the server |
| Run metadata (commander, opponent, condition, model settings, commit) | `runtime/meta-<id>.json` | the server, at battle start |
| Per-battle summary | `runtime/summary-<id>.json` | the server at battle end (or next battle start); `s2ai/summary.py` |
| All battles in one table | `runtime/battles.csv` | `python -m s2ai.summary` |
| Figures | `research/figures/*.svg, *.png` | `research/figures.py` |
| Condition tables with intervals and tests | `research/tables/` *(planned)* | `research/analyze.py` *(planned)* |
| Engine log of the bridge | `runtime/bridge.log` | the bridge |

## Frozen result sets

None yet. When the main runs finish: copy the summaries and meta files of the study battles into
`research/data/study-v1/` (summaries are numbers only: no game content), commit with the `study-v1` tag, and
render all tables and figures from that folder only.
