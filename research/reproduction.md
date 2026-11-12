# Reproduction

## What is needed

- Windows 10/11 and *Total War: SHOGUN 2 - Complete Edition* on Steam (the Remastered CE launcher is optional).
- A Claude subscription and the native Claude Code CLI, logged in (the general runs through `claude -p`).
- Python 3.12+, Zig (to build the native module). See the main [README](../README.md#install).

Without the game, the **analysis** can still be reproduced from released summaries and recordings
(`research/data/`, once frozen); the battles themselves cannot.

## Environment record (fill in for the study)

| Item | Value |
|---|---|
| Windows build | |
| Game build (Steam) / launcher (CE or Remastered CE) | |
| GPU, driver | |
| Game settings: unit size, graphics preset, battle speed | 1x |
| Claude Code version (`claude --version`) | |
| Model ids as reported in plans | |
| Repository commit / tag | `study-v1` |
| Python version, `pip freeze` | |

## Install and check

```powershell
git clone https://github.com/dmytro-kushnir/shogun2-battle-ai.git C:\Games\shogun2-battle-ai
cd C:\Games\shogun2-battle-ai
git checkout study-v1
py -3 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt -r research\requirements.txt
powershell -ExecutionPolicy Bypass -File .\native\build.ps1
powershell -ExecutionPolicy Bypass -File .\tools\install.ps1
.\.venv\Scripts\python tools\test_logic.py
.\.venv\Scripts\python tools\test_bridge.py
.\.venv\Scripts\python tools\check_installed.py
```

## Run one condition

See [experiments.md](experiments.md). In short: start the server with the condition's flags, start the game,
load the scenario, start the battle, do not touch the units, let it end, note the run in [runlog.md](runlog.md).

## What to record for a report

- the run log line (battle id, condition, scenario, anything unusual);
- `runtime/meta-<id>.json` must show the expected commit (no `-dirty`), condition and model;
- `runtime/bridge.log`: no `ERROR` lines, the planner lines present;
- the summary row.
