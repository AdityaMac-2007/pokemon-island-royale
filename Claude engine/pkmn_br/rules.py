"""Generation-specific battle mechanics (canonical baseline = Gen 6+ type chart,
Gen 5+ damage formula). Contains NO battle-royale logic (zone, biomes, flight)."""
from __future__ import annotations
from dataclasses import dataclass

from .abilities import (
    apply_atk_abilities,
    apply_hit_chance_abilities,
    apply_def_abilities,
    apply_incoming_damage_abilities,
    check_ability_immunity,
    has_absorb_hook,
    ignores_burn_atk_drop,
)

TYPES = ["normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison", "ground",
         "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy"]

_RAW = {
 "normal":   {"rock": .5, "steel": .5, "ghost": 0},
 "fire":     {"fire": .5, "water": .5, "grass": 2, "ice": 2, "bug": 2, "rock": .5, "dragon": .5, "steel": 2},
 "water":    {"fire": 2, "water": .5, "grass": .5, "ground": 2, "rock": 2, "dragon": .5},
 "electric": {"water": 2, "electric": .5, "grass": .5, "ground": 0, "flying": 2, "dragon": .5},
 "grass":    {"fire": .5, "water": 2, "grass": .5, "poison": .5, "ground": 2, "flying": .5, "bug": .5, "rock": 2, "dragon": .5, "steel": .5},
 "ice":      {"fire": .5, "water": .5, "grass": 2, "ice": .5, "ground": 2, "flying": 2, "dragon": 2, "steel": .5},
 "fighting": {"normal": 2, "ice": 2, "poison": .5, "flying": .5, "psychic": .5, "bug": .5, "rock": 2, "ghost": 0, "dark": 2, "steel": 2, "fairy": .5},
 "poison":   {"grass": 2, "poison": .5, "ground": .5, "rock": .5, "ghost": .5, "steel": 0, "fairy": 2},
 "ground":   {"fire": 2, "electric": 2, "grass": .5, "poison": 2, "flying": 0, "bug": .5, "rock": 2, "steel": 2},
 "flying":   {"electric": .5, "grass": 2, "fighting": 2, "bug": 2, "rock": .5, "steel": .5},
 "psychic":  {"fighting": 2, "poison": 2, "psychic": .5, "dark": 0, "steel": .5},
 "bug":      {"fire": .5, "grass": 2, "fighting": .5, "poison": .5, "flying": .5, "psychic": 2, "ghost": .5, "dark": 2, "steel": .5, "fairy": .5},
 "rock":     {"fire": 2, "ice": 2, "fighting": .5, "ground": .5, "flying": 2, "bug": 2, "steel": .5},
 "ghost":    {"normal": 0, "psychic": 2, "ghost": 2, "dark": .5},
 "dragon":   {"dragon": 2, "steel": .5, "fairy": 0},
 "dark":     {"fighting": .5, "psychic": 2, "ghost": 2, "dark": .5, "fairy": .5},
 "steel":    {"fire": .5, "water": .5, "electric": .5, "ice": 2, "rock": 2, "steel": .5, "fairy": 2},
 "fairy":    {"fire": .5, "fighting": 2, "poison": .5, "dragon": 2, "dark": 2, "steel": .5},
}
CHART = {a: {d: _RAW[a].get(d, 1.0) for d in TYPES} for a in TYPES}


def type_mult(move_type: str, def_types) -> float:
    m = 1.0
    row = CHART[move_type]
    for d in def_types:
        m *= row[d]
    return m


STATS = ("hp", "atk", "def", "spa", "spd", "spe")
STAGE_STATS = ("atk", "def", "spa", "spd", "spe")   # HP has no stages
STAGE_MIN, STAGE_MAX = -6, 6


def apply_stage(stat: int, stage: int) -> int:
    """Gen 3+ stage multiplier with integer floor: +n -> floor(stat*(2+n)/2); -n -> floor(stat*2/(2+n)).
    Applied before burn/item/ability modifiers. Stage is clamped to -6..+6."""
    if stage == 0:
        return stat
    s = max(STAGE_MIN, min(STAGE_MAX, stage))
    return stat * (2 + s) // 2 if s >= 0 else stat * 2 // (2 - s)


def calc_stats(base: dict, evs: dict, up: str | None, down: str | None, ivs: int = 31, level: int = 100) -> dict:
    out = {"hp": (2 * base["hp"] + ivs + evs.get("hp", 0) // 4) * level // 100 + level + 10}
    for s in STATS[1:]:
        v = (2 * base[s] + ivs + evs.get(s, 0) // 4) * level // 100 + 5
        if s == up:
            v = v * 110 // 100
        elif s == down:
            v = v * 90 // 100
        out[s] = v
    return out


# ---- abilities & items: data-driven hook tables (small subset in M1; registry is the extension point)
PINCH_ABILITY = {"blaze": "fire", "overgrow": "grass", "torrent": "water", "swarm": "bug"}
GROUND_IMMUNE_ABILITIES = {"levitate"}
ITEMS = {
    "life_orb": {"dmg_mult": 1.3, "recoil": 0.10},
    "leftovers": {"heal": 1 / 16},
    "choice_band": {"phys_mult": 1.5},
    "choice_specs": {"spec_mult": 1.5},
}
STATUS_IMMUNE = {"burn": {"fire"}, "poison": {"poison", "steel"}, "para": {"electric"}}


@dataclass(frozen=True)
class BattleRules:
    crit_chance: float = 1 / 24
    crit_mult: float = 1.5
    roll_lo: float = 0.85
    roll_hi: float = 1.0
    stab: float = 1.5

    @classmethod
    def from_config(cls, cfg) -> "BattleRules":
        if cfg.ruleset == "chaos" or cfg.chaos.enabled:
            c = cfg.chaos
            return cls(crit_chance=c.crit_chance, roll_lo=c.roll_lo, roll_hi=c.roll_hi, stab=c.stab)
        return cls()


def weather_mult(weather: str, mtype: str) -> float:
    if weather == "rain":
        return 1.5 if mtype == "water" else 0.5 if mtype == "fire" else 1.0
    if weather == "sun":
        return 1.5 if mtype == "fire" else 0.5 if mtype == "water" else 1.0
    return 1.0


def _core(att, dfn, mv, weather, boost, rules: BattleRules, crit: float, roll: float) -> float:
    if mv.category == "status":
        return 0.0
    te = type_mult(mv.type, dfn.types)
    if te == 0.0:
        return 0.0
    if check_ability_immunity(dfn, mv):
        return 0.0
    phys = mv.category == "physical"
    ak, dk = ("atk", "def") if phys else ("spa", "spd")
    sa, sd = att.stages[ak], dfn.stages[dk]
    if crit:                       # Gen 3+: crits ignore the attacker's drops and the defender's boosts
        sa, sd = max(0, sa), min(0, sd)
    A = att.stats[ak]
    D = dfn.stats[dk]
    if sa:
        A = apply_stage(A, sa)
    if sd:
        D = apply_stage(D, sd)
    if phys and att.status == "burn" and not ignores_burn_atk_drop(att):
        A = A // 2
    it = ITEMS.get(att.item or "", {})
    if phys and "phys_mult" in it:
        A = int(A * it["phys_mult"])
    if (not phys) and "spec_mult" in it:
        A = int(A * it["spec_mult"])
    A = apply_atk_abilities(att, dfn, mv, weather, A)
    D, A = apply_def_abilities(att, dfn, mv, weather, D, A)
    base = (42 * mv.power * A // D) // 50 + 2           # level 100: floor(2*100/5)+2 = 42
    m = weather_mult(weather, mv.type) * boost
    m *= (rules.crit_mult if crit else 1.0)
    m *= roll
    if mv.type in att.types:
        m *= rules.stab
    m *= te
    m = apply_incoming_damage_abilities(att, dfn, mv, weather, m, te)
    m *= it.get("dmg_mult", 1.0)
    return base * m


def hit_chance(mv, cover: float, weather: str = "", att=None, dfn=None) -> float:
    """Calculate hit probability, including cover and defender ability hooks.

    Optional context preserves compatibility with existing two-argument callers.
    """
    acc = mv.accuracy / 100.0
    if mv.reach == "ranged":
        acc *= (1.0 - 0.5 * cover)                       # cover from the defender's biome
    if dfn is None:
        return acc
    acc = apply_hit_chance_abilities(att, dfn, mv, weather, acc)
    return max(0.0, min(1.0, acc))


def calc_damage(att, dfn, mv, rolls, weather, boost, cover, rules):
    """rolls = 4 uniform draws (accuracy, crit, damage roll, status). Always 4 consumed upstream."""
    # Immunity is decided before the accuracy roll: an immune target never "dodges",
    # and the accuracy draw is not spent on an impossible hit. Mirrors the 0-damage
    # branches inside _core (type immunity, Levitate vs Ground); Combat v1 stage/item
    # mechanics are untouched. The raw <= 0 guard below is kept for every other
    # non-positive outcome.
    te = type_mult(mv.type, dfn.types)
    is_ability_immune = check_ability_immunity(dfn, mv)
    # Passive immunities (e.g. Levitate) apply before accuracy; reactive absorption/boost
    # abilities (Water/Volt Absorb, Flash Fire) require the move to pass accuracy first.
    if te == 0.0 or (is_ability_immune and not has_absorb_hook(dfn)):
        return 0, {"immune": True}
    if rolls[0] >= hit_chance(mv, cover, weather, att, dfn):
        return 0, {"miss": True}
    if is_ability_immune:
        return 0, {"immune": True, "absorb": True}
    crit = 1.0 if rolls[1] < rules.crit_chance else 0.0
    roll = rules.roll_lo + (rules.roll_hi - rules.roll_lo) * rolls[2]
    raw = _core(att, dfn, mv, weather, boost, rules, crit, roll)
    if raw <= 0:
        return 0, {"immune": True}
    info = {"crit": bool(crit), "eff": te}
    return max(1, int(raw)), info


def expected_damage(att, dfn, mv, weather, boost, cover, rules) -> float:
    """Deterministic expectation used by the AI (no RNG)."""
    avg_roll = (rules.roll_lo + rules.roll_hi) / 2
    raw = _core(att, dfn, mv, weather, boost, rules, 0.0, avg_roll)
    raw *= (1 + (rules.crit_mult - 1) * rules.crit_chance)
    return raw * hit_chance(mv, cover, weather, att, dfn)
