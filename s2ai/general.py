"""The LLM general: Claude through the Claude Code CLI (`claude -p`, headless, on the user's subscription).

One `claude` process per battle with stream-json in/out, so the conversation (and the prompt cache) carries over
from plan to plan; restarted after CONVERSATION_PLANS plans to keep the context small. No tools, no MCP, no
settings: the model only reads the report and answers with a plan in the PLAN_SCHEMA.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .plan import PLAN_SCHEMA

log = logging.getLogger("s2ai.general")

REPO = Path(__file__).resolve().parent.parent
CONVERSATION_PLANS = 25
TIMEOUT_S = 90

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
- Keep the plan if it still fits; repeat it and change only what needs changing.
- `intent` is shown to the player in game: one short sentence, in character.
- `next_plan_in_s`: 4-8 when armies are close or something is happening, up to 20-30 while manoeuvring far away.
"""


def find_claude() -> str:
    found = shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "claude.exe"
    if local.exists():
        return str(local)
    raise SystemExit("claude CLI not found - install Claude Code and log in (claude), or pass --claude PATH")


def system_prompt() -> str:
    parts = [INSTRUCTIONS]
    for name in ("rules.md", "strategy.md"):
        p = REPO / name
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


class ClaudeCodeGeneral:
    def __init__(self, model: str = "claude-sonnet-5-5", effort: str = "low", binary: str | None = None):
        self.bin = binary or find_claude()
        self.model, self.effort = model, effort
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue = queue.Queue()
        self.plans_in_conversation = 0
        self.total_plans = 0
        self.total_cost = 0.0

    def describe(self) -> str:
        return f"claude-code {self.model} effort={self.effort}"

    def _start(self) -> None:
        self.close()
        env = None
        effort = ["--effort", self.effort]
        if "haiku" in self.model:  # no effort levels; thinking off keeps it fast
            env, effort = dict(os.environ, MAX_THINKING_TOKENS="0"), []
        args = [self.bin, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--system-prompt", system_prompt(), "--model", self.model, *effort,
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
        if self.proc is None or self.proc.poll() is not None or self.plans_in_conversation >= CONVERSATION_PLANS:
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
            raise RuntimeError(f"claude: {ev.get('result')}")
        data = ev.get("structured_output")
        if data is None:
            text = ev.get("result", "")
            data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        u = ev.get("usage") or {}
        cost = ev.get("total_cost_usd") or 0.0
        self.total_plans += 1
        self.total_cost += cost
        info = {"seconds": round(time.monotonic() - t0, 1), "api_equiv_usd": cost,
                "tokens_in": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                + u.get("cache_creation_input_tokens", 0), "tokens_out": u.get("output_tokens", 0)}
        return data, info
