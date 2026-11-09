-- shogun2-battle-ai: main menu hook. Runs from the game's frontend_scripted.lua (loader added by
-- tools/install.ps1) every time the main menu loads, and loads the native module, which patches the engine so
-- battles without a battle script (custom and campaign battles) start data/s2ai/custom.battle_script, i.e. the
-- bridge. The module is idempotent and logs to runtime/native.log. Nothing here may raise: the main menu's own
-- script must keep working.

local ROOT = S2AI_ROOT
if not ROOT then return end

local function log(msg)
	pcall(function()
		local f = io.open(ROOT .. "/runtime/frontend.log", "a")
		f:write(os.date("%Y-%m-%d %H:%M:%S"), " ", tostring(msg), "\n")
		f:close()
	end)
end

local ok, open, err = pcall(package.loadlib, ROOT .. "/native/s2ai_native.dll", "luaopen_s2ai_native")
if ok and open then
	local ok2, e2 = pcall(open)
	log(ok2 and "native module called (see native.log)" or ("native module failed: " .. tostring(e2)))
else
	log("native module not loaded (build it with native/build.ps1): " .. tostring(open) .. " " .. tostring(err))
end
