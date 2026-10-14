"""
POKÉMON ISLAND BATTLE ROYALE — 2D PYGAME VIEWER
Interactive top-down 2D visualizer for Claude's island simulation engine.
File: gemini/island_2d.py
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sys
import time
import urllib.request
from typing import Dict, List, Optional, Tuple, Any

# Ensure project paths are resolved
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CLAUDE_DIR = os.path.join(_ROOT, "Claude engine")
if _CLAUDE_DIR not in sys.path:
    sys.path.insert(0, _CLAUDE_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import pygame
except ImportError as exc:
    raise ImportError(
        "Pygame is required to run the 2D Island Battle Royale Viewer.\n"
        "Install it via: pip install pygame"
    ) from exc

from pkmn_br.config import Config
from pkmn_br.engine import Engine
from pkmn_br.data import get_provider, select_roster, StubProvider, Build
from pkmn_br.island import BIOMES


# ============================================================================
# 1. COLOR PALETTES & BIOME THEMES
# ============================================================================

TYPE_COLORS: Dict[str, Tuple[int, int, int]] = {
    "normal":   (168, 167, 122),
    "fire":     (238, 129, 48),
    "water":    (99, 144, 240),
    "electric": (247, 208, 44),
    "grass":    (122, 199, 76),
    "ice":      (150, 217, 214),
    "fighting": (194, 46, 40),
    "poison":   (163, 62, 161),
    "ground":   (226, 191, 101),
    "flying":   (169, 143, 243),
    "psychic":  (249, 85, 135),
    "bug":      (166, 185, 26),
    "rock":     (182, 161, 54),
    "ghost":    (115, 87, 151),
    "dragon":   (111, 53, 252),
    "dark":     (112, 87, 70),
    "steel":    (183, 183, 206),
    "fairy":    (214, 133, 173),
}

BIOME_COLORS: Dict[str, Tuple[int, int, int]] = {
    "ocean":    (24, 44, 68),
    "forest":   (46, 125, 50),
    "woods":    (56, 142, 60),
    "mountain": (115, 115, 115),
    "rock":     (130, 125, 120),
    "beach":    (225, 205, 145),
    "sand":     (218, 195, 130),
    "swamp":    (75, 90, 60),
    "volcano":  (140, 45, 35),
    "snow":     (230, 240, 248),
    "plains":   (125, 185, 85),
    "meadow":   (140, 195, 90),
}

POC_SPECIES_DEX = {
    "charizard": 6,
    "gengar": 94,
    "latios": 381,
    "garchomp": 445,
    "corviknight": 823,
}


# ============================================================================
# 2. PROOF-OF-CONCEPT ROSTER PROVIDER
# ============================================================================

class PocProvider:
    """Loads exactly the 4 proof-of-concept combatants from StubProvider."""
    def load_roster(self, cfg: Config) -> List[Build]:
        stub = StubProvider().load_roster(cfg)
        poc_names = {"Gengar", "Garchomp", "Latios", "Charizard"}
        selected = [b for b in stub if b.species in poc_names]
        order = ["Gengar", "Garchomp", "Latios", "Charizard"]
        return sorted(selected, key=lambda b: order.index(b.species) if b.species in order else 99)


# ============================================================================
# 3. SPRITE MANAGER & PROCEDURAL TOKEN GENERATOR
# ============================================================================

class SpriteManager:
    """
    Manages Pokémon sprites with transparent PNG loading and high-contrast
    procedural token fallbacks when asset files are not present.
    """
    def __init__(self, asset_dir: str = "assets/sprites"):
        self.asset_dir = asset_dir
        self.cache: Dict[Tuple[str, int], pygame.Surface] = {}
        os.makedirs(self.asset_dir, exist_ok=True)

    def get_sprite(self, species_name: str, types: Tuple[str, ...], size_px: int = 48) -> pygame.Surface:
        cache_key = (species_name.lower(), size_px)
        if cache_key in self.cache:
            return self.cache[cache_key]

        surf = self._load_from_disk(species_name, size_px)
        if surf is None:
            surf = self._generate_procedural_token(species_name, types, size_px)

        self.cache[cache_key] = surf
        return surf

    def _load_from_disk(self, species_name: str, size_px: int) -> Optional[pygame.Surface]:
        norm = species_name.lower().replace("-", "").replace(" ", "").replace("_", "")
        dex_num = POC_SPECIES_DEX.get(norm)

        candidates = [
            os.path.join(self.asset_dir, f"{norm}.png"),
            os.path.join(self.asset_dir, f"{species_name.lower()}.png"),
        ]
        if dex_num is not None:
            candidates.append(os.path.join(self.asset_dir, f"{dex_num}.png"))

        for path in candidates:
            if os.path.isfile(path):
                try:
                    img = pygame.image.load(path).convert_alpha()
                    return pygame.transform.smoothscale(img, (size_px, size_px))
                except Exception:
                    pass
        return None

    def _generate_procedural_token(self, species_name: str, types: Tuple[str, ...], size_px: int) -> pygame.Surface:
        """Renders an attractive, transparent per-pixel circular badge with type rims."""
        surf = pygame.Surface((size_px, size_px), pygame.SRCALPHA)
        center = size_px // 2
        radius = (size_px // 2) - 2

        t1 = types[0] if types else "normal"
        t2 = types[1] if len(types) > 1 else t1
        c1 = TYPE_COLORS.get(t1, (160, 160, 160))
        c2 = TYPE_COLORS.get(t2, c1)

        # Draw dual-type outer rim
        pygame.draw.circle(surf, c1, (center, center), radius)
        if len(types) > 1:
            pygame.draw.arc(surf, c2, (2, 2, size_px - 4, size_px - 4), -math.pi / 2, math.pi / 2, width=4)

        # Draw dark inner disk
        inner_r = max(2, radius - 4)
        pygame.draw.circle(surf, (28, 32, 40), (center, center), inner_r)

        # Draw monogram text
        initials = species_name[:2].upper()
        font = pygame.font.SysFont("Arial", max(10, size_px // 3), bold=True)
        txt = font.render(initials, True, (240, 240, 245))
        txt_rect = txt.get_rect(center=(center, center))
        surf.blit(txt, txt_rect)

        return surf

    @staticmethod
    def download_poc_sprites(target_dir: str = "assets/sprites") -> List[str]:
        """Downloads the 4 POC sprites directly from PokeAPI's repository."""
        os.makedirs(target_dir, exist_ok=True)
        downloaded = []
        base_url = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"

        manifest = {
            "source": "https://github.com/PokeAPI/sprites",
            "license": "Sprites: Nintendo/Game Freak/Creatures Inc. (Fair Use Simulation). Repository: BSD-3-Clause",
            "items": {}
        }

        for name, dex in POC_SPECIES_DEX.items():
            dest = os.path.join(target_dir, f"{name}.png")
            url = f"{base_url}/{dex}.png"
            if not os.path.exists(dest):
                try:
                    urllib.request.urlretrieve(url, dest)
                    downloaded.append(name)
                except Exception as exc:
                    print(f"Warning: Failed to download {name} from {url}: {exc}")
            manifest["items"][name] = {"dex": dex, "url": url, "path": dest}

        with open(os.path.join(target_dir, "manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)

        return downloaded


# ============================================================================
# 4. CAMERA & COORDINATE TRANSFORMATIONS
# ============================================================================

class Camera:
    """Handles smooth zoom, pan, and World-to-Screen coordinate projection."""
    def __init__(self, viewport_w: int, viewport_h: int, cx: float = 24.0, cy: float = 24.0, zoom: float = 18.0):
        self.w = viewport_w
        self.h = viewport_h
        self.cx = cx
        self.cy = cy
        self.zoom = zoom
        self.min_zoom = 6.0
        self.max_zoom = 70.0
        self.follow_id: Optional[int] = None

    def world_to_screen(self, wx: float, wy: float) -> Tuple[int, int]:
        sx = int((wx - self.cx) * self.zoom + (self.w / 2.0))
        sy = int((wy - self.cy) * self.zoom + (self.h / 2.0))
        return sx, sy

    def screen_to_world(self, sx: int, sy: int) -> Tuple[float, float]:
        wx = (sx - (self.w / 2.0)) / self.zoom + self.cx
        wy = (sy - (self.h / 2.0)) / self.zoom + self.cy
        return wx, wy

    def zoom_at(self, sx: int, sy: int, factor: float):
        old_wx, old_wy = self.screen_to_world(sx, sy)
        self.zoom = max(self.min_zoom, min(self.max_zoom, self.zoom * factor))
        # Keep mouse world coordinate anchored
        self.cx = old_wx - (sx - (self.w / 2.0)) / self.zoom
        self.cy = old_wy - (sy - (self.h / 2.0)) / self.zoom

    def pan(self, dx_px: int, dy_px: int):
        self.cx -= dx_px / self.zoom
        self.cy -= dy_px / self.zoom
        self.follow_id = None


# ============================================================================
# 5. VISUAL COMBAT EFFECTS
# ============================================================================

class CombatFxManager:
    """Manages hit beams, floating damage numbers, and elimination markers."""
    def __init__(self):
        self.beams: List[Dict[str, Any]] = []
        self.floating_texts: List[Dict[str, Any]] = []

    def add_hit(self, ax: float, ay: float, dx: float, dy: float, dmg: int, crit: bool = False):
        color = (255, 215, 0) if crit else (255, 75, 75)
        self.beams.append({
            "ax": ax, "ay": ay, "dx": dx, "dy": dy,
            "color": color, "life": 12, "max_life": 12
        })
        text = f"CRIT -{dmg}" if crit else f"-{dmg}"
        self.floating_texts.append({
            "wx": dx, "wy": dy - 0.5,
            "text": text, "color": color, "life": 32, "max_life": 32
        })

    def add_event_notice(self, wx: float, wy: float, text: str, color: Tuple[int, int, int]):
        self.floating_texts.append({
            "wx": wx, "wy": wy - 0.6,
            "text": text, "color": color, "life": 28, "max_life": 28
        })

    def update(self):
        for b in self.beams:
            b["life"] -= 1
        self.beams = [b for b in self.beams if b["life"] > 0]

        for ft in self.floating_texts:
            ft["life"] -= 1
            ft["wy"] -= 0.02  # Float upwards in world units
        self.floating_texts = [ft for ft in self.floating_texts if ft["life"] > 0]

    def render(self, surface: pygame.Surface, camera: Camera):
        font = pygame.font.SysFont("Arial", max(11, int(camera.zoom * 0.75)), bold=True)

        for b in self.beams:
            sx1, sy1 = camera.world_to_screen(b["ax"], b["ay"])
            sx2, sy2 = camera.world_to_screen(b["dx"], b["dy"])
            alpha = int(255 * (b["life"] / b["max_life"]))
            beam_surf = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            color = (*b["color"], alpha)
            pygame.draw.line(beam_surf, color, (sx1, sy1), (sx2, sy2), width=3)
            surface.blit(beam_surf, (0, 0))

        for ft in self.floating_texts:
            sx, sy = camera.world_to_screen(ft["wx"], ft["wy"])
            alpha = int(255 * (ft["life"] / ft["max_life"]))
            txt_surf = font.render(ft["text"], True, ft["color"])
            txt_surf.set_alpha(alpha)
            # Outline
            out_surf = font.render(ft["text"], True, (10, 10, 10))
            out_surf.set_alpha(alpha)
            for ox, oy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                surface.blit(out_surf, (sx + ox, sy + oy))
            surface.blit(txt_surf, (sx, sy))


# ============================================================================
# 6. COMPLETE 2D ISLAND VIEWER
# ============================================================================

class IslandViewer:
    """
    Main 2D Battle Royale Viewer.
    Reads Claude's engine directly without mutating state or altering RNG.
    """
    def __init__(
        self,
        config: Config,
        provider=None,
        sprite_dir: str = "assets/sprites",
        headless: bool = False,
        window_size: Tuple[int, int] = (1280, 800)
    ):
        self.cfg = config
        self.headless = headless
        self.engine = Engine(config, provider)
        self.sprites = SpriteManager(sprite_dir)
        self.fx = CombatFxManager()

        self.last_event_idx = 0
        self.paused = False
        self.speed = 1.0
        self.step_accumulator = 0.0
        self.selected_id: Optional[int] = None
        self.show_help = False

        self.w, self.h = window_size
        self.hud_w = 320
        self.view_w = self.w - self.hud_w
        self.view_h = self.h

        # Center camera on the island
        isl = self.engine.island
        init_zoom = min(self.view_w, self.view_h) / (isl.size * 1.15)
        self.camera = Camera(self.view_w, self.view_h, cx=isl.cx, cy=isl.cy, zoom=init_zoom)

        if not self.headless:
            pygame.init()
            pygame.display.set_caption("Pokémon Island Battle Royale — 2D Viewer")
            self.screen = pygame.display.set_mode((self.w, self.h), pygame.RESIZABLE)
            self.clock = pygame.time.Clock()
            self.font_sm = pygame.font.SysFont("Consolas, Menlo, Monaco", 12)
            self.font_md = pygame.font.SysFont("Consolas, Menlo, Monaco", 14, bold=True)
            self.font_lg = pygame.font.SysFont("Consolas, Menlo, Monaco", 18, bold=True)

    # ------------------------------------------------------------------------
    # Simulation Stepping (Preserves 100% Determinism)
    # ------------------------------------------------------------------------
    def step_simulation(self):
        """Advances engine by exactly 1 tick and digests emitted events."""
        if self.engine.finished:
            return
        self.engine.step()
        self._process_new_events()

    def _process_new_events(self):
        events = self.engine.log.events
        while self.last_event_idx < len(events):
            ev = events[self.last_event_idx]
            self.last_event_idx += 1
            e_type = ev.get("e")

            if e_type == "hit":
                a = self.engine.by_id[ev["a"]]
                d = self.engine.by_id[ev["d"]]
                self.fx.add_hit(a.x, a.y, d.x, d.y, ev["dmg"], crit=ev.get("crit", False))
            elif e_type == "elim":
                c = self.engine.by_id[ev["id"]]
                self.fx.add_event_notice(c.x, c.y, "FAINTED", (220, 60, 60))
            elif e_type == "berry":
                c = self.engine.by_id[ev["id"]]
                self.fx.add_event_notice(c.x, c.y, "+BERRY", (46, 204, 113))
            elif e_type == "takeoff":
                c = self.engine.by_id[ev["id"]]
                self.fx.add_event_notice(c.x, c.y, "^ FLYING", (100, 200, 255))
            elif e_type == "land":
                c = self.engine.by_id[ev["id"]]
                self.fx.add_event_notice(c.x, c.y, "v LANDED", (200, 200, 200))

    def run_all_headless(self) -> Dict[str, Any]:
        """Runs the entire match headlessly and returns final engine result."""
        while not self.engine.finished:
            self.step_simulation()
        return self.engine.result()

    # ------------------------------------------------------------------------
    # Rendering Pipeline
    # ------------------------------------------------------------------------
    def render(self):
        if self.headless:
            return

        # Viewport surface
        view_surf = pygame.Surface((self.view_w, self.view_h))
        view_surf.fill(BIOME_COLORS["ocean"])

        self._render_terrain(view_surf)
        self._render_berries(view_surf)
        self._render_hazards(view_surf)
        self._render_safe_zone(view_surf)
        self._render_combatants(view_surf)
        self.fx.render(view_surf, self.camera)

        self.screen.blit(view_surf, (0, 0))
        self._render_hud()
        if self.show_help:
            self._render_help_overlay()

        pygame.display.flip()

    def _render_terrain(self, surf: pygame.Surface):
        isl = self.engine.island
        cam = self.camera

        # Island base circular landmass
        center_s = cam.world_to_screen(isl.cx, isl.cy)
        rad_s = int(isl.radius * cam.zoom)
        pygame.draw.circle(surf, (110, 165, 80), center_s, rad_s)

        # Biome grid cells
        cell_size_s = math.ceil(isl.CELL * cam.zoom)
        for r, row in enumerate(isl.grid):
            for c, biome in enumerate(row):
                wx = c * isl.CELL
                wy = r * isl.CELL
                # Distance of cell center to island center
                dist_sq = (wx + isl.CELL / 2.0 - isl.cx) ** 2 + (wy + isl.CELL / 2.0 - isl.cy) ** 2
                if dist_sq <= (isl.radius + 0.5) ** 2:
                    sx, sy = cam.world_to_screen(wx, wy)
                    color = BIOME_COLORS.get(biome, (100, 160, 80))
                    pygame.draw.rect(surf, color, (sx, sy, cell_size_s, cell_size_s))

        # Circular island beach edge
        pygame.draw.circle(surf, BIOME_COLORS["beach"], center_s, rad_s, width=max(2, int(cam.zoom * 0.4)))

    def _render_berries(self, surf: pygame.Surface):
        cam = self.camera
        berry_r = max(3, int(cam.zoom * 0.28))
        for bid, pos in self.engine.island.berries.pos.items():
            bx, by = pos[0], pos[1]
            sx, sy = cam.world_to_screen(bx, by)
            pygame.draw.circle(surf, (220, 40, 70), (sx, sy), berry_r)
            pygame.draw.circle(surf, (255, 255, 255), (sx, sy), max(1, berry_r - 2), width=1)

    def _render_hazards(self, surf: pygame.Surface):
        cam = self.camera
        t = self.engine.tick
        for h in self.engine.active_hazards:
            sx, sy = cam.world_to_screen(h["x"], h["y"])
            hr_s = int(h["r"] * cam.zoom)
            prog = (t - h["warn"]) / max(1, h["impact"] - h["warn"])
            alpha = int(80 + 120 * prog)

            haz_surf = pygame.Surface((hr_s * 2, hr_s * 2), pygame.SRCALPHA)
            pygame.draw.circle(haz_surf, (240, 60, 40, alpha), (hr_s, hr_s), hr_s)
            pygame.draw.circle(haz_surf, (255, 200, 50, 220), (hr_s, hr_s), hr_s, width=2)
            surf.blit(haz_surf, (sx - hr_s, sy - hr_s))

    def _render_safe_zone(self, surf: pygame.Surface):
        cam = self.camera
        z = self.engine.zone
        zx_s, zy_s = cam.world_to_screen(z.cx, z.cy)
        zr_s = int(z.r * cam.zoom)

        # Storm overlay
        storm = pygame.Surface((self.view_w, self.view_h), pygame.SRCALPHA)
        storm.fill((70, 15, 90, 85))  # Atmospheric purple storm
        pygame.draw.circle(storm, (0, 0, 0, 0), (zx_s, zy_s), max(0, zr_s))
        surf.blit(storm, (0, 0))

        # Glowing safe-zone ring
        if zr_s > 0:
            pygame.draw.circle(surf, (60, 220, 255), (zx_s, zy_s), zr_s, width=2)

    def _render_combatants(self, surf: pygame.Surface):
        cam = self.camera
        tick = self.engine.tick
        combatants = self.engine.by_id

        # Sort so grounded are drawn before airborne; dead drawn under alive
        sorted_combatants = sorted(combatants, key=lambda c: (1 if c.alive else 0, c.alt))

        for c in sorted_combatants:
            sx, sy = cam.world_to_screen(c.x, c.y)
            sprite_size = max(24, int(cam.zoom * 2.2))

            # 1. Ground Shadow (Always placed at ground level)
            shadow_w = int(sprite_size * 0.75)
            shadow_h = int(shadow_w * 0.45)
            shadow_surf = pygame.Surface((shadow_w, shadow_h), pygame.SRCALPHA)
            shadow_alpha = 110 if c.alive else 50
            if c.alt == 1:
                shadow_alpha = 70  # Fainter shadow for elevated flyers
            pygame.draw.ellipse(shadow_surf, (0, 0, 0, shadow_alpha), (0, 0, shadow_w, shadow_h))
            surf.blit(shadow_surf, (sx - shadow_w // 2, sy - shadow_h // 2))

            # 2. Altitude Layer Offset (Flyers render elevated)
            render_y = sy
            if c.alt == 1 and c.alive:
                # Vertical visual displacement + floating sine wave bob
                bob = int(math.sin(tick * 0.18 + c.id) * 3.5)
                render_y = sy - int(cam.zoom * 1.3) + bob
                # Connector indicator line
                pygame.draw.line(surf, (140, 200, 255, 120), (sx, sy), (sx, render_y), width=1)

            # 3. Sprite / Token Rendering
            sprite = self.sprites.get_sprite(c.name, c.types, size_px=sprite_size)
            if not c.alive:
                sprite = sprite.copy()
                sprite.fill((100, 100, 100, 160), special_flags=pygame.BLEND_RGBA_MULT)

            sprite_rect = sprite.get_rect(center=(sx, render_y))
            surf.blit(sprite, sprite_rect)

            # 4. Selection Highlight
            if c.id == self.selected_id:
                sel_r = (sprite_size // 2) + 4
                pygame.draw.circle(surf, (255, 220, 0), (sx, render_y), sel_r, width=2)

            # 5. Overhead Status & Health Bar
            if c.alive:
                self._render_combatant_bars(surf, c, sx, render_y, sprite_size)

    def _render_combatant_bars(self, surf: pygame.Surface, c, sx: int, sy: int, sprite_size: int):
        bar_w = max(34, int(sprite_size * 1.1))
        bar_h = 5
        bar_x = sx - bar_w // 2
        bar_y = sy - (sprite_size // 2) - 14

        # Background
        pygame.draw.rect(surf, (30, 30, 30), (bar_x, bar_y, bar_w, bar_h))
        hp_frac = max(0.0, min(1.0, c.hp / c.max_hp))
        hp_color = (46, 204, 113) if hp_frac > 0.5 else ((241, 196, 15) if hp_frac > 0.2 else (231, 76, 60))
        fill_w = int(bar_w * hp_frac)
        if fill_w > 0:
            pygame.draw.rect(surf, hp_color, (bar_x, bar_y, fill_w, bar_h))

        # Combatant Label: #{id} {Name} [AIR]
        tag = f"#{c.id} {c.name}"
        if c.alt == 1:
            tag += " [AIR]"
        txt = self.font_sm.render(tag, True, (255, 255, 255))
        surf.blit(txt, (sx - txt.get_width() // 2, bar_y - 12))

    # ------------------------------------------------------------------------
    # HUD & Control Interface
    # ------------------------------------------------------------------------
    def _render_hud(self):
        hud = pygame.Surface((self.hud_w, self.h))
        hud.fill((20, 24, 32))
        pygame.draw.line(hud, (45, 55, 72), (0, 0), (0, self.h), width=2)

        y = 12
        # Title
        title = self.font_lg.render("ISLAND ROYALE 2D", True, (255, 215, 0))
        hud.blit(title, (14, y)); y += 28

        # Match State Overview
        alive = self.engine.alive_count
        tot = len(self.engine.by_id)
        stat_lines = [
            f"Tick: {self.engine.tick} / {self.cfg.max_ticks}",
            f"Alive: {alive} / {tot} | Weather: {self.engine.weather.upper()}",
            f"Safe Radius: {self.engine.zone.r:.1f} m",
            f"Speed: {self.speed:.1f}x {'[PAUSED]' if self.paused else '[RUNNING]'}",
        ]
        for l in stat_lines:
            hud.blit(self.font_sm.render(l, True, (200, 210, 225)), (14, y))
            y += 18
        y += 8

        # Leaderboard
        pygame.draw.line(hud, (45, 55, 72), (14, y), (self.hud_w - 14, y))
        y += 8
        hud.blit(self.font_md.render("ROSTER LEADERBOARD", True, (100, 200, 255)), (14, y))
        y += 22

        sorted_roster = sorted(
            self.engine.by_id,
            key=lambda c: (1 if c.alive else 0, c.hp / c.max_hp if c.alive else -c.placement),
            reverse=True
        )

        for c in sorted_roster:
            color = (240, 240, 240) if c.alive else (120, 120, 120)
            if c.id == self.selected_id:
                color = (255, 220, 0)
            status_txt = f"{int(c.hp)}hp" if c.alive else f"P#{c.placement}"
            line = f"#{c.id} {c.name:<10} {status_txt:>6} K:{c.kills}"
            hud.blit(self.font_sm.render(line, True, color), (14, y))
            y += 18
        y += 12

        # Inspector Panel
        pygame.draw.line(hud, (45, 55, 72), (14, y), (self.hud_w - 14, y))
        y += 8
        hud.blit(self.font_md.render("COMBATANT INSPECTOR", True, (100, 200, 255)), (14, y))
        y += 22

        if self.selected_id is not None and self.selected_id < len(self.engine.by_id):
            c = self.engine.by_id[self.selected_id]
            insp_data = [
                f"Species: {c.name} (#{c.id})",
                f"Types: {' / '.join(c.types).upper()}",
                f"Status: {'ALIVE' if c.alive else 'FAINTED'} (Layer: {'AIR' if c.alt else 'GROUND'})",
                f"HP: {c.hp} / {c.max_hp} ({c.hp / c.max_hp * 100:.1f}%)",
                f"Stamina: {c.stamina:.1f} | Energy: {c.energy:.1f}",
                f"AI Mode: {c.mode.upper()} -> Target: {c.target}",
                f"Damage Dealt: {c.dmg_dealt} | Taken: {c.dmg_taken}",
                f"Ability: {c.ability or 'None'} | Item: {c.item or 'None'}",
                f"Position: ({c.x:.1f}, {c.y:.1f}) in {c.biome}",
            ]
            for d in insp_data:
                hud.blit(self.font_sm.render(d, True, (215, 225, 235)), (14, y))
                y += 18
        else:
            hud.blit(self.font_sm.render("Click a Pokémon to inspect", True, (140, 150, 160)), (14, y))
            y += 18

        # Footer Shortcuts
        y = self.h - 40
        hud.blit(self.font_sm.render("[Space] Pause  [1-4] Speed  [H] Help", True, (140, 150, 160)), (14, y))
        self.screen.blit(hud, (self.view_w, 0))

    def _render_help_overlay(self):
        overlay = pygame.Surface((self.view_w, self.view_h), pygame.SRCALPHA)
        overlay.fill((10, 15, 25, 225))
        lines = [
            "POKÉMON ISLAND BATTLE ROYALE — KEYBOARD CONTROLS",
            "--------------------------------------------------",
            "[SPACE]       : Pause / Resume simulation",
            "[.]           : Single tick step (when paused)",
            "[1, 2, 3, 4]  : Speed multiplier (0.5x, 1x, 2x, 5x)",
            "[W, A, S, D]  : Pan camera (or Left-Click Drag)",
            "[+, -] / Wheel: Zoom in / Zoom out",
            "[R]           : Reset camera to island center",
            "[F]           : Focus camera on selected combatant",
            "[Click Unit]  : Inspect combatant details",
            "[H]           : Toggle this help guide",
            "[ESC]         : Exit viewer",
        ]
        y = self.view_h // 2 - (len(lines) * 22) // 2
        for l in lines:
            t = self.font_md.render(l, True, (255, 215, 0) if l.startswith("POKÉMON") else (240, 240, 245))
            overlay.blit(t, (self.view_w // 2 - t.get_width() // 2, y))
            y += 22
        self.screen.blit(overlay, (0, 0))

    # ------------------------------------------------------------------------
    # Event Loop
    # ------------------------------------------------------------------------
    def handle_events(self) -> bool:
        cam = self.camera
        mouse_buttons = pygame.mouse.get_pressed()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return False
                elif event.key == pygame.K_SPACE:
                    self.paused = not self.paused
                elif event.key == pygame.K_PERIOD:
                    if self.paused:
                        self.step_simulation()
                elif event.key == pygame.K_r:
                    isl = self.engine.island
                    cam.cx, cam.cy = isl.cx, isl.cy
                    cam.zoom = min(self.view_w, self.view_h) / (isl.size * 1.15)
                    cam.follow_id = None
                elif event.key == pygame.K_f:
                    if self.selected_id is not None:
                        cam.follow_id = self.selected_id
                elif event.key == pygame.K_h:
                    self.show_help = not self.show_help
                elif event.key == pygame.K_1:
                    self.speed = 0.5
                elif event.key == pygame.K_2:
                    self.speed = 1.0
                elif event.key == pygame.K_3:
                    self.speed = 2.0
                elif event.key == pygame.K_4:
                    self.speed = 5.0
            elif event.type == pygame.MOUSEWHEEL:
                mx, my = pygame.mouse.get_pos()
                if mx < self.view_w:
                    cam.zoom_at(mx, my, 1.15 if event.y > 0 else 0.85)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mx, my = event.pos
                if mx < self.view_w:
                    # Select nearest Pokémon
                    wx, wy = cam.screen_to_world(mx, my)
                    closest_id = None
                    min_dist = 2.5
                    for c in self.engine.by_id:
                        dist = math.sqrt((c.x - wx) ** 2 + (c.y - wy) ** 2)
                        if dist < min_dist:
                            min_dist = dist
                            closest_id = c.id
                    self.selected_id = closest_id
                    if closest_id is not None and cam.follow_id is not None:
                        cam.follow_id = closest_id
            elif event.type == pygame.MOUSEMOTION:
                if mouse_buttons[0] and event.pos[0] < self.view_w:
                    cam.pan(event.rel[0], event.rel[1])

        # Continuous Keyboard Pan
        keys = pygame.key.get_pressed()
        pan_speed = 12
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            cam.pan(pan_speed, 0)
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            cam.pan(-pan_speed, 0)
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            cam.pan(0, pan_speed)
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            cam.pan(0, -pan_speed)

        # Camera follow
        if cam.follow_id is not None and cam.follow_id < len(self.engine.by_id):
            target = self.engine.by_id[cam.follow_id]
            cam.cx += (target.x - cam.cx) * 0.1
            cam.cy += (target.y - cam.cy) * 0.1

        return True

    def run(self):
        """Main interactive visualization loop."""
        running = True
        base_tick_rate = 15.0  # Ticks per second at 1.0x speed

        while running:
            dt = self.clock.tick(60) / 1000.0
            running = self.handle_events()

            if not self.paused and not self.engine.finished:
                self.step_accumulator += dt * (base_tick_rate * self.speed)
                while self.step_accumulator >= 1.0 and not self.engine.finished:
                    self.step_simulation()
                    self.step_accumulator -= 1.0

            self.fx.update()
            self.render()

        pygame.quit()
        return self.engine.result()


# ============================================================================
# 7. COMMAND-LINE ENTRY POINT
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Pokémon Island Battle Royale — 2D Viewer")
    parser.add_argument("--seed", type=int, default=1, help="Match random seed")
    parser.add_argument("-n", type=int, default=4, help="Number of combatants")
    parser.add_argument("--source", default="stub", help="Roster source (stub, synthetic, or builds.json)")
    parser.add_argument("--poc", action="store_true", help="Launch 4-Pokémon proof-of-concept match")
    parser.add_argument("--sprite-dir", default="assets/sprites", help="Path to transparent sprite directory")
    parser.add_argument("--download-poc-sprites", action="store_true", help="Download 4 POC sprites from PokeAPI")
    parser.add_argument("--headless", action="store_true", help="Run without UI window")
    args = parser.parse_args()

    if args.download_poc_sprites:
        print("[*] Downloading proof-of-concept sprites from PokeAPI repository...")
        dl = SpriteManager.download_poc_sprites(args.sprite_dir)
        print(f"[*] Downloaded {len(dl)} sprites to {args.sprite_dir}")

    provider = PocProvider() if args.poc else None
    cfg = Config(seed=args.seed, n_combatants=args.n if not args.poc else 4, roster_source=args.source)

    viewer = IslandViewer(cfg, provider=provider, sprite_dir=args.sprite_dir, headless=args.headless)

    if args.headless:
        print("[*] Running headless match...")
        res = viewer.run_all_headless()
        print(f"[*] Match completed: ticks={res['ticks']}, winner={res['winner']}, digest={res['digest'][:16]}")
    else:
        viewer.run()


if __name__ == "__main__":
    main()