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

-- Which Lua environment loaded us (set by the loader, tools/install.ps1):
--   "ui"     - data/battle_scripted.lua: the UI/event scripting state, loaded for every battle, no battle API
--   "battle" - a historical battle's .battle_script: the battle-script state with empire_battle
local ENV = S2AI_ENV or "?"

-- Logging must never be what breaks: the battle-script state may lack io/os, and then the probe still runs
-- (the control test moving a unit is visible in game even without a log).
local function log(msg)
	pcall(function()
		local f = io.open(LOG_PATH, "a")
		if f then
			f:write(os.date("%Y-%m-%d %H:%M:%S"), " [", ENV, "] ", tostring(msg), "\n")
			f:close()
		end
	end)
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
		-- no math.huge in the game's Lua (run 7): NaN fails v == v, infinities exceed any real coordinate
		if v ~= v or v > 1e300 or v < -1e300 then return "null" end
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
	-- build the line first: in run 6 the file was created but stayed empty (an error inside json() was swallowed)
	local ok, line = pcall(json, { t = S.ticks, clock = os.clock(), phase = S.phase, units = units })
	if not ok then
		if not S.snapshot_error_logged then
			S.snapshot_error_logged = true
			log("SNAPSHOT ERROR: " .. tostring(line) .. " | first unit: " .. (units[1] and (function()
				local parts = {}
				for k, v in pairs(units[1]) do parts[#parts + 1] = tostring(k) .. "=" .. type(v) .. ":" .. tostring(v) end
				return table.concat(parts, " ")
			end)() or "none"))
		end
		return
	end
	pcall(function()
		local f = io.open(STATE_PATH, "a")
		if f then
			f:write(line, "\n")
			f:close()
		end
	end)
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
-- Takes a WHOLE army away from the game's AI, sends every unit 60 m east, and gives them back after
-- CONTROL_TEST_LENGTH_S - easy to see in game. At Sekigahara the player commands Ishida Mitsunari (alliance 1),
-- so alliance 2 army 1 is Tokugawa Ieyasu's army, the enemy.
local CONTROL_TEST_ALLIANCE = 2

-- The battle-script state may not be able to write files. Its advisor messages, though, raise AdviceIssued /
-- AdviceSuperseded events in the UI state, where the probe logs them (ENV "ui"). So the battle side can report
-- through the advisor: the key carries the diagnostics.
local function signal(text)
	log("SIGNAL " .. text)
	if S.battle then
		pcall(function() S.battle:show_advisor_message("S2AI " .. text, "S2AI " .. text) end)
	end
end

local NO_PHASE_FALLBACK_S = 30 -- a historical battle's own script may replace our phase handler

local function control_test(now)
	if not S.deployed_at and S.phase == "?" and now >= NO_PHASE_FALLBACK_S then
		S.deployed_at = now - CONTROL_TEST_DELAY_S
		log("no phase events seen; starting the control test anyway at " .. now .. " s")
	end
	if not S.deployed_at then return end
	local t = S.test
	if not t and now - S.deployed_at >= CONTROL_TEST_DELAY_S then
		local alliances = S.battle:alliances()
		if alliances:count() < CONTROL_TEST_ALLIANCE then return end
		local army = alliances:item(CONTROL_TEST_ALLIANCE):armies():item(1)
		local units = army:units()
		local uc = army:create_unit_controller()
		log("API unitcontroller: " .. method_names(uc))
		local added, errors = 0, {}
		for i = 1, units:count() do
			local ok, e = pcall(function() uc:add_units(units:item(i)) end)
			if ok then added = added + 1 else errors[#errors + 1] = tostring(e) end
		end
		local ok2, e2 = pcall(function() uc:take_control() end)
		-- one goto per unit: each to its own spot 60 m east, so the army keeps its shape
		local sent = 0
		for i = 1, units:count() do
			local u = units:item(i)
			local one = army:create_unit_controller()
			local ok = pcall(function()
				local p = u:position()
				one:add_units(u)
				one:take_control()
				one:goto_location(vec(p:get_x() + 60, p:get_z()))
			end)
			if ok then sent = sent + 1 end
			S.test_ucs = S.test_ucs or {}
			S.test_ucs[#S.test_ucs + 1] = one
		end
		local unit = units:item(1)
		local p = unit:position()
		log(string.format("CONTROL TEST start: alliance %d army 1 (%d units): add_units ok %d %s; take_control=%s %s; goto sent to %d units",
			CONTROL_TEST_ALLIANCE, units:count(), added, table.concat(errors, "; "), tostring(ok2), tostring(e2 or ""), sent))
		signal(string.format("control_test units=%d added=%d take_control=%s goto_sent=%d", units:count(), added,
			tostring(ok2), sent))
		S.test = { uc = uc, unit = unit, started = now, x0 = p:get_x(), z0 = p:get_z() }
	elseif t and not t.done then
		local p = t.unit:position()
		local moved = math.sqrt((p:get_x() - t.x0) ^ 2 + (p:get_z() - t.z0) ^ 2)
		if now - t.started >= CONTROL_TEST_LENGTH_S then
			local ok = pcall(function() t.uc:release_control() end)
			for _, one in ipairs(S.test_ucs or {}) do pcall(function() one:release_control() end) end
			S.test_ucs = nil
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

pcall(function()
	-- file read-back check (needed later for reading orders)
	local f = io.open(LOG_PATH, "r")
	local size = f and f:seek("end")
	if f then f:close() end
	log("read-back of probe.log: " .. tostring(size) .. " bytes")
end)

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
		signal(string.format("loaded env=%s io=%s os=%s dofile=%s alliances=%s", ENV, type(io), type(os), type(dofile),
			tostring(call(S.battle:alliances(), "count"))))
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

-- ---------------------------------------------------------------- UI-state introspection
-- Looking for a way from the UI state (which every battle has) to the battle: tables of engine functions,
-- the Lua registry, and what the event contexts expose.

local function keys_of(t, limit)
	if type(t) ~= "table" then return "(" .. type(t) .. ")" end
	local names = {}
	for k, v in pairs(t) do names[#names + 1] = tostring(k) .. ":" .. type(v) end
	table.sort(names)
	local n = #names
	if limit and n > limit then
		for i = limit + 1, n do names[i] = nil end
		names[#names + 1] = "...(" .. n .. " total)"
	end
	return table.concat(names, " ")
end

local function dump_ui_state()
	for _, name in ipairs({ "system", "vfs", "lookup", "udata_lookup", "defined", "bit" }) do
		log("TABLE " .. name .. ": " .. keys_of(_G[name], 200))
	end
	log("package.loaded: " .. keys_of(package.loaded, 200))
	log("CliExecute=" .. type(CliExecute))
	local ok, reg = pcall(debug.getregistry)
	if ok then
		local names = {}
		for k, v in pairs(reg) do
			if type(k) == "string" then names[#names + 1] = k .. ":" .. type(v) end
		end
		table.sort(names)
		log("REGISTRY string keys (" .. #names .. "): " .. table.concat(names, " "))
		-- UI classes keep their methods in __index (run 7); battle.* classes hold them directly
		for _, cls in ipairs({ "UIBattleSetup", "UIHistoricBattleSetup", "UIComponent", "UIPrefsInterface",
			"UISelectionManager", "UICardManager", "UIMPInterface", "Pointer<GAME_CORE>", "Pointer<Component>",
			"battle.ai_unit_controller", "battle.commandevent", "battle.unit_controller" }) do
			local c = reg[cls]
			if type(c) == "table" then
				local methods = type(c.__index) == "table" and c.__index or c
				log("CLASS " .. cls .. ": " .. keys_of(methods, 400))
			end
		end
	end
end

-- method names tried on event contexts; their __index is a C function, so they cannot be listed
local CONTEXT_PROBES = { "string", "number", "boolean", "table", "unit", "character", "faction", "component",
	"building", "army", "alliance", "battle", "position", "get_name", "type", "id", "key", "name", "unit_key",
	"get_unit", "get_army", "get_alliance", "garrison_residence", "region", "settlement", "ship", "target" }

local function describe_context(context)
	if context == nil then return "nil" end
	local parts = { "tostring=" .. tostring(context) }
	local mt = getmetatable(context)
	if type(mt) == "table" and type(mt.type) == "function" then
		local ok, v = pcall(mt.type, context)
		parts[#parts + 1] = "mt.type()=" .. tostring(v)
	end
	local found = {}
	for _, m in ipairs(CONTEXT_PROBES) do
		local ok, v = pcall(function() return context[m] end)
		if ok and v ~= nil then
			local val = ""
			if type(v) == "function" then
				local ok2, r = pcall(v, context)
				val = "=" .. (ok2 and (type(r) .. ":" .. tostring(r)) or "err")
			end
			found[#found + 1] = m .. val
		end
	end
	parts[#parts + 1] = "has[" .. table.concat(found, " ") .. "]"
	return table.concat(parts, " ")
end

-- ---------------------------------------------------------------- frontend (main menu) inspection
-- The custom battle screen (layout sp_battle_setup, interface MPBattleSetup) may be what UIBattleSetup wraps:
-- RetrieveDetails / SetDetails could let us give custom battles a battle script. Read-only for now.

local function dump(v, depth, seen)
	depth, seen = depth or 0, seen or {}
	local t = type(v)
	if t == "string" then return string.format("%q", v) end
	if t ~= "table" then return tostring(v) end
	if seen[v] then return "<cycle>" end
	if depth >= 5 then return "{...}" end
	seen[v] = true
	local keys = {}
	for k in pairs(v) do keys[#keys + 1] = k end
	table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
	local parts = {}
	for _, k in ipairs(keys) do parts[#parts + 1] = tostring(k) .. "=" .. dump(v[k], depth + 1, seen) end
	return "{" .. table.concat(parts, ", ") .. "}"
end

local function ui_class(name)
	if _G[name] ~= nil then return _G[name] end
	local ok, reg = pcall(debug.getregistry)
	local c = ok and reg[name]
	return type(c) == "table" and type(c.__index) == "table" and c.__index.new or nil
end

-- Walk the UI tree (Find(i) by child index, as frontend_scripted.lua does with ChildCount) looking for an id.
-- Run 9: root:Find("sp_battle_setup") found nothing, so the panel is deeper than the root's children.
local function find_deep(root, wanted)
	local UIC = ui_class("UIComponent")
	local queue, head, visited = { { root, "root" } }, 1, 0
	while head <= #queue and visited < 5000 do
		local node, path = queue[head][1], queue[head][2]
		head, visited = head + 1, visited + 1
		local okn, n = pcall(function() return node:ChildCount() end)
		for i = 0, (okn and n or 0) - 1 do
			local ok, child = pcall(function() return UIC(node:Find(i)) end)
			if ok and child then
				local okid, id = pcall(function() return child:Id() end)
				local cpath = path .. "/" .. tostring(okid and id or "?")
				if okid and id == wanted then return child, cpath, visited end
				queue[#queue + 1] = { child, cpath }
			end
		end
	end
	return nil, nil, visited
end

local function frontend_inspect(trigger)
	local root = S.fe_root
	if not root then return end
	local UIC, UBS = ui_class("UIComponent"), ui_class("UIBattleSetup")
	for _, id in ipairs({ "sp_battle_setup", "mp_battle_setup", "panel_battle_setup" }) do
		local okf, comp, path, visited = pcall(find_deep, root, id)
		if not okf then
			log("FRONTEND find_deep ERROR: " .. tostring(comp))
		elseif not comp then
			log(string.format("FRONTEND %s: %s not found (%d components walked)", trigger, id, visited or -1))
		end
		local addr = okf and comp and call(comp, "Address")
		if okf and comp then log(string.format("FRONTEND %s: %s at %s address=%s", trigger, id, path, tostring(addr))) end
		if addr then S.try_setup(trigger .. "/" .. id, addr) end
	end
	if S.fe_screen then
		local addr = call(S.fe_screen, "Address")
		if addr then S.try_setup(trigger .. "/screen_component", addr) end
	end
end

-- wrap a component address as UIBattleSetup and log RetrieveDetails(); at most 6 times per label kind
function S.try_setup(key, addr)
	local UIC, UBS = ui_class("UIComponent"), ui_class("UIBattleSetup")
	local kind = key:match("[^/]+$")
	S.fe_dumps = S.fe_dumps or {}
	if (S.fe_dumps[kind] or 0) >= 6 then return end
	S.fe_dumps[kind] = (S.fe_dumps[kind] or 0) + 1
	local okb, bs = pcall(function() return UBS(addr) end)
	log(string.format("FRONTEND %s: UIBattleSetup ctor=%s -> %s %s", key, type(UBS), tostring(okb), tostring(bs)))
	if okb and bs then
		local okr, details = pcall(function() return bs:RetrieveDetails() end)
		local text = okr and dump(details) or ("ERROR " .. tostring(details))
		log("FRONTEND RetrieveDetails(" .. key .. ") type=" .. type(details) .. ": " .. text:sub(1, 20000))
	end
	local okc, comp = pcall(function() return UIC(addr) end)
	if okc and comp then
		log(string.format("FRONTEND %s component: id=%s visible=%s children=%s", key, tostring(call(comp, "Id")),
			tostring(call(comp, "Visible")), tostring(call(comp, "ChildCount"))))
	end
end

local function frontend_event(name, context)
	if ENV ~= "frontend" then return end
	if name == "UICreated" then
		local UIC = ui_class("UIComponent")
		local ok, root = pcall(function() return UIC(context.component) end)
		if ok then S.fe_root = root end
		log("FRONTEND UICreated root=" .. tostring(ok and root))
	elseif name == "ComponentLClickUp" then
		local UIC = ui_class("UIComponent")
		local ok, id = pcall(function() return UIC(context.component):Id() end)
		log("FRONTEND click: " .. tostring(id) .. (ok and "" or " (Id() failed)"))
		frontend_inspect("click:" .. tostring(ok and id or "?"))
	elseif name == "FrontEndScreenChange" then
		local ok, s = pcall(function() return context.string end) -- a field, as frontend_scripted.lua uses it
		log("FRONTEND screen: " .. tostring(ok and s))
		if ok and s == "sp_battle_setup" then
			-- the screen also arrives as context.component: try it directly
			local UIC = ui_class("UIComponent")
			local okc, comp = pcall(function() return UIC(context.component) end)
			log("FRONTEND screen component: " .. tostring(okc and comp) .. " id=" .. tostring(okc and call(comp, "Id")))
			if okc and comp then S.fe_screen = comp end
		end
		frontend_inspect("screen:" .. tostring(ok and s))
	end
end

S.events_seen = S.events_seen or {}
local function on_event(name, context)
	frontend_event(name, context)
	local seen = S.events_seen
	seen[name] = (seen[name] or 0) + 1
	-- advice events may carry the battle side's signal: log all of them
	if seen[name] <= 2 or name:find("^Advice") then
		log(string.format("event %s #%d empire_battle=%s %s", name, seen[name], type(empire_battle),
			describe_context(context)))
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

-- Is package.loadlib real (a native DLL could then patch the engine so custom battles get a script)?
-- Harmless check: ask kernel32 for a symbol that does not exist. Real loadlib: nil + "...procedure..." + "init";
-- a disabled stub answers "absent" / "dynamic libraries not enabled".
do
	local ok, f, err, where = pcall(package.loadlib, "kernel32.dll", "s2ai_no_such_symbol")
	log(string.format("LOADLIB check: pcall=%s f=%s err=%s where=%s", tostring(ok), tostring(f), tostring(err),
		tostring(where)))
end

do -- both states: the battle-script state's registry may hold more classes
	local ok, err = pcall(dump_ui_state)
	if not ok then log("STATE DUMP ERROR: " .. tostring(err)) end
end

try_start("load")
