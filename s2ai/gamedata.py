"""Facts read from the game's own database: unit stats and which special abilities each unit type has.

The battle-script API can trigger an ability (unitcontroller:perform_special_ability(name)) but cannot list a
unit's abilities, so they come from db/unit_to_unit_abilities_junctions in the game's data.pack (Remastered CE's
first, it is what the user plays). Read once and cached in runtime/unit_abilities.json; nothing from the game is
stored in the repository.
"""
from __future__ import annotations

import json
import logging
import os
import re
import struct
import threading
import zlib
from pathlib import Path

log = logging.getLogger("s2ai.gamedata")

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "runtime" / "unit_abilities.json"
JUNCTIONS = "db\\unit_to_unit_abilities_junctions_tables\\unit_to_unit_abilities_junctions"
TIMINGS = "db\\unit_special_abilities_tables\\unit_special_abilities"
ICONS = REPO / "runtime" / "icons"

# Abilities the general may use: they act on the unit itself or around it, so perform_special_ability(name) needs
# no target. (Inspire Unit and Regroup Position need a clicked target; Stand and Fight pins the general down;
# formations and deployables are other commands.) Text: the game's own descriptions, shortened.
ABILITIES = {
    "rally": "general: boosts the morale of units around him and rallies ROUTING units nearby; long cooldown",
    "warcry": "demoralises up to 4 nearby enemy units, lowering their melee defence and speed for a while",
    "stand_firm": "Hold Firm: this unit and up to 3 nearby become unbreakable with more defence for a while, but slower",
    "banzai": "unbreakable, faster, stronger charge and melee for a short time; weaker for a while afterwards",
    "second_wind": "removes fatigue from this unit and up to 3 nearby units",
    "rapid_advance": "more speed and charge bonus for a short time",
    "active_hiding": "Stealth: hidden even when running for a short time, with a charge and melee bonus",
    "ganbatte": "general: a much larger area of influence for a while - more morale, melee and rate of fire",
    "rapid_volley": "missile: higher rate of fire for a short time",
    "range_effect": "missile: longer firing range for a short time",
    "suppression_fire": "missile: faster reload, lower accuracy; enemies hit are slowed and lose morale",
    "flaming_arrows_ability": "bows: flaming arrows for a while - more damage, set fire to buildings; not in rain",
    "flaming_arrows_long_range_ability": "bows: flaming arrows (long range) for a while; not in rain",
    "flaming_arrows_extreme_range_ability": "bows: flaming arrows (extreme range) for a while; not in rain",
    "whistling_arrows_ability": "bows: arrows that lower morale and fighting of ALL troops they fly over, "
                                "friend or foe",
    "whistling_arrows_long_range_ability": "bows: whistling arrows (long range), friend or foe",
    "whistling_arrows_extreme_range_ability": "bows: whistling arrows (extreme range), friend or foe",
    "shrapnel_shot": "artillery: shells that burst above the target units for a while",
}
# as the game's buttons call them (for the battle map)
ABILITY_NAMES = {"rally": "Rally", "warcry": "War Cry", "stand_firm": "Hold Firm", "banzai": "Banzai",
                 "second_wind": "Second Wind", "rapid_advance": "Rapid Advance", "active_hiding": "Stealth",
                 "ganbatte": "Ganbatte", "rapid_volley": "Rapid Volley", "range_effect": "Increased Range",
                 "suppression_fire": "Suppression Fire", "flaming_arrows_ability": "Flaming Arrows",
                 "flaming_arrows_long_range_ability": "Flaming Arrows (long range)",
                 "flaming_arrows_extreme_range_ability": "Flaming Arrows (extreme range)",
                 "whistling_arrows_ability": "Whistling Arrows",
                 "whistling_arrows_long_range_ability": "Whistling Arrows (long range)",
                 "whistling_arrows_extreme_range_ability": "Whistling Arrows (extreme range)",
                 "shrapnel_shot": "Shrapnel Shot"}


# ---------------------------------------------------------------- packs

def _lz4_block(src: bytes, size: int) -> bytes:
    out, i = bytearray(), 0
    while i < len(src):
        tok = src[i]
        i += 1
        n = tok >> 4
        if n == 15:
            while True:
                b = src[i]
                i += 1
                n += b
                if b != 255:
                    break
        out += src[i:i + n]
        i += n
        if i >= len(src):
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        m = tok & 15
        if m == 15:
            while True:
                b = src[i]
                i += 1
                m += b
                if b != 255:
                    break
        m += 4
        start = len(out) - off
        for k in range(m):
            out.append(out[start + k])
    return bytes(out[:size])


def read_pack_entries(pack: Path, names: list[str]) -> dict[str, bytes]:
    """Files from a PFHs (CE, LZ4 entries) or PFH3/PFH4 pack in one pass: {name as asked: bytes}; missing ones are
    left out."""
    want = {n.lower(): n for n in names}
    found: dict[str, bytes] = {}
    with pack.open("rb") as f:
        hdr = f.read(32)
        magic = hdr[:4]
        if magic not in (b"PFHs", b"PFH3", b"PFH4"):
            return found
        typ, _, psize, count, isize = struct.unpack_from("<5I", hdr, 4)
        f.seek(32 + psize)
        idx = f.read(isize)
        data_start = 32 + psize + isize
        p, off = 0, data_start
        for _ in range(count):
            if magic == b"PFHs":
                eoff, size, stored = struct.unpack_from("<QII", idx, p)
                p += 16 + (8 if typ & 0x40 else 0)
            else:
                size, = struct.unpack_from("<I", idx, p)
                eoff, stored = off, size
                off += size
                p += 4 + (4 if typ & 0x40 else 0)
            end = idx.index(b"\0", p)
            ename = idx[p:end].decode("latin1").lower()
            p = end + 1
            if ename in want:
                f.seek(eoff)
                raw = f.read(stored)
                found[want[ename]] = _lz4_block(raw, size) if magic == b"PFHs" else raw
                if len(found) == len(want):
                    break
    return found


def read_pack_entry(pack: Path, name: str) -> bytes | None:
    """One file from a pack, or None if it is not there."""
    return read_pack_entries(pack, [name]).get(name)


def parse_junctions(b: bytes) -> dict[str, list[str]]:
    """db table with two string columns (unit key, ability key) -> {unit: [abilities]}."""
    p = 0
    if b[:4] == b"\xfd\xfe\xfc\xff":  # GUID
        n, = struct.unpack_from("<H", b, 4)
        p = 6 + 2 * n
    if b[p:p + 4] == b"\xfc\xfd\xfe\xff":  # version
        p += 8
    p += 1  # marker
    rows, = struct.unpack_from("<I", b, p)
    p += 4

    def s() -> str:
        nonlocal p
        n, = struct.unpack_from("<H", b, p)
        v = b[p + 2:p + 2 + 2 * n].decode("utf-16le")
        p += 2 + 2 * n
        return v
    out: dict[str, list[str]] = {}
    for _ in range(rows):
        u, a = s(), s()
        out.setdefault(u, []).append(a)
    if p != len(b):
        raise ValueError(f"unexpected table layout ({p} of {len(b)} bytes read)")
    return out


def parse_timings(b: bytes, keys) -> dict[str, dict]:
    """db/unit_special_abilities -> {ability: {active, recharge, uses}} for the given keys. Each row starts with the
    key, then active time (f32 s), recharge time (f32 s) and number of uses (i32, -1 = unlimited); the rest of the
    row has optional fields, so rows are found by their key instead of being walked one by one."""
    out = {}
    for k in keys:
        i = b.find(struct.pack("<H", len(k)) + k.encode("utf-16le"))
        if i < 0:
            continue
        active, recharge, uses = struct.unpack_from("<ffi", b, i + 2 + 2 * len(k))
        if 0 < active < 3600 and 0 <= recharge < 3600:
            out[k] = {"active": round(active, 1), "recharge": round(recharge, 1), "uses": uses}
    return out


class _Row:
    """Reads one db row field by field (Shogun 2 db encoding: UTF-16 strings with a u16 length, optional strings
    with a bool byte in front, 1-byte booleans, little-endian numbers)."""

    def __init__(self, b: bytes, p: int):
        self.b, self.p = b, p

    def s(self) -> str:
        n, = struct.unpack_from("<H", self.b, self.p)
        v = self.b[self.p + 2:self.p + 2 + 2 * n].decode("utf-16le")
        self.p += 2 + 2 * n
        return v

    def o(self) -> str:
        self.p += 1
        return self.s() if self.b[self.p - 1] else ""

    def i(self) -> int:
        self.p += 4
        return struct.unpack_from("<i", self.b, self.p - 4)[0]

    def f(self) -> float:
        self.p += 4
        return struct.unpack_from("<f", self.b, self.p - 4)[0]

    def bool(self) -> bool:
        self.p += 1
        if self.b[self.p - 1] > 1:
            raise ValueError("not a boolean")
        return bool(self.b[self.p - 1])


def _table_version(b: bytes) -> int:
    p = 0
    if b[:4] == b"\xfd\xfe\xfc\xff":
        n, = struct.unpack_from("<H", b, 4)
        p = 6 + 2 * n
    return struct.unpack_from("<I", b, p + 4)[0] if b[p:p + 4] == b"\xfc\xfd\xfe\xff" else 0


# Field order of the game's db tables: the community schema of RPFM (github.com/Frodo45127/rpfm-schemas,
# schema_sho2.ron) - unit_stats_land version 16, battle_entities version 0.


# unit_stats_land version 16, one letter per field: S string, O optional string, I i32, F f32, B boolean
STATS_LAYOUT = "SIIISOOSSSSISIOOOIIOOOOBOOIISSOIOSIIIIIISSSSIIFFFFFFFFFIBBBBBBBBBBBBBBFFFBBBBBIBBBBBBBBBBOOOBBSSSSSSSSBBBBBBFFFFB"
F = {n: i for i, n in enumerate((
    "key men horse guns officer musician standard_bearer soldier_model man_entity man_animations equipment_theme "
    "armour armour_audio man_health mount_model mount_entity mount_animations mount_armour mount_health "
    "articulated_entity caisson_entity caisson_model artillery_model siege_gun gun_type primary_missile_weapon "
    "marksmanship loading_skill missile_attack firing_mechanism missile_type ammo skeleton_missile melee_weapon "
    "melee_attack charge_bonus dis_melee dis_charge melee_defence shield_bonus skeleton_melee pistols drill_set "
    "training morale rank_depth f1 f2 f3 f4 f5 f6 f7 f8 density vs_cav grenades skirmish guard_mode can_dismount "
    "stakes cannot_run can_rally hide1 hide2 hide3 hide4 can_stalk can_snipe fires_mounted spot1 spot2 spot3 "
    "camp1 camp2 disciplined impetuous scares_horses fear inspire fatigue_resistant siege_ladders heat cold "
    "formed_attack pike_wall pike_square no_attrition fire_moving musician_2 caisson_destroyed caisson_destruction "
    "all_ranks_fire guerrilla a1 a2 a3 a4 a5 a6 a7 a8 climb_fast fire_buildings fire_ground ap_missile ap_melee "
    "loose_formation v1 v2 v3 v4 bulletproof").split())}
assert len(F) == len(STATS_LAYOUT)
TRAITS = {"disciplined": "disciplined", "impetuous": "impetuous", "scares_horses": "scares horses",
          "inspire": "inspires", "fatigue_resistant": "fatigue resistant", "stakes": "can place stakes",
          "can_stalk": "stealth", "ap_missile": "armour-piercing missiles", "ap_melee": "armour-piercing melee",
          "bulletproof": "bulletproof"}


def read_rows(b: bytes, layout: str) -> list[list]:
    """Every row of a db table, given its field layout. Raises ValueError if the layout does not fit."""
    p = 0
    if b[:4] == b"\xfd\xfe\xfc\xff":
        n, = struct.unpack_from("<H", b, 4)
        p = 6 + 2 * n
    if b[p:p + 4] == b"\xfc\xfd\xfe\xff":
        p += 8
    rows, = struct.unpack_from("<I", b, p + 1)
    r = _Row(b, p + 5)
    read = {"S": r.s, "O": r.o, "I": r.i, "F": r.f, "B": r.bool}
    out = []
    try:
        for _ in range(rows):
            out.append([read[c]() for c in layout])
    except (struct.error, UnicodeDecodeError, IndexError) as e:
        raise ValueError(f"row {len(out)}: {e}") from None
    if r.p != len(b):
        raise ValueError(f"unexpected table layout ({r.p} of {len(b)} bytes read)")
    return out


def parse_unit_stats(b: bytes) -> dict[str, dict]:
    """db/unit_stats_land (version 16) -> {unit type: stats}."""
    if _table_version(b) != 16:
        raise ValueError(f"unit_stats_land version {_table_version(b)}, expected 16")
    out = {}
    for row in read_rows(b, STATS_LAYOUT):
        v = lambda name: row[F[name]]
        horses = v("horse")
        traits = [text for name, text in TRAITS.items() if v(name)] + (["causes fear"] if v("fear") > 0 else [])
        out[v("key")] = {
            "men": v("men"), "horses": horses, "guns": v("guns"), "melee": v("melee_attack"),
            "charge": v("charge_bonus"), "defence": v("melee_defence"), "shield": v("shield_bonus"),
            "armour": v("armour") + (v("mount_armour") if horses else 0),
            "hp": v("man_health") + (v("mount_health") if horses else 0), "morale": v("morale"),
            "vs_cav": v("vs_cav"), "ammo": v("ammo"), "accuracy": v("marksmanship"), "reload": v("loading_skill"),
            "weapon": v("melee_weapon"), "traits": traits,
            "_entity": v("mount_entity") if horses and v("mount_entity") else v("man_entity")}
    return out


# battle_entities version 0: key, class validation, type, walk / run speed, acceleration, deceleration, charge
# speed, crawl speed, 3 charge distances, radius, shape, radii ratio, mass, height, 2 fire arcs, turn speed, hit points
ENTITY_LAYOUT = "SSSFFFFFFFFFFSFFFFFFI"


def parse_entities(b: bytes) -> dict[str, dict]:
    """db/battle_entities (version 0) -> {entity: {walk, run, charge_speed} in m/s}."""
    if _table_version(b) != 0:
        raise ValueError(f"battle_entities version {_table_version(b)}, expected 0")
    return {r[0]: {"walk": round(r[3], 1), "run": round(r[4], 1), "charge_speed": round(r[7], 1)}
            for r in read_rows(b, ENTITY_LAYOUT)}


# units version 13: key, on-screen name, ... (only the name is used); uniforms version 2: uniform, faction,
# picture file (the unit card / icon name), unit, officer / standard bearer / musician / imposter files
UNITS_LAYOUT = "SSSSIIIIIIOSSSOISBBBIBIOBSOOOBOIBISSB"
UNIFORMS_LAYOUT = "SOSSOOOO"


def parse_unit_names(b: bytes) -> dict[str, str]:
    if _table_version(b) != 13:
        raise ValueError(f"units version {_table_version(b)}, expected 13")
    return {r[0]: r[1] for r in read_rows(b, UNITS_LAYOUT) if r[1]}


def parse_uniforms(b: bytes) -> dict[str, str]:
    """-> {unit type: picture file name}; the uniform without a faction is the default."""
    if _table_version(b) not in (2, 3):
        raise ValueError(f"uniforms version {_table_version(b)}, expected 2")
    out: dict[str, str] = {}
    for r in sorted(read_rows(b, UNIFORMS_LAYOUT), key=lambda r: r[1] != ""):
        out.setdefault(r[3], r[2].lower())
    return out


# Ships (naval battles). unit_stats_naval version 16 (135 fields; the guns on each deck, crew, speed, armour,
# hull strength, ...), gun_type_to_projectiles version 1 (gun, projectile, muzzle flash, fire frequency) and
# projectiles version 5 (shot type, range, damage, armour piercing, incendiary, reload, ...).
SHIPS_LAYOUT = ("SIIIOOOOOOOOSIIIIISFFFFIFIIIIFFFIIIFFFFFFFFFFFFFFFFFFFFFFBIIFIIFIIFIIFIIFIIFIIFIIFIIFIIFIIFIIFIIFIIFIIFIIFIIF"
                "IIFIIFBBBSISSIOSIIFSBFFFFB")
SHIP_F = {"key": 0, "morale": 1, "guns": (5, 6, 7, 8, 9, 10, 11), "marines": 13, "seamen": 14, "gunners": 15,
          "fire_resistance": 22, "speed": 28, "armour": 58, "hull": 119, "category": 120, "torpedo": 126,
          "ram": 127, "boardable": 134}
GUN_SHOTS_LAYOUT = "SSSF"
PROJECTILES_LAYOUT = "SSSSOOOSIOSIIIFFFFOOOBBFIIOFFSSOOOOOFFBF"
SHOT_NAMES = {"solid_shot": "solid shot", "common_shell": "common shell (explosive)",
              "segment_shell": "segment shell", "segment_shell_improved": "improved segment shell",
              "torpedo": "torpedo", "bullet": "machine-gun bullets", "round_shot": "round shot",
              "flaming_arrows_naval": "fire arrows", "arrow": "arrows", "hot_shot": "heated shot"}


def parse_ships(b: bytes) -> dict[str, dict]:
    """db/unit_stats_naval (version 16) -> {ship type: {"_guns": [gun types], crew, speed, armour, hull, ...}}."""
    if _table_version(b) != 16:
        raise ValueError(f"unit_stats_naval version {_table_version(b)}, expected 16")
    out = {}
    for r in read_rows(b, SHIPS_LAYOUT):
        f = lambda k: r[SHIP_F[k]]
        guns = [r[i] for i in SHIP_F["guns"] if r[i]]
        out[f("key")] = {"ship": True, "_guns": list(dict.fromkeys(guns)), "morale": f("morale"),
                         "crew": f("marines") + f("seamen") + f("gunners"), "marines": f("marines"),
                         "speed": f("speed"), "armour": f("armour"), "hull": f("hull"),
                         "category": ", ".join(x.strip().lower().replace("hull_", "") for x in f("category").split(",")),
                         "torpedo": f("torpedo") > 0, "ram": round(f("ram"), 2), "boardable": f("boardable")}
    return out


def parse_gun_shots(b: bytes) -> dict[str, list[str]]:
    """db/gun_type_to_projectiles (version 1) -> {gun type: [projectiles]}."""
    if _table_version(b) != 1:
        raise ValueError(f"gun_type_to_projectiles version {_table_version(b)}, expected 1")
    out: dict[str, list[str]] = {}
    for r in read_rows(b, GUN_SHOTS_LAYOUT):
        out.setdefault(r[0], []).append(r[1])
    return out


def parse_projectiles(b: bytes) -> dict[str, dict]:
    """db/projectiles (version 5) -> {projectile: {shot, range, min_range, damage, reload, ap, incendiary, explodes}}."""
    if _table_version(b) != 5:
        raise ValueError(f"projectiles version {_table_version(b)}, expected 5")
    return {r[0]: {"shot": r[2], "range": r[11], "min_range": r[12], "damage": round(r[17]), "ap": r[18] == "ap",
                   "incendiary": r[19], "explodes": r[20] == "explode", "reload": r[24]}
            for r in read_rows(b, PROJECTILES_LAYOUT)}


def ship_stats(ships: dict, gun_shots: dict, projectiles: dict) -> dict[str, dict]:
    """Ships with their shot types: {"shots": {shot type: {range, damage, ...}}} for the main guns (the best of
    each shot type over the ship's cannon), "secondary": other weapons (machine guns, torpedoes)."""
    out = {}
    for key, st in ships.items():
        shots: dict[str, dict] = {}
        secondary: dict[str, dict] = {}
        for gun in st["_guns"]:
            for pr in gun_shots.get(gun, []):
                p = projectiles.get(pr)
                if not p:
                    continue
                into = secondary if p["shot"] in ("bullet", "torpedo") else shots
                cur = into.get(p["shot"])
                if cur is None or (p["range"], p["damage"]) > (cur["range"], cur["damage"]):
                    into[p["shot"]] = {k: v for k, v in p.items() if k != "shot"}
        out[key] = {**{k: v for k, v in st.items() if k != "_guns"}, "shots": shots, "secondary": secondary,
                    "range": max((x["range"] for x in shots.values()), default=0)}
    return out


def battery_stats(stats: dict, gun_shots: dict, projectiles: dict) -> dict[str, dict]:
    """Coastal batteries (shore guns in naval battles near a port) -> {battlefield building key: {kind, guns, shot,
    range, damage, reload}}. The building coastal_batteries_<size> is crewed by the unit Boshin_Art_Coastal_Guns_<Size>
    (db battlefield_buildings) whose cannon are boshin_coastal_cannon_<size> (db gun_type_to_projectiles)."""
    crews = {re.sub(r"_+", "_", k.lower()): st for k, st in stats.items()}
    out = {}
    for gun, prs in gun_shots.items():
        m = re.fullmatch(r"\w*coastal_cannon_(\w+?)(_mp)?", gun.lower())
        best = max((projectiles[p] for p in prs if p in projectiles), key=lambda p: (p["range"], p["damage"]),
                   default=None)
        if not m or not best:
            continue
        size = m.group(1)
        crew = next((st for k, st in sorted(crews.items(), key=lambda kv: kv[0].endswith("_mp"))
                     if k.removesuffix("_mp").endswith(f"coastal_guns_{size}")), {})
        out[f"coastal_batteries_{size}"] = {"kind": "battery", "guns": crew.get("guns") or 0, "shot": best["shot"],
                                            **{k: best[k] for k in ("range", "damage", "reload")}}
    return out


CACHE_VERSION = 6
SHIPS = "db\\unit_stats_naval_tables\\unit_stats_naval"
GUN_SHOTS = "db\\gun_type_to_projectiles_tables\\gun_type_to_projectiles"
PROJECTILES = "db\\projectiles_tables\\projectiles"
STATS = "db\\unit_stats_land_tables\\unit_stats_land"
ENTITIES = "db\\battle_entities_tables\\battle_entities"
UNITS = "db\\units_tables\\units"
UNIFORMS = "db\\uniforms_tables\\uniforms"


def _data_packs() -> list[Path]:
    from .gamepaths import data_dirs
    return sorted((d / "data.pack" for d in data_dirs()), key=lambda p: "remasteredce" not in str(p).lower())


_game_data_lock = threading.Lock()
_game_data: dict | None = None
_game_data_checked = 0.0
GAME_DATA_RECHECK_S = 60.0  # how often to look whether the packs changed (a game update)


def game_data() -> dict:
    """{"units": {unit type: usable abilities}, "timings": {ability: {active, recharge, uses}},
    "stats": {unit type: stats}, "names": {unit type: name}, "pictures": {unit type: picture}} from the installed
    game, kept in memory and cached in runtime/. Empty parts if the game is not found or its packs cannot be read."""
    global _game_data, _game_data_checked
    import time
    with _game_data_lock:
        if _game_data is not None and time.monotonic() - _game_data_checked < GAME_DATA_RECHECK_S:
            return _game_data
        _game_data_checked = time.monotonic()
        try:
            packs = _data_packs()
            stamp = [[str(p), p.stat().st_mtime] for p in packs]
        except OSError as e:
            log.warning("could not find the game's packs: %s", e)
            packs, stamp = [], []
        if _game_data is not None and _game_data.get("packs") == stamp:
            return _game_data
        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
            if cached.get("packs") == stamp and cached.get("v") == CACHE_VERSION:
                _game_data = cached
                return cached
        except (OSError, ValueError, KeyError):
            pass
        _game_data = _read_game_data(packs, stamp)
        return _game_data


def _read_game_data(packs: list[Path], stamp: list) -> dict:
    units: dict[str, list[str]] = {}
    timings: dict[str, dict] = {}
    stats: dict[str, dict] = {}
    entities: dict[str, dict] = {}
    names: dict[str, str] = {}
    pictures: dict[str, str] = {}
    ships: dict[str, dict] = {}
    gun_shots: dict[str, list[str]] = {}
    projectiles: dict[str, dict] = {}
    for pack in reversed(packs):  # Remastered CE last: its rows win
        try:
            got = read_pack_entries(pack, [JUNCTIONS, TIMINGS, STATS, ENTITIES, UNITS, UNIFORMS, SHIPS, GUN_SHOTS,
                                           PROJECTILES])
        except (OSError, ValueError, struct.error, IndexError) as e:  # missing, or a format this does not know
            log.warning("could not read %s: %s", pack, e)
            continue
        for name, parse, into in ((JUNCTIONS, parse_junctions, units),
                                  (TIMINGS, lambda b: parse_timings(b, ABILITIES), timings),
                                  (STATS, parse_unit_stats, stats), (ENTITIES, parse_entities, entities),
                                  (UNITS, parse_unit_names, names), (UNIFORMS, parse_uniforms, pictures),
                                  (SHIPS, parse_ships, ships), (GUN_SHOTS, parse_gun_shots, gun_shots),
                                  (PROJECTILES, parse_projectiles, projectiles)):
            if name in got:
                try:
                    into.update(parse(got[name]))
                except (ValueError, struct.error, IndexError, KeyError) as e:
                    log.warning("could not read %s from %s: %s", name.rsplit("\\", 1)[-1], pack, e)
    for st in stats.values():  # speeds come from the man's entity, or the horse's for cavalry
        st.update(entities.get(st.pop("_entity", None), {}))
    stats.update(battery_stats(stats, gun_shots, projectiles))  # keyed by building, read before ships join
    stats.update(ship_stats(ships, gun_shots, projectiles))  # ships live with the land units' stats
    units = {u: [a for a in ab if a in ABILITIES] for u, ab in units.items()}
    units = {u: ab for u, ab in units.items() if ab}
    data = {"v": CACHE_VERSION, "packs": stamp, "units": units, "timings": timings, "stats": stats,
            "names": names, "pictures": pictures}
    try:
        CACHE.parent.mkdir(exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, CACHE)
    except OSError:
        pass
    log.info("game data: %d unit types with usable special abilities, timings for %d abilities, "
             "stats for %d unit types", len(units), len(timings), len(stats))
    return data


def unit_abilities() -> dict[str, list[str]]:
    """{unit type: usable special abilities}."""
    return game_data()["units"]


def unit_stats() -> dict[str, dict]:
    """{unit type: {men, melee, charge, defence, armour, morale, vs_cav, ammo, accuracy, walk, run, ...}}."""
    return game_data()["stats"]


def ability_timings() -> dict[str, dict]:
    """{ability: {"active": s, "recharge": s, "uses": n or -1}}."""
    return game_data()["timings"]


# ---------------------------------------------------------------- icons (for the battle map)

def tga_to_png(b: bytes) -> bytes:
    """Truevision TGA (uncompressed or RLE, 24/32 bit - what the game's UI icons use) -> PNG."""
    id_len, cmap_type, img_type = b[0], b[1], b[2]
    w, h, bpp, desc = struct.unpack_from("<HHBB", b, 12)
    if cmap_type or img_type not in (2, 10) or bpp not in (24, 32):
        raise ValueError(f"unsupported TGA (type {img_type}, {bpp} bpp)")
    px, n = bpp // 8, w * h
    p = 18 + id_len
    if img_type == 2:
        data = b[p:p + n * px]
    else:
        out = bytearray()
        while len(out) < n * px:
            c = b[p]
            p += 1
            if c & 0x80:
                out += b[p:p + px] * ((c & 0x7F) + 1)
                p += px
            else:
                k = ((c & 0x7F) + 1) * px
                out += b[p:p + k]
                p += k
        data = bytes(out[:n * px])
    rows = [data[r * w * px:(r + 1) * w * px] for r in range(h)]
    if not desc & 0x20:  # origin bottom-left
        rows.reverse()
    raw = bytearray()
    for r in rows:
        raw.append(0)
        for i in range(0, len(r), px):  # BGR(A) -> RGBA
            raw += bytes((r[i + 2], r[i + 1], r[i], r[i + 3] if px == 4 else 255))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


UNIT_ICONS = ICONS / "units"
_unit_icons_lock = threading.Lock()


def unit_icons() -> Path:
    """Every unit's square icon (ui/units/icons_square) and unit card (ui/units/icons) as
    runtime/icons/units/<picture>.png and <picture>_card.png, made once (a few seconds). -> the folder."""
    with _unit_icons_lock:
        UNIT_ICONS.mkdir(parents=True, exist_ok=True)
        want = {}
        for pic in set(game_data().get("pictures", {}).values()):
            for folder, suffix in (("icons_square", ""), ("icons", "_card")):
                if not (UNIT_ICONS / f"{pic}{suffix}.png").exists():
                    want[f"ui\\units\\{folder}\\{pic}.tga"] = f"{pic}{suffix}.png"
        for pack in _data_packs():  # Remastered CE first
            if not want:
                break
            try:
                got = read_pack_entries(pack, list(want))
            except OSError as e:
                log.warning("could not read unit icons from %s: %s", pack, e)
                continue
            for name, data in got.items():
                try:
                    (UNIT_ICONS / want.pop(name)).write_bytes(tga_to_png(data))
                except (ValueError, struct.error, IndexError) as e:
                    log.warning("unit icon %s: %s", name, e)
        return UNIT_ICONS


class Shogun2Assets:
    """The battle map's pictures and names from the installed game (see assets.py)."""

    def __init__(self):
        self._made = False

    def unit_icon(self, unit_type: str, card: bool = False) -> bytes | None:
        pic = game_data().get("pictures", {}).get(unit_type)
        if not pic:
            return None
        path = UNIT_ICONS / f"{pic}{'_card' if card else ''}.png"
        if not path.exists() and not self._made:
            self._made = True
            unit_icons()
        return path.read_bytes() if path.exists() else None

    def ability_icon(self, ability: str) -> bytes | None:
        path = ICONS / f"{ability}.png"
        return path.read_bytes() if ability in ABILITIES and path.exists() else None

    def names(self) -> dict[str, dict[str, str]]:
        return {"units": game_data().get("names", {}), "abilities": ABILITY_NAMES}


def ability_icons() -> Path:
    """The game's button icons of the usable abilities as runtime/icons/<ability>.png (made once). -> the folder."""
    ICONS.mkdir(parents=True, exist_ok=True)
    missing = [a for a in ABILITIES if not (ICONS / f"{a}.png").exists()]
    if not missing:
        return ICONS
    names = {a: f"ui\\battle ui\\button icons\\{a}_icon.tga" for a in missing}
    for pack in _data_packs():  # Remastered CE first
        try:
            got = read_pack_entries(pack, list(names.values()))
        except OSError as e:
            log.warning("could not read icons from %s: %s", pack, e)
            continue
        for a, name in list(names.items()):
            if name in got:
                try:
                    (ICONS / f"{a}.png").write_bytes(tga_to_png(got[name]))
                    del names[a]
                except (ValueError, struct.error, IndexError) as e:
                    log.warning("icon %s: %s", a, e)
        if not names:
            break
    return ICONS


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ua = unit_abilities()
    for u in sorted(ua)[:15]:
        print(u, ua[u])
    print(ability_timings())
    print(sorted(p.name for p in ability_icons().glob("*.png")))
    print(len(list(unit_icons().glob("*.png"))), "unit pictures")
