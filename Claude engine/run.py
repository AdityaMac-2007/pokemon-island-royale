import argparse, json, time
from pkmn_br.config import Config
from pkmn_br.engine import Engine

ap = argparse.ArgumentParser()
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("-n", type=int, default=24)
ap.add_argument("--source", default="stub")
ap.add_argument("--ruleset", default="canonical")
ap.add_argument("--config")
ap.add_argument("--out", default="out")
a = ap.parse_args()
cfg = Config.load(a.config) if a.config else Config(seed=a.seed, n_combatants=a.n, roster_source=a.source, ruleset=a.ruleset)
t0 = time.time()
e = Engine(cfg)
res = e.run()
e.write_outputs(f"{a.out}/match_{cfg.seed}")
w = e.winner
print(f"seed={cfg.seed} n={cfg.n_combatants} ticks={res['ticks']} time={time.time()-t0:.2f}s digest={res['digest'][:12]}")
print("winner:", w.name if w else None, json.dumps(e.explain(w.id)) if w else "")
for r in res["rankings"][:5]:
    print(r["placement"], r["name"], "kills", r["kills"], "dmg", r["damage_dealt"])
