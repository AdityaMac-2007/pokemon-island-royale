"""Regression tests for Tactical AI v1 and the legacy golden baseline."""
from __future__ import annotations

import json
import os
import random
import unittest
from unittest.mock import patch

import pkmn_br.rng as rng_mod
from pkmn_br import ai
from pkmn_br.config import Config
from pkmn_br.engine import Engine
from pkmn_br.island import BIOMES
from pkmn_br.log import legacy_digest


def _verify_golden_entry(test_case: unittest.TestCase, entry: dict) -> None:
    """Check every output stored by the project's original golden fixture."""
    cfg = Config.from_dict(entry["cfg"])
    cfg.island_shape = "circle"  # golden fixture records pre-irregular geometry
    engine = Engine(cfg)
    result = engine.run()

    test_case.assertEqual(
        legacy_digest(engine.log.events),
        entry["legacy_digest"],
        f"Legacy digest mismatch for seed={cfg.seed}",
    )
    test_case.assertEqual(result["winner"], entry["winner"])
    test_case.assertEqual(result["ticks"], entry["ticks"])
    ranked = sorted(result["rankings"], key=lambda row: row["placement"])
    test_case.assertEqual([row["id"] for row in ranked], entry["order"])
    test_case.assertEqual([row["damage_dealt"] for row in ranked], entry["dmg"])


class TestTacticalAIV1(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(seed=42, n_combatants=8)
        self.eng = Engine(self.cfg)
        for combatant in self.eng.by_id:
            combatant.ai_policy = "tactical_v1"

    def test_mandatory_golden_legacy_fixture_compatibility(self):
        path = os.path.join(os.path.dirname(__file__), "golden_legacy.json")
        self.assertTrue(os.path.isfile(path), f"Mandatory golden fixture missing: {path}")
        with open(path, encoding="utf-8") as f:
            golden = json.load(f)

        checked = 0
        if isinstance(golden, list):
            entries = golden
        elif isinstance(golden, dict) and "legacy_digest" in golden and "cfg" in golden:
            entries = [golden]
        elif isinstance(golden, dict) and isinstance(golden.get("cases"), list):
            entries = golden["cases"]
        elif isinstance(golden, dict):
            entries = []
            for value in golden.values():
                if isinstance(value, dict) and "legacy_digest" in value:
                    entries.append(value)
                elif isinstance(value, list):
                    entries.extend(x for x in value if isinstance(x, dict) and "legacy_digest" in x)
        else:
            entries = []

        for entry in entries:
            if isinstance(entry, dict) and "legacy_digest" in entry:
                _verify_golden_entry(self, entry)
                checked += 1
        self.assertGreater(checked, 0, "golden_legacy.json contained no verifiable legacy digest entries")

    def test_engine_and_ai_stamina_drain_parity(self):
        """The engine and AI must use the same updraft predicate and stamina drain."""
        for name, biome in BIOMES.items():
            if "updraft" in biome and "note" in biome:
                self.assertEqual(
                    bool(biome.get("updraft", False)),
                    str(biome.get("note", "")).startswith("updrafts"),
                    f"Inconsistent updraft metadata for biome {name!r}",
                )

        normal_biome = next((name for name, b in BIOMES.items() if not ai._biome_has_updraft(b)), None)
        updraft_biome = next((name for name, b in BIOMES.items() if ai._biome_has_updraft(b)), None)
        self.assertIsNotNone(normal_biome, "BIOMES must define at least one normal biome")
        self.assertIsNotNone(updraft_biome, "BIOMES must define at least one updraft biome")

        flyer = next((c for c in self.eng.by_id if c.can_fly), None)
        self.assertIsNotNone(flyer, "Test roster must contain a flying combatant")
        for other in self.eng.by_id:
            if other.id != flyer.id:
                other.alive = False
        self.eng.active_hazards = []
        flyer.x, flyer.y = self.eng.zone.cx, self.eng.zone.cy
        flyer.alt = 1

        flyer.biome = normal_biome
        flyer.stamina = 50.0
        normal_drain = ai.stamina_drain_rate(flyer)
        self.assertEqual(normal_drain, 1.0)
        self.eng._phase_environment(1)
        self.assertEqual(flyer.stamina, 50.0 - normal_drain)

        flyer.biome = updraft_biome
        flyer.alt = 1
        flyer.stamina = 50.0
        updraft_drain = ai.stamina_drain_rate(flyer)
        self.assertEqual(updraft_drain, 0.5)
        self.eng._phase_environment(2)
        self.assertEqual(flyer.stamina, 50.0 - updraft_drain)

    def test_public_p_win_and_best_move_respect_legacy_default(self):
        engine = Engine(Config(seed=7, n_combatants=4))
        attacker, defender = engine.by_id[0], engine.by_id[1]
        attacker.hp, attacker.max_hp = 15, 100
        defender.hp, defender.max_hp = 250, 250
        defender.ability = "sturdy"
        defender.sturdy_used = False

        self.assertEqual(ai.p_win(attacker, defender, 300.0, 120.0, cfg=engine.cfg),
                         ai.p_win_legacy(attacker, defender, 300.0, 120.0))
        self.assertEqual(ai.best_move(engine, attacker, defender),
                         ai.best_move_legacy(engine, attacker, defender))

        attacker.ai_policy = "tactical_v1"
        self.assertEqual(ai.p_win(attacker, defender, 300.0, 120.0, cfg=engine.cfg),
                         ai.p_win_tactical(attacker, defender, 300.0, 120.0, cfg=engine.cfg))
        self.assertEqual(ai.best_move(engine, attacker, defender),
                         ai.best_move_tactical(engine, attacker, defender))

    def test_strict_rng_purity_and_order_independence(self):
        def forbid_rng(*_args, **_kwargs):
            raise AssertionError("ai.decide must not instantiate or advance an RNG stream")

        for policy in ("legacy", "tactical_v1"):
            for combatant in self.eng.by_id:
                combatant.ai_policy = policy
            with patch.object(rng_mod, "stream", side_effect=forbid_rng), \
                 patch.object(random.Random, "random", side_effect=forbid_rng):
                forward = [ai.decide(self.eng, c, 10) for c in self.eng.by_id if c.alive]
                reverse = [ai.decide(self.eng, c, 10) for c in reversed(self.eng.by_id) if c.alive]
                reverse.reverse()
            self.assertEqual(forward, reverse, f"Order dependence under policy={policy}")

    def test_hazard_altitude_and_low_stamina_edge_cases(self):
        flyer = next((c for c in self.eng.by_id if c.can_fly), None)
        self.assertIsNotNone(flyer, "Test roster must contain a flying combatant")
        for other in self.eng.by_id:
            if other.id != flyer.id:
                other.alive = False
        self.eng._phase_index()

        normal_biome = next((name for name, b in BIOMES.items() if not ai._biome_has_updraft(b)), None)
        updraft_biome = next((name for name, b in BIOMES.items() if ai._biome_has_updraft(b)), None)
        self.assertIsNotNone(normal_biome)
        self.assertIsNotNone(updraft_biome)
        flyer.biome = normal_biome
        flyer.x, flyer.y = self.eng.island.cx, self.eng.island.cy
        flyer.hp = int(flyer.max_hp * 0.45)
        self.eng.active_hazards = [{
            "kind": "fissure", "x": flyer.x, "y": flyer.y, "r": 6.0,
            "dmg": 0.40, "immune": [], "air_immune": True, "warn": 0, "impact": 15,
        }]

        flyer.alt, flyer.stamina = 1, 10.5
        enough = ai.decide(self.eng, flyer, 5)
        self.assertEqual(enough.alt, 1)
        self.assertNotEqual(enough.mode, "evade")
        self.assertNotEqual(enough.mode, "forage")

        flyer.stamina = 10.0
        short = ai.decide(self.eng, flyer, 5)
        self.assertEqual(short.mode, "evade")
        self.assertEqual(short.alt, 1)

        flyer.biome, flyer.alt, flyer.stamina = updraft_biome, 1, 5.5
        up_enough = ai.decide(self.eng, flyer, 5)
        self.assertEqual(up_enough.alt, 1)
        self.assertNotEqual(up_enough.mode, "evade")
        flyer.stamina = 5.0
        up_short = ai.decide(self.eng, flyer, 5)
        self.assertEqual(up_short.mode, "evade")
        self.assertEqual(up_short.alt, 1)

        flyer.biome, flyer.alt = normal_biome, 1
        for low_stamina in (1.0, 2.0):
            flyer.stamina = low_stamina
            low = ai.decide(self.eng, flyer, 5)
            self.assertEqual(low.mode, "evade")
            self.assertEqual(low.alt, 1)

        self.eng.active_hazards[0]["impact"] = 15
        flyer.alt, flyer.stamina = 0, 40.0
        takeoff_min_ok = ai.decide(self.eng, flyer, 5)
        self.assertEqual(takeoff_min_ok.alt, 1)
        self.assertNotEqual(takeoff_min_ok.mode, "evade")
        flyer.stamina = 39.9
        takeoff_min_short = ai.decide(self.eng, flyer, 5)
        self.assertEqual(takeoff_min_short.mode, "evade")
        self.assertEqual(takeoff_min_short.alt, 0)

        self.eng.active_hazards[0]["impact"] = 45
        flyer.alt, flyer.stamina = 0, 48.5
        endurance_ok = ai.decide(self.eng, flyer, 5)
        self.assertEqual(endurance_ok.alt, 1)
        self.assertNotEqual(endurance_ok.mode, "evade")
        flyer.stamina = 48.0
        endurance_short = ai.decide(self.eng, flyer, 5)
        self.assertEqual(endurance_short.mode, "evade")
        self.assertEqual(endurance_short.alt, 0)

    def test_priority_heuristic_adjustment_in_p_win_tactical(self):
        a, d = self.eng.by_id[0], self.eng.by_id[1]
        a.hp = a.max_hp = 100
        d.hp = d.max_hp = 100
        a.stats["spe"], d.stats["spe"] = 90, 100
        a.energy = d.energy = 100.0
        neutral = ai.p_win_tactical(a, d, 120.0, 120.0, cfg=self.cfg, pri_a=0, pri_d=0)
        friendly_priority = ai.p_win_tactical(a, d, 120.0, 120.0, cfg=self.cfg, pri_a=1, pri_d=0)
        enemy_priority = ai.p_win_tactical(a, d, 120.0, 120.0, cfg=self.cfg, pri_a=0, pri_d=1)
        self.assertGreater(friendly_priority, neutral)
        self.assertGreater(friendly_priority, 0.55)
        self.assertLess(enemy_priority, neutral)

    def test_sturdy_awareness_in_p_win(self):
        attacker, defender = self.eng.by_id[0], self.eng.by_id[1]
        attacker.hp = attacker.max_hp = 100
        defender.hp = defender.max_hp = 250
        defender.ability = "sturdy"
        defender.sturdy_used = False
        attacker.stats["spe"], defender.stats["spe"] = 100, 90
        attacker.energy = defender.energy = 0.0
        with_sturdy = ai.p_win_tactical(attacker, defender, 300.0, 120.0, cfg=self.cfg)
        defender.sturdy_used = True
        without_sturdy = ai.p_win_tactical(attacker, defender, 300.0, 120.0, cfg=self.cfg)
        self.assertLess(with_sturdy, 0.40)
        self.assertGreater(without_sturdy, 0.50)

    def test_ranged_kites_melee_threat(self):
        """Isolate kiting: use a matchup with viable ranged/melee attacks and force hunt mode."""
        original_moves = {c.id: list(c.moves) for c in self.eng.by_id}
        pair = None

        # Do not depend on whichever species happens to appear first in the roster.
        # Some candidates have ranged moves that are immune against the first melee
        # candidate, or the candidate matchup is rationally judged unfavorable.
        for ranged_candidate in self.eng.by_id:
            ranged_moves = [
                m for m in original_moves[ranged_candidate.id]
                if m.reach == "ranged" and m.category != "status"
            ]
            if not ranged_moves:
                continue

            for melee_candidate in self.eng.by_id:
                if melee_candidate.id == ranged_candidate.id:
                    continue
                melee_moves = [
                    m for m in original_moves[melee_candidate.id]
                    if m.reach == "melee" and m.category != "status"
                ]
                if not melee_moves:
                    continue

                # Configure the candidate moves first, then require positive damage
                # in both directions so the test actually reaches the kiting branch.
                ranged_candidate.moves = list(ranged_moves)
                melee_candidate.moves = list(melee_moves)
                ranged_candidate.utility = []
                melee_candidate.utility = []
                ranged_candidate.alt = 0
                melee_candidate.alt = 0
                ranged_mi, ranged_damage = ai.best_move_tactical(
                    self.eng, ranged_candidate, melee_candidate
                )
                melee_mi, melee_damage = ai.best_move_tactical(
                    self.eng, melee_candidate, ranged_candidate
                )
                if (ranged_mi >= 0 and ranged_damage > 0
                        and melee_mi >= 0 and melee_damage > 0):
                    pair = (ranged_candidate, melee_candidate)
                    break
            if pair is not None:
                break

        self.assertIsNotNone(
            pair,
            "Test roster must contain a matchup with effective ranged and melee attacks",
        )
        ranged, melee = pair

        for other in self.eng.by_id:
            if other.id not in (ranged.id, melee.id):
                other.alive = False

        ranged.x, ranged.y = self.eng.island.cx, self.eng.island.cy
        melee.x, melee.y = ranged.x + 2.0, ranged.y
        ranged.hp = ranged.max_hp
        melee.hp = 40
        ranged.alt = melee.alt = 0
        ranged.utility = []
        melee.utility = []

        # This test is specifically about the kiting branch once hunting has been
        # selected, not about calibrating the flee threshold for arbitrary species.
        ranged.bravery = 1.0
        self.eng._phase_index()

        # Assert the intended kiting preconditions before testing the resulting intent.
        ranged_mi, ranged_damage = ai.best_move_tactical(self.eng, ranged, melee)
        melee_mi, melee_damage = ai.best_move_tactical(self.eng, melee, ranged)
        self.assertGreater(ranged_damage, 0.0)
        self.assertGreater(melee_damage, 0.0)
        self.assertEqual(ranged.moves[ranged_mi].reach, "ranged")
        self.assertEqual(melee.moves[melee_mi].reach, "melee")

        intent = ai.decide(self.eng, ranged, 1)
        self.assertEqual(intent.mode, "hunt")
        self.assertIsNotNone(intent.attack)
        self.assertIsNotNone(intent.goal)
        self.assertGreater(
            ai._dist(intent.goal[0], intent.goal[1], melee.x, melee.y),
            2.0,
        )

    def test_airborne_flyer_lands_for_berry_when_hurt_and_safe(self):
        flyer = next((c for c in self.eng.by_id if c.can_fly), None)
        self.assertIsNotNone(flyer, "Test roster must contain a flying combatant")
        for other in self.eng.by_id:
            if other.id != flyer.id:
                other.alive = False
        zx, zy, zr = self.eng.zone.safe
        safe_berries = [
            (bid, x, y) for bid, (x, y, _) in self.eng.island.berries.pos.items()
            if (x - zx) ** 2 + (y - zy) ** 2 < (zr - 2.0) ** 2
        ]
        self.assertTrue(safe_berries, "Test island must have a berry within the safe zone")
        bid, bx, by = min(safe_berries, key=lambda item: (item[1] - zx) ** 2 + (item[2] - zy) ** 2)
        flyer.alt = 1
        flyer.stamina = 80.0
        flyer.hp = int(flyer.max_hp * 0.50)
        flyer.x, flyer.y = bx + 1.0, by
        self.eng.active_hazards = []
        self.eng._phase_index()
        intent = ai.decide(self.eng, flyer, 1)
        self.assertEqual(intent.mode, "forage")
        self.assertEqual(intent.alt, 0)
        self.assertEqual(intent.goal, (bx, by))


class SafeZoneKitingBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(seed=42, n_combatants=8)
        self.eng = Engine(self.cfg)

    def test_kiting_goal_respects_effective_safe_radius(self):
        """A feasible perimeter retreat must remain inside the effective radius."""
        ranged = self.eng.by_id[0]
        zx, zy, zr = self.eng.zone.safe
        margin = 0.5
        safe_r = max(1.0, zr - margin)

        # Move the combatant near the right perimeter. The opponent is inward,
        # so an unrestricted retreat would point outward.
        ranged.x = zx + zr - 1.0
        ranged.y = zy
        opponent_x = ranged.x - 2.0
        opponent_y = zy

        goal = ai._safe_kite_goal(
            self.eng, ranged, opponent_x, opponent_y,
            zx, zy, zr, dist=4.5, margin=margin,
        )

        goal_dist_sq = (goal[0] - zx) ** 2 + (goal[1] - zy) ** 2
        self.assertLessEqual(
            goal_dist_sq,
            safe_r * safe_r + 1e-9,
            f"Kiting goal {goal} exceeds effective safe radius {safe_r}",
        )

        separation = ai._dist(goal[0], goal[1], opponent_x, opponent_y)
        self.assertGreater(separation, 2.0)

    def test_kiting_outside_zone_makes_deterministic_inward_progress(self):
        """An outside combatant should retreat inward when no immediate safe
        destination exists; it need not return to safety in a single step."""
        ranged = self.eng.by_id[0]
        zx, zy, zr = self.eng.zone.safe

        ranged.x = zx + zr + 6.0
        ranged.y = zy
        opponent_x = ranged.x - 1.5
        opponent_y = zy

        start = (ranged.x, ranged.y)
        goal1 = ai._safe_kite_goal(
            self.eng, ranged, opponent_x, opponent_y, zx, zy, zr,
            dist=4.5, margin=0.5,
        )
        goal2 = ai._safe_kite_goal(
            self.eng, ranged, opponent_x, opponent_y, zx, zy, zr,
            dist=4.5, margin=0.5,
        )

        self.assertEqual(goal1, goal2)

        start_dist = ai._dist(start[0], start[1], zx, zy)
        goal_dist = ai._dist(goal1[0], goal1[1], zx, zy)

        self.assertLess(goal_dist, start_dist)
        self.assertAlmostEqual(start_dist - goal_dist, 4.5, places=5)



if __name__ == "__main__":
    unittest.main()
