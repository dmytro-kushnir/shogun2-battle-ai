"""Unit tests for the server's own logic - no game, no recorded battle, no model:
usage-limit parsing, plan validation, the executor (waypoints, abilities, cavalry hit-and-run, matchups).

    .venv\\Scripts\\python tools\\test_logic.py      -> ALL OK
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from s2ai.executor import Executor  # noqa: E402
from s2ai.general import limit_reset  # noqa: E402
from s2ai.plan import validate  # noqa: E402
from s2ai.report import U  # noqa: E402

# a few unit types with the stats the executor looks at (the real ones come from the game's data.pack)
STATS = {
    "Cav_Sword_Katana_Cavalry": {"melee": 14, "vs_cav": 0},
    "Inf_Spear_Yari_Ashigaru": {"melee": 5, "vs_cav": 20},
    "Boshin_Inf_Spear_Kyoto_Police": {"melee": 8, "vs_cav": 20},
    "Inf_Sword_Katana_Samurai": {"melee": 12, "vs_cav": 0},
    "Boshin_Art_Armstrong_Guns": {"melee": 4, "vs_cav": 0},
}
TIMINGS = {"rapid_advance": {"active": 30.0, "recharge": 180.0, "uses": -1}}


def unit(i, al, x, y, kind="cav", men=80, typ="Cav_Sword_Katana_Cavalry", missile=False):
    return U(id=i, alliance=al, type=typ, kind=kind, missile=missile, general=False, men=men, men0=men, x=x, y=y,
             h=0, w=30, moving=False, running=False, routing=False, shattered=False, gone=False, ammo=None, range=0)


def group(units, task="attack", target=None, engage=0, via=None):
    return {"name": "g", "units": units, "task": task, "target": target, "building": None, "position": None,
            "via": via, "facing": None, "run": True, "engage_within_m": engage, "keep_range_m": 0}


def plan_of(*groups, abilities=()):
    return {"intent": "", "groups": list(groups), "abilities": list(abilities), "next_plan_in_s": 8}


def test_limit_reset():
    now = dt.datetime(2026, 11, 10, 9, 18).timestamp()
    at = lambda s: dt.datetime.fromtimestamp(limit_reset(s, now))  # noqa: E731
    r = at("You've hit your 5-hour limit · resets 12:10pm (Europe/Kyiv)")
    assert (r.hour, r.minute) == (12, 11), r  # a minute's margin
    r = at("You've hit your weekly limit · resets Nov 14, 3pm (Europe/Kyiv)")
    assert (r.month, r.day, r.hour) == (11, 14, 15), r
    r = at("You've hit your 5-hour limit · resets 8am")
    assert (r.day, r.hour) == (11, 8), r  # 8am already passed today: tomorrow
    assert limit_reset("Claude AI usage limit reached|1794000000", now) == 1794000060
    assert 25 * 60 < limit_reset("You've hit your limit", now) - now < 35 * 60  # no time given: wait a while
    assert limit_reset("API Error: 500 overloaded", now) is None
    # other wordings the CLI uses
    r = at("5-hour limit reached ∙ resets 3pm")
    assert (r.day, r.hour) == (10, 15), r
    r = at("Weekly limit reached ∙ resets Mon 9am")  # 2026-11-10 is a Tuesday: next Monday
    assert (r.month, r.day, r.hour) == (11, 16, 9), r
    r = at("Claude usage limit reached. Your limit will reset at 3pm (Europe/Kyiv).")
    assert (r.day, r.hour) == (10, 15), r
    assert 25 * 60 < limit_reset("You've hit your limit · resets Feb 30, 3pm", now) - now < 35 * 60  # no crash


def test_validate_and_waypoints():
    ex = Executor(2, TIMINGS, STATS)
    cav = [unit(1, 2, 0, -300), unit(2, 2, 40, -300)]
    enemy = [unit(9, 1, 0, 300, "inf", 200, "Inf_Sword_Katana_Samurai")]
    raw = plan_of(group([1, 2], target=9, via=[[-300, 280], [-300, 400]]),
                  abilities=[{"unit": 1, "ability": "rapid_advance"}])
    raw["reasoning"] = ""
    plan, probs = validate(raw, {1, 2}, {9}, set(), {1: ["rapid_advance"], 2: []})
    assert plan["groups"][0]["via"] == [[-300, 280], [-300, 400]], plan
    assert not ex.set_plan(plan, cav + enemy, 10)
    _, orders = ex.tick(cav + enemy, 10)
    assert orders[0]["cmd"] == "move" and abs(orders[0]["x"] + 300) < 40 and abs(orders[0]["y"] - 280) < 40, orders
    assert any(o["cmd"] == "ability" for o in orders)
    assert ex.ability_status(1, "rapid_advance", 20)[0] == "active"
    assert ex.ability_status(1, "rapid_advance", 100) == ("recharging", 90.0)
    assert ex.ability_status(1, "rapid_advance", 200)[0] == "ready"
    # the general repeats the plan, ability included: not sent again while active
    probs = ex.set_plan(plan, cav + enemy, 30)
    assert probs and "active" in probs[0], probs
    assert not any(o["cmd"] == "ability" for o in ex.tick(cav + enemy, 30)[1])
    # through waypoint 1 -> on to waypoint 2, and a repeated plan does not send them back
    for u in cav:
        u.x, u.y = ex.via_slots[u.id][0][:2]
    assert all(abs(o["y"] - 400) < 40 for o in ex.tick(cav + enemy, 40)[1] if o["cmd"] == "move")
    ex.set_plan(plan, cav + enemy, 45)
    for u in cav:
        u.y += 60
    assert all(abs(o["y"] - 400) < 40 for o in ex.tick(cav + enemy, 46)[1] if o["cmd"] == "move")
    for u in cav:
        u.x, u.y = ex.via_slots[u.id][1][:2]
    orders = ex.tick(cav + enemy, 60)[1]
    assert orders and all(o["cmd"] == "attack" for o in orders), orders
    # unknown units are dropped with a problem
    _, probs = validate(plan_of(group([1, 20013], target=9)), {1, 2}, {9}, set(), {})
    assert probs and "20013" in probs[0], probs


def test_cavalry_hit_and_run():
    plan = plan_of(group([1], target=9))
    ex = Executor(2, TIMINGS, STATS)
    cav = unit(1, 2, 0, 0)
    spears = unit(9, 1, 0, 20, "inf", 200, "Inf_Spear_Yari_Ashigaru")
    other = unit(8, 1, 60, 60, "inf", 200, "Inf_Sword_Katana_Samurai")
    us = [cav, spears, other]
    ex.set_plan(plan, us, 0)
    assert ex.tick(us, 0)[1][0]["cmd"] == "attack"
    assert not ex.tick(us, 5)[1]                  # charging: same order
    o = ex.tick(us, 6.5)[1]                       # 6.5 s on spears: pull back...
    assert o and o[0]["cmd"] == "move" and o[0]["y"] < -50, o   # ...away from them
    assert "pulling back" in ex.unit_note(1)
    cav.x, cav.y = o[0]["x"], o[0]["y"]
    o = ex.tick(us, 15)[1]
    assert o and o[0]["cmd"] == "attack", o       # back: charge again
    assert ex.unit_note(1) == ""

    # cutting down a gun crew: stays in, unless it loses too many men
    plan = plan_of(group([1], target=7))
    ex = Executor(2, TIMINGS, STATS)
    cav = unit(1, 2, 0, 0)
    gun = unit(7, 1, 0, 15, "art", 40, "Boshin_Art_Armstrong_Guns", True)
    ex.set_plan(plan, [cav, gun], 0)
    for t in range(0, 40, 2):
        assert not any(x["cmd"] == "move" for x in ex.tick([cav, gun], t)[1]), t
    cav.men = 60
    o = ex.tick([cav, gun], 41)[1]
    assert o and o[0]["cmd"] == "move", o

    # battle 141245: sent at a gun 60 m off, caught by the spears guarding it -> pulls back after 6 s
    ex = Executor(2, TIMINGS, STATS)
    cav = unit(1, 2, 0, 0)
    gun = unit(7, 1, 0, 60, "art", 40, "Boshin_Art_Armstrong_Guns", True)
    police = unit(12, 1, 5, 16, "inf", 200, "Boshin_Inf_Spear_Kyoto_Police")
    us = [cav, gun, police]
    ex.set_plan(plan, us, 0)
    assert ex.tick(us, 0)[1][0]["cmd"] == "attack"
    assert not ex.tick(us, 3)[1]
    o = ex.tick(us, 6.5)[1]
    assert o and o[0]["cmd"] == "move", o
    assert ex.pullbacks[-1]["foe"] == 12, ex.pullbacks

    # a new non-attack order ends a pull-back at once
    ex.set_plan(plan_of(group([1], task="hold")), us, 7)
    ex.tick(us, 7)
    assert 1 not in ex.pullback


def test_matchups():
    # the gun breaks; the police spears are nearest, another gun 150 m on: the cavalry takes the gun
    ex = Executor(2, TIMINGS, STATS)
    cav = unit(1, 2, 0, 0)
    gun1 = unit(7, 1, 0, 10, "art", 40, "Boshin_Art_Armstrong_Guns", True)
    gun1.routing = True
    police = unit(12, 1, 30, 30, "inf", 120, "Boshin_Inf_Spear_Kyoto_Police")
    gun2 = unit(6, 1, 0, 160, "art", 40, "Boshin_Art_Armstrong_Guns", True)
    us = [cav, gun1, police, gun2]
    ex.set_plan(plan_of(group([1], target=7)), us, 0)
    o = ex.tick(us, 0)[1]
    assert o[0]["cmd"] == "attack" and o[0]["target"] == 6, o
    # spears with an engage reflex go for cavalry over closer infantry
    sp = unit(30, 2, 0, 0, "inf", 200, "Inf_Spear_Yari_Ashigaru")
    foe_inf = unit(31, 1, 10, 15, "inf", 160, "Inf_Sword_Katana_Samurai")
    foe_cav = unit(32, 1, -25, 25)
    ex = Executor(2, TIMINGS, STATS)
    ex.set_plan(plan_of(group([30], task="hold", engage=50)), [sp, foe_inf, foe_cav], 0)
    assert ex.tick([sp, foe_inf, foe_cav], 0)[1][0]["target"] == 32
    # cavalry does not ride into spears by reflex
    cav = unit(1, 2, 0, 0)
    spears = unit(9, 1, 0, 30, "inf", 200, "Inf_Spear_Yari_Ashigaru")
    ex = Executor(2, TIMINGS, STATS)
    ex.set_plan(plan_of(group([1], task="hold", engage=60)), [cav, spears], 0)
    assert not any(o["cmd"] == "attack" for o in ex.tick([cav, spears], 0)[1])


def test_ship_shot_types():
    ships = [unit(1, 2, 0, 0, "ship", 70, "Boshin_Ironclad_Kotetsu"), unit(9, 1, 0, 800, "ship", 90, "X")]
    raw = plan_of(group([1], target=9))
    raw["groups"][0]["shot"] = "segment_shell"
    plan, _ = validate(raw, {1}, {9}, set(), {})
    assert plan["groups"][0]["shot"] == "segment_shell", plan
    bad, _ = validate({**raw, "groups": [{**raw["groups"][0], "shot": "x; os.exit()"}]}, {1}, {9}, set(), {})
    assert bad["groups"][0]["shot"] is None
    ex = Executor(2, TIMINGS, STATS)
    ex.set_plan(plan, ships, 0)
    o = ex.tick(ships, 0)[1]
    assert {"cmd": "shot_type", "unit": 1, "shot": "segment_shell"} in o, o
    assert not any(x["cmd"] == "shot_type" for x in ex.tick(ships, 1)[1])  # loaded once
    assert "segment_shell" in ex.unit_note(1)
    # battle 190434: the target sank while the model was thinking -> attack the nearest, not hold still
    plan, probs = validate(plan_of(group([1], target=10001)), {1}, {9}, set(), {})
    assert plan["groups"][0]["task"] == "attack" and "nearest" in probs[0], (plan, probs)
    ex = Executor(2, TIMINGS, STATS)
    ex.set_plan(plan, ships, 0)
    assert {"cmd": "attack", "unit": 1, "target": 9} == {k: v for k, v in ex.tick(ships, 0)[1][0].items()
                                                         if k in ("cmd", "unit", "target")}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name}: ok")
    print("ALL OK")
