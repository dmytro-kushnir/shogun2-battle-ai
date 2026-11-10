"""End-to-end test without the game: lua/s2ai_bridge.lua runs in a real Lua 5.1 (lupa) against a mock of the
battle-script API, and talks through real files to the real Python side (s2ai.link + the demo commander).

    .venv\\Scripts\\python tools\\test_bridge.py
"""
import pathlib
import sys
import tempfile

import lupa.lua51 as lua51

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from s2ai.link import GameLink  # noqa: E402
from s2ai.server import DemoCommander, guess_ai_alliance  # noqa: E402

MOCK = r"""
local function obj(methods) return setmetatable({}, { __index = methods }) end
local function list(items)
    return obj({ count = function() return #items end, item = function(_, i) return items[i] end })
end

battle_vector = {}
function battle_vector:new()
    local v = { x = 0, y = 0, z = 0 }
    return setmetatable(v, { __index = {
        set = function(s, x, y, z) s.x, s.y, s.z = x, y, z end,
        get_x = function(s) return s.x end, get_y = function(s) return s.y end, get_z = function(s) return s.z end,
    } })
end

MOCK_UNITS, MOCK_CALLS = {}, {}
local function make_unit(name, x, z)
    local u = { pos = battle_vector:new(), name_ = name, controlled = false, dest = nil, target = nil }
    u.pos:set(x, 0, z)
    MOCK_UNITS[#MOCK_UNITS + 1] = u
    return setmetatable(u, { __index = {
        position = function(s) return s.pos end, bearing = function() return 90 end,
        name = function(s) return s.name_ end, type = function(s) return "type_" .. s.name_ end,
        number_of_men_alive = function() return 100 end, initial_number_of_men = function() return 120 end,
        is_routing = function() return false end, is_shattered = function() return false end,
        is_moving = function(s) return s.dest ~= nil or s.target ~= nil end, is_moving_fast = function() return false end,
        is_cavalry = function() return false end, is_infantry = function() return true end,
        is_artillery = function() return false end, ammo_left = function() return 0 end,
        starting_ammo = function() return 0 end, missile_range = function() return 0 end,
        is_leaving_battle = function() return false end, ordered_width = function() return 30 end,
        current_special_ability = function() return "" end, can_perform_special_ability = function() return false end,
    } })
end

MOCK_ARMY_UNITS, MOCK_RELEASES, MOCK_OVER, MOCK_LATE_RELEASES = {}, 0, false, 0
local function make_army(prefix, n, x0, handicap)
    local units = {}
    for i = 1, n do units[i] = make_unit(prefix .. i, x0, i * 30) end
    MOCK_ARMY_UNITS[prefix] = units
    return obj({
        units = function() return list(units) end,
        army_handicap = function() return handicap end,
        create_unit_controller = function()
            MOCK_CONTROLLERS = (MOCK_CONTROLLERS or 0) + 1
            local mine = {}
            local function each(fn) for _, u in ipairs(mine) do fn(u) end end
            return obj({
                add_units = function(_, u) mine[#mine + 1] = u end,
                take_control = function() each(function(u) u.controlled = true end) end,
                release_control = function()
                    MOCK_RELEASES = MOCK_RELEASES + 1
                    if MOCK_OVER then MOCK_LATE_RELEASES = MOCK_LATE_RELEASES + 1 end  -- this crashed the real game
                    each(function(u) u.controlled = false; u.target = nil end)
                end,
                attack_unit = function(_, t, flag, run)
                    MOCK_CALLS[#MOCK_CALLS + 1] = "attack " .. mine[1].name_ .. "->" .. t.name_
                    each(function(u) u.target = t end)
                end,
                goto_location = function(_, p) each(function(u) u.dest = p end) end,
                goto_location_angle_width = function(_, p) each(function(u) u.dest = p end) end,
                halt = function() each(function(u) u.dest = nil; u.target = nil end) end,
                defend_building = function(_, b, run)
                    MOCK_CALLS[#MOCK_CALLS + 1] = "garrison " .. mine[1].name_ .. "->" .. MOCK_BUILDING_NAME(b)
                end,
            })
        end,
    })
end

local player_army, ai_army = make_army("player", 2, -300, -2), make_army("ai", 3, 300, 2)
local alliances = list({
    obj({ armies = function() return list({ player_army }) end }),
    obj({ armies = function() return list({ ai_army }) end }),
})

-- buildings as battle:buildings() lists them: all share the registered battle.building class (like the engine's
-- userdata), plus one item of ANOTHER class (run 14: such items exist; calling methods on them broke the battle)
local FIELDS = {}
local METHODS = {
    name = function(s) return FIELDS[s].name_ end, position = function(s) return FIELDS[s].pos end,
    capacity = function(s) return FIELDS[s].cap end, alliance_owner_id = function(s) return FIELDS[s].owner end,
    health = function(s) return FIELDS[s].hp or 1 end, currently_garrisoned = function() return false end,
    destroy = function() error("the bridge must never call destroy") end,
}
-- like the game (run 16): the methods sit directly in the object's metatable (which also serves as __index),
-- and that metatable is NOT the registry's class table
local BASE = newproxy(true)
local BMT = getmetatable(BASE)
for k, f in pairs(METHODS) do BMT[k] = f end
BMT.central_position = METHODS.position
BMT.is_garrisoned = METHODS.currently_garrisoned
BMT.__index = BMT
debug.getregistry()["battle.building"] = { note = "a different table, as in the game" }
local function make_building(name, x, z, cap, owner)
    local b = newproxy(BASE)
    FIELDS[b] = { name_ = name, pos = battle_vector:new(), cap = cap, owner = owner }
    FIELDS[b].pos:set(x, 120, z)
    return b
end
function MOCK_BUILDING_NAME(b) return FIELDS[b].name_ end
-- change a building mid-battle (damage, capture): every building whose name contains `part`
function MOCK_SET_BUILDING(part, key, value)
    for b, f in pairs(FIELDS) do
        if type(f.name_) == "string" and f.name_:find(part, 1, true) then f[key] = value end
    end
end
MOCK_FOREIGN_TOUCHED = false
local foreign = newproxy(true)
getmetatable(foreign).__index = function(_, k)
    MOCK_FOREIGN_TOUCHED = true
    return function() return function() end end  -- alliance_owner_id() returns a function, as in run 14
end
-- and one building whose name comes back as a function (run 18: the engine returned shifted values)
local broken = make_building(function() end, 0, 0, 0, -1)
local gate, wall = make_building("castle_02_gate_02", 80, 40, 168, 1), make_building("castle_02_wall_10_01", 80, 20, 20, 1)
local all_items = { gate, wall, foreign, broken, make_building("village_house_01", -500, 300, 0, -1) }
local garrisonable = { gate, wall }  -- only buildings that can hold troops
local buildings = obj({
    count = function() return #all_items end,
    item = function(_, i)
        MOCK_FULL_LIST_USED = true  -- reading the full list (scenery) broke the game in runs 17/18
        return all_items[i]
    end,
    garrisonable_count = function() return #garrisonable end,
    garrisonable_item = function(_, i) return garrisonable[i] end,
})

MOCK_TIMERS = {}
empire_battle = {}
function empire_battle:new()
    return obj({
        buildings = function() return buildings end,
        alliances = function() return alliances end,
        register_repeating_timer = function(_, name, ms) MOCK_TIMERS[name] = ms end,
        game_time = function() return 0 end,
        show_advisor_message = function() end,
    })
end

-- like the engine: a destroyed unit is removed and the list closes the gap (positions of later units shift)
function MOCK_KILL(prefix, index) table.remove(MOCK_ARMY_UNITS[prefix], index) end
-- a reinforcement joins an army mid-battle
function MOCK_REINFORCE(prefix, name, x, z) table.insert(MOCK_ARMY_UNITS[prefix], make_unit(name, x, z)) end

events = { BattleDeploymentPhaseCommenced = {}, BattleConflictPhaseCommenced = {}, BattleCompleted = {},
           BattleUnitRouts = {}, BattleCommandingUnitRouts = {}, BattleUnitAttacksEnemyUnit = {} }
function MOCK_FIRE(name) for _, f in ipairs(events[name]) do f({}) end end

-- half a second of simulation: units walk 3 m towards their target or destination
function MOCK_STEP()
    for _, u in ipairs(MOCK_UNITS) do
        local goal = u.target and u.target.pos or u.dest
        if goal then
            local dx, dz = goal.x - u.pos.x, goal.z - u.pos.z
            local d = math.sqrt(dx * dx + dz * dz)
            if d > 3 then u.pos:set(u.pos.x + dx / d * 3, 0, u.pos.z + dz / d * 3) end
        end
    end
end
"""


def main() -> bool:
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)              # stands in for the repository: the bridge uses S2AI_ROOT/runtime
        real_runtime = root / "runtime"
        real_runtime.mkdir()
        rt = lua51.LuaRuntime()
        g = rt.globals()
        rt.execute(MOCK)
        g.S2AI_ROOT = str(root).replace("\\", "/")
        if True:
            g.dofile(str(REPO / "lua" / "s2ai_bridge.lua"))
            link = GameLink(runtime=real_runtime)
            if dict(g.MOCK_TIMERS.items()) != {"S2AI_Bridge_Tick": 500}:
                problems.append(f"timer not registered: {dict(g.MOCK_TIMERS.items())}")
            tick = g.S2AI_Bridge_Tick
            commander = None
            for step in range(1, 81):  # 40 s
                if step == 4:
                    g.MOCK_FIRE("BattleConflictPhaseCommenced")
                if step == 30:
                    g.MOCK_KILL("ai", 1)  # ai1 destroyed: ai2 and ai3 move up in the list
                if step == 35:
                    g.MOCK_REINFORCE("player", "player3", -320, 200)
                if step == 40:
                    g.MOCK_SET_BUILDING("gate", "hp", 0.4)    # the player's guns damage the gate
                    g.MOCK_SET_BUILDING("wall", "owner", 0)   # and capture a wall section (alliance 1, 0-based)
                g.MOCK_STEP()
                tick()
                for msg in link.poll():
                    if msg["type"] == "battle_start":
                        ai = guess_ai_alliance(msg)
                        if ai != 2:
                            problems.append(f"guessed AI alliance {ai}, expected 2")
                        commander = DemoCommander(ai)
                    elif msg["type"] == "applied" and msg["errors"]:
                        problems.append(f"bridge reported errors: {msg['errors']}")
                if commander and link.battle and link.battle.state:
                    decision = commander.decide(link.battle)
                    if decision:
                        link.send(*decision)
            # ids must still mean the same units after the list was compacted: compare final positions by name
            by_name = {u.name_: u for u in g.MOCK_UNITS.values()}
            static = link.battle.static
            for su in link.battle.state["units"]:
                mock = by_name.get(static.get(su["id"], {}).get("name"))
                if mock is None:
                    problems.append(f"unit id {su['id']} has no static info (reinforcement not announced?)")
                elif abs(mock.pos.x - su["x"]) > 0.01 or abs(mock.pos.z - su["y"]) > 0.01:
                    problems.append(f"unit id {su['id']} ({mock.name_}) points at another unit")
            if "player3" not in {u.get("name") for u in static.values()}:
                problems.append("the reinforcement player3 never got static info")

            # sieges: the building list arrives, and a garrison order reaches the engine
            blds = link.battle.buildings or {}
            gate = next((b for b in blds.values() if "gate" in b["name"]), None)
            if gate is None or not gate["mil"] or gate["owner"] != 2:
                problems.append(f"fortifications not streamed as expected: {list(blds.values())[:2]}")
            if g.MOCK_FOREIGN_TOUCHED or g.MOCK_FULL_LIST_USED:
                problems.append("the bridge walked the full building list (scenery broke the game in runs 17/18)")
            if sorted(b["name"] for b in blds.values()) != ["castle_02_gate_02", "castle_02_wall_10_01"]:
                problems.append(f"expected the 2 garrisonable buildings, got {[b['name'] for b in blds.values()]}")
            # live status: the gate's damage and the captured wall must have reached the server
            by_nm = {b["name"]: b for b in blds.values()}
            if by_nm.get("castle_02_gate_02", {}).get("hp") != 0.4:
                problems.append(f"gate damage not updated: {by_nm.get('castle_02_gate_02')}")
            if by_nm.get("castle_02_wall_10_01", {}).get("owner") != 1:
                problems.append(f"wall capture not updated: {by_nm.get('castle_02_wall_10_01')}")
            if gate is not None:
                ai_id = next(u["id"] for u in link.battle.state["units"] if static[u["id"]]["alliance"] == 2)
                link.send([u["id"] for u in link.battle.state["units"] if static[u["id"]]["alliance"] == 2],
                          [{"cmd": "garrison", "unit": ai_id, "building": gate["id"], "run": True}])
                tick()
                link.poll()
                if not any(c.startswith("garrison") for c in g.MOCK_CALLS.values()):
                    problems.append(f"garrison order did not reach the engine: {link.battle.applied[-1:]}")

            g.MOCK_OVER = True
            g.MOCK_FIRE("BattleCompleted")
            link.poll()
            tick()  # the timer may still fire once after the battle: must not touch the engine
            # no controller may ever be garbage-collected (its destructor corrupted the game's heap at exit)
            kept = len(g.S2AI_CONTROLLER_GRAVEYARD)
            if kept < g.MOCK_CONTROLLERS:
                problems.append(f"{g.MOCK_CONTROLLERS} controllers created but only {kept} kept alive")
            if g.MOCK_LATE_RELEASES:
                problems.append(f"release_control called {g.MOCK_LATE_RELEASES}x after the battle ended "
                                "(crashed the real game)")

            calls = list(g.MOCK_CALLS.values())
            units = list(g.MOCK_UNITS.values())
            controlled_names = [u.name_ for u in units if u.controlled]
            print(f"battle {link.battle.id}: phase={link.battle.phase} ended={link.battle.ended}")
            print(f"states received: {link.battle.state['tick']}, orders applied: {len(link.battle.applied)}")
            print(f"attack calls: {calls[:6]}{' ...' if len(calls) > 6 else ''}")
            ai_units = [u for u in units if u.name_.startswith("ai")]
            moved = [round(300 - u.pos.x) for u in ai_units]
            print(f"AI units moved towards the player by (m): {moved}")
            if not any(c.startswith("attack ai") for c in calls):
                problems.append("no attack orders reached the AI units")
            if any(c.startswith("attack player") for c in calls):
                problems.append("player units were given orders")
            if min(moved) < 30:
                problems.append("AI units did not advance")
            # at battle end the bridge must NOT call release_control (the engine is tearing units down), only
            # forget its controllers
            if len(g.S2AI_BRIDGE.controlled):
                problems.append(f"bridge still holds controllers after the battle: {list(g.S2AI_BRIDGE.controlled)}")
            if not link.battle.ended:
                problems.append("battle_end not received")
            log = (real_runtime / "bridge.log").read_text() if (real_runtime / "bridge.log").exists() else ""
            if "ERROR" in log:
                problems.append("bridge.log has errors:\n" + log)

            # hostile orders files: must be refused / cut off, never hang or run code
            read_orders = g.S2AI_BRIDGE._read_orders
            orders = real_runtime / "orders.lua"
            cases = {
                "endless loop": b"while true do end return {}",
                "bytecode": None,  # written by Lua itself below (string.dump output is binary)
                "reaches globals": b"return { leaked = os ~= nil or io ~= nil }",
                "too big": b"return {}" + b" " * 1_100_000,
            }
            for name, body in cases.items():
                if body is None:
                    g.BYTECODE_PATH = str(orders)
                    rt.execute('local f = io.open(BYTECODE_PATH, "wb") '
                               'f:write(string.dump(function() return {} end)) f:close()')
                else:
                    orders.write_bytes(body)
                result = read_orders()
                if name == "reaches globals":
                    if result is None or result["leaked"]:
                        problems.append("orders file could reach globals")
                elif result is not None:
                    problems.append(f"orders file accepted: {name}")
            print("hostile orders files: refused / sandboxed as expected" if not any(
                p.startswith("orders file") for p in problems) else "hostile orders files: PROBLEM")
    for p in problems:
        print("FAIL:", p)
    print("\nALL OK" if not problems else "\nFAILED")
    return not problems


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
