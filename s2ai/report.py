"""Battle report for the LLM general: a compact text picture of the battle from the AI side's point of view.

Geometry: x east, y north (metres); headings are compass degrees, 0 = north, clockwise - the same as the game's
unit:bearing() (checked against the deployment at Sekigahara: each army faced the other within ~10 degrees).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

CONTACT_M = 25.0  # an enemy this close is probably in melee with the unit


@dataclass
class U:
    """One unit with its static info merged in."""
    id: int
    alliance: int
    type: str
    kind: str          # "cav", "inf", "art"
    missile: bool
    general: bool
    men: int
    men0: int
    x: float
    y: float
    h: float
    w: float
    moving: bool
    running: bool
    routing: bool
    shattered: bool
    gone: bool
    ammo: float | None
    range: float
    z: float = 0.0          # altitude, metres
    garrisoned: bool = False

    @property
    def alive(self) -> bool:
        return self.men > 0 and not self.gone

    @property
    def fighting_fit(self) -> bool:
        return self.alive and not self.routing and not self.shattered

    @property
    def label(self) -> str:
        return pretty_type(self.type)


def pretty_type(t: str) -> str:
    t = re.sub(r"^(Genpei|Boshin|Sho|Mon|FotS|Shogun|Rok)_", "", t or "?")
    return t.replace("_", " ")


def merge(static: dict[int, dict], state: dict) -> list[U]:
    units = []
    for s in state["units"]:
        st = static.get(s["id"])
        if st is None or s.get("x") is None:
            continue
        t = st.get("type") or "?"
        kind = "art" if st.get("art") else "cav" if st.get("cav") else "inf"
        units.append(U(
            id=s["id"], alliance=st["alliance"], type=t, kind=kind,
            missile=(st.get("range") or 0) > 0 and (st.get("ammo0") or 0) > 0,
            general=bool(re.search(r"(^|_)Gen(_|$)|General|Bodyguard|Daimyo", t)),
            men=s.get("men") or 0, men0=st.get("men0") or 0,
            x=s["x"], y=s["y"], h=s.get("h") or 0.0, w=s.get("w") or 0.0,
            moving=bool(s.get("mov")), running=bool(s.get("run")),
            routing=bool(s.get("rout")), shattered=bool(s.get("shat")), gone=bool(s.get("gone")),
            ammo=s.get("ammo"), range=st.get("range") or 0.0,
            z=s.get("z") or 0.0, garrisoned=bool(s.get("gar")),
        ))
    return units


# ---------------------------------------------------------------- fortifications

def building_kind(name: str) -> str:
    n = (name or "").lower()
    if "gate" in n:
        return "gate"
    if "keep" in n or "honmaru" in n:
        return "keep"
    if "corner" in n or "tower" in n or "turret" in n or "yagura" in n:
        return "tower"
    if "wall" in n or "palisade" in n or "stockade" in n:
        return "wall"
    return "building"


def buildings_from_message(msg: dict | None) -> dict[int, dict] | None:
    """The bridge's 'buildings' message -> {id: building}. Also reads the discovery format of the first siege
    recording (data.items with alliance_owner_id 0-based, capacity, health, position {x, y, z})."""
    if not msg:
        return None
    if "buildings" in msg:
        return {b["id"]: b for b in msg["buildings"]}
    data = msg.get("data")
    if not isinstance(data, dict):
        return None
    out = {}
    for it in data.get("items", []):
        p, owner = it.get("position") or {}, it.get("alliance_owner_id", -1)
        name, cap = it.get("name", ""), it.get("capacity") or 0
        out[it["i"]] = {"id": it["i"], "name": name, "x": p.get("x"), "y": p.get("y"), "z": p.get("z"), "cap": cap,
                        "owner": owner + 1 if owner >= 0 else 0, "hp": it.get("health"),
                        "mil": cap > 0 or owner >= 0 or building_kind(name) != "building"}
    return out


def fortifications(buildings: dict[int, dict] | None) -> list[dict]:
    """Military buildings with a kind; houses and scenery left out."""
    if not buildings:
        return []
    out = []
    for b in buildings.values():
        if not b.get("mil") or b.get("x") is None:
            continue
        kind = building_kind(b.get("name"))
        if kind == "building" and not b.get("cap"):
            continue
        out.append({**b, "kind": kind})
    return out


def fort_section(forts: list[dict], ai: int) -> list[str]:
    if not forts:
        return []
    side = lambda o: "yours" if o == ai else "nobody's" if not o else "ENEMY"
    xs, ys, zs = [f["x"] for f in forts], [f["y"] for f in forts], [f.get("z") or 0 for f in forts]
    out = ["", "FORTIFICATIONS (siege). Units on the walls / at the castle level stand at about this altitude: "
           f"{sum(zs) / len(zs):.0f} m. Extent x {min(xs):.0f}..{max(xs):.0f}, y {min(ys):.0f}..{max(ys):.0f}."]
    out.append("  Use the building id with the tasks garrison / climb / attack_building.")
    order = {"gate": 0, "keep": 1, "tower": 2, "wall": 3, "building": 4}
    for f in sorted(forts, key=lambda f: (order[f["kind"]], f["id"])):
        hp = f.get("hp")
        dmg = f" health {hp:.0%}" if isinstance(hp, (int, float)) and hp < 0.999 else ""
        gar = " GARRISONED" if f.get("gar") else ""
        out.append(f"  b{f['id']:<4} {f['kind']:<6} at ({f['x']:5.0f},{f['y']:5.0f}) alt {f.get('z') or 0:4.0f} "
                   f"holds {f.get('cap') or 0:>3} men, {side(f.get('owner'))}{dmg}{gar}")
    return out


def dist(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def bearing(x0, y0, x1, y1) -> float:
    return math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360


def compass(deg: float) -> str:
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int(((deg % 360) + 22.5) // 45) % 8]


def centre(units: list[U]) -> tuple[float, float]:
    if not units:
        return 0.0, 0.0
    return sum(u.x for u in units) / len(units), sum(u.y for u in units) / len(units)


def state_word(u: U, nearest_enemy_m: float | None) -> str:
    if u.shattered:
        return "SHATTERED"
    if u.routing:
        return "ROUTING"
    if u.gone:
        return "leaving"
    if nearest_enemy_m is not None and nearest_enemy_m < CONTACT_M:
        return "in contact"
    if u.running:
        return "running"
    if u.moving:
        return "moving"
    return "standing"


def unit_line(u: U, enemies: list[U], show_nearest: bool) -> str:
    near = min(enemies, key=lambda e: dist(u, e)) if enemies else None
    d = dist(u, near) if near else None
    kind = u.kind + (" missile" if u.missile else "") + (" GENERAL" if u.general else "")
    ammo = ""
    if u.missile:
        ammo = f" ammo {u.ammo}" if u.ammo is not None else ""
        ammo += f" range {u.range:.0f}m"
    line = (f"  {u.id:>5} {u.label[:34]:<34} {kind:<18} {u.men:>4}/{u.men0:<4} at ({u.x:6.0f},{u.y:6.0f}) "
            f"alt {u.z:4.0f} facing {u.h:3.0f} {state_word(u, d)}{' GARRISONED' if u.garrisoned else ''}{ammo}")
    if show_nearest and near is not None:
        line += f" | nearest enemy {near.id} {d:.0f}m {compass(bearing(u.x, u.y, near.x, near.y))}"
    return line


def build(static: dict[int, dict], state: dict, ai: int, events: list[dict], plan: dict | None,
          plan_age_s: float | None, buildings: dict[int, dict] | None = None, terrain=None) -> tuple[str, list[U]]:
    units = merge(static, state)
    ours = [u for u in units if u.alliance == ai and u.alive]
    theirs = [u for u in units if u.alliance != ai and u.alive]
    fit_theirs = [e for e in theirs if e.fighting_fit]
    out = []
    t = state.get("t", 0)
    out.append(f"BATTLE TIME {t:.0f}s, phase {state.get('phase')}.")
    ox, oy = centre([u for u in ours if u.fighting_fit] or ours)
    ex, ey = centre(fit_theirs or theirs)
    men_o = sum(u.men for u in ours if u.fighting_fit)
    men_e = sum(u.men for u in fit_theirs)
    out.append(f"Your army: {sum(1 for u in ours if u.fighting_fit)} fighting units, {men_o} men, centre ({ox:.0f},{oy:.0f}).")
    out.append(f"Enemy: {len(fit_theirs)} fighting units, {men_e} men, centre ({ex:.0f},{ey:.0f}), "
               f"{math.hypot(ex - ox, ey - oy):.0f} m to the {compass(bearing(ox, oy, ex, ey))} "
               f"(bearing {bearing(ox, oy, ex, ey):.0f}).")
    out.extend(fort_section(fortifications(buildings), ai))
    if terrain is not None:
        out.extend(terrain.section())
    out.append("")
    out.append("YOUR UNITS (id, type, kind, men, position, altitude, facing, state):")
    for u in sorted(ours, key=lambda u: (u.kind, u.id)):
        out.append(unit_line(u, fit_theirs, show_nearest=True))
    out.append("")
    out.append("ENEMY UNITS (closest to your army first):")
    for e in sorted(theirs, key=lambda e: math.hypot(e.x - ox, e.y - oy)):
        out.append(unit_line(e, [u for u in ours if u.fighting_fit], show_nearest=False))
    gone = [u for u in units if not u.alive or u.shattered]
    present = {u.id for u in units}
    destroyed = [sid for sid in static if sid not in present]  # no longer on the field at all
    if gone or destroyed:
        out.append("")
        out.append("OUT OF THE FIGHT (do not give them orders): " + ", ".join(
            [f"{u.id} {'yours' if u.alliance == ai else 'enemy'}" for u in gone]
            + [f"{sid} {'yours' if static[sid]['alliance'] == ai else 'enemy'} (destroyed)" for sid in destroyed]))
    if events:
        out.append("")
        out.append("RECENT EVENTS: " + "; ".join(f"t={e.get('tick', 0) * 0.5:.0f}s {e.get('name')}" for e in events[-8:]))
    if plan:
        out.append("")
        out.append(f"YOUR CURRENT PLAN (given {plan_age_s or 0:.0f}s ago): {plan['intent']}")
        for g in plan["groups"]:
            what = {"attack": f"attack {g['target']}", "move": f"move to {g['position']}",
                    "hold": f"hold at {g['position'] or 'current place'}", "withdraw": "withdraw",
                    "garrison": f"garrison b{g.get('building')}", "climb": f"climb b{g.get('building')}",
                    "attack_building": f"attack building b{g.get('building')}",
                    "occupy": f"occupy {g['position']}"}.get(g["task"], g["task"])
            out.append(f"  - {g['name']}: {g['units']} {what}")
    return "\n".join(out), units
