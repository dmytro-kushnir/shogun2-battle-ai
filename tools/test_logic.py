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


def test_coastal_batteries():
    from s2ai import report
    from s2ai.gamedata import battery_stats
    # game data: building coastal_batteries_<size> <- crew Boshin_Art_Coastal_Guns_<Size> <- boshin_coastal_cannon_<size>
    stats = {"Boshin_Art_Coastal_Guns_Medium": {"guns": 6}, "Boshin_Art_Coastal_Guns__Large_MP": {"guns": 8}}
    shots = {"boshin_coastal_cannon_medium": ["p_med"], "boshin_coastal_cannon_large_mp": ["p_large"]}
    proj = {"p_med": {"shot": "solid_shot", "range": 1000, "damage": 200, "reload": 8},
            "p_large": {"shot": "solid_shot", "range": 1100, "damage": 300, "reload": 9}}
    bs = battery_stats(stats, shots, proj)
    assert bs["coastal_batteries_medium"] == {"kind": "battery", "guns": 6, "shot": "solid_shot", "range": 1000,
                                              "damage": 200, "reload": 8}, bs
    assert bs["coastal_batteries_large"]["guns"] == 8 and bs["coastal_batteries_large"]["range"] == 1100, bs
    # battle 20261112-192928: the frigate took damage from a battery at 862 m while no ship could reach it
    static = {10001: {"id": 10001, "alliance": 1, "type": "Frigate", "men0": 205, "ship": True},
              20001: {"id": 20001, "alliance": 2, "type": "Corvette", "men0": 80, "ship": True}}
    state = {"t": 130, "phase": "conflict",
             "units": [{"id": 10001, "x": -507, "y": -198, "men": 203, "hull": 0.01},
                       {"id": 20001, "x": -400, "y": 650, "men": 80, "hull": 0}]}
    buildings = {4: {"id": 4, "name": "coastal_batteries_medium", "owner": 2, "x": -87, "y": 555, "hp": 1,
                     "mil": True, "cap": 18},
                 7: {"id": 7, "name": "coastal_batteries_medium_mp", "owner": 1, "x": -500, "y": -900, "hp": 1,
                     "mil": True, "cap": 18},
                 9: {"id": 9, "name": "village_house_01", "owner": 0, "x": 0, "y": 0, "mil": False}}
    text, _ = report.build(static, state, 1, [], None, None, buildings, unit_stats={**bs})
    assert "COASTAL BATTERIES" in text, text
    assert "b4    at (  -87,  555) ENEMY: 6 guns, range 1000m" in text, text
    assert "can hit your 10001 (862m)" in text, text
    assert "IN RANGE OF ENEMY BATTERY b4 862m" in text, text
    assert "b7" in text and "yours" in text and "village" not in text, text
    assert "FORTIFICATIONS" not in text, text   # at sea: no siege section


def test_siege_assault_and_tempo():
    from s2ai import report
    from s2ai.server import goes_forward
    static = {1: {"id": 1, "alliance": 1, "type": "Boshin_Inf_Line_Infantry", "men0": 160, "range": 125, "ammo0": 30},
              2: {"id": 2, "alliance": 1, "type": "Boshin_Cav_Sword_Sabre_Cavalry", "men0": 60, "cav": True},
              3: {"id": 3, "alliance": 1, "type": "Boshin_Inf_Spear_Levy", "men0": 160},
              9: {"id": 9, "alliance": 2, "type": "Boshin_Inf_Line_Red_Bear_Infantry", "men0": 160, "range": 125,
                  "ammo0": 30}}
    state = {"t": 100, "phase": "conflict", "units": [{"id": 1, "x": 0, "y": 400, "men": 160},
                                                       {"id": 2, "x": 30, "y": 420, "men": 60},
                                                       {"id": 3, "x": -30, "y": 400, "men": 160},
                                                       {"id": 9, "x": 0, "y": 150, "men": 160}]}
    walls = {i: {"id": i, "name": f"castle_wall_{i}", "owner": 2, "x": 20 * i - 200, "y": 140, "hp": 1, "mil": True,
                 "cap": 40} for i in range(1, 25)}
    walls[40] = {"id": 40, "name": "castle_gate_02", "owner": 2, "x": 26, "y": 140, "hp": 0, "mil": True, "cap": 68}
    walls[38] = {"id": 38, "name": "castle_gate_01", "owner": 2, "x": 66, "y": 0, "hp": 1, "mil": True, "cap": 68}
    text, units = report.build(static, state, 1, [], None, None, walls, idle_s=120)
    assert "SIEGE - YOU ATTACK" in text and "FORTIFICATIONS" not in text, text
    assert "1 gates / wall pieces are damaged" in text and "cannot march through" in text, text
    # climb points are spread out (walls here are 20 m apart in a row): 5 points, 60 m+ apart
    pts = [ln for ln in text.splitlines() if " wall  at" in ln]
    assert len(pts) == 5, text
    xs = sorted(int(ln.split("at (")[1].split(",")[0]) for ln in pts)
    assert all(b - a >= 60 for a, b in zip(xs, xs[1:])), xs
    assert "Defenders' missiles reach up to 125 m" in text, text
    # a hill castle: an inner ring 8 m higher is a second level with its own climb points
    walls2 = dict(walls)
    walls2.update({i: {"id": i, "name": f"castle_wall_{i}", "owner": 2, "x": 20 * (i - 100), "y": 60, "z": 8, "hp": 1,
                       "mil": True, "cap": 40} for i in range(100, 106)})
    t2, _ = report.build(static, state, 1, [], None, None, walls2)
    assert "2 levels" in t2 and "top level: climb points" in t2 and "outer level: climb points" in t2, t2
    assert "Your army by role: 1 missile infantry, 1 shock cavalry, 1 spears" in text, text
    assert "TEMPO: no assault" in text and "task assault" in text, text
    for b in walls.values():   # the same castle defended: full list, no tempo nagging
        b["owner"] = 1
    text, _ = report.build(static, state, 1, [], None, None, walls, idle_s=120)
    assert "FORTIFICATIONS" in text and "TEMPO" not in text, text
    assert "TEMPO" not in report.build(static, state, 1, [], None, None, None, idle_s=30)[0]
    # what counts as going forward: a third of the army attacking or advancing 60 m+ toward the enemy
    hold = {"groups": [{"units": [1, 2, 3], "task": "hold", "position": None}]}
    assert not goes_forward(hold, units, 1)
    assert goes_forward({"groups": [{"units": [2], "task": "attack", "target": 9},
                                    {"units": [1, 3], "task": "hold"}]}, units, 1)
    assert goes_forward({"groups": [{"units": [1, 3], "task": "move", "position": [0, 300]}]}, units, 1)
    assert not goes_forward({"groups": [{"units": [1, 3], "task": "move", "position": [0, 430]}]}, units, 1)


def test_assault():
    from s2ai.server import goes_forward
    # a hill castle around (0, 0): outer ring (alt 0) radius 120, inner ring (alt 8) radius 50
    import math as m
    blds = {}
    for i in range(36):
        a = m.radians(i * 10)
        blds[i + 1] = {"id": i + 1, "name": f"castle_wall_{i}", "owner": 1, "x": 120 * m.sin(a), "y": 120 * m.cos(a),
                       "z": 0, "hp": 1}
    for i in range(12):
        a = m.radians(i * 30)
        blds[50 + i] = {"id": 50 + i, "name": f"castle_wall_in_{i}", "owner": 1, "x": 50 * m.sin(a),
                        "y": 50 * m.cos(a), "z": 8, "hp": 1}
    inf = [unit(10 + i, 2, -60 + 30 * i, 220, "inf", 160, "Inf_Spear_Yari_Ashigaru") for i in range(4)]
    bow = unit(20, 2, 0, 260, "inf", 120, "Inf_Missile_Bow_Ashigaru", True)
    cav = unit(21, 2, 50, 280)
    foe = unit(30, 1, 0, 60, "inf", 100, "Inf_Missile_Bow_Ashigaru", True)
    us = inf + [bow, cav, foe]
    ex = Executor(2, TIMINGS, STATS)
    ex.set_buildings(blds)
    ex.set_plan(plan_of(group([10, 11, 12, 13, 20, 21], task="assault")), us, 0)
    o = {x["unit"]: x for x in ex.tick(us, 0)[1]}
    climbs = [o[i]["building"] for i in (10, 11, 12, 13)]
    assert all(o[i]["cmd"] == "climb" for i in (10, 11, 12, 13)), o
    assert len(set(climbs)) == 4 and all(b <= 36 for b in climbs), climbs  # four different outer sections
    pts = [(blds[b]["x"], blds[b]["y"]) for b in climbs]
    assert all(m.dist(p, q) >= 40 for p in pts for q in pts if p != q), pts  # spread out
    assert o[20]["cmd"] == "attack" and o[20]["target"] == 30, o            # bows shoot the defenders
    assert 21 not in o, o                                                   # cavalry: no breach, waits
    # unit 10 is over the outer ring: it climbs the inner one; unit 11 is past both: it takes the top
    inf[0].x, inf[0].y, inf[0].z = -40, 80, 2
    inf[1].x, inf[1].y, inf[1].z = 10, 20, 9
    o = {x["unit"]: x for x in ex.tick(us, 1)[1]}
    assert o[10]["cmd"] == "climb" and o[10]["building"] >= 50, o
    assert o[11]["cmd"] == "occupy", o
    # damaged pieces are no way in (siege 201310): cavalry still waits, nobody is sent "through"
    blds[3]["hp"] = 0
    o = {x["unit"]: x for x in ex.tick(us, 2)[1]}
    assert 21 not in o and o.get(12, {}).get("cmd") != "occupy", o
    # a unit up on the wall it climbed is not sent up again (siege 201310: 12 -> 5 -> 12 m for 250 s)
    ex = Executor(2, TIMINGS, STATS)
    ex.set_buildings(blds)
    w = blds[1]
    climber = unit(40, 2, w["x"], w["y"] + 30, "inf", 160, "Inf_Spear_Yari_Ashigaru")
    raw = plan_of(group([40], task="climb"))
    raw["groups"][0]["building"] = 1
    ex.set_plan(raw, [climber, foe], 0)
    assert ex.tick([climber, foe], 0)[1][0]["cmd"] == "climb"
    assert ex.tick([climber, foe], 30)[1] == []          # standing at the ladder: no repeat
    climber.y, climber.z = w["y"] + 2, 7                  # up on the wall
    assert ex.tick([climber, foe], 60)[1] == []
    # TEMPO: attacking a castle, marching does not count, assault does
    out = [unit(10 + i, 2, -60 + 30 * i, 220, "inf", 160, "Inf_Spear_Yari_Ashigaru") for i in range(4)] + [foe]
    march = {"groups": [{"units": [10, 11, 12, 13], "task": "move", "position": [0, 130]}]}
    assert goes_forward(march, out, 2) and not goes_forward(march, out, 2, siege_attack=True)
    assert goes_forward({"groups": [{"units": [10, 11, 12, 13], "task": "assault"}]}, out, 2, siege_attack=True)


def test_missile_line_width():
    from s2ai.executor import line_width
    gun = unit(1, 2, 0, 0, "inf", 120, "Inf_Missile_Matchlock_Samurai", True)
    gun.w = 20.0                        # siege 20261113-193825: 6 deep
    assert line_width(gun) == 60.0      # two ranks
    gun.men, gun.w = 200, 50.0
    assert line_width(gun) == 80.0      # capped
    spear = unit(2, 2, 0, 0, "inf", 160, "Inf_Spear_Yari_Samurai")
    spear.w = 30.0
    assert line_width(spear) == 30.0    # melee keeps its formation
    ex = Executor(2, TIMINGS, STATS)
    ex.set_plan(plan_of(group([1], task="hold")), [gun], 0)
    o = ex.tick([gun], 0)[1]
    assert o and o[0]["cmd"] == "move" and o[0]["width"] == 80.0, o  # standing in place, but too deep: re-form


def test_ai_alliance():
    from s2ai.server import guess_ai_alliance
    arm = lambda *a: {"armies": [{"alliance": al, "army": n, "handicap": h} for al, n, h in a]}
    assert guess_ai_alliance(arm((1, 1, 2), (2, 1, -2))) == 1
    assert guess_ai_alliance(arm((1, 1, -2), (2, 1, 2))) == 2
    # Sekigahara: the player and two allied AI armies against two AI armies
    assert guess_ai_alliance(arm((1, 1, -2), (1, 2, 2), (1, 3, 2), (2, 1, 2), (2, 2, 2))) == 2
    # campaign 20261113-185839: the player's AI ally (+2) made a tie, and the general took the player's army
    assert guess_ai_alliance(arm((1, 1, 2), (2, 1, -2), (2, 2, 2))) == 1
    assert guess_ai_alliance(arm((1, 1, 0), (2, 1, 0))) == 2   # nothing to go by


def test_gameai_opponent():
    from types import SimpleNamespace
    from s2ai.server import GameAIOpponent, NoCommander
    static = {i: {"id": i, "alliance": a, "type": "Inf_Spear_Yari_Ashigaru", "men0": 100}
              for i, a in ((10001, 1), (10002, 1), (20001, 2), (20002, 2))}
    pos = {10001: (0, 0), 10002: (40, 0), 20001: (500, 300), 20002: (60, 200)}
    state = {"t": 30.0, "tick": 60, "units": [{"id": i, "x": x, "y": y, "men": 100} for i, (x, y) in pos.items()]}
    battle = SimpleNamespace(static=static, state=state, phase="conflict", buildings=None)
    opp = GameAIOpponent(2)   # we command alliance 2: the game's AI gets alliance 1
    assert opp.decide(battle) == [{"cmd": "ai_attack", "alliance": 1, "target": 20002}]
    assert opp.decide(battle) == []                  # not again before RETARGET_S
    battle.phase, state["t"] = "deployment", 2.0
    assert GameAIOpponent(2).decide(battle) == []    # nothing while deploying
    # a siege: one order for the whole battle; the human attacks -> take the castle, defends -> hold it
    battle.phase, state["t"] = "conflict", 30.0
    battle.buildings = {1: {"name": "castle_02_gate_02", "mil": True, "x": 60, "y": 200, "owner": 2},
                        2: {"name": "castle_02_wall_10_01", "mil": True, "x": 100, "y": 200, "owner": 2},
                        3: {"name": "village_house_01", "mil": False, "x": 0, "y": 0, "owner": 0}}
    opp = GameAIOpponent(2)
    o = opp.decide(battle)
    assert len(o) == 1 and o[0]["cmd"] == "ai_capture" and o[0]["alliance"] == 1 and o[0]["x"] == 80, o
    state["t"] = 50.0
    assert opp.decide(battle) == []
    for b in battle.buildings.values():
        b["owner"] = 1 if b["owner"] else 0
    o = GameAIOpponent(2).decide(battle)
    assert o[0]["cmd"] == "ai_defend" and o[0]["radius"] == 40, o
    nc = NoCommander(2)
    assert nc.decide(battle) == ([], []) and nc.decide(battle) is None  # releases everything once


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name}: ok")
    print("ALL OK")
