"""Carries out the general's plan every state tick (0.5 s) without calling the model.

Per unit it works out the wanted command (group task, then reflexes on top), and only sends it when it changes
or when a stuck order is due to be repeated. Routing / shattered units are left to the game (released).
"""
from __future__ import annotations

import math

from .report import U, bearing, dist

REISSUE_S = 12.0        # repeat an unchanged command this often if the unit seems to have dropped it
SPACING_EXTRA_M = 6.0   # gap between units in a line
DEFAULT_WIDTH_M = 30.0


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
    def __init__(self, ai: int):
        self.ai = ai
        self.plan: dict | None = None
        self.plan_t = 0.0
        self.slots: dict[int, tuple[float, float, float]] = {}  # unit -> (x, y, facing) of its group's line
        self.issued: dict[int, tuple[str, float]] = {}          # unit -> (signature, battle time sent)
        self.engaged: dict[int, int] = {}                      # unit -> enemy it is engaging by reflex
        self.last_orders: list[dict] = []

    def set_plan(self, plan: dict, units: list[U], t: float) -> None:
        self.plan, self.plan_t = plan, t
        self.slots = {}
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
            want = self._want(u, by_id, enemies)
            if want is None:
                continue
            sig = want.pop("_sig")
            last = self.issued.get(u.id)
            stale = last is not None and t - last[1] > REISSUE_S and not u.moving
            if last is None or last[0] != sig or stale:
                self.issued[u.id] = (sig, t)
                orders.append(want)
        self.last_orders = orders
        return take, orders

    def _want(self, u: U, by_id: dict[int, U], enemies: list[U]) -> dict | None:
        g = self._group_of(u.id)
        near = min(enemies, key=lambda e: dist(u, e)) if enemies else None
        d = dist(u, near) if near else 1e9

        # reflex: missile units step back from melee threats
        if g and g["keep_range_m"] > 0 and u.missile and near and not near.missile and d < g["keep_range_m"]:
            away = bearing(near.x, near.y, u.x, u.y)
            step = g["keep_range_m"] - d + 25
            x = u.x + math.sin(math.radians(away)) * step
            y = u.y + math.cos(math.radians(away)) * step
            return {"cmd": "move", "unit": u.id, "x": x, "y": y, "facing": (away + 180) % 360, "run": True,
                    "_sig": f"evade:{near.id}:{round(x / 20)}:{round(y / 20)}"}

        # reflex: engage what comes close (and stay on it until it breaks)
        if g and g["engage_within_m"] > 0:
            cur = by_id.get(self.engaged.get(u.id, -1))
            if cur is not None and cur.fighting_fit and dist(u, cur) < g["engage_within_m"] * 2:
                return {"cmd": "attack", "unit": u.id, "target": cur.id, "run": True, "_sig": f"attack:{cur.id}"}
            self.engaged.pop(u.id, None)
            if near and d < g["engage_within_m"]:
                self.engaged[u.id] = near.id
                return {"cmd": "attack", "unit": u.id, "target": near.id, "run": True, "_sig": f"attack:{near.id}"}

        if g is None:
            return None  # not in the plan: keep its last order
        task = g["task"]
        if task == "attack":
            target = by_id.get(g["target"])
            if target is None or not target.fighting_fit:
                # target lost: the nearest enemy to this unit
                if near is None:
                    return None
                target = near
            return {"cmd": "attack", "unit": u.id, "target": target.id, "run": g["run"] or dist(u, target) < 80,
                    "_sig": f"attack:{target.id}"}
        if task == "withdraw":
            return {"cmd": "withdraw", "unit": u.id, "run": True, "_sig": "withdraw"}
        slot = self.slots.get(u.id)
        if slot is None:
            return None
        x, y, facing = slot
        if math.hypot(u.x - x, u.y - y) < 8 and abs(((u.h - facing) + 180) % 360 - 180) < 20:
            return None  # in its slot: nothing to do
        return {"cmd": "move", "unit": u.id, "x": x, "y": y, "facing": facing, "width": u.w or None,
                "run": g["run"], "_sig": f"move:{round(x / 5)}:{round(y / 5)}:{round(facing / 10)}:{g['run']}"}
