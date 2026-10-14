"""Combat AI: pure, read-only, RNG-free utility scoring. No LLM calls.

Inputs are the frozen world at the start of the tick; output is an Intent.
Decisions depend on build (damage race estimate), HP, ability/item (via expected damage),
altitude legality, biome cover, zone state, telegraphed hazards and per-Pokemon bravery.
"""
from __future__ import annotations
import math

from .island import BIOMES, boost_for
from .rules import STATUS_IMMUNE, expected_damage, type_mult
from .state import Intent

TAU = 2 * math.pi


def legal(att, tgt, mv) -> bool:
    """Ground-to-air rule: a grounded attacker needs a move flagged hits_air to touch an airborne target."""
    return not (tgt.alt == 1 and att.alt == 0 and not mv.hits_air)


def reach_of(cfg, mv) -> float:
    return cfg.melee_range if mv.reach == "melee" else cfg.ranged_range


def best_move(eng, a, d):
    """Return (index, expected_damage) of a's best legal move vs d; (-1, 0) if none."""
    bi, bs, bd = -1, 0.0, 0.0
    for i, mv in enumerate(a.moves):
        if mv.category == "status" or not legal(a, d, mv):
            continue
        cover = BIOMES[d.biome]["cover"] if d.alt == 0 else 0.0
        e = expected_damage(a, d, mv, eng.weather, boost_for(a.biome, mv.type), cover, eng.rules)
        s = e * (1.5 if (mv.priority > 0 and e >= d.hp) else 1.0)   # finisher bonus for priority
        if s > bs:
            bi, bs, bd = i, s, e
    return bi, bd


def p_win(a, d, da, dd) -> float:
    """Estimated win probability from a time-to-kill race. Closed form (no exp/log => platform-stable):
    p = td^2 / (ta^2 + td^2), i.e. logistic(2*ln(td/ta))."""
    if da <= 0 and dd <= 0:
        return 0.5
    if da <= 0:
        return 0.02
    if dd <= 0:
        return 0.98
    ta = d.hp / da / max(1, a.eff_speed())
    td = a.hp / dd / max(1, d.eff_speed())
    return td * td / (ta * ta + td * td)


def _dist(ax, ay, bx, by):
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2)


def _toward(c, x, y):
    return (x, y)


def _away(c, ox, oy, eng, dist=12.0):
    dx, dy = c.x - ox, c.y - oy
    n = math.sqrt(dx * dx + dy * dy) or 1.0
    return _clamp(eng, c.x + dx / n * dist, c.y + dy / n * dist)


def _clamp(eng, x, y):
    isl = eng.island
    dx, dy = x - isl.cx, y - isl.cy
    n = math.sqrt(dx * dx + dy * dy)
    lim = isl.radius - 0.5
    if n > lim:
        return (isl.cx + dx / n * lim, isl.cy + dy / n * lim)
    return (x, y)


def decide(eng, c, t) -> Intent:
    from .rng import hash01
    cfg = eng.cfg
    I = Intent()
    hp_frac = c.hp / c.max_hp
    zx, zy, zr = eng.zone.safe
    dz = _dist(c.x, c.y, zx, zy)
    outside = dz > zr - cfg.zone_margin
    vis = cfg.vision_radius * (1.25 if c.alt == 1 else 1.0)

    # 1. telegraphed hazards (highest priority)
    for h in eng.active_hazards:
        if eng.hazard_affects(h, c, margin=1.5):
            I.goal = _away(c, h["x"], h["y"], eng, h["r"] + 3.0)
            I.mode, I.reason = "evade", h["kind"]
            I.alt = c.alt
            return I

    # 2. perception
    enemies = []
    for oid in eng.index.query(c.x, c.y, vis):
        if oid == c.id:
            continue
        o = eng.by_id[oid]
        if not o.alive:
            continue
        cover = BIOMES[o.biome]["cover"] if o.alt == 0 else 0.0
        if _dist(c.x, c.y, o.x, o.y) <= vis * (1.0 - cover):
            enemies.append(o)

    scored, threats = [], []
    for o in enemies:
        d = _dist(c.x, c.y, o.x, o.y)
        mi, da = best_move(eng, c, o)
        _, dd = best_move(eng, o, c)
        p = p_win(c, o, da, dd)
        if dd > 0:
            threats.append((o, dd, d))
        if mi >= 0:
            s = p + 0.35 * (1 - o.hp / o.max_hp) - 0.25 * d / vis + (0.1 if o.target == c.id else 0.0)
            scored.append((s, -o.id, o, mi, p, d))
    scored.sort(reverse=True)
    best = scored[0] if scored else None

    flee_thr = 0.35 - c.bravery + (0.20 if hp_frac < 0.35 else 0.0)

    # 3. mode selection
    mode, reason = "wander", ""
    tgt = None
    if best and best[4] >= flee_thr:
        mode, reason, tgt = "hunt", f"p={best[4]:.2f}", best
    elif threats and (best is None or best[4] < flee_thr):
        mode, reason = "flee", "outmatched" if best else "cannot_hit"
    if outside and mode != "flee":
        mode, reason = "zone", "outside_safe_zone"
    elif outside and mode == "flee":
        reason = "outmatched+outside"
    # forage if hurt and calm
    berry = None
    if mode in ("wander", "zone") and hp_frac < 0.9 and c.alt == 0:
        ids = eng.island.berries.query(c.x, c.y, vis)
        if ids:
            bid = min(ids, key=lambda b: (eng.island.berries.pos[b][0] - c.x) ** 2 + (eng.island.berries.pos[b][1] - c.y) ** 2)
            bx, by, _ = eng.island.berries.pos[bid]
            if mode == "wander" or _dist(bx, by, zx, zy) < zr:
                berry = (bx, by)
                if mode == "wander":
                    mode, reason = "forage", "hurt"
    I.mode, I.reason = mode, reason

    # 4. goals
    if mode == "hunt":
        _, _, o, mi, p, d = tgt
        mv = c.moves[mi]
        pref = (cfg.ranged_range * 0.85) if mv.reach == "ranged" else cfg.melee_range * 0.8
        I.target, I.p_win = o.id, p
        if d > pref:
            I.goal = (o.x, o.y)
    elif mode == "flee":
        vx = vy = 0.0
        for (o, dd, d) in threats:
            dx, dy = c.x - o.x, c.y - o.y
            n = math.sqrt(dx * dx + dy * dy) or 1.0
            w = dd / (d + 1.0)
            vx += dx / n * w
            vy += dy / n * w
        n = math.sqrt(vx * vx + vy * vy) or 1.0
        pull = 0.5 if outside else 0.15
        zdx, zdy = zx - c.x, zy - c.y
        zn = math.sqrt(zdx * zdx + zdy * zdy) or 1.0
        vx, vy = vx / n + pull * zdx / zn, vy / n + pull * zdy / zn
        n = math.sqrt(vx * vx + vy * vy) or 1.0
        I.goal = _clamp(eng, c.x + vx / n * 12, c.y + vy / n * 12)
    elif mode == "forage":
        I.goal = berry
    elif mode == "zone":
        I.goal = (zx, zy)
    else:  # wander: drift with a slowly changing heading, biased toward the safe circle
        ang = hash01(eng.cfg.seed, c.id, t // 40) * TAU
        wx, wy = math.cos(ang), math.sin(ang)
        zdx, zdy = zx - c.x, zy - c.y
        zn = math.sqrt(zdx * zdx + zdy * zdy) or 1.0
        bias = 0.6 if dz > zr * 0.6 else 0.15
        vx, vy = wx + bias * zdx / zn, wy + bias * zdy / zn
        n = math.sqrt(vx * vx + vy * vy) or 1.0
        I.goal = _clamp(eng, c.x + vx / n * 10, c.y + vy / n * 10)

    # 5. opportunistic attack: best in-range legal target (primary target preferred)
    atk = None
    for (s, _nid, o, mi, p, d) in scored:
        mv = c.moves[mi]
        if d <= reach_of(cfg, mv) + 0.1:
            if mode == "flee" and p < flee_thr and hp_frac > 0.2:
                continue
            key = (o.id == I.target, -o.hp)
            if atk is None or key > atk[0]:
                atk = (key, o.id, mi)
    if atk:
        I.attack = (atk[1], atk[2])
        if I.goal is not None and mode == "hunt":
            I.goal = None      # in range: hold position and trade blows

    if c.utility:
        u = _utility_choice(eng, c, mode, tgt, threats, hp_frac)
        if u is not None:
            I.attack = u

    # 6. altitude
    if c.can_fly:
        want = 0
        if c.alt == 1:
            want = 1 if c.stamina > 15 else 0
        if mode == "flee" and c.stamina > cfg.takeoff_min:
            # escape upward only if it actually cuts incoming damage (threats lacking anti-air stop hitting us)
            av = _AirView(c)
            ground_dmg = sum(dd for (_, dd, _) in threats)
            air_dmg = sum(best_move(eng, o, av)[1] for (o, _, _) in threats)
            if air_dmg < 0.5 * ground_dmg:
                want = 1
        if mode == "hunt":
            _, _, o, mi, p, d = tgt
            _, dd_air = best_move(eng, o, _AirView(c))
            if (o.alt == 1 or dd_air <= 0) and c.stamina > (15 if c.alt else cfg.takeoff_min):
                want = 1
            elif c.alt == 1 and o.alt == 0 and c.moves[mi].reach == "melee":
                want = 0
        if mode in ("wander", "forage") and c.alt == 1:
            want = 0
        I.alt = want
    return I


def _utility_choice(eng, c, mode, tgt, threats, hp_frac):
    """Small deterministic policy for status moves. Only runs for combatants that own one.
    Priority: Protect (incoming hit imminent) > heal (hurt) > debuff/paralyse (target in reach) > self-boost (safe)."""
    cfg = eng.cfg
    for mi in c.utility:                                          # 1. Protect
        mv = c.moves[mi]
        if mv.is_protect and c.protect_streak < 2 and hp_frac < 0.75:
            for (o, dd, d) in threats:
                reach = max((reach_of(cfg, m) for m in o.moves if m.category != "status"), default=0.0) + 1.0
                soon = o.energy + o.eff_speed() * cfg.energy_per_speed * cfg.protect_duration_ticks
                if o.target == c.id and d <= reach and soon >= cfg.action_threshold:
                    return (c.id, mi)
    for mi in c.utility:                                          # 2. heal
        if hp_frac < 0.5 and any(e.kind == "heal" for e in c.moves[mi].effects):
            return (c.id, mi)
    if mode == "hunt" and tgt is not None:                        # 3. debuff / paralyse the hunted target
        _, _, o, _, p, d = tgt
        for mi in c.utility:
            mv = c.moves[mi]
            if mv.self_targeted or d > reach_of(cfg, mv) + 0.1 or not legal(c, o, mv):
                continue
            for e in mv.effects:
                if e.kind == "stat_stage" and e.delta < 0 and o.stages[e.stat] > -2:
                    return (o.id, mi)
                if (e.kind == "status" and o.status is None and p < 0.9
                        and type_mult(mv.type, o.types) > 0 and not (set(o.types) & STATUS_IMMUNE[e.status])):
                    return (o.id, mi)
    if mode in ("hunt", "wander"):                                # 4. self-boost while nobody can hit us soon
        calm = not any(d <= cfg.ranged_range + 3.0 for (_, _, d) in threats)
        if calm:
            for mi in c.utility:
                mv = c.moves[mi]
                if any(e.kind == "stat_stage" and e.target == "self" and e.delta > 0 and c.stages[e.stat] < 2
                       for e in mv.effects):
                    return (c.id, mi)
    return None


class _AirView:
    """Read-only proxy: 'what if c were airborne' for legality/damage estimates."""
    def __init__(self, c):
        self._c = c

    def __getattr__(self, k):
        return 1 if k == "alt" else getattr(self._c, k)
