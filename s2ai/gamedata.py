"""Facts read from the game's own database: which special abilities each unit type has.

The battle-script API can trigger an ability (unitcontroller:perform_special_ability(name)) but cannot list a
unit's abilities, so they come from db/unit_to_unit_abilities_junctions in the game's data.pack (Remastered CE's
first, it is what the user plays). Read once and cached in runtime/unit_abilities.json; nothing from the game is
stored in the repository.
"""
from __future__ import annotations

import json
import logging
import struct
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


CACHE_VERSION = 2


def _data_packs() -> list[Path]:
    from .gamepaths import data_dirs
    return sorted((d / "data.pack" for d in data_dirs()), key=lambda p: "remasteredce" not in str(p).lower())


def game_data() -> dict:
    """{"units": {unit type: usable abilities}, "timings": {ability: {active, recharge, uses}}} from the installed
    game (cached in runtime/). Empty if the game is not found."""
    packs = _data_packs()
    stamp = [[str(p), p.stat().st_mtime] for p in packs]
    try:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if cached.get("packs") == stamp and cached.get("v") == CACHE_VERSION:
            return cached
    except (OSError, ValueError, KeyError):
        pass
    units: dict[str, list[str]] = {}
    timings: dict[str, dict] = {}
    for pack in reversed(packs):  # Remastered CE last: its rows win
        try:
            got = read_pack_entries(pack, [JUNCTIONS, TIMINGS])
            if JUNCTIONS in got:
                units.update(parse_junctions(got[JUNCTIONS]))
            if TIMINGS in got:
                timings.update(parse_timings(got[TIMINGS], ABILITIES))
        except (OSError, ValueError, struct.error) as e:
            log.warning("could not read unit abilities from %s: %s", pack, e)
    units = {u: [a for a in ab if a in ABILITIES] for u, ab in units.items()}
    units = {u: ab for u, ab in units.items() if ab}
    data = {"v": CACHE_VERSION, "packs": stamp, "units": units, "timings": timings}
    try:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    log.info("game data: %d unit types with usable special abilities, timings for %d abilities",
             len(units), len(timings))
    return data


def unit_abilities() -> dict[str, list[str]]:
    """{unit type: usable special abilities}."""
    return game_data()["units"]


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
