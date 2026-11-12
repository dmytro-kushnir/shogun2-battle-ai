# EXP-P002 - the game's AI as the opponent, sieges

**Status:** open (fix built, waiting for a game test).

**Question:** does the planner opponent attack (and defend) a castle sensibly?

**What we did so far:** siege 20261110-220312 with the P001 setup (attack the nearest commanded unit every
10 s): the attackers (26 units) marched about 350 m away from the castle (centre from (-101,-628) to
(200,-790), castle around (-46,-76)) and never reached the walls; presumably the planner searched for a way
around to a unit inside. Fix (commit ca4fd56): in a siege the planner gets one objective for the battle:
attacker -> `capture_settlement` (no game script calls it, so the bridge tries no arguments, then a position,
then falls back to `attack_unit`, and logs which), defender -> `defend_position` over a circle covering all
walls.

**Conclusion (recorded):** pending.

**Next:** one siege with the human attacking, one with the human defending; snapshots at 60, 120, 200 s.

**Paper role:** Methods, "Opponent"; decides whether SC3 (siege) is in the study.

**Key artifacts:** battle 20261110-220312; commit ca4fd56.
