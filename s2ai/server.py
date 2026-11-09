"""The AI server: follows the battle the game streams, decides for the AI side, writes orders back.

    .venv\\Scripts\\python -m s2ai.server                    # demo commander: every AI unit attacks the nearest enemy
    .venv\\Scripts\\python -m s2ai.server --ai-alliance 2    # say which alliance is the AI's (default: guessed)

Start it before (or during) a battle that has the bridge (for now: the menu historical battles). Ctrl+C stops it;
the bridge then hands every unit back to the game's AI within STALE_S (15 s).
"""
from __future__ import annotations

import argparse
import logging
import math
import time

from .link import GameLink

log = logging.getLogger("s2ai")

POLL_S = 0.25
HEARTBEAT_S = 3.0


def guess_ai_alliance(start: dict) -> int:
    """The player's army carries the player's difficulty as handicap (run 6: player -2, every AI army +2).
    So the AI side is the alliance whose armies have the highest handicap; ties -> alliance 2."""
    best: dict[int, float] = {}
    for a in start["armies"]:
        h = a.get("handicap")
        if h is not None:
            best[a["alliance"]] = max(best.get(a["alliance"], -99), h)
    if len(set(best.values())) > 1:
        return max(best, key=best.get)
    return 2


class DemoCommander:
    """Every AI unit attacks the nearest standing enemy unit; re-targets every RETARGET_S. Only in the conflict
    phase. Proves the loop end to end before the LLM general goes in."""

    RETARGET_S = 10.0

    def __init__(self, ai_alliance: int):
        self.ai = ai_alliance
        self.next_at = 0.0

    def decide(self, battle) -> tuple[list[int], list[dict]] | None:
        state = battle.state
        if state is None or state["t"] < self.next_at:
            return None
        # the conflict event can be missed (battles without deployment): act after 2 minutes regardless
        if battle.phase != "conflict" and state["t"] < 120:
            return None
        self.next_at = state["t"] + self.RETARGET_S
        static = battle.static
        ours, enemies = [], []
        for u in state["units"]:
            if u.get("x") is None or u.get("men", 0) <= 0 or u.get("gone"):
                continue
            side = static.get(u["id"], {}).get("alliance")
            if side == self.ai:
                ours.append(u)
            elif not u.get("rout") and not u.get("shat"):
                enemies.append(u)
        if not ours or not enemies:
            return None
        orders = []
        for u in ours:
            if u.get("rout"):
                continue
            target = min(enemies, key=lambda e: math.hypot(e["x"] - u["x"], e["y"] - u["y"]))
            orders.append({"cmd": "attack", "unit": u["id"], "target": target["id"], "run": True})
        return [u["id"] for u in ours], orders


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ai-alliance", type=int, help="alliance index (1-based) the server commands")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    link = GameLink()
    commander = None
    battle_id = None
    take: list[int] = []
    last_write = 0.0
    log.info("waiting for a battle (runtime: %s)", link.runtime)
    while True:
        for msg in link.poll():
            kind = msg.get("type")
            if kind == "battle_start":
                b = link.battle
                ai = args.ai_alliance or guess_ai_alliance(msg)
                commander, battle_id, take = DemoCommander(ai), b.id, []
                armies = ", ".join(f"a{a['alliance']}/{a['army']}: {a['units']} units hcp {a['handicap']}"
                                   for a in msg["armies"])
                log.info("battle %s: %d units [%s]; commanding alliance %d", b.id, len(msg["units"]), armies, ai)
            elif kind == "event":
                log.info("event %s (phase %s)", msg.get("name"), msg.get("phase"))
            elif kind == "applied" and msg.get("errors"):
                log.warning("orders %s: %d errors, first: %s", msg.get("seq"), len(msg["errors"]), msg["errors"][0])
            elif kind == "battle_end":
                log.info("battle %s ended", msg.get("battle"))
        b = link.battle
        if b is not None and commander is not None and b.id == battle_id and not b.ended:
            decision = commander.decide(b)
            now = time.monotonic()
            if decision is not None:
                take, orders = decision
                seq = link.send(take, orders)
                last_write = now
                log.info("t=%.0fs orders #%d: %d units, %d orders", b.state["t"], seq, len(take), len(orders))
            elif take and now - last_write > HEARTBEAT_S:
                link.heartbeat(take)
                last_write = now
        time.sleep(POLL_S)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
