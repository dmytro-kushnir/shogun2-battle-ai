-- shogun2-battle-ai: game-side bridge. Runs in the battle-script Lua state (loaded by the loader that
-- tools/install.ps1 puts in front of a battle's .battle_script, shipped in s2ai_battle.pack).
--
-- game -> server: runtime/battle-<id>.jsonl, one JSON object per line:
--     {"type":"battle_start", ...armies and units...}, then {"type":"state", ...} every TICK_MS,
--     {"type":"event", ...} for battle events, {"type":"applied", ...} after each orders file, {"type":"battle_end"}
--   runtime/current_battle.txt holds the id of the battle being fought.
-- server -> game: runtime/orders.lua, written atomically by the server:
--     return { battle = "<id>", seq = 7, written = <unix time>, take = {unit ids}, orders = { {cmd=...}, ... } }
--   Units in `take` are taken from the game's AI, every other unit we hold is handed back. If the server stops
--   writing (written older than STALE_S) everything goes back to the game's AI.
--
-- Coordinates: x = game x, y = game z (the ground plane), metres. Headings as the game's unit:bearing().
-- Every engine call is wrapped in pcall: the battle must never break because of us.

local ROOT = S2AI_ROOT -- set by the loader to this repository's folder
if not ROOT then return end
local RUN = ROOT .. "/runtime"
local TICK_MS = 500
local STALE_S = 15
local ORDERS_PATH = RUN .. "/orders.lua"

S2AI_BRIDGE = S2AI_BRIDGE or {}
local B = S2AI_BRIDGE

-- ------------------------------------------------------------------ util

local function log(msg)
	pcall(function()
		local f = io.open(RUN .. "/bridge.log", "a")
		f:write(os.date("%Y-%m-%d %H:%M:%S"), " ", tostring(msg), "\n")
		f:close()
	end)
end

local function call(obj, name, ...)
	local args = { ... }
	local ok, res = pcall(function() return obj[name](obj, unpack(args)) end)
	if ok then return res end
	return nil
end

local function json(v)
	local t = type(v)
	if t == "nil" then return "null"
	elseif t == "boolean" then return v and "true" or "false"
	elseif t == "number" then
		-- the game's Lua has no math.huge
		if v ~= v or v > 1e300 or v < -1e300 then return "null" end
		if v == math.floor(v) and v > -1e15 and v < 1e15 then return string.format("%d", v) end
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

local function emit(msg)
	msg.battle = B.id
	local ok, line = pcall(json, msg)
	if not ok then
		log("JSON ERROR (" .. tostring(msg.type) .. "): " .. tostring(line))
		return
	end
	pcall(function()
		local f = io.open(B.stream, "a")
		f:write(line, "\n")
		f:close()
	end)
end

local function vec(x, y)
	local p = battle_vector:new()
	p:set(x, 0, y)
	return p
end

-- defined in the buildings section further down, used by the tick function before it
local scan_buildings, building_states

-- ------------------------------------------------------------------ units

-- Stable unit ids (alliance * 10000 + n, in order of first sighting). The engine compacts an army's unit list when
-- a unit is destroyed, so a list position does not identify a unit (at Sekigahara, ids built from positions
-- pointed at other units after the first losses).
--
-- refresh B.units (id -> {unit, army, alliance}) from the battle; cheap, done every tick (reinforcements)
--
-- Unit identity follows the unit itself, not a name: run 24 showed that in custom/campaign battles units are
-- named "1", "2", ... by their PLACE in the army, so when one dies the rest are renamed (ids changed under the
-- general's feet). Names only shift when an army LOSES a unit, so:
--   1. armies whose unit count did not drop since the last scan (0.5 s): same army + name + type = same unit,
--      however far it moved (run 25: a routing unit's centre jumps 20-80 m as its men scatter, and the whole
--      army jumps from deployment to battle positions at the start);
--   2. armies that lost a unit: each unit takes the id of the closest unit of the same type and alliance in the
--      previous scan (a matching name only breaks ties). No match within TRACK_MAX_M: a new unit, fresh id.
local TRACK_MAX_M = 40

local function scan()
	local units, current = {}, {}
	B.next_n = B.next_n or {}
	B.track = B.track or {}        -- id -> {a, r, key, type, name, x, y} from the previous scan
	B.army_n = B.army_n or {}      -- "a|r" -> unit count in the previous scan
	local army_n, seen = {}, {}
	local alliances = B.battle:alliances()
	for a = 1, alliances:count() do
		local armies = alliances:item(a):armies()
		for r = 1, armies:count() do
			local army = armies:item(r)
			-- naval battles: the fleets are army:ships() (army:units() is empty); see start()
			local list = B.naval and army:ships() or army:units()
			army_n[a .. "|" .. r] = list:count()
			for u = 1, list:count() do
				local unit = list:item(u)
				local p = call(unit, "position")
				local c = { unit = unit, army = army, a = a, r = r, ship = B.naval,
					type = tostring(call(unit, "type")), name = tostring(call(unit, "name")),
					x = p and call(p, "get_x") or 0, y = p and call(p, "get_z") or 0 }
				local key = a .. "|" .. r .. "|" .. c.name .. "|" .. c.type
				seen[key] = (seen[key] or 0) + 1
				c.key = key .. "#" .. seen[key]
				current[#current + 1] = c
			end
		end
	end
	local taken_id, assigned = {}, {}
	-- 1. by name in armies that lost nothing
	local by_key = {}
	for id, t in pairs(B.track) do by_key[t.key] = id end
	for ci, c in ipairs(current) do
		local ar = c.a .. "|" .. c.r
		local id = by_key[c.key]
		if id and B.army_n[ar] and army_n[ar] >= B.army_n[ar] then
			taken_id[id], assigned[ci] = true, id
		end
	end
	-- 2. the rest by distance: all candidate pairs (previous id, current unit) of the same alliance and type,
	-- closest first
	local pairs_ = {}
	for ci, c in ipairs(current) do
		for id, t in pairs(B.track) do
			if not assigned[ci] and not taken_id[id] and t.a == c.a and t.type == c.type then
				local d = math.sqrt((t.x - c.x) ^ 2 + (t.y - c.y) ^ 2) + ((t.name == c.name) and 0 or 3)
				if d <= TRACK_MAX_M then pairs_[#pairs_ + 1] = { d = d, id = id, ci = ci } end
			end
		end
	end
	table.sort(pairs_, function(p1, p2) return p1.d < p2.d end)
	for _, pr in ipairs(pairs_) do
		if not taken_id[pr.id] and not assigned[pr.ci] then
			taken_id[pr.id], assigned[pr.ci] = true, pr.id
		end
	end
	local track = {}
	for ci, c in ipairs(current) do
		local id = assigned[ci]
		if not id then
			B.next_n[c.a] = (B.next_n[c.a] or 0) + 1
			id = c.a * 10000 + B.next_n[c.a]
		end
		units[id] = { unit = c.unit, army = c.army, alliance = c.a, army_index = c.r, ship = c.ship }
		track[id] = { a = c.a, r = c.r, key = c.key, type = c.type, name = c.name, x = c.x, y = c.y }
	end
	B.units, B.track, B.army_n = units, track, army_n
end

local function sorted_ids()
	local ids = {}
	for id in pairs(B.units) do ids[#ids + 1] = id end
	table.sort(ids)
	return ids
end

-- Ships (naval battles). Methods the game's own naval scripts call on a ship: position, name,
-- number_of_men_alive, hull_damage, is_routing, is_shattered, is_surrendered; the rest is tried and logged.
local SHIP_W = 60 -- metres a ship takes in a line abreast (the server spaces its lines by this)

local function ship_static(id, e)
	local s = e.unit
	return {
		id = id, alliance = e.alliance, army = e.army_index, ship = true,
		name = call(s, "name"), type = call(s, "type"),
		men0 = call(s, "initial_number_of_men") or call(s, "number_of_men_alive"),
	}
end

local function ship_state(id, e)
	local s = e.unit
	local p = call(s, "position")
	local surrendered = call(s, "is_surrendered")
	return {
		id = id,
		x = p and call(p, "get_x"), y = p and call(p, "get_z"), z = 0,
		h = call(s, "bearing"), w = SHIP_W,
		men = call(s, "number_of_men_alive"),
		hull = call(s, "hull_damage"), sail = call(s, "sail_damage"),
		rout = call(s, "is_routing"), shat = call(s, "is_shattered"), surr = surrendered,
		mov = call(s, "is_moving"),
		gone = surrendered == true,
		ctl = B.controlled[id] ~= nil,
	}
end

-- the method names an engine object offers (they live on its metatable), for the log
local function method_names(obj)
	local names = {}
	pcall(function()
		local mt = getmetatable(obj)
		local seen = {}
		for _, t in ipairs({ mt, type(mt) == "table" and rawget(mt, "__index") or nil }) do
			if type(t) == "table" then
				for k, v in pairs(t) do
					if type(k) == "string" and type(v) == "function" and not seen[k] and not k:match("^__") then
						seen[k] = true
						names[#names + 1] = k
					end
				end
			end
		end
	end)
	table.sort(names)
	return table.concat(names, " ")
end

local function static_record(id, e)
	if e.ship then return ship_static(id, e) end
	local u = e.unit
	return {
		id = id, alliance = e.alliance, army = e.army_index,
		name = call(u, "name"), type = call(u, "type"),
		men0 = call(u, "initial_number_of_men"),
		cav = call(u, "is_cavalry"), inf = call(u, "is_infantry"), art = call(u, "is_artillery"),
		range = call(u, "missile_range"), ammo0 = call(u, "starting_ammo"),
	}
end

local function state_record(id, e)
	if e.ship then return ship_state(id, e) end
	local u = e.unit
	local p = call(u, "position")
	return {
		id = id,
		x = p and call(p, "get_x"), y = p and call(p, "get_z"), z = p and call(p, "get_y"), -- z = altitude
		h = call(u, "bearing"),
		w = call(u, "ordered_width"),
		men = call(u, "number_of_men_alive"),
		rout = call(u, "is_routing"), shat = call(u, "is_shattered"),
		mov = call(u, "is_moving"), run = call(u, "is_moving_fast"),
		ammo = call(u, "ammo_left"),
		gone = call(u, "is_leaving_battle"),
		gar = call(u, "is_currently_garrisoned"),
		ab = call(u, "current_special_ability"), ab_ok = call(u, "can_perform_special_ability"),
		ctl = B.controlled[id] ~= nil,
	}
end

-- ------------------------------------------------------------------ control

-- A unit controller must never be garbage-collected: its C++ destructor then runs while the engine tears the
-- battle down (or after), touching units that are gone. That corrupted the heap; the game crashed on exit
-- (0xc0000374) after every battle since the bridge stopped calling release_control at battle end. Every
-- controller we ever create is therefore kept alive here for the rest of the game session.
S2AI_CONTROLLER_GRAVEYARD = S2AI_CONTROLLER_GRAVEYARD or {}
local GRAVEYARD = S2AI_CONTROLLER_GRAVEYARD

local function bury(uc)
	if uc then GRAVEYARD[#GRAVEYARD + 1] = uc end
end

local function take(id)
	if B.controlled[id] then return B.controlled[id] end
	local e = B.units[id]
	if not e then return nil, "no unit " .. tostring(id) end
	local ok, uc = pcall(function()
		local c = e.ship and e.army:create_ship_controller() or e.army:create_unit_controller()
		bury(c) -- alive for the whole session, whatever happens next (see GRAVEYARD)
		if e.ship then c:add_ships(e.unit) else c:add_units(e.unit) end
		c:take_control()
		return c
	end)
	if not ok then return nil, tostring(uc) end
	if e.ship and not B.ship_api_logged then
		B.ship_api_logged = true
		log("ship methods: " .. method_names(e.unit))
		log("ship controller methods: " .. method_names(uc))
	end
	B.controlled[id] = uc
	return uc
end

-- release_control() on a unit the engine has already taken down crashes the game (CE.dll+0x2f6703 reads a null
-- unit field; seen when a campaign battle ended). So only hand back units that are verifiably still on the field,
-- and once the battle is over never touch the engine's controllers again: just forget them.
local function on_field(id)
	local e = B.units and B.units[id]
	if not e or B.over then return false end
	local men = call(e.unit, "number_of_men_alive")
	if e.ship then return men ~= nil and men > 0 and not call(e.unit, "is_surrendered") end
	return men ~= nil and men > 0 and not call(e.unit, "is_leaving_battle")
end

local function release(id)
	local uc = B.controlled[id]
	if uc and on_field(id) then pcall(function() uc:release_control() end) end
	bury(uc)
	B.controlled[id] = nil
end

local function release_all(reason)
	local n = 0
	for id in pairs(B.controlled) do release(id); n = n + 1 end
	if n > 0 then log("released " .. n .. " units: " .. reason) end
end

local function forget_all(reason)
	local n = 0
	for _, uc in pairs(B.controlled) do n = n + 1; bury(uc) end
	B.controlled = {}
	if n > 0 then log("dropped " .. n .. " controllers without engine calls: " .. reason) end
end

-- ------------------------------------------------------------------ orders

local HANDLERS = {
	move = function(uc, o)
		local run = o.run == true
		if o.facing then
			-- the unit can be gone from the battle by now (destroyed): fall back to a default width
			local e = B.units[o.unit]
			local width = o.width or (e and call(e.unit, "ordered_width")) or 40
			uc:goto_location_angle_width(vec(o.x, o.y), o.facing, width, run)
		else
			uc:goto_location(vec(o.x, o.y), run)
		end
	end,
	attack = function(uc, o)
		local target = B.units[o.target]
		if not target then error("no target unit " .. tostring(o.target)) end
		-- (target, bool, run): the game's scripts pass (enemy, true|false, true); meaning of arg 2 not confirmed
		uc:attack_unit(target.unit, o.flag == true, o.run ~= false)
	end,
	halt = function(uc) uc:halt() end,
	fire_at_will = function(uc, o) uc:fire_at_will(o.on ~= false) end,
	skirmish = function(uc, o) uc:skirmish(o.on ~= false) end,
	melee = function(uc, o) uc:melee(o.on ~= false) end,
	formation = function(uc, o) uc:change_formation(o.formation) end,
	ability = function(uc, o) uc:perform_special_ability(o.ability) end,
	-- sieges (argument conventions as in the game's own battle scripts)
	-- sieges: the building is fetched fresh from the garrisonable list (its id is the index there)
	garrison = function(uc, o) uc:defend_building(B.building_handle(o.building) or error("no building " .. tostring(o.building)), o.run ~= false) end,
	climb = function(uc, o) uc:climb_building(B.building_handle(o.building) or error("no building " .. tostring(o.building)), 1, o.run ~= false) end,
	-- defenders stationed on a wall section / tower / gate. The Battle Script Documentation: occupy_zone(vector,
	-- move_fast) "instructs units to occupy a zone near the location specified, if found. A battlefield zone is a
	-- defensible position, like a castle wall" - the defenders' "station on the wall". (climb_building "scales"
	-- a building: the attackers' ladders; defend_building is for enterable buildings and errors on walls.)
	-- Fallbacks if the game refuses: climb_building, then a plain move. The first outcome of each is logged.
	man_wall = function(uc, o)
		local run = o.run ~= false
		local ok, err = pcall(function() uc:occupy_zone(vec(o.x, o.y), run) end)
		local how = "occupy_zone"
		if not ok then
			local b = B.building_handle(o.building)
			if b then
				ok, err = pcall(function() uc:climb_building(b, 1, run) end)
				how = "climb_building (occupy_zone refused: " .. tostring(err) .. ")"
			end
			b = nil
		end
		if not ok then
			uc:goto_location_angle_width(vec(o.x, o.y), o.facing or 0, 20, run)
			how = "move (occupy_zone and climb_building refused: " .. tostring(err) .. ")"
		end
		B.man_wall_logged = B.man_wall_logged or {}
		local kind = how:match("^%S+")
		if not B.man_wall_logged[kind] then
			B.man_wall_logged[kind] = true
			log("man_wall: units sent onto walls with " .. how)
		end
	end,
	attack_building = function(uc, o) uc:attack_building(B.building_handle(o.building) or error("no building " .. tostring(o.building))) end,
	occupy = function(uc, o) uc:occupy_zone(vec(o.x, o.y), o.run ~= false) end,
	leave_building = function(uc) uc:leave_building() end,
	shot_type = function(uc, o) uc:change_shot_type(o.shot) end,
	withdraw = function(uc, o) uc:withdraw(o.run ~= false) end,
}

-- Ship controllers (the game's naval scripts: goto_location(v, "full sail"), attack_ship(ship), withdraw(true),
-- fire_at_will(bool)). The first outcome of each kind of call is logged, so a test battle shows what the engine
-- takes.
local function ship_log_once(what, msg)
	B.ship_logged = B.ship_logged or {}
	if not B.ship_logged[what] then
		B.ship_logged[what] = true
		log("ship " .. what .. ": " .. msg)
	end
end

local SHIP_HANDLERS = {
	move = function(uc, o)
		-- the game's scripts only ever pass "full sail"; a plain goto_location(v) is tried for the slower pace
		if o.run == true then
			uc:goto_location(vec(o.x, o.y), "full sail")
			ship_log_once("move (full sail)", "goto_location(v, \"full sail\") ok")
		else
			local ok, err = pcall(function() uc:goto_location(vec(o.x, o.y)) end)
			if ok then
				ship_log_once("move", "goto_location(v) ok")
			else
				uc:goto_location(vec(o.x, o.y), "full sail")
				ship_log_once("move", "goto_location(v) refused (" .. tostring(err) .. "), full sail used")
			end
		end
	end,
	attack = function(uc, o)
		local target = B.units[o.target]
		if not target then error("no target ship " .. tostring(o.target)) end
		uc:attack_ship(target.unit)
		ship_log_once("attack", "attack_ship ok")
	end,
	withdraw = function(uc) uc:withdraw(true) end,
	-- the shot type's name, as the game's scripts pass it (change_shot_type("flaming_arrows") for archers)
	shot_type = function(uc, o)
		uc:change_shot_type(o.shot)
		ship_log_once("shot_type", "change_shot_type(\"" .. tostring(o.shot) .. "\") ok")
	end,
	fire_at_will = function(uc, o) uc:fire_at_will(o.on ~= false) end,
	halt = function(uc) uc:halt() end,
}

-- The game's own tactical AI for a whole alliance, as the historical battles script it
-- (Alliance:create_ai_unit_planner(), add_units, attack_unit / defend_position). For research runs the human's
-- side is handed to it, so the general fights a fixed opponent instead of a person. Orders carry `alliance`
-- instead of `unit`. The planner is created once per battle with every unit of the alliance and kept alive
-- like the controllers (GRAVEYARD). The first outcome of each call is logged.
local function ai_log_once(what, msg)
	B.ai_logged = B.ai_logged or {}
	if not B.ai_logged[what] then
		B.ai_logged[what] = true
		log("ai planner " .. what .. ": " .. msg)
	end
end

local function ai_planner(a)
	B.planners = B.planners or {}
	if B.planners[a] then return B.planners[a] end
	if B.naval then error("no ai planner in naval battles") end
	local alliance = B.battle:alliances():item(a)
	local p = alliance:create_ai_unit_planner()
	bury(p)
	local n = 0
	local armies = alliance:armies()
	for r = 1, armies:count() do
		local list = armies:item(r):units()
		for u = 1, list:count() do
			p:add_units(list:item(u))
			n = n + 1
		end
	end
	B.planners[a] = p
	log("ai planner: alliance " .. a .. " handed to the game's AI, " .. n .. " units; methods: " .. method_names(p))
	return p
end

local AI_HANDLERS = {
	ai_attack = function(o)
		local target = B.units[o.target]
		if not target then error("no target unit " .. tostring(o.target)) end
		ai_planner(o.alliance):attack_unit(target.unit)
		ai_log_once("attack", "attack_unit ok")
	end,
	-- sieges: the attacking side's AI takes the castle (run 220312: attack_unit on a defender made the planner
	-- march away from the castle, presumably looking for a way round). capture_settlement is a planner method
	-- that no game script calls, so its arguments are unknown: none, then a position, then attack_unit.
	ai_capture = function(o)
		local p = ai_planner(o.alliance)
		local ok, err = pcall(function() p:capture_settlement() end)
		if ok then ai_log_once("capture", "capture_settlement() ok"); return end
		local ok2, err2 = pcall(function() p:capture_settlement(vec(o.x, o.y)) end)
		if ok2 then
			ai_log_once("capture", "capture_settlement(v) ok (without arguments: " .. tostring(err) .. ")")
			return
		end
		ai_log_once("capture", "capture_settlement refused (" .. tostring(err) .. " / " .. tostring(err2)
			.. "), attack_unit used")
		local target = B.units[o.target]
		if not target then error("no target unit " .. tostring(o.target)) end
		p:attack_unit(target.unit)
	end,
	ai_defend = function(o)
		ai_planner(o.alliance):defend_position(vec(o.x, o.y), o.radius or 100)
		ai_log_once("defend", "defend_position ok")
	end,
}

-- The orders file is a Lua table literal written by the server. It runs in an empty environment, as source
-- text only (precompiled bytecode can escape a setfenv sandbox, so it is refused) and with an instruction budget
-- (so a broken file cannot hang the battle).
local ORDERS_MAX_BYTES = 1000000
local ORDERS_MAX_INSTRUCTIONS = 2000000

local function read_orders()
	local f = io.open(ORDERS_PATH, "rb")
	if not f then return nil end
	local text = f:read(ORDERS_MAX_BYTES + 1)
	f:close()
	if not text or #text > ORDERS_MAX_BYTES or text:byte(1) == 27 then return nil end
	local chunk = loadstring(text, "=orders.lua")
	if not chunk then return nil end
	setfenv(chunk, {})
	-- keep whatever hook the engine has installed (it ships a debugger hook) and put it back afterwards
	local okg, old_fn, old_mask, old_count = pcall(debug.gethook)
	local hooked = pcall(debug.sethook, function() error("orders file runs too long", 2) end, "",
		ORDERS_MAX_INSTRUCTIONS)
	local ok, t = pcall(chunk)
	if hooked then
		if okg and old_fn then pcall(debug.sethook, old_fn, old_mask, old_count) else pcall(debug.sethook) end
	end
	if ok and type(t) == "table" then return t end
	return nil
end
B._read_orders = read_orders -- for tools/test_bridge.py

-- Is the server alive? It bumps `beat` in every write (also with no new orders, every few seconds). The game's Lua
-- numbers are 32-bit floats, so wall-clock times cannot be compared here (os.time() comes out in 128 s steps,
-- which used to release the army every ~2 minutes): staleness is measured in the bridge's own battle ticks.
local function server_alive(t)
	if t and t.battle == B.id and t.beat ~= nil and t.beat ~= B.beat then
		B.beat, B.beat_tick = t.beat, B.tick
	end
	return B.beat_tick ~= nil and (B.tick - B.beat_tick) * TICK_MS / 1000 <= STALE_S
end

local function apply_orders()
	local t = read_orders()
	local alive = server_alive(t)
	if not alive then
		if next(B.controlled) then
			release_all(string.format("no word from the server for %d s of battle time", STALE_S))
		end
		return
	end
	if not t or t.battle ~= B.id then return end
	if type(t.say) == "string" and t.say ~= B.said then
		B.said = t.say
		pcall(function() B.battle:show_advisor_message("S2AI " .. t.say, t.say) end)
	end
	if t.seq == B.seq then return end
	B.seq = t.seq

	local results = {}
	-- control set first
	local wanted = {}
	for _, id in ipairs(t.take or {}) do wanted[id] = true end
	for id in pairs(B.controlled) do
		if not wanted[id] then release(id) end
	end
	for id in pairs(wanted) do
		local uc, err = take(id)
		if not uc then results[#results + 1] = { unit = id, cmd = "take", err = err } end
	end
	-- then the orders
	for i, o in ipairs(t.orders or {}) do
		-- in a naval battle every controller is a ship controller (also for a ship sunk since the order was written)
		local handler = B.naval and SHIP_HANDLERS[o.cmd] or (not B.naval and HANDLERS[o.cmd]) or nil
		local uc = B.controlled[o.unit]
		local err
		if o.alliance ~= nil then
			-- an order for a whole alliance's game AI (no unit controller involved)
			if not AI_HANDLERS[o.cmd] then err = "unknown cmd"
			else
				local ok, e = pcall(AI_HANDLERS[o.cmd], o)
				if not ok then err = tostring(e) end
			end
		elseif not handler then err = "unknown cmd"
		elseif not uc then err = "unit not controlled"
		else
			local ok, e = pcall(handler, uc, o)
			if not ok then err = tostring(e) end
		end
		if err then results[#results + 1] = { i = i, unit = o.unit, cmd = o.cmd, err = err } end
	end
	emit({ type = "applied", seq = t.seq, n = #(t.orders or {}), errors = results })
end

-- ------------------------------------------------------------------ engine callbacks

function S2AI_Bridge_Tick()
	if not B.battle or B.over then return end
	local ok, err = pcall(function()
		B.tick = B.tick + 1
		if B.tick == 1 then log("first tick: the battle timer runs") end
		local known = B.units or {}
		scan()
		-- units that were not there before (reinforcements arriving, armies joining): send their static info,
		-- or the server cannot tell what they are
		local added = {}
		for _, id in ipairs(sorted_ids()) do
			if not known[id] and B.static_sent and not B.static_sent[id] then
				added[#added + 1] = static_record(id, B.units[id])
				B.static_sent[id] = true
			end
		end
		if #added > 0 then emit({ type = "units", tick = B.tick, units = added }) end
		local units = {}
		for _, id in ipairs(sorted_ids()) do units[#units + 1] = state_record(id, B.units[id]) end
		local bstate = nil
		if B.forts and #B.forts > 0 and B.tick % 20 == 3 then bstate = building_states() end -- every 10 s
		emit({ type = "state", tick = B.tick, t = B.tick * TICK_MS / 1000, gt = call(B.battle, "game_time"),
			phase = B.phase, units = units, buildings = bstate })
		apply_orders()
	end)
	if not ok then
		B.errors = (B.errors or 0) + 1
		if B.errors <= 20 then log("TICK ERROR: " .. tostring(err)) end
	end
end

local PHASE_EVENTS = {
	BattleDeploymentPhaseCommenced = "deployment",
	BattleConflictPhaseCommenced = "conflict",
	BattleCompleted = "complete",
}
local FORWARD_EVENTS = {
	BattleUnitRouts = true, BattleCommandingUnitRouts = true, BattleUnitAttacksEnemyUnit = true,
}

local function on_event(name)
	if not B.battle then return end
	if PHASE_EVENTS[name] and B.phase ~= PHASE_EVENTS[name] then
		B.phase = PHASE_EVENTS[name]
		emit({ type = "event", name = name, phase = B.phase, tick = B.tick })
		if B.phase == "complete" then
			-- the engine is tearing the battle down: no more engine calls from us (see release())
			B.over = true
			forget_all("battle completed")
			emit({ type = "battle_end", tick = B.tick })
		end
	elseif FORWARD_EVENTS[name] then
		emit({ type = "event", name = name, tick = B.tick })
	end
end

-- ------------------------------------------------------------------ buildings (sieges)
-- battle:buildings() lists every building on the map: fortifications (walls, gates, towers, keep), houses,
-- scenery. Building methods (run 13): name, position, central_position, health, capacity (garrison size),
-- alliance_owner_id (0-based alliance, -1 = nobody), is_garrisoned, currently_garrisoned, plus mutators
-- (destroy, hide, change_on_fire, ...) that are never called. A building's id is its list index.

local FORT_NAME = { "castle", "fort", "wall", "gate", "tower", "keep", "corner", "palisade", "stockade", "bastion",
	"turret", "barbican", "yagura" }

local function is_military(name, cap, owner)
	if (cap or 0) > 0 or (owner or -1) >= 0 then return true end
	name = tostring(name):lower()
	for _, w in ipairs(FORT_NAME) do if name:find(w, 1, true) then return true end end
	return false
end

local function num(v) return type(v) == "number" and v or nil end

local function owner_alliance(b)
	local o = num(call(b, "alliance_owner_id"))
	return (o and o >= 0) and (o + 1) or 0 -- 1-based like units' alliances; 0 = nobody
end

-- What the game showed so far (runs 13-17):
--   OK:     discovery - at battle LOAD, the first 200 items, every handle read and dropped (nothing kept)
--   broken: keeping the handles of all 368 items (at load: run 14; on the first tick: run 17) - handles past
--           the ~23rd came back as garbage and the battle-script timer died
-- So, step 1 of sieges reproduces the discovery conditions exactly: once, at load, at most SCAN_LIMIT items,
-- plain data copied out, no handle kept. Each handle's class is recognised without calling anything: its
-- metatable carries the building methods itself (run 16 showed it is not the registry's class table).
local SCAN_LIMIT = 200

local function looks_like_building(b)
	local mt = type(b) == "userdata" and getmetatable(b) or nil
	return type(mt) == "table" and type(rawget(mt, "central_position")) == "function"
		and type(rawget(mt, "capacity")) == "function" and type(rawget(mt, "alliance_owner_id")) == "function"
		and type(rawget(mt, "is_garrisoned")) == "function"
end

-- Run 19 (diagnostic): from inside a coroutine the engine's calls return garbage (alliances():count() and
-- building names came back as functions): the bindings push their results onto the battle script's MAIN
-- thread, not the caller's. So no coroutines - and everything stays in the main thread.
-- Runs 17/18: reading the full list broke right where the castle pieces end and the scenery begins (items 24,
-- 20, 67 on three maps); the castle pieces themselves always read fine. The list also offers
-- garrisonable_count() / garrisonable_item(i): only buildings that can hold troops - walls, towers, gates -
-- exactly what a siege needs, and no scenery. Step 3 reads only that list, calling the methods in the order of
-- the discovery run that worked, and stops at the first wrongly typed value. A building's id is its index in
-- the garrisonable list. (The keep holds no troops, so it is not in the list.)
local function read_garrisonable(list, i)
	local b = call(list, "garrisonable_item", i)
	if not looks_like_building(b) then return nil, "not a building" end
	-- discovery-run order: alliance_owner_id, capacity, currently_garrisoned, health, is_garrisoned, name, position
	local owner_id = call(b, "alliance_owner_id")
	local cap = call(b, "capacity")
	local gar = call(b, "currently_garrisoned")
	local hp = call(b, "health")
	local name = call(b, "name")
	local p = call(b, "position")
	local x, z_, y = p and call(p, "get_x"), p and call(p, "get_y"), p and call(p, "get_z")
	if type(name) ~= "string" then return nil, "name is a " .. type(name) end
	for _, v in ipairs({ owner_id, cap, hp, x, y, z_ }) do
		if v ~= nil and type(v) ~= "number" then return nil, "a value is a " .. type(v) end
	end
	return { name = name, x = x, y = y, z = z_, cap = cap, hp = hp, gar = gar == true,
		owner = (owner_id and owner_id >= 0) and (owner_id + 1) or 0 }
end

function scan_buildings() -- assigns the local declared at the top
	B.forts = {}
	local list = call(B.battle, "buildings")
	local total = list and num(call(list, "count")) or 0
	local n = list and num(call(list, "garrisonable_count")) or 0
	local out, why = {}, nil
	for i = 1, math.min(n, SCAN_LIMIT) do
		local r, err = read_garrisonable(list, i)
		if not r then why = string.format("item %d: %s - scan stopped", i, err); break end
		B.forts[#B.forts + 1] = i
		out[#out + 1] = { id = i, name = r.name, x = r.x, y = r.y, z = r.z, cap = r.cap, owner = r.owner, hp = r.hp,
			gar = r.gar, mil = true }
	end
	local check = num(call(call(B.battle, "alliances"), "count"))
	log(string.format("buildings: %d on the map, %d garrisonable, %d read%s; engine check: %s", total, n, #out,
		why and (" (" .. why .. ")") or "", check and ("ok, " .. check .. " alliances") or "FAILED"))
	return out
end

-- Step 4: the fortifications' changing state (damage, captures, garrisons), re-read every 10 s from the same
-- garrisonable list the initial read proved safe (runs 20-21), same calls in the same order: [id, health,
-- owner, garrisoned]. The engine is checked after every update; one failure switches updates off for the rest of
-- the battle, so they can never take the battle down with them.
function building_states() -- assigns the local declared at the top
	if B.bstate_off or not B.forts or #B.forts == 0 then return nil end
	local list = call(B.battle, "buildings")
	local n = list and num(call(list, "garrisonable_count")) or 0
	local out = {}
	for i = 1, math.min(n, SCAN_LIMIT) do
		local b = call(list, "garrisonable_item", i)
		if not looks_like_building(b) then break end
		local owner_id = call(b, "alliance_owner_id")
		local cap = call(b, "capacity")
		local gar = call(b, "currently_garrisoned")
		local hp = call(b, "health")
		if (owner_id ~= nil and type(owner_id) ~= "number") or (hp ~= nil and type(hp) ~= "number") then
			log(string.format("building update: item %d gave a %s / %s - updates stop", i, type(owner_id), type(hp)))
			B.bstate_off = true
			break
		end
		out[#out + 1] = { i, hp, (owner_id and owner_id >= 0) and (owner_id + 1) or 0, gar == true and 1 or 0 }
		b = nil
	end
	local check = num(call(call(B.battle, "alliances"), "count"))
	if not check then
		log("building update: engine check FAILED - building updates switched off for this battle")
		B.bstate_off = true
	end
	return out
end

-- a fresh garrisonable building for a siege order, used at once and dropped
local function building_handle(i)
	local list = call(B.battle, "buildings")
	local b = list and call(list, "garrisonable_item", i) or nil
	return looks_like_building(b) and b or nil
end
B.building_handle = building_handle

-- ------------------------------------------------------------------ start

local function start()
	B.battle = empire_battle:new()
	B.id = os.date("%Y%m%d-%H%M%S")
	B.stream = RUN .. "/battle-" .. B.id .. ".jsonl"
	B.tick, B.seq, B.controlled, B.phase, B.said = 0, nil, {}, "deployment", nil
	B.beat, B.beat_tick, B.over, B.static_sent, B.keys, B.next_n, B.track, B.army_n = nil, nil, false, {}, {}, {}, {}, {}
	B.naval, B.ship_api_logged, B.ship_logged = false, false, {}

	local armies = {}
	local n_units = 0
	local alliances = B.battle:alliances()
	for a = 1, alliances:count() do
		local list = alliances:item(a):armies()
		for r = 1, list:count() do
			local army = list:item(r)
			local n = call(call(army, "units"), "count") or 0
			armies[#armies + 1] = { alliance = a, army = r, handicap = call(army, "army_handicap"), units = n,
				_army = army }
			n_units = n_units + n
		end
	end
	-- a naval battle: no army has land units. Only then are the ships read (land battles never call ships()).
	if n_units == 0 then
		local n_ships = 0
		for _, ar in ipairs(armies) do
			ar.ships = call(call(ar._army, "ships"), "count") or 0
			n_ships = n_ships + ar.ships
		end
		B.naval = n_ships > 0
		log(string.format("no land units: %d ships - %s", n_ships, B.naval and "naval battle" or "nothing to command"))
	end
	for _, ar in ipairs(armies) do ar._army = nil end
	scan()
	for id in pairs(B.units) do B.static_sent[id] = true end
	local units = {}
	for _, id in ipairs(sorted_ids()) do units[#units + 1] = static_record(id, B.units[id]) end
	emit({ type = "battle_start", version = 1, tick_ms = TICK_MS, armies = armies, units = units, naval = B.naval })
	-- buildings: once, here at load, before the timer (the discovery run's conditions)
	local okb, buildings = pcall(scan_buildings)
	if okb then
		emit({ type = "buildings", buildings = buildings })
	else
		log("BUILDINGS ERROR: " .. tostring(buildings))
		B.forts = {}
	end
	pcall(function()
		local f = io.open(RUN .. "/current_battle.txt", "w")
		f:write(B.id)
		f:close()
	end)

	B.battle:register_repeating_timer("S2AI_Bridge_Tick", TICK_MS)
	if type(events) == "table" then
		for name in pairs(PHASE_EVENTS) do
			if type(events[name]) == "table" then table.insert(events[name], function() pcall(on_event, name) end) end
		end
		for name in pairs(FORWARD_EVENTS) do
			if type(events[name]) == "table" then table.insert(events[name], function() pcall(on_event, name) end) end
		end
	end
	log("bridge started: battle " .. B.id .. ", " .. #units .. (B.naval and " ships" or " units") .. ", stream " .. B.stream)
	if B.naval and B.units then
		for _, e in pairs(B.units) do
			log("ship methods (read side): " .. method_names(e.unit))
			break
		end
	end
	pcall(function() B.battle:show_advisor_message("S2AI bridge active", "S2AI bridge active: battle " .. B.id) end)
end

local ok, err = pcall(start)
if not ok then log("START ERROR: " .. tostring(err)) end
