"""
POKÉMON ISLAND BATTLE ROYALE - GENETIC BUILD OPTIMIZER
Self-contained multi-combatant optimizer and micro-simulator.
File: gemini/optimizer.py
"""
from __future__ import annotations
import copy
import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set


# ============================================================================
# 1. CANONICAL CONSTANTS & TYPE CHART
# ============================================================================

NATURES = {
    "Adamant": {"plus": "atk", "minus": "spa"},
    "Bashful": {"plus": None,  "minus": None},
    "Bold":    {"plus": "def", "minus": "atk"},
    "Brave":   {"plus": "atk", "minus": "spe"},
    "Calm":    {"plus": "spd", "minus": "atk"},
    "Careful": {"plus": "spd", "minus": "spa"},
    "Docile":  {"plus": None,  "minus": None},
    "Gentle":  {"plus": "spd", "minus": "def"},
    "Hardy":   {"plus": None,  "minus": None},
    "Hasty":   {"plus": "spe", "minus": "def"},
    "Impish":  {"plus": "def", "minus": "spa"},
    "Jolly":   {"plus": "spe", "minus": "spa"},
    "Lax":     {"plus": "def", "minus": "spd"},
    "Lonely":  {"plus": "atk", "minus": "def"},
    "Mild":    {"plus": "spa", "minus": "def"},
    "Modest":  {"plus": "spa", "minus": "atk"},
    "Naive":   {"plus": "spe", "minus": "spd"},
    "Naughty": {"plus": "atk", "minus": "spd"},
    "Quiet":   {"plus": "spa", "minus": "spe"},
    "Quirky":  {"plus": None,  "minus": None},
    "Rash":    {"plus": "spa", "minus": "spd"},
    "Relaxed": {"plus": "def", "minus": "spe"},
    "Sassy":   {"plus": "spd", "minus": "spe"},
    "Serious": {"plus": None,  "minus": None},
    "Timid":   {"plus": "spe", "minus": "atk"},
}

TYPE_CHART: Dict[str, Dict[str, float]] = {
    "Normal":   {"Rock": 0.5, "Ghost": 0.0, "Steel": 0.5},
    "Fire":     {"Fire": 0.5, "Water": 0.5, "Grass": 2.0, "Ice": 2.0, "Bug": 2.0, "Rock": 0.5, "Dragon": 0.5, "Steel": 2.0},
    "Water":    {"Fire": 2.0, "Water": 0.5, "Grass": 0.5, "Ground": 2.0, "Rock": 2.0, "Dragon": 0.5},
    "Electric": {"Water": 2.0, "Electric": 0.5, "Grass": 0.5, "Ground": 0.0, "Flying": 2.0, "Dragon": 0.5},
    "Grass":    {"Fire": 0.5, "Water": 2.0, "Grass": 0.5, "Poison": 0.5, "Ground": 2.0, "Flying": 0.5, "Bug": 0.5, "Rock": 2.0, "Dragon": 0.5, "Steel": 0.5},
    "Ice":      {"Fire": 0.5, "Water": 0.5, "Grass": 2.0, "Ice": 0.5, "Ground": 2.0, "Flying": 2.0, "Dragon": 2.0, "Steel": 0.5},
    "Fighting": {"Normal": 2.0, "Ice": 2.0, "Poison": 0.5, "Flying": 0.5, "Psychic": 0.5, "Bug": 0.5, "Rock": 2.0, "Ghost": 0.0, "Dark": 2.0, "Steel": 2.0, "Fairy": 0.5},
    "Poison":   {"Grass": 2.0, "Poison": 0.5, "Ground": 0.5, "Rock": 0.5, "Ghost": 0.5, "Steel": 0.0, "Fairy": 2.0},
    "Ground":   {"Fire": 2.0, "Electric": 2.0, "Grass": 0.5, "Poison": 2.0, "Flying": 0.0, "Bug": 0.5, "Rock": 2.0, "Steel": 2.0},
    "Flying":   {"Electric": 0.5, "Grass": 2.0, "Fighting": 2.0, "Bug": 2.0, "Rock": 0.5, "Steel": 0.5},
    "Psychic":  {"Fighting": 2.0, "Poison": 2.0, "Psychic": 0.5, "Dark": 0.0, "Steel": 0.5},
    "Bug":      {"Fire": 0.5, "Grass": 2.0, "Fighting": 0.5, "Poison": 0.5, "Flying": 0.5, "Psychic": 2.0, "Ghost": 0.5, "Dark": 2.0, "Steel": 0.5, "Fairy": 0.5},
    "Rock":     {"Fire": 2.0, "Ice": 2.0, "Fighting": 0.5, "Ground": 0.5, "Flying": 2.0, "Bug": 2.0, "Steel": 0.5},
    "Ghost":    {"Normal": 0.0, "Psychic": 2.0, "Ghost": 2.0, "Dark": 0.5},
    "Dragon":   {"Dragon": 2.0, "Steel": 0.5, "Fairy": 0.0},
    "Dark":     {"Fighting": 0.5, "Psychic": 2.0, "Ghost": 2.0, "Dark": 0.5, "Fairy": 0.5},
    "Steel":    {"Fire": 0.5, "Water": 0.5, "Electric": 0.5, "Ice": 2.0, "Rock": 2.0, "Steel": 0.5, "Fairy": 2.0},
    "Fairy":    {"Fire": 0.5, "Fighting": 2.0, "Poison": 0.5, "Dragon": 2.0, "Dark": 2.0, "Steel": 0.5},
}


def get_type_effectiveness(atk_type: str, def_types: List[str]) -> float:
    mult = 1.0
    for dt in def_types:
        mult *= TYPE_CHART.get(atk_type, {}).get(dt, 1.0)
    return mult


# ============================================================================
# 2. STAT CALCULATION ENGINE
# ============================================================================

def calc_hp(base: int, iv: int = 31, ev: int = 0, level: int = 100) -> int:
    if base == 1:
        return 1
    return math.floor(((2 * base + iv + math.floor(ev / 4)) * level) / 100) + level + 10


def calc_stat(base: int, stat_name: str, nature: str, iv: int = 31, ev: int = 0, level: int = 100) -> int:
    raw = math.floor(((2 * base + iv + math.floor(ev / 4)) * level) / 100) + 5
    nat_cfg = NATURES.get(nature.capitalize(), {"plus": None, "minus": None})
    if nat_cfg["plus"] == stat_name:
        return math.floor(raw * 1.10)
    elif nat_cfg["minus"] == stat_name:
        return math.floor(raw * 0.90)
    return raw


# ============================================================================
# 3. REFERENCE DATA & BASELINES
# ============================================================================

REFERENCE_MOVES = {
    "protect":        {"name": "Protect", "type": "Normal", "cat": "status", "bp": 0, "acc": 100, "priority": 4, "target": "self"},
    "recover":        {"name": "Recover", "type": "Normal", "cat": "status", "bp": 0, "acc": 100, "priority": 0, "target": "self", "heal": 0.5},
    "earthquake":     {"name": "Earthquake", "type": "Ground", "cat": "physical", "bp": 100, "acc": 100, "priority": 0, "target": "adjacentAll"},
    "surf":           {"name": "Surf", "type": "Water", "cat": "special", "bp": 90, "acc": 100, "priority": 0, "target": "adjacentAll"},
    "bodypress":      {"name": "Body Press", "type": "Fighting", "cat": "physical", "bp": 80, "acc": 100, "priority": 0, "target": "normal", "def_scaling": True},
    "irondefense":    {"name": "Iron Defense", "type": "Steel", "cat": "status", "bp": 0, "acc": 100, "priority": 0, "target": "self", "boosts": {"def": 2}},
    "dazzlinggleam":  {"name": "Dazzling Gleam", "type": "Fairy", "cat": "special", "bp": 80, "acc": 100, "priority": 0, "target": "adjacentFoes"},
    "closecombat":    {"name": "Close Combat", "type": "Fighting", "cat": "physical", "bp": 120, "acc": 100, "priority": 0, "target": "normal"},
    "flashcannon":    {"name": "Flash Cannon", "type": "Steel", "cat": "special", "bp": 80, "acc": 100, "priority": 0, "target": "normal"},
    "dracometeor":    {"name": "Draco Meteor", "type": "Dragon", "cat": "special", "bp": 130, "acc": 90, "priority": 0, "target": "normal"},
    "snarl":          {"name": "Snarl", "type": "Dark", "cat": "special", "bp": 55, "acc": 95, "priority": 0, "target": "adjacentFoes"},
    "icebeam":        {"name": "Ice Beam", "type": "Ice", "cat": "special", "bp": 90, "acc": 100, "priority": 0, "target": "normal"},
    "thunderbolt":    {"name": "Thunderbolt", "type": "Electric", "cat": "special", "bp": 90, "acc": 100, "priority": 0, "target": "normal"},
    "scald":          {"name": "Scald", "type": "Water", "cat": "special", "bp": 80, "acc": 100, "priority": 0, "target": "normal"},
    "toxic":          {"name": "Toxic", "type": "Poison", "cat": "status", "bp": 0, "acc": 90, "priority": 0, "target": "normal"},
    "leechseed":      {"name": "Leech Seed", "type": "Grass", "cat": "status", "bp": 0, "acc": 90, "priority": 0, "target": "normal"},
}

REFERENCE_SPECIES = {
    "garchomp": {
        "dex": 445, "name": "Garchomp", "types": ["Dragon", "Ground"],
        "stats": {"hp": 108, "atk": 130, "def": 95, "spa": 80, "spd": 85, "spe": 102},
        "abilities": ["Rough Skin", "Sand Veil"],
        "pool": ["earthquake", "protect", "closecombat", "irondefense", "dracometeor", "snarl", "surf"]
    },
    "corviknight": {
        "dex": 823, "name": "Corviknight", "types": ["Flying", "Steel"],
        "stats": {"hp": 98, "atk": 87, "def": 105, "spa": 53, "spd": 85, "spe": 67},
        "abilities": ["Pressure", "Mirror Armor"],
        "pool": ["bodypress", "irondefense", "protect", "flashcannon"]
    },
    "toxapex": {
        "dex": 748, "name": "Toxapex", "types": ["Poison", "Water"],
        "stats": {"hp": 50, "atk": 63, "def": 152, "spa": 53, "spd": 142, "spe": 35},
        "abilities": ["Regenerator", "Merciless"],
        "pool": ["recover", "toxic", "scald", "protect", "surf", "icebeam"]
    },
    "dragapult": {
        "dex": 887, "name": "Dragapult", "types": ["Dragon", "Ghost"],
        "stats": {"hp": 88, "atk": 120, "def": 75, "spa": 100, "spd": 75, "spe": 142},
        "abilities": ["Infiltrator", "Clear Body"],
        "pool": ["dracometeor", "dazzlinggleam", "thunderbolt", "icebeam", "surf", "protect"]
    }
}

DEFAULT_BASELINES: List[Dict] = [
    {
        "build_id": "baseline_corv",
        "species_id": "corviknight",
        "level": 100,
        "nature": "Impish",
        "ability": "Mirror Armor",
        "item": "Leftovers",
        "moves": ["bodypress", "irondefense", "protect", "flashcannon"],
        "ivs": {"hp": 31, "atk": 31, "def": 31, "spa": 31, "spd": 31, "spe": 31},
        "evs": {"hp": 252, "atk": 0, "def": 252, "spa": 0, "spd": 4, "spe": 0}
    },
    {
        "build_id": "baseline_pex",
        "species_id": "toxapex",
        "level": 100,
        "nature": "Bold",
        "ability": "Regenerator",
        "item": "Leftovers",
        "moves": ["scald", "toxic", "recover", "protect"],
        "ivs": {"hp": 31, "atk": 0, "def": 31, "spa": 31, "spd": 31, "spe": 31},
        "evs": {"hp": 252, "atk": 0, "def": 252, "spa": 0, "spd": 4, "spe": 0}
    },
    {
        "build_id": "baseline_drag",
        "species_id": "dragapult",
        "level": 100,
        "nature": "Timid",
        "ability": "Infiltrator",
        "item": "Leftovers",
        "moves": ["dracometeor", "thunderbolt", "icebeam", "protect"],
        "ivs": {"hp": 31, "atk": 0, "def": 31, "spa": 31, "spd": 31, "spe": 31},
        "evs": {"hp": 0, "atk": 0, "def": 4, "spa": 252, "spd": 0, "spe": 252}
    }
]


def get_default_baselines() -> List[Dict]:
    """Returns an isolated deep copy of the standard baseline opponents."""
    return copy.deepcopy(DEFAULT_BASELINES)


# ============================================================================
# 4. LEGALITY VALIDATOR
# ============================================================================

class BuildLegalityValidator:
    """Validates builds against internal tournament rules and species data."""

    @staticmethod
    def validate(build: Dict, species_db: Dict, moves_db: Dict) -> Tuple[bool, List[str]]:
        errors = []
        spec_id = build.get("species_id", "").lower()

        if spec_id not in species_db:
            return False, [f"Unknown species identifier: '{spec_id}'"]

        species = species_db[spec_id]

        if build.get("level", 100) != 100:
            errors.append(f"Invalid level {build.get('level')}: Tournament requires Level 100.")

        nat = build.get("nature", "").capitalize()
        if nat not in NATURES:
            errors.append(f"Invalid nature '{build.get('nature')}'.")

        if build.get("ability") not in species["abilities"]:
            errors.append(f"Illegal ability '{build.get('ability')}' for {species['name']}.")

        evs = build.get("evs", {})
        total_evs = sum(evs.values())
        if total_evs > 510:
            errors.append(f"Total EVs ({total_evs}) exceed limit of 510.")
        for stat, val in evs.items():
            if val < 0 or val > 252:
                errors.append(f"EV for {stat} ({val}) is outside [0, 252].")

        ivs = build.get("ivs", {})
        if isinstance(ivs, dict):
            for stat, val in ivs.items():
                if val < 0 or val > 31:
                    errors.append(f"IV for {stat} ({val}) is outside [0, 31].")

        moves = build.get("moves", [])
        if not (1 <= len(moves) <= 4):
            errors.append(f"Move count ({len(moves)}) must be 1 to 4.")
        if len(set(moves)) != len(moves):
            errors.append(f"Duplicate moves detected: {moves}")

        for m in moves:
            if m not in moves_db:
                errors.append(f"Move '{m}' is not recognized in database.")
            elif m not in species["pool"]:
                errors.append(f"Move '{m}' is illegal for {species['name']}'s allowed pool.")

        return (len(errors) == 0, errors)


# ============================================================================
# 5. SIMULATION ENGINE
# ============================================================================

@dataclass
class CombatantState:
    build_id: str
    name: str
    types: List[str]
    max_hp: int
    current_hp: int
    stats: Dict[str, int]
    stages: Dict[str, int] = field(default_factory=lambda: {"atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0})
    moves: List[str] = field(default_factory=list)
    ability: str = ""
    item: str = ""
    is_fainted: bool = False
    protecting: bool = False
    damage_dealt: int = 0
    damage_taken: int = 0
    healing_done: int = 0
    kills: int = 0
    survival_turns: int = 0

    def get_effective_stat(self, stat_name: str) -> int:
        base = self.stats[stat_name]
        stage = self.stages.get(stat_name, 0)
        multiplier = (2 + stage) / 2 if stage >= 0 else 2 / (2 - stage)
        return math.floor(base * multiplier)


class BattleRoyaleSim:
    """Deterministic micro-simulator for multi-opponent survival evaluations."""

    def __init__(
        self,
        combatants_data: List[Dict],
        seed: int = 42,
        species_db: Optional[Dict] = None,
        moves_db: Optional[Dict] = None
    ):
        self.rng = random.Random(seed)
        self.species_db = species_db if species_db is not None else REFERENCE_SPECIES
        self.moves_db = moves_db if moves_db is not None else REFERENCE_MOVES
        self.combatants: List[CombatantState] = []

        for d in combatants_data:
            s_spec = self.species_db[d["species_id"]]
            ivs = d.get("ivs", {})
            evs = d.get("evs", {})
            hp_iv = ivs.get("hp", 31) if isinstance(ivs, dict) else (ivs if isinstance(ivs, int) else 31)
            hp_ev = evs.get("hp", 0)
            hp_val = calc_hp(s_spec["stats"]["hp"], hp_iv, hp_ev)

            stats_dict = {}
            for stat in ["atk", "def", "spa", "spd", "spe"]:
                st_iv = ivs.get(stat, 31) if isinstance(ivs, dict) else (ivs if isinstance(ivs, int) else 31)
                st_ev = evs.get(stat, 0)
                stats_dict[stat] = calc_stat(s_spec["stats"][stat], stat, d["nature"], st_iv, st_ev)

            c = CombatantState(
                build_id=d.get("build_id", d.get("species_id")),
                name=s_spec["name"],
                types=s_spec["types"],
                max_hp=hp_val,
                current_hp=hp_val,
                stats=stats_dict,
                moves=d["moves"],
                ability=d["ability"],
                item=d.get("item", "Leftovers")
            )
            self.combatants.append(c)

    def calculate_damage(self, attacker: CombatantState, defender: CombatantState, move_id: str, is_spread: bool) -> int:
        mv = self.moves_db[move_id]
        if mv["cat"] == "status":
            return 0

        if mv.get("def_scaling", False):
            atk_stat = attacker.get_effective_stat("def")
            def_stat = defender.get_effective_stat("def")
        elif mv["cat"] == "physical":
            atk_stat = attacker.get_effective_stat("atk")
            def_stat = defender.get_effective_stat("def")
        else:
            atk_stat = attacker.get_effective_stat("spa")
            def_stat = defender.get_effective_stat("spd")

        base_damage = math.floor(math.floor((2 * 100 / 5 + 2) * mv["bp"] * atk_stat / def_stat) / 50) + 2
        if is_spread:
            base_damage = math.floor(base_damage * 0.75)

        stab = 1.5 if mv["type"] in attacker.types else 1.0
        type_eff = get_type_effectiveness(mv["type"], defender.types)
        roll = self.rng.randint(85, 100) / 100.0

        total_damage = math.floor(base_damage * stab * type_eff * roll)
        return max(1 if type_eff > 0 and mv["bp"] > 0 else 0, total_damage)

    def run_turn(self):
        active = [c for c in self.combatants if not c.is_fainted]
        if len(active) <= 1:
            return

        for c in active:
            c.protecting = False
            c.survival_turns += 1

        actions = []
        for c in active:
            chosen_move = self.rng.choice(c.moves)
            mv = self.moves_db[chosen_move]
            actions.append((mv["priority"], c.get_effective_stat("spe"), self.rng.random(), c, chosen_move))

        actions.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)

        for _, _, _, attacker, move_id in actions:
            if attacker.is_fainted:
                continue

            mv = self.moves_db[move_id]
            if move_id == "protect":
                attacker.protecting = True
                continue

            if move_id in ("recover", "roost"):
                heal_amt = math.floor(attacker.max_hp * mv.get("heal", 0.5))
                actual_heal = min(heal_amt, attacker.max_hp - attacker.current_hp)
                attacker.current_hp += actual_heal
                attacker.healing_done += actual_heal
                continue

            if "boosts" in mv:
                for b_stat, amt in mv["boosts"].items():
                    attacker.stages[b_stat] = min(6, attacker.stages[b_stat] + amt)
                continue

            alive_others = [c for c in self.combatants if not c.is_fainted and c != attacker]
            if not alive_others:
                break

            target_mode = mv["target"]
            is_spread = False
            if target_mode in ("adjacentAll", "adjacentFoes"):
                targets = alive_others
                is_spread = len(targets) > 1
            else:
                targets = [min(alive_others, key=lambda c: c.current_hp)]

            for target in targets:
                if target.protecting:
                    continue
                dmg = self.calculate_damage(attacker, target, move_id, is_spread)
                applied = min(dmg, target.current_hp)
                target.current_hp -= applied
                target.damage_taken += applied
                attacker.damage_dealt += applied

                if target.current_hp <= 0:
                    target.is_fainted = True
                    attacker.kills += 1

        for c in [c for c in self.combatants if not c.is_fainted]:
            if c.item == "Leftovers" and c.current_hp < c.max_hp:
                p_heal = min(math.floor(c.max_hp / 16), c.max_hp - c.current_hp)
                c.current_hp += p_heal
                c.healing_done += p_heal

    def simulate(self, max_turns: int = 50) -> Dict[str, Dict]:
        turns = 0
        while turns < max_turns:
            active = [c for c in self.combatants if not c.is_fainted]
            if len(active) <= 1:
                break
            self.run_turn()
            turns += 1

        sorted_combatants = sorted(
            self.combatants,
            key=lambda c: (1 if not c.is_fainted else 0, c.survival_turns, c.damage_dealt),
            reverse=True
        )

        results = {}
        for rank, c in enumerate(sorted_combatants, start=1):
            results[c.build_id] = {
                "rank": rank,
                "kills": c.kills,
                "damage_dealt": c.damage_dealt,
                "damage_taken": c.damage_taken,
                "healing_done": c.healing_done,
                "survival_turns": c.survival_turns,
                "is_fainted": c.is_fainted
            }
        return results


# ============================================================================
# 6. GENETIC OPTIMIZER
# ============================================================================

class BattleRoyaleBuildOptimizer:
    """Optimizes level-100 builds for a multi-opponent survival objective."""

    def __init__(
        self,
        candidate_species_id: str,
        baseline_opponents: Optional[List[Dict]] = None,
        move_pool_override: Optional[List[str]] = None,
        species_db: Optional[Dict] = None,
        moves_db: Optional[Dict] = None,
        seed: Optional[int] = None
    ):
        self.species_id = candidate_species_id.lower()
        self.species_db = species_db if species_db is not None else REFERENCE_SPECIES
        self.moves_db = moves_db if moves_db is not None else REFERENCE_MOVES

        if self.species_id not in self.species_db:
            raise ValueError(f"Unknown species ID '{candidate_species_id}' in species database.")

        self.species_info = copy.deepcopy(self.species_db[self.species_id])

        if move_pool_override is not None:
            canonical_pool = set(self.species_db[self.species_id]["pool"])
            for m in move_pool_override:
                if m not in self.moves_db:
                    raise ValueError(f"Unsupported move '{m}' in move_pool_override: not in moves database.")
                if m not in canonical_pool:
                    raise ValueError(f"Illegal move '{m}' for species '{self.species_id}': not in canonical move pool.")
            self.species_info["pool"] = list(move_pool_override)

        self.baselines = baseline_opponents if baseline_opponents is not None else get_default_baselines()
        self.rng = random.Random(seed) if seed is not None else random

    def generate_random_genome(self) -> Dict:
        ev_keys = ["hp", "atk", "def", "spa", "spd", "spe"]
        weights = [self.rng.random() for _ in range(6)]
        w_sum = sum(weights) or 1.0
        ev_allotment = [int(w / w_sum * 508) for w in weights]

        ev_dict = {}
        for k, v in zip(ev_keys, ev_allotment):
            ev_dict[k] = min(252, (v // 4) * 4)

        while sum(ev_dict.values()) > 508:
            for k in ev_keys:
                if ev_dict[k] >= 4 and sum(ev_dict.values()) > 508:
                    ev_dict[k] -= 4

        pool = self.species_info["pool"]
        n_moves = min(4, len(pool))
        moves = self.rng.sample(pool, n_moves)

        return {
            "build_id": "candidate",
            "species_id": self.species_id,
            "species": self.species_info["name"],
            "level": 100,
            "nature": self.rng.choice(list(NATURES.keys())),
            "ability": self.rng.choice(self.species_info["abilities"]),
            "item": "Leftovers",
            "moves": moves,
            "ivs": {"hp": 31, "atk": 31, "def": 31, "spa": 31, "spd": 31, "spe": 31},
            "evs": ev_dict
        }

    def evaluate_fitness(self, genome: Dict, n_trials: int = 30) -> float:
        active_db = dict(self.species_db)
        active_db[self.species_id] = self.species_info
        is_legal, _ = BuildLegalityValidator.validate(genome, active_db, self.moves_db)
        if not is_legal:
            return -9999.0

        total_score = 0.0
        cand_id = genome.get("build_id", "candidate")

        for trial in range(n_trials):
            opponents = [copy.deepcopy(b) for b in self.baselines[:3]]
            match_pool = [genome] + opponents

            sim = BattleRoyaleSim(
                match_pool,
                seed=(trial * 7 + 13),
                species_db=active_db,
                moves_db=self.moves_db
            )
            res = sim.simulate()
            cand_res = res[cand_id]

            rank_score = (4.0 - cand_res["rank"]) / 3.0
            kills = cand_res["kills"]
            eff = (cand_res["damage_dealt"] + cand_res["healing_done"]) / (cand_res["damage_taken"] + 1)
            trial_score = 0.50 * rank_score + 0.25 * kills + 0.25 * min(3.0, eff)
            total_score += trial_score

        return total_score / n_trials

    def optimize(self, generations: int = 10, pop_size: int = 8, n_trials: int = 25) -> Tuple[Dict, float]:
        population = [self.generate_random_genome() for _ in range(pop_size)]
        best_genome = population[0]
        best_fitness = -9999.0

        for _ in range(generations):
            scored = []
            for indiv in population:
                fit = self.evaluate_fitness(indiv, n_trials=n_trials)
                scored.append((fit, indiv))

            scored.sort(key=lambda x: x[0], reverse=True)
            if scored[0][0] > best_fitness:
                best_fitness = scored[0][0]
                best_genome = copy.deepcopy(scored[0][1])

            survivors = [ind for fit, ind in scored[:max(1, pop_size // 2)]]
            new_pop = [copy.deepcopy(s) for s in survivors]

            while len(new_pop) < pop_size:
                parent = self.rng.choice(survivors)
                child = copy.deepcopy(parent)

                available = [m for m in self.species_info["pool"] if m not in child["moves"]]
                if available:
                    child["moves"] = list(child["moves"])
                    child["moves"][self.rng.randint(0, len(child["moves"]) - 1)] = self.rng.choice(available)

                if self.rng.random() < 0.35:
                    child["nature"] = self.rng.choice(list(NATURES.keys()))

                if self.rng.random() < 0.40:
                    k_from, k_to = self.rng.sample(list(child["evs"].keys()), 2)
                    delta = self.rng.choice([4, 8, 16, 32])
                    if child["evs"][k_from] >= delta and child["evs"][k_to] + delta <= 252:
                        child["evs"][k_from] -= delta
                        child["evs"][k_to] += delta

                new_pop.append(child)

            population = new_pop

        return best_genome, best_fitness