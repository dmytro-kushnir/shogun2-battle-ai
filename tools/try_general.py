"""Ask the real general (Claude Code) for one plan on a recorded battle, without the game.

    .venv\\Scripts\\python tools\\try_general.py runtime\\battle-<id>.jsonl [--at 60] [--ai 2] [--model ...]

Prints the report the model sees, the plan, any validation problems and the orders the executor would send.
"""
import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from s2ai import report  # noqa: E402
from s2ai.executor import Executor  # noqa: E402
from s2ai.general import ClaudeCodeGeneral  # noqa: E402
from s2ai.plan import validate  # noqa: E402
from s2ai.server import guess_ai_alliance  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("stream")
ap.add_argument("--at", type=float, default=60.0, help="battle time (s) of the state to plan on")
ap.add_argument("--ai", type=int)
ap.add_argument("--model", default="sonnet")
ap.add_argument("--effort", default="low")
args = ap.parse_args()

msgs = [json.loads(line) for line in pathlib.Path(args.stream).read_text().splitlines() if line.strip()]
start = next(m for m in msgs if m["type"] == "battle_start")
states = [m for m in msgs if m["type"] == "state"]
state = min(states, key=lambda s: abs(s["t"] - args.at))
events = [m for m in msgs if m["type"] == "event" and m.get("tick", 0) * 0.5 <= state["t"]]
ai = args.ai or guess_ai_alliance(start)
static = {u["id"]: u for u in start["units"]}
for m in msgs:
    if m["type"] == "units":
        static.update({u["id"]: u for u in m["units"]})

buildings = report.buildings_from_message(next((m for m in msgs if m["type"] == "buildings"), None))

text, units = report.build(static, state, ai, events, None, None, buildings)
print(text)
print("\n" + "=" * 100)
general = ClaudeCodeGeneral(model=args.model, effort=args.effort)
raw, info = general.plan(text)
general.close()
plan, problems = validate(raw, {u.id for u in units if u.alliance == ai}, {u.id for u in units if u.alliance != ai},
                          {f["id"] for f in report.fortifications(buildings)})
print(f"PLAN in {info['seconds']}s, {info['tokens_in']} in / {info['tokens_out']} out tokens, ~${info['api_equiv_usd']:.3f} API-equivalent")
print(json.dumps(plan, indent=1))
for p in problems:
    print("PROBLEM:", p)
ex = Executor(ai)
ex.set_plan(plan, units, state["t"])
take, orders = ex.tick(units, state["t"])
print(f"\nexecutor: controls {len(take)} units, sends {len(orders)} orders, e.g.:")
for o in orders[:8]:
    print("  ", o)
