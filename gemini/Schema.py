"""
POKÉMON ISLAND BATTLE ROYALE - CORE SCHEMAS
Version: 1.0.0-gen9
"""
from __future__ import annotations
from enum import Enum
from typing import Dict, List, Literal, Optional, Set
from pydantic import BaseModel, Field, conint, confloat, model_validator


class TargetScope(str, Enum):
    NORMAL = "normal"                          # Single adjacent target
    ANY = "any"                                # Any single target on field
    SELF = "self"                              # User only
    ADJACENT_ALL = "adjacentAll"               # All adjacent combatants (Explosion, Earthquake)
    ADJACENT_FOES = "adjacentFoes"             # All adjacent enemies (Surf, Heat Wave)
    ALL = "all"                                # Everyone on the field
    ALL_FOES = "allFoes"                      # All enemies across the active zone
    FIELD = "field"                            # Modifies field/weather/terrain


class DamageClass(str, Enum):
    PHYSICAL = "physical"
    SPECIAL = "special"
    STATUS = "status"


class MovementType(str, Enum):
    TERRESTRIAL = "terrestrial"
    AERIAL = "aerial"                          # Flying, Levitate, Magnet Rise
    AQUATIC = "aquatic"                        # Swift Swim, Water-type native swimmers
    AMPHIBIOUS = "amphibious"


class StatsTable(BaseModel):
    hp: conint(ge=1, le=255)
    atk: conint(ge=1, le=255)
    def_: conint(ge=1, le=255) = Field(alias="def")
    spa: conint(ge=1, le=255)
    spd: conint(ge=1, le=255)
    spe: conint(ge=1, le=255)

    class Config:
        populate_by_name = True


class MoveData(BaseModel):
    id: str
    num: int
    name: str
    type: str
    category: DamageClass
    base_power: int = Field(ge=0, le=250)
    accuracy: int = Field(ge=0, le=100)        # 0 denotes moves that bypass accuracy checks
    pp: int = Field(ge=1, le=40)
    priority: int = Field(ge=-7, le=5)
    target: TargetScope
    flags: Dict[str, bool] = Field(default_factory=dict)  # contact, protect, mirror, sound, etc.
    drain: Optional[List[int]] = None           # [numerator, denominator]
    recoil: Optional[List[int]] = None
    heal: Optional[List[int]] = None
    secondary_effects: Optional[List[Dict]] = None


class SpeciesData(BaseModel):
    id: str                                    # Normalized showdown ID (e.g. "urshifurapidstrike")
    dex_number: int
    name: str
    form_id: Optional[str] = None
    types: List[str] = Field(min_length=1, max_length=2)
    base_stats: StatsTable
    abilities: Dict[str, str]                  # {"0": "ability1", "1": "ability2", "H": "hidden"}
    weight_kg: float
    height_m: float
    movement_profile: MovementType
    legal_moves: Set[str] = Field(default_factory=set)
    is_battle_only_form: bool = False
    transformation_trigger: Optional[Dict] = None


class BattleRoyaleBuild(BaseModel):
    species_id: str
    level: Literal[100] = 100
    nature: str
    ability: str
    item: str
    moves: List[str] = Field(min_length=1, max_length=4)
    ivs: StatsTable = Field(
        default_factory=lambda: StatsTable(hp=31, atk=31, **{"def": 31}, spa=31, spd=31, spe=31)
    )
    evs: StatsTable
    tera_type: Optional[str] = None

    @model_validator(mode="after")
    def validate_build_constraints(self) -> BattleRoyaleBuild:
        total_evs = (
            self.evs.hp + self.evs.atk + self.evs.def_ +
            self.evs.spa + self.evs.spd + self.evs.spe
        )
        if total_evs > 510:
            raise ValueError(f"EV sum exceeds 510 limit: Total is {total_evs}")

        for stat, val in [("hp", self.evs.hp), ("atk", self.evs.atk), ("def", self.evs.def_),
                          ("spa", self.evs.spa), ("spd", self.evs.spd), ("spe", self.evs.spe)]:
            if val < 0 or val > 252:
                raise ValueError(f"EV for {stat} ({val}) is out of legal bounds [0, 252]")

        if len(set(self.moves)) != len(self.moves):
            raise ValueError(f"Duplicate moves detected: {self.moves}")

        return self