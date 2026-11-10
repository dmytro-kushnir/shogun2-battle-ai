"""Estimated morale risk per unit.

The battle-script API cannot read morale or fatigue (no such method on units, probe run 6), but most of what breaks
a unit in Shogun 2 can be seen from outside: heavy and fast losses, enemies in its flank or rear, being outnumbered
where it fights, a dead / routing / distant general, and friends routing next to it. Each cause adds points; the
total gives LOW / MEDIUM / HIGH. Shown for both sides: the general's own wavering units and the enemy's shaky ones.
"""
from __future__ import annotations

import math
from collections import deque

from .report import U, bearing, dist

WINDOW_S = 20.0         # "losing men fast" looks back this far
CONTACT_M = 35.0        # an enemy this close is fighting the unit (centres; units are 20-60 m wide)
LOCAL_M = 60.0          # who counts for "outnumbered here"
GENERAL_RANGE_M = 150.0  # roughly where the general's presence still helps
ROUT_NEAR_M = 80.0


class MoraleTracker:
    def __init__(self):
        self.men: dict[int, deque] = {}   # id -> (t, men) samples over the last WINDOW_S

    def add_state(self, state: dict) -> None:
        t = state.get("t") or 0.0
        for u in state.get("units", []):
            if u.get("men") is None:
                continue
            q = self.men.setdefault(u["id"], deque())
            q.append((t, u["men"]))
            while q and q[0][0] < t - WINDOW_S:
                q.popleft()

    def lost_recently(self, uid: int) -> int:
        q = self.men.get(uid)
        return max(0, q[0][1] - q[-1][1]) if q and len(q) > 1 else 0

    def assess(self, units: list[U]) -> dict[int, tuple[str, list[str]]]:
        """id -> (LOW/MEDIUM/HIGH, reasons) for every fighting-fit unit."""
        fit = [u for u in units if u.fighting_fit]
        out = {}
        for u in fit:
            score, why = 0, []
            friends = [f for f in fit if f.alliance == u.alliance]
            enemies = [e for e in fit if e.alliance != u.alliance]
            # losses: overall, and how fast right now
            if u.men0:
                lost = 1 - u.men / u.men0
                if lost >= 0.5:
                    score, why = score + 2, why + [f"lost {lost:.0%}"]
                elif lost >= 0.3:
                    score, why = score + 1, why + [f"lost {lost:.0%}"]
                fast = self.lost_recently(u.id) / u.men0
                if fast >= 0.10:
                    score, why = score + 2, why + [f"losing men fast ({fast:.0%} in {WINDOW_S:.0f}s)"]
                elif fast >= 0.05:
                    score, why = score + 1, why + ["taking losses"]
            # attacked from the side or behind
            worst = 0
            for e in enemies:
                if dist(u, e) < CONTACT_M:
                    off = abs((bearing(u.x, u.y, e.x, e.y) - u.h + 180) % 360 - 180)
                    worst = max(worst, 3 if off > 120 else 2 if off > 60 else 0)
            if worst == 3:
                score, why = score + 3, why + ["enemy in its REAR"]
            elif worst == 2:
                score, why = score + 2, why + ["enemy on its flank"]
            # outnumbered where it stands
            e_men = sum(e.men for e in enemies if dist(u, e) < LOCAL_M)
            if e_men:
                f_men = sum(f.men for f in friends if dist(u, f) < LOCAL_M)
                ratio = e_men / max(1, f_men)
                if ratio >= 2:
                    score, why = score + 2, why + [f"outnumbered {ratio:.1f}:1 here"]
                elif ratio >= 1.5:
                    score, why = score + 1, why + [f"outnumbered {ratio:.1f}:1 here"]
            # the general
            if not u.general:
                gens = [g for g in units if g.alliance == u.alliance and g.general]
                alive = [g for g in gens if g.fighting_fit]
                if gens and not alive:
                    score, why = score + 2, why + ["general dead or routed"]
                elif alive and min(dist(u, g) for g in alive) > GENERAL_RANGE_M:
                    score, why = score + 1, why + ["general far away"]
            # friends breaking next to it
            routing = sum(1 for f in units if f.alliance == u.alliance and f.alive and f.routing
                          and dist(u, f) < ROUT_NEAR_M)
            if routing:
                score, why = score + min(2, routing), why + [f"{routing} friendly unit{'s' if routing > 1 else ''} "
                                                              "routing nearby"]
            level = "HIGH" if score >= 4 else "MEDIUM" if score >= 2 else "LOW"
            out[u.id] = (level, why)
        return out


def tag(assessment: dict[int, tuple[str, list[str]]], uid: int) -> str:
    """' | morale risk HIGH (reasons)' for MEDIUM and HIGH; nothing for LOW (keeps the report short)."""
    level, why = assessment.get(uid, ("LOW", []))
    if level == "LOW":
        return ""
    return f" | morale risk {level} ({', '.join(why)})"
