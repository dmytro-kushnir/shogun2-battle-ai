"""Checks what tools/install.ps1 wrote into the game's data folders, with Lua 5.1:
compiles every installed script, and runs each loader once in a sandbox WITHOUT io/os/dofile, checking it never
raises (the game's own script after or before the loader must always get to run).

    .venv\\Scripts\\python tools\\check_installed.py
"""
import pathlib
import sys

import lupa.lua51 as lua51

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from s2ai.gamepaths import data_dirs  # noqa: E402

MARKER = "-- shogun2-battle-ai loader"
END = "-- end of shogun2-battle-ai loader"

ok, seen = True, 0
for d in data_dirs():
    for path in [d / "frontend_scripted.lua", d / "s2ai" / "custom.battle_script",
                 *d.glob("shogun_historical_battles/*/*.battle_script")]:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if MARKER not in text:
            continue
        seen += 1
        res = lua51.LuaRuntime().globals().loadstring(text, "@" + path.name)
        if isinstance(res, tuple) and res[0] is None:
            print(f"SYNTAX ERROR {path}: {res[1]}")
            ok = False
            continue
        rt = lua51.LuaRuntime()
        rt.execute("io, os, dofile = nil, nil, nil")
        try:
            rt.execute(text[text.index(MARKER): text.index(END)])
            print(f"ok  {path.relative_to(d.parent)}")
        except Exception as e:  # noqa: BLE001
            print(f"BAD {path}: loader raised in a sandbox: {e}")
            ok = False
if not seen:
    print("nothing installed (run tools/install.ps1)")
print("\nALL OK" if ok else "\nFAILED")
sys.exit(0 if ok else 1)
