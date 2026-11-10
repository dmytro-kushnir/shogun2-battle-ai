"""What the battle map may show from a game: unit pictures, ability icons and display names.

The map itself (viewer.html) knows nothing about a particular game: it asks for /icon/unit/<type>.png (square,
for the map), /icon/card/<type>.png (the unit card, for the tooltip), /icon/ability/<name>.png and /names, and
falls back to its own symbols when an answer is missing. A game provides a GameAssets; NoAssets is the fallback.
Pictures are taken from the user's own copy of the game at run time and are never stored in the repository.
"""
from __future__ import annotations

from typing import Protocol


class GameAssets(Protocol):
    def unit_icon(self, unit_type: str, card: bool = False) -> bytes | None:
        """PNG: a small square picture of the unit (card=False) or its tall unit card (card=True)."""

    def ability_icon(self, ability: str) -> bytes | None:
        """PNG icon of a special ability."""

    def names(self) -> dict[str, dict[str, str]]:
        """{"units": {unit type: display name}, "abilities": {ability: display name}}."""


class NoAssets:
    def unit_icon(self, unit_type: str, card: bool = False) -> bytes | None:
        return None

    def ability_icon(self, ability: str) -> bytes | None:
        return None

    def names(self) -> dict[str, dict[str, str]]:
        return {"units": {}, "abilities": {}}
