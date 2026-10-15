"""Configuration: one JSON-serialisable object fully determines a match."""
from __future__ import annotations
import hashlib
import json
import math
from dataclasses import dataclass, field, asdict


@dataclass
class ZoneConfig:
    phase_fracs: list = field(default_factory=lambda: [1.0, 0.7, 0.45, 0.25, 0.12, 0.04])
    hold_ticks: int = 250
    shrink_ticks: int = 150
    damage_pct: list = field(default_factory=lambda: [0.002, 0.006, 0.012, 0.025, 0.04, 0.06])
    final_shrink_ticks: int = 300      # last circle collapses toward a point (sudden death)
    final_damage_pct: float = 0.08
    lookahead_ticks: int = 60          # AI starts moving to the next circle this early


@dataclass
class ChaosConfig:
    """Cross-generation 'chaos' layer. Off by default; independent of canonical rules."""
    enabled: bool = False
    crit_chance: float = 0.20
    roll_lo: float = 0.70
    roll_hi: float = 1.15
    stab: float = 2.0
    event_rate_mult: float = 3.0


@dataclass
class Config:
    seed: int = 1
    n_combatants: int = 24
    roster_source: str = "stub"        # "stub" | "synthetic" | path to builds.json
    roster_snapshot: str = "stub-v1"
    allow_duplicates: bool = True
    form_policy: str = "battle_forms"  # base_only | battle_forms | all  (consumed by data pipeline)
    ruleset: str = "canonical"         # canonical | chaos
    arena_size: float = 0.0            # 0 -> auto from n_combatants
    max_ticks: int = 6000
    # --- combat pacing
    level: int = 100
    action_threshold: float = 100.0
    energy_per_speed: float = 0.10
    energy_cap: float = 130.0
    speed_tie_mode: str = "random"     # random | simultaneous
    residual_interval: int = 8
    # --- combat systems v1
    para_skip_chance: float = 0.0      # canonical Gen 9 value is 0.25; kept 0.0 so legacy digests stay valid
    protect_duration_ticks: int = 3    # a successful Protect blocks moves aimed at the user for this many ticks
    # --- spatial
    melee_range: float = 1.8
    ranged_range: float = 7.0
    attack_slack: float = 0.4
    vision_radius: float = 16.0
    body_radius: float = 0.5
    base_move: float = 0.5
    air_speed_mult: float = 1.4
    stamina_max: float = 100.0
    takeoff_min: float = 40.0
    takeoff_cost: float = 8.0
    # --- ai
    ai_interval: int = 2
    zone_margin: float = 1.5
    # --- world
    berry_density: float = 0.6         # berries per combatant
    berry_heal_frac: float = 0.30
    pickup_radius: float = 1.6
    event_interval: int = 45           # mean ticks between telegraphed hazards
    hazard_warning_ticks: int = 12
    # --- logging
    log_hits: bool = True
    replay_every: int = 5
    zone: ZoneConfig = field(default_factory=ZoneConfig)
    chaos: ChaosConfig = field(default_factory=ChaosConfig)
    island_shape: str = "irregular"  # "irregular" | "circle"

    def resolved_size(self) -> float:
        return self.arena_size if self.arena_size > 0 else max(48.0, 12.0 * math.sqrt(self.n_combatants))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        d = dict(d)
        # Backward compatibility: historical serialized configs predate island_shape
        # and must retain the original circular arena geometry.
        d.setdefault("island_shape", "circle")
        z = ZoneConfig(**d.pop("zone", {}))
        c = ChaosConfig(**d.pop("chaos", {}))
        return cls(zone=z, chaos=c, **d)

    @classmethod
    def load(cls, path: str) -> "Config":
        with open(path) as f:
            return cls.from_dict(json.load(f))

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:16]
