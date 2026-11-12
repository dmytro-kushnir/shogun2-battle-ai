# EXP-D000 - development log (Claude vs the author)

**Status:** done (2026-11-09 to 2026-11-10).

**Question:** does the system work end to end in real battles, and how does the general fail?

**What we did:** 25 battles with Claude Sonnet (Claude Code CLI, effort low) commanding the computer's side
against the author: custom field battles (Sengoku and Boshin), historical battles from the menu (Sekigahara and
others), sieges (defending and attacking), campaign battles, and two custom naval battles. The code changed
between almost every battle (fixes after each one), and the author played differently each time.

**Conclusion (recorded):**

- The system runs in every battle type the game offers: field, historical, siege (walls manned through
  `occupy_zone`), campaign (with a crash after the battle, see analysis), naval (ship controllers, shot types).
- Results against the author: 11 won, 2 won on points, 3 lost, 1 lost on points, 8 left unfinished.
- Per battle: median 14 plans, median API-equivalent cost $0.90 (max $3.31). Plan latency over 423 plans:
  median 4.1 s, 90th percentile 5.9 s.
- Failure case studies with fixes: see [research_analysis.md](../research_analysis.md#development-phase-case-studies).

**Caveats:** not a result about strength. Code, scenario, army and opponent behaviour all varied; the opponent
was the system's author. Only latency and cost are usable as (descriptive) feasibility numbers.

**Paper role:** system description; RQ5 case studies (with battle snapshots); RQ1 descriptive numbers, clearly
labelled as development-phase.

**Key artifacts:** `runtime/battle-*.jsonl`, `plans-*`, `summary-*` for battles 20261109-* to
20261110-190434; `runtime/battles.csv`; git history (each fix names the battle that motivated it).

**See also:** [experiments.md](../experiments.md#exp-d000---development-log-done).
