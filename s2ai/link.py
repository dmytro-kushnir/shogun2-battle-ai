"""File link to the game-side bridge (lua/s2ai_bridge.lua).

game -> us: runtime/battle-<id>.jsonl (battle_start, state, event, applied, battle_end), id in current_battle.txt
us -> game: runtime/orders.lua, a Lua table the bridge loads sandboxed every tick
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .terrain import HeightMap, fort_points

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def to_lua(v) -> str:
    """Python value -> Lua literal (dicts become tables with string keys, lists become arrays)."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (v != v or abs(v) > 1e15):
            return "nil"  # nan / inf have no Lua 5.1 literal
        return repr(round(v, 3) if isinstance(v, float) else v)
    if isinstance(v, str):
        # Lua 5.1 has no \u escape: write the UTF-8 bytes, escaping anything not plain printable ASCII as \ddd
        out = []
        for b in v.encode("utf-8"):
            ch = chr(b)
            out.append(ch if 32 <= b < 127 and ch not in '"\\' else f"\\{b:03d}")
        return '"' + "".join(out) + '"'
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(to_lua(x) for x in v) + "}"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {to_lua(x)}" for k, x in v.items() if x is not None) + "}"
    raise TypeError(f"cannot write {type(v).__name__} to Lua")


@dataclass
class Battle:
    id: str
    start: dict
    state: dict | None = None
    phase: str = "deployment"
    ended: bool = False
    events: list = field(default_factory=list)
    applied: list = field(default_factory=list)
    buildings: dict[int, dict] | None = None  # id -> {name, x, y, z, cap, owner (1-based, 0 none), hp, mil, gar}
    terrain: HeightMap = field(default_factory=HeightMap)  # ground heights learned from unit altitudes

    @property
    def static(self) -> dict[int, dict]:
        return {u["id"]: u for u in self.start["units"]}


class GameLink:
    def __init__(self, runtime: Path = RUNTIME):
        self.runtime = runtime
        self.runtime.mkdir(exist_ok=True)
        self.battle: Battle | None = None
        self._pos = 0
        self._partial = ""
        self._seq = 0
        self._last_orders: list[dict] = []

    # ---------------------------------------------------------------- reading

    def current_id(self) -> str | None:
        try:
            return (self.runtime / "current_battle.txt").read_text().strip() or None
        except OSError:
            return None

    def poll(self) -> list[dict]:
        """New messages from the game since the last call (switches to a new battle when the game starts one)."""
        bid = self.current_id()
        if bid is None:
            return []
        if self.battle is None or self.battle.id != bid:
            self._pos, self._partial = 0, ""
            self.battle = None
        path = self.runtime / f"battle-{bid}.jsonl"
        if self.battle is None and self._pos == 0:
            # a battle left mid-way never sends battle_end: do not attach to a stream that went quiet
            try:
                if time.time() - path.stat().st_mtime > 30:
                    return []
            except OSError:
                return []
        try:
            with path.open("rb") as f:
                f.seek(self._pos)
                chunk = f.read()
                self._pos = f.tell()
        except OSError:
            return []
        text = self._partial + chunk.decode("utf-8", errors="replace")
        lines = text.split("\n")
        self._partial = lines.pop()  # unfinished last line, completed on a later poll
        msgs = []
        for line in lines:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._absorb(bid, msg)
            msgs.append(msg)
        return msgs

    def _absorb(self, bid: str, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "battle_start":
            self.battle = Battle(id=bid, start=msg)
        if self.battle is None:
            return
        if kind == "state":
            self.battle.state = msg
            self.battle.phase = msg.get("phase", self.battle.phase)
            self.battle.terrain.add_state(msg)
            for i, hp, owner, gar in msg.get("buildings") or []:  # fortifications' changing state, every 2 s
                b = (self.battle.buildings or {}).get(i)
                if b is not None:
                    b["hp"], b["owner"], b["gar"] = hp, owner, bool(gar)
        elif kind == "event":
            self.battle.events.append(msg)
            self.battle.phase = msg.get("phase", self.battle.phase)
        elif kind == "applied":
            self.battle.applied.append(msg)
        elif kind == "buildings":  # every building on the map, once: walls, gates, towers, keep, houses
            self.battle.buildings = {b["id"]: b for b in msg.get("buildings", [])}
            self.battle.terrain.set_forts(fort_points(self.battle.buildings))
        elif kind == "units":  # units that joined after the start (reinforcements): their static info
            have = {u["id"] for u in self.battle.start["units"]}
            self.battle.start["units"].extend(u for u in msg.get("units", []) if u["id"] not in have)
        elif kind == "battle_end":
            self.battle.ended = True

    # ---------------------------------------------------------------- writing

    say: str | None = None  # shown in game as an advisor message when it changes

    def send(self, take: list[int], orders: list[dict]) -> int:
        """Write a new orders file: control exactly `take`, then carry out `orders` once. Returns its seq."""
        if self.battle is None:
            raise RuntimeError("no battle")
        self._seq += 1
        self._write(take, orders)
        return self._seq

    def heartbeat(self, take: list[int]) -> None:
        """Keep control without new orders (the bridge hands units back when the file goes stale)."""
        if self.battle is not None:
            self._write(take, [], same_seq=True)

    def _write(self, take, orders, same_seq=False) -> None:
        # `beat` changes with every write: the bridge times the server's silence in battle ticks (its Lua numbers
        # are 32-bit floats, too coarse for wall-clock timestamps)
        self._beat = (getattr(self, "_beat", 0) + 1) % 1_000_000
        body = {"battle": self.battle.id, "seq": self._seq, "beat": self._beat, "take": list(take),
                "orders": orders if not same_seq else self._last_orders, "say": self.say}
        if not same_seq:
            self._last_orders = orders
        tmp = self.runtime / "orders.lua.tmp"
        tmp.write_text("return " + to_lua(body) + "\n", encoding="ascii")
        # the game may be reading orders.lua right now (Windows then refuses the replace): retry briefly
        for attempt in range(20):
            try:
                os.replace(tmp, self.runtime / "orders.lua")
                return
            except PermissionError:
                time.sleep(0.02)
        raise PermissionError("orders.lua stayed locked for 0.4 s")
