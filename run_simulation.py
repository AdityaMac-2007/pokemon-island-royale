#!/usr/bin/env python3
"""Pokemon Island Battle Royale - permanent project launcher.

Examples:
    python run_simulation.py
    python run_simulation.py --seed 7 --ruleset chaos
    python run_simulation.py --roster stub --roster out/roster/builds.json
    python run_simulation.py --validate-only
    python run_simulation.py --roster out/roster/builds.json -n 1 --validate-only
    python run_simulation.py optimize --species garchomp

This is glue only. Battle logic stays in ``Claude engine/pkmn_br`` and build
optimisation stays in ``gemini``. This launcher does not implement mechanics.

Exit codes: 0 success; 2 usage/validation error; 3 environment problem;
4 reproducibility check failed.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import platform
import secrets
import sys
import time
from typing import Any

LAUNCHER_VERSION = "1.0.1"
ROOT = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.join(ROOT, "Claude engine")
DEFAULT_OUT = os.path.join(ROOT, "out", "matches")
DEFAULT_ROSTER_OUT = os.path.join(ROOT, "out", "roster", "builds.json")
DEFAULT_AUDIT_OUT = os.path.join(ROOT, "out", "roster", "builds_audit.json")
BUILTIN_SOURCES = ("stub", "synthetic")
RULESETS = ("canonical", "chaos")
TIE_MODES = ("random", "simultaneous")
ENGINE_INLINE_ABILITIES = {"sturdy"}


class LauncherError(Exception):
    """A problem the user can fix; printed without a traceback."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- imports
_ENGINE = None


def _bootstrap_paths() -> None:
    # Insert engine directory before project root so pkmn_br resolves to this engine.
    for path in (ROOT, ENGINE_DIR):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


def _import_engine():
    """Import the real engine package once, with an actionable failure message."""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    if not os.path.isdir(ENGINE_DIR):
        raise LauncherError(
            f"Engine directory not found: {ENGINE_DIR}\n"
            "The project root must contain the folder 'Claude engine'.", code=3
        )
    _bootstrap_paths()
    try:
        from pkmn_br.config import Config
        from pkmn_br.engine import Engine
        from pkmn_br.data import MOVES, get_provider
        from pkmn_br import rules
    except ImportError as exc:
        raise LauncherError(
            f"Could not import 'pkmn_br' from {ENGINE_DIR}: {exc}\n"
            "Check that 'Claude engine/pkmn_br/' exists and use the project's Python environment.",
            code=3,
        ) from exc
    _ENGINE = argparse.Namespace(
        Config=Config, Engine=Engine, MOVES=MOVES, get_provider=get_provider, rules=rules
    )
    return _ENGINE


def _import_gemini():
    _bootstrap_paths()
    try:
        from gemini import adapter, optimizer
    except ImportError as exc:
        raise LauncherError(
            f"Could not import Gemini adapter/optimizer: {exc}\n"
            "The optimize command requires the 'gemini/' folder in the project root.",
            code=3,
        ) from exc
    return adapter, optimizer


# --------------------------------------------------------------------------- roster
class CombinedProvider:
    """Serve the exact roster pool already loaded and validated by the launcher.

    Freezing the pool prevents source providers (especially generated/stub providers)
    from being called a second time with a different config after validation.
    """

    def __init__(self, builds):
        self.builds = list(builds)

    def load_roster(self, cfg):
        # Return a new list but preserve the same validated Build objects and ordering.
        return list(self.builds)


def resolve_source(src: str) -> str:
    """Resolve a source path against cwd first, then the project root."""
    if src in BUILTIN_SOURCES:
        return src
    path = os.path.expanduser(src)
    if os.path.isabs(path):
        return path
    for base in (os.getcwd(), ROOT):
        candidate = os.path.join(base, path)
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
    return os.path.abspath(os.path.join(ROOT, path))


def display_path(path: str) -> str:
    """Show project-relative paths when possible, for portable logs/config digests."""
    try:
        rel = os.path.relpath(path, ROOT)
    except ValueError:
        return path
    return path if rel.startswith("..") else rel.replace(os.sep, "/")


def roster_fingerprint(pool) -> str:
    rows = [dataclasses.asdict(build) for build in pool]
    blob = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_pool(cfg, sources, eng):
    """Load each source once, then provide that same validated pool to the engine."""
    pool = []
    for source in sources:
        source_cfg = dataclasses.replace(cfg, roster_source=source)
        provider = eng.get_provider(source_cfg)
        try:
            pool.extend(provider.load_roster(source_cfg))
        except Exception as exc:
            raise LauncherError(
                f"Failed to load roster from '{display_path(source)}': "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    return CombinedProvider(pool), pool


def validate_pool(pool, eng):
    """Strict structural checks on every build. Return (errors, warnings)."""
    errors, warnings = [], []
    if not pool:
        return ["The roster is empty (0 builds)."], warnings

    legal_types = set(eng.rules.TYPES)
    stat_keys = ("hp", "atk", "def", "spa", "spd", "spe")
    known_abilities = (
        set(eng.rules.PINCH_ABILITY)
        | set(eng.rules.GROUND_IMMUNE_ABILITIES)
        | ENGINE_INLINE_ABILITIES
    )
    known_items = set(eng.rules.ITEMS)
    inert_abilities, inert_items = set(), set()

    for index, build in enumerate(pool):
        species = getattr(build, "species", "?")
        tag = f"build #{index} ({species})"
        if not isinstance(build.species, str) or not build.species.strip():
            errors.append(f"{tag}: missing species name")

        if not (1 <= len(build.types) <= 2) or any(t not in legal_types for t in build.types):
            errors.append(f"{tag}: types must be 1-2 of the engine's 18 types, got {list(build.types)}")

        missing = [key for key in stat_keys if key not in build.stats]
        if missing:
            errors.append(f"{tag}: missing stats {missing}")
        elif any(not isinstance(build.stats[key], int) or build.stats[key] < 1 for key in stat_keys):
            errors.append(f"{tag}: stats must be positive integers, got {build.stats}")

        if not (1 <= len(build.moves) <= 4):
            errors.append(f"{tag}: needs 1-4 moves, has {len(build.moves)}")
        if len(set(build.moves)) != len(build.moves):
            errors.append(f"{tag}: duplicate moves {build.moves}")

        bad_moves = [move for move in build.moves if move not in eng.MOVES]
        if bad_moves:
            errors.append(f"{tag}: moves not in the engine registry: {bad_moves}")
        if build.ability and build.ability not in known_abilities:
            inert_abilities.add(build.ability)
        if build.item and build.item not in known_items:
            inert_items.add(build.item)

    if inert_abilities:
        warnings.append("Abilities with NO engine effect (carried but inert): " + ", ".join(sorted(inert_abilities)))
    if inert_items:
        warnings.append("Items with NO engine effect (carried but inert): " + ", ".join(sorted(inert_items)))
    return errors, warnings


def check_count(n: int, pool_size: int, allow_duplicates: bool):
    """Validate entrant count and explain any duplicates/sampling."""
    if n < 2:
        raise LauncherError(
            f"A battle royale needs at least 2 combatants (got {n}).\n"
            "Add more builds to the roster or pass --count 2 or higher."
        )
    if n > pool_size:
        if not allow_duplicates:
            raise LauncherError(
                f"Requested {n} combatants but the roster only has {pool_size} build(s).\n"
                "The launcher will not silently pad the field with duplicate copies. Options:\n"
                f"  1) lower the count:           --count {pool_size}\n"
                "  2) add more builds:           --roster stub --roster <your builds.json>\n"
                "  3) accept duplicate copies:   --allow-duplicates (mirror match, not a diverse field)"
            )
        dup = n - pool_size
        return [], [
            f"DUPLICATES ENABLED: {n} entrants drawn with replacement from {pool_size} build(s); "
            f"at least {dup} entrant(s) are duplicate copies."
        ]
    if n < pool_size:
        return [f"Sampling {n} of {pool_size} builds (seeded, without replacement)."], []
    return [], []


# --------------------------------------------------------------------------- planning
@dataclasses.dataclass
class Plan:
    cfg: object
    provider: object
    pool: list
    sources: list
    fingerprint: str
    notes: list
    warnings: list
    seed_was_random: bool = False


def _parse_seed(text: str):
    if text.lower() == "random":
        return "random"
    try:
        return int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"seed must be an integer or 'random', got {text!r}") from exc


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an integer, got {text!r}") from exc
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {value}")
    return value


def build_run_parser():
    parser = argparse.ArgumentParser(
        prog="run_simulation.py",
        allow_abbrev=False,
        description="Run one Pokemon Island Battle Royale match using the real engine.",
        epilog="Optional, slow, explicit: python run_simulation.py optimize --help",
    )
    parser.add_argument("--seed", type=_parse_seed, help="integer seed or 'random' (default: 1)")
    parser.add_argument("-n", "--count", type=_positive_int,
                        help="number of combatants; default is the entire loaded roster")
    parser.add_argument("--roster", action="append", metavar="SOURCE",
                        help="'stub', 'synthetic', or builds.json path; repeat to combine sources")
    parser.add_argument("--ruleset", choices=RULESETS, help="canonical (default) or chaos")
    parser.add_argument("--out", help=f"base output directory (default: {display_path(DEFAULT_OUT)})")
    parser.add_argument("--config", metavar="FILE", help="load a Config JSON; passed flags override it")
    parser.add_argument("--allow-duplicates", action="store_true",
                        help="explicitly permit filling the field with duplicate builds")
    parser.add_argument("--max-ticks", type=_positive_int, help="override the configured tick cap")
    parser.add_argument("--speed-tie-mode", choices=TIE_MODES, help="resolve exact speed ties")
    parser.add_argument("--policy", choices=("legacy", "tactical_v1"), default="legacy",
                        help="AI policy for every combatant (default: legacy)")
    parser.add_argument("--no-hit-log", action="store_true", help="omit individual hit events")
    parser.add_argument("--verify-repro", action="store_true", help="run twice and require identical digests")
    parser.add_argument("--validate-only", action="store_true", help="validate inputs without simulating")
    parser.add_argument("--top", type=int, default=10, help="number of ranking rows to print; 0 prints none")
    parser.add_argument("--elims", type=int, default=10, help="recent eliminations to print; 0 prints none")
    parser.add_argument("--full", action="store_true", help="print all rankings and eliminations")
    parser.add_argument("--quiet", action="store_true", help="print only winner, digest, and output path")
    return parser


def prepare_run(args):
    eng = _import_engine()
    try:
        cfg = eng.Config.load(args.config) if args.config else eng.Config()
    except FileNotFoundError as exc:
        raise LauncherError(f"Config file not found: {args.config}") from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise LauncherError(f"Could not read config '{args.config}': {type(exc).__name__}: {exc}") from exc

    seed_was_random = args.seed == "random"
    if seed_was_random:
        cfg.seed = secrets.randbelow(2**31)
    elif args.seed is not None:
        cfg.seed = args.seed
    if args.ruleset:
        cfg.ruleset = args.ruleset
    if args.max_ticks is not None:
        cfg.max_ticks = args.max_ticks
    if args.speed_tie_mode:
        cfg.speed_tie_mode = args.speed_tie_mode
    if args.no_hit_log:
        cfg.log_hits = False

    # Duplicate permission must be explicit on this invocation, never inherited.
    cfg.allow_duplicates = bool(args.allow_duplicates)

    if args.roster:
        raw_sources = args.roster
    elif args.config and str(cfg.roster_source).startswith("multi:"):
        raise LauncherError("This config came from a multi-source launch; pass those sources again with --roster.")
    else:
        raw_sources = [cfg.roster_source]

    sources = [resolve_source(source) for source in raw_sources]
    if "synthetic" in sources and len(sources) > 1:
        raise LauncherError("'synthetic' cannot be combined with other roster sources (its size follows --count).")

    if args.count is not None:
        cfg.n_combatants = args.count
        count_given = True
    elif args.config:
        count_given = True
    else:
        count_given = False
        cfg.n_combatants = 24  # used to size built-in/stub or synthetic providers

    provider, pool = load_pool(cfg, sources, eng)
    if not count_given and "synthetic" not in sources:
        cfg.n_combatants = len(pool)

    errors, warnings = validate_pool(pool, eng)
    if errors:
        shown = "\n".join(f"  - {error}" for error in errors[:12])
        more = f"\n  ... and {len(errors) - 12} more" if len(errors) > 12 else ""
        raise LauncherError(f"Roster validation failed ({len(errors)} problem(s)):\n{shown}{more}")

    notes, count_warnings = check_count(cfg.n_combatants, len(pool), cfg.allow_duplicates)
    shown_sources = [source if source in BUILTIN_SOURCES else display_path(source) for source in sources]
    cfg.roster_source = shown_sources[0] if len(shown_sources) == 1 else "multi:" + "+".join(shown_sources)
    return Plan(
        cfg=cfg,
        provider=provider,
        pool=pool,
        sources=shown_sources,
        fingerprint=roster_fingerprint(pool),
        notes=notes,
        warnings=warnings + count_warnings,
        seed_was_random=seed_was_random,
    )


# --------------------------------------------------------------------------- execution
def run_once(plan, eng, policy="legacy"):
    """Run one match with an explicit AI policy assigned before the first tick."""
    start = time.perf_counter()
    engine = eng.Engine(plan.cfg, plan.provider)
    for combatant in engine.by_id:
        combatant.ai_policy = policy
    result = engine.run()
    return engine, result, time.perf_counter() - start


def output_dir_for(plan, base, policy="legacy"):
    name = f"match_seed{plan.cfg.seed}_{plan.cfg.digest()[:8]}_{plan.fingerprint[:8]}_{policy}"
    return os.path.join(base, name)


def execute(plan, args):
    eng = _import_engine()
    engine, result, elapsed = run_once(plan, eng, args.policy)
    base = os.path.abspath(os.path.expanduser(args.out)) if args.out else DEFAULT_OUT
    if os.path.exists(base) and not os.path.isdir(base):
        raise LauncherError(f"--out path exists and is not a directory: {base}")
    outdir = output_dir_for(plan, base, args.policy)

    try:
        engine.write_outputs(outdir)
        winner = engine.winner
        metadata = {
            "launcher_version": LAUNCHER_VERSION,
            "ai_policy": args.policy,
            "argv": sys.argv[1:],
            "seed": plan.cfg.seed,
            "config_digest": plan.cfg.digest(),
            "roster_sources": plan.sources,
            "roster_fingerprint": plan.fingerprint,
            "entrants": plan.cfg.n_combatants,
            "result_digest": result["digest"],
            "ticks": result["ticks"],
            "winner": {"id": winner.id, "name": winner.name} if winner else None,
            "elapsed_seconds": round(elapsed, 3),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "note": "elapsed_seconds, Python and platform are informational; compare result_digest for reproducibility.",
        }
        with open(os.path.join(outdir, "launch.json"), "w", encoding="utf-8") as file:
            json.dump(metadata, file, indent=2)
    except OSError as exc:
        raise LauncherError(f"Could not write outputs to {outdir}: {exc}") from exc

    reproducible = None
    if args.verify_repro:
        _, second, _ = run_once(plan, eng, args.policy)
        reproducible = second["digest"] == result["digest"]
        if not reproducible:
            print(
                "error: REPRODUCIBILITY FAILURE: digests differ\n"
                f"  run 1: {result['digest']}\n  run 2: {second['digest']}",
                file=sys.stderr,
            )
            raise LauncherError("Reproducibility check failed.", code=4)
    return engine, result, elapsed, outdir, reproducible


# --------------------------------------------------------------------------- reporting
def roster_table(pool, limit=40):
    lines = [f"  {'#':>3}  {'species':<14}{'types':<18}{'HP':>4} {'moves'}"]
    for index, build in enumerate(pool[:limit]):
        lines.append(
            f"  {index:>3}  {build.species:<14}{'/'.join(build.types):<18}"
            f"{build.stats['hp']:>4} {', '.join(build.moves)}"
            + ("  [flies]" if build.can_fly else "")
        )
    if len(pool) > limit:
        lines.append(f"  ... {len(pool) - limit} more")
    return "\n".join(lines)


def print_plan(plan, quiet=False):
    cfg = plan.cfg
    unique_species = len({build.species for build in plan.pool})
    print("=" * 78)
    print("POKEMON ISLAND BATTLE ROYALE")
    print("=" * 78)
    seed_note = f" (random; replay with --seed {cfg.seed})" if plan.seed_was_random else ""
    print(f"seed {cfg.seed}{seed_note} | ruleset {cfg.ruleset} | tie mode {cfg.speed_tie_mode}")
    print(f"roster {', '.join(plan.sources)}: {len(plan.pool)} builds, {unique_species} distinct species | entrants {cfg.n_combatants}")
    for note in plan.notes:
        print(f"note: {note}")
    for warning in plan.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if not quiet:
        print("scope: prototype mechanics subset; this is not the full Pokedex or full canonical rules.")


def print_report(plan, engine, result, elapsed, outdir, reproducible, args):
    winner = engine.winner
    end_event = next((event for event in reversed(engine.log.events) if event.get("e") == "match_end"), {})
    reason = end_event.get("reason", "?")
    print()
    if winner:
        row = engine.explain(winner.id)
        print(
            f"WINNER: #{winner.id} {winner.name} (kills {row['kills']}, damage dealt {row['damage_dealt']}, "
            f"HP left {row['hp_frac_at_end']:.0%})"
        )
    else:
        print("WINNER: none")

    if not args.quiet:
        rows = result["rankings"] if args.full else result["rankings"][:max(0, args.top)]
        if rows:
            label = "all" if args.full else "top"
            print(f"\nRANKINGS ({label} {len(rows)} of {len(result['rankings'])})")
            print(f"  {'pl':>3} {'id':>4}  {'name':<14}{'kills':>5} {'dmg out':>8} {'dmg in':>7} {'env dmg':>8} {'out@tick':>9}")
            for row in rows:
                eliminated = "-" if row["eliminated_tick"] is None else row["eliminated_tick"]
                print(
                    f"  {row['placement']:>3} {row['id']:>4}  {row['name']:<14}{row['kills']:>5} "
                    f"{row['damage_dealt']:>8} {row['damage_taken']:>7} {row['environment_damage']:>8} {eliminated:>9}"
                )
        eliminations = [event for event in engine.log.events if event.get("e") == "elim"]
        shown = eliminations if args.full else (eliminations[-args.elims:] if args.elims > 0 else [])
        if shown:
            label = "all" if args.full else "most recent"
            print(f"\nELIMINATIONS ({label} {len(shown)} of {len(eliminations)}, chronological)")
            for event in shown:
                if event.get("killer") is None:
                    cause = event.get("cause", "unknown cause")
                else:
                    killer = engine.by_id[event["killer"]]
                    cause = f"{killer.name} #{event['killer']} with {event.get('move', 'unknown move')}"
                print(f"  t={event['t']:>5}  #{event['id']:<4} {event['name']:<14} <- {cause}")

    print(f"\nend reason : {reason}")
    print(f"ticks      : {result['ticks']}")
    print(f"elapsed    : {elapsed:.2f}s")
    print(f"config     : {plan.cfg.digest()}")
    print(f"roster     : {plan.fingerprint[:16]}")
    print(f"digest     : {result['digest']}")
    if reproducible is not None:
        print("repro check: PASSED (second run produced the identical digest)")
    print(f"saved to   : {outdir}")


def cmd_run(args):
    plan = prepare_run(args)
    print_plan(plan, quiet=args.quiet)
    if args.validate_only:
        print("\nRoster:")
        print(roster_table(plan.pool))
        print("\nValidation passed. Nothing was simulated or written.")
        return 0
    engine, result, elapsed, outdir, reproducible = execute(plan, args)
    print_report(plan, engine, result, elapsed, outdir, reproducible, args)
    return 0


# --------------------------------------------------------------------------- optimize
def build_optimize_parser():
    parser = argparse.ArgumentParser(
        prog="run_simulation.py optimize",
        allow_abbrev=False,
        description=(
            "EXPLICIT and optional: run Gemini's genetic build optimizer and export a roster via BuildAdapter. "
            "This is never invoked by a normal simulation run."
        ),
    )
    parser.add_argument("--species", required=True, help="comma-separated optimizer reference IDs, e.g. garchomp,dragapult")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--generations", type=_positive_int, default=10)
    parser.add_argument("--pop-size", type=_positive_int, default=8)
    parser.add_argument("--trials", type=_positive_int, default=25)
    parser.add_argument("--roster-out", default=DEFAULT_ROSTER_OUT)
    parser.add_argument("--audit-out", default=DEFAULT_AUDIT_OUT)
    parser.add_argument("--overwrite", action="store_true", help="allow overwriting existing roster/audit files")
    return parser


def cmd_optimize(args):
    eng = _import_engine()
    adapter, optimizer = _import_gemini()
    names = [name.strip().lower() for name in args.species.split(",") if name.strip()]
    if not names:
        raise LauncherError("--species is empty.")
    if len(set(names)) != len(names):
        raise LauncherError(f"Duplicate species in --species: {names}")

    known = sorted(optimizer.REFERENCE_SPECIES)
    unknown = [name for name in names if name not in optimizer.REFERENCE_SPECIES]
    if unknown:
        raise LauncherError(
            f"Unknown species {unknown}. Optimizer reference data currently covers only: {known}. "
            "This is not yet the full imported Pokedex."
        )

    roster_out = os.path.abspath(os.path.expanduser(args.roster_out))
    audit_out = os.path.abspath(os.path.expanduser(args.audit_out))
    for path in (roster_out, audit_out):
        if os.path.exists(path) and not args.overwrite:
            raise LauncherError(f"Refusing to overwrite existing file: {path}\nPass --overwrite to replace it.")

    registry = set(eng.MOVES)
    workload = args.generations * args.pop_size * args.trials
    print(f"Optimizing {names} (seed {args.seed}): up to {workload} evaluation trials per species; this is the slow step.")
    entries = []

    for species_id in names:
        info = optimizer.REFERENCE_SPECIES[species_id]
        usable = [move for move in info["pool"] if adapter.resolve_move_identifier(move, registry) is not None]
        rejected = [move for move in info["pool"] if move not in usable]
        if not usable:
            raise LauncherError(f"{species_id}: none of its moves {info['pool']} map to the engine's move registry.")
        print(f"  {species_id}: usable moves {usable}; unsupported moves excluded {rejected}")
        if len(usable) < 4:
            print(f"  WARNING: {species_id} has only {len(usable)} engine-supported candidate move(s).", file=sys.stderr)

        opt = optimizer.BattleRoyaleBuildOptimizer(
            candidate_species_id=species_id,
            baseline_opponents=optimizer.get_default_baselines(),
            move_pool_override=usable,
            seed=args.seed,
        )
        best, fitness = opt.optimize(
            generations=args.generations,
            pop_size=args.pop_size,
            n_trials=args.trials,
        )
        best["build_id"] = f"opt_{species_id}"
        print(f"  {species_id}: fitness {fitness:.4f} (Gemini simplified simulator, not Claude's island engine)")
        entries.append((best, info["stats"], info["types"], None, fitness, args.trials))

    try:
        _, audits = adapter.BuildAdapter.export_roster(entries, roster_out, audit_out)
    except (ValueError, KeyError, TypeError) as exc:
        raise LauncherError(f"Adapter rejected a build: {type(exc).__name__}: {exc}") from exc

    for audit in audits:
        for warning in audit.unsupported_mechanics_warnings:
            print(f"AUDIT [{audit.species}]: {warning}", file=sys.stderr)
    print(f"Wrote roster: {roster_out}\nWrote audit : {audit_out}")
    print(
        "To simulate with these builds alongside the stub roster, run:\n"
        f"  python run_simulation.py --roster stub --roster {display_path(roster_out)}"
    )
    return 0


# --------------------------------------------------------------------------- entry
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] == "optimize":
            return cmd_optimize(build_optimize_parser().parse_args(argv[1:]))
        return cmd_run(build_run_parser().parse_args(argv))
    except LauncherError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
