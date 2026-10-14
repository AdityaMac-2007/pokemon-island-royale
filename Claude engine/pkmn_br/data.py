"""Data interfaces. The engine never touches raw Pokemon data: it consumes `Build`s
from a `DataProvider`. The real data/optimisation pipeline plugs in by emitting
builds.json (see JsonProvider) or implementing `load_roster`."""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Protocol

from .effects import Effect, validate_move
from .rng import stream
from .rules import TYPES, calc_stats


@dataclass(frozen=True)
class Move:
    name: str
    type: str
    category: str          # physical | special
    power: int
    accuracy: int
    priority: int = 0
    reach: str = "melee"   # melee | ranged
    hits_air: bool = False # can a GROUNDED user target an AIRBORNE foe with it?
    status: str | None = None
    status_chance: float = 0.0
    effects: tuple = ()    # Combat v1 data-driven effects; validated at registration

    @property
    def self_targeted(self) -> bool:
        return self.category == "status" and all(e.target == "self" for e in self.effects)

    @property
    def is_protect(self) -> bool:
        return any(e.kind == "protect" for e in self.effects)


_M = [  # name,type,cat,power,acc,prio,reach,hits_air,status,chance
 ("earthquake","ground","physical",100,100,0,"ranged",False,None,0),
 ("close_combat","fighting","physical",120,100,0,"melee",False,None,0),
 ("flare_blitz","fire","physical",120,100,0,"melee",False,"burn",.1),
 ("flamethrower","fire","special",90,100,0,"ranged",True,"burn",.1),
 ("surf","water","special",90,100,0,"ranged",True,None,0),
 ("waterfall","water","physical",80,100,0,"melee",False,None,0),
 ("hydro_pump","water","special",110,80,0,"ranged",True,None,0),
 ("thunderbolt","electric","special",90,100,0,"ranged",True,"para",.1),
 ("energy_ball","grass","special",90,100,0,"ranged",True,None,0),
 ("leaf_blade","grass","physical",90,100,0,"melee",False,None,0),
 ("ice_beam","ice","special",90,100,0,"ranged",True,None,0),
 ("icicle_crash","ice","physical",85,90,0,"melee",False,None,0),
 ("rock_slide","rock","physical",75,90,0,"ranged",True,None,0),
 ("stone_edge","rock","physical",100,80,0,"melee",False,None,0),
 ("shadow_ball","ghost","special",80,100,0,"ranged",True,None,0),
 ("dragon_claw","dragon","physical",80,100,0,"melee",False,None,0),
 ("draco_meteor","dragon","special",130,90,0,"ranged",True,None,0),
 ("dark_pulse","dark","special",80,100,0,"ranged",True,None,0),
 ("crunch","dark","physical",80,100,0,"melee",False,None,0),
 ("psychic","psychic","special",90,100,0,"ranged",True,None,0),
 ("moonblast","fairy","special",95,100,0,"ranged",True,None,0),
 ("play_rough","fairy","physical",90,90,0,"melee",False,None,0),
 ("iron_head","steel","physical",80,100,0,"melee",False,None,0),
 ("flash_cannon","steel","special",80,100,0,"ranged",True,None,0),
 ("sludge_bomb","poison","special",90,100,0,"ranged",True,"poison",.3),
 ("bug_buzz","bug","special",90,100,0,"ranged",True,None,0),
 ("x_scissor","bug","physical",80,100,0,"melee",False,None,0),
 ("brave_bird","flying","physical",120,100,0,"melee",False,None,0),
 ("air_slash","flying","special",75,95,0,"ranged",True,None,0),
 ("hurricane","flying","special",110,70,0,"ranged",True,None,0),
 ("extreme_speed","normal","physical",80,100,2,"melee",False,None,0),
 ("quick_attack","normal","physical",40,100,1,"melee",False,None,0),
 ("aqua_jet","water","physical",40,100,1,"melee",False,None,0),
 ("shadow_sneak","ghost","physical",40,100,1,"melee",False,None,0),
 ("body_slam","normal","physical",85,100,0,"melee",False,"para",.3),
 ("hyper_voice","normal","special",90,100,0,"ranged",True,None,0),
]
MOVES = {}


def register_move(mv: Move) -> Move:
    """The only way into the registry. Raises EffectError if any declared effect lacks a validated handler."""
    validate_move(mv)
    MOVES[mv.name] = mv
    return mv


for _m in _M:
    register_move(Move(*_m))

_E = Effect
_NEW_MOVES = [  # Combat Systems v1 first batch. Values per Gen 9 move data; see docs/COMBAT_V1.md for sources.
    Move("protect", "normal", "status", 0, 100, 4, "melee", True, effects=(_E("protect", "use", "self"),)),
    Move("swords_dance", "normal", "status", 0, 100, 0, "melee", True,
         effects=(_E("stat_stage", "use", "self", stat="atk", delta=2),)),
    Move("calm_mind", "psychic", "status", 0, 100, 0, "melee", True,
         effects=(_E("stat_stage", "use", "self", stat="spa", delta=1), _E("stat_stage", "use", "self", stat="spd", delta=1))),
    Move("dragon_dance", "dragon", "status", 0, 100, 0, "melee", True,
         effects=(_E("stat_stage", "use", "self", stat="atk", delta=1), _E("stat_stage", "use", "self", stat="spe", delta=1))),
    Move("recover", "normal", "status", 0, 100, 0, "melee", True, effects=(_E("heal", "use", "self", num=1, den=2),)),
    Move("screech", "normal", "status", 0, 85, 0, "ranged", True,
         effects=(_E("stat_stage", "use", "target", stat="def", delta=-2),)),
    Move("thunder_wave", "electric", "status", 0, 90, 0, "ranged", True,
         effects=(_E("status", "use", "target", status="para"),)),
    Move("double_edge", "normal", "physical", 120, 100, 0, "melee", False, effects=(_E("recoil", "hit", "self", num=1, den=3),)),
    Move("head_smash", "rock", "physical", 150, 80, 0, "melee", False, effects=(_E("recoil", "hit", "self", num=1, den=2),)),
    Move("superpower", "fighting", "physical", 120, 100, 0, "melee", False,
         effects=(_E("stat_stage", "hit", "self", stat="atk", delta=-1), _E("stat_stage", "hit", "self", stat="def", delta=-1))),
    Move("icy_wind", "ice", "special", 55, 95, 0, "ranged", True,
         effects=(_E("stat_stage", "hit", "target", stat="spe", delta=-1),)),
    Move("mystical_fire", "fire", "special", 75, 100, 0, "ranged", True,
         effects=(_E("stat_stage", "hit", "target", stat="spa", delta=-1),)),
]
for _m in _NEW_MOVES:
    register_move(_m)
LEGACY_MOVE_COUNT = len(_M)


@dataclass
class Build:
    species: str
    types: tuple
    stats: dict            # final level-100 stats (hp, atk, def, spa, spd, spe)
    moves: list            # move names (<=4)
    ability: str = ""
    item: str = ""
    can_fly: bool = False
    form: str = "base"


class DataProvider(Protocol):
    def load_roster(self, cfg) -> list: ...


# name, types, base stats (hp,atk,def,spa,spd,spe), ability, item, moves, can_fly
_S = [
 ("Charizard",("fire","flying"),(78,84,78,109,85,100),"blaze","life_orb",["flamethrower","air_slash","dragon_claw","earthquake"],True),
 ("Garchomp",("dragon","ground"),(108,130,95,80,85,102),"","life_orb",["earthquake","dragon_claw","rock_slide","crunch"],False),
 ("Blastoise",("water",),(79,83,100,85,105,78),"torrent","leftovers",["hydro_pump","ice_beam","dark_pulse","flash_cannon"],False),
 ("Venusaur",("grass","poison"),(80,82,83,100,100,80),"overgrow","leftovers",["energy_ball","sludge_bomb","earthquake","hyper_voice"],False),
 ("Pikachu",("electric",),(35,55,40,50,50,90),"","life_orb",["thunderbolt","quick_attack","energy_ball","hyper_voice"],False),
 ("Gengar",("ghost","poison"),(60,65,60,130,75,110),"levitate","life_orb",["shadow_ball","sludge_bomb","energy_ball","thunderbolt"],False),
 ("Dragonite",("dragon","flying"),(91,134,95,100,100,80),"","leftovers",["dragon_claw","extreme_speed","earthquake","brave_bird"],True),
 ("Gyarados",("water","flying"),(95,125,79,60,100,81),"","life_orb",["waterfall","earthquake","icicle_crash","stone_edge"],True),
 ("Lucario",("fighting","steel"),(70,110,70,115,70,90),"","life_orb",["close_combat","flash_cannon","extreme_speed","dark_pulse"],False),
 ("Tyranitar",("rock","dark"),(100,134,110,95,100,61),"","leftovers",["stone_edge","crunch","earthquake","icicle_crash"],False),
 ("Snorlax",("normal",),(160,110,65,65,110,30),"","leftovers",["body_slam","earthquake","crunch","shadow_sneak"],False),
 ("Alakazam",("psychic",),(55,50,45,135,95,120),"","life_orb",["psychic","shadow_ball","energy_ball","flash_cannon"],False),
 ("Scizor",("bug","steel"),(70,130,100,55,80,65),"swarm","choice_band",["x_scissor","iron_head","quick_attack","close_combat"],False),
 ("Togekiss",("fairy","flying"),(85,50,95,120,115,80),"","leftovers",["moonblast","air_slash","flamethrower","shadow_ball"],True),
 ("Lapras",("water","ice"),(130,85,80,85,95,60),"","leftovers",["surf","ice_beam","thunderbolt","dragon_claw"],False),
 ("Arcanine",("fire",),(90,110,80,100,80,95),"","life_orb",["flare_blitz","extreme_speed","close_combat","crunch"],False),
 ("Magnezone",("electric","steel"),(70,70,115,130,90,60),"","choice_specs",["thunderbolt","flash_cannon","hyper_voice","rock_slide"],False),
 ("Salamence",("dragon","flying"),(95,135,80,110,80,100),"","life_orb",["draco_meteor","dragon_claw","earthquake","brave_bird"],True),
 ("Excadrill",("ground","steel"),(110,135,60,50,65,88),"","life_orb",["earthquake","iron_head","rock_slide","x_scissor"],False),
 ("Sceptile",("grass",),(70,85,65,105,85,120),"overgrow","life_orb",["energy_ball","leaf_blade","dragon_claw","shadow_ball"],False),
 ("Weavile",("dark","ice"),(70,120,65,45,85,125),"","life_orb",["icicle_crash","crunch","quick_attack","close_combat"],False),
 ("Latios",("dragon","psychic"),(80,90,80,130,110,110),"levitate","life_orb",["draco_meteor","psychic","shadow_ball","flamethrower"],True),
 ("Skarmory",("steel","flying"),(65,80,140,40,70,70),"","leftovers",["iron_head","brave_bird","rock_slide","extreme_speed"],True),
 ("Ferrothorn",("grass","steel"),(74,94,131,54,116,20),"","leftovers",["leaf_blade","iron_head","earthquake","x_scissor"],False),
]


def _make(name, types, base, ability, item, moves, fly, level=100) -> Build:
    b = dict(zip(("hp", "atk", "def", "spa", "spd", "spe"), base))
    phys = b["atk"] >= b["spa"]
    off, weak = ("atk", "spa") if phys else ("spa", "atk")
    bulky = (b["hp"] + b["def"] + b["spd"]) >= 300 and b["spe"] < 70
    evs = {off: 252, "hp" if bulky else "spe": 252, "spe" if bulky else "hp": 4}
    return Build(name, tuple(types), calc_stats(b, evs, off, weak, level=level), list(moves), ability, item, fly)


class StubProvider:
    """24 hand-built species for prototype testing."""
    def load_roster(self, cfg):
        return [_make(*s, level=cfg.level) for s in _S]


class SyntheticProvider:
    """Procedural roster for scale/perf tests (any size). Deterministic per seed.
    By default it draws only from the original damaging moves so existing synthetic rosters are
    unchanged; combat_moves=True also draws the Combat v1 moves (status/effect moves)."""
    def __init__(self, combat_moves: bool = False):
        self.combat_moves = combat_moves

    def load_roster(self, cfg):
        rng = stream(cfg.seed, "synthetic_roster")
        pool = [m for m in MOVES.values() if self.combat_moves or (not m.effects and m.category != "status")]
        by_type = {}
        for m in pool:
            by_type.setdefault(m.type, []).append(m.name)
        allm = sorted(m.name for m in pool)
        out = []
        n = max(cfg.n_combatants, 1)
        for i in range(n):
            t1 = TYPES[rng.randrange(18)]
            t2 = TYPES[rng.randrange(18)]
            types = (t1,) if t2 == t1 or rng.random() < 0.4 else (t1, t2)
            total = rng.randint(330, 680)
            w = [rng.random() + 0.3 for _ in range(6)]
            s = sum(w)
            base = {k: max(20, int(total * wi / s)) for k, wi in zip(("hp", "atk", "def", "spa", "spd", "spe"), w)}
            mv = []
            for t in types:
                mv += rng.sample(by_type[t], min(2, len(by_type[t])))
            while len(mv) < 4:
                x = allm[rng.randrange(len(allm))]
                if x not in mv:
                    mv.append(x)
            fly = rng.random() < 0.15 or "flying" in types
            out.append(_make(f"Synth-{i:04d}", types, tuple(base.values()), "", rng.choice(["life_orb", "leftovers", ""]), mv[:4], fly, cfg.level))
        return out


class JsonProvider:
    """Reads the builds file emitted by the external Pokemon data/optimisation pipeline.
    Schema: [{"species","types":[...],"stats":{hp,atk,def,spa,spd,spe},"moves":[...],
              "ability","item","can_fly","form"}]"""
    def __init__(self, path):
        self.path = path

    def load_roster(self, cfg):
        with open(self.path) as f:
            rows = json.load(f)
        out = []
        for r in rows:
            for m in r["moves"]:
                if m not in MOVES:
                    raise ValueError(f"unknown move {m!r} in build {r['species']}")
            out.append(Build(r["species"], tuple(r["types"]), r["stats"], r["moves"], r.get("ability", ""),
                             r.get("item", ""), bool(r.get("can_fly", False)), r.get("form", "base")))
        return out


def get_provider(cfg):
    if cfg.roster_source == "stub":
        return StubProvider()
    if cfg.roster_source == "synthetic":
        return SyntheticProvider()
    return JsonProvider(cfg.roster_source)


def select_roster(cfg, provider=None):
    provider = provider or get_provider(cfg)
    pool = provider.load_roster(cfg)
    rng = stream(cfg.seed, "roster")
    n = cfg.n_combatants
    if n <= len(pool):
        idx = rng.sample(range(len(pool)), n)
    elif cfg.allow_duplicates:
        idx = [rng.randrange(len(pool)) for _ in range(n)]
    else:
        raise ValueError("n_combatants exceeds roster size and allow_duplicates is false")
    return [pool[i] for i in idx]
