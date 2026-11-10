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
from pathlib import Path

log = logging.getLogger("s2ai.gamedata")

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "runtime" / "unit_abilities.json"
JUNCTIONS = "db\\unit_to_unit_abilities_junctions_tables\\unit_to_unit_abilities_junctions"

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


def read_pack_entry(pack: Path, name: str) -> bytes | None:
    """One file from a PFHs (CE, LZ4 entries) or PFH3/PFH4 pack, or None if it is not there."""
    with pack.open("rb") as f:
        hdr = f.read(32)
        magic = hdr[:4]
        if magic not in (b"PFHs", b"PFH3", b"PFH4"):
            return None
        typ, _, psize, count, isize = struct.unpack_from("<5I", hdr, 4)
        f.seek(32 + psize)
        idx = f.read(isize)
        data_start = 32 + psize + isize
        p, off = 0, data_start
        want = name.lower()
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
            ename = idx[p:end].decode("latin1")
            p = end + 1
            if ename.lower() == want:
                f.seek(eoff)
                raw = f.read(stored)
                return _lz4_block(raw, size) if magic == b"PFHs" else raw
    return None


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


def unit_abilities() -> dict[str, list[str]]:
    """{unit type: usable special abilities} from the installed game (cached). Empty if the game is not found."""
    from .gamepaths import data_dirs
    packs = sorted((d / "data.pack" for d in data_dirs()), key=lambda p: "remasteredce" not in str(p).lower())
    stamp = [[str(p), p.stat().st_mtime] for p in packs]
    try:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if cached.get("packs") == stamp:
            return cached["units"]
    except (OSError, ValueError, KeyError):
        pass
    units: dict[str, list[str]] = {}
    for pack in reversed(packs):  # Remastered CE last: its rows win
        try:
            raw = read_pack_entry(pack, JUNCTIONS)
            if raw is None:
                continue
            units.update(parse_junctions(raw))
        except (OSError, ValueError, struct.error) as e:
            log.warning("could not read unit abilities from %s: %s", pack, e)
    units = {u: [a for a in ab if a in ABILITIES] for u, ab in units.items()}
    units = {u: ab for u, ab in units.items() if ab}
    try:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps({"packs": stamp, "units": units}), encoding="utf-8")
    except OSError:
        pass
    log.info("unit abilities: %d unit types with usable special abilities", len(units))
    return units


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ua = unit_abilities()
    for u in sorted(ua)[:15]:
        print(u, ua[u])
