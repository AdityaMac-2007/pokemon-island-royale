#!/usr/bin/env python3
"""Pokémon Island Royale — modern procedural Pygame viewer.

Drop-in companion for the project's existing pkmn_br simulation engine.
Visual effects are cosmetic only: they never modify engine state or RNG.

Run from the repository root:
    python gemini/island_2d.py --poc
    python gemini/island_2d.py --download-poc-sprites --poc
    python gemini/island_2d.py --seed 42 --source stub

Keys: Space play/pause, . single step while paused, 1–4 speed, WASD pan,
mouse wheel zoom, click a Pokémon to inspect, F follow, Tab next living Pokémon,
R replay same seed, N new random seed, H help, Esc quit.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any

import pygame

# ---------------------------------------------------------------------------
# Find the repository's real engine. This supports launching from the project
# root or directly from gemini/island_2d.py, without changing sys.path globally
# outside this process.
# ---------------------------------------------------------------------------

def find_project_root() -> Path:
    here = Path(__file__).resolve()
    candidates = [here.parent.parent, Path.cwd(), *Path.cwd().parents]
    for candidate in candidates:
        if (candidate / "Claude engine" / "pkmn_br" / "engine.py").is_file():
            return candidate
    # Useful, explicit fallback for the expected gemini/ placement.
    return here.parent.parent


PROJECT_ROOT = find_project_root()
ENGINE_ROOT = PROJECT_ROOT / "Claude engine"
if ENGINE_ROOT.is_dir() and str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

try:
    from pkmn_br.config import Config
    from pkmn_br.data import StubProvider
    from pkmn_br.engine import Engine
    from pkmn_br.island import BIOMES
except ImportError as exc:  # do not silently disappear if the engine moved
    raise SystemExit(
        "Could not import the simulation engine. Expected "
        f"'{ENGINE_ROOT / 'pkmn_br'}'. Run from the project root or keep this "
        "script in gemini/island_2d.py.\n"
        f"Original import error: {exc}"
    ) from exc


# PokeAPI national dex numbers for the built-in 24-species prototype roster.
# Unknown/custom species still render as attractive type-coloured tokens.
SPECIES_DEX: dict[str, int] = {
    "bulbasaur": 1, "venusaur": 3, "charizard": 6, "blastoise": 9,
    "pikachu": 25, "alakazam": 65, "arcanine": 59, "gengar": 94,
    "gyarados": 130, "lapras": 131, "snorlax": 143, "dragonite": 149,
    "scizor": 212, "skarmory": 227, "tyranitar": 248, "sceptile": 254,
    "salamence": 373, "latios": 381, "garchomp": 445, "lucario": 448,
    "weavile": 461, "magnezone": 462, "togekiss": 468, "excadrill": 530,
    "ferrothorn": 598, "corviknight": 823, "dragapult": 887,
    "toxapex": 748,
}

POC_SPECIES = {"charizard", "gengar", "latios", "garchomp"}

TYPE_COLORS: dict[str, tuple[int, int, int]] = {
    "normal": (176, 167, 130), "fire": (246, 112, 72),
    "water": (73, 148, 242), "electric": (247, 205, 65),
    "grass": (95, 190, 104), "ice": (110, 210, 224),
    "fighting": (205, 82, 91), "poison": (174, 106, 198),
    "ground": (204, 160, 92), "flying": (137, 161, 237),
    "psychic": (243, 108, 153), "bug": (162, 183, 65),
    "rock": (181, 157, 84), "ghost": (124, 113, 191),
    "dragon": (111, 96, 235), "dark": (105, 96, 91),
    "steel": (154, 170, 186), "fairy": (232, 151, 210),
}

# Biome art palette intentionally differs from the engine's logic values.
BIOME_COLORS: dict[str, tuple[int, int, int]] = {
    "plains": (103, 176, 100),
    "forest": (44, 117, 82),
    "volcanic": (157, 79, 61),
    "wetland": (63, 143, 132),
    "tundra": (173, 211, 211),
    "storm_peak": (94, 112, 164),
    "ruins": (127, 111, 151),
}

UI = {
    "ink": (13, 22, 33),
    "panel": (20, 31, 46),
    "panel2": (27, 42, 59),
    "border": (53, 74, 93),
    "text": (236, 243, 248),
    "muted": (151, 170, 188),
    "mint": (83, 225, 178),
    "gold": (255, 205, 104),
    "red": (255, 104, 112),
    "blue": (100, 178, 255),
}


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def mix_color(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = clamp(t, 0.0, 1.0)
    return tuple(round(a[i] * (1 - t) + b[i] * t) for i in range(3))


def color_jitter(c: tuple[int, int, int], amount: int, rng: random.Random) -> tuple[int, int, int]:
    return tuple(int(clamp(v + rng.randint(-amount, amount), 0, 255)) for v in c)


class PocProvider:
    """A tiny four-species roster using the real engine's official stub builds."""
    def load_roster(self, cfg):
        builds = StubProvider().load_roster(cfg)
        selected = [b for b in builds if slugify(b.species) in POC_SPECIES]
        if len(selected) != len(POC_SPECIES):
            found = {slugify(b.species) for b in selected}
            missing = sorted(POC_SPECIES - found)
            raise RuntimeError(f"POC roster is missing expected stub species: {missing}")
        return selected


class SpriteManager:
    """Loads local Pokémon art and can download it with visible error reporting."""
    BASE_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"

    def __init__(self, sprite_dir: Path):
        self.sprite_dir = Path(sprite_dir).expanduser()
        self.cache: dict[str, pygame.Surface | None] = {}
        self.failed: set[str] = set()

    def _candidates(self, species: str) -> list[Path]:
        slug = slugify(species)
        dex = SPECIES_DEX.get(slug)
        candidates = [
            self.sprite_dir / f"{slug}.png",
            self.sprite_dir / f"{slug}-sprite.png",
            self.sprite_dir / f"{slug}-artwork.png",
            self.sprite_dir / "official-artwork" / f"{slug}.png",
            self.sprite_dir / "sprites" / f"{slug}.png",
        ]
        if dex is not None:
            candidates += [
                self.sprite_dir / f"{dex}.png",
                self.sprite_dir / "official-artwork" / f"{dex}.png",
                self.sprite_dir / "sprites" / f"{dex}.png",
            ]
        return candidates

    def _load_from_disk(self, species: str) -> pygame.Surface | None:
        for path in self._candidates(species):
            if not path.is_file():
                continue
            try:
                surface = pygame.image.load(str(path)).convert_alpha()
                if surface.get_width() > 1 and surface.get_height() > 1:
                    return surface
            except (pygame.error, OSError) as exc:
                print(f"[sprite] Could not load {path}: {exc}", file=sys.stderr)
        return None

    def get(self, species: str) -> pygame.Surface | None:
        key = slugify(species)
        if key not in self.cache:
            self.cache[key] = self._load_from_disk(species)
        return self.cache[key]

    def download_for(self, species_names: list[str]) -> tuple[int, list[str]]:
        self.sprite_dir.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        problems: list[str] = []
        unique = sorted({slugify(name): name for name in species_names}.items())
        for slug, display_name in unique:
            dex = SPECIES_DEX.get(slug)
            if dex is None:
                problems.append(f"{display_name}: no Pokédex number configured for downloader")
                continue
            existing = self._load_from_disk(display_name)
            if existing is not None:
                self.cache[slug] = existing
                continue
            urls = [
                f"{self.BASE_URL}/other/official-artwork/{dex}.png",
                f"{self.BASE_URL}/{dex}.png",
            ]
            payload: bytes | None = None
            last_error = "no source returned an image"
            for url in urls:
                request = urllib.request.Request(
                    url,
                    headers={"User-Agent": "PokemonIslandRoyale/1.0 (local visualizer)"},
                )
                try:
                    with urllib.request.urlopen(request, timeout=8) as response:
                        data = response.read()
                    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                        last_error = f"response was not PNG data ({url})"
                        continue
                    payload = data
                    break
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
            if payload is None:
                problems.append(f"{display_name}: download failed ({last_error})")
                continue
            target = self.sprite_dir / f"{slug}.png"
            try:
                target.write_bytes(payload)
                downloaded += 1
                print(f"[sprite] Downloaded {display_name} -> {target}")
            except OSError as exc:
                problems.append(f"{display_name}: could not save sprite ({exc})")
        # Flush cache so newly written files are immediately visible.
        self.cache.clear()
        if downloaded:
            print(f"[sprite] Downloaded {downloaded} image(s) into {self.sprite_dir}")
        if problems:
            print("[sprite] Some images are unavailable:")
            for problem in problems:
                print(f"  - {problem}")
        if downloaded == 0 and not problems:
            print(f"[sprite] All requested images were already present in {self.sprite_dir}")
        return downloaded, problems


class IslandViewer:
    FPS = 60
    SPEEDS = (18, 42, 90, 170)  # simulation ticks / second; cosmetic frame rate stays 60 FPS

    def __init__(self, cfg: Config, provider: Any, sprite_dir: Path, *, headless: bool = False):
        self.cfg = cfg
        self.provider = provider
        self.sprite_dir = Path(sprite_dir)
        self.headless = headless
        self.seed = cfg.seed
        self.engine = Engine(cfg, provider=provider)
        self.sprites = SpriteManager(self.sprite_dir)
        self.rng = random.Random((self.seed ^ 0x5EEDBEEF) & 0xFFFFFFFF)
        self.width, self.height = 1380, 860
        self.sidebar_width = 292
        self.header_h = 72
        self.footer_h = 44
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.follow_selected = False
        self.playing = True
        self.speed_index = 1
        self.accumulator = 0.0
        self.sim_time = 0.0
        self.selected_id = self.engine.by_id[0].id if self.engine.by_id else None
        self.help_visible = False
        self.running = True
        self.clock = pygame.time.Clock()
        self.fonts: dict[tuple[str, int], pygame.font.Font] = {}
        self.events_seen = 0
        self.feed: deque[tuple[float, str, tuple[int, int, int]]] = deque(maxlen=7)
        self.effects: list[dict[str, Any]] = []
        self.wave_marks: list[tuple[float, float, float, float]] = []
        self.decorations: dict[tuple[int, int], dict[str, Any]] = {}
        self._init_visuals()
        if not self.headless:
            pygame.init()
            pygame.font.init()
            pygame.display.set_caption("Pokémon Island Royale | Field Observatory")
            try:
                self.screen = pygame.display.set_mode((self.width, self.height), pygame.RESIZABLE)
            except pygame.error as exc:
                raise SystemExit(f"Could not create Pygame window: {exc}") from exc
            self._font("body", 16)
            self._font("small", 13)
            self._font("tiny", 11)
            self._font("label", 18)
            self._font("title", 25)
            self._font("hero", 31)
            self._font("number", 22)
            self.events_seen = len(self.engine.log.events)
            self._push_feed("Battle started — autonomous simulation running", UI["mint"])
            print(f"[viewer] Seed: {self.seed}. Press H for controls; N starts a new random seed.")

    def _init_visuals(self) -> None:
        isl = self.engine.island
        # Stable marks and decorations: seeded visuals, with no influence on battles.
        for _ in range(130):
            a = self.rng.random() * math.tau
            d = isl.radius * self.rng.uniform(1.12, 1.85)
            self.wave_marks.append((isl.cx + math.cos(a) * d,
                                    isl.cy + math.sin(a) * d,
                                    self.rng.uniform(0.45, 1.5),
                                    self.rng.uniform(0, math.tau)))
        for gy, row in enumerate(isl.grid):
            for gx, biome in enumerate(row):
                rr = random.Random((self.seed * 1315423911 + gx * 2654435761 + gy * 2246822519) & 0xFFFFFFFF)
                self.decorations[(gx, gy)] = {
                    "color": color_jitter(BIOME_COLORS.get(biome, (90, 150, 100)), 10, rr),
                    "motif": rr.randrange(5),
                    "dots": [(rr.random(), rr.random(), rr.random()) for _ in range(rr.randint(1, 4))],
                    "angle": rr.random() * math.tau,
                    "scale": rr.uniform(0.5, 1.25),
                }

    def _font(self, role: str, size: int) -> pygame.font.Font:
        key = (role, size)
        if key not in self.fonts:
            # Font(None) is deliberately used as a universal fallback for macOS/Linux.
            try:
                self.fonts[key] = pygame.font.Font(None, size)
            except pygame.error:
                pygame.font.init()
                self.fonts[key] = pygame.font.Font(None, size)
        return self.fonts[key]

    def _push_feed(self, message: str, color: tuple[int, int, int] = UI["text"]) -> None:
        self.feed.appendleft((self.sim_time, message, color))

    def _reset(self, seed: int) -> None:
        self.seed = int(seed)
        self.cfg.seed = self.seed
        self.engine = Engine(self.cfg, provider=self.provider)
        self.rng = random.Random((self.seed ^ 0x5EEDBEEF) & 0xFFFFFFFF)
        self.zoom, self.pan_x, self.pan_y = 1.0, 0.0, 0.0
        self.follow_selected = False
        self.selected_id = self.engine.by_id[0].id if self.engine.by_id else None
        self.accumulator = 0.0
        self.sim_time = 0.0
        self.effects.clear()
        self.feed.clear()
        self.wave_marks.clear()
        self.decorations.clear()
        self.events_seen = len(self.engine.log.events)
        self._init_visuals()
        self._push_feed(f"New match · seed {self.seed}", UI["mint"])
        print(f"[viewer] Started match with seed {self.seed}")

    def map_rect(self) -> pygame.Rect:
        return pygame.Rect(16, self.header_h + 14,
                           max(220, self.width - self.sidebar_width - 28),
                           max(200, self.height - self.header_h - self.footer_h - 38))

    def world_scale(self) -> float:
        r = self.map_rect()
        size = self.engine.island.size
        return min(r.width, r.height) / (size * 1.40) * self.zoom

    def world_to_screen(self, x: float, y: float, *, altitude: int = 0) -> tuple[int, int]:
        r = self.map_rect()
        s = self.world_scale()
        px = r.centerx + self.pan_x + (x - self.engine.island.cx) * s
        py = r.centery + self.pan_y + (y - self.engine.island.cy) * s - altitude * s * 0.78
        return round(px), round(py)

    def world_radius(self, r: float) -> int:
        return max(1, round(r * self.world_scale()))

    def _selected(self):
        if self.selected_id is None or not (0 <= self.selected_id < len(self.engine.by_id)):
            return None
        return self.engine.by_id[self.selected_id]

    def _living(self):
        return [c for c in self.engine.by_id if c.alive]

    def _center_on_selected(self) -> None:
        c = self._selected()
        if c is not None:
            s = self.world_scale()
            self.pan_x = -(c.x - self.engine.island.cx) * s
            self.pan_y = -(c.y - self.engine.island.cy) * s

    def _zoom_at(self, point: tuple[int, int], factor: float) -> None:
        """Zoom toward a screen point, preserving the world point under the cursor.

        While following a selected Pokémon, zoom remains centred on that Pokémon.
        Zoom range is deliberately wide enough to inspect individual sprites.
        """
        rect = self.map_rect()
        px, py = point
        old_scale = self.world_scale()
        if old_scale <= 0:
            return
        island = self.engine.island
        if self.follow_selected:
            self.zoom = clamp(self.zoom * factor, 0.45, 6.0)
            self._center_on_selected()
            return
        world_x = island.cx + (px - rect.centerx - self.pan_x) / old_scale
        world_y = island.cy + (py - rect.centery - self.pan_y) / old_scale
        self.zoom = clamp(self.zoom * factor, 0.45, 6.0)
        new_scale = self.world_scale()
        self.pan_x = px - rect.centerx - (world_x - island.cx) * new_scale
        self.pan_y = py - rect.centery - (world_y - island.cy) * new_scale

    def _events_to_visuals(self) -> None:
        events = self.engine.log.events
        if self.events_seen > len(events):
            self.events_seen = 0
        for ev in events[self.events_seen:]:
            kind = ev.get("e", "")
            t = ev.get("t", self.engine.tick)
            if kind == "weather":
                weather = str(ev.get("weather", "clear")).upper()
                self._push_feed(f"Weather shifted · {weather}", UI["blue"])
            elif kind == "hit":
                a_id, d_id = ev.get("a"), ev.get("d")
                a = self.engine.by_id[a_id] if isinstance(a_id, int) and a_id < len(self.engine.by_id) else None
                d = self.engine.by_id[d_id] if isinstance(d_id, int) and d_id < len(self.engine.by_id) else None
                if a is not None and d is not None:
                    self.effects.append({"kind": "hit", "born": self.sim_time, "life": 0.42,
                                         "a": (a.x, a.y), "d": (d.x, d.y),
                                         "dmg": int(ev.get("dmg", 0)), "color": TYPE_COLORS.get(
                                             getattr(ev.get("mv_obj", None), "type", ""), UI["gold"]),
                                         "move": str(ev.get("mv", "ATTACK")).replace("_", " ").upper()})
                    self._push_feed(f"{a.name} → {d.name}  −{int(ev.get('dmg', 0))} HP", UI["gold"])
            elif kind == "elim":
                at = ev.get("at", [0, 0])
                self.effects.append({"kind": "burst", "born": self.sim_time, "life": 0.72,
                                     "x": float(at[0]), "y": float(at[1]), "color": UI["red"]})
                self._push_feed(f"{ev.get('name', 'A Pokémon')} eliminated", UI["red"])
            elif kind == "hazard_warning":
                self.effects.append({"kind": "warning", "born": self.sim_time, "life": 1.0,
                                     "x": float(ev.get("x", 0)), "y": float(ev.get("y", 0)),
                                     "r": float(ev.get("r", 3.5)), "color": UI["gold"],
                                     "label": str(ev.get("kind", "hazard")).upper()})
                self._push_feed(f"Hazard warning · {str(ev.get('kind', 'event')).upper()}", UI["gold"])
            elif kind == "hazard_impact":
                self.effects.append({"kind": "impact", "born": self.sim_time, "life": 0.55,
                                     "x": float(ev.get("x", 0)), "y": float(ev.get("y", 0)),
                                     "r": 6.0, "color": UI["red"]})
                self._push_feed(f"{str(ev.get('kind', 'hazard')).upper()} impact", UI["red"])
            elif kind == "berry":
                c_id = ev.get("id")
                if isinstance(c_id, int) and c_id < len(self.engine.by_id):
                    c = self.engine.by_id[c_id]
                    self.effects.append({"kind": "heal", "born": self.sim_time, "life": 0.6,
                                         "x": c.x, "y": c.y, "color": UI["mint"]})
            elif kind == "takeoff":
                self._push_feed(f"{self.engine.by_id[ev['id']].name} took flight", UI["blue"])
            elif kind == "land":
                self._push_feed(f"{self.engine.by_id[ev['id']].name} landed", UI["muted"])
        self.events_seen = len(events)
        self.effects = [fx for fx in self.effects if self.sim_time - fx["born"] <= fx["life"]]

    def _step_engine(self) -> None:
        if not self.engine.finished:
            self.engine.step()
            if self.engine.finished:
                self.playing = False
                winner = self.engine.winner.name if self.engine.winner else "No winner"
                self._push_feed(f"Match complete · {winner}", UI["mint"])

    def run(self) -> None:
        if self.headless:
            result = self.engine.run()
            print(f"Seed: {result['seed']} | ticks: {result['ticks']} | winner: "
                  f"{self.engine.winner.name if self.engine.winner else 'None'} | digest: {result['digest']}")
            for row in result["rankings"][:10]:
                print(f"#{row['placement']:>2} {row['name']:<14} KOs {row['kills']:>2}  "
                      f"damage {row['damage_dealt']:>5}  HP {row['hp_frac_at_end']:.0%}")
            return

        while self.running:
            dt = min(self.clock.tick(self.FPS) / 1000.0, 0.05)
            self.sim_time += dt
            self._handle_events()
            keys = pygame.key.get_pressed()
            pan_speed = 480 * dt
            if keys[pygame.K_a] or keys[pygame.K_LEFT]: self.pan_x += pan_speed
            if keys[pygame.K_d] or keys[pygame.K_RIGHT]: self.pan_x -= pan_speed
            if keys[pygame.K_w] or keys[pygame.K_UP]: self.pan_y += pan_speed
            if keys[pygame.K_s] or keys[pygame.K_DOWN]: self.pan_y -= pan_speed
            if self.follow_selected:
                self._center_on_selected()
            if self.playing and not self.engine.finished:
                self.accumulator += dt * self.SPEEDS[self.speed_index]
                steps = 0
                while self.accumulator >= 1.0 and steps < 24 and not self.engine.finished:
                    self._step_engine()
                    self.accumulator -= 1.0
                    steps += 1
            self._events_to_visuals()
            self._draw()
            pygame.display.flip()
        pygame.quit()

    def _handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.VIDEORESIZE:
                self.width = max(760, event.w)
                self.height = max(520, event.h)
                self.screen = pygame.display.set_mode((self.width, self.height), pygame.RESIZABLE)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_SPACE:
                    if not self.engine.finished:
                        self.playing = not self.playing
                elif event.key == pygame.K_PERIOD and not self.playing:
                    self._step_engine()
                elif event.key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4):
                    self.speed_index = event.key - pygame.K_1
                elif event.key == pygame.K_f:
                    self.follow_selected = not self.follow_selected
                    if self.follow_selected:
                        self.zoom = clamp(max(self.zoom, 2.0), 0.45, 6.0)
                        self._center_on_selected()
                elif event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                    self._zoom_at(self.map_rect().center, 1.20)
                elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    self._zoom_at(self.map_rect().center, 1.0 / 1.20)
                elif event.key in (pygame.K_0, pygame.K_HOME):
                    self.zoom, self.pan_x, self.pan_y = 1.0, 0.0, 0.0
                    self.follow_selected = False
                elif event.key == pygame.K_TAB:
                    living = self._living()
                    if living:
                        ids = [c.id for c in living]
                        self.selected_id = ids[(ids.index(self.selected_id) + 1) % len(ids)] if self.selected_id in ids else ids[0]
                        if self.follow_selected: self._center_on_selected()
                elif event.key == pygame.K_r:
                    self.playing = True
                    self._reset(self.seed)
                elif event.key == pygame.K_n:
                    self.playing = True
                    self._reset(secrets.randbelow(2**31 - 1) + 1)
                elif event.key == pygame.K_h:
                    self.help_visible = not self.help_visible
            elif event.type == pygame.MOUSEWHEEL:
                point = getattr(event, "pos", None)
                if point is None:
                    point = pygame.mouse.get_pos()
                if self.map_rect().collidepoint(point):
                    self._zoom_at(point, 1.15 ** event.y)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self._select_at(event.pos)

    def _select_at(self, point: tuple[int, int]) -> None:
        px, py = point
        r = self.map_rect()
        if not r.collidepoint(point):
            return
        nearest = None
        nearest_d = 28.0
        for c in self._living():
            x, y = self.world_to_screen(c.x, c.y, altitude=c.alt)
            d = math.hypot(px - x, py - y)
            if d < nearest_d:
                nearest, nearest_d = c, d
        if nearest is not None:
            self.selected_id = nearest.id
            self.follow_selected = False
            self._push_feed(f"Inspecting {nearest.name}", UI["blue"])

    # ------------------------------------------------------------------- draw
    def _draw(self) -> None:
        self.screen.fill(UI["ink"])
        self._draw_header()
        full_clip = self.screen.get_clip()
        self.screen.set_clip(self.map_rect())
        self._draw_ocean_and_island()
        self._draw_zone()
        self._draw_hazards()
        self._draw_berries()
        self._draw_combatants()
        self._draw_effects()
        self._draw_weather()
        self.screen.set_clip(full_clip)
        self._draw_sidebar()
        self._draw_footer()
        if self.help_visible:
            self._draw_help()
        if self.engine.finished:
            self._draw_endcard()

    def _draw_header(self) -> None:
        pygame.draw.rect(self.screen, UI["panel"], (0, 0, self.width, self.header_h))
        pygame.draw.line(self.screen, UI["border"], (0, self.header_h - 1), (self.width, self.header_h - 1), 1)
        # Brand tile
        pygame.draw.rect(self.screen, (35, 66, 77), (16, 13, 42, 42), border_radius=12)
        pygame.draw.circle(self.screen, UI["mint"], (37, 34), 12, 3)
        pygame.draw.line(self.screen, UI["mint"], (25, 34), (49, 34), 2)
        pygame.draw.circle(self.screen, UI["mint"], (37, 34), 3)
        self._text("POKÉMON ISLAND ROYALE", 70, 15, "title", UI["text"])
        self._text("FIELD OBSERVATORY  /  LIVE SIMULATION", 72, 45, "tiny", UI["muted"])

        alive = len(self._living())
        n = len(self.engine.by_id)
        status = "LIVE" if self.playing else ("FINISHED" if self.engine.finished else "PAUSED")
        status_col = UI["mint"] if self.playing else UI["gold"]
        badge = self._render_text(status, "small", status_col)
        if self.width >= 1220:
            metrics = [
                ("SEED", str(self.seed), UI["blue"]),
                ("TICK", f"{self.engine.tick:04d}", UI["text"]),
                ("ALIVE", f"{alive:02d}/{n:02d}", UI["mint"] if alive > 1 else UI["gold"]),
                ("WEATHER", self.engine.weather.upper(), self._weather_color(self.engine.weather)),
            ]
            x = self.width - self.sidebar_width - 15 - 465
            for label, value, col in metrics:
                self._text(label, x, 17, "tiny", UI["muted"])
                self._text(value, x, 35, "body", col)
                x += 112
            bx = self.width - self.sidebar_width - badge.get_width() - 34
        else:
            # At smaller window sizes, suppress secondary metrics rather than
            # letting the header collide with the title or each other.
            self._text(f"TICK {self.engine.tick:04d}", 390, 20, "small", UI["text"])
            self._text(f"ALIVE {alive:02d}/{n:02d}", 390, 42, "small", UI["mint"] if alive > 1 else UI["gold"])
            bx = self.width - badge.get_width() - 28
        pygame.draw.rect(self.screen, UI["panel2"], (bx - 10, 23, badge.get_width() + 20, 28), border_radius=10)
        self.screen.blit(badge, (bx, 29))

    def _draw_ocean_and_island(self) -> None:
        r = self.map_rect()
        old_clip = self.screen.get_clip()
        self.screen.set_clip(r)
        # Subtle water gradient strips and patterned wave dashes.
        for i in range(0, r.height, 4):
            col = mix_color((18, 44, 65), (12, 29, 48), i / max(1, r.height))
            pygame.draw.line(self.screen, col, (r.left, r.top + i), (r.right, r.top + i), 4)
        for wx, wy, length, phase in self.wave_marks:
            sx, sy = self.world_to_screen(wx, wy)
            if not (r.left - 30 <= sx <= r.right + 30 and r.top - 30 <= sy <= r.bottom + 30):
                continue
            pulse = (math.sin(self.sim_time * 1.4 + phase) + 1) * 0.5
            c = mix_color((25, 65, 86), (46, 110, 126), pulse * 0.55)
            half = max(2, round(length * self.world_scale() * 0.75))
            pygame.draw.arc(self.screen, c, (sx - half, sy - 3, half * 2, 7), math.pi * 0.08, math.pi * 0.92, 1)

        isl = self.engine.island
        # Polygon rings are derived from the same seeded coastline as gameplay.
        coast = [self.world_to_screen(x, y) for x, y in isl.coastline_points(segments=180, scale=1.0)]
        beach = [self.world_to_screen(x, y) for x, y in isl.coastline_points(segments=180, scale=1.035)]
        rock = [self.world_to_screen(x, y) for x, y in isl.coastline_points(segments=180, scale=1.065)]
        shadow = [(x + 7, y + 11) for x, y in rock]
        pygame.draw.polygon(self.screen, (6, 18, 27), shadow)
        pygame.draw.polygon(self.screen, (28, 64, 64), rock)
        pygame.draw.polygon(self.screen, (210, 187, 130), beach)
        pygame.draw.polygon(self.screen, (74, 137, 99), coast)

        scale = self.world_scale()
        cell = isl.CELL
        for gy, row in enumerate(isl.grid):
            y0, y1 = gy * cell, (gy + 1) * cell
            for gx, biome in enumerate(row):
                x0, x1 = gx * cell, (gx + 1) * cell
                # Every tile corner must remain inside the shared irregular coastline.
                # This keeps terrain rendering aligned with the simulation boundary.
                corners = ((x0, y0), (x1, y0), (x0, y1), (x1, y1))
                if any(not isl.point_is_land(x, y) for x, y in corners):
                    continue
                sx0, sy0 = self.world_to_screen(x0, y0)
                sx1, sy1 = self.world_to_screen(x1, y1)
                tile = pygame.Rect(min(sx0, sx1), min(sy0, sy1), abs(sx1 - sx0), abs(sy1 - sy0))
                if tile.width < 2 or tile.height < 2 or not r.colliderect(tile):
                    continue
                d = self.decorations[(gx, gy)]
                col = d["color"]
                tile.inflate_ip(-1, -1)
                pygame.draw.rect(self.screen, col, tile, border_radius=max(0, min(5, tile.width // 7)))
                self._draw_tile_motif(tile, biome, d)

        # Coastal foam speckles sample the actual generated shoreline, not a circle.
        foam_points = isl.coastline_points(segments=38, scale=1.012)
        for i, world_point in enumerate(foam_points):
            xx, yy = self.world_to_screen(*world_point)
            if r.collidepoint(xx, yy):
                wobble = 0 if (i + int(self.sim_time * 3)) % 3 else 1
                pygame.draw.circle(self.screen, (222, 237, 216), (xx, yy),
                                   max(1, round(scale * 0.06)) + wobble)
        self.screen.set_clip(old_clip)

    def _draw_tile_motif(self, rect: pygame.Rect, biome: str, deco: dict[str, Any]) -> None:
        if rect.width < 13 or rect.height < 13:
            return
        cx, cy = rect.center
        w, h = rect.width, rect.height
        motif = deco["motif"]
        if biome == "forest":
            count = 1 + motif % 2
            for i in range(count):
                x = rect.left + w * (0.28 + 0.40 * (i + 0.25))
                y = rect.top + h * (0.25 + 0.28 * ((motif + i) % 2))
                pygame.draw.line(self.screen, (94, 70, 48), (round(x), round(y + h * 0.12)), (round(x), round(y + h * 0.28)), 2)
                pygame.draw.circle(self.screen, mix_color((26, 91, 70), (64, 144, 92), (i + 1) / 3), (round(x), round(y)), max(3, w // 8))
        elif biome == "volcanic":
            pts = [(cx - w // 4, cy + h // 5), (cx, cy - h // 4), (cx + w // 4, cy + h // 5)]
            pygame.draw.polygon(self.screen, (91, 57, 55), pts)
            pygame.draw.line(self.screen, (246, 139, 68), (cx, cy - h // 5), (cx + w // 10, cy), max(1, w // 18))
        elif biome == "wetland":
            for fx, fy, _ in deco["dots"][:3]:
                x, y = rect.left + round(fx * w), rect.top + round(fy * h)
                pygame.draw.ellipse(self.screen, (151, 206, 170), (x - 3, y - 1, 7, 3))
            pygame.draw.line(self.screen, (189, 193, 117), (cx + w // 8, cy + h // 5), (cx + w // 8, cy - h // 5), 1)
        elif biome == "tundra":
            pygame.draw.circle(self.screen, (227, 245, 240), (cx - w // 6, cy - h // 7), max(2, w // 12))
            pygame.draw.line(self.screen, (205, 232, 231), (cx, cy + h // 4), (cx + w // 5, cy - h // 5), 2)
        elif biome == "storm_peak":
            pts = [(cx - w // 3, cy + h // 4), (cx - w // 10, cy - h // 3), (cx + w // 3, cy + h // 4)]
            pygame.draw.polygon(self.screen, (69, 83, 127), pts)
            pygame.draw.polygon(self.screen, (207, 224, 234), [(cx - w // 10, cy - h // 3), (cx, cy - h // 10), (cx + w // 12, cy - h // 6)])
        elif biome == "ruins":
            bw, bh = max(4, w // 4), max(4, h // 5)
            pygame.draw.rect(self.screen, (82, 78, 107), (cx - bw // 2, cy - bh // 2, bw, bh), border_radius=2)
            pygame.draw.line(self.screen, (185, 170, 175), (cx - bw // 2, cy - bh // 2), (cx + bw // 2, cy - bh // 2), 1)
        else:  # plains: little clumps of grass, deliberately subtle
            for fx, fy, _ in deco["dots"][:3]:
                x, y = rect.left + round(fx * w), rect.top + round(fy * h)
                pygame.draw.line(self.screen, (177, 211, 124), (x, y + 2), (x - 2, y - 2), 1)
                pygame.draw.line(self.screen, (177, 211, 124), (x, y + 2), (x + 2, y - 2), 1)

    def _draw_zone(self) -> None:
        zone = self.engine.zone
        cx, cy = self.world_to_screen(zone.cx, zone.cy)
        radius = self.world_radius(zone.r)
        r = self.map_rect()
        # Tint unsafe ground, cut the current safe circle out of the tint.
        overlay = pygame.Surface((r.width, r.height), pygame.SRCALPHA)
        overlay.fill((242, 81, 80, 38))
        pygame.draw.circle(overlay, (0, 0, 0, 0), (cx - r.x, cy - r.y), radius)
        self.screen.blit(overlay, r.topleft)
        pygame.draw.circle(self.screen, (236, 247, 223), (cx, cy), radius, max(1, self.world_radius(0.13)))
        pygame.draw.circle(self.screen, (75, 224, 185), (cx, cy), radius, max(2, self.world_radius(0.28)))
        # The next ring is the zone's published safe destination while a hold is active.
        safe = getattr(zone, "safe", None)
        if safe and len(safe) >= 3:
            sx, sy = self.world_to_screen(float(safe[0]), float(safe[1]))
            sr = self.world_radius(float(safe[2]))
            if math.hypot(sx - cx, sy - cy) > 4 or abs(sr - radius) > 4:
                self._dashed_circle((sx, sy), sr, (115, 235, 202), 1)

    def _dashed_circle(self, center: tuple[int, int], radius: int, color: tuple[int, int, int], width: int) -> None:
        if radius < 4:
            return
        for start in range(0, 360, 16):
            end = start + 9
            rect = pygame.Rect(center[0] - radius, center[1] - radius, radius * 2, radius * 2)
            pygame.draw.arc(self.screen, color, rect, math.radians(start), math.radians(end), width)

    def _draw_hazards(self) -> None:
        for h in getattr(self.engine, "active_hazards", []):
            x, y = self.world_to_screen(float(h["x"]), float(h["y"]))
            rad = self.world_radius(float(h.get("r", 3.5)))
            now = self.engine.tick
            remain = int(h.get("impact", now) - now)
            pulse = 0.5 + 0.5 * math.sin(self.sim_time * 7)
            col = (255, 187, 95) if remain > 0 else UI["red"]
            pygame.draw.circle(self.screen, col, (x, y), max(3, rad), 2)
            pygame.draw.circle(self.screen, mix_color(col, UI["ink"], 0.6), (x, y), max(2, round(rad * pulse)), 1)
            if remain >= 0 and remain < 90:
                self._text(str(remain), x + rad + 4, y - 8, "tiny", col)

    def _draw_berries(self) -> None:
        berries = getattr(self.engine.island, "berries", None)
        if berries is None:
            return
        try:
            # pkmn_br.SpatialHash.pos maps berry id -> (x, y, grid_cell).
            # Retain a fallback for alternate versions of the project data layer.
            index = getattr(berries, "pos", None)
            if isinstance(index, dict):
                coords = [(float(value[0]), float(value[1])) for value in index.values()]
            else:
                points = getattr(berries, "points", {})
                coords = [(float(value[0]), float(value[1])) for value in points.values()]
        except (AttributeError, TypeError, ValueError, IndexError):
            # Missing cosmetic berry markers must never break the simulation.
            return
        for x, y in coords:
            px, py = self.world_to_screen(x, y)
            if not self.map_rect().collidepoint(px, py):
                continue
            pulse = 0.5 + 0.5 * math.sin(self.sim_time * 3.4 + x + y)
            rr = max(2, round(2.6 + pulse * 1.5))
            pygame.draw.circle(self.screen, (50, 92, 56), (px + 1, py + 2), rr + 1)
            pygame.draw.circle(self.screen, (100, 240, 166), (px, py), rr)
            pygame.draw.circle(self.screen, (219, 255, 220), (px - 1, py - 1), 1)

    def _draw_combatants(self) -> None:
        scale = self.world_scale()
        living = sorted(self._living(), key=lambda c: (c.alt, c.y))
        for c in living:
            ground_x, ground_y = self.world_to_screen(c.x, c.y)
            bob = math.sin(self.sim_time * (6.2 if c.alt else 8.5) + c.id * 1.7) * (2.8 if c.alt else 1.2)
            x, y = self.world_to_screen(c.x, c.y, altitude=c.alt)
            y += round(bob)
            size = int(clamp(scale * (3.6 if c.alt else 3.15), 28, 58))
            col = TYPE_COLORS.get(c.types[0] if c.types else "normal", (168, 180, 190))
            # Ground shadow, altitude shadow gap, and wing-like orbit marks when airborne.
            shadow_w = max(8, round(size * (0.55 if c.alt else 0.8)))
            pygame.draw.ellipse(self.screen, (22, 37, 38), (ground_x - shadow_w // 2, ground_y - 3, shadow_w, max(4, size // 5)))
            if c.alt:
                orbit = 0.5 + 0.5 * math.sin(self.sim_time * 3 + c.id)
                pygame.draw.arc(self.screen, (160, 205, 224), (x - size // 2, y - size // 3, size, size // 2), 0.15, 2.95, 1)
                if orbit > 0.7:
                    pygame.draw.circle(self.screen, (202, 236, 255), (x + size // 3, y - size // 3), 2)
            pygame.draw.circle(self.screen, (10, 24, 30), (x, y + 2), size // 2 + 3)
            pygame.draw.circle(self.screen, col, (x, y), size // 2 + 1, 2)
            sprite = self.sprites.get(c.name)
            if sprite is not None:
                sw, sh = sprite.get_size()
                factor = min((size - 5) / max(1, sw), (size - 5) / max(1, sh))
                draw_size = (max(1, round(sw * factor)), max(1, round(sh * factor)))
                image = pygame.transform.smoothscale(sprite, draw_size)
                self.screen.blit(image, image.get_rect(center=(x, y)))
            else:
                # Clear, intentionally polished fallback token until a sprite is installed.
                pygame.draw.circle(self.screen, mix_color(col, (17, 29, 40), 0.30), (x, y), size // 2 - 2)
                pygame.draw.circle(self.screen, mix_color(col, (255, 255, 255), 0.35), (x, y), size // 2 - 2, 1)
                initials = self._initials(c.name)
                surf = self._render_text(initials, "small", (255, 255, 255))
                self.screen.blit(surf, surf.get_rect(center=(x, y)))
            # Selection ring and concise status bars.
            is_sel = self.selected_id == c.id
            if is_sel:
                pygame.draw.circle(self.screen, UI["gold"], (x, y), size // 2 + 7, 2)
                pygame.draw.circle(self.screen, (255, 255, 255), (x, y), size // 2 + 10, 1)
            bw = max(28, size + 8)
            bh = 4
            bx, by = x - bw // 2, y + size // 2 + 4
            pygame.draw.rect(self.screen, (20, 29, 38), (bx, by, bw, bh), border_radius=2)
            frac = clamp(c.hp / max(1, c.max_hp), 0, 1)
            hpcol = UI["mint"] if frac > 0.55 else (255, 190, 89) if frac > 0.27 else UI["red"]
            pygame.draw.rect(self.screen, hpcol, (bx, by, max(1, round(bw * frac)), bh), border_radius=2)
            if c.alt:
                stfrac = clamp(c.stamina / max(1.0, self.cfg.stamina_max), 0, 1)
                pygame.draw.rect(self.screen, (34, 45, 61), (bx, by + 5, bw, 2), border_radius=1)
                pygame.draw.rect(self.screen, UI["blue"], (bx, by + 5, max(1, round(bw * stfrac)), 2), border_radius=1)
            if is_sel or len(living) <= 10 or self.zoom >= 1.42:
                label = c.name[:16]
                ts = self._render_text(label, "tiny", UI["text"])
                back = pygame.Rect(x - ts.get_width() // 2 - 5, by + 8, ts.get_width() + 10, ts.get_height() + 3)
                pygame.draw.rect(self.screen, (15, 28, 40), back, border_radius=4)
                self.screen.blit(ts, ts.get_rect(center=back.center))

    def _draw_effects(self) -> None:
        for fx in self.effects:
            age = self.sim_time - fx["born"]
            life = max(0.001, fx["life"])
            p = clamp(age / life, 0, 1)
            color = fx.get("color", UI["gold"])
            if fx["kind"] == "hit":
                ax, ay = self.world_to_screen(*fx["a"])
                dx, dy = self.world_to_screen(*fx["d"])
                tip = (round(ax + (dx - ax) * p), round(ay + (dy - ay) * p))
                pygame.draw.line(self.screen, mix_color(color, (255, 255, 255), 0.35), (ax, ay), tip, max(1, round(3 * (1 - p))))
                pygame.draw.circle(self.screen, color, tip, max(2, round(7 * (1 - p))))
                dmg = self._render_text(str(fx["dmg"]), "small", UI["gold"])
                self.screen.blit(dmg, (dx + 8, dy - 18 - round(p * 20)))
            elif fx["kind"] in ("burst", "impact", "warning", "heal"):
                wx, wy = (fx["x"], fx["y"])
                x, y = self.world_to_screen(wx, wy)
                if fx["kind"] == "warning":
                    rad = self.world_radius(fx.get("r", 3.5)) * (0.85 + p * 0.25)
                    self._dashed_circle((x, y), max(4, rad), color, 2)
                    if p < 0.45:
                        self._text(fx.get("label", "HAZARD"), x - 27, y - rad - 16, "tiny", color)
                else:
                    rad = max(2, round((8 + p * 28) * self.world_scale() / 8))
                    pygame.draw.circle(self.screen, color, (x, y), rad, max(1, round(3 * (1 - p))))
                    if fx["kind"] == "heal":
                        self._text("+", x - 4, y - 12 - round(10 * p), "label", color)

    def _draw_weather(self) -> None:
        weather = self.engine.weather
        r = self.map_rect()
        old_clip = self.screen.get_clip()
        self.screen.set_clip(r)
        if weather == "rain":
            for i in range(42):
                x = r.left + ((i * 83 + int(self.sim_time * 340)) % max(1, r.width))
                y = r.top + ((i * 127 + int(self.sim_time * 590)) % max(1, r.height))
                pygame.draw.line(self.screen, (88, 164, 209), (x, y), (x - 4, y + 9), 1)
        elif weather == "snow":
            for i in range(36):
                x = r.left + ((i * 97 + int(self.sim_time * 24)) % max(1, r.width))
                y = r.top + ((i * 151 + int(self.sim_time * (25 + i % 13))) % max(1, r.height))
                pygame.draw.circle(self.screen, (223, 244, 247), (x, y), 1 + i % 2)
        elif weather == "sand":
            for i in range(34):
                x = r.left + ((i * 71 + int(self.sim_time * 120)) % max(1, r.width))
                y = r.top + ((i * 109 + int(self.sim_time * 56)) % max(1, r.height))
                pygame.draw.circle(self.screen, (219, 188, 123), (x, y), 1)
        elif weather == "sun":
            # Small moving warm glints, not a full-screen opaque filter.
            for i in range(12):
                x = r.left + ((i * 113 + int(self.sim_time * 8)) % max(1, r.width))
                y = r.top + ((i * 181 + int(self.sim_time * 5)) % max(1, r.height))
                pygame.draw.circle(self.screen, (255, 210, 125), (x, y), 1)
        self.screen.set_clip(old_clip)

    def _draw_sidebar(self) -> None:
        x0 = self.width - self.sidebar_width - 8
        panel = pygame.Rect(x0, self.header_h + 14, self.sidebar_width - 8,
                            max(200, self.height - self.header_h - self.footer_h - 38))
        pygame.draw.rect(self.screen, UI["panel"], panel, border_radius=16)
        pygame.draw.rect(self.screen, UI["border"], panel, 1, border_radius=16)
        inner_x = panel.x + 16
        inner_w = panel.width - 32
        y = panel.y + 16
        self._text("MATCH INTEL", inner_x, y, "small", UI["muted"])
        y += 28
        alive = self._living()
        alive_sorted = sorted(alive, key=lambda c: (-c.kills, -(c.hp / max(1, c.max_hp)), c.id))
        self._text(f"{len(alive)}  COMBATANTS ACTIVE", inner_x, y, "label", UI["text"])
        y += 31
        # compact alive bars
        for c in alive_sorted[:5]:
            frac = clamp(c.hp / max(1, c.max_hp), 0, 1)
            col = TYPE_COLORS.get(c.types[0] if c.types else "normal", UI["blue"])
            self.screen.blit(self._render_text(c.name[:15], "small", UI["text"]), (inner_x, y))
            self._text(f"{c.kills} KO", panel.right - 62, y, "tiny", UI["gold"])
            y += 19
            pygame.draw.rect(self.screen, (38, 54, 69), (inner_x, y, inner_w, 5), border_radius=3)
            hpcol = UI["mint"] if frac > .5 else UI["gold"] if frac > .25 else UI["red"]
            pygame.draw.rect(self.screen, hpcol, (inner_x, y, max(2, int(inner_w * frac)), 5), border_radius=3)
            y += 13
        y += 8
        self._line(inner_x, y, inner_w)
        y += 14
        c = self._selected()
        self._text("SELECTED", inner_x, y, "small", UI["muted"])
        y += 24
        if c is None:
            self._text("Click a Pokémon", inner_x, y, "body", UI["text"])
            return
        ccol = TYPE_COLORS.get(c.types[0] if c.types else "normal", UI["blue"])
        self._text(c.name[:21], inner_x, y, "label", UI["text"])
        y += 24
        type_line = " / ".join(str(t).upper() for t in c.types) or "UNKNOWN"
        self._text(type_line, inner_x, y, "tiny", ccol)
        y += 18
        ability_name = str(getattr(c, "ability", None) or "None").replace("_", " ").title()
        self._text(f"ABILITY  {ability_name}"[:32], inner_x, y, "tiny", UI["gold"])
        y += 16
        biome_name = str(getattr(c, "biome", "unknown")).replace("_", " ").title()
        self._text(f"BIOME  {biome_name}"[:32], inner_x, y, "tiny", UI["muted"])
        y += 16
        self._text(f"POS  {c.x:.1f}, {c.y:.1f}  ALT {c.alt}", inner_x, y, "tiny", UI["muted"])
        y += 18
        status = "AIRBORNE" if c.alt else "GROUNDED"
        if c.status: status += f" · {str(c.status).upper()}"
        self._text(status, inner_x, y, "small", UI["blue"] if c.alt else UI["muted"])
        y += 23
        stats = [("HP", c.hp, c.max_hp), ("STAMINA", round(c.stamina), self.cfg.stamina_max)]
        for label, value, maximum in stats:
            self._text(label, inner_x, y, "tiny", UI["muted"])
            self._text(f"{value}/{int(maximum)}", panel.right - 78, y, "tiny", UI["text"])
            y += 16
            pct = clamp(float(value) / max(1.0, float(maximum)), 0, 1)
            pygame.draw.rect(self.screen, (39, 55, 69), (inner_x, y, inner_w, 5), border_radius=3)
            color = UI["mint"] if label == "HP" and pct > .4 else UI["gold"] if label == "HP" else UI["blue"]
            pygame.draw.rect(self.screen, color, (inner_x, y, max(2, round(inner_w * pct)), 5), border_radius=3)
            y += 17
        self._text(f"KOs  {c.kills}    DMG  {c.dmg_dealt}", inner_x, y, "small", UI["gold"])
        y += 25
        self._line(inner_x, y, inner_w)
        y += 13
        self._text("MOVESET", inner_x, y, "small", UI["muted"])
        y += 20
        for mv in c.moves[:4]:
            name = str(getattr(mv, "name", mv)).replace("_", " ").title()[:22]
            typ = str(getattr(mv, "type", "normal"))
            self._text(name, inner_x, y, "small", UI["text"])
            self._text(typ[:3].upper(), panel.right - 49, y, "tiny", TYPE_COLORS.get(typ, UI["muted"]))
            y += 19
        y += 9
        self._line(inner_x, y, inner_w)
        y += 13
        self._text("FIELD FEED", inner_x, y, "small", UI["muted"])
        y += 19
        for _, message, color in list(self.feed)[:5]:
            short = message if len(message) <= 31 else message[:28] + "…"
            self._text(short, inner_x, y, "tiny", color)
            y += 17

    def _draw_footer(self) -> None:
        y = self.height - self.footer_h
        pygame.draw.rect(self.screen, UI["panel"], (0, y, self.width, self.footer_h))
        pygame.draw.line(self.screen, UI["border"], (0, y), (self.width, y), 1)
        controls = ["SPACE  pause", ".  step", "1–4  speed", "WASD  pan", "SCROLL  zoom", "CLICK  inspect", "F  follow", "N  new seed", "H  help"]
        x = 18
        for text in controls:
            surf = self._render_text(text, "tiny", UI["muted"])
            if x + surf.get_width() > self.width - 10:
                break
            self.screen.blit(surf, (x, y + 15))
            x += surf.get_width() + 18
        if self.playing:
            rate = self.SPEEDS[self.speed_index]
            self._text(f"{rate} ticks/s", max(18, self.width - self.sidebar_width - 120), y + 13, "tiny", UI["mint"])

    def _draw_help(self) -> None:
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((4, 10, 18, 195))
        self.screen.blit(overlay, (0, 0))
        w, h = 560, 390
        rect = pygame.Rect((self.width - w) // 2, (self.height - h) // 2, w, h)
        pygame.draw.rect(self.screen, UI["panel"], rect, border_radius=18)
        pygame.draw.rect(self.screen, UI["border"], rect, 1, border_radius=18)
        self._text("FIELD CONTROLS", rect.x + 28, rect.y + 24, "title", UI["text"])
        lines = [
            ("SPACE", "Pause or resume the autonomous battle"),
            (".", "Advance exactly one simulation tick while paused"),
            ("1–4", "Change simulation speed"),
            ("WASD / ARROWS", "Pan the camera around the island"),
            ("MOUSE WHEEL", "Zoom at cursor (0.45x to 6x)"),
            ("F / TAB", "Focus-follow / select next living Pokémon"),
            ("+ / - / 0", "Zoom in / out / reset full-island view"),
            ("MOUSE WHEEL", "Zoom in / out"),
            ("CLICK POKÉMON", "Inspect a combatant in the right panel"),
            ("F / TAB", "Follow selected / select next survivor"),
            ("R / N", "Replay this seed / launch a new random seed"),
            ("ESC", "Quit the viewer"),
        ]
        y = rect.y + 71
        for key, desc in lines:
            self._text(key, rect.x + 28, y, "small", UI["mint"])
            self._text(desc, rect.x + 175, y, "small", UI["text"])
            y += 31
        self._text("Cosmetic animations never change match outcomes.", rect.x + 28, rect.bottom - 34, "tiny", UI["muted"])

    def _draw_endcard(self) -> None:
        r = self.map_rect()
        overlay = pygame.Surface((r.width, r.height), pygame.SRCALPHA)
        overlay.fill((8, 18, 28, 115))
        self.screen.blit(overlay, r.topleft)
        winner = self.engine.winner
        name = winner.name if winner else "No winner"
        card = pygame.Rect(0, 0, min(420, r.width - 30), 170)
        card.center = r.center
        pygame.draw.rect(self.screen, UI["panel"], card, border_radius=18)
        pygame.draw.rect(self.screen, UI["mint"], card, 2, border_radius=18)
        self._text("CHAMPION", card.centerx - 52, card.y + 22, "small", UI["gold"])
        name_surf = self._render_text(name, "hero", UI["text"])
        self.screen.blit(name_surf, name_surf.get_rect(center=(card.centerx, card.y + 73)))
        detail = f"{self.engine.tick} ticks   ·   {winner.kills if winner else 0} KOs   ·   seed {self.seed}"
        det = self._render_text(detail, "small", UI["muted"])
        self.screen.blit(det, det.get_rect(center=(card.centerx, card.y + 119)))
        self._text("Press N for another island", card.centerx - 75, card.y + 141, "tiny", UI["mint"])

    # --------------------------------------------------------------- utilities
    def _line(self, x: int, y: int, width: int) -> None:
        pygame.draw.line(self.screen, UI["border"], (x, y), (x + width, y), 1)

    def _render_text(self, text: str, role: str = "body", color: tuple[int, int, int] = UI["text"]) -> pygame.Surface:
        size = {"tiny": 11, "small": 13, "body": 16, "label": 18, "title": 25, "hero": 31, "number": 22}.get(role, 16)
        return self._font(role, size).render(str(text), True, color)

    def _text(self, text: str, x: int, y: int, role: str = "body", color: tuple[int, int, int] = UI["text"]) -> None:
        self.screen.blit(self._render_text(text, role, color), (int(x), int(y)))

    @staticmethod
    def _initials(name: str) -> str:
        words = [w for w in re.split(r"[^A-Za-z0-9]+", name) if w]
        return ("".join(w[0] for w in words[:2]) or "?").upper()[:2]

    @staticmethod
    def _weather_color(weather: str) -> tuple[int, int, int]:
        return {"clear": UI["mint"], "sun": UI["gold"], "rain": UI["blue"],
                "snow": (199, 232, 245), "sand": (224, 183, 118)}.get(weather, UI["text"])


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Modern 2D observer for Pokémon Island Royale")
    parser.add_argument("--seed", type=int, default=None,
                        help="reproducible seed (default: random seed each launch)")
    parser.add_argument("-n", "--combatants", type=int, default=None,
                        help="number of combatants (default: 4 for --poc, otherwise 24)")
    parser.add_argument("--source", default="stub",
                        help="stub, synthetic, or a path to builds.json (ignored by --poc)")
    parser.add_argument("--poc", action="store_true", help="run the four-species proof-of-concept roster")
    parser.add_argument("--sprite-dir", default=None,
                        help="sprite directory (default: assets/sprites in the repository)")
    parser.add_argument("--download-poc-sprites", action="store_true",
                        help="download art for the current roster into the sprite directory")
    parser.add_argument("--download-sprites", action="store_true", dest="download_poc_sprites",
                        help=argparse.SUPPRESS)
    parser.add_argument("--headless", action="store_true", help="run the match in the terminal without a window")
    parser.add_argument("--paused", action="store_true", help="open paused rather than auto-running")
    parser.add_argument("--ruleset", choices=("canonical", "chaos"), default="canonical")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    seed = args.seed if args.seed is not None else secrets.randbelow(2**31 - 1) + 1
    n = args.combatants if args.combatants is not None else (len(POC_SPECIES) if args.poc else 24)
    if n < 2:
        raise SystemExit("The battle royale needs at least two combatants.")
    if args.poc and n > len(POC_SPECIES):
        raise SystemExit(f"--poc has only {len(POC_SPECIES)} species; use -n {len(POC_SPECIES)} or omit --poc.")
    provider = PocProvider() if args.poc else None
    cfg = Config(seed=seed, n_combatants=n, roster_source=args.source, ruleset=args.ruleset)
    sprite_dir = Path(args.sprite_dir).expanduser() if args.sprite_dir else PROJECT_ROOT / "assets" / "sprites"

    if not args.headless:
        pygame.init()
        pygame.font.init()
    # Construct with the actual engine, so assets are keyed to the species selected
    # for this specific seed/roster and no duplicate independent simulation is created.
    viewer = IslandViewer(cfg, provider=provider, sprite_dir=sprite_dir, headless=args.headless)
    if args.paused:
        viewer.playing = False
    if args.download_poc_sprites:
        names = [c.name for c in viewer.engine.by_id]
        viewer.sprites.download_for(names)
        # Drop any earlier negative cache entries after download.
        viewer.sprites.cache.clear()
    elif not args.headless:
        loaded = sum(1 for c in viewer.engine.by_id if viewer.sprites.get(c.name) is not None)
        print(f"[sprite] Found local artwork for {loaded}/{len(viewer.engine.by_id)} selected combatants.")
        if loaded < len(viewer.engine.by_id):
            print(f"[sprite] Put PNGs in {sprite_dir} or relaunch with --download-poc-sprites.")
    viewer.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
