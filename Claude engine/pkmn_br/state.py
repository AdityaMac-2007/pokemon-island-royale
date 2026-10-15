"""Combatant state (pure data; mutated only by the Engine)."""
from __future__ import annotations
from dataclasses import dataclass, field
from .data import MOVES
from .rules import STAGE_STATS, apply_stage


@dataclass
class Intent:
    goal: tuple | None = None
    alt: int = 0
    attack: tuple | None = None    # (target_id, move_index)
    target: int | None = None
    mode: str = "wander"
    reason: str = ""
    p_win: float | None = None


@dataclass
class Combatant:
    id: int
    name: str
    types: tuple
    stats: dict
    moves: list
    ability: str
    item: str
    can_fly: bool
    max_hp: int = 0
    hp: int = 0
    x: float = 0.0
    y: float = 0.0
    alt: int = 0
    stamina: float = 100.0
    energy: float = 0.0
    status: str | None = None
    alive: bool = True
    placement: int = 0
    bravery: float = 0.0
    biome: str = "plains"
    intent: Intent | None = None
    target: int | None = None
    mode: str = "wander"
    sturdy_used: bool = False
    flash_fire_active: bool = False
    stages: dict = field(default_factory=lambda: {s: 0 for s in STAGE_STATS})
    protected_until: int = -1          # blocks moves aimed at this combatant while tick < protected_until
    protect_streak: int = 0
    utility: list = field(default_factory=list)   # indexes of status moves (AI fast path)
    # stats / explainability
    kills: int = 0
    dmg_dealt: int = 0
    dmg_taken: int = 0
    env_dmg: int = 0
    berries: int = 0
    flight_ticks: int = 0
    last_p_win: float | None = None
    elim_tick: int | None = None
    recent: list = field(default_factory=list)   # last 5 hits taken

    @classmethod
    def from_build(cls, i, b):
        c = cls(i, b.species, tuple(b.types), dict(b.stats), [MOVES[m] for m in b.moves], b.ability, b.item, b.can_fly)
        c.max_hp = c.hp = b.stats["hp"]
        c.utility = [i for i, m in enumerate(c.moves) if m.category == "status"]
        c.sturdy_used = False
        c.flash_fire_active = False
        return c

    def eff_speed(self) -> int:
        s = self.stats["spe"]
        st = self.stages["spe"]
        if st:
            s = apply_stage(s, st)
        return s // 2 if self.status == "para" else s
