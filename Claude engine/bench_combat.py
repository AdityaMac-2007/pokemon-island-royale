"""Combat v1 benchmark.  python bench_combat.py [--n 24 240 840] [--repeats 3] [--baseline PATH_TO_PRE_CHANGE_ENGINE]

Each measurement runs in a fresh subprocess. Rows:
  new/legacy-roster   this engine, original moves only (compare with baseline for overhead)
  new/combat-roster   this engine, roster that also draws the Combat v1 status/effect moves
  baseline/legacy     the pre-change engine directory you pass with --baseline (optional)
"""
import argparse, json, os, statistics, subprocess, sys

CHILD = r'''
import sys, time, json
sys.path.insert(0, sys.argv[1])
combat, n, seed = sys.argv[2] == "1", int(sys.argv[3]), int(sys.argv[4])
from pkmn_br.config import Config
from pkmn_br.engine import Engine
cfg = Config(seed=seed, n_combatants=n, roster_source="synthetic", log_hits=False, replay_every=25, max_ticks=6000)
prov = None
if combat:
    from pkmn_br.data import SyntheticProvider
    prov = SyntheticProvider(combat_moves=True)
t0 = time.perf_counter(); e = Engine(cfg, prov) if prov else Engine(cfg); t1 = time.perf_counter()
ms = []
while not e.finished:
    s = time.perf_counter(); e.step(); ms.append((time.perf_counter() - s) * 1000)
tot = time.perf_counter() - t1
ms.sort()
print(json.dumps({"setup": t1 - t0, "run": tot, "ticks": e.tick, "median_ms": ms[len(ms)//2],
                  "p99_ms": ms[int(len(ms)*.99)], "events": len(e.log.events),
                  "winner": e.winner.name if e.winner else None}))
'''


def measure(engine_dir, combat, n, seed, repeats):
    runs = []
    for _ in range(repeats):
        out = subprocess.run([sys.executable, "-c", CHILD, engine_dir, "1" if combat else "0", str(n), str(seed)],
                             capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1]
        runs.append(json.loads(out))
    pick = sorted(runs, key=lambda r: r["run"])[len(runs) // 2]       # median run by wall time
    pick["run_min"] = min(r["run"] for r in runs)
    return pick


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, nargs="+", default=[24, 240, 840])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--baseline")
    a = ap.parse_args()
    here = os.path.dirname(os.path.abspath(__file__))
    rows = [("new/legacy-roster", here, False), ("new/combat-roster", here, True)]
    if a.baseline:
        rows.append(("baseline/legacy", os.path.abspath(a.baseline), False))
    print(f"seed={a.seed} repeats={a.repeats} (median run shown; log_hits=False; synthetic rosters)")
    print(f"{'N':>4}  {'config':<18}{'setup s':>8}{'run s':>8}{'ticks':>7}{'ms/tick med':>12}{'p99 ms':>8}{'events':>8}  winner")
    for n in a.n:
        for label, d, combat in rows:
            r = measure(d, combat, n, a.seed, a.repeats)
            print(f"{n:>4}  {label:<18}{r['setup']:>8.2f}{r['run']:>8.2f}{r['ticks']:>7}{r['median_ms']:>12.3f}"
                  f"{r['p99_ms']:>8.1f}{r['events']:>8}  {r['winner']}")


if __name__ == "__main__":
    main()
