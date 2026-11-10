"""One summary per battle from what the server recorded (runtime/battle-, plans-, orders-, failures-, meta-<id>),
to compare versions of the general across battles instead of judging replays by eye.

    .venv\\Scripts\\python -m s2ai.summary                 # every recorded battle: table + runtime/battles.csv
    .venv\\Scripts\\python -m s2ai.summary 20261110-091516  # one battle, all fields

The server writes runtime/summary-<id>.json when a battle ends (with what only it knows, e.g. cavalry pull-backs).
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent / "runtime"
DECISIVE = 0.10  # a side whose fighting-fit men fell below this share of its start has lost


def _jsonl(path: Path) -> list[dict]:
    out = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return out


def summarize(bid: str, runtime: Path = RUNTIME, extra: dict | None = None) -> dict | None:
    msgs = _jsonl(runtime / f"battle-{bid}.jsonl")
    states = [m for m in msgs if m.get("type") == "state"]
    if not states:
        return None
    try:
        meta = json.loads((runtime / f"meta-{bid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    static: dict[int, dict] = {}
    for m in msgs:
        if m.get("type") in ("battle_start", "units"):
            for u in m.get("units", []):
                static.setdefault(u["id"], u)
    ai = meta.get("ai") or 2
    last = states[-1]
    end = {u["id"]: u for u in last["units"]}
    first_men: dict[int, int] = {}
    for st in states:  # a unit's strength when first seen (reinforcements arrive later)
        for u in st["units"]:
            first_men.setdefault(u["id"], u.get("men") or 0)

    def side(mine: bool) -> dict:
        ids = [i for i, s in static.items() if (s.get("alliance") == ai) == mine]
        men0 = sum(static[i].get("men0") or first_men.get(i, 0) for i in ids)
        alive = fit = lost = 0
        for i in ids:
            u = end.get(i)
            men = (u.get("men") or 0) if u and not u.get("gone") else 0
            alive += men
            if men and not u.get("rout") and not u.get("shat"):
                fit += men
            else:
                lost += 1
        return {"units": len(ids), "men_start": men0, "men_alive": alive, "men_fighting": fit,
                "casualties": max(0, men0 - alive), "units_lost": lost,
                "fighting_share": round(fit / men0, 3) if men0 else 0.0}
    a, p = side(True), side(False)
    ended = any(m.get("type") == "battle_end" for m in msgs)
    if p["fighting_share"] < DECISIVE <= a["fighting_share"]:
        result = "won"
    elif a["fighting_share"] < DECISIVE <= p["fighting_share"]:
        result = "lost"
    elif ended:
        result = "won on points" if a["fighting_share"] > p["fighting_share"] else "lost on points"
    else:
        result = "left unfinished"

    plans = [x for x in _jsonl(runtime / f"plans-{bid}.jsonl") if "plan" in x]
    fails = _jsonl(runtime / f"failures-{bid}.jsonl")
    infos = [x.get("info") or {} for x in plans]
    orders = _jsonl(runtime / f"orders-{bid}.jsonl")
    out = {
        "battle": bid, "general": meta.get("general", ""), "ai_alliance": ai,
        "duration_s": round(last.get("t", 0)), "ended": ended, "result": result,
        "ai": a, "player": p,
        "loss_ratio": round(p["casualties"] / a["casualties"], 2) if a["casualties"] else None,
        "plans": len(plans), "plan_failures": len(fails),
        "usage_limit_hits": sum(1 for f in fails if f.get("limit")),
        "plan_seconds_avg": round(sum(i.get("seconds", 0) for i in infos) / len(infos), 1) if infos else None,
        "tokens_in": sum(i.get("tokens_in", 0) for i in infos),
        "tokens_out": sum(i.get("tokens_out", 0) for i in infos),
        "api_equiv_usd": round(sum(i.get("api_equiv_usd", 0) for i in infos), 3),
        "plan_problems": sum(len(x.get("problems") or []) for x in plans),
        "abilities_used": sum(1 for o in orders for c in o.get("orders", []) if c.get("cmd") == "ability"),
    }
    out.update(extra or {})
    return out


COLUMNS = [("battle", "battle"), ("general", "general"), ("result", "result"), ("duration_s", "time s"),
           ("ai.units", "AI units"), ("ai.men_start", "AI men"), ("ai.fighting_share", "AI left"),
           ("player.units", "pl units"), ("player.men_start", "pl men"), ("player.fighting_share", "pl left"),
           ("loss_ratio", "kill ratio"), ("plans", "plans"), ("plan_failures", "fails"),
           ("api_equiv_usd", "$ API"), ("tokens_out", "tok out"), ("abilities_used", "abil"),
           ("cavalry_pullbacks", "cav pull")]


def _get(row: dict, path: str):
    v = row
    for k in path.split("."):
        v = v.get(k) if isinstance(v, dict) else None
    return v


def all_summaries(runtime: Path = RUNTIME) -> list[dict]:
    out = []
    for p in sorted(runtime.glob("battle-*.jsonl")):
        bid = p.stem[len("battle-"):]
        saved = runtime / f"summary-{bid}.json"
        try:
            row = json.loads(saved.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            row = summarize(bid, runtime)
        if row:
            out.append(row)
    return out


def main() -> None:
    if len(sys.argv) > 1:
        for bid in sys.argv[1:]:
            print(json.dumps(summarize(bid), indent=1))
        return
    rows = all_summaries()

    def cell(r: dict, k: str) -> str:
        v = _get(r, k)
        return "" if v is None else f"{v:.0%}" if k.endswith("share") else str(v)
    print("| " + " | ".join(h for _, h in COLUMNS) + " |")
    print("|" + "---|" * len(COLUMNS))
    for r in rows:
        print("| " + " | ".join(cell(r, k) for k, _ in COLUMNS) + " |")
    with (RUNTIME / "battles.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([k for k, _ in COLUMNS])
        w.writerows([[_get(r, k) for k, _ in COLUMNS] for r in rows])
    print(f"\n{len(rows)} battles; CSV: {RUNTIME / 'battles.csv'}")


if __name__ == "__main__":
    main()
