"""Compiles the loose files tools/install.ps1 wrote into the game's data folders with Lua 5.1, and runs each
historical-battle loader once in a sandbox WITHOUT io/os/dofile (as the battle-script state may be), checking
that it never raises - the battle's own script below the loader must always get to run.

    .venv\\Scripts\\python tools\\check_installed.py
"""
import pathlib
import re
import sys

import lupa.lua51 as lua51

DATA_DIRS = [
    pathlib.Path(r"F:\SteamLibrary\steamapps\common\Shogun 2 Complete Edition\data"),
    pathlib.Path(r"F:\SteamLibrary\steamapps\common\Shogun 2 Complete Edition\RemasteredCE\data"),
]
MARKER = "-- shogun2-battle-ai loader"
END = "-- end of shogun2-battle-ai loader"

ok = True
for d in DATA_DIRS:
    for path in list(d.glob("battle_scripted.lua")) + list(d.glob("shogun_historical_battles/*/*.battle_script")):
        text = path.read_text(encoding="utf-8")
        if MARKER not in text:
            continue
        rt = lua51.LuaRuntime()
        res = rt.globals().loadstring(text, "@" + path.name)
        if isinstance(res, tuple) and res[0] is None:
            print(f"SYNTAX ERROR {path}: {res[1]}")
            ok = False
            continue
        msg = "compiles"
        if path.suffix == ".battle_script":
            loader = text[text.index(MARKER): text.index(END)]
            rt = lua51.LuaRuntime()
            rt.execute("io, os, dofile = nil, nil, nil")
            try:
                rt.execute(loader)  # empire_battle is nil here too: the probe must just give up quietly
                msg += ", loader survives a sandbox without io/os/dofile"
            except Exception as e:  # noqa: BLE001
                msg = f"LOADER RAISED in sandbox: {e}"
                ok = False
        print(f"{'ok ' if 'RAISED' not in msg else 'BAD'} {path.relative_to(d.parent)}: {msg}")

print("\nALL OK" if ok else "\nFAILED")
sys.exit(0 if ok else 1)
