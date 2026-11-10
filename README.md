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
| Campaign battles, including reinforcements arriving mid-battle | ✅ |
| Sieges | ⚠️ the general fights them, but does not see walls, gates and towers yet (in progress) |
| Naval battles | ❓ not tested |
| Terrain (hills, forests, rivers) | ❌ the general does not see it yet (unit altitude is recorded) |
| Live map and replays in the browser, also from other devices on your network | ✅ |

## Requirements

- Windows 10/11, *Total War: SHOGUN 2 - Complete Edition* on Steam (Remastered CE optional).
- Python 3.12 or newer (`winget install Python.Python.3.14`).
- [Claude Code](https://claude.com/claude-code), the **native** build (installer from claude.com, not the npm
  package), installed and logged in: the general runs on your Claude subscription through it. Each plan uses
  roughly 7-10k tokens; a 7-minute battle needs about 30 plans.
- [Zig](https://ziglang.org) to build the small native module (`winget install zig.zig`, then open a new terminal
  so `zig` is on the PATH).

## Install

Put the repository in a folder whose path has only plain ASCII characters (for example `C:\Games\`): the game
cannot open paths with other letters, such as a Cyrillic user name.

```powershell
git clone https://github.com/dmytro-kushnir/shogun2-battle-ai.git C:\Games\shogun2-battle-ai
cd C:\Games\shogun2-battle-ai
py -3 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\native\build.ps1   # builds native\s2ai_native.dll, tests the patch offline
powershell -ExecutionPolicy Bypass -File .\tools\install.ps1  # adds the mod's pack to the game's data folder(s)
```

(`-ExecutionPolicy Bypass` because Windows blocks unsigned scripts by default. If you downloaded a ZIP instead of
cloning, run `Get-ChildItem -Recurse | Unblock-File` in the folder first.)

`install.ps1` finds the game through Steam. It writes a small pack into each data folder of the game (`s2ai_battle.pack`, and `s2ai_battle_rce.pack` for Remastered CE: its launcher refuses two enabled packs with the same name)
(Complete Edition, and Remastered CE's if installed), plus loose copies of the scripts in it: the game's own
`frontend_scripted.lua` and five historical battle scripts with the mod's loader added (taken from your copy of
the game), and `s2ai\custom.battle_script`. Keep the repository where it is after installing: the game loads the
mod's code from this folder (if you move it, run `install.ps1` again).

## Play

1. Start the AI server and leave it running:

   ```powershell
   .\.venv\Scripts\python -m s2ai.server
   ```

   It opens the battle map at <http://127.0.0.1:8765>.
2. Start the game as usual (Steam, or the Remastered CE launcher). At the main menu the mod's native module enables
   scripts for custom and campaign battles (see `runtime\native.log`).
3. Fight a battle. When it loads you see the advisor message **"S2AI bridge active"**. Once the fighting starts,
   the general takes over the computer's army; each new plan's intent is shown as an advisor message.

Options: `--model opus` (stronger, slower), `--effort medium`, `--ai-alliance 1` (if the server picks the wrong
side; it guesses from the armies' difficulty handicap), `--commander demo` (no model: every AI unit charges the
nearest enemy), `--lan` (let other devices on your network open the map; read-only and without a password, so only
on a network you trust).

If the server stops, the game's own AI takes the army back within 15 seconds.

> **Do not play multiplayer with the server running.** The server would take one side's army, which in a
> multiplayer battle may be a human's, and the game would go out of sync. Multiplayer has not been tested; to be
> safe, uninstall the mod (below) before playing online.

### Battle map and history

The map shows every unit (red: the AI, blue: you, yellow outline: under the general's control), the orders being
sent and the plan's positions, with the general's intent, reasoning and groups beside it. **History** replays any
past battle with a timeline; click a plan to jump to it and read the exact report the general saw.

### Teach the general

Edit [`rules.md`](rules.md) (facts about the game) and [`strategy.md`](strategy.md) (tactical advice) in plain
English, one point per line. Both are read again for every battle.

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\install.ps1 -Uninstall
```

removes the pack and the loose script copies from the game folders. Nothing else in the game was changed. The engine
patch only ever lives in the game's memory and ends when the game exits.

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
- **Bridge.** `lua/s2ai_bridge.lua` streams the battle as JSON lines and applies the orders file, a plain Lua table
  run as source only, in an empty environment and with an instruction budget. Every engine call is wrapped so an
  error in the mod cannot break the battle.
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
