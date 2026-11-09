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

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def to_lua(v) -> str:
    """Python value -> Lua literal (dicts become tables with string keys, lists become arrays)."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(round(v, 3) if isinstance(v, float) else v)
    if isinstance(v, str):
        return json.dumps(v)  # JSON string escapes are valid Lua for our ASCII content
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
        elif kind == "event":
            self.battle.events.append(msg)
            self.battle.phase = msg.get("phase", self.battle.phase)
        elif kind == "applied":
            self.battle.applied.append(msg)
        elif kind == "battle_end":
            self.battle.ended = True

    # ---------------------------------------------------------------- writing

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
        body = {"battle": self.battle.id, "seq": self._seq, "written": int(time.time()), "take": list(take),
                "orders": orders if not same_seq else self._last_orders}
        if not same_seq:
            self._last_orders = orders
        tmp = self.runtime / "orders.lua.tmp"
        tmp.write_text("return " + to_lua(body) + "\n", encoding="ascii")
        os.replace(tmp, self.runtime / "orders.lua")

    _last_orders: list = []
