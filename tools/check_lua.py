"""Checks the mod's Lua with a real Lua 5.1 (the game's version), without starting the game.

1. compiles every file under lua/ (syntax errors),
2. runs lua/s2ai_probe.lua against a small mock of the battle-script API and drives its timer and
   phase callbacks the way the engine would, then prints what the probe wrote.

    .venv\\Scripts\\python tools\\check_lua.py
"""
import pathlib
import sys
import tempfile

import lupa.lua51 as lua51

REPO = pathlib.Path(__file__).resolve().parent.parent

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

local function make_unit(x, z)
    local u = { pos = battle_vector:new(), dest = nil, controlled = false }
    u.pos:set(x, 0, z)
    return setmetatable(u, { __index = {
        position = function(s) return s.pos end,
        bearing = function() return 90 end,
        number_of_men_alive = function() return 150 end,
        initial_number_of_men = function() return 160 end,
        is_routing = function() return false end,
        is_shattered = function() return false end,
        is_moving = function(s) return s.dest ~= nil end,
        is_moving_fast = function() return false end,
        is_cavalry = function() return false end,
        is_infantry = function() return true end,
        is_artillery = function() return false end,
        ammo_left = function() return 0 end,
        missile_range = function() return 0 end,
        is_leaving_battle = function() return false end,
    } })
end

MOCK_UNITS = {}
local function make_army(n, x0)
    local units = {}
    for i = 1, n do units[i] = make_unit(x0 + i * 20, 0); MOCK_UNITS[#MOCK_UNITS + 1] = units[i] end
    return obj({
        units = function() return list(units) end,
        create_unit_controller = function()
            local controlled = {}
            return obj({
                add_units = function(_, u) controlled[#controlled + 1] = u end,
                take_control = function() for _, u in ipairs(controlled) do u.controlled = true end end,
                release_control = function() for _, u in ipairs(controlled) do u.controlled = false end end,
                goto_location = function(_, p) for _, u in ipairs(controlled) do u.dest = p end end,
            })
        end,
    })
end

local alliances = list({
    obj({ armies = function() return list({ make_army(3, -200) }) end }),
    obj({ armies = function() return list({ make_army(3, 200) }) end }),
})

MOCK_TIMERS, MOCK_PHASE = {}, nil
empire_battle = {}
function empire_battle:new()
    return obj({
        alliances = function() return alliances end,
        register_repeating_timer = function(_, name, ms) MOCK_TIMERS[name] = ms end,
        register_battle_phase_handler = function(_, name) MOCK_PHASE = name end,
    })
end

-- one simulated second: units with a destination walk 5 m towards it
function MOCK_STEP()
    for _, u in ipairs(MOCK_UNITS) do
        if u.dest then
            local dx, dz = u.dest.x - u.pos.x, u.dest.z - u.pos.z
            local d = math.sqrt(dx * dx + dz * dz)
            if d < 5 then u.pos:set(u.dest.x, 0, u.dest.z); u.dest = nil
            else u.pos:set(u.pos.x + dx / d * 5, 0, u.pos.z + dz / d * 5) end
        end
    end
end

function MOCK_EVENT(name) return obj({ get_name = function() return name end }) end
"""


def compile_all() -> bool:
    ok = True
    for path in sorted((REPO / "lua").rglob("*.lua")):
        rt = lua51.LuaRuntime()
        res = rt.globals().loadstring(path.read_text(encoding="utf-8"), "@" + path.name)
        if res is None or (isinstance(res, tuple) and res[0] is None):
            print(f"SYNTAX ERROR {path.relative_to(REPO)}: {res[1] if isinstance(res, tuple) else res}")
            ok = False
        else:
            print(f"ok  {path.relative_to(REPO)}")
    return ok


def run_probe() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "runtime").mkdir()
        rt = lua51.LuaRuntime()
        g = rt.globals()
        rt.execute(MOCK)
        g.S2AI_ROOT = str(root).replace("\\", "/")
        g.dofile(str(REPO / "lua" / "s2ai_probe.lua"))

        timers = dict(g.MOCK_TIMERS.items())
        if timers != {"S2AI_Tick": 1000} or g.MOCK_PHASE != "S2AI_Phase":
            print(f"FAIL: registration timers={timers} phase={g.MOCK_PHASE}")
            return False
        tick, phase = g.S2AI_Tick, g.S2AI_Phase
        for second in range(1, 61):
            if second == 3:
                phase(g.MOCK_EVENT("Deployed"))
            g.MOCK_STEP()
            tick()

        log = (root / "runtime" / "probe.log").read_text()
        states = (root / "runtime" / "state.jsonl").read_text().splitlines()
        print("---- probe.log (mock battle) ----")
        print(log.rstrip())
        print(f"---- state.jsonl: {len(states)} lines; last: {states[-1][:200]}...")
        problems = [line for line in log.splitlines() if "ERROR" in line]
        if "CONTROL TEST end: unit moved 60.0 m" not in log:
            problems.append("control test did not move the unit 60 m")
        if len(states) != 60:
            problems.append(f"expected 60 snapshots, got {len(states)}")
        for p in problems:
            print("FAIL:", p)
        return not problems


if __name__ == "__main__":
    good = compile_all() and run_probe()
    print("\nALL OK" if good else "\nFAILED")
    sys.exit(0 if good else 1)
