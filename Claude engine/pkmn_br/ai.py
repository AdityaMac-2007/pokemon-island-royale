"""Combat AI: pure, read-only, RNG-free utility scoring. No LLM calls.

Policy: legacy by default; tactical_v1 is explicitly opt-in.

Inputs are the frozen world at the start of the tick; output is an Intent.
Decisions depend on build (damage race estimate), HP, ability/item (via expected damage),
altitude legality, biome cover, zone state, telegraphed hazards and per-Pokemon bravery.
"""
from __future__ import annotations
import math

from .abilities import check_status_immunity
from .island import BIOMES, boost_for
from .rules import ITEMS, PINCH_ABILITY, STATUS_IMMUNE, expected_damage, hit_chance, type_mult
from .state import Intent

TAU = 2 * math.pi


def legal(att, tgt, mv) -> bool:
    """Ground-to-air rule: a grounded attacker needs a move flagged hits_air to touch an airborne target."""
    return not (tgt.alt == 1 and att.alt == 0 and not mv.hits_air)


def reach_of(cfg, mv) -> float:
    return cfg.melee_range if mv.reach == "melee" else cfg.ranged_range


def best_move_legacy(eng, a, d):
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


def p_win_legacy(a, d, da, dd) -> float:
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


def _safe_kite_goal(
    eng, c, opp_x: float, opp_y: float,
    zx: float, zy: float, zr: float,
    dist: float = 4.5, margin: float = 0.5,
) -> tuple[float, float]:
    """Return a deterministic kiting goal that respects the safe circle.

    A combatant outside the effective safe circle prioritizes returning inward.
    Its radial progress is bounded by ``dist`` (or the remaining distance to
    the safe circle edge), avoiding a longer-than-requested jump caused by
    considering/clamping an evasive candidate first.
    """
    safe_r = max(1.0, zr - margin)
    epsilon = 1e-9

    rx, ry = c.x - zx, c.y - zy
    radius = math.hypot(rx, ry)

    # Safety takes priority over kiting once outside the effective circle.
    # Moving radially toward the centre remains inside a circular island when
    # the starting combatant is on the island, so do not clamp this step again.
    if radius > safe_r + epsilon:
        ux, uy = rx / radius, ry / radius
        step = min(max(0.0, dist), radius - safe_r)
        return c.x - ux * step, c.y - uy * step

    def safe_candidate(x: float, y: float):
        px, py = _clamp(eng, x, y)
        if (px - zx) ** 2 + (py - zy) ** 2 <= safe_r * safe_r + epsilon:
            return px, py
        return None

    # Inside the safe circle, first try directly moving away from the opponent.
    dx, dy = c.x - opp_x, c.y - opp_y
    length = math.hypot(dx, dy)
    if length > epsilon:
        ux, uy = dx / length, dy / length
    elif radius > epsilon:
        ux, uy = rx / radius, ry / radius
    else:
        ux, uy = 1.0, 0.0

    candidate = safe_candidate(c.x + ux * dist, c.y + uy * dist)
    if candidate is not None:
        return candidate

    # If straight retreat would leave the safe circle, test two deterministic
    # diagonal choices that blend separation, lateral movement, and inward drift.
    if radius > epsilon:
        ix, iy = -rx / radius, -ry / radius
    else:
        ix, iy = 0.0, 0.0

    best_candidate = None
    best_separation_sq = -1.0
    for sign in (1.0, -1.0):
        px, py = -sign * uy, sign * ux
        bx = 0.35 * ux + 0.40 * px + 0.45 * ix
        by = 0.35 * uy + 0.40 * py + 0.45 * iy
        blend_length = math.hypot(bx, by)
        if blend_length <= epsilon:
            continue
        bx, by = bx / blend_length, by / blend_length
        candidate = safe_candidate(c.x + bx * dist, c.y + by * dist)
        if candidate is None:
            continue
        cx, cy = candidate
        separation_sq = (cx - opp_x) ** 2 + (cy - opp_y) ** 2
        if separation_sq > best_separation_sq:
            best_candidate = candidate
            best_separation_sq = separation_sq

    if best_candidate is not None:
        return best_candidate

    # Cornered: stay put rather than choose a goal outside the safe circle.
    return _clamp(eng, c.x, c.y)


def decide_legacy(eng, c, t) -> Intent:
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
        mi, da = best_move_legacy(eng, c, o)
        _, dd = best_move_legacy(eng, o, c)
        p = p_win_legacy(c, o, da, dd)
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
            air_dmg = sum(best_move_legacy(eng, o, av)[1] for (o, _, _) in threats)
            if air_dmg < 0.5 * ground_dmg:
                want = 1
        if mode == "hunt":
            _, _, o, mi, p, d = tgt
            _, dd_air = best_move_legacy(eng, o, _AirView(c))
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
                        and type_mult(mv.type, o.types) > 0 and not (set(o.types) & STATUS_IMMUNE[e.status])
                        and not check_status_immunity(o, e.status)):
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


# ==============================================================================
# Tactical AI v1: explicitly opt-in; legacy remains the default.
# ==============================================================================

class _GroundView:
    """Read-only proxy representing the same combatant as grounded."""
    def __init__(self, c):
        self._c = c

    def __getattr__(self, k):
        return 0 if k == "alt" else getattr(self._c, k)


def _biome_has_updraft(b: dict) -> bool:
    """Use the active BIOMES schema's canonical field, with legacy-note fallback."""
    if "updraft" in b:
        return bool(b["updraft"])
    return str(b.get("note", "")).startswith("updrafts")


def stamina_drain_rate(c) -> float:
    """Shared source of truth for airborne stamina drain used by AI and engine."""
    return 0.5 if _biome_has_updraft(BIOMES[c.biome]) else 1.0


def _sturdy_intact(d) -> bool:
    return (getattr(d, "ability", "") == "sturdy" and d.hp == d.max_hp
            and not getattr(d, "sturdy_used", False))


def _hits_to_ko(d, dmg: float) -> float:
    """Approximate discrete attacks needed to KO defender d at expected damage dmg."""
    if dmg <= 0:
        return 999.0
    if _sturdy_intact(d) and dmg >= d.hp and d.max_hp > 1:
        return 2.0
    return float(math.ceil(d.hp / dmg))


def best_move_tactical(eng, a, d):
    """Score legal attacks using damage, KOs, accuracy, recoil/drain and stage effects."""
    bi, bs, bd = -1, 0.0, 0.0
    hp_frac = a.hp / max(1, a.max_hp)
    item_recoil = ITEMS.get(getattr(a, "item", "") or "", {}).get("recoil", 0.0)

    for i, mv in enumerate(a.moves):
        if mv.category == "status" or not legal(a, d, mv):
            continue
        cover = BIOMES[d.biome]["cover"] if d.alt == 0 else 0.0
        e = expected_damage(a, d, mv, eng.weather, boost_for(a.biome, mv.type), cover, eng.rules)
        if e <= 0:
            continue

        s = e
        kills = e >= d.hp and not (_sturdy_intact(d) and d.max_hp > 1)
        acc = hit_chance(mv, cover, eng.weather, a, d)
        if kills:
            s = max(s, d.hp * 1.35) * (0.5 + 0.5 * acc)
            if mv.priority > 0:
                s *= 1.5
        elif mv.priority > 0 and hp_frac < 0.30:
            s *= 1.25

        for eff in getattr(mv, "effects", ()):
            if eff.kind == "drain" and a.hp < a.max_hp:
                missing = a.max_hp - a.hp
                heal_val = min(missing, e * getattr(eff, "fraction", 0.5))
                s += heal_val * (1.2 if hp_frac < 0.6 else 0.6)
            elif eff.kind == "recoil" and not kills:
                rec_dmg = e * getattr(eff, "fraction", 0.25)
                if rec_dmg >= a.hp:
                    s *= 0.15
                else:
                    s -= rec_dmg * 0.5
            elif eff.kind == "stat_stage":
                p_chance = getattr(eff, "chance", 1.0)
                if getattr(eff, "target", "") == "self":
                    if eff.delta < 0 and not kills:
                        s *= max(0.5, 1.0 + 0.12 * eff.delta * p_chance)
                    elif eff.delta > 0:
                        s *= 1.0 + 0.10 * eff.delta * p_chance
                elif eff.delta < 0 and not kills:
                    s *= 1.0 + 0.06 * abs(eff.delta) * p_chance

        status = getattr(mv, "status", None)
        if status and d.status is None and not kills:
            if not (set(d.types) & STATUS_IMMUNE.get(status, set())) and not check_status_immunity(d, status):
                s += e * 0.15 * getattr(mv, "status_chance", 0.0)

        if item_recoil and not kills and a.hp <= max(1, int(a.max_hp * item_recoil)):
            s *= 0.5

        if s > bs:
            bi, bs, bd = i, s, e
    return bi, bd


def p_win_tactical(a, d, da, dd, cfg=None, pri_a: int = 0, pri_d: int = 0) -> float:
    """Deterministic time-to-KO score, not a calibrated win probability.

    Estimates attacks-to-KO (including Sturdy), energy head starts and speed. Priority
    and speed receive heuristic adjustments when estimated KO times are within one tick;
    this is a scoring approximation, not an exact replica of attack-phase ordering.
    """
    if da <= 0 and dd <= 0:
        return 0.5
    if da <= 0:
        return 0.02
    if dd <= 0:
        return 0.98

    hits_a = _hits_to_ko(d, da)
    hits_d = _hits_to_ko(a, dd)
    spe_a = max(1, a.eff_speed())
    spe_d = max(1, d.eff_speed())

    if cfg is not None:
        eps = cfg.energy_per_speed
        threshold = cfg.action_threshold
        first_a = max(0.0, threshold - getattr(a, "energy", 0.0)) / max(1e-6, spe_a * eps)
        first_d = max(0.0, threshold - getattr(d, "energy", 0.0)) / max(1e-6, spe_d * eps)
        step_a = threshold / max(1e-6, spe_a * eps)
        step_d = threshold / max(1e-6, spe_d * eps)
        ta = max(0.5, first_a + (hits_a - 1.0) * step_a)
        td = max(0.5, first_d + (hits_d - 1.0) * step_d)
        if abs(ta - td) <= 1.0:
            if pri_a != pri_d:
                if pri_a > pri_d:
                    ta = max(0.25, ta * 0.65)
                else:
                    td = max(0.25, td * 0.65)
            elif spe_a != spe_d:
                if spe_a > spe_d:
                    ta = max(0.25, ta * 0.85)
                else:
                    td = max(0.25, td * 0.85)
    else:
        ta = hits_a / spe_a
        td = hits_d / spe_d

    return td * td / (ta * ta + td * td)


def best_move(eng, a, d):
    """Policy dispatcher; legacy unless the combatant or config opts into tactical_v1."""
    policy = getattr(a, "ai_policy", getattr(eng.cfg, "ai_policy", "legacy"))
    if policy == "tactical_v1":
        return best_move_tactical(eng, a, d)
    return best_move_legacy(eng, a, d)


def p_win(a, d, da, dd, cfg=None, pri_a: int = 0, pri_d: int = 0) -> float:
    """Policy dispatcher; passing cfg alone never opts a legacy combatant into tactical scoring."""
    policy = getattr(a, "ai_policy", getattr(cfg, "ai_policy", "legacy") if cfg is not None else "legacy")
    if policy == "tactical_v1":
        return p_win_tactical(a, d, da, dd, cfg=cfg, pri_a=pri_a, pri_d=pri_d)
    return p_win_legacy(a, d, da, dd)


def _move_speed_est(cfg, c) -> float:
    speed = cfg.base_move * (0.75 + c.stats["spe"] / 800.0)
    if c.alt == 1:
        return speed * cfg.air_speed_mult
    biome = BIOMES[c.biome]
    move_mult = biome["move"]
    if move_mult < 1.0 and (set(c.types) & set(biome["favored"])):
        move_mult = 1.0
    return speed * move_mult


def _takes_biome_chip(c) -> bool:
    if c.alt == 1:
        return False
    chip_immune = BIOMES.get(c.biome, {}).get("chip_immune")
    return chip_immune is not None and not (set(c.types) & set(chip_immune))


def _can_outlast_hazard_in_air(cfg, c, h, t: int) -> bool:
    """Whether flight can avoid an air-immune hazard through its impact tick.

    Impact resolution precedes that tick's stamina drain. A decision at t must therefore
    survive drains at t .. impact-1: max(0, impact-t) drains. Stamina must remain positive
    after all those drains because the engine auto-lands at zero.
    """
    if not (h.get("air_immune", False) and c.can_fly):
        return False
    pre_impact_drains = max(0, int(h.get("impact", t) - t))
    stamina_needed = pre_impact_drains * stamina_drain_rate(c)
    if c.alt == 1:
        return c.stamina > stamina_needed
    return c.stamina >= cfg.takeoff_min and (c.stamina - cfg.takeoff_cost) > stamina_needed


def _horizontal_hazard_evade(eng, c, h, zx: float, zy: float, zr: float, dz: float) -> Intent:
    """Return an immediate horizontal evade, biased inward near the safe-zone edge."""
    cfg = eng.cfg
    intent = Intent()
    dx, dy = c.x - h["x"], c.y - h["y"]
    norm = math.sqrt(dx * dx + dy * dy) or 1.0
    ux, uy = dx / norm, dy / norm
    if dz > zr - 4.0:
        zdx, zdy = zx - c.x, zy - c.y
        zn = math.sqrt(zdx * zdx + zdy * zdy) or 1.0
        ux, uy = ux * 0.65 + zdx / zn * 0.35, uy * 0.65 + zdy / zn * 0.35
        un = math.sqrt(ux * ux + uy * uy) or 1.0
        ux, uy = ux / un, uy / un
    intent.goal = _clamp(eng, c.x + ux * (h["r"] + 3.0), c.y + uy * (h["r"] + 3.0))
    intent.mode, intent.reason = "evade", h["kind"]
    if c.can_fly:
        if c.alt == 1:
            # Keep air speed this movement phase; natural exhaustion is handled by the engine.
            intent.alt = 1 if c.stamina > 0.0 else 0
        elif (not h.get("air_immune", False)
              and c.stamina >= cfg.takeoff_min + cfg.takeoff_cost + 2.0):
            intent.alt = 1
        else:
            intent.alt = 0
    else:
        intent.alt = c.alt
    return intent


def decide(eng, c, t) -> Intent:
    """Engine entry point. Defaults to legacy to preserve the established baseline."""
    policy = getattr(c, "ai_policy", getattr(eng.cfg, "ai_policy", "legacy"))
    if policy == "tactical_v1":
        return _decide_tactical_v1(eng, c, t)
    return decide_legacy(eng, c, t)


def _decide_tactical_v1(eng, c, t) -> Intent:
    from .rng import hash01
    cfg = eng.cfg
    intent = Intent()
    hp_frac = c.hp / max(1, c.max_hp)
    zx, zy, zr = eng.zone.safe
    dz = _dist(c.x, c.y, zx, zy)
    outside = dz > zr - cfg.zone_margin
    deep_outside = dz > zr + 0.5
    vis = cfg.vision_radius * (1.25 if c.alt == 1 else 1.0)

    # Hazards are evaluated before combat; ground-projection detects air-immune threats.
    force_air_evade = False
    for hazard in eng.active_hazards:
        ground_affected = eng.hazard_affects(hazard, _GroundView(c), margin=1.5)
        current_affected = eng.hazard_affects(hazard, c, margin=1.5)
        if ground_affected or current_affected:
            if _can_outlast_hazard_in_air(cfg, c, hazard, t):
                force_air_evade = True
                continue
            return _horizontal_hazard_evade(eng, c, hazard, zx, zy, zr, dz)

    # Perception and combat evaluation.
    enemies = []
    for oid in eng.index.query(c.x, c.y, vis):
        if oid == c.id:
            continue
        opponent = eng.by_id[oid]
        if not opponent.alive:
            continue
        cover = BIOMES[opponent.biome]["cover"] if opponent.alt == 0 else 0.0
        if _dist(c.x, c.y, opponent.x, opponent.y) <= vis * (1.0 - cover):
            enemies.append(opponent)

    can_consider_air = c.can_fly and (c.alt == 1 or c.stamina >= cfg.takeoff_min)
    air_self = _AirView(c) if can_consider_air else None
    my_speed = _move_speed_est(cfg, c)
    scored, threats = [], []
    attacker_count = 0
    for opponent in enemies:
        distance = _dist(c.x, c.y, opponent.x, opponent.y)
        mi, da = best_move_tactical(eng, c, opponent)
        opp_mi, dd = best_move_tactical(eng, opponent, c)
        pri_a = c.moves[mi].priority if mi >= 0 else 0
        pri_d = opponent.moves[opp_mi].priority if opp_mi >= 0 else 0
        win_score = p_win_tactical(c, opponent, da, dd, cfg=cfg, pri_a=pri_a, pri_d=pri_d)

        if air_self is not None and c.alt == 0:
            air_mi, air_da = best_move_tactical(eng, air_self, opponent)
            opp_air_mi, air_dd = best_move_tactical(eng, opponent, air_self)
            pri_air_a = c.moves[air_mi].priority if air_mi >= 0 else 0
            pri_air_d = opponent.moves[opp_air_mi].priority if opp_air_mi >= 0 else 0
            air_score = p_win_tactical(air_self, opponent, air_da, air_dd,
                                       cfg=cfg, pri_a=pri_air_a, pri_d=pri_air_d)
            if air_mi >= 0 and (mi < 0 or air_score > win_score + 0.08 or force_air_evade):
                mi, da, dd, win_score = air_mi, air_da, air_dd, air_score

        if dd > 0:
            threats.append((opponent, dd, distance))
            if opponent.target == c.id and distance <= cfg.ranged_range + 3.5:
                attacker_count += 1

        if mi >= 0:
            move = c.moves[mi]
            hits_ko = _hits_to_ko(opponent, da)
            ko_efficiency = 1.0 / max(1.0, hits_ko)
            score = (win_score + 0.35 * ko_efficiency
                     + 0.15 * (1.0 - opponent.hp / max(1, opponent.max_hp))
                     - 0.25 * (distance / vis)
                     + (0.10 if opponent.target == c.id else 0.0))
            if (move.reach == "melee" and distance > cfg.melee_range + 1.0
                    and _move_speed_est(cfg, opponent) > my_speed and opponent.target != c.id):
                score -= 0.22
            if _dist(opponent.x, opponent.y, zx, zy) > zr:
                score -= 0.25
            scored.append((score, -opponent.id, opponent, mi, win_score, distance, da))

    scored.sort(reverse=True)
    best = scored[0] if scored else None
    pinch_active = (best is not None and c.hp * 3 <= c.max_hp
                    and PINCH_ABILITY.get(c.ability) == c.moves[best[3]].type)
    low_hp_penalty = 0.05 if pinch_active else (0.20 if hp_frac < 0.35 else 0.0)
    swarm_penalty = 0.12 * max(0, attacker_count - 1)
    flee_threshold = 0.35 - c.bravery + low_hp_penalty + swarm_penalty

    mode, reason, target = "wander", "", None
    if best and best[4] >= flee_threshold:
        mode, reason, target = "hunt", f"p={best[4]:.2f}", best
    elif threats and (best is None or best[4] < flee_threshold):
        mode, reason = "flee", "outmatched" if best else "cannot_hit"

    if outside and mode != "flee":
        keep_hunt = (not deep_outside and mode == "hunt" and target is not None
                     and target[5] <= reach_of(cfg, c.moves[target[3]]) + 1.5
                     and _hits_to_ko(target[2], target[6]) <= 1.0)
        if not keep_hunt:
            mode, reason = "zone", "outside_safe_zone"
    elif outside and mode == "flee":
        reason = "outmatched+outside"

    # Never forage while the altitude lock is protecting against a ground-only hazard.
    berry = None
    want_berry = not force_air_evade and (
        (mode in ("wander", "zone") and hp_frac < 0.85 and (c.alt == 0 or hp_frac < 0.65))
        or (mode == "flee" and hp_frac < 0.60)
    )
    if want_berry:
        berry_ids = eng.island.berries.query(c.x, c.y, vis)
        if berry_ids:
            valid = []
            for berry_id in berry_ids:
                bx, by, _ = eng.island.berries.pos[berry_id]
                if outside and _dist(bx, by, zx, zy) >= zr - cfg.zone_margin:
                    continue
                berry_dist = _dist(c.x, c.y, bx, by)
                if mode == "flee" and (
                    berry_dist > 5.0
                    or any(_dist(bx, by, enemy.x, enemy.y) < td for enemy, _, td in threats)
                ):
                    continue
                valid.append((berry_dist, bx, by))
            if valid:
                valid.sort()
                _, bx, by = valid[0]
                berry = (bx, by)
                if mode == "wander":
                    mode, reason = "forage", "hurt"
    intent.mode, intent.reason = mode, reason

    # Goals and positioning.
    if mode == "hunt":
        _, _, opponent, mi, win_score, distance, da = target
        move = c.moves[mi]
        intent.target, intent.p_win = opponent.id, win_score
        opponent_mi, opponent_damage = best_move_tactical(eng, opponent, c)
        opponent_melee_threat = (opponent_mi >= 0
                                 and opponent.moves[opponent_mi].reach == "melee"
                                 and opponent_damage > 0)
        if move.reach == "ranged" and opponent_melee_threat and distance < cfg.melee_range + 2.2:
            intent.goal = _safe_kite_goal(
                eng, c, opponent.x, opponent.y, zx, zy, zr, dist=4.5,
            )
        else:
            preferred = cfg.ranged_range * 0.85 if move.reach == "ranged" else cfg.melee_range * 0.8
            if distance > preferred:
                intent.goal = (opponent.x, opponent.y)
    elif mode == "flee":
        if berry is not None:
            intent.goal = berry
        else:
            vx = vy = 0.0
            for opponent, damage, distance in threats:
                dx, dy = c.x - opponent.x, c.y - opponent.y
                norm = math.sqrt(dx * dx + dy * dy) or 1.0
                focus_mult = 1.75 if opponent.target == c.id else 1.0
                weight = damage * focus_mult / (distance + 1.0)
                vx += dx / norm * weight
                vy += dy / norm * weight
            norm = math.sqrt(vx * vx + vy * vy) or 1.0
            pull = 0.65 if outside else 0.20
            zdx, zdy = zx - c.x, zy - c.y
            zn = math.sqrt(zdx * zdx + zdy * zdy) or 1.0
            vx, vy = vx / norm + pull * zdx / zn, vy / norm + pull * zdy / zn
            norm = math.sqrt(vx * vx + vy * vy) or 1.0
            intent.goal = _clamp(eng, c.x + vx / norm * 12, c.y + vy / norm * 12)
    elif mode == "forage":
        intent.goal = berry
    elif mode == "zone":
        if berry is not None and not deep_outside:
            intent.goal = berry
        else:
            zdx, zdy = zx - c.x, zy - c.y
            zn = math.sqrt(zdx * zdx + zdy * zdy) or 1.0
            ux, uy = zdx / zn, zdy / zn
            for opponent, _, distance in threats:
                if distance < 9.0:
                    dx, dy = c.x - opponent.x, c.y - opponent.y
                    norm = math.sqrt(dx * dx + dy * dy) or 1.0
                    ux += dx / norm * 0.35
                    uy += dy / norm * 0.35
            norm = math.sqrt(ux * ux + uy * uy) or 1.0
            step_dist = min(dz, max(6.0, dz - (zr - cfg.zone_margin - 2.5)))
            intent.goal = _clamp(eng, c.x + ux / norm * step_dist, c.y + uy / norm * step_dist)
    else:
        ang = hash01(cfg.seed, c.id, t // 40) * TAU
        wx, wy = math.cos(ang), math.sin(ang)
        zdx, zdy = zx - c.x, zy - c.y
        zn = math.sqrt(zdx * zdx + zdy * zdy) or 1.0
        bias = 0.65 if dz > zr * 0.6 or _takes_biome_chip(c) else 0.15
        vx, vy = wx + bias * zdx / zn, wy + bias * zdy / zn
        norm = math.sqrt(vx * vx + vy * vy) or 1.0
        intent.goal = _clamp(eng, c.x + vx / norm * 10, c.y + vy / norm * 10)

    # Opportunistic attacks prioritize guaranteed approximate KOs, current target, then damage ratio.
    effective_self = _AirView(c) if force_air_evade else c
    attack = None
    for score, _, opponent, mi, win_score, distance, damage in scored:
        move = c.moves[mi]
        if not legal(effective_self, opponent, move):
            continue
        if distance <= reach_of(cfg, move) + 0.1:
            can_ko = _hits_to_ko(opponent, damage) <= 1.0
            if mode == "flee" and not can_ko and win_score < flee_threshold and hp_frac > 0.2:
                continue
            key = (can_ko, opponent.id == intent.target, damage / max(1, opponent.hp), -opponent.id)
            if attack is None or key > attack[0]:
                attack = (key, opponent.id, mi, distance)
    if attack:
        intent.attack = (attack[1], attack[2])
        if intent.goal is not None and mode == "hunt" and attack[1] == intent.target:
            _, _, opponent, mi, _, distance, _ = target
            opponent_mi, opponent_damage = best_move_tactical(eng, opponent, c)
            kiting = (c.moves[mi].reach == "ranged" and opponent_mi >= 0
                      and opponent.moves[opponent_mi].reach == "melee"
                      and opponent_damage > 0 and distance < cfg.melee_range + 2.2)
            if not kiting:
                intent.goal = None

    if c.utility:
        utility = _utility_choice_tactical(eng, c, t, mode, target, threats, hp_frac, outside)
        if utility is not None:
            intent.attack = utility

    # Altitude and stamina management. A hazard lock cannot be overwritten by forage/wander logic.
    if c.can_fly:
        want = 0
        min_stamina = 15 if c.alt == 1 else cfg.takeoff_min
        if force_air_evade:
            want = 1
        else:
            if _takes_biome_chip(c) and c.stamina > min_stamina:
                want = 1
            if mode == "flee" and c.stamina > min_stamina:
                air_view = _AirView(c)
                ground_damage = sum(damage for _, damage, _ in threats)
                air_damage = sum(best_move_tactical(eng, enemy, air_view)[1]
                                 for enemy, _, _ in threats)
                if (air_damage < 0.7 * ground_damage or BIOMES[c.biome]["move"] < 1.0
                        or attacker_count >= 2):
                    want = 1
            elif mode == "hunt" and target is not None:
                _, _, opponent, mi, _, distance, _ = target
                move = c.moves[mi]
                _, ground_damage = best_move_tactical(eng, opponent, c)
                _, air_damage = best_move_tactical(eng, opponent, _AirView(c))
                needs_air_to_hit = opponent.alt == 1 and not legal(_GroundView(c), opponent, move)
                air_reduces_damage = air_damage < 0.75 * ground_damage
                closing_gap = distance > reach_of(cfg, move) + 1.5 and c.stamina > 55
                if (needs_air_to_hit or air_reduces_damage or closing_gap) and c.stamina > min_stamina:
                    want = 1
                if c.alt == 1 and opponent.alt == 0 and move.reach == "melee" and distance <= cfg.melee_range + 0.5:
                    want = 0
            elif mode == "zone" and deep_outside and c.stamina > max(45.0, min_stamina):
                want = 1
            if mode in ("wander", "forage") or (
                berry is not None and intent.goal == berry
                and _dist(c.x, c.y, berry[0], berry[1]) <= 3.0
            ):
                want = 0
        intent.alt = want
    return intent


def _utility_choice_tactical(eng, c, t, mode, target, threats, hp_frac, outside=False):
    """Tactical status-move policy: protect, heal, debuff, then safe self-boost."""
    cfg = eng.cfg
    has_leftovers = getattr(c, "item", "") == "leftovers"
    near_residual = (t % cfg.residual_interval) >= (cfg.residual_interval - cfg.protect_duration_ticks)

    for mi in c.utility:
        move = c.moves[mi]
        if move.is_protect and c.protect_streak < 2:
            hp_gate = 0.90 if has_leftovers and near_residual else 0.75
            if hp_frac < hp_gate:
                for opponent, _, distance in threats:
                    reach = max((reach_of(cfg, m) for m in opponent.moves if m.category != "status"), default=0.0) + 1.0
                    soon = opponent.energy + opponent.eff_speed() * cfg.energy_per_speed * cfg.protect_duration_ticks
                    dot_stall = opponent.status in ("burn", "poison") and near_residual
                    if (opponent.target == c.id or dot_stall) and distance <= reach and soon >= cfg.action_threshold:
                        return (c.id, mi)
    for mi in c.utility:
        if hp_frac < 0.55 and any(e.kind == "heal" for e in c.moves[mi].effects):
            return (c.id, mi)

    if mode == "hunt" and target is not None:
        opponent, p, distance, damage = target[2], target[4], target[5], target[6]
        if _hits_to_ko(opponent, damage) > 1.0:
            for mi in c.utility:
                move = c.moves[mi]
                if move.self_targeted or distance > reach_of(cfg, move) + 0.1 or not legal(c, opponent, move):
                    continue
                for effect in move.effects:
                    if effect.kind == "stat_stage" and effect.delta < 0 and opponent.stages[effect.stat] > -2:
                        return (opponent.id, mi)
                    if (effect.kind == "status" and opponent.status is None and p < 0.9
                            and type_mult(move.type, opponent.types) > 0
                            and not (set(opponent.types) & STATUS_IMMUNE.get(effect.status, set()))
                            and not check_status_immunity(opponent, effect.status)):
                        return (opponent.id, mi)

    if mode in ("hunt", "wander") and not outside and hp_frac >= 0.55:
        calm = not any(distance <= cfg.ranged_range + 2.5 for _, _, distance in threats)
        if calm:
            for mi in c.utility:
                move = c.moves[mi]
                if any(e.kind == "stat_stage" and e.target == "self" and e.delta > 0
                       and c.stages[e.stat] < 2 for e in move.effects):
                    return (c.id, mi)
    return None
