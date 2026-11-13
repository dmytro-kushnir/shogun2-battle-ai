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
    kind: str          # "cav", "inf", "art", "ship"
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
    ability: str = ""       # special ability active right now ("" = none)
    hull: float | None = None  # ships: hull damage, 0 = intact .. 1
    sail: float | None = None  # ships: sail damage

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


# Ships drop out of the battle (sunk, burnt or blown up) at a reported hull damage of 10-50%, most around
# 20-25% (naval battles 175931 and 190434, 17 ships lost), with most of the crew alive: the number is not a
# fraction of what a ship can take.
SHIP_HULL_DANGER = 0.15

# The general held every unit for the first 260 s of field battle 20261113-191107 and lost; players find an
# idle opponent dull. After this long without a plan that sends a real share of the army forward the report
# says so.
IDLE_WARN_S = 75.0


def merge(static: dict[int, dict], state: dict) -> list[U]:
    units = []
    for s in state["units"]:
        st = static.get(s["id"])
        if st is None or s.get("x") is None:
            continue
        t = st.get("type") or "?"
        kind = "ship" if st.get("ship") else "art" if st.get("art") else "cav" if st.get("cav") else "inf"
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
            ability="" if str(s.get("ab") or "none").lower() in ("none", "") else str(s.get("ab")),
            hull=s.get("hull"), sail=s.get("sail"),
        ))
    return units


# ---------------------------------------------------------------- fortifications

def building_kind(name: str) -> str:
    n = (name or "").lower()
    if "coastal_batter" in n:
        return "battery"
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
    order = {"gate": 0, "keep": 1, "tower": 2, "wall": 3, "battery": 4, "building": 5}
    for f in sorted(forts, key=lambda f: (order[f["kind"]], f["id"])):
        hp = f.get("hp")
        dmg = f" health {hp:.0%}" if isinstance(hp, (int, float)) and hp < 0.999 else ""
        gar = " GARRISONED" if f.get("gar") else ""
        out.append(f"  b{f['id']:<4} {f['kind']:<6} at ({f['x']:5.0f},{f['y']:5.0f}) alt {f.get('z') or 0:4.0f} "
                   f"holds {f.get('cap') or 0:>3} men, {side(f.get('owner'))}{dmg}{gar}")
    return out


LEVEL_GAP_M = 4.0       # fort pieces this much higher than the last start a new level (ring) of the castle
CLIMB_SPREAD_M = 60.0   # climb points offered this far apart, so an assault splits the defence
CLIMB_POINTS = 5        # climb points offered per level


def castle_levels(forts: list[dict]) -> list[list[dict]]:
    """Castle pieces grouped by altitude, lowest (outer) ring first. A hill castle has several rings (siege
    20261113-193825: walls at 160, 167 and 174 m)."""
    levels: list[list[dict]] = []
    for f in sorted(forts, key=lambda f: f.get("z") or 0):
        if levels and (f.get("z") or 0) - (levels[-1][-1].get("z") or 0) < LEVEL_GAP_M:
            levels[-1].append(f)
        else:
            levels.append([f])
    return levels


def spread(pieces: list[dict], key, n: int, gap: float) -> list[dict]:
    """Up to n pieces, best first by key, each at least gap metres from those already taken."""
    out: list[dict] = []
    for f in sorted(pieces, key=key):
        if all(math.hypot(f["x"] - o["x"], f["y"] - o["y"]) >= gap for o in out):
            out.append(f)
            if len(out) == n:
                break
    return out


def assault_section(forts: list[dict], ai: int, ours: list["U"], theirs: list["U"] | None = None) -> list[str]:
    """Sieges where we attack: the ways in, not every wall piece (battle 20261113-192734 listed 94 pieces and the
    general never climbed). Climb points are spread out and given per level: in siege 20261113-193825 the ten
    nearest sections were all one corner, and five units climbed there together."""
    if not forts:
        return []
    ox, oy = centre([u for u in ours if u.fighting_fit] or ours)
    near = lambda f: math.hypot(f["x"] - ox, f["y"] - oy)
    broken = lambda f: isinstance(f.get("hp"), (int, float)) and f["hp"] < 0.05
    levels = castle_levels(forts)
    top = levels[-1]
    keep = [f for f in forts if f["kind"] == "keep"] or top
    cx, cy = sum(f["x"] for f in keep) / len(keep), sum(f["y"] for f in keep) / len(keep)
    xs, ys = [f["x"] for f in forts], [f["y"] for f in forts]
    out = ["", f"SIEGE - YOU ATTACK. Castle x {min(xs):.0f}..{max(xs):.0f}, y {min(ys):.0f}..{max(ys):.0f}; the "
               f"keep / top level (take it with task occupy) is about ({cx:.0f},{cy:.0f})."]
    shooters = [e for e in (theirs or []) if e.fighting_fit and e.missile and e.range]
    if shooters:
        far = max(e.range for e in shooters)
        out.append(f"  Defenders' missiles reach up to {far:.0f} m: keep cavalry and waiting units farther than that "
                   "from them (walls and towers add height and reach).")
    line = lambda f, extra="": (f"    b{f['id']:<4} {f['kind']:<5} at ({f['x']:5.0f},{f['y']:5.0f}) alt {f.get('z') or 0:3.0f}, "
                                f"{near(f):4.0f} m from your army{extra}{' GARRISONED' if f.get('gar') else ''}")
    gaps = sorted([f for f in forts if broken(f) and f["kind"] in ("gate", "wall")], key=near)
    if gaps:
        out.append("  OPEN (broken gates / walls - march or ride straight in):")
        out.extend(line(f, " BROKEN") for f in gaps[:8])
    if len(levels) > 1:
        out.append(f"  The castle has {len(levels)} levels (rings of walls), outer first: "
                   + ", ".join(f"alt {min(f.get('z') or 0 for f in lv):.0f}-{max(f.get('z') or 0 for f in lv):.0f}"
                               f" ({len(lv)} pieces)" for lv in levels)
                   + ". Over one ring, the next is another climb: keep climbing level by level to the top.")
    for i, lv in enumerate(levels):
        walls = [f for f in lv if f["kind"] == "wall" and not broken(f)]
        gates = [f for f in lv if f["kind"] == "gate" and not broken(f)]
        pts = spread(walls, near, CLIMB_POINTS, CLIMB_SPREAD_M)
        name = "outer level" if i == 0 else "top level" if i == len(levels) - 1 else f"level {i + 1}"
        if len(levels) == 1:
            name = "walls"
        if pts:
            out.append(f"  {name}: climb points at least {CLIMB_SPREAD_M:.0f} m apart (task climb; send units to "
                       "DIFFERENT points at the same time):")
            out.extend(line(f) for f in pts)
        if gates:
            out.append(f"    shut gates here (attack_building, slow): "
                       + ", ".join(f"b{f['id']} ({f['x']:.0f},{f['y']:.0f})" for f in sorted(gates, key=near)[:3]))
    return out


def role(u: "U") -> str:
    t = u.type.lower()
    if u.kind == "ship":
        return "ships"
    if u.general:
        return "generals"
    if u.kind == "art":
        return "guns"
    if u.kind == "cav":
        return "missile cavalry" if u.missile else "shock cavalry"
    if "ninja" in t:
        return "ninjas"
    if u.missile:
        return "missile infantry"
    if "spear" in t or "yari" in t or "pike" in t:
        return "spears"
    return "melee infantry"


def character(units: list["U"]) -> str:
    """The army by role, e.g. "8 missile infantry, 5 spears, 6 shock cavalry"."""
    n: dict[str, int] = {}
    for u in units:
        if u.fighting_fit:
            n[role(u)] = n.get(role(u), 0) + 1
    return ", ".join(f"{c} {r}" for r, c in sorted(n.items(), key=lambda kv: -kv[1])) or "none"


def batteries(buildings: dict[int, dict] | None, stats: dict[str, dict] | None) -> list[dict]:
    """Coastal batteries (shore guns that fire on ships) with their guns and range from the game's database
    (range None when it is not known)."""
    out = []
    for b in (buildings or {}).values():
        if b.get("x") is None or building_kind(b.get("name")) != "battery":
            continue
        st = (stats or {}).get(re.sub(r"_mp$", "", (b.get("name") or "").lower())) or {}
        out.append({**b, "range": st.get("range"), "guns": st.get("guns"), "damage": st.get("damage"),
                    "reload": st.get("reload")})
    return out


def battery_section(bats: list[dict], ai: int, ours: list[U]) -> list[str]:
    if not bats:
        return []
    out = ["", "COASTAL BATTERIES (guns on the shore; they fire on ships in range; you cannot order an attack on them):"]
    for b in sorted(bats, key=lambda b: (b.get("owner") == ai, b["id"])):
        owner = b.get("owner")
        side = "yours - they cover your ships" if owner == ai else "nobody's" if not owner else "ENEMY"
        rng = b.get("range")
        guns = f"{b['guns']} guns, " if b.get("guns") else ""
        what = (f"{guns}range {rng:.0f}m, damage {b.get('damage')}, reload {b.get('reload')}s" if rng
                else "range unknown (shore guns usually outrange ships)")
        hp = b.get("hp")
        dmg = f", health {hp:.0%}" if isinstance(hp, (int, float)) and hp < 0.999 else ""
        line = f"  b{b['id']:<4} at ({b['x']:5.0f},{b['y']:5.0f}) {side}: {what}{dmg}"
        if owner and owner != ai and rng:
            hit = [f"{u.id} ({math.hypot(u.x - b['x'], u.y - b['y']):.0f}m)" for u in ours
                   if u.alive and math.hypot(u.x - b["x"], u.y - b["y"]) <= rng]
            line += " | can hit your " + ", ".join(hit) if hit else ""
        out.append(line)
    return out


def battery_tag(u: U, bats: list[dict], ai: int) -> str:
    """Our ship's line: the enemy batteries that can hit it now."""
    hit = [f"b{b['id']} {math.hypot(u.x - b['x'], u.y - b['y']):.0f}m" for b in bats
           if b.get("owner") and b.get("owner") != ai and b.get("range")
           and math.hypot(u.x - b["x"], u.y - b["y"]) <= b["range"]]
    return " | IN RANGE OF ENEMY BATTERY " + ", ".join(hit) if hit else ""


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


def unit_line(u: U, enemies: list[U], show_nearest: bool, extra: str = "") -> str:
    near = min(enemies, key=lambda e: dist(u, e)) if enemies else None
    d = dist(u, near) if near else None
    kind = u.kind + (" missile" if u.missile else "") + (" GENERAL" if u.general else "")
    ammo = ""
    if u.missile:
        ammo = f" ammo {u.ammo}" if u.ammo is not None else ""
        ammo += f" range {u.range:.0f}m"
    if u.kind == "ship":  # ships: no altitude or ammunition count; hull and sails instead
        kind = "ship"
        ammo = f" guns {u.range:.0f}m" if u.range else ""
        if u.hull is not None:
            ammo += f" hull damage {u.hull:.0%}"
            if u.hull >= SHIP_HULL_DANGER:
                ammo += " (SINKING RISK)"
        if u.sail:
            ammo += f" sails damaged {u.sail:.0%}"
        return (f"  {u.id:>5} {u.label[:34]:<34} {kind:<6} crew {u.men:>4}/{u.men0:<4} at ({u.x:6.0f},{u.y:6.0f}) "
                f"heading {u.h:3.0f} {state_word(u, d)}{ammo}"
                + (f" | nearest enemy {near.id} {d:.0f}m {compass(bearing(u.x, u.y, near.x, near.y))}"
                   if show_nearest and near is not None else "") + extra)
    line = (f"  {u.id:>5} {u.label[:34]:<34} {kind:<18} {u.men:>4}/{u.men0:<4} at ({u.x:6.0f},{u.y:6.0f}) "
            f"alt {u.z:4.0f} facing {u.h:3.0f} {state_word(u, d)}{' GARRISONED' if u.garrisoned else ''}{ammo}")
    if show_nearest and near is not None:
        line += f" | nearest enemy {near.id} {d:.0f}m {compass(bearing(u.x, u.y, near.x, near.y))}"
    return line + extra


def shooters(u: U, enemies: list[U]) -> tuple[int, int]:
    """-> (enemy artillery, enemy small-arms units) that have ammunition and can reach u from where they stand."""
    art = arms = 0
    for e in enemies:
        if e.missile and e.fighting_fit and (e.ammo is None or e.ammo > 0) and dist(u, e) <= e.range:
            if e.kind == "art":
                art += 1
            else:
                arms += 1
    return art, arms


def fire_tag(u: U, enemies: list[U]) -> str:
    if u.kind == "ship":  # which enemy ships can hit it, and which it can hit
        them = [e for e in enemies if e.kind == "ship" and e.range and dist(u, e) <= e.range]
        mine = sorted((e for e in enemies if u.range and dist(u, e) <= u.range), key=lambda e: dist(u, e))
        out = f" | in range of enemy ships {', '.join(str(e.id) for e in them)}" if them else ""
        if mine:
            out += f" | can hit {', '.join(f'{e.id} ({dist(u, e):.0f}m)' for e in mine[:5])}"
        return out
    art, arms = shooters(u, enemies)
    parts = ([f"{arms} missile units"] if arms else []) + ([f"{art} artillery"] if art else [])
    return f" | in range of enemy {', '.join(parts)}" if parts else ""


def formation_section(theirs: list[U], ours: list[U]) -> list[str]:
    """The enemy's array: which way it faces, its front line and flank ends, what stands behind it, and routes
    around each flank that stay out of reach of its small arms (for `via`)."""
    fit = [e for e in theirs if e.fighting_fit and not e.garrisoned]
    ours = [u for u in ours if u.fighting_fit]
    if len(fit) < 3 or not ours:
        return []
    ex, ey = centre(fit)
    ox, oy = centre(ours)
    sx = sum(math.sin(math.radians(e.h)) for e in fit)
    sy = sum(math.cos(math.radians(e.h)) for e in fit)
    face = math.degrees(math.atan2(sx, sy)) % 360 if math.hypot(sx, sy) / len(fit) > 0.6 \
        else bearing(ex, ey, ox, oy)
    fx, fy = math.sin(math.radians(face)), math.cos(math.radians(face))
    rx, ry = math.sin(math.radians(face + 90)), math.cos(math.radians(face + 90))
    depth = lambda x, y: (x - ex) * fx + (y - ey) * fy
    lat = lambda x, y: (x - ex) * rx + (y - ey) * ry
    world = lambda la, de: (ex + rx * la + fx * de, ey + ry * la + fy * de)
    lead = max(depth(e.x, e.y) for e in fit)
    front = [e for e in fit if depth(e.x, e.y) > lead - 40 and e.kind != "art"] or \
        [e for e in fit if depth(e.x, e.y) > lead - 40]
    front.sort(key=lambda e: lat(e.x, e.y))
    behind = [e for e in fit if e not in front]
    arms = [e for e in fit if e.missile and e.kind != "art" and (e.ammo is None or e.ammo > 0)]
    arty = [e for e in fit if e.missile and e.kind == "art" and (e.ammo is None or e.ammo > 0)]
    reach = max((e.range for e in arms), default=0.0)
    margin = max(reach, 40.0) + 40.0
    rear = min(depth(e.x, e.y) for e in fit)

    def route(end: U, side: int) -> list[tuple[float, float]]:
        la = lat(end.x, end.y) + side * margin
        for _ in range(15):  # push outward until no enemy small arms reach either point
            pts = [world(la, lead + 30), world(la, rear - 50)]
            if all(math.hypot(px - e.x, py - e.y) > e.range + 15 for px, py in pts for e in arms):
                break
            la += side * 20
        return pts
    out = ["", f"ENEMY FORMATION: faces {compass(face)} ({face:.0f}). Front line: {len(front)} units, "
               f"{abs(lat(front[-1].x, front[-1].y) - lat(front[0].x, front[0].y)):.0f} m wide, from "
               f"{front[0].id} at ({front[0].x:.0f},{front[0].y:.0f}) to {front[-1].id} at "
               f"({front[-1].x:.0f},{front[-1].y:.0f})."]
    if behind:
        bx, by = centre(behind)
        out.append(f"  Behind the front: {', '.join(f'{e.id} {e.kind}' for e in behind[:10])}"
                   f"{' ...' if len(behind) > 10 else ''} around ({bx:.0f},{by:.0f}).")
    if arms or arty:
        out.append(f"  Its missile infantry/cavalry reach {reach:.0f} m"
                   + (f"; its artillery {max(e.range for e in arty):.0f} m" if arty else "") + ".")
    routes = []
    for end, side in ((front[0], -1), (front[-1], 1)):
        p1, p2 = route(end, side)
        routes.append(f"{compass(bearing(ex, ey, *p1))} flank (past {end.id}): via ({p1[0]:.0f},{p1[1]:.0f}) "
                      f"then ({p2[0]:.0f},{p2[1]:.0f})")
    out.append("  Routes around its flanks that stay out of its small-arms range (not artillery range): "
               + "; ".join(routes) + ".")
    return out


def unit_types_section(units: list[U], ai: int, stats: dict[str, dict]) -> list[str]:
    """One line per unit type on the field with its stats from the game's database."""
    count: dict[str, list[int]] = {}
    for u in units:
        if u.alive:
            count.setdefault(u.type, [0, 0])[0 if u.alliance == ai else 1] += 1
    lines = []
    for t, (mine, theirs) in sorted(count.items(), key=lambda kv: (-kv[1][0], kv[0])):
        st = stats.get(t)
        if not st:
            continue
        side = ", ".join(([f"yours {mine}"] if mine else []) + ([f"enemy {theirs}"] if theirs else []))
        if st.get("ship"):
            lines.append(f"  {pretty_type(t)[:34]} ({side}): {ship_text(st)}")
            continue
        parts = [f"melee {st['melee']}", f"defence {st['defence']}", f"charge +{st['charge']}",
                 f"armour {st['armour']}", f"morale {st['morale']}"]
        if st.get("vs_cav"):
            parts.append(f"+{st['vs_cav']} vs cavalry")
        if st.get("ammo") and st.get("accuracy"):
            parts.append(f"accuracy {st['accuracy']}")
        if st.get("run"):
            parts.append(f"runs {st['run']:g} m/s")
        if st.get("traits"):
            parts.append(", ".join(st["traits"]))
        lines.append(f"  {pretty_type(t)[:34]} ({side}): {', '.join(parts)}")
    if not lines:
        return []
    return ["", "UNIT TYPES (from the game's database; higher is better; speeds are full running speed, horses "
                "~10 m/s, men on foot ~4; ships: hull strength, side armour, speed, then each shot type with "
                "range, damage and reload):"] + lines


SHOT_WORDS = {"solid_shot": "solid shot", "common_shell": "common shell", "segment_shell": "segment shell",
              "segment_shell_improved": "improved segment shell", "round_shot": "round shot"}


def ship_text(st: dict) -> str:
    parts = [f"hull {st['hull']}", f"armour {st['armour']}", f"speed {st['speed']}", f"crew {st['crew']}",
             st["category"], "can be boarded" if st.get("boardable") else "cannot be boarded"]
    shots = []
    for k, x in sorted(st.get("shots", {}).items(), key=lambda kv: -kv[1]["range"]):
        tags = (["armour-piercing"] if x["ap"] else []) + (["explosive"] if x["explodes"] else []) + \
               ([f"fire risk {x['incendiary']}"] if x["incendiary"] else [])
        shots.append(f"{k} {x['range']}m dmg {x['damage']} reload {x['reload']}s"
                     + (f" ({', '.join(tags)})" if tags else ""))
    if shots:
        parts.append("shots: " + "; ".join(shots))
    for k, x in st.get("secondary", {}).items():
        parts.append(f"also {'torpedoes' if k == 'torpedo' else 'machine guns'} {x['range']}m")
    return ", ".join(parts)


def build(static: dict[int, dict], state: dict, ai: int, events: list[dict], plan: dict | None,
          plan_age_s: float | None, buildings: dict[int, dict] | None = None, terrain=None,
          morale=None, abilities: dict[str, list[str]] | None = None,
          ability_state=None, unit_stats: dict[str, dict] | None = None, unit_note=None,
          static_info: bool = True, idle_s: float | None = None) -> tuple[str, list[U]]:
    """abilities: {unit type: usable special abilities} (gamedata.unit_abilities); ability_state(unit id, ability)
    -> text with its state, e.g. "rally (ready)" (Executor.ability_text); unit_stats: {unit type: stats}
    (gamedata.unit_stats); unit_note(unit id) -> extra text for our unit's line (what the executor does with it).
    static_info: include what does not change in a battle (unit type stats, ability descriptions) - the general
    keeps it in its conversation, so it is only needed when a conversation starts. idle_s: seconds since the
    general's plans last sent a real share of the army forward (None = not tracked)."""
    units = merge(static, state)
    abilities = abilities or {}
    for u in units:  # ships: gun range from the game's database (the engine does not report it)
        if u.kind == "ship":
            st = (unit_stats or {}).get(u.type) or {}
            u.range = float(st.get("range") or 0)
            u.missile = u.range > 0
    naval = any(u.kind == "ship" for u in units)

    def ab_tag(u: U) -> str:
        have = abilities.get(u.type) if u.fighting_fit else None
        if not have:
            return ""
        return " | abilities: " + ", ".join(ability_state(u.id, a) if ability_state else a for a in have)
    from .morale import tag  # morale.py builds on this module
    risk = morale.assess(units) if morale is not None else {}
    mtag = lambda u: tag(risk, u.id)
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
    if not naval:
        out.append(f"Your army by role: {character(ours)}. Enemy by role: {character(theirs)}.")
    # at sea only the coastal batteries matter (they outrange most ships: battle 20261112-192928)
    forts = fortifications(buildings) if not naval else []
    bats = batteries(buildings, unit_stats) if naval else []
    owners = [f.get("owner") for f in forts if f.get("owner")]
    attacking = bool(owners) and sum(o != ai for o in owners) > len(owners) / 2
    out.extend(assault_section(forts, ai, ours, theirs) if attacking else fort_section(forts, ai))
    out.extend(battery_section(bats, ai, ours))
    defending_walls = bool(owners) and not attacking
    if idle_s is not None and idle_s >= IDLE_WARN_S and not defending_walls:
        out.append("")
        out.append(f"TEMPO: your plans have not sent the army forward for {idle_s:.0f}s. Unless the enemy is closing "
                   "in on you right now, take the initiative: advance, flank, threaten their guns or "
                   + ("climb the walls / go through the open gates" if attacking else "probe their line")
                   + ". The player came for a fight.")
    if not forts and not naval:  # in a siege the castle is the formation; at sea there are no flanks to ride
        out.extend(formation_section(theirs, ours))
    if terrain is not None and not naval:
        out.extend(terrain.section())
    if static_info and unit_stats:
        out.extend(unit_types_section(units, ai, unit_stats))
    out.append("")
    out.append("YOUR UNITS (id, type, kind, men, position, altitude, facing, state):")
    for u in sorted(ours, key=lambda u: (u.kind, u.id)):
        out.append(unit_line(u, fit_theirs, show_nearest=True,
                             extra=mtag(u) + (fire_tag(u, fit_theirs) if u.fighting_fit else "") + ab_tag(u)
                             + battery_tag(u, bats, ai)
                             + ((unit_note(u.id) or "") if unit_note and u.fighting_fit else "")))
    ours_ab = sorted({a for u in ours if u.fighting_fit for a in abilities.get(u.type, [])})
    if ours_ab and static_info:
        from .gamedata import ABILITIES
        out.append("  Special abilities (use with `abilities` in the plan; only when ready - each lasts a while, "
                   "then recharges):")
        out.extend(f"    {a}: {ABILITIES.get(a, '')}" for a in ours_ab)
    out.append("")
    out.append("ENEMY UNITS (closest to your army first):")
    for e in sorted(theirs, key=lambda e: math.hypot(e.x - ox, e.y - oy)):
        out.append(unit_line(e, [u for u in ours if u.fighting_fit], show_nearest=False, extra=mtag(e)))
    if morale is not None:
        out.append("")
        out.append("(morale risk: an estimate from losses, flanks, local numbers, the general and routing friends - "
                   "the game does not report morale. Units without a tag look steady.)")
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
            via = f" via {' -> '.join(f'({w[0]:.0f},{w[1]:.0f})' for w in g['via'])}" if g.get("via") else ""
            shot = f", firing {g['shot']}" if g.get("shot") else ""
            out.append(f"  - {g['name']}: {g['units']}{via} {what}{shot}")
    return "\n".join(out), units
