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


# Base stats, IVs and EVs are three different tables with three different legal ranges.
# (Previously one StatsTable with ge=1 was reused for all three, which rejected legal 0 EVs / 0 IVs.)
BaseStat = conint(ge=1, le=255)   # 1 is legal (Shedinja HP), 255 is legal (Blissey HP); 0 is not
IVValue = conint(ge=0, le=31)     # 0 is legal
EVValue = conint(ge=0, le=252)    # 0 is legal; the 510 total is checked on the build


class BaseStatsTable(BaseModel):
    hp: BaseStat
    atk: BaseStat
    def_: BaseStat = Field(alias="def")
    spa: BaseStat
    spd: BaseStat
    spe: BaseStat

    class Config:
        populate_by_name = True


class IVTable(BaseModel):
    hp: IVValue = 31
    atk: IVValue = 31
    def_: IVValue = Field(default=31, alias="def")
    spa: IVValue = 31
    spd: IVValue = 31
    spe: IVValue = 31

    class Config:
        populate_by_name = True


class EVTable(BaseModel):
    hp: EVValue = 0
    atk: EVValue = 0
    def_: EVValue = Field(default=0, alias="def")
    spa: EVValue = 0
    spd: EVValue = 0
    spe: EVValue = 0

    class Config:
        populate_by_name = True


StatsTable = BaseStatsTable  # backwards-compatible alias; use the specific table for IVs/EVs


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
    base_stats: BaseStatsTable
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
    ivs: IVTable = Field(default_factory=IVTable)
    evs: EVTable = Field(default_factory=EVTable)
    tera_type: Optional[str] = None

    @model_validator(mode="after")
    def validate_build_constraints(self) -> BattleRoyaleBuild:
        total_evs = (
            self.evs.hp + self.evs.atk + self.evs.def_ +
            self.evs.spa + self.evs.spd + self.evs.spe
        )
        if total_evs > 510:
            raise ValueError(f"EV sum exceeds 510 limit: Total is {total_evs}")

        if len(set(self.moves)) != len(self.moves):
            raise ValueError(f"Duplicate moves detected: {self.moves}")

        return self