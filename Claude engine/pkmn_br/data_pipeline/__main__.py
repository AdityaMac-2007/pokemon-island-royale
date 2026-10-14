import argparse
import json
import sys
from pathlib import Path

from . import source
from .build import BuildError, run_build
from .engine_compat import EngineError
from .legality import check_file
from .ruleset import DEFAULT_PATH, POLICY_CLASSES, Ruleset

ROOT = source.ROOT


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m data_pipeline")
    ap.add_argument("--root", default=str(ROOT), help="project root (default: parent of data_pipeline/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pin", help="[network] resolve a ref to a commit SHA and write source.lock.json")
    p.add_argument("--repo", default=source.DEFAULT_REPO)
    p.add_argument("--ref", default="master")
    p.add_argument("--commit", help="pin this exact 40-char SHA instead of resolving a ref")
    p = sub.add_parser("fetch", help="[network] fetch the pinned commit and export data/*.ts to JSON")
    p.add_argument("--node-modules", help="directory containing sucrase (default: data_pipeline/node/node_modules)")
    p.add_argument("--no-npm", action="store_true")
    p = sub.add_parser("build", help="[offline] build data/gen9 from the cache")
    p.add_argument("--out", default="data/gen9")
    p.add_argument("--engine-dir", default="Claude engine")
    p.add_argument("--overrides", default="data/overrides")
    p.add_argument("--ruleset", default=str(DEFAULT_PATH))
    p.add_argument("--form-policy", choices=sorted(POLICY_CLASSES))
    p.add_argument("--allow-errors", action="store_true")
    p.add_argument("--no-engine", action="store_true", help="skip engine-compatible artifacts")
    p = sub.add_parser("check", help="validate builds (JSON list/object with species, nature, ability, item, moves, evs, ivs)")
    p.add_argument("file")
    p.add_argument("--data", default="data/gen9")
    p.add_argument("--ruleset", default=str(DEFAULT_PATH))
    a = ap.parse_args(argv)
    root = Path(a.root)
    try:
        if a.cmd == "pin":
            lock = source.pin(a.repo, a.ref, a.commit)
            print(f"pinned {lock['repo']} @ {lock['commit']}")
        elif a.cmd == "fetch":
            rec = source.fetch(root, source.read_lock(), node_modules=a.node_modules, run_npm=not a.no_npm)
            print(f"fetched {rec['commit']} and exported JSON")
        elif a.cmd == "build":
            rs = Ruleset.load(a.ruleset)
            if a.form_policy:
                rs = rs.with_form_policy(a.form_policy)
            m = run_build(root, root / a.out, root / a.engine_dir, rs, root / a.overrides,
                          allow_errors=a.allow_errors, no_engine=a.no_engine)
            print(json.dumps(m["counts"], indent=1))
            print(f"wrote {a.out}/ (see COVERAGE.md, validation_report.json, manifest.json)")
        elif a.cmd == "check":
            res = check_file(a.file, root / a.data, Ruleset.load(a.ruleset))
            bad = 0
            for r in res:
                for v in r["violations"]:
                    bad += 1
                    print(f"{r['species_id']}: {v['code']}: {v['detail']}")
                for w in r["warnings"]:
                    print(f"{r['species_id']}: {w['code']}: {w['detail']}")
            print(f"{len(res)} build(s), {bad} violation(s)")
            return 1 if bad else 0
    except (source.SourceError, BuildError, EngineError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
