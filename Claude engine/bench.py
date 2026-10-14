"""Performance benchmark: python bench.py [N ...]"""
import sys, time
from pkmn_br.config import Config
from pkmn_br.engine import Engine

for n in [int(a) for a in sys.argv[1:]] or [100, 500, 1000]:
    cfg = Config(seed=1, n_combatants=n, roster_source="synthetic", log_hits=False, replay_every=25, max_ticks=4000)
    t0 = time.time(); e = Engine(cfg); t1 = time.time()
    ticks_ms = []
    while not e.finished:
        s = time.time(); e.step(); ticks_ms.append((time.time() - s) * 1000)
    tot = time.time() - t1
    ticks_ms.sort()
    print(f"N={n:5d} setup={t1-t0:5.2f}s run={tot:6.2f}s ticks={e.tick:5d} "
          f"median_tick={ticks_ms[len(ticks_ms)//2]:6.2f}ms p99={ticks_ms[int(len(ticks_ms)*.99)]:6.2f}ms "
          f"events={len(e.log.events)} winner={e.winner.name if e.winner else None}")
