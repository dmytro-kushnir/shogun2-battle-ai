"""The plan the LLM general answers with, as a JSON schema (enforced by structured output) and a validator
that drops anything that cannot be carried out (unknown units, enemy units in our groups, ...)."""
from __future__ import annotations

TASKS = ["attack", "move", "hold", "withdraw"]

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "reasoning", "groups", "next_plan_in_s"],
    "properties": {
        "intent": {"type": "string", "description": "One short sentence: what the army does now (shown in game)."},
        "reasoning": {"type": "string", "description": "Brief: what you see and why this plan (2-4 sentences)."},
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "units", "task", "target", "position", "facing", "run",
                             "engage_within_m", "keep_range_m"],
                "properties": {
                    "name": {"type": "string", "description": "e.g. 'left wing cavalry'"},
                    "units": {"type": "array", "items": {"type": "integer"}, "description": "ids of YOUR units"},
                    "task": {"type": "string", "enum": TASKS,
                             "description": "attack: charge `target`; move: go to `position` and stand there in a "
                                            "line; hold: stand in a line at `position` (or where they are if null); "
                                            "withdraw: leave the battlefield"},
                    "target": {"type": ["integer", "null"], "description": "attack: id of an ENEMY unit"},
                    "position": {"type": ["array", "null"], "items": {"type": "number"}, "minItems": 2,
                                 "maxItems": 2, "description": "move/hold: [x, y] where the group's centre goes"},
                    "facing": {"type": ["number", "null"],
                               "description": "move/hold: compass degrees the line faces; null = towards the enemy"},
                    "run": {"type": "boolean"},
                    "engage_within_m": {"type": "number",
                                        "description": "reflex: attack any enemy that comes this close, then return "
                                                       "to the task; 0 = off"},
                    "keep_range_m": {"type": "number",
                                     "description": "reflex for missile units: step back when an enemy melee unit "
                                                    "comes this close; 0 = off"},
                },
            },
        },
        "next_plan_in_s": {"type": "integer", "description": "When to look again (4-30 s); sooner if things change."},
    },
}


def validate(plan: dict, ours: set[int], enemies: set[int]) -> tuple[dict, list[str]]:
    """Keep what can be carried out; return (clean plan, problems)."""
    problems: list[str] = []
    groups = []
    seen: set[int] = set()
    for g in plan.get("groups", []):
        units = []
        for uid in g.get("units", []):
            if uid not in ours:
                problems.append(f"group '{g.get('name')}': {uid} is not one of your units")
            elif uid in seen:
                problems.append(f"group '{g.get('name')}': unit {uid} is already in another group")
            else:
                units.append(uid)
                seen.add(uid)
        if not units:
            continue
        task = g.get("task") if g.get("task") in TASKS else "hold"
        target = g.get("target")
        if task == "attack" and target not in enemies:
            problems.append(f"group '{g.get('name')}': attack target {target} is not an enemy unit; holding")
            task, target = "hold", None
        pos = g.get("position")
        if task == "move" and not (isinstance(pos, list) and len(pos) == 2):
            problems.append(f"group '{g.get('name')}': move without a position; holding")
            task = "hold"
        groups.append({
            "name": str(g.get("name") or "group"), "units": units, "task": task, "target": target,
            "position": pos if isinstance(pos, list) and len(pos) == 2 else None,
            "facing": g.get("facing"), "run": bool(g.get("run")),
            "engage_within_m": max(0.0, float(g.get("engage_within_m") or 0)),
            "keep_range_m": max(0.0, float(g.get("keep_range_m") or 0)),
        })
    clean = {
        "intent": str(plan.get("intent", ""))[:160],
        "reasoning": str(plan.get("reasoning", "")),
        "groups": groups,
        "next_plan_in_s": min(30, max(4, int(plan.get("next_plan_in_s") or 8))),
    }
    return clean, problems
