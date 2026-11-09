"""Checks the mod's Lua with a real Lua 5.1 (the game's version), without starting the game.

1. compiles every file under lua/,
2. runs lua/s2ai_frontend.lua the way the main menu does, with the native module missing, and checks it neither
   raises nor needs anything but io/os/package (the bridge itself is exercised by tools/test_bridge.py).

    .venv\\Scripts\\python tools\\check_lua.py
"""
import pathlib
import sys
import tempfile

import lupa.lua51 as lua51

REPO = pathlib.Path(__file__).resolve().parent.parent


def compile_all() -> bool:
    ok = True
    for path in sorted((REPO / "lua").rglob("*.lua")):
        rt = lua51.LuaRuntime()
        res = rt.globals().loadstring(path.read_text(encoding="utf-8"), "@" + path.name)
        if isinstance(res, tuple) and res[0] is None:
            print(f"SYNTAX ERROR {path.relative_to(REPO)}: {res[1]}")
            ok = False
        else:
            print(f"ok  {path.relative_to(REPO)}")
    return ok


def run_frontend() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "runtime").mkdir()
        rt = lua51.LuaRuntime()
        rt.globals().S2AI_ROOT = str(root).replace("\\", "/")
        try:
            rt.globals().dofile(str(REPO / "lua" / "s2ai_frontend.lua"))
        except Exception as e:  # noqa: BLE001
            print(f"FAIL: s2ai_frontend.lua raised: {e}")
            return False
        log = (root / "runtime" / "frontend.log").read_text()
        print("frontend.log:", log.strip())
        if "native module not loaded" not in log:
            print("FAIL: expected a 'not loaded' note without the DLL")
            return False
    return True


if __name__ == "__main__":
    good = compile_all() and run_frontend()
    print("\nALL OK" if good else "\nFAILED")
    sys.exit(0 if good else 1)
