-- shogun2-battle-ai: probe
--
-- Loaded at battle start by the game's data/battle_scripted.lua (see tools/install.ps1), via dofile().
-- Answers the questions the whole project depends on:
--   1. does battle_scripted.lua run in ordinary custom / campaign battles?
--   2. can a script write (and read) files on disk?            -> runtime/probe.log
--   3. what methods do battle / alliance / army / unit objects have? (metatable dump)
--   4. can a script take a unit away from the game's AI and order it around? (control test)
-- and writes a battle snapshot once a second to runtime/state.jsonl.
--
-- Everything is wrapped in pcall: an error here must never break the battle.

local ROOT = S2AI_ROOT or "D:/Develop/shogun2-battle-ai"
local LOG_PATH = ROOT .. "/runtime/probe.log"
local STATE_PATH = ROOT .. "/runtime/state.jsonl"

local TICK_MS = 1000
local CONTROL_TEST_DELAY_S = 15  -- after deployment ends
local CONTROL_TEST_LENGTH_S = 30 -- then the unit is handed back to the game's AI

local function log(msg)
	local f = io.open(LOG_PATH, "a")
	if f then
		f:write(os.date("%Y-%m-%d %H:%M:%S"), "  ", tostring(msg), "\n")
		f:close()
	end
end

-- ---------------------------------------------------------------- helpers

-- Call obj:name() safely; returns nil if the method is missing or errors.
local function call(obj, name, ...)
	local args = { ... }
	local ok, res = pcall(function() return obj[name](obj, unpack(args)) end)
	if ok then return res end
	return nil
end

local function method_names(obj)
	local names = {}
	local mt = getmetatable(obj)
	if type(mt) == "table" then
		if type(mt.__index) == "table" then
			for k in pairs(mt.__index) do names[#names + 1] = tostring(k) end
		end
		for k in pairs(mt) do names[#names + 1] = "mt." .. tostring(k) end
	elseif mt ~= nil then
		names[1] = "(metatable is " .. type(mt) .. ": " .. tostring(mt) .. ")"
	else
		names[1] = "(no metatable)"
	end
	table.sort(names)
	return table.concat(names, " ")
end

local function json(v)
	local t = type(v)
	if t == "nil" then return "null"
	elseif t == "boolean" then return v and "true" or "false"
	elseif t == "number" then
		if v ~= v or v == math.huge or v == -math.huge then return "null" end
		if v == math.floor(v) then return string.format("%d", v) end
		return string.format("%.2f", v)
	elseif t == "string" then
		return '"' .. v:gsub('[%c"\\]', function(c) return string.format("\\u%04x", c:byte()) end) .. '"'
	elseif t == "table" then
		local out = {}
		if #v > 0 or next(v) == nil then
			for i = 1, #v do out[i] = json(v[i]) end
			return "[" .. table.concat(out, ",") .. "]"
		end
		for k, val in pairs(v) do out[#out + 1] = json(tostring(k)) .. ":" .. json(val) end
		return "{" .. table.concat(out, ",") .. "}"
	end
	return json(tostring(v))
end

local function vec(x, z)
	local p = battle_vector:new()
	p:set(x, 0, z)
	return p
end

-- ---------------------------------------------------------------- state

S2AI = S2AI or {}
local S = S2AI
S.ticks = 0
S.phase = "?"
S.deployed_at = nil
S.test = nil -- control test record

local function each_unit(fn)
	local alliances = S.battle:alliances()
	for a = 1, alliances:count() do
		local armies = alliances:item(a):armies()
		for r = 1, armies:count() do
			local army = armies:item(r)
			local units = army:units()
			for u = 1, units:count() do
				fn(a, r, u, units:item(u), army)
			end
		end
	end
end

local function unit_record(a, r, u, unit)
	local pos = call(unit, "position")
	return {
		id = a .. "." .. r .. "." .. u,
		x = pos and call(pos, "get_x"), z = pos and call(pos, "get_z"),
		bearing = call(unit, "bearing"),
		men = call(unit, "number_of_men_alive"),
		men0 = call(unit, "initial_number_of_men"),
		routing = call(unit, "is_routing"),
		shattered = call(unit, "is_shattered"),
		moving = call(unit, "is_moving"),
		running = call(unit, "is_moving_fast"),
		cav = call(unit, "is_cavalry"), inf = call(unit, "is_infantry"), art = call(unit, "is_artillery"),
		ammo = call(unit, "ammo_left"), range = call(unit, "missile_range"),
		leaving = call(unit, "is_leaving_battle"),
	}
end

local function write_snapshot()
	local units = {}
	each_unit(function(a, r, u, unit) units[#units + 1] = unit_record(a, r, u, unit) end)
	local f = io.open(STATE_PATH, "a")
	if f then
		f:write(json({ t = S.ticks, clock = os.clock(), phase = S.phase, units = units }), "\n")
		f:close()
	end
end

local function dump_api_once()
	if S.api_dumped then return end
	S.api_dumped = true
	local alliances = S.battle:alliances()
	log("API battle:    " .. method_names(S.battle))
	log("API alliances: " .. method_names(alliances))
	log("alliances: " .. tostring(alliances:count()))
	for a = 1, alliances:count() do
		local alliance = alliances:item(a)
		local armies = alliance:armies()
		if a == 1 then
			log("API alliance:  " .. method_names(alliance))
			log("API armies:    " .. method_names(armies))
		end
		for r = 1, armies:count() do
			local army = armies:item(r)
			local units = army:units()
			if a == 1 and r == 1 then
				log("API army:      " .. method_names(army))
				log("API units:     " .. method_names(units))
				if units:count() > 0 then
					local unit = units:item(1)
					log("API unit:      " .. method_names(unit))
					log("API vector:    " .. method_names(unit:position()))
				end
			end
			log(string.format("alliance %d army %d: %d units, handicap=%s", a, r, units:count(),
				tostring(call(army, "army_handicap"))))
		end
	end
end

-- ---------------------------------------------------------------- control test
-- Takes the first unit of alliance 2 away from the game's AI, sends it 60 m sideways, and gives it back
-- after CONTROL_TEST_LENGTH_S. Whoever's unit that is (yours or the AI's), you will see it walk off.

local function control_test(now)
	if not S.deployed_at then return end
	local t = S.test
	if not t and now - S.deployed_at >= CONTROL_TEST_DELAY_S then
		local alliances = S.battle:alliances()
		if alliances:count() < 2 then return end
		local army = alliances:item(2):armies():item(1)
		local unit = army:units():item(1)
		local uc = army:create_unit_controller()
		log("API unitcontroller: " .. method_names(uc))
		local p = unit:position()
		local x, z = p:get_x(), p:get_z()
		local ok1, e1 = pcall(function() uc:add_units(unit) end)
		local ok2, e2 = pcall(function() uc:take_control() end)
		local ok3, e3 = pcall(function() uc:goto_location(vec(x + 60, z)) end)
		log(string.format("CONTROL TEST start: unit 2.1.1 at (%.1f, %.1f) -> (%.1f, %.1f); add_units=%s %s take_control=%s %s goto_location=%s %s",
			x, z, x + 60, z, tostring(ok1), tostring(e1 or ""), tostring(ok2), tostring(e2 or ""), tostring(ok3), tostring(e3 or "")))
		S.test = { uc = uc, unit = unit, started = now, x0 = x, z0 = z }
	elseif t and not t.done then
		local p = t.unit:position()
		local moved = math.sqrt((p:get_x() - t.x0) ^ 2 + (p:get_z() - t.z0) ^ 2)
		if now - t.started >= CONTROL_TEST_LENGTH_S then
			local ok = pcall(function() t.uc:release_control() end)
			log(string.format("CONTROL TEST end: unit moved %.1f m; release_control=%s", moved, tostring(ok)))
			t.done = true
		elseif math.floor(now - t.started) % 5 == 0 then
			log(string.format("CONTROL TEST +%ds: moved %.1f m, is_moving=%s", math.floor(now - t.started), moved,
				tostring(call(t.unit, "is_moving"))))
		end
	end
end

-- ---------------------------------------------------------------- engine callbacks (globals, called by name)

function S2AI_Tick()
	if not S.battle then return end
	local ok, err = pcall(function()
		S.ticks = S.ticks + 1
		local now = S.ticks * TICK_MS / 1000
		dump_api_once()
		write_snapshot()
		control_test(now)
	end)
	if not ok and not S.tick_error_logged then
		S.tick_error_logged = true
		log("TICK ERROR: " .. tostring(err))
	end
end

function S2AI_Phase(event)
	local ok, err = pcall(function()
		S.phase = event:get_name()
		log("phase: " .. tostring(S.phase) .. " (tick " .. S.ticks .. ")")
		if S.phase == "Deployed" and not S.deployed_at then
			S.deployed_at = S.ticks * TICK_MS / 1000
		end
	end)
	if not ok then log("PHASE ERROR: " .. tostring(err)) end
	-- The battle_manager of the game's own historical-battle scripts also listens for phases;
	-- forward the event if it exists so those battles keep working.
	if battle_manager_phase_change and my_battle_manager and my_battle_manager ~= 0 then
		pcall(battle_manager_phase_change, event)
	end
end

-- ---------------------------------------------------------------- start

log("==================== probe loaded ====================")
log(string.format("_VERSION=%s io=%s os=%s dofile=%s loadlib=%s empire_battle=%s battle_vector=%s",
	tostring(_VERSION), type(io), type(os), type(dofile), type(package and package.loadlib),
	type(empire_battle), type(battle_vector)))

do
	-- file read-back check (needed later for reading orders)
	local f = io.open(LOG_PATH, "r")
	local size = f and f:seek("end")
	if f then f:close() end
	log("read-back of probe.log: " .. tostring(size) .. " bytes")
end

do
	-- what does this Lua state contain at load time?
	local names = {}
	for k, v in pairs(_G) do names[#names + 1] = tostring(k) .. ":" .. type(v) end
	table.sort(names)
	log("GLOBALS (" .. #names .. "): " .. table.concat(names, " "))
end

-- The first probe showed battle_scripted.lua runs once (probably at game start), before any battle exists,
-- so empire_battle is nil then. Start as soon as it appears: try at load, and again on every game event.
local function try_start(reason)
	if S.battle or empire_battle == nil then return end
	local ok, err = pcall(function()
		S.battle = empire_battle:new()
		S.battle:register_repeating_timer("S2AI_Tick", TICK_MS)
		S.battle:register_battle_phase_handler("S2AI_Phase")
	end)
	if ok then
		log("battle object created (" .. reason .. "), timer + phase handler registered")
	else
		log("START ERROR (" .. reason .. "): " .. tostring(err))
		S.battle = nil
		S.start_failed = (S.start_failed or 0) + 1
	end
end

local function reset(reason)
	log("reset: " .. reason)
	S.battle, S.ticks, S.phase, S.deployed_at, S.test, S.api_dumped, S.tick_error_logged = nil, 0, "?", nil, nil, nil, nil
end

S.events_seen = S.events_seen or {}
local function on_event(name, context)
	local seen = S.events_seen
	seen[name] = (seen[name] or 0) + 1
	if seen[name] <= 2 then
		log(string.format("event %s #%d empire_battle=%s context=[%s]", name, seen[name], type(empire_battle),
			context ~= nil and method_names(context) or "nil"))
	end
	if (S.start_failed or 0) < 3 then try_start("event " .. name) end
	if name == "BattleConflictPhaseCommenced" and S.battle and not S.deployed_at then
		S.deployed_at = S.ticks * TICK_MS / 1000
		log("deployment over (BattleConflictPhaseCommenced, tick " .. S.ticks .. ")")
	elseif name == "BattleCompleted" then
		reset("BattleCompleted")
	end
end

if type(events) == "table" then
	local n = 0
	for name, list in pairs(events) do
		if type(list) == "table" then
			n = n + 1
			local event_name = tostring(name)
			list[#list + 1] = function(context)
				local ok, err = pcall(on_event, event_name, context)
				if not ok then log("EVENT ERROR " .. event_name .. ": " .. tostring(err)) end
			end
		end
	end
	log("hooked " .. n .. " game events")
else
	log("no global 'events' table to hook (type " .. type(events) .. ")")
end

try_start("load")
