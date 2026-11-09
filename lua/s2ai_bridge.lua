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

-- ------------------------------------------------------------------ units

-- Stable unit ids. The engine compacts an army's unit list when a unit is destroyed, so a list position does not
-- identify a unit (at Sekigahara, ids built from positions pointed at other units after the first losses). A unit
-- keeps its name (historical battles: "Ukita_Hideie_3"; custom/campaign: "1", "2", ... per army) and its type, so
-- alliance + name + type (+ an occurrence number for exact duplicates) is the key; ids are handed out in order of
-- first sighting: alliance * 10000 + n.
local function unit_key(a, unit, seen)
	local key = a .. "|" .. tostring(call(unit, "name")) .. "|" .. tostring(call(unit, "type"))
	seen[key] = (seen[key] or 0) + 1
	return key .. "#" .. seen[key]
end

-- refresh B.units (id -> {unit, army, alliance}) from the battle; cheap, done every tick (reinforcements)
local function scan()
	local units, seen = {}, {}
	B.keys, B.next_n = B.keys or {}, B.next_n or {}
	local alliances = B.battle:alliances()
	for a = 1, alliances:count() do
		local armies = alliances:item(a):armies()
		for r = 1, armies:count() do
			local army = armies:item(r)
			local list = army:units()
			for u = 1, list:count() do
				local unit = list:item(u)
				local key = unit_key(a, unit, seen)
				local id = B.keys[key]
				if not id then
					B.next_n[a] = (B.next_n[a] or 0) + 1
					id = a * 10000 + B.next_n[a]
					B.keys[key] = id
				end
				units[id] = { unit = unit, army = army, alliance = a, army_index = r }
			end
		end
	end
	B.units = units
end

local function sorted_ids()
	local ids = {}
	for id in pairs(B.units) do ids[#ids + 1] = id end
	table.sort(ids)
	return ids
end

local function static_record(id, e)
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
	local u = e.unit
	local p = call(u, "position")
	return {
		id = id,
		x = p and call(p, "get_x"), y = p and call(p, "get_z"),
		h = call(u, "bearing"),
		w = call(u, "ordered_width"),
		men = call(u, "number_of_men_alive"),
		rout = call(u, "is_routing"), shat = call(u, "is_shattered"),
		mov = call(u, "is_moving"), run = call(u, "is_moving_fast"),
		ammo = call(u, "ammo_left"),
		gone = call(u, "is_leaving_battle"),
		ab = call(u, "current_special_ability"), ab_ok = call(u, "can_perform_special_ability"),
		ctl = B.controlled[id] ~= nil,
	}
end

-- ------------------------------------------------------------------ control

local function take(id)
	if B.controlled[id] then return B.controlled[id] end
	local e = B.units[id]
	if not e then return nil, "no unit " .. tostring(id) end
	local ok, uc = pcall(function()
		local c = e.army:create_unit_controller()
		c:add_units(e.unit)
		c:take_control()
		return c
	end)
	if not ok then return nil, tostring(uc) end
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
	return men ~= nil and men > 0 and not call(e.unit, "is_leaving_battle")
end

local function release(id)
	local uc = B.controlled[id]
	if uc and on_field(id) then pcall(function() uc:release_control() end) end
	B.controlled[id] = nil
end

local function release_all(reason)
	local n = 0
	for id in pairs(B.controlled) do release(id); n = n + 1 end
	if n > 0 then log("released " .. n .. " units: " .. reason) end
end

local function forget_all(reason)
	local n = 0
	for _ in pairs(B.controlled) do n = n + 1 end
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
	shot_type = function(uc, o) uc:change_shot_type(o.shot) end,
	withdraw = function(uc, o) uc:withdraw(o.run ~= false) end,
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
		local handler = HANDLERS[o.cmd]
		local uc = B.controlled[o.unit]
		local err
		if not handler then err = "unknown cmd"
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
		emit({ type = "state", tick = B.tick, t = B.tick * TICK_MS / 1000, gt = call(B.battle, "game_time"),
			phase = B.phase, units = units })
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

-- ------------------------------------------------------------------ start

local function start()
	B.battle = empire_battle:new()
	B.id = os.date("%Y%m%d-%H%M%S")
	B.stream = RUN .. "/battle-" .. B.id .. ".jsonl"
	B.tick, B.seq, B.controlled, B.phase, B.said = 0, nil, {}, "deployment", nil
	B.beat, B.beat_tick, B.over, B.static_sent, B.keys, B.next_n = nil, nil, false, {}, {}, {}
	scan()
	for id in pairs(B.units) do B.static_sent[id] = true end

	local armies = {}
	local alliances = B.battle:alliances()
	for a = 1, alliances:count() do
		local list = alliances:item(a):armies()
		for r = 1, list:count() do
			local army = list:item(r)
			armies[#armies + 1] = { alliance = a, army = r, handicap = call(army, "army_handicap"),
				units = call(call(army, "units"), "count") }
		end
	end
	local units = {}
	for _, id in ipairs(sorted_ids()) do units[#units + 1] = static_record(id, B.units[id]) end
	emit({ type = "battle_start", version = 1, tick_ms = TICK_MS, armies = armies, units = units })
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
	log("bridge started: battle " .. B.id .. ", " .. #units .. " units, stream " .. B.stream)
	pcall(function() B.battle:show_advisor_message("S2AI bridge active", "S2AI bridge active: battle " .. B.id) end)
end

local ok, err = pcall(start)
if not ok then log("START ERROR: " .. tostring(err)) end
