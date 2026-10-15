"""Regression tests for deterministic irregular island geometry."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE_ROOT = ROOT / "Claude engine"
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from pkmn_br.config import Config
from pkmn_br.island import Island
from pkmn_br.rng import stream


class IrregularIslandGeometryTests(unittest.TestCase):
    def make_island(self, seed):
        cfg = Config(seed=seed, n_combatants=4, roster_source="stub")
        return Island.generate(cfg, 4)

    def test_same_seed_produces_identical_coastline(self):
        a = self.make_island(77)
        b = self.make_island(77)
        self.assertEqual(a.coastline_points(), b.coastline_points())

    def test_different_seeds_produce_different_coastlines(self):
        a = self.make_island(77)
        b = self.make_island(78)
        self.assertNotEqual(a.coastline_points(), b.coastline_points())

    def test_coastline_points_are_on_land_boundary(self):
        island = self.make_island(77)
        for x, y in island.coastline_points(segments=96):
            self.assertTrue(island.point_is_land(x, y), (x, y))

    def test_random_points_are_on_land_and_inset(self):
        island = self.make_island(77)
        rng = stream(1234, "geometry-test-points")
        for _ in range(250):
            x, y = island.random_land_point(rng, margin=0.5)
            self.assertTrue(island.point_is_land(x, y, margin=0.49), (x, y))

    def test_project_to_land_handles_points_outside_coast(self):
        island = self.make_island(77)
        outside = (island.cx + island.size, island.cy + island.size)
        x, y = island.project_to_land(*outside, margin=0.4)
        self.assertTrue(island.point_is_land(x, y, margin=0.39), (x, y))

    def test_center_is_land_and_far_corner_is_ocean(self):
        island = self.make_island(77)
        self.assertTrue(island.point_is_land(island.cx, island.cy))
        self.assertFalse(island.point_is_land(0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
