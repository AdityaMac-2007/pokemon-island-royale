"""Headless regression tests for modern viewer zoom and coastline foam."""
from __future__ import annotations
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = ROOT / "Claude engine"
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pygame
from gemini.island_2d_modern import IslandViewer


class ModernViewerUsabilityTests(unittest.TestCase):
    def make_zoom_viewer(self):
        # Use a lightweight viewer instance so this test is independent of a window.
        viewer = object.__new__(IslandViewer)
        viewer.zoom = 1.0
        viewer.pan_x = 17.0
        viewer.pan_y = -9.0
        viewer.follow_selected = False
        viewer.engine = SimpleNamespace(island=SimpleNamespace(cx=24.0, cy=24.0, size=48.0))
        rect = pygame.Rect(40, 60, 800, 600)
        viewer.map_rect = lambda: rect
        viewer.world_scale = lambda: min(rect.width, rect.height) / (viewer.engine.island.size * 1.40) * viewer.zoom
        return viewer

    def world_under(self, viewer, point):
        rect = viewer.map_rect()
        island = viewer.engine.island
        scale = viewer.world_scale()
        return (
            island.cx + (point[0] - rect.centerx - viewer.pan_x) / scale,
            island.cy + (point[1] - rect.centery - viewer.pan_y) / scale,
        )

    def test_zoom_keeps_cursor_world_point_anchored(self):
        viewer = self.make_zoom_viewer()
        point = (375, 286)
        before = self.world_under(viewer, point)
        viewer._zoom_at(point, 1.35)
        after = self.world_under(viewer, point)
        self.assertGreater(viewer.zoom, 1.0)
        self.assertAlmostEqual(before[0], after[0], places=7)
        self.assertAlmostEqual(before[1], after[1], places=7)

    def test_zoom_clamps_at_six_times(self):
        viewer = self.make_zoom_viewer()
        viewer._zoom_at(viewer.map_rect().center, 100.0)
        self.assertEqual(viewer.zoom, 6.0)

    def test_wheel_zoom_can_zoom_out_to_minimum(self):
        viewer = self.make_zoom_viewer()
        viewer._zoom_at(viewer.map_rect().center, 0.001)
        self.assertEqual(viewer.zoom, 0.45)

    def test_coastline_foam_does_not_use_removed_circle_locals(self):
        source_path = ROOT / "gemini" / "island_2d_modern.py"
        source = source_path.read_text(encoding="utf-8")
        start = source.index("    def _draw_ocean_and_island(")
        end = source.index("    def _draw_tile_motif(", start)
        function_text = source[start:end]
        self.assertNotIn("rr = radius +", function_text)
        self.assertNotIn("center[0] + math.cos", function_text)
        self.assertIn("coastline_points(segments=38, scale=1.012)", function_text)


if __name__ == "__main__":
    unittest.main()
