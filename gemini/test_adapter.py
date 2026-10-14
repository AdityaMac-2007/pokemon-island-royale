"""
Unit and Integration Tests for Gemini-Claude Adapter and Optimizer Pipeline.
File: gemini/test_adapter.py
"""
import copy
import json
import os
import sys
import tempfile
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CLAUDE_DIR = os.path.join(_ROOT, "Claude engine")
if _CLAUDE_DIR not in sys.path:
    sys.path.insert(0, _CLAUDE_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from gemini.adapter import (
    BuildAdapter,
    resolve_move_identifier,
    get_claude_move_registry,
    MOVE_ALIASES,
)
from gemini.optimizer import (
    BattleRoyaleBuildOptimizer,
    REFERENCE_SPECIES,
    DEFAULT_BASELINES,
    get_default_baselines
)
from gemini.integrate import filter_candidate_pool

# Imports from Claude's actual engine
from pkmn_br.data import JsonProvider  # type: ignore
from pkmn_br.config import Config  # type: ignore
from pkmn_br.rules import calc_stats as claude_real_calc_stats  # type: ignore


class TestAdapterIntegration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.roster_path = os.path.join(self.temp_dir.name, "builds.json")
        self.audit_path = os.path.join(self.temp_dir.name, "builds_audit.json")
        self.registry = get_claude_move_registry()

    def tearDown(self):
        self.temp_dir.cleanup()

    # ------------------------------------------------------------------------
    # 1. Compact Move Aliases
    # ------------------------------------------------------------------------
    def test_compact_move_aliases(self):
        """Verifies aliases for identical moves across formatting styles."""
        self.assertEqual(resolve_move_identifier("closecombat", self.registry), "close_combat")
        self.assertEqual(resolve_move_identifier("dracometeor", self.registry), "draco_meteor")
        self.assertEqual(resolve_move_identifier("dragonclaw", self.registry), "dragon_claw")
        self.assertEqual(resolve_move_identifier("stoneedge", self.registry), "stone_edge")
        self.assertEqual(resolve_move_identifier("earthquake", self.registry), "earthquake")
        self.assertEqual(resolve_move_identifier("surf", self.registry), "surf")

    def test_bodypress_not_aliased_to_bodyslam(self):
        """Ensures mechanically distinct moves like bodypress are never aliased to body_slam."""
        self.assertNotIn("bodypress", MOVE_ALIASES)
        self.assertIsNone(resolve_move_identifier("bodypress", self.registry))

    # ------------------------------------------------------------------------
    # 2. Strict Rejection of Unsupported Moves
    # ------------------------------------------------------------------------
    def test_rejection_of_unsupported_moves(self):
        """Unsupported moves (snarl, irondefense) return None and fail validation."""
        self.assertEqual(resolve_move_identifier("protect", self.registry), "protect")
        self.assertIsNone(resolve_move_identifier("snarl", self.registry))
        self.assertIsNone(resolve_move_identifier("irondefense", self.registry))

        illegal_build = {
            "species": "Garchomp",
            "moves": ["earthquake", "snarl", "closecombat"],
            "nature": "Jolly",
            "evs": {"atk": 252, "spe": 252}
        }
        with self.assertRaises(ValueError) as ctx:
            BuildAdapter.validate_and_convert(
                illegal_build,
                REFERENCE_SPECIES["garchomp"]["stats"],
                REFERENCE_SPECIES["garchomp"]["types"]
            )
        self.assertIn("Move rejection", str(ctx.exception))
        self.assertIn("snarl", str(ctx.exception))

    # ------------------------------------------------------------------------
    # 3. Garchomp Candidate-Pool Filter
    # ------------------------------------------------------------------------
    def test_garchomp_candidate_pool_filter(self):
        """Keeps supported Protect while filtering unsupported moves from Garchomp."""
        raw_pool = REFERENCE_SPECIES["garchomp"]["pool"]
        usable, mapped, rejected = filter_candidate_pool(raw_pool, self.registry)

        self.assertEqual(set(usable), {"earthquake", "protect", "closecombat", "dracometeor", "surf"})
        self.assertEqual(set(mapped), {"earthquake", "protect", "close_combat", "draco_meteor", "surf"})
        self.assertEqual(set(rejected), {"irondefense", "snarl"})

    # ------------------------------------------------------------------------
    # 4. Move Pool Override & DB Mutation Protection
    # ------------------------------------------------------------------------
    def test_optimizer_move_pool_override_and_no_unsupported_moves(self):
        """Verifies move pool override isolates species info without mutating global REFERENCE_SPECIES."""
        override_moves = ["earthquake", "closecombat", "dracometeor", "surf"]
        original_pool_len = len(REFERENCE_SPECIES["garchomp"]["pool"])

        opt = BattleRoyaleBuildOptimizer(
            candidate_species_id="garchomp",
            move_pool_override=override_moves,
            seed=42
        )

        self.assertEqual(opt.species_info["pool"], override_moves)
        self.assertEqual(len(REFERENCE_SPECIES["garchomp"]["pool"]), original_pool_len)

        for _ in range(20):
            genome = opt.generate_random_genome()
            for m in genome["moves"]:
                self.assertIn(m, override_moves)
                self.assertNotIn(m, ["protect", "snarl", "irondefense"])

    def test_optimizer_rejection_of_invalid_moves_in_override(self):
        """Verifies move_pool_override rejects unknown or illegal moves."""
        with self.assertRaises(ValueError) as ctx:
            BattleRoyaleBuildOptimizer("garchomp", move_pool_override=["earthquake", "nonexistent_move"])
        self.assertIn("Unsupported move", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            BattleRoyaleBuildOptimizer("garchomp", move_pool_override=["earthquake", "bodypress"])
        self.assertIn("Illegal move", str(ctx.exception))

    # ------------------------------------------------------------------------
    # 5. Deterministic Reproducibility
    # ------------------------------------------------------------------------
    def test_deterministic_reproducibility_same_seed(self):
        """Ensures identical seeds yield identical builds, fitness scores, and exported bytes."""
        override = ["earthquake", "closecombat", "dracometeor", "surf"]
        opt1 = BattleRoyaleBuildOptimizer("garchomp", move_pool_override=override, seed=123)
        b1, f1 = opt1.optimize(generations=4, pop_size=6, n_trials=10)

        opt2 = BattleRoyaleBuildOptimizer("garchomp", move_pool_override=override, seed=123)
        b2, f2 = opt2.optimize(generations=4, pop_size=6, n_trials=10)

        self.assertEqual(b1, b2)
        self.assertEqual(f1, f2)

        entry1 = (b1, REFERENCE_SPECIES["garchomp"]["stats"], REFERENCE_SPECIES["garchomp"]["types"], "terrestrial", f1, 10)
        entry2 = (b2, REFERENCE_SPECIES["garchomp"]["stats"], REFERENCE_SPECIES["garchomp"]["types"], "terrestrial", f2, 10)

        p1_r = os.path.join(self.temp_dir.name, "r1.json")
        p1_a = os.path.join(self.temp_dir.name, "a1.json")
        p2_r = os.path.join(self.temp_dir.name, "r2.json")
        p2_a = os.path.join(self.temp_dir.name, "a2.json")

        BuildAdapter.export_roster([entry1], p1_r, p1_a)
        BuildAdapter.export_roster([entry2], p2_r, p2_a)

        with open(p1_r, "rb") as f_a, open(p2_r, "rb") as f_b:
            self.assertEqual(f_a.read(), f_b.read())
        with open(p1_a, "rb") as f_a, open(p2_a, "rb") as f_b:
            self.assertEqual(f_a.read(), f_b.read())

    # ------------------------------------------------------------------------
    # 6. Final-Stat Calculation Matches Claude's calc_stats
    # ------------------------------------------------------------------------
    def test_stat_calculation_matches_claude_real_calc_stats(self):
        """Compares BuildAdapter stat calculation directly with Claude's calc_stats for Jolly and Timid."""
        base = REFERENCE_SPECIES["garchomp"]["stats"]
        evs = {"hp": 4, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252}

        # Jolly (+spe, -spa): Attack remains unaffected at 359
        adapter_jolly = BuildAdapter.calculate_stats(base, evs, "Jolly", ivs=31, level=100)
        claude_jolly = claude_real_calc_stats(base, evs, "spe", "spa", ivs=31, level=100)
        self.assertEqual(adapter_jolly, claude_jolly)
        self.assertEqual(adapter_jolly["hp"], 358)
        self.assertEqual(adapter_jolly["atk"], 359)
        self.assertEqual(adapter_jolly["spe"], 333)

        # Timid (+spe, -atk): Attack is lowered by 10% to 323
        adapter_timid = BuildAdapter.calculate_stats(base, evs, "Timid", ivs=31, level=100)
        claude_timid = claude_real_calc_stats(base, evs, "spe", "atk", ivs=31, level=100)
        self.assertEqual(adapter_timid, claude_timid)
        self.assertEqual(adapter_timid["hp"], 358)
        self.assertEqual(adapter_timid["atk"], 323)
        self.assertEqual(adapter_timid["spe"], 333)

    # ------------------------------------------------------------------------
    # 7. JSON Loading Through Claude's Actual JsonProvider
    # ------------------------------------------------------------------------
    def test_exported_roster_loads_through_claude_actual_json_provider(self):
        """Loads optimized Garchomp build directly through Claude's JsonProvider."""
        opt = BattleRoyaleBuildOptimizer(
            "garchomp",
            move_pool_override=["earthquake", "closecombat", "dracometeor", "surf"],
            seed=99
        )
        best_b, fit = opt.optimize(generations=2, pop_size=4, n_trials=5)

        entry = (
            best_b,
            REFERENCE_SPECIES["garchomp"]["stats"],
            REFERENCE_SPECIES["garchomp"]["types"],
            "terrestrial",
            fit,
            5
        )
        BuildAdapter.export_roster([entry], self.roster_path, self.audit_path)

        cfg = Config(roster_source=self.roster_path, n_combatants=1)
        provider = JsonProvider(self.roster_path)
        roster = provider.load_roster(cfg)

        self.assertEqual(len(roster), 1)
        c = roster[0]
        self.assertEqual(c.species, "Garchomp")
        self.assertEqual(set(c.moves), {"earthquake", "close_combat", "draco_meteor", "surf"})
        self.assertEqual(c.ability, BuildAdapter.normalize_identifier(best_b["ability"]))
        self.assertIn(c.ability, ["rough_skin", "sand_veil"])
        self.assertEqual(c.item, BuildAdapter.normalize_identifier(best_b["item"]))
        self.assertFalse(c.can_fly)

    # ------------------------------------------------------------------------
    # 8. Clear Failure When Claude Package Cannot Be Imported
    # ------------------------------------------------------------------------
    def test_clear_failure_if_engine_cannot_be_imported(self):
        """Simulates missing pkmn_br package and asserts loud failure."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("pkmn_br"):
                raise ImportError(f"No module named '{name}' (simulated test failure)")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            with self.assertRaises(ImportError) as ctx:
                get_claude_move_registry()
            self.assertIn("Could not import Claude's battle royale engine", str(ctx.exception))
        finally:
            builtins.__import__ = real_import

    # ------------------------------------------------------------------------
    # 9. Baselines Integrity
    # ------------------------------------------------------------------------
    def test_default_baselines_reusable_and_immutable(self):
        """Verifies get_default_baselines returns deep copies that do not mutate DEFAULT_BASELINES."""
        baselines = get_default_baselines()
        self.assertEqual(len(baselines), 3)
        self.assertEqual(len(DEFAULT_BASELINES), 3)

        baselines[0]["moves"].append("extra_move")
        self.assertNotIn("extra_move", DEFAULT_BASELINES[0]["moves"])


if __name__ == "__main__":
    unittest.main()