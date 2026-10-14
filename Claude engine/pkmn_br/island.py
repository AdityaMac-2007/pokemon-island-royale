"""Procedural island: biomes, resources, weather schedule, telegraphed hazards, shrinking zone.
All generation is a pure function of (seed, config); it never reads combat state."""
from __future__ import annotations
import bisect
import math
from collections import namedtuple

from .rng import stream
from .spatial import SpatialHash

# Each biome: boost for favored-type moves, a movement trade-off, cover (reduces ranged accuracy
# against occupants + their visibility), optional chip hazard, and an alternative benefit.
BIOMES = {
 "plains":     dict(favored=("normal", "fighting"), boost=1.10, move=1.15, cover=0.0,  chip_immune=None, berries=1.0, note="fast but exposed"),
 "forest":     dict(favored=("grass", "bug"),       boost=1.20, move=0.90, cover=0.35, chip_immune=None, berries=1.6, note="cover + food, slow"),
 "volcanic":   dict(favored=("fire", "ground", "rock"), boost=1.20, move=0.95, cover=0.0, chip_immune=("fire", "rock", "ground", "steel"), berries=0.5, note="ember chip on grounded non-resistants; eruptions"),
 "wetland":    dict(favored=("water", "poison"),    boost=1.20, move=0.80, cover=0.15, chip_immune=None, berries=1.0, note="mud slows non-favored"),
 "tundra":     dict(favored=("ice", "dragon"),      boost=1.20, move=0.85, cover=0.0,  chip_immune=("ice", "steel"), berries=0.4, note="frostbite chip on grounded non-resistants"),
 "storm_peak": dict(favored=("electric", "flying", "steel"), boost=1.20, move=0.85, cover=0.0, chip_immune=None, berries=0.5, updraft=True, note="updrafts halve flight drain; lightning strikes"),
 "ruins":      dict(favored=("ghost", "psychic", "dark", "fairy"), boost=1.15, move=1.0, cover=0.40, chip_immune=None, berries=0.8, note="heavy cover, mid food"),
}
BIOME_NAMES = sorted(BIOMES)
CHIP_FRAC = 1 / 16

Zs = namedtuple("ZoneState", "cx cy r dmg safe phase")


class Zone:
    def __init__(self, zc, cx, cy, R, rng):
        self.zc = zc
        self.phases = [(cx, cy, R * zc.phase_fracs[0])]
        for f in zc.phase_fracs[1:]:
            px, py, pr = self.phases[-1]
            nr = R * f
            ang = rng.random() * 2 * math.pi
            off = (pr - nr) * math.sqrt(rng.random())
            self.phases.append((px + off * math.cos(ang), py + off * math.sin(ang), nr))

    def state(self, t: int) -> Zs:
        zc, P, D = self.zc, self.phases, self.zc.damage_pct
        n = len(P)
        cyc = zc.hold_ticks + zc.shrink_ticks
        if t < (n - 1) * cyc:
            k, u = divmod(t, cyc)
            if u < zc.hold_ticks:
                cur, dmg = P[k], D[k]
                safe = P[k + 1] if zc.hold_ticks - u <= zc.lookahead_ticks else P[k]
            else:
                f = (u - zc.hold_ticks) / zc.shrink_ticks
                a, b = P[k], P[k + 1]
                cur = tuple(a[i] + (b[i] - a[i]) * f for i in range(3))
                dmg, safe = D[k + 1], b
            return Zs(cur[0], cur[1], cur[2], dmg, safe, k)
        u = t - (n - 1) * cyc
        f = min(1.0, u / zc.final_shrink_ticks)
        x, y, r = P[-1]
        rr = r * (1 - f) + 1.0 * f
        dmg = D[-1] + (zc.final_damage_pct - D[-1]) * f
        return Zs(x, y, rr, dmg, (x, y, rr), n - 1)


class Island:
    CELL = 4.0

    @classmethod
    def generate(cls, cfg, n):
        self = cls()
        self.size = cfg.resolved_size()
        self.cx = self.cy = self.size / 2
        self.radius = self.size / 2
        rng = stream(cfg.seed, "island")
        # biome map: Voronoi regions around jittered centres; every biome appears at least once
        k = max(len(BIOME_NAMES), int(self.size * self.size / (28 * 28)))
        names = list(BIOME_NAMES)
        rng.shuffle(names)
        centres = []
        for i in range(k):
            a = rng.random() * 2 * math.pi
            d = self.radius * math.sqrt(rng.random())
            centres.append((self.cx + d * math.cos(a), self.cy + d * math.sin(a),
                            names[i] if i < len(names) else BIOME_NAMES[rng.randrange(len(BIOME_NAMES))]))
        self.gw = int(math.ceil(self.size / self.CELL))
        self.grid = []
        for gy in range(self.gw):
            row = []
            for gx in range(self.gw):
                px, py = (gx + .5) * self.CELL, (gy + .5) * self.CELL
                best, bn = 1e18, None
                for (x, y, nm) in centres:
                    d = (x - px) ** 2 + (y - py) ** 2
                    if d < best:
                        best, bn = d, nm
                row.append(bn)
            self.grid.append(row)
        # berries (weighted by biome food richness)
        self.berries = SpatialHash(6.0)
        br = stream(cfg.seed, "berries")
        want, bid, tries = int(cfg.berry_density * n), 0, 0
        while bid < want and tries < want * 40:
            tries += 1
            a = br.random() * 2 * math.pi
            d = self.radius * 0.97 * math.sqrt(br.random())
            x, y = self.cx + d * math.cos(a), self.cy + d * math.sin(a)
            if br.random() < BIOMES[self.biome_at(x, y)]["berries"] / 1.6:
                self.berries.insert(bid, x, y)
                bid += 1
        # weather schedule
        wr = stream(cfg.seed, "weather")
        self.w_starts, self.w_kinds, t = [], [], 0
        while t < cfg.max_ticks + 1000:
            self.w_starts.append(t)
            self.w_kinds.append(wr.choices(["clear", "rain", "sun", "sand", "snow"], [4, 2, 2, 1, 1])[0])
            t += wr.randint(300, 700)
        # telegraphed hazards (lightning on storm_peak, eruptions on volcanic)
        er = stream(cfg.seed, "events")
        mult = cfg.chaos.event_rate_mult if (cfg.ruleset == "chaos" or cfg.chaos.enabled) else 1.0
        interval = max(5, int(cfg.event_interval / mult))
        # scale event count with area so big islands see proportional activity
        per_slot = max(1, int(self.size * self.size / (60 * 60)))
        self.hazards = []
        t = interval
        while t < cfg.max_ticks:
            for _ in range(per_slot):
                kind = "lightning" if er.random() < 0.55 else "eruption"
                want_b = "storm_peak" if kind == "lightning" else "volcanic"
                for _try in range(25):
                    a = er.random() * 2 * math.pi
                    d = self.radius * math.sqrt(er.random())
                    x, y = self.cx + d * math.cos(a), self.cy + d * math.sin(a)
                    if self.biome_at(x, y) == want_b:
                        warn = t
                        self.hazards.append(dict(
                            kind=kind, x=x, y=y, r=3.5 if kind == "lightning" else 5.0,
                            warn=warn, impact=warn + cfg.hazard_warning_ticks,
                            dmg=0.15 if kind == "lightning" else 0.12,
                            immune=("electric", "ground") if kind == "lightning" else ("fire", "rock"),
                            air_immune=(kind == "eruption")))
                        break
            t += max(1, int(er.expovariate(1.0 / interval)))
        self.hazards.sort(key=lambda h: (h["warn"], h["x"]))
        self.zone = Zone(cfg.zone, self.cx, self.cy, self.radius, stream(cfg.seed, "zone"))
        return self

    def biome_at(self, x, y):
        gx = min(self.gw - 1, max(0, int(x / self.CELL)))
        gy = min(self.gw - 1, max(0, int(y / self.CELL)))
        return self.grid[gy][gx]

    def weather_at(self, t):
        return self.w_kinds[bisect.bisect_right(self.w_starts, t) - 1]


def boost_for(biome: str, mtype: str) -> float:
    b = BIOMES[biome]
    return b["boost"] if mtype in b["favored"] else 1.0
