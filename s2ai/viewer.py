"""Battle map in the browser: live view of the battle being fought, and replays of past battles.

    live:     the server loop calls board.update(...); the page polls /data twice a second
    history:  /battles lists runtime/battle-*.jsonl; /replay?id=<battle> returns the whole battle (positions every
              second, plans with the report the general saw, orders sent, events) for the timeline view

Listens on this PC only unless the server runs with --lan (then: all interfaces, read-only, no password).
"""
from __future__ import annotations

import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .link import RUNTIME

PAGE = Path(__file__).with_name("viewer.html")
GENERAL_RE = re.compile(r"(^|_)Gen(_|$)|General|Bodyguard|Daimyo")


class Board:
    def __init__(self):
        self.lock = threading.Lock()
        self.data: dict = {"battle": None}
        self.log: list[str] = []

    def update(self, **kw) -> None:
        with self.lock:
            self.data.update(kw)

    def note(self, line: str) -> None:
        with self.lock:
            self.log.append(line)
            del self.log[:-40]

    def snapshot(self) -> bytes:
        with self.lock:
            return json.dumps({**self.data, "log": self.log}).encode()


# ---------------------------------------------------------------- history

def _read_jsonl(path: Path) -> list[dict]:
    out = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return out


def battles(runtime: Path = RUNTIME) -> list[dict]:
    out = []
    for p in sorted(runtime.glob("battle-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        bid = p.stem[len("battle-"):]
        plans = runtime / f"plans-{bid}.jsonl"
        n_plans = sum(1 for _ in plans.open(encoding="utf-8")) if plans.exists() else 0
        out.append({"id": bid, "size_kb": round(p.stat().st_size / 1024), "plans": n_plans})
    return out


_cache: dict[str, tuple[float, float, bytes]] = {}  # battle -> (file mtime, built at, body)
_build_lock = threading.Lock()
LIVE_REBUILD_S = 10.0  # the battle in progress changes every 0.5 s: rebuild its replay at most this often


def replay(bid: str, runtime: Path = RUNTIME) -> bytes:
    if not re.fullmatch(r"[0-9-]+", bid):
        raise FileNotFoundError(bid)
    path = runtime / f"battle-{bid}.jsonl"
    with _build_lock:  # one build at a time, so map clients cannot pile up work on the server
        mtime = path.stat().st_mtime
        hit = _cache.get(bid)
        if hit and (hit[0] == mtime or time.monotonic() - hit[1] < LIVE_REBUILD_S):
            return hit[2]
        body = _build_replay(bid, path, runtime)
        _cache[bid] = (mtime, time.monotonic(), body)
        return body


def _build_replay(bid: str, path: Path, runtime: Path) -> bytes:
    msgs = _read_jsonl(path)
    start = next(m for m in msgs if m["type"] == "battle_start")
    meta = {}
    meta_path = runtime / f"meta-{bid}.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if "ai" not in meta:
        from .server import guess_ai_alliance
        meta["ai"] = guess_ai_alliance(start)
    ai = meta["ai"]
    from .gamedata import ability_timings, unit_abilities
    abl = unit_abilities()
    units = {}
    static = list(start["units"]) + [u for m in msgs if m["type"] == "units" for u in m.get("units", [])]
    for u in static:
        t = u.get("type") or "?"
        units[u["id"]] = {"ai": u["alliance"] == ai, "type": t,
                          "kind": "art" if u.get("art") else "cav" if u.get("cav") else "inf",
                          "missile": (u.get("range") or 0) > 0 and (u.get("ammo0") or 0) > 0,
                          "gen": bool(GENERAL_RE.search(t)), "men0": u.get("men0"),
                          "abl": abl.get(t) if u["alliance"] == ai else None}
    from .report import buildings_from_message
    from .server import map_buildings
    bmsg = next((m for m in msgs if m["type"] == "buildings"), None)
    buildings = map_buildings(buildings_from_message(bmsg), ai)
    from .terrain import HeightMap, fort_points
    terrain = HeightMap()
    terrain.set_forts(fort_points(buildings_from_message(bmsg)))
    frames, events, bstates, last_t = [], [], [], -1.0
    for m in msgs:
        if m["type"] == "state":
            terrain.add_state(m)
        if m["type"] == "state" and m.get("buildings"):
            bstates.append({"t": m["t"], "b": m["buildings"]})  # [id, health, owner, garrisoned] every 2 s
        if m["type"] == "state":
            if m["t"] - last_t < 0.99:  # one frame per second is plenty for a replay
                continue
            last_t = m["t"]
            frames.append({"t": m["t"], "phase": m.get("phase"), "u": [
                [u["id"], round(u["x"], 1), round(u["y"], 1), round(u.get("h") or 0), round(u.get("w") or 0, 1),
                 u.get("men") or 0,
                 (1 if u.get("rout") or u.get("shat") else 0) | (2 if u.get("ctl") else 0) | (4 if u.get("gar") else 0),
                 round(u.get("z") or 0)]
                for u in m["units"] if u.get("x") is not None and not u.get("gone")]})
        elif m["type"] == "event" and m.get("name") != "BattleUnitAttacksEnemyUnit":
            events.append({"t": m.get("tick", 0) * 0.5, "name": m.get("name")})
    plans = [{"t": p["t"], "asked_t": p.get("asked_t"), "plan": p["plan"], "info": p.get("info"),
              "problems": p.get("problems"), "report": p.get("report")}
             for p in _read_jsonl(runtime / f"plans-{bid}.jsonl")]
    orders = _read_jsonl(runtime / f"orders-{bid}.jsonl")
    ab_used = [{"t": o["t"], "unit": c["unit"], "ability": c["ability"]}
               for o in orders for c in o.get("orders", []) if c.get("cmd") == "ability"]
    return json.dumps({"ab_used": ab_used, "timings": ability_timings(),"battle": bid, "ai": ai, "units": units, "frames": frames, "events": events,
                       "plans": plans, "orders": orders, "general": meta.get("general"),
                       "buildings": buildings, "bstates": bstates, "terrain": terrain.cells()}).encode()


# ---------------------------------------------------------------- http

def lan_addresses() -> list[str]:
    addrs = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addrs.add(ip)
    except OSError:
        pass
    return sorted(addrs)


def serve(board: Board, port: int = 8765, host: str = "127.0.0.1", lan: list[str] | None = None) -> ThreadingHTTPServer:
    # Only answer requests addressed to us by IP / localhost: a page on some other site cannot then read the map
    # through a DNS name re-pointed at this machine (DNS rebinding).
    allowed = {f"{h}:{port}" for h in ["127.0.0.1", "localhost", *(lan or [])]}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Host", "") not in allowed:
                self.send_error(403, "unexpected Host header")
                return
            url = urlparse(self.path)
            try:
                if url.path == "/data":
                    body, ctype = board.snapshot(), "application/json"
                elif url.path == "/battles":
                    body, ctype = json.dumps(battles()).encode(), "application/json"
                elif url.path == "/replay":
                    body, ctype = replay(parse_qs(url.query).get("id", [""])[0]), "application/json"
                elif re.fullmatch(r"/icon/[a-z0-9_]+\.png", url.path):
                    from .gamedata import ICONS
                    body, ctype = (ICONS / url.path[len("/icon/"):]).read_bytes(), "image/png"
                elif url.path in ("/", "/index.html"):
                    body, ctype = PAGE.read_bytes(), "text/html; charset=utf-8"
                else:
                    self.send_error(404)
                    return
            except (FileNotFoundError, StopIteration, OSError):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "max-age=86400" if ctype == "image/png" else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # quiet
            pass

    httpd = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
