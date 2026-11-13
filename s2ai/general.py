"""The LLM general: Claude through the Claude Code CLI (`claude -p`, headless, on the user's subscription).

One `claude` process per battle with stream-json in/out, so the conversation (and the prompt cache) carries over
from plan to plan; restarted after CONVERSATION_PLANS plans to keep the context small. No tools, no MCP, no
settings: the model only reads the report and answers with a plan in the PLAN_SCHEMA.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .plan import PLAN_SCHEMA

log = logging.getLogger("s2ai.general")

REPO = Path(__file__).resolve().parent.parent
# Every plan stays in the conversation, so each new one costs more: in the first live battle plan #25 cost
# ~$0.79 API-equivalent against ~$0.03 for a fresh conversation. The report carries the current plan anyway.
CONVERSATION_PLANS = 6
TIMEOUT_S = 90
LIMIT_FALLBACK_S = 30 * 60  # when a usage-limit message has no reset time we can read


class UsageLimit(RuntimeError):
    """The subscription's usage limit is reached: no plans until `resume_at` (Unix time)."""

    def __init__(self, message: str, resume_at: float):
        super().__init__(message)
        self.resume_at = resume_at


LIMIT_RE = re.compile(r"(?i)hit your .{0,30}limit|usage limit|limit reached")
# "resets 12:10pm (Europe/Kyiv)", "resets Nov 14, 3pm", "resets Mon 9am", "will reset at 3pm"
RESET_RE = re.compile(r"(?i)resets?\s+(?:(?:at|on)\s+)?"
                      r"(?:(?P<wd>mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+(?:at\s+)?)?"
                      r"(?:(?P<mon>[a-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2}),?\s+(?:at\s+)?)?"
                      r"(?P<h>\d{1,2})(?::(?P<m>\d\d))?\s*(?P<ap>am|pm)?(?:\s*\((?P<tz>[^)]+)\))?")
MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
WEEKDAYS = "mon tue wed thu fri sat sun".split()


def limit_reset(text: str, now: float | None = None) -> float | None:
    """'You've hit your 5-hour limit · resets 12:10pm (Europe/Kyiv)' -> Unix time of the reset (+1 min);
    None if the text is not about a usage limit; now + LIMIT_FALLBACK_S if the time cannot be read."""
    if not LIMIT_RE.search(text or ""):
        return None
    now = time.time() if now is None else now
    epoch = re.search(r"\|(\d{10})\b", text)  # older CLIs: "Claude AI usage limit reached|<unix time>"
    if epoch:
        return int(epoch[1]) + 60
    m = RESET_RE.search(text)
    if not m:
        return now + LIMIT_FALLBACK_S
    tz = None
    if m["tz"]:  # the CLI prints the user's own zone; without a tz database (Windows) local time is used
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(m["tz"].strip())
        except Exception:  # noqa: BLE001 - no tz database (Windows without tzdata): local time
            tz = None
    base = dt.datetime.fromtimestamp(now, tz) if tz else dt.datetime.fromtimestamp(now)
    h, mi = int(m["h"]), int(m["m"] or 0)
    if m["ap"]:
        h = h % 12 + (12 if m["ap"].lower() == "pm" else 0)
    if h > 23 or mi > 59:
        return now + LIMIT_FALLBACK_S
    try:
        if m["mon"] and m["mon"].lower()[:3] in MONTHS:
            when = base.replace(month=MONTHS.index(m["mon"].lower()[:3]) + 1, day=int(m["day"]), hour=h, minute=mi,
                                second=0, microsecond=0)
            if when.timestamp() < now - 86400:
                when = when.replace(year=when.year + 1)
        else:
            when = base.replace(hour=h, minute=mi, second=0, microsecond=0)
            if m["wd"]:
                when += dt.timedelta(days=(WEEKDAYS.index(m["wd"].lower()[:3]) - when.weekday()) % 7)
            if when.timestamp() <= now:
                when += dt.timedelta(days=7 if m["wd"] else 1)
    except ValueError:  # "Feb 30" and the like
        return now + LIMIT_FALLBACK_S
    return when.timestamp() + 60

INSTRUCTIONS = """You are the commanding general of one army in a Total War: SHOGUN 2 battle, fighting against a
human player. Every few seconds you get a battle report and answer with a plan for your army. An executor carries
the plan out twice a second without you: it moves groups into lines, attacks targets, re-targets when a target
breaks, and runs the reflexes you set (engage_within_m: attack enemies that come close; keep_range_m: missile units
step back from melee threats). You only command YOUR units (the ids under YOUR UNITS).

How to answer:
- Put every one of your fighting units in exactly one group. Give each group one task.
- attack: `target` = an enemy id. move: `position` = [x, y] for the group's centre; the units form a line there
  facing `facing` (compass degrees; null = towards the enemy). hold: like move, `position` null = stay where they are.
- Use the reflexes: infantry lines usually engage_within_m 30-60; missile units keep_range_m 40-80 and
  engage_within_m 0; cavalry in reserve engage_within_m 0 until you commit it.
- Sieges (a FORTIFICATIONS section is in the report): a position next to a wall is on the ground, NOT on the wall.
  To put a unit ON a wall section, tower or gate, use task garrison with that building's id (one unit per
  building; pick the sections facing the enemy). Any infantry unit can man a wall: missile units shoot from the
  walls facing the attack, melee units (spears, swords) hold the sections and gates where ladders and the
  enemy's assault come; keep a reserve below to plug a breach. Cavalry stays off the walls.
  Attackers use climb on a wall section, attack_building on a gate, and occupy on the keep's area.
- Routes: `via` = up to 3 waypoints the group goes through before its task. Use it to go AROUND an enemy line
  instead of through its fire: the ENEMY FORMATION section gives flank routes that stay out of its small-arms
  range. A unit that is "in range of enemy ..." is being shot at, or can be.
- Special abilities: units that have them show "abilities: name (ready / ACTIVE / recharging)". Put one in
  `abilities` to use it now, once; only "ready" ones work; leave `abilities` empty otherwise (most plans). Each
  lasts tens of seconds and then recharges for minutes, so use it at the moment it matters: rapid_advance /
  banzai / warcry when the unit is under ~100 m from the enemy it is charging (earlier, the effect is spent
  before contact and the unit outruns its friends), stand_firm for a line about to break, rally when units nearby
  waver or rout, rapid_volley when the enemy is in range, second_wind for tired units.
- Cavalry hits and runs by itself: after the impact of its charge (about 12 s in melee, 6 s against spears or
  bayonets, sooner if it is losing men) it pulls back ~110 m and charges the same target again. It stays in the
  melee against artillery crews and units that are about to break. So give cavalry an `attack` on a target worth
  charging - guns, missile units, a flank or rear - and do not leave it on a spear or bayonet line's front.
- UNIT TYPES (at the start of a conversation) gives each type's stats from the game: compare melee/defence/charge,
  "+N vs cavalry" (spears; keep cavalry off them), morale and running speed (time a flank ride with the infantry's
  advance: cavalry covers ~10 m/s, foot ~4 m/s).
- Gunpowder battles (Boshin units: line infantry, riflemen, guns - the 1860s, not the age of katana charges):
  firepower decides, so fight a firefight, not a brawl. Form a firing line, make the enemy walk into it, and do
  not march a line across open ground into rifles that outrange it (compare the ranges in the report: a unit
  that outranges its enemy should keep that distance, keep_range_m about the difference). Concentrate several
  units' fire on one enemy unit until it breaks, then the next. Take crossfire: wing units or skirmishers that
  shoot into the side of the enemy line hurt it far more than fire from the front. Keep guns behind or beside
  the line with a clear view, firing at massed infantry and enemy guns. Melee units (spears, swords, levies
  without rifles) are a reserve: they guard the guns and flanks, receive cavalry, and charge only shaken or
  routing units or ones already pinned by fire. Cavalry goes round the flanks for guns and riflemen, never into
  a formed bayonet line. Higher ground and cover (woods, buildings, walls) protect riflemen - hold them. When the
  enemy out-shoots you, pull back out of range and re-form instead of trading volleys you lose. Close to bayonet
  range only when their line wavers or you outnumber them heavily there.
- Naval battles (units of kind "ship"): attack = close with that ship and fight it (the game fires broadsides and
  may board); move = sail to a position (ships in a group sail in line abreast); withdraw = leave the battle. Each
  ship shows its gun range, hull damage, which enemy ships can hit it and which it can hit. UNIT TYPES lists every
  ship's shot types with range, damage and reload. Set a group's `shot` to load one: armour-piercing segment shells
  against iron-armoured ships, explosive common shells (shorter range, they start fires) against wooden hulls,
  solid shot for the longest reach where it outranges the rest. Keep ships together so several fire at one enemy,
  stay at the range where your guns reach and theirs do not when you can, and finish damaged ships first.
  Ships sink at a reported hull damage of 10-50%, most around 20-25%: a ship at 15% is in danger (SINKING RISK),
  pull it back out of range. A big ironclad (hull 6000-8000) outlasts corvettes: keep your ships together
  against it, mind its gun range, and do not let it pick off lone or damaged ships.
  COASTAL BATTERIES are guns on the shore: they usually outrange ships (1000 m and more), your ships cannot be
  ordered to attack them, and they keep firing at every ship in range. Fight the enemy ships outside the range of enemy batteries
  - lure them out to you instead of chasing them under the guns - and only cross a battery's range at full speed.
  Your own batteries cover you: fall back under them when outmatched.
- Keep the plan if it still fits; repeat it and change only what needs changing.
- `intent` is shown to the player in game: one short sentence, in character.
- `next_plan_in_s`: 4-8 when armies are close or something is happening, up to 20-30 while manoeuvring far away.
"""


def find_claude() -> str:
    local = Path.home() / ".local" / "bin" / "claude.exe"  # where the native installer puts it
    found = shutil.which("claude")
    if found and found.lower().endswith((".cmd", ".bat")):
        # an npm install: a .cmd goes through cmd.exe, which mangles quotes in the JSON schema argument
        if local.exists():
            return str(local)
        raise SystemExit(f"found {found} (an npm install of Claude Code); the general needs the native build: "
                         "https://claude.com/claude-code (e.g. `irm https://claude.ai/install.ps1 | iex`)")
    if found:
        return found
    if local.exists():
        return str(local)
    raise SystemExit("claude CLI not found - install Claude Code and log in (run `claude` once)")


def system_prompt() -> str:
    parts = [INSTRUCTIONS]
    for name in ("rules.md", "strategy.md"):
        p = REPO / name
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


class ClaudeCodeGeneral:
    def __init__(self, model: str = "sonnet", effort: str = "low", binary: str | None = None,
                 conversation_plans: int = CONVERSATION_PLANS):
        self.bin = binary or find_claude()
        self.model, self.effort = model, effort
        self.conversation_plans = max(1, conversation_plans)
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue = queue.Queue()
        self.plans_in_conversation = 0
        self.total_plans = 0
        self.total_cost = 0.0

    def describe(self) -> str:
        return f"claude-code {self.model} effort={self.effort}"

    @property
    def fresh(self) -> bool:
        """True if the next plan starts a new conversation (the report must then carry the static info again)."""
        return self.proc is None or self.proc.poll() is not None or self.plans_in_conversation >= self.conversation_plans

    def _start(self) -> None:
        self.close()
        env = None
        effort = ["--effort", self.effort]
        if "haiku" in self.model:  # no effort levels; thinking off keeps it fast
            env, effort = dict(os.environ, MAX_THINKING_TOKENS="0"), []
        # the prompt goes through a file: rules.md + strategy.md grow, and command lines are limited on Windows
        prompt_file = REPO / "runtime" / "system_prompt.txt"
        prompt_file.parent.mkdir(exist_ok=True)
        prompt_file.write_text(system_prompt(), encoding="utf-8")
        args = [self.bin, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--system-prompt-file", str(prompt_file), "--model", self.model, *effort,
                "--json-schema", json.dumps(PLAN_SCHEMA), "--tools", "", "--no-session-persistence",
                "--strict-mcp-config", "--setting-sources", ""]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, cwd=str(Path.home()), env=env,
                                     text=True, encoding="utf-8", bufsize=1,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.lines = queue.Queue()
        threading.Thread(target=self._pump, args=(self.proc, self.lines), daemon=True).start()
        self.plans_in_conversation = 0
        log.info("claude process started (pid %d, %s)", self.proc.pid, self.describe())

    @staticmethod
    def _pump(proc: subprocess.Popen, q: queue.Queue) -> None:
        for line in proc.stdout:
            q.put(line)
        q.put(None)

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None

    def plan(self, report: str) -> tuple[dict, dict]:
        """-> (raw plan dict, info {seconds, cost, tokens})."""
        if self.fresh:
            self._start()
        msg = {"type": "user", "message": {"role": "user", "content": report}}
        t0 = time.monotonic()
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        while True:
            try:
                line = self.lines.get(timeout=max(1.0, TIMEOUT_S - (time.monotonic() - t0)))
            except queue.Empty:
                self.close()
                raise RuntimeError(f"no answer from claude within {TIMEOUT_S}s")
            if line is None:
                self.close()
                raise RuntimeError("claude process ended")
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "result":
                break
        self.plans_in_conversation += 1
        if ev.get("is_error"):
            text = str(ev.get("result"))
            resume = limit_reset(text)
            if resume is not None:
                self.close()  # nothing to gain from this process until the reset
                raise UsageLimit(f"claude: {text}", resume)
            raise RuntimeError(f"claude: {text}")
        data = ev.get("structured_output")
        if data is None:
            text = str(ev.get("result") or "")
            if "{" not in text:  # no plan at all: maybe the usage limit, reported as an ordinary answer
                resume = limit_reset(text)
                if resume is not None:
                    self.close()
                    raise UsageLimit(f"claude: {text}", resume)
                raise RuntimeError(f"claude answered without a plan: {text[:200]}")
            data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        u = ev.get("usage") or {}
        cost = ev.get("total_cost_usd") or 0.0
        self.total_plans += 1
        self.total_cost += cost
        info = {"seconds": round(time.monotonic() - t0, 1), "api_equiv_usd": cost,
                "tokens_in": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                + u.get("cache_creation_input_tokens", 0), "tokens_out": u.get("output_tokens", 0)}
        return data, info
