"""Data-driven move effects (Combat Systems v1).

Capability rule: a Move can only be registered if EVERY effect it declares has a
handler in HANDLERS and passes validate_effect(). There is no 'implemented' flag
that can be set on a move; capability is derived from this table. A move whose real
effect is not implemented therefore cannot enter the engine's registry.

Handlers receive an FxContext and return None on success or a short failure-reason
string. They mutate state only through Engine helpers that also emit events.
"""
from __future__ import annotations

from dataclasses import dataclass

from .abilities import check_status_immunity
from .rules import STAGE_STATS, STATUS_IMMUNE, type_mult

FX_DRAWS = 6          # seeded draws per ready attacker per tick, from stream(seed, "fx", tick)
IDX_PARA = 4          # draw index: full-paralysis check
IDX_PROTECT = 5       # draw index: Protect success
MAX_EFFECTS = 4       # draw indexes 0..3 are the per-effect chance rolls
VALIDATED_STATUSES = {"para"}   # only statuses with tests behind the effect framework
PROTECT_STREAK_CAP = 6          # success chance floors at 1/3**6 = 1/729


class EffectError(ValueError):
    pass


@dataclass(frozen=True)
class Effect:
    kind: str
    when: str = "hit"      # "use": status-move effect | "hit": after a damaging hit that dealt HP damage
    target: str = "self"   # self | target
    stat: str = ""
    delta: int = 0
    chance: float = 1.0
    num: int = 0           # integer fraction num/den (heal: of max HP, recoil: of damage dealt)
    den: int = 1
    status: str = ""


class FxContext:
    __slots__ = ("engine", "t", "att", "dfn", "mv", "dmg", "fx", "index")

    def __init__(self, engine, t, att, dfn, mv, dmg, fx, index):
        self.engine, self.t, self.att, self.dfn, self.mv = engine, t, att, dfn, mv
        self.dmg, self.fx, self.index = dmg, fx, index


def protect_succeeds(streak: int, roll: float) -> bool:
    """Gen VI+ rule: success chance is 1/3 per consecutive successful use (floor 1/729)."""
    return roll * (3 ** min(streak, PROTECT_STREAK_CAP)) < 1.0


# ------------------------------------------------------------------ handlers
def _h_stat_stage(ctx, e):
    tgt = ctx.att if e.target == "self" else ctx.dfn
    if not tgt.alive or ctx.fx[ctx.index] >= e.chance:
        return None
    ctx.engine.change_stage(ctx.t, tgt, e.stat, e.delta, ctx.att.id, ctx.mv.name)
    return None


def _h_status(ctx, e):
    tgt = ctx.att if e.target == "self" else ctx.dfn
    if not tgt.alive or ctx.fx[ctx.index] >= e.chance:
        return None
    if e.when == "use" and type_mult(ctx.mv.type, tgt.types) == 0.0:
        return "immune"                       # e.g. Thunder Wave vs Ground
    if tgt.status is not None:
        return "already_statused"
    if set(tgt.types) & STATUS_IMMUNE[e.status]:
        return "immune_type"                  # e.g. Electric types cannot be paralysed
    if check_status_immunity(tgt, e.status):
        if e.when == "use":
            ctx.engine.emit(ctx.t, "move_blocked", a=ctx.att.id, d=tgt.id, mv=ctx.mv.name, by="immune_ability")
        return "immune_ability"
    if not ctx.engine.try_status(ctx.t, ctx.att.id, tgt, e.status):
        return "status_blocked"
    return None


def _h_heal(ctx, e):
    c = ctx.att
    if not c.alive:
        return None
    if c.hp >= c.max_hp:
        return "full_hp"
    ctx.engine.heal_hp(ctx.t, c, max(1, c.max_hp * e.num // e.den), "move", ctx.mv.name)
    return None


def _h_recoil(ctx, e):
    a = ctx.att
    if ctx.dmg <= 0 or not a.alive:
        return None
    ctx.engine.apply_recoil(ctx.t, a, max(1, ctx.dmg * e.num // e.den), "move", ctx.mv.name)
    return None


def _h_protect(ctx, e):
    a, eng = ctx.att, ctx.engine
    ok = protect_succeeds(a.protect_streak, ctx.fx[IDX_PROTECT])
    if ok:
        a.protected_until = ctx.t + eng.cfg.protect_duration_ticks
        a.protect_streak += 1
    else:
        a.protect_streak = 0
    eng.emit(ctx.t, "protect", id=a.id, ok=ok, streak=a.protect_streak, until=a.protected_until if ok else None)
    return None if ok else "protect_failed"


HANDLERS = {
    "stat_stage": _h_stat_stage,
    "status": _h_status,
    "heal": _h_heal,
    "recoil": _h_recoil,
    "protect": _h_protect,
}


# ------------------------------------------------------------------ validation
def validate_effect(e: Effect, category: str):
    if e.kind not in HANDLERS:
        raise EffectError(f"effect kind {e.kind!r} has no implemented handler")
    if e.when not in ("use", "hit") or e.target not in ("self", "target"):
        raise EffectError(f"bad when/target on {e}")
    if not (0.0 < e.chance <= 1.0):
        raise EffectError(f"chance must be in (0,1]: {e}")
    if e.kind == "stat_stage":
        if e.stat not in STAGE_STATS or e.delta == 0 or abs(e.delta) > 3:
            raise EffectError(f"stat_stage needs stat in {STAGE_STATS} and 0<|delta|<=3: {e}")
    elif e.kind == "status":
        if e.status not in VALIDATED_STATUSES or e.target != "target":
            raise EffectError(f"status effect supports only {sorted(VALIDATED_STATUSES)} on the target: {e}")
    elif e.kind == "heal":
        if e.when != "use" or e.target != "self" or not (0 < e.num <= e.den):
            raise EffectError(f"heal must be a self 'use' effect with 0<num<=den: {e}")
    elif e.kind == "recoil":
        if e.when != "hit" or e.target != "self" or not (0 < e.num <= e.den):
            raise EffectError(f"recoil must be a self 'hit' effect with 0<num<=den: {e}")
    elif e.kind == "protect":
        if e.when != "use" or e.target != "self" or category != "status":
            raise EffectError(f"protect must be a self 'use' effect on a status move: {e}")


def validate_move(mv):
    if mv.category not in ("physical", "special", "status"):
        raise EffectError(f"{mv.name}: unknown category {mv.category!r}")
    if len(mv.effects) > MAX_EFFECTS:
        raise EffectError(f"{mv.name}: at most {MAX_EFFECTS} effects")
    for e in mv.effects:
        validate_effect(e, mv.category)
    if mv.category == "status":
        if mv.power != 0 or not mv.effects or any(e.when != "use" for e in mv.effects):
            raise EffectError(f"{mv.name}: a status move needs power 0 and only 'use' effects")
    elif any(e.when == "use" for e in mv.effects):
        raise EffectError(f"{mv.name}: a damaging move cannot have 'use' effects")
