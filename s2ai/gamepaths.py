"""Finds Total War: SHOGUN 2 - Complete Edition through Steam (registry -> library folders), so no tool has to
hard-code where the game lives. Remastered CE, if installed, sits in the game folder as RemasteredCE/."""
from __future__ import annotations

import re
from pathlib import Path

GAME_DIR_NAME = "Shogun 2 Complete Edition"


def steam_root() -> Path | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        return None


def steam_libraries() -> list[Path]:
    root = steam_root()
    if root is None:
        return []
    libs = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    try:
        for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")):
            libs.append(Path(m.group(1).replace("\\\\", "\\")))
    except OSError:
        pass
    seen, out = set(), []
    for p in libs:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def game_dirs() -> list[Path]:
    return [d for lib in steam_libraries() if (d := lib / "steamapps" / "common" / GAME_DIR_NAME).is_dir()]


def data_dirs() -> list[Path]:
    """Every data folder the game runs from: the Complete Edition's, and Remastered CE's if installed."""
    out = []
    for g in game_dirs():
        for sub in ("data", "RemasteredCE/data"):
            d = g / sub
            if (d / "data.pack").exists():
                out.append(d)
    return out


def engine_dlls() -> list[Path]:
    """Engine DLLs: Remastered CE's CE.dll (what its launcher runs) and the vanilla empire.retail.x64.dll."""
    out = []
    for g in game_dirs():
        for rel in ("RemasteredCE/CE.dll", "empire.retail.x64.dll"):
            if (g / rel).exists():
                out.append(g / rel)
    return out


if __name__ == "__main__":
    print("steam libraries:", *steam_libraries(), sep="\n  ")
    print("data folders:", *data_dirs(), sep="\n  ")
    print("engine DLLs:", *engine_dlls(), sep="\n  ")
