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

local function make_army(prefix, n, x0, handicap)
    local units = {}
    for i = 1, n do units[i] = make_unit(prefix .. i, x0, i * 30) end
    return obj({
        units = function() return list(units) end,
        army_handicap = function() return handicap end,
        create_unit_controller = function()
            local mine = {}
            local function each(fn) for _, u in ipairs(mine) do fn(u) end end
            return obj({
                add_units = function(_, u) mine[#mine + 1] = u end,
                take_control = function() each(function(u) u.controlled = true end) end,
                release_control = function() each(function(u) u.controlled = false; u.target = nil end) end,
                attack_unit = function(_, t, flag, run)
                    MOCK_CALLS[#MOCK_CALLS + 1] = "attack " .. mine[1].name_ .. "->" .. t.name_
                    each(function(u) u.target = t end)
                end,
                goto_location = function(_, p) each(function(u) u.dest = p end) end,
                goto_location_angle_width = function(_, p) each(function(u) u.dest = p end) end,
                halt = function() each(function(u) u.dest = nil; u.target = nil end) end,
            })
        end,
    })
end

local player_army, ai_army = make_army("player", 2, -300, -2), make_army("ai", 3, 300, 2)
local alliances = list({
    obj({ armies = function() return list({ player_army }) end }),
    obj({ armies = function() return list({ ai_army }) end }),
})

MOCK_TIMERS = {}
empire_battle = {}
function empire_battle:new()
    return obj({
        alliances = function() return alliances end,
        register_repeating_timer = function(_, name, ms) MOCK_TIMERS[name] = ms end,
        game_time = function() return 0 end,
        show_advisor_message = function() end,
    })
end

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
            g.MOCK_FIRE("BattleCompleted")
            link.poll()

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
            if controlled_names:
                problems.append(f"units still controlled after the battle: {controlled_names}")
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
