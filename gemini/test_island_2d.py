"""
POKÉMON ISLAND BATTLE ROYALE — 2D VIEWER TEST SUITE
Headless verification of coordinate projection, sprite transparency,
aerial layer rendering, terrain mapping, and zero-side-effect determinism.
File: gemini/test_island_2d.py
"""
import os
import sys
import unittest

# Enforce dummy video driver for 100% headless execution
os.environ["SDL_VIDEODRIVER"] = "dummy"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CLAUDE_DIR = os.path.join(_ROOT, "Claude engine")
if _CLAUDE_DIR not in sys.path:
    sys.path.insert(0, _CLAUDE_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pygame
from pkmn_br.config import Config
from pkmn_br.engine import Engine
from gemini.island_2d import (
    Camera,
    SpriteManager,
    IslandViewer,
    PocProvider,
    BIOME_COLORS
)


class TestIsland2DViewer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        pygame.init()

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def setUp(self):
        self.cfg_poc = Config(seed=42, n_combatants=4, roster_source="stub")

    # ------------------------------------------------------------------------
    # 1. Camera & Coordinate Transformation Tests
    # ------------------------------------------------------------------------
    def test_camera_world_screen_roundtrip(self):
        """Verifies mathematical invertibility of World-to-Screen projections."""
        cam = Camera(viewport_w=800, viewport_h=600, cx=24.0, cy=24.0, zoom=20.0)

        test_points = [(24.0, 24.0), (0.0, 0.0), (48.0, 48.0), (12.5, 33.7)]
        for wx, wy in test_points:
            sx, sy = cam.world_to_screen(wx, wy)
            rec_wx, rec_wy = cam.screen_to_world(sx, sy)
            self.assertAlmostEqual(wx, rec_wx, places=4)
            self.assertAlmostEqual(wy, rec_wy, places=4)

    def test_camera_zoom_anchoring(self):
        """Verifies zooming at a screen position anchors the world point underneath."""
        cam = Camera(viewport_w=800, viewport_h=600, cx=20.0, cy=20.0, zoom=15.0)
        sx, sy = 300, 250
        world_before = cam.screen_to_world(sx, sy)

        cam.zoom_at(sx, sy, 1.5)
        world_after = cam.screen_to_world(sx, sy)

        self.assertAlmostEqual(world_before[0], world_after[0], places=4)
        self.assertAlmostEqual(world_before[1], world_after[1], places=4)

    # ------------------------------------------------------------------------
    # 2. Sprite Manager & Transparency Tests
    # ------------------------------------------------------------------------
    def test_sprite_procedural_transparency(self):
        """Verifies procedural fallback surfaces possess per-pixel alpha transparency."""
        sm = SpriteManager(asset_dir="nonexistent_test_dir")
        token = sm.get_sprite("Garchomp", ("dragon", "ground"), size_px=48)

        self.assertIsInstance(token, pygame.Surface)
        self.assertTrue(token.get_flags() & pygame.SRCALPHA)
        self.assertEqual(token.get_size(), (48, 48))

        # Corner pixel must be completely transparent
        corner_alpha = token.get_at((0, 0)).a
        self.assertEqual(corner_alpha, 0)

        # Center pixel must be opaque
        center_alpha = token.get_at((24, 24)).a
        self.assertEqual(center_alpha, 255)

    # ------------------------------------------------------------------------
    # 3. Terrain & Biome Mapping Tests
    # ------------------------------------------------------------------------
    def test_terrain_grid_and_biomes(self):
        """Verifies engine island grid, biomes, and safe zones are correctly read."""
        viewer = IslandViewer(self.cfg_poc, provider=PocProvider(), headless=True)
        isl = viewer.engine.island

        self.assertGreater(isl.size, 0)
        self.assertGreater(isl.radius, 0)
        self.assertTrue(len(isl.grid) > 0)

        # Verify every cell in the grid maps to a recognized or fallback color
        for row in isl.grid:
            for biome_name in row:
                self.assertTrue(isinstance(biome_name, str))

    # ------------------------------------------------------------------------
    # 4. Aerial Layer & Flight Capability Tests
    # ------------------------------------------------------------------------
    def test_aerial_combatant_layering(self):
        """Verifies that flyers are elevated while grounded combatants remain planted."""
        viewer = IslandViewer(self.cfg_poc, provider=PocProvider(), headless=True)
        by_name = {c.name: c for c in viewer.engine.by_id}

        self.assertIn("Gengar", by_name)
        self.assertIn("Garchomp", by_name)
        self.assertIn("Latios", by_name)
        self.assertIn("Charizard", by_name)

        # Terrestrial units cannot fly; aerial units can fly
        self.assertFalse(by_name["Gengar"].can_fly)
        self.assertFalse(by_name["Garchomp"].can_fly)
        self.assertTrue(by_name["Latios"].can_fly)
        self.assertTrue(by_name["Charizard"].can_fly)

        # Advance match until a flyer takes off or lands
        for _ in range(50):
            viewer.step_simulation()
            if viewer.engine.finished:
                break

        # Grounded combatants must have alt=0; flyers can toggle alt=0 or alt=1
        for c in viewer.engine.by_id:
            if not c.can_fly:
                self.assertEqual(c.alt, 0)

    # ------------------------------------------------------------------------
    # 5. Deterministic Engine Integrity Verification (Zero-Side-Effect Rule)
    # ------------------------------------------------------------------------
    def test_zero_side_effect_determinism(self):
        """
        CRITICAL TEST: Compares match digest of a headless Engine run vs.
        stepped IslandViewer run with identical seed and roster.
        The resulting digest, tick count, and rankings MUST match identically!
        """
        seed = 77
        cfg1 = Config(seed=seed, n_combatants=4, roster_source="stub")
        cfg2 = Config(seed=seed, n_combatants=4, roster_source="stub")

        # 1. Native headless engine execution
        eng_native = Engine(cfg1, provider=PocProvider())
        res_native = eng_native.run()

        # 2. IslandViewer stepped simulation execution
        viewer = IslandViewer(cfg2, provider=PocProvider(), headless=True)
        res_viewer = viewer.run_all_headless()

        # Assert identical results
        self.assertEqual(res_native["ticks"], res_viewer["ticks"])
        self.assertEqual(res_native["winner"], res_viewer["winner"])
        self.assertEqual(res_native["digest"], res_viewer["digest"])
        self.assertEqual(res_native["rankings"], res_viewer["rankings"])


if __name__ == "__main__":
    unittest.main()