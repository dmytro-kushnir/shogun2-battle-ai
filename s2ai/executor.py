"""Carries out the general's plan every state tick (0.5 s) without calling the model.

Per unit it works out the wanted command (group task, then reflexes on top), and only sends it when it changes
or when a stuck order is due to be repeated. Routing / shattered units are left to the game (released).
"""
from __future__ import annotations

import math

from .report import U, bearing, building_kind, dist

REISSUE_S = 12.0        # repeat an unchanged command this often if the unit seems to have dropped it
SPACING_EXTRA_M = 6.0   # gap between units in a line
DEFAULT_WIDTH_M = 30.0
VIA_REACHED_M = 30.0     # a unit counts as through a waypoint this close to it
DEFAULT_TIMING = {"active": 30.0, "recharge": 180.0, "uses": -1}

# Cavalry hit-and-run: a charge does its damage on impact; cavalry that stays in a melee bleeds (battle 091516:
# three units that reached the guns intact went from ~210 men to ~20 in 40 s of melee next to the Marines). So
# cavalry in contact pulls back once the charge is spent and charges again - unless it is cutting down artillery
# crews or a unit that is about to break.
CAV_CONTACT_M = 30.0     # centres this close: in melee
CAV_MELEE_S = 12.0       # pull out after this long in melee...
CAV_MELEE_SPEARS_S = 6.0 # ...or this long against units with a bonus against cavalry (spears, bayonets)
CAV_LOSS_PULL = 0.2      # ...or as soon as it has lost this share of its men in this melee
CAV_STAY_BELOW = 0.35    # stay on a target that is down to this share of its men (it is about to break)
CAV_PULLBACK_M = 110.0   # how far to pull back before charging again
CAV_PULLBACK_S = 20.0    # longest pull-back
RETARGET_REACH_M = 250.0 # a replacement target this much farther than the nearest enemy is still considered


def matchup(a: U, e: U, stats: dict[str, dict]) -> float:
    """How bad a fight attacker `a` picks with enemy `e`, as a factor on the distance (1 = neutral, lower =
    better). Shogun 2 is rock-paper-scissors: spears beat cavalry, cavalry beats missile units, artillery and
    swords caught in the open, swords beat spears. Battle 120309: each time a gun crew broke, the cavalry
    re-targeted the nearest enemy - the Kyoto Police spears (+20 vs cavalry) - and lost ~45 men to them."""
    sa, se = stats.get(a.type, {}), stats.get(e.type, {})
    f = 1.0
    if a.kind == "cav":
        if se.get("vs_cav", 0) > 0:
            f *= 1 + se["vs_cav"] / 5       # pikes / yari (+20): x5; bayonets (+7): x2.4
        elif e.kind == "art":
            f *= 0.5
        elif e.missile:
            f *= 0.6
    elif sa.get("vs_cav", 0) > 0 and e.kind == "cav":
        f *= 0.5                            # spears go for cavalry
    elif not a.missile and a.kind == "inf" and se.get("vs_cav", 0) > 0 and not e.missile and sa.get("melee", 0) > se.get("melee", 0):
        f *= 0.8                            # swords against spears
    if e.missile and not a.missile and e.kind != "art":
        f *= 0.9                            # missile units are weak in melee
    return f


def line_slots(n: int, cx: float, cy: float, facing: float, widths: list[float]) -> list[tuple[float, float]]:
    """n positions side by side, centred on (cx, cy), in a line facing `facing` (compass degrees)."""
    rad = math.radians(facing)
    # right-hand direction of a unit facing `facing`: facing + 90
    rx, ry = math.sin(rad + math.pi / 2), math.cos(rad + math.pi / 2)
    spans = [(w or DEFAULT_WIDTH_M) + SPACING_EXTRA_M for w in widths]
    total = sum(spans)
    slots, offset = [], -total / 2
    for s in spans:
        mid = offset + s / 2
        slots.append((cx + rx * mid, cy + ry * mid))
        offset += s
    return slots


class Executor:
    def __init__(self, ai: int, timings: dict[str, dict] | None = None, stats: dict[str, dict] | None = None):
        self.ai = ai
        self.timings = timings or {}  # ability -> {active, recharge, uses} from the game's database
        self.stats = stats or {}      # unit type -> stats from the game's database
        self.plan: dict | None = None
        self.plan_t = 0.0
        self.slots: dict[int, tuple[float, float, float]] = {}  # unit -> (x, y, facing) of its group's line
        self.issued: dict[int, tuple[str, float]] = {}          # unit -> (signature, battle time sent)
        self.engaged: dict[int, int] = {}                      # unit -> enemy it is engaging by reflex
        self.last_orders: list[dict] = []
        self.buildings: dict[int, dict] = {}                   # id -> building (from the bridge), for sieges
        self.pending_abilities: list[dict] = []                # from the latest plan, sent on the next tick
        self.ability_used: dict[tuple[int, str], list[float]] = {}  # (unit, ability) -> battle times sent
        self.ability_log: list[dict] = []                      # every ability sent: {t, unit, ability}
        self.via_slots: dict[int, list[tuple[float, float, float]]] = {}  # unit -> its spots at the group's waypoints
        self.via_reached: dict[int, list[tuple[float, float]]] = {}       # unit -> waypoints it has been through
        self.retarget: dict[int, int] = {}                      # unit -> the enemy it took on when its target broke
        self.melee: dict[int, tuple[float, int, int]] = {}      # cavalry unit -> (in melee since, its men then, target)
        self.pullback: dict[int, tuple[float, float, float, int]] = {}  # cavalry unit -> (x, y, until, target)
        self.pullbacks: list[dict] = []                         # every pull-back: {t, unit, target}
        self.shot: dict[int, str] = {}                          # ship -> shot type we last told it to load

    def set_buildings(self, buildings: dict[int, dict] | None) -> None:
        self.buildings = buildings or {}

    # ---------------------------------------------------------------- special abilities
    # The game does not report which ability is active (current_special_ability() said "none" all through a battle
    # in which units used them), so the state comes from what was sent and the durations in the game's database.
    # Recharge is counted from activation.

    def ability_status(self, uid: int, ability: str, t: float) -> tuple[str, float]:
        """-> ("ready", 0) | ("active", seconds left) | ("recharging", seconds until ready) | ("used up", 0)."""
        used = self.ability_used.get((uid, ability))
        if not used:
            return "ready", 0.0
        tm = self.timings.get(ability, DEFAULT_TIMING)
        age = t - used[-1]
        if age < tm["active"]:
            return "active", tm["active"] - age
        if tm.get("uses", -1) > 0 and len(used) >= tm["uses"]:
            return "used up", 0.0
        if age < tm["recharge"]:
            return "recharging", tm["recharge"] - age
        return "ready", 0.0

    def ability_text(self, uid: int, ability: str, t: float) -> str:
        st, left = self.ability_status(uid, ability, t)
        if st == "active":
            return f"{ability} (ACTIVE, {left:.0f}s left)"
        if st == "recharging":
            return f"{ability} (recharging, ready in {left:.0f}s)"
        if st == "used up":
            return f"{ability} (used up)"
        return f"{ability} (ready)"

    def set_plan(self, plan: dict, units: list[U], t: float) -> list[str]:
        """-> problems (abilities that are not ready are dropped)."""
        self.plan, self.plan_t = plan, t
        self.slots = {}
        self.retarget = {}  # a new plan: its targets first, replacements chosen afresh
        problems = []
        self.pending_abilities = []
        for a in plan.get("abilities") or []:
            st, left = self.ability_status(a["unit"], a["ability"], t)
            if st == "ready":
                self.pending_abilities.append(a)
            else:
                problems.append(f"ability {a['ability']} on {a['unit']}: {st}"
                                + (f", {left:.0f}s" if left else "") + "; not sent")
        by_id = {u.id: u for u in units}
        enemies = [u for u in units if u.alliance != self.ai and u.fighting_fit]
        ecx = sum(e.x for e in enemies) / len(enemies) if enemies else 0.0
        ecy = sum(e.y for e in enemies) / len(enemies) if enemies else 0.0
        for g in plan["groups"]:
            if g["task"] not in ("move", "hold"):
                continue
            members = [by_id[i] for i in g["units"] if i in by_id]
            if not members:
                continue
            if g["position"]:
                cx, cy = g["position"]
            else:
                cx, cy = sum(u.x for u in members) / len(members), sum(u.y for u in members) / len(members)
            facing = g["facing"] if g["facing"] is not None else bearing(cx, cy, ecx, ecy)
            rad = math.radians(facing)
            rx, ry = math.sin(rad + math.pi / 2), math.cos(rad + math.pi / 2)
            # keep left-to-right order as they stand now, so units do not cross each other's paths
            members.sort(key=lambda u: (u.x - cx) * rx + (u.y - cy) * ry)
            for u, (sx, sy) in zip(members, line_slots(len(members), cx, cy, facing, [u.w for u in members])):
                self.slots[u.id] = (sx, sy, facing)
        # waypoints: each unit gets its own spot at every waypoint (a line across the direction of travel), so a
        # group goes around a flank side by side instead of piling onto one point
        self.via_slots = {}
        for g in plan["groups"]:
            members = [by_id[i] for i in g["units"] if i in by_id]
            if not g.get("via") or not members:
                continue
            px, py = sum(u.x for u in members) / len(members), sum(u.y for u in members) / len(members)
            for wx, wy in g["via"]:
                travel = bearing(px, py, wx, wy)
                rad = math.radians(travel)
                rx, ry = math.sin(rad + math.pi / 2), math.cos(rad + math.pi / 2)
                members.sort(key=lambda u: (u.x - px) * rx + (u.y - py) * ry)
                for u, (sx, sy) in zip(members, line_slots(len(members), wx, wy, travel, [u.w for u in members])):
                    self.via_slots.setdefault(u.id, []).append((sx, sy, travel))
                px, py = wx, wy
        return problems

    def _via_step(self, u: U, g: dict) -> dict | None:
        """The move to the unit's next waypoint, or None when it has been through all of them. A waypoint counts as
        passed if the unit was ever within VIA_REACHED_M of it - plans are often repeated, and a repeated plan must
        not send units back to a waypoint they already went through."""
        reached = self.via_reached.setdefault(u.id, [])
        for (wx, wy), (sx, sy, travel) in zip(g.get("via") or [], self.via_slots.get(u.id, [])):
            if any(math.hypot(wx - rx, wy - ry) < VIA_REACHED_M * 1.5 for rx, ry in reached):
                continue
            if math.hypot(u.x - sx, u.y - sy) < VIA_REACHED_M:
                reached.append((wx, wy))
                continue
            return {"cmd": "move", "unit": u.id, "x": sx, "y": sy, "facing": travel, "width": u.w or None,
                    "run": g["run"], "_sig": f"via:{round(sx / 5)}:{round(sy / 5)}:{g['run']}"}
        return None

    def _fort_centre(self) -> tuple[float, float]:
        pts = [(b["x"], b["y"]) for b in self.buildings.values()
               if b.get("x") is not None and building_kind(b.get("name")) in ("wall", "tower", "gate")]
        if not pts:
            return 0.0, 0.0
        return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)

    def _group_of(self, uid: int) -> dict | None:
        if self.plan:
            for g in self.plan["groups"]:
                if uid in g["units"]:
                    return g
        return None

    def tick(self, units: list[U], t: float) -> tuple[list[int], list[dict]]:
        """-> (units to control, new orders). Without a plan: control nothing."""
        if self.plan is None:
            return [], []
        ours = [u for u in units if u.alliance == self.ai and u.fighting_fit]
        enemies = [u for u in units if u.alliance != self.ai and u.fighting_fit]
        by_id = {u.id: u for u in units}
        orders, take = [], []
        for u in ours:
            take.append(u.id)
            want = self._hit_and_run(u, self._want(u, by_id, enemies), by_id, enemies, t)
            if want is None:
                continue
            sig = want.pop("_sig")
            last = self.issued.get(u.id)
            stale = last is not None and t - last[1] > REISSUE_S and not u.moving
            if last is None or last[0] != sig or stale:
                self.issued[u.id] = (sig, t)
                orders.append(want)
        # ships: load the group's shot type when it changes (the game does not report the loaded one)
        for u in ours:
            g = self._group_of(u.id)
            if u.kind == "ship" and g and g.get("shot") and self.shot.get(u.id) != g["shot"]:
                self.shot[u.id] = g["shot"]
                orders.append({"cmd": "shot_type", "unit": u.id, "shot": g["shot"]})
        # special abilities: once per plan, after the unit's movement order, and only when ready (the general
        # often repeats its last plan, abilities included)
        fit = {u.id for u in ours}
        for a in self.pending_abilities:
            if a["unit"] in fit and self.ability_status(a["unit"], a["ability"], t)[0] == "ready":
                self.ability_used.setdefault((a["unit"], a["ability"]), []).append(t)
                self.ability_log.append({"t": t, "unit": a["unit"], "ability": a["ability"]})
                orders.append({"cmd": "ability", "unit": a["unit"], "ability": a["ability"]})
        self.pending_abilities = []
        self.last_orders = orders
        return take, orders

    def unit_note(self, uid: int) -> str:
        """What the executor is doing with the unit on its own, for the report."""
        if uid in self.pullback:
            return " | pulling back after its charge, will charge again"
        if uid in self.shot:
            return f" | loaded: {self.shot[uid]}"
        return ""

    def _hit_and_run(self, u: U, want: dict | None, by_id: dict[int, U], enemies: list[U], t: float) -> dict | None:
        pb = self.pullback.get(u.id)
        if pb is not None and (want is None or want.get("cmd") != "attack"):
            del self.pullback[u.id]  # the general gave it something else to do: that wins
            pb = None
        if pb is not None:
            x, y, until, target = pb
            tgt = by_id.get(target)
            if t < until and math.hypot(u.x - x, u.y - y) > 15 and tgt is not None and tgt.fighting_fit:
                return {"cmd": "move", "unit": u.id, "x": x, "y": y, "facing": bearing(x, y, tgt.x, tgt.y),
                        "run": True, "_sig": f"pullback:{round(x / 10)}:{round(y / 10)}"}
            del self.pullback[u.id]  # back far enough (or the target is gone): charge again
            self.melee.pop(u.id, None)
        if u.kind != "cav" or want is None or want.get("cmd") != "attack":
            self.melee.pop(u.id, None)
            return want
        target = by_id.get(want["target"])
        if target is None:
            return want
        # The melee is with whoever the cavalry is in contact with, which need not be its target: battle 141245,
        # cavalry sent at a gun was caught by the Kyoto Police guarding it (16 m off, the gun 60 m) and lost 35 men
        # in 44 s without ever pulling back, because only the distance to the gun was checked.
        # So the melee runs while it touches any enemy, and is judged by the worst of them (spears first).
        contact = [e for e in enemies if dist(u, e) <= CAV_CONTACT_M]
        m = self.melee.get(u.id)
        if not contact:
            if m is not None and not any(dist(u, e) <= CAV_CONTACT_M * 2 for e in enemies):
                del self.melee[u.id]  # centres jitter in a melee: only a clear gap ends it
            return want
        foe = max(contact, key=lambda e: (self.stats.get(e.type, {}).get("vs_cav", 0), e.id == target.id))
        if m is None:
            self.melee[u.id] = (t, u.men, foe.id)
            return want
        since, men_then, _ = m
        st = self.stats.get(foe.type, {})
        lost = (men_then - u.men) / men_then if men_then else 0.0
        hold = CAV_MELEE_SPEARS_S if st.get("vs_cav", 0) > 0 else CAV_MELEE_S
        if lost < CAV_LOSS_PULL and (foe.kind == "art" or (foe.men0 and foe.men < foe.men0 * CAV_STAY_BELOW)
                                     or t - since < hold):
            return want
        # pull back away from the enemies around it, then charge again
        close = [e for e in enemies if dist(u, e) < 150] or [target]
        cx, cy = sum(e.x for e in close) / len(close), sum(e.y for e in close) / len(close)
        away = math.radians(bearing(cx, cy, u.x, u.y))
        x, y = u.x + math.sin(away) * CAV_PULLBACK_M, u.y + math.cos(away) * CAV_PULLBACK_M
        self.pullback[u.id] = (x, y, t + CAV_PULLBACK_S, target.id)
        self.pullbacks.append({"t": t, "unit": u.id, "target": target.id, "foe": foe.id})
        self.melee.pop(u.id, None)
        return {"cmd": "move", "unit": u.id, "x": x, "y": y, "facing": bearing(x, y, target.x, target.y),
                "run": True, "_sig": f"pullback:{round(x / 10)}:{round(y / 10)}"}

    def _best_target(self, u: U, enemies: list[U], within: float | None = None) -> U | None:
        """The enemy to take on: distance weighed by the matchup. within: only enemies closer than this."""
        if within is not None:
            enemies = [e for e in enemies if dist(u, e) < within]
        if not enemies:
            return None
        nearest = min(dist(u, e) for e in enemies)
        pool = [e for e in enemies if dist(u, e) <= nearest + RETARGET_REACH_M]
        return min(pool, key=lambda e: (dist(u, e) + 20) * matchup(u, e, self.stats))

    def _want(self, u: U, by_id: dict[int, U], enemies: list[U]) -> dict | None:
        g = self._group_of(u.id)
        near = min(enemies, key=lambda e: dist(u, e)) if enemies else None
        d = dist(u, near) if near else 1e9
        # units manning or scaling a wall stay on it: no reflexes pull them off
        reflexes = g is not None and g["task"] not in ("garrison", "climb")

        # reflex: missile units step back from melee threats
        if reflexes and g["keep_range_m"] > 0 and u.missile and near and not near.missile and d < g["keep_range_m"]:
            away = bearing(near.x, near.y, u.x, u.y)
            step = g["keep_range_m"] - d + 25
            x = u.x + math.sin(math.radians(away)) * step
            y = u.y + math.cos(math.radians(away)) * step
            return {"cmd": "move", "unit": u.id, "x": x, "y": y, "facing": (away + 180) % 360, "run": True,
                    "_sig": f"evade:{near.id}:{round(x / 20)}:{round(y / 20)}"}

        # reflex: engage what comes close (and stay on it until it breaks)
        if reflexes and g["engage_within_m"] > 0:
            cur = by_id.get(self.engaged.get(u.id, -1))
            if cur is not None and cur.fighting_fit and dist(u, cur) < g["engage_within_m"] * 2:
                return {"cmd": "attack", "unit": u.id, "target": cur.id, "run": True, "_sig": f"attack:{cur.id}"}
            self.engaged.pop(u.id, None)
            pick = self._best_target(u, enemies, within=g["engage_within_m"])
            if pick is not None and not (u.kind == "cav" and matchup(u, pick, self.stats) > 2):
                self.engaged[u.id] = pick.id  # (cavalry does not ride into spears by reflex)
                return {"cmd": "attack", "unit": u.id, "target": pick.id, "run": True, "_sig": f"attack:{pick.id}"}

        if g is None:
            return None  # not in the plan: keep its last order
        if g.get("via"):
            step = self._via_step(u, g)
            if step is not None:
                return step
        task = g["task"]
        if task == "attack":
            target = by_id.get(g["target"])
            if target is None or not target.fighting_fit:
                # target lost: a new one by distance and matchup, kept until it breaks too
                target = by_id.get(self.retarget.get(u.id, -1))
                if target is None or not target.fighting_fit:
                    target = self._best_target(u, enemies)
                    if target is None:
                        return None
                    self.retarget[u.id] = target.id
            return {"cmd": "attack", "unit": u.id, "target": target.id, "run": g["run"] or dist(u, target) < 80,
                    "_sig": f"attack:{target.id}"}
        if task == "withdraw":
            return {"cmd": "withdraw", "unit": u.id, "run": True, "_sig": "withdraw"}
        if task == "garrison":
            b = self.buildings.get(g["building"])
            if b is not None and b.get("x") is not None and building_kind(b.get("name")) in ("wall", "tower", "gate"):
                # Castle walls are not "defendable" buildings (run 20: defend_building refused every wall piece),
                # and a move to the piece's position leaves the unit at its foot (run 23: 4 m away, 7 m too low).
                # man_wall makes the bridge use climb_building - what the game's own scripts use to put units on
                # walls - and fall back to that move if the game refuses. Facing: away from the castle centre.
                cx, cy = self._fort_centre()
                facing = bearing(cx, cy, b["x"], b["y"])
                return {"cmd": "man_wall", "unit": u.id, "building": g["building"], "x": b["x"], "y": b["y"],
                        "facing": facing, "run": True, "_sig": f"man_wall:{g['building']}"}
            if u.garrisoned:
                return None  # inside
            return {"cmd": "garrison", "unit": u.id, "building": g["building"], "run": True,
                    "_sig": f"garrison:{g['building']}"}
        if task in ("climb", "attack_building"):
            return {"cmd": task, "unit": u.id, "building": g["building"], "run": True,
                    "_sig": f"{task}:{g['building']}"}
        if task == "occupy":
            x, y = g["position"]
            return {"cmd": "occupy", "unit": u.id, "x": x, "y": y, "run": g["run"],
                    "_sig": f"occupy:{round(x / 5)}:{round(y / 5)}"}
        slot = self.slots.get(u.id)
        if slot is None:
            return None
        x, y, facing = slot
        if math.hypot(u.x - x, u.y - y) < 8 and abs(((u.h - facing) + 180) % 360 - 180) < 20:
            return None  # in its slot: nothing to do
        return {"cmd": "move", "unit": u.id, "x": x, "y": y, "facing": facing, "width": u.w or None,
                "run": g["run"], "_sig": f"move:{round(x / 5)}:{round(y / 5)}:{round(facing / 10)}:{g['run']}"}
