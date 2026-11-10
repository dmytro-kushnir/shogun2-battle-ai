"""The AI server: follows the battle the game streams, decides for the AI side, writes orders back.

    .venv\\Scripts\\python -m s2ai.server                      # the LLM general (Claude via your Claude Code login)
    .venv\\Scripts\\python -m s2ai.server --model opus --effort medium
    .venv\\Scripts\\python -m s2ai.server --commander demo     # no model: every AI unit attacks the nearest enemy
    .venv\\Scripts\\python -m s2ai.server --ai-alliance 1      # which alliance is the AI's (default: guessed)
    .venv\\Scripts\\python -m s2ai.server --model haiku --far-plan-s 20 --conversation-plans 4   # cheaper

Plans that fail are retried with a growing delay; after FAIL_HANDBACK failures in a row, or at once when the
Claude usage limit is reached, the army goes back to the game's own AI (until the limit resets, then planning
resumes). runtime/summary-<battle>.json is written when a battle ends (python -m s2ai.summary: all battles).

Live map: http://127.0.0.1:8765 . Plans are logged to runtime/plans-<battle>.jsonl.
Ctrl+C stops it; the bridge hands every unit back to the game's AI within 15 s.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import logging
import queue
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

from . import report
from .executor import Executor
from .gamedata import Shogun2Assets, ability_icons, ability_timings, unit_abilities, unit_icons, unit_stats
from .link import GameLink
from .plan import validate
from .summary import summarize
from .viewer import Board, lan_addresses, serve

log = logging.getLogger("s2ai")

POLL_S = 0.25
HEARTBEAT_S = 3.0
START_PLANNING_AFTER_S = 120  # if the conflict-phase event never arrives
FAIL_HANDBACK = 3             # failed plans in a row before the army goes back to the game's AI
FAR_M = 300.0                 # armies this far apart: plan at most every --far-plan-s


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


class NoCommander:
    """Controls nothing: the AI side keeps the game's own battle AI (the baseline in research runs)."""

    def __init__(self, ai_alliance: int):
        self.ai = ai_alliance
        self.sent = False

    def decide(self, battle) -> tuple[list[int], list[dict]] | None:
        if self.sent:
            return None
        self.sent = True
        return [], []


class GameAIOpponent:
    """Research runs: the human's side is handed to the game's AI planner (the one the historical battles script),
    so every condition fights the same opponent. Every RETARGET_S it is told to attack the commanded side's unit
    nearest to its own centre; the game's AI does the rest (formations, flanks, missiles)."""

    RETARGET_S = 10.0

    def __init__(self, ai_alliance: int):
        self.ai = ai_alliance      # the side we command (the opponent is every other alliance)
        self.next_at = 0.0

    def decide(self, battle) -> list[dict]:
        state = battle.state
        if state is None or state["t"] < self.next_at or not conflict(battle):
            return []
        self.next_at = state["t"] + self.RETARGET_S
        units = report.merge(battle.static, state)
        if any(u.kind == "ship" for u in units):
            return []  # no planner at sea (not probed yet)
        orders = []
        for al in sorted({u.alliance for u in units if u.alliance != self.ai}):
            own = [u for u in units if u.alliance == al and u.fighting_fit]
            targets = [u for u in units if u.alliance == self.ai and u.fighting_fit]
            if not own or not targets:
                continue
            cx, cy = sum(u.x for u in own) / len(own), sum(u.y for u in own) / len(own)
            t = min(targets, key=lambda e: math.hypot(e.x - cx, e.y - cy))
            orders.append({"cmd": "ai_attack", "alliance": al, "target": t.id})
        return orders


class GeneralCommander:
    """The LLM plans in a background thread; the executor carries the latest plan out every tick."""

    def __init__(self, ai: int, general, battle_id: str, board: Board, link: GameLink,
                 min_plan_s: float = 0.0, far_plan_s: float = 0.0):
        self.ai, self.general, self.battle_id, self.board, self.link = ai, general, battle_id, board, link
        self.min_plan_s, self.far_plan_s = min_plan_s, far_plan_s
        self.timings = ability_timings()  # {ability: {active, recharge, uses}}, from the game's database
        self.stats = unit_stats()         # {unit type: stats}, from the game's database
        self.executor = Executor(ai, self.timings, self.stats)
        self.failures = 0                 # failed plans in a row
        self.paused_until = 0.0           # wall clock: usage limit reached, the game's AI fights until then
        self.handed_back = False          # the army is with the game's AI (no plan to carry out)
        self.results: queue.Queue = queue.Queue()
        self.busy = False
        self.next_plan_t = 0.0
        self.plan_t = 0.0
        self.plans = 0
        self.plan_log = link.runtime / f"plans-{battle_id}.jsonl"
        self.fail_log = link.runtime / f"failures-{battle_id}.jsonl"
        self.abilities = unit_abilities()  # {unit type: special abilities}, from the game's database (cached)

    def extra_summary(self) -> dict:
        return {"cavalry_pullbacks": len(self.executor.pullbacks)}

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
            self._apply(raw, info, report_text, asked_t, units, t, battle)
        except queue.Empty:
            pass
        if self.paused_until and time.time() >= self.paused_until:
            self.paused_until = 0.0
            self.next_plan_t = 0.0
            log.info("usage limit should have reset: asking the general again")
            self.board.note(f"t={t:.0f}s usage limit reset: asking the general again")
        # nothing to command (or nobody left to fight): no point asking the model
        armies_up = any(u.alliance == self.ai and u.fighting_fit for u in units) and \
            any(u.alliance != self.ai and u.fighting_fit for u in units)
        if not self.busy and not self.paused_until and armies_up and conflict(battle) and t >= self.next_plan_t:
            self._ask(battle, units, t)
        if self.handed_back:
            return [], []  # control nothing: the bridge gives every unit back to the game's AI
        if self.executor.plan is None:
            return None
        self.executor.set_buildings(battle.buildings)
        n, npb = len(self.executor.ability_log), len(self.executor.pullbacks)
        decision = self.executor.tick(units, t)
        if len(self.executor.ability_log) != n:
            for a in self.executor.ability_log[n:]:
                self.board.note(f"t={t:.0f}s {a['unit']} uses {a['ability']}")
            self.board.update(ab_used=list(self.executor.ability_log))
        for pb in self.executor.pullbacks[npb:]:
            self.board.note(f"t={t:.0f}s cavalry {pb['unit']} pulls back from {pb['target']} to charge again")
        return decision

    def _ask(self, battle, units, t) -> None:
        text, _ = report.build(battle.static, battle.state, self.ai, battle.events, self.executor.plan,
                               t - self.plan_t if self.executor.plan else None, battle.buildings, battle.terrain,
                               battle.morale, self.abilities,
                               ability_state=lambda uid, a: self.executor.ability_text(uid, a, t),
                               unit_stats=self.stats, unit_note=self.executor.unit_note,
                               static_info=getattr(self.general, "fresh", True))
        self.busy = True
        self.board.note(f"t={t:.0f}s asking the general…")

        def work():
            try:
                raw, info = self.general.plan(text)
                self.results.put((raw, info, text, t))
            except Exception as e:  # noqa: BLE001 - report and retry later
                self.results.put((None, {"error": str(e), "resume_at": getattr(e, "resume_at", None)}, text, t))

        threading.Thread(target=work, daemon=True).start()

    def _apply(self, raw, info, report_text, asked_t, units, t, battle) -> None:
        if raw is None:
            self._failed(info, t)
            return
        self.failures = 0
        if self.handed_back:
            log.info("the general is back: taking the army from the game's AI")
            self.board.note(f"t={t:.0f}s the general takes command again")
            self.handed_back = False
        ours = {u.id for u in units if u.alliance == self.ai}
        enemies = {u.id for u in units if u.alliance != self.ai}
        forts = {f["id"] for f in report.fortifications(battle.buildings)}
        by_id = {u.id: self.abilities.get(u.type, []) for u in units if u.alliance == self.ai}
        plan, problems = validate(raw, ours, enemies, forts, by_id)
        problems += self.executor.set_plan(plan, units, t)
        self.plan_t = t
        self.plans += 1
        wait = max(plan["next_plan_in_s"], self.min_plan_s)
        if self.far_plan_s and self._far_apart(units):
            wait = max(wait, self.far_plan_s)
        self.next_plan_t = t + wait
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

    def _far_apart(self, units) -> bool:
        ours = [u for u in units if u.alliance == self.ai and u.fighting_fit]
        theirs = [u for u in units if u.alliance != self.ai and u.fighting_fit]
        return bool(ours and theirs) and min(report.dist(a, b) for a in ours for b in theirs) > FAR_M

    def _failed(self, info: dict, t: float) -> None:
        err, resume = info.get("error"), info.get("resume_at")
        self.failures += 1
        with self.fail_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": t, "error": err, "limit": resume is not None}) + "\n")
        if resume is not None and resume > time.time():
            # nothing to gain by asking every few seconds until the reset (battle 091516: ~200 s of retries)
            self.paused_until = resume
            until = dt.datetime.fromtimestamp(resume).strftime("%H:%M")
            log.warning("usage limit reached (%s): the game's AI commands the army until %s", err, until)
            self.board.note(f"t={t:.0f}s usage limit reached: the game's AI commands the army until {until}")
            self._hand_back(f"The general is out of contact until {until}; the army fights on its own.")
            return
        delay = min(60.0, 5.0 * 2 ** (self.failures - 1))
        self.next_plan_t = t + delay
        log.warning("plan failed (%d in a row, next try in %.0f s): %s", self.failures, delay, err)
        self.board.note(f"t={t:.0f}s plan FAILED ({self.failures} in a row): {err}")
        if self.failures >= FAIL_HANDBACK:
            self._hand_back("The general is out of contact; the army fights on its own.")

    def _hand_back(self, say: str) -> None:
        if not self.handed_back:
            log.info("handing the army back to the game's AI")
            self.handed_back = True
            self.executor.plan = None  # a stale plan must not run on when the general comes back
            self.link.say = say
            self.board.update(plan=None)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commander", choices=["general", "demo", "none"], default="general",
                    help="none: the AI side keeps the game's own battle AI (research baseline)")
    ap.add_argument("--opponent", choices=["human", "gameai"], default="human",
                    help="gameai: the human's side is handed to the game's AI planner (research runs; watch, "
                         "do not command)")
    ap.add_argument("--condition", default="",
                    help="a label for this run's experimental condition, saved with every battle (research runs)")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    ap.add_argument("--conversation-plans", type=int, default=6,
                    help="plans per conversation before a fresh one (each plan re-reads the whole conversation)")
    ap.add_argument("--min-plan-s", type=float, default=0.0,
                    help="never plan more often than this (the general asks for 4-30 s)")
    ap.add_argument("--far-plan-s", type=float, default=15.0,
                    help=f"while the armies are over {FAR_M:.0f} m apart, plan at most this often (0 = off)")
    ap.add_argument("--ai-alliance", type=int, help="alliance index (1-based) the server commands")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--lan", action="store_true",
                    help="let other devices on the local network open the map (read-only, no password)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the live map")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    board = Board()
    lan = lan_addresses() if args.lan else []
    serve(board, args.port, "0.0.0.0" if args.lan else "127.0.0.1", lan, Shogun2Assets())
    url = f"http://127.0.0.1:{args.port}"
    log.info("battle map: %s", url)
    for ip in lan:
        log.info("battle map on the local network: http://%s:%d", ip, args.port)
    if not args.no_browser:
        webbrowser.open(url)

    general = None
    if args.commander == "general":
        from .general import ClaudeCodeGeneral
        general = ClaudeCodeGeneral(model=args.model, effort=args.effort,
                                    conversation_plans=args.conversation_plans)
        log.info("general: %s", general.describe())

    threading.Thread(target=unit_icons, daemon=True).start()  # unit pictures for the map, made once
    try:
        ability_icons()  # the game's ability icons for the map, extracted once into runtime/icons
    except OSError as e:
        log.warning("ability icons: %s", e)
    abl = unit_abilities()
    link = GameLink()
    commander = None
    opponent = None
    battle_id = None
    take: list[int] = []
    last_write = 0.0
    last_tick = -1
    log.info("waiting for a battle (runtime: %s)", link.runtime)
    summarized: set[str] = set()

    def write_summary(bid: str | None) -> None:
        if not bid or bid in summarized:
            return
        summarized.add(bid)
        try:
            extra = commander.extra_summary() if hasattr(commander, "extra_summary") else {}
            sm = summarize(bid, link.runtime, extra)
            if sm:
                (link.runtime / f"summary-{bid}.json").write_text(json.dumps(sm, indent=1), encoding="utf-8")
                log.info("battle %s: %s after %d s; AI kept %.0f%% of its men fighting, the player %.0f%%; "
                         "%d plans, ~$%.2f API-equivalent", bid, sm["result"], sm["duration_s"],
                         100 * sm["ai"]["fighting_share"], 100 * sm["player"]["fighting_share"], sm["plans"],
                         sm["api_equiv_usd"])
        except Exception:  # noqa: BLE001 - a summary must never stop the server
            log.exception("could not summarize battle %s", bid)

    while True:
        for msg in link.poll():
            kind = msg.get("type")
            if kind == "battle_start":
                b = link.battle
                if battle_id != b.id:
                    write_summary(battle_id)  # the last battle was left without a battle_end
                ai = args.ai_alliance or guess_ai_alliance(msg)
                battle_id, take, link.say = b.id, [], None
                if general is not None:
                    general.close()  # a fresh conversation per battle
                    commander = GeneralCommander(ai, general, b.id, board, link, args.min_plan_s, args.far_plan_s)
                elif args.commander == "none":
                    commander = NoCommander(ai)
                else:
                    commander = DemoCommander(ai)
                opponent = GameAIOpponent(ai) if args.opponent == "gameai" else None
                armies = ", ".join(f"a{a['alliance']}/{a['army']}: {a['units']} units hcp {a['handicap']}"
                                   for a in msg["armies"])
                log.info("battle %s: %d units [%s]; commanding alliance %d", b.id, len(msg["units"]), armies, ai)
                desc = general.describe() if general else f"{args.commander} commander"
                board.update(battle=b.id, ai=ai, plan=None, plans=0, orders=[], general=desc, ab_used=[],
                             timings=ability_timings())
                # for the history view: which side we commanded, and with what
                (link.runtime / f"meta-{b.id}.json").write_text(json.dumps({
                    "ai": ai, "general": desc, "commander": args.commander, "opponent": args.opponent,
                    "condition": args.condition, "commit": git_commit(),
                    "settings": {k: getattr(args, k) for k in ("model", "effort", "conversation_plans",
                                                                "min_plan_s", "far_plan_s")}}))
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
                write_summary(battle_id)
        b = link.battle
        if b is not None and commander is not None and b.id == battle_id and not b.ended and b.state is not None:
            if b.state["tick"] != last_tick:
                last_tick = b.state["tick"]
                try:
                    decision = commander.decide(b)
                except Exception:  # noqa: BLE001 - a bad plan or state must not stop the server mid-battle
                    log.exception("commander failed on tick %s", last_tick)
                    decision = None
                if opponent is not None:
                    try:
                        extra = opponent.decide(b)
                    except Exception:  # noqa: BLE001
                        log.exception("opponent failed on tick %s", last_tick)
                        extra = []
                    if extra:
                        decision = (decision[0], decision[1] + extra) if decision is not None else (take, extra)
                now = time.monotonic()
                try:
                    if decision is not None and (decision[1] or decision[0] != take):
                        take, orders = decision
                        link.send(take, orders)
                        last_write = now
                        if orders:
                            board.update(orders=orders)
                            with (link.runtime / f"orders-{b.id}.jsonl").open("a", encoding="utf-8") as f:
                                f.write(json.dumps({"t": b.state["t"], "orders": orders}) + "\n")
                    elif take and now - last_write > HEARTBEAT_S:
                        link.heartbeat(take)
                        last_write = now
                except OSError as e:  # never die mid-battle over a file hiccup; the next tick writes again
                    log.warning("could not write orders: %s", e)
                ai = commander.ai
                units = report.merge(b.static, b.state)
                board.update(t=b.state["t"], phase=b.phase, units=[
                    {"id": u.id, "x": round(u.x, 1), "y": round(u.y, 1), "h": round(u.h), "w": u.w, "men": u.men,
                     "men0": u.men0, "type": u.type,
                     "ai": u.alliance == ai, "kind": u.kind, "missile": u.missile, "gen": u.general,
                     "rout": u.routing or u.shattered, "ctl": u.id in take, "z": round(u.z, 1), "gar": u.garrisoned,
                     "abl": abl.get(u.type) if u.alliance == ai else None}
                    for u in units if u.alive], buildings=map_buildings(b.buildings, ai))
                if last_tick % 10 == 0:  # every 5 s: the learned ground heights
                    board.update(terrain=b.terrain.cells())
        time.sleep(POLL_S)


def git_commit() -> str:
    """The code version a battle ran with (research runs must name it); "" outside a git checkout."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5,
                             cwd=Path(__file__).resolve().parents[1])
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                               text=True, timeout=5, cwd=Path(__file__).resolve().parents[1])
        return out.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "") if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def map_buildings(buildings: dict | None, ai: int) -> list[dict]:
    """Buildings for the map: fortifications with kind / side / health, other buildings as plain shapes."""
    out = []
    for bb in (buildings or {}).values():
        if bb.get("x") is None:
            continue
        kind = report.building_kind(bb.get("name")) if bb.get("mil") else "scenery"
        owner = bb.get("owner") or 0
        out.append({"id": bb["id"], "kind": kind, "x": round(bb["x"], 1), "y": round(bb["y"], 1),
                    "z": round(bb.get("z") or 0, 1), "side": "ai" if owner == ai else "pl" if owner else "",
                    "hp": bb.get("hp"), "gar": bool(bb.get("gar")), "cap": bb.get("cap"), "name": bb.get("name")})
    return out


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
