#!/usr/bin/env python3
"""
Integration Runner: Runs BattleRoyaleBuildOptimizer on Garchomp's filtered candidate pool,
demonstrates reproducible optimization, exports to Claude-compatible JSON, and validates via JsonProvider.
File: gemini/integrate.py
"""
import json
import os
import random
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CLAUDE_DIR = os.path.join(_ROOT, "Claude engine")
if _CLAUDE_DIR not in sys.path:
    sys.path.insert(0, _CLAUDE_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Fail loudly if Claude's real package cannot be imported
try:
    from pkmn_br.data import MOVES, JsonProvider  # type: ignore
    from pkmn_br.config import Config  # type: ignore
except ImportError as exc:
    print(f"\n[FATAL ERROR] Claude engine package failed to import: {exc}", file=sys.stderr)
    print("Ensure 'Claude engine' directory exists in the project root.\n", file=sys.stderr)
    sys.exit(1)

from gemini.adapter import BuildAdapter, resolve_move_identifier
from gemini.optimizer import (
    BattleRoyaleBuildOptimizer,
    REFERENCE_SPECIES,
    get_default_baselines
)


def filter_candidate_pool(raw_moves, valid_registry):
    """Filters species move pool against Claude's active move registry."""
    usable_moves = []
    rejected_moves = []
    mapped_moves = []

    for m in raw_moves:
        resolved = resolve_move_identifier(m, valid_registry)
        if resolved is not None:
            usable_moves.append(m)
            mapped_moves.append(resolved)
        else:
            rejected_moves.append(m)

    return usable_moves, mapped_moves, rejected_moves


def main():
    print("=" * 80)
    print("PROJECT: POKÉMON ISLAND BATTLE ROYALE — INTEGRATION RUNNER")
    print("=" * 80)

    # 1. Inspect real Claude move registry
    claude_registry = set(MOVES.keys())
    print(f"[Phase 1] Claude move registry loaded: {len(claude_registry)} moves registered.")

    # 2. Filter Garchomp move pool
    raw_pool = REFERENCE_SPECIES["garchomp"]["pool"]
    usable, mapped, rejected = filter_candidate_pool(raw_pool, claude_registry)
    print(f"\n[Phase 2] Move Pool Filtering for Garchomp:")
    print(f" -> Raw Garchomp Pool: {raw_pool}")
    print(f" -> Usable Candidates: {usable}")
    print(f" -> Canonical Mapped:  {mapped}")
    print(f" -> Rejected Moves:    {rejected}")

    expected_usable = ["earthquake", "closecombat", "dracometeor", "surf"]
    if set(usable) != set(expected_usable):
        raise AssertionError(f"Filtered pool {usable} does not match expected {expected_usable}")
    print(" -> Verification PASSED: Usable pool is exactly earthquake, closecombat, dracometeor, surf.")

    # 3. Instantiate and run BattleRoyaleBuildOptimizer with reproducible seed
    seed = 42
    generations = 10
    pop_size = 8
    n_trials = 25

    print(f"\n[Phase 3] Executing BattleRoyaleBuildOptimizer (Seed={seed}, Gen={generations}, Pop={pop_size})...")
    opt = BattleRoyaleBuildOptimizer(
        candidate_species_id="garchomp",
        baseline_opponents=get_default_baselines(),
        move_pool_override=usable,
        seed=seed
    )
    best_build, best_fitness = opt.optimize(
        generations=generations,
        pop_size=pop_size,
        n_trials=n_trials
    )

    print(f" -> Best Genome Selected: Species={best_build['species_id']}, Nature={best_build['nature']}")
    print(f" -> Selected Moves: {best_build['moves']}")
    print(f" -> Selected EVs:   {best_build['evs']}")
    print(f" -> Fitness Score:  {best_fitness:.4f} (Gemini multi-combatant survival score)")

    # 4. Demonstrate deterministic reproducibility under identical configuration
    print("\n[Phase 4] Verifying Seed Reproducibility...")
    opt_verify = BattleRoyaleBuildOptimizer(
        candidate_species_id="garchomp",
        baseline_opponents=get_default_baselines(),
        move_pool_override=usable,
        seed=seed
    )
    best_build_v, best_fitness_v = opt_verify.optimize(
        generations=generations,
        pop_size=pop_size,
        n_trials=n_trials
    )
    assert best_build == best_build_v, "Reproducibility failure: build genomes differ"
    assert best_fitness == best_fitness_v, "Reproducibility failure: fitness scores differ"
    print(" -> Reproducibility PASSED: Identical genome and score reproduced.")

    # 5. Export build through BuildAdapter
    out_dir = os.path.join(_ROOT, "out", "roster")
    roster_json = os.path.join(out_dir, "builds.json")
    audit_json = os.path.join(out_dir, "builds_audit.json")

    species_info = REFERENCE_SPECIES["garchomp"]
    build_entry = (
        best_build,
        species_info["stats"],
        species_info["types"],
        "terrestrial",
        best_fitness,
        n_trials
    )

    print(f"\n[Phase 5] Exporting Roster via BuildAdapter...")
    _, audit_records = BuildAdapter.export_roster([build_entry], roster_json, audit_json)
    print(f" -> Wrote engine roster: {roster_json}")
    print(f" -> Wrote audit log:     {audit_json}")

    # 6. Mechanics audit review
    audit = audit_records[0]
    print("\n[Phase 6] Engine Mechanics & Compatibility Audit:")
    if audit.unsupported_mechanics_warnings:
        for w in audit.unsupported_mechanics_warnings:
            print(f" [!] AUDIT WARNING: {w}")
    print(f" -> Final Level-100 Stats: {audit.engine_stats}")
    print(f" -> Flight Capability: {audit.can_fly}")

    # 7. Ingestion verification via Claude's actual JsonProvider
    print("\n[Phase 7] Ingesting Exported Roster via Claude's JsonProvider...")
    cfg = Config(roster_source=roster_json, n_combatants=1)
    provider = JsonProvider(roster_json)
    loaded_roster = provider.load_roster(cfg)

    print(f" -> Successfully loaded {len(loaded_roster)} build(s) into Claude's data layer.")
    c = loaded_roster[0]
    print(f"    Species:  {c.species}")
    print(f"    Types:    {c.types}")
    print(f"    Moves:    {c.moves}")
    print(f"    Stats:    {c.stats}")
    print(f"    Ability:  {c.ability}")
    print(f"    Item:     {c.item}")
    print(f"    Can Fly:  {c.can_fly}")

    print("\n" + "=" * 80)
    print("MILESTONE VERIFICATION COMPLETE: Garchomp build exported and verified.")
    print("NOTE ON SCOPE:")
    print("- Fitness score was generated by Gemini's simplified survival simulator.")
    print("- It is NOT a fitness score from Claude's tick-based island engine.")
    print("- Baseline opponents (Corviknight, Toxapex, Dragapult) were used internally")
    print("  and are NOT exported to Claude's engine.")
    print("=" * 80)


if __name__ == "__main__":
    main()