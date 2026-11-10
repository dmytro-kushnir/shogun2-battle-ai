"""Ask the real general (Claude Code) for one plan on a recorded battle, without the game.

    .venv\\Scripts\\python tools\\try_general.py runtime\\battle-<id>.jsonl [--at 60] [--ai 2] [--model ...]
    .venv\\Scripts\\python tools\\try_general.py runtime\\battle-<id>.jsonl --at 60 --report-only   # no model call

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
from s2ai.gamedata import ability_timings, unit_abilities, unit_stats  # noqa: E402
from s2ai.general import ClaudeCodeGeneral  # noqa: E402
from s2ai.link import GameLink  # noqa: E402
from s2ai.plan import validate  # noqa: E402
from s2ai.server import guess_ai_alliance  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("stream")
ap.add_argument("--at", type=float, default=60.0, help="battle time (s) of the state to plan on")
ap.add_argument("--ai", type=int)
ap.add_argument("--model", default="sonnet")
ap.add_argument("--effort", default="low")
ap.add_argument("--report-only", action="store_true", help="print the report, do not ask the model")
args = ap.parse_args()

# replay the stream up to --at through the same link the server uses (terrain, morale history, buildings)
link = GameLink.__new__(GameLink)
link.runtime, link.battle = pathlib.Path(args.stream).parent, None
bid = pathlib.Path(args.stream).stem[len("battle-"):]
start = None
for line in pathlib.Path(args.stream).read_text().splitlines():
    if not line.strip():
        continue
    m = json.loads(line)
    start = start or (m if m["type"] == "battle_start" else None)
    link._absorb(bid, m)
    if m["type"] == "state" and m["t"] >= args.at:
        break
b = link.battle
ai = args.ai or guess_ai_alliance(start)
abilities = unit_abilities()
stats = unit_stats()
ex = Executor(ai, ability_timings(), stats)
text, units = report.build(b.static, b.state, ai, b.events, None, None, b.buildings, b.terrain, b.morale, abilities,
                           ability_state=lambda uid, a: ex.ability_text(uid, a, b.state["t"]), unit_stats=stats,
                           unit_note=ex.unit_note)
print(text)
if args.report_only:
    sys.exit(0)
print("\n" + "=" * 100)
general = ClaudeCodeGeneral(model=args.model, effort=args.effort)
raw, info = general.plan(text)
general.close()
plan, problems = validate(raw, {u.id for u in units if u.alliance == ai}, {u.id for u in units if u.alliance != ai},
                          {f["id"] for f in report.fortifications(b.buildings)},
                          {u.id: abilities.get(u.type, []) for u in units if u.alliance == ai})
state = b.state
print(f"PLAN in {info['seconds']}s, {info['tokens_in']} in / {info['tokens_out']} out tokens, ~${info['api_equiv_usd']:.3f} API-equivalent")
print(json.dumps(plan, indent=1))
problems += ex.set_plan(plan, units, state["t"])
for p in problems:
    print("PROBLEM:", p)
take, orders = ex.tick(units, state["t"])
print(f"\nexecutor: controls {len(take)} units, sends {len(orders)} orders, e.g.:")
for o in orders[:8]:
    print("  ", o)
