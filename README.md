# Shogun 2 Battle AI

An experiment to let an LLM command the AI side of *Total War: SHOGUN 2* battles (Complete Edition and
Remastered CE), inspired by [rome-battle-ai](https://github.com/Cegli865/rome-battle-ai).

> Unofficial fan project. Not affiliated with SEGA or Creative Assembly. No game files are included.

## Idea

Rome Remastered has no scripting, so rome-battle-ai reads the battle from raw memory with an injected C hook.
Shogun 2 does not need that: its engine embeds **Lua 5.1 with the full standard library** and the official
**battle-script API** used by the historical battles:

- read: `alliances() / armies() / units()`, `unit:position()`, `bearing`, `number_of_men_alive`, `is_routing`,
  `ammo_left`, `missile_range`, ...
- order: `army:create_unit_controller()`, `uc:take_control()`, `goto_location_angle_width`, `attack_unit`,
  `change_formation`, `perform_special_ability`, `fire_at_will`, `withdraw`, ...
- time: `register_repeating_timer`, `register_battle_phase_handler`

The engine runs `data/battle_scripted.lua` at the start of every battle. The plan:

```
game (Lua, every 0.5 s)  --state.json-->  Python server (executor + reflexes)  --plan-->  LLM general
                         <--orders.json--
```

## Status: probe

`lua/s2ai_probe.lua` checks whether the approach works: does the script run in custom battles, can it write
files, what the API objects really offer, and can it take a unit from the game's AI.

```powershell
.\tools\install.ps1            # adds a loose data\battle_scripted.lua to each CE data folder
# play a custom battle, then look at runtime\probe.log and runtime\state.jsonl
.\tools\install.ps1 -Uninstall
```

## Development

```powershell
py -3.14 -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python tools\check_lua.py   # compile all Lua with 5.1 + run the probe against a mock battle API
```

## Tools

- `tools/packtool.ps1` - lists / extracts files from Shogun 2 packs (original `PFH3` and CE `PFHs` with LZ4).
