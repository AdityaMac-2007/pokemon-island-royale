"""Data-driven ability mechanics and trigger lifecycle registry.

Derives live ability capabilities for the engine, rules calculation, and AI estimation.
Abilities must register explicit handlers for active lifecycle hooks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class AbilityTrigger:
    MODIFY_ATK = "modify_atk"          # (att, dfn, mv, weather, A) -> int
    MODIFY_DEF = "modify_def"          # (att, dfn, mv, weather, D, A) -> tuple[int, int]
    MODIFY_INCOMING_DAMAGE = "modify_incoming_damage"  # (att, dfn, mv, weather, m, te) -> float
    CHECK_IMMUNITY = "check_immunity"  # (dfn, mv) -> bool (True if immune)
    ON_ABSORB_HIT = "on_absorb_hit"    # (eng, t, att, dfn, mv) -> None (mutates state/heals on actual hit)
    ON_HIT_DEFENSE = "on_hit_defense"  # (eng, t, att, dfn, mv, raw_dmg, info) -> (modified_dmg, bool)


# Canonical set of triggers verified to have real, active call sites in the engine.
# Maintained alongside the actual engine and rules dispatchers.
CONNECTED_TRIGGERS: frozenset[str] = frozenset({
    AbilityTrigger.MODIFY_ATK,
    AbilityTrigger.MODIFY_DEF,
    AbilityTrigger.MODIFY_INCOMING_DAMAGE,
    AbilityTrigger.CHECK_IMMUNITY,
    AbilityTrigger.ON_ABSORB_HIT,
    AbilityTrigger.ON_HIT_DEFENSE,
})


@dataclass(frozen=True)
class AbilitySpec:
    name: str
    description: str
    hooks: dict[str, Callable[..., Any]]


ABILITY_REGISTRY: dict[str, AbilitySpec] = {}


def register_ability(spec: AbilitySpec, *, replace: bool = False) -> AbilitySpec:
    """Validate and register an ability specification into the runtime table.

    Rejects duplicate registrations by default unless replace=True is explicitly set.
    Rejects abilities that declare empty handlers, non-callable hooks,
    or triggers not present in CONNECTED_TRIGGERS.
    """
    if not isinstance(spec.name, str) or not spec.name.strip():
        raise ValueError(f"Ability specification must have a non-empty name, got {spec.name!r}")
    if not spec.hooks:
        raise ValueError(f"Ability {spec.name!r} must define at least one trigger hook")
    if spec.name in ABILITY_REGISTRY and not replace:
        raise ValueError(
            f"Ability {spec.name!r} is already registered. Silent replacement is forbidden; "
            f"pass replace=True if replacement is intentional."
        )
    for trigger, hook in spec.hooks.items():
        if trigger not in CONNECTED_TRIGGERS:
            raise ValueError(
                f"Ability {spec.name!r} declared unconnected or unsupported trigger {trigger!r}. "
                f"Connected triggers: {sorted(CONNECTED_TRIGGERS)}"
            )
        if not callable(hook):
            raise ValueError(f"Hook for trigger {trigger!r} on ability {spec.name!r} must be callable")
    ABILITY_REGISTRY[spec.name] = spec
    return spec


# ---------------------------------------------------------------------------
# Baseline Functional Abilities (Blaze, Overgrow, Torrent, Swarm, Levitate, Sturdy)
# ---------------------------------------------------------------------------

def _pinch_atk_hook(mtype: str):
    def hook(att, dfn, mv, weather, A: int) -> int:
        if mv.type == mtype and att.hp * 3 <= att.max_hp:
            return int(A * 1.5)
        return A
    return hook


for _name, _mtype in (("blaze", "fire"), ("overgrow", "grass"), ("torrent", "water"), ("swarm", "bug")):
    register_ability(AbilitySpec(
        name=_name,
        description=f"Boosts {_mtype.capitalize()}-type attack by 1.5x at 1/3 HP or less.",
        hooks={AbilityTrigger.MODIFY_ATK: _pinch_atk_hook(_mtype)}
    ))


def _levitate_immunity(dfn, mv) -> bool:
    return mv.type == "ground"


register_ability(AbilitySpec(
    name="levitate",
    description="Grants full immunity to Ground-type attacks.",
    hooks={AbilityTrigger.CHECK_IMMUNITY: _levitate_immunity}
))


def _sturdy_defense_hook(eng, t: int, att, dfn, mv, raw_dmg: int, info: dict) -> tuple[int, bool]:
    if raw_dmg > 0 and dfn.hp == dfn.max_hp and raw_dmg >= dfn.hp and not getattr(dfn, "sturdy_used", False):
        dfn.sturdy_used = True
        info["sturdy"] = True
        return (dfn.hp - 1, True)
    return (raw_dmg, False)


register_ability(AbilitySpec(
    name="sturdy",
    description="Survives lethal attacks from full HP with 1 HP remaining.",
    hooks={AbilityTrigger.ON_HIT_DEFENSE: _sturdy_defense_hook}
))


# ---------------------------------------------------------------------------
# Stage 2 Functional Abilities:
# Water Absorb, Volt Absorb, Flash Fire, Thick Fat, Filter, Solid Rock
# ---------------------------------------------------------------------------

def _water_absorb_immunity(dfn, mv) -> bool:
    return mv.category != "status" and mv.type == "water"


def _water_absorb_on_hit(eng, t: int, att, dfn, mv) -> None:
    eng.heal_hp(t, dfn, max(1, dfn.max_hp // 4), "ability", "water_absorb")


register_ability(AbilitySpec(
    name="water_absorb",
    description="Immunity to damaging Water-type moves; heals 1/4 max HP when hit by a Water move.",
    hooks={
        AbilityTrigger.CHECK_IMMUNITY: _water_absorb_immunity,
        AbilityTrigger.ON_ABSORB_HIT: _water_absorb_on_hit,
    }
))


def _volt_absorb_immunity(dfn, mv) -> bool:
    return mv.type == "electric"


def _volt_absorb_on_hit(eng, t: int, att, dfn, mv) -> None:
    eng.heal_hp(t, dfn, max(1, dfn.max_hp // 4), "ability", "volt_absorb")


register_ability(AbilitySpec(
    name="volt_absorb",
    description="Immunity to Electric-type moves; heals 1/4 max HP when hit by an Electric move.",
    hooks={
        AbilityTrigger.CHECK_IMMUNITY: _volt_absorb_immunity,
        AbilityTrigger.ON_ABSORB_HIT: _volt_absorb_on_hit,
    }
))


def _flash_fire_immunity(dfn, mv) -> bool:
    # Pure predicate: never mutates state during AI expected_damage queries
    return mv.type == "fire"


def _flash_fire_on_hit(eng, t: int, att, dfn, mv) -> None:
    if not getattr(dfn, "flash_fire_active", False):
        dfn.flash_fire_active = True
        eng.emit(t, "ability_trigger", id=dfn.id, ability="flash_fire", effect="flash_fire_boost")


def _flash_fire_atk_hook(att, dfn, mv, weather, A: int) -> int:
    if mv.type == "fire" and getattr(att, "flash_fire_active", False):
        return int(A * 1.5)
    return A


register_ability(AbilitySpec(
    name="flash_fire",
    description="Immunity to Fire-type moves; activates 1.5x Fire attack boost when hit by a Fire move.",
    hooks={
        AbilityTrigger.CHECK_IMMUNITY: _flash_fire_immunity,
        AbilityTrigger.ON_ABSORB_HIT: _flash_fire_on_hit,
        AbilityTrigger.MODIFY_ATK: _flash_fire_atk_hook,
    }
))


def _thick_fat_def_hook(att, dfn, mv, weather, D: int, A: int) -> tuple[int, int]:
    if mv.type in ("fire", "ice"):
        return D, A // 2
    return D, A


register_ability(AbilitySpec(
    name="thick_fat",
    description="Halves incoming Fire-type and Ice-type attack stat.",
    hooks={AbilityTrigger.MODIFY_DEF: _thick_fat_def_hook}
))


def _super_effective_reduction_hook(att, dfn, mv, weather, m: float, te: float) -> float:
    if te > 1.0:
        return m * 0.75
    return m


for _name in ("filter", "solid_rock"):
    register_ability(AbilitySpec(
        name=_name,
        description="Reduces incoming super-effective damage by 25%.",
        hooks={AbilityTrigger.MODIFY_INCOMING_DAMAGE: _super_effective_reduction_hook}
    ))


# ---------------------------------------------------------------------------
# Dispatch Helpers
# ---------------------------------------------------------------------------

def get_ability_hook(ability_name: str | None, hook_name: str) -> Callable[..., Any] | None:
    if not ability_name:
        return None
    spec = ABILITY_REGISTRY.get(ability_name)
    if spec is None:
        return None
    return spec.hooks.get(hook_name)


def apply_atk_abilities(att, dfn, mv, weather, A: int) -> int:
    hook = get_ability_hook(getattr(att, "ability", None), AbilityTrigger.MODIFY_ATK)
    if hook is not None:
        return hook(att, dfn, mv, weather, A)
    return A


def apply_def_abilities(att, dfn, mv, weather, D: int, A: int) -> tuple[int, int]:
    hook = get_ability_hook(getattr(dfn, "ability", None), AbilityTrigger.MODIFY_DEF)
    if hook is not None:
        return hook(att, dfn, mv, weather, D, A)
    return D, A


def apply_incoming_damage_abilities(att, dfn, mv, weather, m: float, te: float) -> float:
    hook_dfn = get_ability_hook(getattr(dfn, "ability", None), AbilityTrigger.MODIFY_INCOMING_DAMAGE)
    if hook_dfn is not None:
        m = hook_dfn(att, dfn, mv, weather, m, te)
    return m


def check_ability_immunity(dfn, mv) -> bool:
    hook = get_ability_hook(getattr(dfn, "ability", None), AbilityTrigger.CHECK_IMMUNITY)
    if hook is not None:
        return bool(hook(dfn, mv))
    return False


def has_absorb_hook(dfn) -> bool:
    return get_ability_hook(getattr(dfn, "ability", None), AbilityTrigger.ON_ABSORB_HIT) is not None


def get_active_abilities() -> list[str]:
    """Return all ability identifiers currently modeled with active handlers."""
    return sorted(ABILITY_REGISTRY.keys())
