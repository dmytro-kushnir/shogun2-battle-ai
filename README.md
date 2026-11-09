# Shogun 2 Battle AI

An LLM commands the computer's army in *Total War: SHOGUN 2* battles. Claude reads the battle every few seconds,
makes a plan, and the mod carries it out through the game's own unit orders, while you watch it all on a live
tactical map in your browser.

> **Alpha.** Unofficial fan project, not affiliated with or endorsed by SEGA, Creative Assembly or Anthropic.
> *Total War* and *SHOGUN 2* are trademarks of SEGA; *Claude* is a trademark of Anthropic. No game files are
> included in this repository: the installer builds what it needs from your own copy of the game.

Inspired by [rome-battle-ai](https://github.com/Cegli865/rome-battle-ai), which does the same for Rome Remastered.
Works with the community [Remastered CE](https://github.com/JakeTheRipperYT/Remastered-CE) as well as with the
plain Complete Edition.

## What works (alpha)

| | |
|---|---|
| Custom battles (land) | ✅ played against the general |
| Historical battles from the menu (Kawanakajima, Nagashino, Okehazama, Sekigahara, Nagashima) | ✅ |
| Campaign battles | ⚠️ expected to work like custom battles, not tested yet |
| Sieges, naval battles, Fall of the Samurai specifics | ❓ not tested |
| Terrain (hills, forests, rivers) | ❌ the general does not see it yet |
| Live map and replays in the browser, also from other devices on your network | ✅ |

## Requirements

- Windows 10/11, *Total War: SHOGUN 2 - Complete Edition* on Steam (Remastered CE optional).
- Python 3.12 or newer (`winget install Python.Python.3.14`).
- [Claude Code](https://claude.com/claude-code), installed and logged in: the general runs on your Claude
  subscription through it. Each plan uses roughly 7-10k tokens; a 7-minute battle needs about 30 plans.
- [Zig](https://ziglang.org) to build the small native module (`winget install zig.zig`).

## Install

```powershell
git clone <this repository> shogun2-battle-ai
cd shogun2-battle-ai
py -3 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\native\build.ps1          # builds native\s2ai_native.dll and tests the engine patch offline on your game
.\tools\install.ps1         # adds s2ai_battle.pack to the game's data folder(s)
```

`install.ps1` finds the game through Steam. Keep the repository where it is after installing: the game loads the
mod's scripts from this folder (move it, then run `install.ps1` again).

## Play

1. Start the AI server and leave it running:

   ```powershell
   .\.venv\Scripts\python -m s2ai.server
   ```

   It opens the battle map at <http://127.0.0.1:8765> and prints its address on your local network.
2. Start the game as usual (Steam, or the Remastered CE launcher). At the main menu the mod's native module enables
   scripts for custom and campaign battles (see `runtime\native.log`).
3. Fight a battle. When it loads you see the advisor message **"S2AI bridge active"**. Once the fighting starts,
   the general takes over the computer's army; each new plan's intent is shown as an advisor message.

Options: `--model claude-opus-5-5` (stronger, slower), `--effort medium`, `--ai-alliance 1` (if the server picks the
wrong side; it guesses from the armies' difficulty handicap), `--commander demo` (no model: every AI unit charges
the nearest enemy), `--host 127.0.0.1` (map on this PC only).

If the server stops, the game's own AI takes the army back within 15 seconds.

### Battle map and history

The map shows every unit (red: the AI, blue: you, yellow outline: under the general's control), the orders being
sent and the plan's positions, with the general's intent, reasoning and groups beside it. **History** replays any
past battle with a timeline; click a plan to jump to it and read the exact report the general saw.

### Teach the general

Edit [`rules.md`](rules.md) (facts about the game) and [`strategy.md`](strategy.md) (tactical advice) in plain
English, one point per line. Both are read again for every battle.

## Uninstall

```powershell
.\tools\install.ps1 -Uninstall
```

removes the pack and the loose script copies from the game folders. Nothing else in the game was changed.

## How it works

```
 main menu ──> s2ai_frontend.lua ──> native/s2ai_native.dll: patches the engine IN MEMORY so battles
                                     without a script load ours (custom, campaign)
 battle    ──> lua/s2ai_bridge.lua (in the game's battle-script Lua)
                 │  every 0.5 s: runtime/battle-<id>.jsonl  (units, positions, men, state, events)
                 ▼
               s2ai/server.py ── report ──> Claude (claude -p) ── plan ──> executor ──┐
                 ▲                                                                      │
                 └──────────── runtime/orders.lua (take control, move, attack, ...) <──┘
```

- **Game side.** Shogun 2 embeds Lua 5.1 with the battle-scripting API its historical battles use (unit
  controllers, positions, orders). The engine only creates that scripting state when a battle names a script, so
  custom and campaign battles never had one. `native/s2ai_native.c`, loaded from the main menu's Lua with
  `package.loadlib`, changes two instructions in the running engine (found by byte signature, nothing written if
  anything does not match) so that a battle without a script loads `data/s2ai/custom.battle_script`; battles with a
  script keep theirs. Details: [`docs/engine-notes.md`](docs/engine-notes.md). Files on disk are never modified.
- **Bridge.** `lua/s2ai_bridge.lua` streams the battle as JSON lines and applies the orders file (sandboxed
  `loadfile`). Every engine call is wrapped so the battle can never break because of the mod.
- **General.** `s2ai/report.py` writes a compact battle report; Claude answers with a plan in a fixed JSON schema
  (`s2ai/plan.py`): groups of units with a task (attack / move / hold / withdraw), position, facing, and reflexes.
  `s2ai/executor.py` carries it out every half second without calling the model: lines by frontage, re-targeting,
  engaging what comes close, missile units keeping their distance.

## Development

```powershell
.\.venv\Scripts\python tools\check_lua.py        # Lua 5.1 compile check + main-menu hook in a sandbox
.\.venv\Scripts\python tools\test_bridge.py      # bridge + server end to end against a mock battle, no game
.\.venv\Scripts\python tools\check_installed.py  # the installed loaders compile and never raise
.\.venv\Scripts\python tools\try_general.py runtime\battle-<id>.jsonl --at 60   # one real plan on a recorded battle
.\tools\packtool.ps1 -Pack <pack> -Filter 'battle_script$'   # list / extract files from SHOGUN 2 packs (PFH3 and CE's PFHs)
```

`tools/re/` holds the static-analysis helpers used to find the engine patch (`pip install pefile capstone numpy`).

## License

[MIT](LICENSE)
