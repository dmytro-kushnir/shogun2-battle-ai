"""The AI server: follows the battle the game streams, decides for the AI side, writes orders back.

    .venv\\Scripts\\python -m s2ai.server                      # the LLM general (Claude via your Claude Code login)
    .venv\\Scripts\\python -m s2ai.server --model claude-opus-5-5 --effort medium
    .venv\\Scripts\\python -m s2ai.server --commander demo     # no model: every AI unit attacks the nearest enemy
    .venv\\Scripts\\python -m s2ai.server --ai-alliance 1      # which alliance is the AI's (default: guessed)

Live map: http://127.0.0.1:8765 . Plans are logged to runtime/plans-<battle>.jsonl.
Ctrl+C stops it; the bridge hands every unit back to the game's AI within 15 s.
"""
from __future__ import annotations

import argparse
import json
import logging

import queue
import threading
import time
import webbrowser

from . import report
from .executor import Executor
from .link import GameLink
from .plan import validate
from .viewer import Board, serve

log = logging.getLogger("s2ai")

POLL_S = 0.25
HEARTBEAT_S = 3.0
START_PLANNING_AFTER_S = 120  # if the conflict-phase event never arrives


def guess_ai_alliance(start: dict) -> int:
    """The player's army carries the player's difficulty as handicap (Sekigahara: player -2, every AI army +2).
    So the AI side is the alliance whose armies have the highest handicap; ties -> alliance 2."""
    best: dict[int, float] = {}
    for a in start["armies"]:
        h = a.get("handicap")
        if h is not None:
            best[a["alliance"]] = max(best.get(a["alliance"], -99), h)
    if len(set(best.values())) > 1:
        return max(best, key=best.get)
    return 2


def conflict(battle) -> bool:
    return battle.phase == "conflict" or (battle.state is not None and battle.state["t"] >= START_PLANNING_AFTER_S)


class DemoCommander:
    """Every AI unit attacks the nearest standing enemy unit; re-targets every RETARGET_S."""

    RETARGET_S = 10.0

    def __init__(self, ai_alliance: int):
        self.ai = ai_alliance
        self.next_at = 0.0

    def decide(self, battle) -> tuple[list[int], list[dict]] | None:
        state = battle.state
        if state is None or state["t"] < self.next_at or not conflict(battle):
            return None
        self.next_at = state["t"] + self.RETARGET_S
        units = report.merge(battle.static, state)
        ours = [u for u in units if u.alliance == self.ai and u.fighting_fit]
        enemies = [u for u in units if u.alliance != self.ai and u.fighting_fit]
        if not ours or not enemies:
            return None
        orders = [{"cmd": "attack", "unit": u.id, "target": min(enemies, key=lambda e: report.dist(u, e)).id,
                   "run": True} for u in ours]
        return [u.id for u in ours], orders


class GeneralCommander:
    """The LLM plans in a background thread; the executor carries the latest plan out every tick."""

    def __init__(self, ai: int, general, battle_id: str, board: Board, link: GameLink):
        self.ai, self.general, self.battle_id, self.board, self.link = ai, general, battle_id, board, link
        self.executor = Executor(ai)
        self.results: queue.Queue = queue.Queue()
        self.busy = False
        self.next_plan_t = 0.0
        self.plan_t = 0.0
        self.plans = 0
        self.plan_log = link.runtime / f"plans-{battle_id}.jsonl"

    def decide(self, battle) -> tuple[list[int], list[dict]] | None:
        state = battle.state
        if state is None:
            return None
        t = state["t"]
        units = report.merge(battle.static, state)
        # a finished plan?
        try:
            raw, info, report_text, asked_t = self.results.get_nowait()
            self.busy = False
            self._apply(raw, info, report_text, asked_t, units, t)
        except queue.Empty:
            pass
        if not self.busy and conflict(battle) and t >= self.next_plan_t:
            self._ask(battle, units, t)
        if self.executor.plan is None:
            return None
        return self.executor.tick(units, t)

    def _ask(self, battle, units, t) -> None:
        text, _ = report.build(battle.static, battle.state, self.ai, battle.events, self.executor.plan,
                               t - self.plan_t if self.executor.plan else None)
        self.busy = True
        self.board.note(f"t={t:.0f}s asking the general…")

        def work():
            try:
                raw, info = self.general.plan(text)
                self.results.put((raw, info, text, t))
            except Exception as e:  # noqa: BLE001 - report and retry later
                self.results.put((None, {"error": str(e)}, text, t))

        threading.Thread(target=work, daemon=True).start()

    def _apply(self, raw, info, report_text, asked_t, units, t) -> None:
        if raw is None:
            log.warning("plan failed: %s", info.get("error"))
            self.board.note(f"t={t:.0f}s plan FAILED: {info.get('error')}")
            self.next_plan_t = t + 5
            return
        ours = {u.id for u in units if u.alliance == self.ai}
        enemies = {u.id for u in units if u.alliance != self.ai}
        plan, problems = validate(raw, ours, enemies)
        self.executor.set_plan(plan, units, t)
        self.plan_t = t
        self.plans += 1
        self.next_plan_t = t + plan["next_plan_in_s"]
        self.link.say = plan["intent"]
        log.info("plan #%d (%.1fs, ~$%.3f API-equivalent): %s", self.plans, info.get("seconds", 0),
                 info.get("api_equiv_usd", 0), plan["intent"])
        for p in problems:
            log.warning("  plan problem: %s", p)
        self.board.note(f"t={t:.0f}s plan #{self.plans} in {info.get('seconds')}s: {plan['intent']}")
        self.board.update(plan=plan, plans=self.plans)
        with self.plan_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": t, "asked_t": asked_t, "info": info, "problems": problems, "plan": plan,
                                "raw": raw, "report": report_text}) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commander", choices=["general", "demo"], default="general")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    ap.add_argument("--ai-alliance", type=int, help="alliance index (1-based) the server commands")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="do not open the live map")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    board = Board()
    serve(board, args.port)
    url = f"http://127.0.0.1:{args.port}"
    log.info("live map: %s", url)
    if not args.no_browser:
        webbrowser.open(url)

    general = None
    if args.commander == "general":
        from .general import ClaudeCodeGeneral
        general = ClaudeCodeGeneral(model=args.model, effort=args.effort)
        log.info("general: %s", general.describe())

    link = GameLink()
    commander = None
    battle_id = None
    take: list[int] = []
    last_write = 0.0
    last_tick = -1
    log.info("waiting for a battle (runtime: %s)", link.runtime)
    while True:
        for msg in link.poll():
            kind = msg.get("type")
            if kind == "battle_start":
                b = link.battle
                ai = args.ai_alliance or guess_ai_alliance(msg)
                battle_id, take, link.say = b.id, [], None
                if general is not None:
                    general.close()  # a fresh conversation per battle
                    commander = GeneralCommander(ai, general, b.id, board, link)
                else:
                    commander = DemoCommander(ai)
                armies = ", ".join(f"a{a['alliance']}/{a['army']}: {a['units']} units hcp {a['handicap']}"
                                   for a in msg["armies"])
                log.info("battle %s: %d units [%s]; commanding alliance %d", b.id, len(msg["units"]), armies, ai)
                board.update(battle=b.id, ai=ai, plan=None, plans=0, orders=[],
                             general=general.describe() if general else "demo commander")
                board.note(f"battle {b.id} started, commanding alliance {ai}")
            elif kind == "event" and msg.get("name") != "BattleUnitAttacksEnemyUnit":  # too chatty to log
                log.info("event %s%s", msg.get("name"), f" -> phase {msg['phase']}" if "phase" in msg else "")
                board.note(f"t={msg.get('tick', 0) * 0.5:.0f}s {msg.get('name')}")
            elif kind == "applied" and msg.get("errors"):
                log.warning("orders %s: %d errors, first: %s", msg.get("seq"), len(msg["errors"]), msg["errors"][0])
                board.note(f"orders {msg.get('seq')}: {len(msg['errors'])} errors, e.g. {msg['errors'][0]}")
            elif kind == "battle_end":
                log.info("battle %s ended", msg.get("battle"))
                board.note("battle ended")
        b = link.battle
        if b is not None and commander is not None and b.id == battle_id and not b.ended and b.state is not None:
            if b.state["tick"] != last_tick:
                last_tick = b.state["tick"]
                decision = commander.decide(b)
                now = time.monotonic()
                try:
                    if decision is not None and (decision[1] or decision[0] != take):
                        take, orders = decision
                        link.send(take, orders)
                        last_write = now
                        if orders:
                            board.update(orders=orders)
                    elif take and now - last_write > HEARTBEAT_S:
                        link.heartbeat(take)
                        last_write = now
                except OSError as e:  # never die mid-battle over a file hiccup; the next tick writes again
                    log.warning("could not write orders: %s", e)
                ai = commander.ai
                units = report.merge(b.static, b.state)
                board.update(t=b.state["t"], phase=b.phase, units=[
                    {"id": u.id, "x": round(u.x, 1), "y": round(u.y, 1), "h": round(u.h), "w": u.w, "men": u.men,
                     "ai": u.alliance == ai, "kind": u.kind, "missile": u.missile, "gen": u.general,
                     "rout": u.routing or u.shattered, "ctl": u.id in take}
                    for u in units if u.alive])
        time.sleep(POLL_S)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
