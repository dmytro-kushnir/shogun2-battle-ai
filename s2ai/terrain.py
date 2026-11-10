"""Terrain learned from the units themselves.

The battle-script API has no ground-height query (the battle, unit and vector method lists dumped in probe run 6 have
none), but every unit's altitude comes in twice a second. Each sample says "the ground here is this high", so the
height map grows wherever units have stood or walked. Unknown elsewhere - the report says so.

Samples that are not ground are left out: garrisoned units, and anything within FORT_CLEAR_M of a wall, tower,
gate or keep (units manning a wall stand on top of it, run 24: 7 m above the ground at its foot).
"""
from __future__ import annotations

import math

from .report import building_kind

CELL_M = 25.0          # resolution of the learned map
FORT_CLEAR_M = 20.0    # ignore samples this close to fortifications
REPORT_MAX_CELLS = 12  # the report's table is at most this many columns / rows
FLAT_M = 3.0           # less relief than this over the known ground: just say it is flat


def fort_points(buildings: dict[int, dict] | None) -> list[tuple[float, float]]:
    return [(b["x"], b["y"]) for b in (buildings or {}).values()
            if b.get("x") is not None and building_kind(b.get("name")) in ("wall", "tower", "gate", "keep")]


class HeightMap:
    def __init__(self):
        self.sum: dict[tuple[int, int], float] = {}
        self.n: dict[tuple[int, int], int] = {}
        self.blocked: set[tuple[int, int]] = set()
        self._forts_seen = 0

    @staticmethod
    def cell(x: float, y: float) -> tuple[int, int]:
        return math.floor(x / CELL_M), math.floor(y / CELL_M)

    def set_forts(self, points: list[tuple[float, float]]) -> None:
        """Cells near fortification pieces (x, y): unit altitudes there may be wall tops."""
        if len(points) == self._forts_seen:
            return
        self._forts_seen = len(points)
        r = math.ceil(FORT_CLEAR_M / CELL_M)
        for x, y in points:
            ci, cj = self.cell(x, y)
            for i in range(ci - r, ci + r + 1):
                for j in range(cj - r, cj + r + 1):
                    # the cell's nearest point to the piece
                    nx = min(max(x, i * CELL_M), (i + 1) * CELL_M)
                    ny = min(max(y, j * CELL_M), (j + 1) * CELL_M)
                    if math.hypot(nx - x, ny - y) <= FORT_CLEAR_M:
                        self.blocked.add((i, j))
        for c in self.blocked:
            self.sum.pop(c, None)
            self.n.pop(c, None)

    def add(self, x: float | None, y: float | None, z: float | None, garrisoned: bool = False) -> None:
        if x is None or y is None or z is None or garrisoned:
            return
        c = self.cell(x, y)
        if c in self.blocked:
            return
        self.sum[c] = self.sum.get(c, 0.0) + z
        self.n[c] = self.n.get(c, 0) + 1

    def add_state(self, state: dict) -> None:
        for u in state.get("units", []):
            if not u.get("gone"):
                self.add(u.get("x"), u.get("y"), u.get("z"), bool(u.get("gar")))

    def height(self, c: tuple[int, int]) -> float | None:
        n = self.n.get(c)
        return self.sum[c] / n if n else None

    def __len__(self) -> int:
        return len(self.n)

    def cells(self) -> list[list[float]]:
        """[[x0, y0, size, height], ...] for the map."""
        return [[i * CELL_M, j * CELL_M, CELL_M, round(self.sum[(i, j)] / n, 1)] for (i, j), n in self.n.items()]

    # ------------------------------------------------------------ report

    def section(self, extent: tuple[float, float, float, float] | None = None) -> list[str]:
        """Lines for the battle report: a coarse height table over `extent` (x0, x1, y0, y1; default: known
        ground), plus the highest and lowest known ground."""
        if len(self) < 4:
            return []
        known = {c: self.height(c) for c in self.n}
        zs = list(known.values())
        lo_c, hi_c = min(known, key=known.get), max(known, key=known.get)
        mid = lambda c: ((c[0] + 0.5) * CELL_M, (c[1] + 0.5) * CELL_M)
        head = ("", "TERRAIN (ground height in m, learned from where units have stood - compare with the units' alt; "
                "'.' = no unit has been there yet):")
        if max(zs) - min(zs) < FLAT_M:
            return ["", f"TERRAIN: flat so far - all ground units have stood on is {min(zs):.0f}-{max(zs):.0f} m high."]
        if extent is None:
            xs = [mid(c)[0] for c in known]
            ys = [mid(c)[1] for c in known]
            extent = (min(xs), max(xs), min(ys), max(ys))
        x0, x1, y0, y1 = extent
        step = CELL_M * max(1, math.ceil(max(x1 - x0, y1 - y0) / CELL_M / REPORT_MAX_CELLS))
        gx0, gy0 = math.floor(x0 / step) * step, math.floor(y0 / step) * step
        cols = max(1, math.ceil((x1 - gx0) / step + 1e-9))
        rows = max(1, math.ceil((y1 - gy0) / step + 1e-9))
        # coarse cell -> mean of the known fine cells inside it
        acc: dict[tuple[int, int], list[float]] = {}
        for c, z in known.items():
            mx, my = mid(c)
            k = (math.floor((mx - gx0) / step), math.floor((my - gy0) / step))
            if 0 <= k[0] < cols and 0 <= k[1] < rows:
                acc.setdefault(k, []).append(z)
        out = list(head)
        out.append(f"  cells {step:.0f} m; columns = x of the cell centre, rows = y (north at the top)")
        out.append("  " + " " * 7 + "".join(f"{gx0 + (i + 0.5) * step:>6.0f}" for i in range(cols)))
        for j in reversed(range(rows)):
            row = "".join(f"{sum(acc[(i, j)]) / len(acc[(i, j)]):>6.0f}" if (i, j) in acc else f"{'.':>6}"
                          for i in range(cols))
            if row.strip(" ."):
                out.append(f"  y{gy0 + (j + 0.5) * step:>6.0f}" + row)
        hx, hy = mid(hi_c)
        lx, ly = mid(lo_c)
        out.append(f"  Highest known ground {known[hi_c]:.0f} m at ({hx:.0f},{hy:.0f}); lowest {known[lo_c]:.0f} m "
                   f"at ({lx:.0f},{ly:.0f}).")
        return out
