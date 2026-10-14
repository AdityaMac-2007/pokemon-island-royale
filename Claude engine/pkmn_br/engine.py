"""Simulation engine: tick loop + deterministic event resolution (see docs/SPEC.md)."""
from __future__ import annotations
import json
import math
import os

from . import ai
from .config import Config
from .effects import FX_DRAWS, HANDLERS, IDX_PARA, FxContext
from .data import select_roster
from .island import BIOMES, CHIP_FRAC, Island, boost_for
from .log import EventLog
from .rng import hash01, stream
from .rules import BattleRules, STATUS_IMMUNE, ITEMS, calc_damage, hit_chance
from .spatial import SpatialHash
from .state import Combatant

ROLL_COUNT = 4


class Engine:
    def __init__(self, cfg: Config, provider=None):
        self.cfg = cfg
        self.rules = BattleRules.from_config(cfg)
        self.log = EventLog(cfg)
        builds = select_roster(cfg, provider)
        self.island = Island.generate(cfg, len(builds))
        self.by_id = []
        self.index = SpatialHash(cfg.vision_radius)
        self.tick = 0
        self.weather = "clear"
        self.zone = self.island.zone.state(0)
        self.active_hazards = []
        self._haz_i = 0
        self.finished = False
        self.winner = None
        self._casualties = []
        self._spawn(builds)
        self.alive_count = len(self.by_id)
        self.log.emit(0, "match_start", config=cfg.digest(), n=len(self.by_id), size=self.island.size,
                      roster=[{"id": c.id, "name": c.name, "types": list(c.types), "hp": c.max_hp, "fly": c.can_fly}
                              for c in self.by_id])

    # ------------------------------------------------------------------ setup
    def _spawn(self, builds):
        cfg, isl = self.cfg, self.island
        rng = stream(cfg.seed, "spawn")
        grid = SpatialHash(4.0)
        for i, b in enumerate(builds):
            c = Combatant.from_build(i, b)
            for _ in range(40):
                a = rng.random() * 2 * math.pi
                d = isl.radius * 0.92 * math.sqrt(rng.random())
                x, y = isl.cx + d * math.cos(a), isl.cy + d * math.sin(a)
                if not grid.query(x, y, 2.5):
                    break
            grid.insert(i, x, y)
            c.x, c.y = x, y
            c.biome = isl.biome_at(x, y)
            c.stamina = cfg.stamina_max
            c.bravery = (hash01(cfg.seed, i, 7) - 0.4) * 0.25       # in [-0.10, +0.15]
            c.energy = hash01(cfg.seed, i, 11) * cfg.action_threshold * 0.5
            self.by_id.append(c)

    def living(self):
        return [c for c in self.by_id if c.alive]

    def hazard_affects(self, h, c, margin=0.0) -> bool:
        if set(c.types) & set(h["immune"]):
            return False
        if h["air_immune"] and c.alt == 1:
            return False
        return (c.x - h["x"]) ** 2 + (c.y - h["y"]) ** 2 <= (h["r"] + margin) ** 2

    # ------------------------------------------------------------------ event + state helpers (Combat v1)
    def emit(self, t, ev, **kw):
        """Combat-detail event, gated by cfg.log_hits (eliminations and statuses are always logged)."""
        if self.cfg.log_hits:
            self.log.emit(t, ev, **kw)

    def change_stage(self, t, c, stat, delta, src, mv):
        cur = c.stages[stat]
        new = max(-6, min(6, cur + delta))
        c.stages[stat] = new
        self.emit(t, "stat_change", id=c.id, stat=stat, requested=delta, applied=new - cur, stage=new, src=src, mv=mv)
        return new - cur

    def heal_hp(self, t, c, amount, src, mv=""):
        applied = max(0, min(amount, c.max_hp - c.hp))
        c.hp += applied
        if applied:
            self.emit(t, "heal", id=c.id, amount=applied, hp_after=c.hp, src=src, mv=mv)
        return applied

    def try_status(self, t, src_id, d, status):
        if d.status is not None or (set(d.types) & STATUS_IMMUNE[status]):
            return False
        d.status = status
        self.log.emit(t, "status", id=d.id, status=status, src=src_id)
        return True

    def apply_recoil(self, t, a, amount, src, mv):
        a.hp -= amount
        a.env_dmg += amount
        self.emit(t, "recoil", id=a.id, amount=amount, hp_after=max(0, a.hp), src=src, mv=mv)
        if a.hp <= 0:
            self._eliminate(t, a, None, mv, "recoil")

    def _run_effects(self, t, a, d, mv, when, dealt, fx):
        for i, e in enumerate(mv.effects):
            if e.when != when:
                continue
            reason = HANDLERS[e.kind](FxContext(self, t, a, d, mv, dealt, fx, i), e)
            if reason and when == "use":
                self.emit(t, "move_failed", a=a.id, mv=mv.name, reason=reason)

    # ------------------------------------------------------------------ main loop
    def run(self):
        while not self.finished:
            self.step()
        return self.result()

    def step(self):
        t = self.tick
        rng = stream(self.cfg.seed, "tick", t)
        self._phase_world(t)          # 1. zone, weather, hazard telegraphs
        self._phase_index()           # 2. spatial snapshot
        self._phase_decide(t)         # 3. read-only decisions
        self._phase_move(t)           # 4. altitude + movement + collision
        self._phase_attacks(t, rng)   # 5. ordered attack resolution
        self._phase_environment(t)    # 6. zone/hazard/residual/pickups/energy
        self._phase_finalize(t)       # 7. placements, win check, replay frame
        self.tick += 1

    # ---- phase 1
    def _phase_world(self, t):
        self.zone = self.island.zone.state(t)
        w = self.island.weather_at(t)
        if w != self.weather:
            self.weather = w
            self.log.emit(t, "weather", weather=w)
        self.active_hazards = [h for h in self.active_hazards if h["impact"] >= t]
        hz = self.island.hazards
        while self._haz_i < len(hz) and hz[self._haz_i]["warn"] <= t:
            h = hz[self._haz_i]
            self._haz_i += 1
            self.active_hazards.append(h)
            self.log.emit(t, "hazard_warning", kind=h["kind"], x=round(h["x"], 1), y=round(h["y"], 1), r=h["r"], impact=h["impact"])
        for c in self.by_id:
            if c.alive:
                c.biome = self.island.biome_at(c.x, c.y)

    # ---- phase 2
    def _phase_index(self):
        self.index.clear()
        for c in self.by_id:
            if c.alive:
                self.index.insert(c.id, c.x, c.y)

    # ---- phase 3
    def _phase_decide(self, t):
        k = self.cfg.ai_interval
        fresh = []
        for c in self.by_id:
            if not c.alive:
                continue
            stale = c.intent is None or (t + c.id) % k == 0 or \
                (c.intent.target is not None and not self.by_id[c.intent.target].alive)
            if stale:
                fresh.append((c, ai.decide(self, c, t)))
        # commit after all decisions so evaluation order cannot matter
        for c, I in fresh:
            c.intent = I
            c.target = I.target
            c.last_p_win = I.p_win if I.p_win is not None else c.last_p_win
            if I.mode != c.mode:
                self.log.emit(t, "ai_mode", id=c.id, frm=c.mode, to=I.mode, why=I.reason,
                              hp=round(c.hp / c.max_hp, 3), target=I.target)
                c.mode = I.mode

    # ---- phase 4
    def _speed(self, c):
        cfg = self.cfg
        s = cfg.base_move * (0.75 + c.stats["spe"] / 800.0)
        if c.alt == 1:
            return s * cfg.air_speed_mult
        b = BIOMES[c.biome]
        m = b["move"]
        if m < 1.0 and (set(c.types) & set(b["favored"])):
            m = 1.0
        return s * m

    def _phase_move(self, t):
        cfg, isl = self.cfg, self.island
        for c in self.by_id:
            if not c.alive or c.intent is None:
                continue
            I = c.intent
            if I.alt == 1 and c.alt == 0 and c.can_fly and c.stamina >= cfg.takeoff_min:
                c.alt = 1
                c.stamina -= cfg.takeoff_cost
                self.log.emit(t, "takeoff", id=c.id)
            elif I.alt == 0 and c.alt == 1:
                c.alt = 0
                self.log.emit(t, "land", id=c.id, why="intent")
            if I.goal is not None:
                dx, dy = I.goal[0] - c.x, I.goal[1] - c.y
                d = math.sqrt(dx * dx + dy * dy)
                if d > 1e-9:
                    step = min(self._speed(c), d)
                    c.x += dx / d * step
                    c.y += dy / d * step
            self._clamp_to_island(c)
        self._separate()
        for c in self.by_id:
            if c.alive:
                c.biome = isl.biome_at(c.x, c.y)

    def _clamp_to_island(self, c):
        isl = self.island
        dx, dy = c.x - isl.cx, c.y - isl.cy
        n = math.sqrt(dx * dx + dy * dy)
        lim = isl.radius - 0.3
        if n > lim:
            c.x, c.y = isl.cx + dx / n * lim, isl.cy + dy / n * lim

    def _separate(self):
        """Single deterministic pass pushing overlapping same-layer bodies apart."""
        r = self.cfg.body_radius * 2
        for layer in (0, 1):
            idx = SpatialHash(max(r, 1.0))
            ids = [c.id for c in self.by_id if c.alive and c.alt == layer]
            for i in ids:
                idx.insert(i, self.by_id[i].x, self.by_id[i].y)
            for i in ids:
                a = self.by_id[i]
                for j in idx.query(a.x, a.y, r):
                    if j <= i:
                        continue
                    b = self.by_id[j]
                    dx, dy = b.x - a.x, b.y - a.y
                    d = math.sqrt(dx * dx + dy * dy)
                    if d < 1e-6:
                        ang = hash01(self.cfg.seed, i, j) * 2 * math.pi
                        dx, dy, d = math.cos(ang), math.sin(ang), 1.0
                    push = (r - d) / 2
                    a.x -= dx / d * push; a.y -= dy / d * push
                    b.x += dx / d * push; b.y += dy / d * push
                    self._clamp_to_island(a); self._clamp_to_island(b)

    # ---- phase 5
    def _phase_attacks(self, t, rng):
        cfg = self.cfg
        ready = [c for c in self.by_id if c.alive and c.intent and c.intent.attack and c.energy >= cfg.action_threshold]
        if not ready:
            return
        # fixed draw count per attacker, in id order -> RNG use independent of resolution order
        rolls, tie = {}, {}
        for c in ready:
            rolls[c.id] = tuple(rng.random() for _ in range(ROLL_COUNT))
            tie[c.id] = rng.random()
        # Combat v1 randomness comes from its OWN seeded stream so legacy draws are untouched
        fxr = stream(cfg.seed, "fx", t)
        fxd = {c.id: tuple(fxr.random() for _ in range(FX_DRAWS)) for c in ready}
        def key(c):
            mv = c.moves[c.intent.attack[1]]
            return (-mv.priority, -c.eff_speed(), tie[c.id] if cfg.speed_tie_mode == "random" else 0.0, c.id)
        ready.sort(key=key)
        # brackets: contiguous runs with identical (priority, speed); size>1 only matters in 'simultaneous' mode
        brackets, cur, ck = [], [], None
        for c in ready:
            k = (c.moves[c.intent.attack[1]].priority, c.eff_speed())
            if cfg.speed_tie_mode == "simultaneous" and k == ck:
                cur.append(c)
            else:
                if cur:
                    brackets.append(cur)
                cur, ck = [c], k
        if cur:
            brackets.append(cur)
        for br in brackets:
            deferred = [] if len(br) > 1 else None
            alive_at_start = [c for c in br if c.alive]
            for a in alive_at_start:
                if deferred is None and not a.alive:
                    continue
                self._do_attack(t, a, rolls[a.id], fxd[a.id], deferred)
            if deferred:
                for hit in deferred:
                    self._apply_hit(t, *hit)

    def _do_attack(self, t, a, rolls, fx, deferred):
        cfg = self.cfg
        tid, mi = a.intent.attack
        d = self.by_id[tid]
        mv = a.moves[mi]
        if not mv.self_targeted:
            if not d.alive:
                self.log.emit(t, "action_lost", id=a.id, why="target_down")
                return
            dist = math.sqrt((a.x - d.x) ** 2 + (a.y - d.y) ** 2)
            reach = (cfg.melee_range if mv.reach == "melee" else cfg.ranged_range) + cfg.attack_slack
            if dist > reach:
                return                                  # out of range: energy kept, retry
            if not ai.legal(a, d, mv):
                return                                  # target went airborne: cannot be hit
        if a.status == "para" and cfg.para_skip_chance > 0 and fx[IDX_PARA] < cfg.para_skip_chance:
            a.energy -= cfg.action_threshold
            self.emit(t, "move_failed", a=a.id, mv=mv.name, reason="fully_paralyzed")
            return
        a.energy -= cfg.action_threshold
        if not mv.is_protect:
            a.protect_streak = 0
        if mv.category == "status":
            self._use_status_move(t, a, d, mv, rolls, fx)
            return
        cover = BIOMES[d.biome]["cover"] if d.alt == 0 else 0.0
        dmg, info = calc_damage(a, d, mv, rolls, self.weather, boost_for(a.biome, mv.type), cover, self.rules)
        if mv.reach == "melee" and a.alt == 1 and d.alt == 0:
            a.alt = 0
            self.log.emit(t, "land", id=a.id, why="dive_attack")
        self.emit(t, "move_use", a=a.id, d=d.id, mv=mv.name, kind="damage")
        if d.protected_until > t:
            self.emit(t, "move_blocked", a=a.id, d=d.id, mv=mv.name, by="protect")
            return
        hit = (a, d, mv, dmg, info, rolls, fx)
        if deferred is None:
            self._apply_hit(t, *hit)
        else:
            deferred.append(hit)

    def _use_status_move(self, t, a, d, mv, rolls, fx):
        """Status moves resolve immediately (also inside a 'simultaneous' bracket; see docs)."""
        if mv.self_targeted:
            d = a
            self.emit(t, "move_use", a=a.id, d=a.id, mv=mv.name, kind="self")
        else:
            self.emit(t, "move_use", a=a.id, d=d.id, mv=mv.name, kind="status")
            if d.protected_until > t:
                self.emit(t, "move_blocked", a=a.id, d=d.id, mv=mv.name, by="protect")
                return
            cover = BIOMES[d.biome]["cover"] if d.alt == 0 else 0.0
            if rolls[0] >= hit_chance(mv, cover):
                self.emit(t, "move_miss", a=a.id, d=d.id, mv=mv.name, reason="accuracy")
                return
        self._run_effects(t, a, d, mv, "use", 0, fx)

    def _apply_hit(self, t, a, d, mv, dmg, info, rolls, fx):
        if not d.alive:
            return
        hp_before = d.hp
        if dmg > 0 and d.ability == "sturdy" and d.hp == d.max_hp and dmg >= d.hp and not d.sturdy_used:
            dmg, d.sturdy_used = d.hp - 1, True
            info["sturdy"] = True
        if dmg > 0:
            d.hp -= dmg
            d.dmg_taken += dmg
            a.dmg_dealt += dmg
            d.recent.append({"t": t, "src": a.id, "move": mv.name, "dmg": dmg})
            del d.recent[:-5]
            if (mv.status and d.status is None and rolls[3] < mv.status_chance
                    and not (set(d.types) & STATUS_IMMUNE[mv.status])):
                d.status = mv.status
                self.log.emit(t, "status", id=d.id, status=mv.status, src=a.id)
        if self.cfg.log_hits:
            self.log.emit(t, "hit", a=a.id, d=d.id, mv=mv.name, dmg=dmg, hp=max(0, d.hp), **{k: v for k, v in info.items() if v})
        dealt = min(dmg, hp_before) if dmg > 0 else 0
        if dmg > 0:
            extra = {"sturdy": True} if info.get("sturdy") else {}
            self.emit(t, "damage", src=a.id, d=d.id, mv=mv.name, amount=dealt, raw=dmg, hp_after=max(0, d.hp),
                      crit=bool(info.get("crit")), eff=info.get("eff"), **extra)
        elif info.get("miss"):
            self.emit(t, "move_miss", a=a.id, d=d.id, mv=mv.name, reason="accuracy")
        elif info.get("immune"):
            self.emit(t, "move_miss", a=a.id, d=d.id, mv=mv.name, reason="immune")
        if d.hp <= 0:
            self._eliminate(t, d, a, mv.name, "move")
        if dealt > 0 and mv.effects:
            self._run_effects(t, a, d, mv, "hit", dealt, fx)
        recoil = ITEMS.get(a.item or "", {}).get("recoil")
        if recoil and dmg > 0 and a.alive:
            self.apply_recoil(t, a, max(1, int(a.max_hp * recoil)), "item", a.item)

    def _eliminate(self, t, c, killer, move, cause):
        if not c.alive:
            return
        c.alive = False
        c.hp = 0
        c.alt = 0
        c.elim_tick = t
        if killer is not None:
            killer.kills += 1
        self._casualties.append(c)
        self.log.emit(t, "elim", id=c.id, name=c.name, cause=cause, killer=killer.id if killer else None,
                      move=move, last_hits=list(c.recent), mode=c.mode, last_p_win=c.last_p_win,
                      at=[round(c.x, 1), round(c.y, 1)], biome=c.biome)

    # ---- phase 6
    def _phase_environment(self, t):
        cfg, z = self.cfg, self.zone
        # 6a zone damage
        for c in self.by_id:
            if not c.alive:
                continue
            if (c.x - z.cx) ** 2 + (c.y - z.cy) ** 2 > z.r * z.r:
                d = max(1, math.ceil(c.max_hp * z.dmg))
                c.hp -= d; c.env_dmg += d
                self.emit(t, "env_damage", id=c.id, src="zone", amount=d, hp_after=max(0, c.hp))
                if c.hp <= 0:
                    self._eliminate(t, c, None, "zone", "zone")
        # 6b hazard impacts
        for h in self.active_hazards:
            if h["impact"] == t:
                self.log.emit(t, "hazard_impact", kind=h["kind"], x=round(h["x"], 1), y=round(h["y"], 1))
                for c in self.by_id:
                    if c.alive and self.hazard_affects(h, c):
                        d = max(1, int(c.max_hp * h["dmg"]))
                        c.hp -= d; c.env_dmg += d
                        self.emit(t, "env_damage", id=c.id, src=h["kind"], amount=d, hp_after=max(0, c.hp))
                        if c.hp <= 0:
                            self._eliminate(t, c, None, h["kind"], "hazard")
        # 6c residual step: status, weather chip, biome chip, held-item heal
        if t % cfg.residual_interval == 0:
            for c in self.by_id:
                if not c.alive:
                    continue
                dmg, why = 0, None
                if c.status in ("burn", "poison"):
                    dmg += max(1, c.max_hp // 16); why = c.status
                if self.weather == "sand" and not (set(c.types) & {"rock", "ground", "steel"}):
                    dmg += max(1, c.max_hp // 16); why = why or "sandstorm"
                if self.weather == "snow" and "ice" not in c.types:
                    dmg += max(1, c.max_hp // 32); why = why or "snow"
                b = BIOMES[c.biome]
                if b["chip_immune"] is not None and c.alt == 0 and not (set(c.types) & set(b["chip_immune"])):
                    dmg += max(1, int(c.max_hp * CHIP_FRAC)); why = why or f"{c.biome}_chip"
                heal = ITEMS.get(c.item or "", {}).get("heal")
                if heal and c.hp < c.max_hp:
                    self.heal_hp(t, c, max(1, int(c.max_hp * heal)), "item", c.item)
                if dmg:
                    c.hp -= dmg; c.env_dmg += dmg
                    self.emit(t, "env_damage", id=c.id, src=why, amount=dmg, hp_after=max(0, c.hp))
                    if c.hp <= 0:
                        self._eliminate(t, c, None, why, "status" if why in ("burn", "poison") else "environment")
        # 6d pickups (grounded only)
        for c in self.by_id:
            if c.alive and c.alt == 0 and c.hp < c.max_hp:
                ids = self.island.berries.query(c.x, c.y, cfg.pickup_radius)
                if ids:
                    self.island.berries.remove(ids[0])
                    self.heal_hp(t, c, int(c.max_hp * cfg.berry_heal_frac), "berry", "berry")
                    c.berries += 1
                    self.log.emit(t, "berry", id=c.id)
        # 6e energy + stamina
        for c in self.by_id:
            if not c.alive:
                continue
            c.energy = min(cfg.energy_cap, c.energy + c.eff_speed() * cfg.energy_per_speed)
            if c.alt == 1:
                c.flight_ticks += 1
                drain = 0.5 if BIOMES[c.biome].get("updraft", False) else 1.0
                c.stamina -= drain
                if c.stamina <= 0:
                    c.alt = 0
                    c.stamina = 0.0
                    self.log.emit(t, "land", id=c.id, why="exhausted")
            else:
                c.stamina = min(cfg.stamina_max, c.stamina + 2.0)

    # ---- phase 7
    def _phase_finalize(self, t):
        cas = self._casualties
        if cas:
            before = self.alive_count
            surv = before - len(cas)
            # same-tick eliminations: better placement = more damage dealt, then kills, then lower id
            cas.sort(key=lambda c: (-c.dmg_dealt, -c.kills, c.id))
            for i, c in enumerate(cas):
                c.placement = surv + 1 + i
                self.log.emit(t, "placement", id=c.id, placement=c.placement, tiebreak=len(cas) > 1)
            self.alive_count = surv
            self._casualties = []
        if t % self.cfg.replay_every == 0:
            self.log.frame(t, self.zone, self.weather, self.by_id)
        if self.alive_count <= 1:
            self._finish(t, "last_standing")
        elif t + 1 >= self.cfg.max_ticks:
            self._finish(t, "timeout")

    def _finish(self, t, reason):
        alive = self.living()
        if reason == "timeout" and alive:
            alive.sort(key=lambda c: (-c.hp / c.max_hp, -c.dmg_dealt, c.id))
            for i, c in enumerate(alive):
                c.placement = i + 1
            self.alive_count = 0
        elif alive:
            alive[0].placement = 1
        winner = next((c for c in self.by_id if c.placement == 1), None)
        self.winner = winner
        self.finished = True
        self.log.emit(t, "match_end", reason=reason, winner=winner.id if winner else None,
                      explanation=self.explain(winner.id) if winner else None)

    # ------------------------------------------------------------------ outputs
    def explain(self, cid):
        c = self.by_id[cid]
        out = {"id": c.id, "name": c.name, "placement": c.placement, "kills": c.kills,
               "damage_dealt": c.dmg_dealt, "damage_taken": c.dmg_taken, "environment_damage": c.env_dmg,
               "berries": c.berries, "flight_ticks": c.flight_ticks,
               "hp_frac_at_end": round(c.hp / c.max_hp, 3), "eliminated_tick": c.elim_tick}
        el = next((e for e in self.log.events if e["e"] == "elim" and e["id"] == cid), None)
        if el:
            out["eliminated_by"] = {"cause": el["cause"], "killer": el["killer"], "move": el["move"], "last_hits": el["last_hits"]}
        return out

    def rankings(self):
        rows = [self.explain(c.id) for c in self.by_id]
        rows.sort(key=lambda r: r["placement"])
        return rows

    def result(self):
        return {"seed": self.cfg.seed, "config": self.cfg.digest(), "ticks": self.tick,
                "winner": self.winner.id if self.winner else None, "digest": self.log.digest(),
                "rankings": self.rankings()}

    def write_outputs(self, outdir):
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "config.json"), "w") as f:
            json.dump(self.cfg.to_dict(), f, indent=2)
        self.log.write_jsonl(os.path.join(outdir, "events.jsonl"))
        res = self.result()
        with open(os.path.join(outdir, "summary.json"), "w") as f:
            json.dump(res, f, indent=1)
        with open(os.path.join(outdir, "rankings.csv"), "w") as f:
            f.write("placement,id,name,kills,damage_dealt,damage_taken,environment_damage,berries,flight_ticks,eliminated_tick\n")
            for r in res["rankings"]:
                f.write(f'{r["placement"]},{r["id"]},{r["name"]},{r["kills"]},{r["damage_dealt"]},{r["damage_taken"]},'
                        f'{r["environment_damage"]},{r["berries"]},{r["flight_ticks"]},{r["eliminated_tick"]}\n')
        from .capabilities import engine_capabilities
        from .feed import combat_feed
        with open(os.path.join(outdir, "capabilities.json"), "w") as f:
            json.dump(engine_capabilities(), f, indent=1)
        with open(os.path.join(outdir, "combat_feed.json"), "w") as f:
            json.dump(combat_feed(self.log.events, self.cfg.replay_every), f, separators=(",", ":"))
        isl = self.island
        with open(os.path.join(outdir, "replay.json"), "w") as f:
            json.dump({"meta": {"seed": self.cfg.seed, "size": isl.size, "cell": isl.CELL, "biome_grid": isl.grid,
                                "zone_phases": isl.zone.phases, "replay_every": self.cfg.replay_every,
                                "roster": [{"id": c.id, "name": c.name, "types": list(c.types)} for c in self.by_id]},
                       "frames": self.log.frames}, f, separators=(",", ":"))
        return res
