"""
Adapter: Bridges Gemini's build optimizer to Claude's battle royale engine.
Enforces move aliasing, strict rejection, auditable flight, and deterministic export.
File: gemini/adapter.py
"""
from __future__ import annotations
import copy
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Set, Any

# Ensure 'Claude engine' is in sys.path if located in project root
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CLAUDE_DIR = os.path.join(_ROOT, "Claude engine")
if os.path.isdir(_CLAUDE_DIR) and _CLAUDE_DIR not in sys.path:
    sys.path.insert(0, _CLAUDE_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def get_claude_move_registry() -> Set[str]:
    """
    Imports Claude's real move registry.
    Fails loudly if Claude's package cannot be imported.
    """
    try:
        from pkmn_br.data import MOVES  # type: ignore
        return set(MOVES.keys())
    except ImportError as exc:
        raise ImportError(
            "FATAL: Could not import Claude's battle royale engine package 'pkmn_br.data'. "
            "Ensure 'Claude engine' is in sys.path or PYTHONPATH. "
            f"Original error: {exc}"
        ) from exc


def get_claude_calc_stats():
    """Imports Claude's real calc_stats function. Fails loudly on failure."""
    try:
        from pkmn_br.rules import calc_stats  # type: ignore
        return calc_stats
    except ImportError as exc:
        raise ImportError(
            "FATAL: Could not import Claude's battle mechanics package 'pkmn_br.rules'. "
            "Ensure 'Claude engine' is in sys.path or PYTHONPATH. "
            f"Original error: {exc}"
        ) from exc


# ============================================================================
# EXPLICIT MOVE ALIAS MAPPING
# ============================================================================

# Strict aliases for identical moves across formatting styles.
# Moves that are mechanically different are NEVER aliased (e.g., bodypress != body_slam).
MOVE_ALIASES: Dict[str, str] = {
    "closecombat": "close_combat",
    "close_combat": "close_combat",
    "dracometeor": "draco_meteor",
    "draco_meteor": "draco_meteor",
    "dragonclaw": "dragon_claw",
    "dragon_claw": "dragon_claw",
    "rockslide": "rock_slide",
    "rock_slide": "rock_slide",
    "stoneedge": "stone_edge",
    "stone_edge": "stone_edge",
    "shadowball": "shadow_ball",
    "shadow_ball": "shadow_ball",
    "flareblitz": "flare_blitz",
    "flare_blitz": "flare_blitz",
    "flashcannon": "flash_cannon",
    "flash_cannon": "flash_cannon",
    "energyball": "energy_ball",
    "energy_ball": "energy_ball",
    "leafblade": "leaf_blade",
    "leaf_blade": "leaf_blade",
    "icebeam": "ice_beam",
    "ice_beam": "ice_beam",
    "iciclecrash": "icicle_crash",
    "icicle_crash": "icicle_crash",
    "ironhead": "iron_head",
    "iron_head": "iron_head",
    "sludgebomb": "sludge_bomb",
    "sludge_bomb": "sludge_bomb",
    "bugbuzz": "bug_buzz",
    "bug_buzz": "bug_buzz",
    "xscissor": "x_scissor",
    "x_scissor": "x_scissor",
    "bravebird": "brave_bird",
    "brave_bird": "brave_bird",
    "airslash": "air_slash",
    "air_slash": "air_slash",
    "extremespeed": "extreme_speed",
    "extreme_speed": "extreme_speed",
    "quickattack": "quick_attack",
    "quick_attack": "quick_attack",
    "aquajet": "aqua_jet",
    "aqua_jet": "aqua_jet",
    "shadowsneak": "shadow_sneak",
    "shadow_sneak": "shadow_sneak",
    "bodyslam": "body_slam",
    "body_slam": "body_slam",
    "hypervoice": "hyper_voice",
    "hyper_voice": "hyper_voice",
    "darkpulse": "dark_pulse",
    "dark_pulse": "dark_pulse",
    "playrough": "play_rough",
    "play_rough": "play_rough",
    "hydropump": "hydro_pump",
    "hydro_pump": "hydro_pump",
    "earthquake": "earthquake",
    "surf": "surf",
    "crunch": "crunch",
    "flamethrower": "flamethrower",
    "hurricane": "hurricane",
    "moonblast": "moonblast",
    "psychic": "psychic",
    "thunderbolt": "thunderbolt",
    "waterfall": "waterfall",
}

# Abilities recognized as having mechanics in Claude's runtime by the build adapter.
CLAUDE_IMPLEMENTED_ABILITIES: Set[str] = {
    "blaze", "overgrow", "torrent", "swarm", "levitate", "sturdy", "sand_veil"
}

# The 4 items with implemented runtime logic in Claude's engine
CLAUDE_IMPLEMENTED_ITEMS: Set[str] = {
    "life_orb", "leftovers", "choice_band", "choice_specs"
}

# Explicit flight capability table (no derivation purely from Flying type or Levitate)
SPECIES_FLIGHT_REGISTRY: Dict[str, bool] = {
    "charizard": True,
    "dragonite": True,
    "gyarados": True,
    "togekiss": True,
    "salamence": True,
    "latios": True,
    "skarmory": True,
    "corviknight": True,
    "garchomp": False,
    "toxapex": False,
    "dragapult": False,
    "blastoise": False,
    "venusaur": False,
    "pikachu": False,
    "gengar": False,
    "lucario": False,
    "tyranitar": False,
    "snorlax": False,
    "alakazam": False,
    "scizor": False,
    "lapras": False,
    "arcanine": False,
    "magnezone": False,
    "excadrill": False,
    "sceptile": False,
    "weavile": False,
    "ferrothorn": False,
}

NATURE_MODIFIERS: Dict[str, Tuple[Optional[str], Optional[str]]] = {
    "adamant": ("atk", "spa"),
    "bashful": (None, None),
    "bold":    ("def", "atk"),
    "brave":   ("atk", "spe"),
    "calm":    ("spd", "atk"),
    "careful": ("spd", "spa"),
    "docile":  (None, None),
    "gentle":  ("spd", "def"),
    "hardy":   (None, None),
    "hasty":   ("spe", "def"),
    "impish":  ("def", "spa"),
    "jolly":   ("spe", "spa"),
    "lax":     ("def", "spd"),
    "lonely":  ("atk", "def"),
    "mild":    ("spa", "def"),
    "modest":  ("spa", "atk"),
    "naive":   ("spe", "spd"),
    "naughty": ("atk", "spd"),
    "quiet":   ("spa", "spe"),
    "quirky":  (None, None),
    "rash":    ("spa", "spd"),
    "relaxed": ("def", "spe"),
    "sassy":   ("spd", "spe"),
    "serious": (None, None),
    "timid":   ("spe", "atk"),
}

CLAUDE_LEGAL_TYPES: Set[str] = {
    "normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison", "ground",
    "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy"
}

CLAUDE_STAT_KEYS = ("hp", "atk", "def", "spa", "spd", "spe")
CLAUDE_STATS = CLAUDE_STAT_KEYS
CLAUDE_TYPES = sorted(CLAUDE_LEGAL_TYPES)

try:
    from pkmn_br.data import MOVES as CLAUDE_MOVES  # type: ignore
except ImportError:
    CLAUDE_MOVES = {}


# ============================================================================
# RESOLUTION FUNCTIONS
# ============================================================================

def resolve_move_identifier(raw_name: str, valid_registry: Optional[Set[str]] = None) -> Optional[str]:
    """
    Resolves a raw move identifier to Claude's canonical move identifier.
    Returns None if the move is unsupported or unmapped.
    """
    if valid_registry is None:
        valid_registry = get_claude_move_registry()

    clean = raw_name.strip().lower().replace("-", "").replace(" ", "").replace("_", "")
    mapped = MOVE_ALIASES.get(clean)
    if mapped and mapped in valid_registry:
        return mapped

    snake_clean = raw_name.strip().lower().replace("-", "_").replace(" ", "_")
    if snake_clean in valid_registry:
        return snake_clean

    return None


def claude_calc_stats(*args, **kwargs) -> Dict[str, int]:
    """Module-level wrapper around Claude's real calc_stats function."""
    fn = get_claude_calc_stats()
    return fn(*args, **kwargs)


def _serialize_ivs(ivs: Any) -> Any:
    if isinstance(ivs, (int, float, str)):
        return ivs
    if isinstance(ivs, dict):
        return dict(ivs)
    if hasattr(ivs, "model_dump"):
        return ivs.model_dump()
    if hasattr(ivs, "dict"):
        return ivs.dict()
    if hasattr(ivs, "__dataclass_fields__"):
        return asdict(ivs)
    return str(ivs)


@dataclass
class BuildAuditRecord:
    build_id: str
    species: str
    original_nature: str
    original_evs: Dict[str, int]
    original_ivs: Any
    original_moves: List[str]
    original_ability: str
    original_item: str
    original_fitness_score: Optional[float]
    recomputed_fitness_score: Optional[float]
    eval_trials: int
    unsupported_mechanics_warnings: List[str] = field(default_factory=list)
    engine_stats: Dict[str, int] = field(default_factory=dict)
    can_fly: bool = False


class BuildAdapter:
    """Deterministic build validator and exporter for Claude's engine."""

    @staticmethod
    def normalize_identifier(ident: str) -> str:
        if not ident:
            return ""
        s = ident.strip().replace("-", "_").replace(" ", "_")
        out = []
        for i, c in enumerate(s):
            if c.isupper() and i > 0 and s[i - 1] != "_" and not s[i - 1].isupper():
                out.append("_")
            out.append(c.lower())
        return "".join(out).replace("__", "_")

    @classmethod
    def resolve_flight(cls, species_id: str, movement_profile: Optional[str] = None, explicit_fly: Optional[bool] = None) -> bool:
        if explicit_fly is not None:
            return bool(explicit_fly)

        if movement_profile is not None:
            mp = movement_profile.lower()
            if mp == "aerial":
                return True
            elif mp in ("terrestrial", "aquatic", "amphibious"):
                return False
            raise ValueError(f"Unknown movement profile '{movement_profile}' for species '{species_id}'")

        spec_norm = cls.normalize_identifier(species_id)
        if spec_norm in SPECIES_FLIGHT_REGISTRY:
            return SPECIES_FLIGHT_REGISTRY[spec_norm]

        raise ValueError(
            f"Flight capability missing or ambiguous for species '{species_id}'. "
            f"An explicit movement_profile or entry in SPECIES_FLIGHT_REGISTRY is required."
        )

    @classmethod
    def calculate_stats(
        cls,
        base_stats: Dict[str, int],
        evs: Dict[str, int],
        nature: str,
        ivs: Any = 31,
        level: int = 100
    ) -> Dict[str, int]:
        calc_fn = get_claude_calc_stats()

        norm_base = {}
        for k in CLAUDE_STAT_KEYS:
            key = "def_" if (k == "def" and "def_" in base_stats) else k
            if key not in base_stats:
                raise KeyError(f"Base stats missing required stat key '{k}'")
            norm_base[k] = base_stats[key]

        norm_evs = {}
        for k in CLAUDE_STAT_KEYS:
            key = "def_" if (k == "def" and "def_" in evs) else k
            norm_evs[k] = evs.get(key, 0)

        nat_key = nature.lower()
        if nat_key not in NATURE_MODIFIERS:
            raise ValueError(f"Unrecognized nature '{nature}'")
        up, down = NATURE_MODIFIERS[nat_key]

        if isinstance(ivs, dict):
            vals = set(ivs.values())
            if len(vals) > 1:
                raise ValueError(
                    f"Claude's calc_stats requires uniform scalar IVs; received non-uniform IV dictionary: {ivs}"
                )
            scalar_iv = next(iter(vals)) if vals else 31
        elif hasattr(ivs, "hp"):
            vals = {ivs.hp, ivs.atk, getattr(ivs, "def_", getattr(ivs, "def", 31)), ivs.spa, ivs.spd, ivs.spe}
            if len(vals) > 1:
                raise ValueError(f"Claude's calc_stats requires uniform scalar IVs; received non-uniform IVs: {vals}")
            scalar_iv = next(iter(vals))
        elif isinstance(ivs, int):
            scalar_iv = ivs
        else:
            raise TypeError(f"Unsupported IV type: {type(ivs)}")

        return calc_fn(norm_base, norm_evs, up, down, ivs=scalar_iv, level=level)

    @classmethod
    def validate_and_convert(
        cls,
        build: Dict[str, Any],
        base_stats: Dict[str, int],
        types: List[str],
        movement_profile: Optional[str] = None,
        recomputed_fitness: Optional[float] = None,
        eval_trials: int = 0
    ) -> Tuple[Dict[str, Any], BuildAuditRecord]:
        warnings = []
        spec_name = build.get("species", build.get("species_id", ""))
        if not spec_name:
            raise ValueError("Build missing mandatory 'species' or 'species_id' field.")

        valid_moves = get_claude_move_registry()
        moves = build.get("moves", [])
        if not (1 <= len(moves) <= 4):
            raise ValueError(f"Build for '{spec_name}' must have 1-4 moves; got {len(moves)}.")

        norm_moves = []
        unsupported_moves = []
        for m in moves:
            resolved = resolve_move_identifier(m, valid_moves)
            if resolved is None:
                unsupported_moves.append(m)
            else:
                norm_moves.append(resolved)

        if unsupported_moves:
            raise ValueError(
                f"Move rejection: Build for '{spec_name}' contains unsupported moves: {unsupported_moves}. "
                f"Engine strictly supports only the {len(valid_moves)} moves registered in pkmn_br.data.MOVES."
            )

        if len(set(norm_moves)) != len(norm_moves):
            raise ValueError(f"Duplicate moves detected in build for '{spec_name}': {norm_moves}")

        norm_types = [t.lower() for t in types]
        for t in norm_types:
            if t not in CLAUDE_LEGAL_TYPES:
                raise ValueError(f"Invalid Pokémon type '{t}' for species '{spec_name}'.")

        raw_ability = build.get("ability", "")
        norm_ability = cls.normalize_identifier(raw_ability)
        if norm_ability and norm_ability not in CLAUDE_IMPLEMENTED_ABILITIES:
            warnings.append(
                f"Ability '{raw_ability}' (normalized: '{norm_ability}') has no implemented mechanics in Claude's engine; "
                f"it will remain inert during match simulation."
            )

        raw_item = build.get("item", "")
        norm_item = cls.normalize_identifier(raw_item)
        if norm_item and norm_item not in CLAUDE_IMPLEMENTED_ITEMS:
            warnings.append(
                f"Item '{raw_item}' (normalized: '{norm_item}') has no implemented mechanics in Claude's engine; "
                f"it will provide no stat or healing benefits."
            )

        explicit_fly = build.get("can_fly")
        can_fly = cls.resolve_flight(spec_name, movement_profile, explicit_fly)

        nature = build.get("nature", "Hardy")
        evs = build.get("evs", {})
        ivs = build.get("ivs", 31)
        final_stats = cls.calculate_stats(base_stats, evs, nature, ivs, level=build.get("level", 100))

        engine_dict = {
            "species": spec_name.capitalize(),
            "types": norm_types,
            "stats": final_stats,
            "moves": norm_moves,
            "ability": norm_ability,
            "item": norm_item,
            "can_fly": can_fly,
            "form": build.get("form", "base")
        }

        audit_record = BuildAuditRecord(
            build_id=build.get("build_id", spec_name.lower()),
            species=spec_name,
            original_nature=nature,
            original_evs=dict(evs),
            original_ivs=_serialize_ivs(ivs),
            original_moves=list(moves),
            original_ability=raw_ability,
            original_item=raw_item,
            original_fitness_score=build.get("fitness_score"),
            recomputed_fitness_score=recomputed_fitness,
            eval_trials=eval_trials,
            unsupported_mechanics_warnings=warnings,
            engine_stats=final_stats,
            can_fly=can_fly
        )

        return engine_dict, audit_record

    @classmethod
    def export_roster(
        cls,
        build_tuples: List[Tuple[Dict[str, Any], Dict[str, int], List[str], Optional[str], Optional[float], int]],
        output_roster_path: str,
        output_audit_path: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], List[BuildAuditRecord]]:
        engine_roster = []
        audit_records = []

        for b, base_stats, types, movement, fitness, trials in build_tuples:
            eng_b, audit_b = cls.validate_and_convert(
                build=b,
                base_stats=base_stats,
                types=types,
                movement_profile=movement,
                recomputed_fitness=fitness,
                eval_trials=trials
            )
            engine_roster.append(eng_b)
            audit_records.append(audit_b)

        os.makedirs(os.path.dirname(os.path.abspath(output_roster_path)), exist_ok=True)
        with open(output_roster_path, "w", encoding="utf-8") as f:
            json.dump(engine_roster, f, indent=2, sort_keys=True)

        if output_audit_path:
            os.makedirs(os.path.dirname(os.path.abspath(output_audit_path)), exist_ok=True)
            with open(output_audit_path, "w", encoding="utf-8") as f:
                json.dump([asdict(a) for a in audit_records], f, indent=2, sort_keys=True)

        return engine_roster, audit_records