"""The plan the LLM general answers with, as a JSON schema (enforced by structured output) and a validator
that drops anything that cannot be carried out (unknown units, enemy units in our groups, ...)."""
from __future__ import annotations

import re

TASKS = ["attack", "move", "hold", "withdraw", "garrison", "climb", "attack_building", "occupy"]
BUILDING_TASKS = {"garrison", "climb", "attack_building"}

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "reasoning", "groups", "abilities", "next_plan_in_s"],
    "properties": {
        "intent": {"type": "string", "description": "One short sentence: what the army does now (shown in game)."},
        "reasoning": {"type": "string", "description": "Brief: what you see and why this plan (2-4 sentences)."},
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "units", "task", "target", "building", "position", "via", "facing", "run",
                             "engage_within_m", "keep_range_m", "shot"],
                "properties": {
                    "name": {"type": "string", "description": "e.g. 'left wing cavalry'"},
                    "units": {"type": "array", "items": {"type": "integer"}, "description": "ids of YOUR units"},
                    "task": {"type": "string", "enum": TASKS,
                             "description": "attack: charge `target`; move: go to `position` and stand there in a "
                                            "line; hold: stand in a line at `position` (or where they are if null); "
                                            "withdraw: leave the battlefield. Sieges: garrison: man `building` "
                                            "(a wall section, tower or gate: missile units shoot from it); climb: "
                                            "scale the wall section `building` (attackers); attack_building: "
                                            "attack `building` (e.g. a gate); occupy: take and hold the point "
                                            "`position` (e.g. the keep's courtyard)"},
                    "target": {"type": ["integer", "null"], "description": "attack: id of an ENEMY unit"},
                    "building": {"type": ["integer", "null"],
                                 "description": "garrison/climb/attack_building: id of a building from "
                                                "FORTIFICATIONS (the number after 'b')"},
                    "position": {"type": ["array", "null"], "items": {"type": "number"}, "minItems": 2,
                                 "maxItems": 2, "description": "move/hold: [x, y] where the group's centre goes"},
                    "via": {"type": ["array", "null"], "maxItems": 3,
                            "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                            "description": "optional route: up to 3 [x, y] waypoints the group goes through first "
                                           "(e.g. wide around an enemy flank), then it does its task; null = "
                                           "straight there"},
                    "facing": {"type": ["number", "null"],
                               "description": "move/hold: compass degrees the line faces; null = towards the enemy"},
                    "run": {"type": "boolean"},
                    "engage_within_m": {"type": "number",
                                        "description": "reflex: attack any enemy that comes this close, then return "
                                                       "to the task; 0 = off"},
                    "keep_range_m": {"type": "number",
                                     "description": "reflex for missile units: step back when an enemy melee unit "
                                                    "comes this close; 0 = off"},
                    "shot": {"type": ["string", "null"],
                             "description": "ships: the shot type to load, one of those listed for the ship "
                                            "(e.g. solid_shot, common_shell, segment_shell); null = keep"},
                },
            },
        },
        "abilities": {
            "type": "array",
            "description": "Special abilities to use NOW, once (usually empty). Only abilities listed for that unit.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["unit", "ability"],
                "properties": {"unit": {"type": "integer", "description": "id of YOUR unit"},
                               "ability": {"type": "string", "description": "e.g. 'rally'"}},
            },
        },
        "next_plan_in_s": {"type": "integer", "description": "When to look again (4-30 s); sooner if things change."},
    },
}


def validate(plan: dict, ours: set[int], enemies: set[int],
             buildings: set[int] | None = None,
             unit_abilities: dict[int, list[str]] | None = None) -> tuple[dict, list[str]]:
    """Keep what can be carried out; return (clean plan, problems). unit_abilities: {our unit id: abilities}."""
    problems: list[str] = []
    groups = []
    seen: set[int] = set()
    if not isinstance(plan, dict):
        plan = {}
    raw_groups = plan.get("groups")
    for g in raw_groups if isinstance(raw_groups, list) else []:
        if not isinstance(g, dict):
            continue
        units = []
        raw_units = g.get("units") if isinstance(g.get("units"), list) else []
        for uid in (_int(u) for u in raw_units):
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
        target = _int(g.get("target"))
        if task == "attack" and target not in enemies:
            if enemies:
                # usually a ship sunk or a unit gone while the model was thinking (battle 190434: the fleet held
                # still twice): the executor takes the best enemy nearby instead
                problems.append(f"group '{g.get('name')}': attack target {g.get('target')} is not a live enemy unit;"
                                f" attacking the nearest instead")
            else:
                problems.append(f"group '{g.get('name')}': attack target {g.get('target')} is not an enemy unit;"
                                f" holding")
                task = "hold"
        if task != "attack":
            target = None
        building = _int(g.get("building"))
        if task in BUILDING_TASKS and building not in (buildings or set()):
            problems.append(f"group '{g.get('name')}': {task} needs a building id from FORTIFICATIONS, "
                            f"got {g.get('building')}; holding")
            task = "hold"
        if task not in BUILDING_TASKS:
            building = None
        pos = g.get("position")
        pos = [_num(pos[0]), _num(pos[1])] if isinstance(pos, list) and len(pos) == 2 else None
        if pos is not None and None in pos:
            pos = None
        if task in ("move", "occupy") and pos is None:
            problems.append(f"group '{g.get('name')}': {task} without a position; holding")
            task = "hold"
        via = []
        for w in g.get("via") if isinstance(g.get("via"), list) else []:
            if isinstance(w, list) and len(w) == 2 and None not in (_num(w[0]), _num(w[1])):
                via.append([_num(w[0]), _num(w[1])])
        groups.append({
            "name": str(g.get("name") or "group")[:60], "units": units, "task": task, "target": target,
            "building": building,
            "position": pos, "via": via[:3] or None, "facing": _num(g.get("facing")), "run": bool(g.get("run")),
            "engage_within_m": max(0.0, _num(g.get("engage_within_m")) or 0.0),
            "keep_range_m": max(0.0, _num(g.get("keep_range_m")) or 0.0),
            "shot": g["shot"] if isinstance(g.get("shot"), str) and re.fullmatch(r"[a-z_]{3,40}", g["shot"]) else None,
        })
    abilities = []
    raw_ab = plan.get("abilities")
    for a in raw_ab if isinstance(raw_ab, list) else []:
        if not isinstance(a, dict):
            continue
        uid, name = _int(a.get("unit")), str(a.get("ability") or "")
        if uid not in ours:
            problems.append(f"ability {name}: {a.get('unit')} is not one of your units")
        elif name not in (unit_abilities or {}).get(uid, []):
            problems.append(f"ability {name}: unit {uid} does not have it")
        elif not any(x["unit"] == uid for x in abilities):  # one per unit per plan
            abilities.append({"unit": uid, "ability": name})
    clean = {
        "intent": str(plan.get("intent", ""))[:160],
        "reasoning": str(plan.get("reasoning", ""))[:2000],
        "groups": groups,
        "abilities": abilities,
        "next_plan_in_s": int(min(30, max(4, _num(plan.get("next_plan_in_s")) or 8))),
    }
    return clean, problems


# The text fallback in general.py bypasses the schema, so anything can arrive here: coerce, never raise.
def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) < 1e7 else None


def _int(v) -> int | None:
    f = _num(v)
    return int(f) if f is not None and f == int(f) else None
