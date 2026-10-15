"""What the ENGINE can actually do, derived from live registries (never from imported data).
Importers/pipelines/viewers should consult this instead of assuming a move or ability works."""
from __future__ import annotations

from .abilities import get_active_abilities
from .data import MOVES
from .effects import HANDLERS, VALIDATED_STATUSES
from .rules import ITEMS, STAGE_STATS


def engine_capabilities() -> dict:
    return {
        "schema": 1,
        "stat_stages": {"stats": list(STAGE_STATS), "min": -6, "max": 6,
                        "formula": "pos: floor(stat*(2+n)/2); neg: floor(stat*2/(2-n)); crit ignores attacker drops and defender boosts"},
        "effect_kinds": sorted(HANDLERS),
        "validated_effect_statuses": sorted(VALIDATED_STATUSES),
        "moves": {n: {"category": m.category, "priority": m.priority, "effects": [e.kind for e in m.effects]}
                  for n, m in sorted(MOVES.items())},
        "moves_with_effects": sorted(n for n, m in MOVES.items() if m.effects),
        "abilities_with_effect": get_active_abilities(),
        "items_with_effect": sorted(ITEMS),
        "statuses": ["burn", "poison", "para"],
        "note": "A move/ability/item absent from these lists has NO engine effect, regardless of imported data.",
    }
