"""Paper figures from recorded battles (runtime/battle-<id>.jsonl, plans-<id>.jsonl, meta-<id>.json).

    .venv\\Scripts\\python research\\figures.py snapshot 20261110-065551 --at 120
    .venv\\Scripts\\python research\\figures.py timeline 20261110-065551
    .venv\\Scripts\\python research\\figures.py all 20261110-065551 --at 60 120 200

Writes SVG and PNG into research/figures/ (gitignored until the figures are final). Everything drawn comes from
the recording: unit positions, facing, frontage and men, the fortifications the bridge listed, and the general's
plans. Nothing is read from the game's files. Needs matplotlib (research/requirements.txt).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Polygon  # noqa: E402

from s2ai import report  # noqa: E402
from s2ai.link import GameLink  # noqa: E402

RUNTIME = ROOT / "runtime"
OUT = Path(__file__).resolve().parent / "figures"

AI, PLAYER = "#c0392b", "#2e5fa8"          # the commanded side, the opponent
FORT = {"wall": "#6b4f2a", "gate": "#d68910", "tower": "#7d3c98", "keep": "#1e8449"}
GROUND = "#efe9dc"


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def replay(bid: str):
    """Yield the battle (link.Battle) after every state message, as the server saw it live."""
    link = GameLink(runtime=RUNTIME)
    for msg in _jsonl(RUNTIME / f"battle-{bid}.jsonl"):
        link._absorb(bid, msg)  # noqa: SLF001 - the same parser the server uses
        if msg.get("type") == "state" and link.battle is not None:
            yield link.battle


def meta(bid: str) -> dict:
    try:
        return json.loads((RUNTIME / f"meta-{bid}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def ai_alliance(bid: str, battle) -> int:
    m = meta(bid)
    if m.get("ai"):
        return m["ai"]
    from s2ai.server import guess_ai_alliance
    return guess_ai_alliance(battle.start)


def at_time(bid: str, t: float):
    """The battle as it stood at battle time t (the last state at or before it)."""
    last = None
    for b in replay(bid):
        if b.state["t"] > t and last is not None:
            break
        last = b
        snap = (dict(b.state), {k: dict(v) for k, v in (b.buildings or {}).items()}, b.static)
    if last is None:
        raise SystemExit(f"no states in battle {bid}")
    return snap


def plan_at(bid: str, t: float) -> dict | None:
    plans = [p for p in _jsonl(RUNTIME / f"plans-{bid}.jsonl") if "plan" in p and p.get("t", 0) <= t]
    return plans[-1] if plans else None


def unit_shape(u) -> list[tuple[float, float]]:
    """The unit's block: frontage w across its facing, depth from its men (about 1.5 m per man per rank)."""
    w = max(u.w or 0, 12.0)
    depth = min(max(u.men * 1.6 / w * 1.5, 6.0), 40.0) if u.kind != "ship" else 14.0
    h = math.radians(u.h or 0)
    fx, fy = math.sin(h), math.cos(h)          # facing (bearing 0 = north = +y)
    rx, ry = fy, -fx                            # to the unit's right
    pts = []
    for a, b in ((-w / 2, depth / 2), (w / 2, depth / 2), (w / 2, -depth / 2), (-w / 2, -depth / 2)):
        pts.append((u.x + rx * a + fx * b, u.y + ry * a + fy * b))
    return pts


def draw_snapshot(bid: str, t: float, ax=None, title: bool = True):
    state, buildings, static = at_time(bid, t)

    class _B:  # what ai_alliance needs
        start = {"units": list(static.values()), "armies": []}
    ai = ai_alliance(bid, _B)
    units = [u for u in report.merge(static, state) if u.alive]
    own_ax = ax is None
    if own_ax:
        fig, ax = plt.subplots(figsize=(8, 8))
    ax.set_facecolor(GROUND)

    # fortifications (the bridge lists the buildings that can hold troops, with owner and damage)
    forts = [b for b in buildings.values() if b.get("mil") and b.get("x") is not None]
    for b in forts:
        kind = report.building_kind(b.get("name"))
        if kind not in FORT:
            continue
        own = b.get("owner")
        edge = AI if own == ai else PLAYER if own else "#555555"
        size = {"gate": 70, "tower": 55, "keep": 120, "wall": 28}[kind]
        marker = {"gate": "o", "tower": "s", "keep": "*", "wall": "s"}[kind]
        alpha = 0.35 + 0.65 * (b.get("hp") if b.get("hp") is not None else 1.0)
        ax.scatter([b["x"]], [b["y"]], s=size, marker=marker, color=FORT[kind], edgecolors=edge, linewidths=1.2,
                   alpha=alpha, zorder=2)

    # the general's current plan: arrows to targets and positions, lines to buildings
    p = plan_at(bid, state["t"])
    by_id = {u.id: u for u in units}
    goals: list[tuple[float, float]] = []
    if p:
        for g in p["plan"].get("groups", []):
            mine = [by_id[i] for i in g.get("units", []) if i in by_id]
            if not mine:
                continue
            cx, cy = sum(u.x for u in mine) / len(mine), sum(u.y for u in mine) / len(mine)
            goal = None
            if g.get("task") == "attack" and g.get("target") in by_id:
                goal = by_id[g["target"]].x, by_id[g["target"]].y
            elif g.get("position"):
                goal = tuple(g["position"])
            elif g.get("building") in buildings:
                b = buildings[g["building"]]
                goal = b.get("x"), b.get("y")
            if goal and goal[0] is not None:
                pts = [(cx, cy)] + [tuple(v) for v in (g.get("via") or [])] + [goal]
                goals.extend(pts[1:])
                for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                    ax.annotate("", xy=(x1, y1), xytext=(x0, y0), zorder=3,
                                arrowprops=dict(arrowstyle="-|>", color=AI, lw=1.3, alpha=0.75,
                                                linestyle="--" if g.get("task") != "attack" else "-"))
            ax.text(cx, cy - 18, g.get("name", ""), fontsize=7, color=AI, ha="center", va="top", zorder=5)

    for u in units:
        mine = u.alliance == ai
        col = AI if mine else PLAYER
        fade = u.routing or u.shattered
        ax.add_patch(Polygon(unit_shape(u), closed=True, facecolor=col, edgecolor="black" if u.general else col,
                             lw=1.6 if u.general else 0.5, alpha=0.3 if fade else 0.85, zorder=4))
        h = math.radians(u.h or 0)  # a facing tick
        ax.plot([u.x, u.x + math.sin(h) * 14], [u.y, u.y + math.cos(h) * 14], color="black", lw=0.6, zorder=5)

    xs = [u.x for u in units] + [b["x"] for b in forts] + [g[0] for g in goals]
    ys = [u.y for u in units] + [b["y"] for b in forts] + [g[1] for g in goals]
    pad = 60
    lo_x, hi_x, lo_y, hi_y = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    side = max(hi_x - lo_x, hi_y - lo_y)
    mx, my = (lo_x + hi_x) / 2, (lo_y + hi_y) / 2
    ax.set_xlim(mx - side / 2, mx + side / 2)
    ax.set_ylim(my - side / 2, my + side / 2)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    # scale bar and north
    bar = 10 ** math.floor(math.log10(side / 4))
    bar = bar * 5 if side / bar > 20 else bar * 2 if side / bar > 8 else bar
    x0, y0 = mx - side / 2 + side * 0.04, my - side / 2 + side * 0.04
    ax.plot([x0, x0 + bar], [y0, y0], color="black", lw=2)
    ax.text(x0 + bar / 2, y0 + side * 0.012, f"{bar:.0f} m", ha="center", fontsize=8)
    ax.text(mx + side / 2 - side * 0.04, my + side / 2 - side * 0.05, "N ↑", ha="right", fontsize=10,
            fontweight="bold")

    legend = [Line2D([], [], marker="s", ls="", color=AI, label="AI general (commanded side)"),
              Line2D([], [], marker="s", ls="", color=PLAYER, label="opponent"),
              Line2D([], [], marker="s", ls="", color=AI, alpha=0.3, label="routing")]
    kinds = {report.building_kind(b.get("name")) for b in forts}
    for k in ("gate", "tower", "wall", "keep"):
        if k in kinds:
            legend.append(Line2D([], [], marker={"gate": "o", "tower": "s", "keep": "*", "wall": "s"}[k], ls="",
                                 color=FORT[k], label=k))
    if p:
        legend.append(Line2D([], [], color=AI, lw=1.3, label="plan: attack (solid), move/hold (dashed)"))
    ax.legend(handles=legend, loc="upper left", fontsize=7, framealpha=0.9)
    if title:
        intent = p["plan"].get("intent", "") if p else ""
        ax.set_title(f"battle {bid}, t = {state['t']:.0f} s" + (f"\nplan: {intent}" if intent else ""),
                     fontsize=9, loc="left")
    if own_ax:
        return fig
    return ax


def draw_timeline(bid: str):
    """Men still fighting per side over time, with the moments new plans arrived."""
    ts, mine, theirs, ai = [], [], [], None
    for b in replay(bid):
        if ai is None:
            ai = ai_alliance(bid, b)
        units = report.merge(b.static, b.state)
        ts.append(b.state["t"])
        mine.append(sum(u.men for u in units if u.alliance == ai and u.fighting_fit))
        theirs.append(sum(u.men for u in units if u.alliance != ai and u.fighting_fit))
    if not ts:
        raise SystemExit(f"no states in battle {bid}")
    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.plot(ts, mine, color=AI, lw=1.8, label=f"AI side ({meta(bid).get('general', 'AI')})")
    ax.plot(ts, theirs, color=PLAYER, lw=1.8, label=f"opponent ({meta(bid).get('opponent', 'human')})")
    for p in _jsonl(RUNTIME / f"plans-{bid}.jsonl"):
        if "plan" in p:
            ax.axvline(p["t"], color="#999999", lw=0.5, alpha=0.6, zorder=0)
    ax.set_xlabel("battle time (s)")
    ax.set_ylabel("men still fighting")
    ax.set_xlim(0, ts[-1])
    ax.set_ylim(0, None)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=8, frameon=False)
    ax.set_title(f"battle {bid}: strength over time (grey lines: new plans)", fontsize=9, loc="left")
    fig.tight_layout()
    return fig


def save(fig, name: str) -> None:
    OUT.mkdir(exist_ok=True)
    for ext in ("svg", "png"):
        fig.savefig(OUT / f"{name}.{ext}", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(OUT / f"{name}.svg")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["snapshot", "timeline", "all"])
    ap.add_argument("battle", help="battle id, e.g. 20261110-065551")
    ap.add_argument("--at", type=float, nargs="+", default=[60.0], help="battle time(s) for snapshots, seconds")
    args = ap.parse_args()
    if args.what in ("snapshot", "all"):
        for t in args.at:
            save(draw_snapshot(args.battle, t), f"{args.battle}-t{t:.0f}")
    if args.what in ("timeline", "all"):
        save(draw_timeline(args.battle), f"{args.battle}-timeline")


if __name__ == "__main__":
    main()
