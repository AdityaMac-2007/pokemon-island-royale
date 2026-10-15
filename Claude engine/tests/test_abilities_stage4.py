"""Stage 4 Sand Veil behavior tests."""
from types import SimpleNamespace
import unittest

from pkmn_br.abilities import (
    AbilityTrigger,
    get_ability_hook,
    get_active_abilities,
    ignores_sandstorm_damage,
)
from pkmn_br.rules import calc_damage, hit_chance
from pkmn_br.config import Config
from pkmn_br.data import Build, MOVES
from pkmn_br.engine import Engine


class SandVeilTests(unittest.TestCase):
    def setUp(self):
        self.move = SimpleNamespace(accuracy=90, reach="ranged", type="normal")
        self.attacker = SimpleNamespace(ability=None, types=("normal",))
        self.defender = SimpleNamespace(ability="sand_veil", types=("normal",))

    def test_registered_and_connected(self):
        self.assertIn("sand_veil", get_active_abilities())
        self.assertIsNotNone(
            get_ability_hook("sand_veil", AbilityTrigger.MODIFY_HIT_CHANCE)
        )

    def test_accuracy_reduced_only_in_sand(self):
        base = hit_chance(self.move, 0.2)
        in_sand = hit_chance(self.move, 0.2, "sand", self.attacker, self.defender)
        in_rain = hit_chance(self.move, 0.2, "rain", self.attacker, self.defender)
        self.assertAlmostEqual(in_sand, base * 0.8)
        self.assertAlmostEqual(in_rain, base)

    def test_old_two_argument_call_remains_compatible(self):
        self.assertAlmostEqual(hit_chance(self.move, 0.2), 0.9 * (1.0 - 0.5 * 0.2))

    def test_damage_resolution_uses_sand_veil_accuracy(self):
        # 0.90 hits at base accuracy 1.0, but misses at Sand Veil accuracy 0.8.
        # The accuracy branch returns before other combatant stats are needed.
        move = SimpleNamespace(accuracy=100, reach="ranged", type="normal")
        result, info = calc_damage(
            self.attacker, self.defender, move,
            [0.90, 0.25, 0.50, 0.25], "sand", 1.0, 0.0, None
        )
        self.assertEqual(result, 0)
        self.assertTrue(info.get("miss"))

    def test_sand_veil_prevents_sandstorm_chip(self):
        self.assertTrue(ignores_sandstorm_damage(self.defender))
        ordinary = SimpleNamespace(ability="blaze")
        self.assertFalse(ignores_sandstorm_damage(ordinary))
        self.assertFalse(ignores_sandstorm_damage(SimpleNamespace()))

    def _make_integration_engine(self):
        stats = {"hp": 300, "atk": 200, "def": 200, "spa": 200, "spd": 200, "spe": 200}
        builds = [
            Build("Attacker", ("normal",), dict(stats), ["thunder_wave"], "", "", False),
            Build("Defender", ("normal",), dict(stats), ["hyper_voice"], "", "", False),
        ]

        class Provider:
            def load_roster(self, cfg):
                return list(builds)

        eng = Engine(Config(seed=23, n_combatants=2, allow_duplicates=False), Provider())
        by_name = {c.name: c for c in eng.by_id}
        a, d = by_name["Attacker"], by_name["Defender"]
        # Keep both combatants on land and away from the initial zone boundary.
        for c, dx in ((a, -3.0), (d, 3.0)):
            c.x, c.y = eng.island.cx + dx, eng.island.cy
            c.biome, c.alt = "plains", 0
        return eng, a, d

    def test_status_move_accuracy_uses_sand_veil_in_engine(self):
        eng, a, d = self._make_integration_engine()
        d.ability = "sand_veil"
        eng.weather = "sand"

        # Thunder Wave has 90% base accuracy; this draw passes base accuracy but
        # fails after Sand Veil's 0.8 multiplier (and any plains cover adjustment).
        eng._use_status_move(
            1, a, d, MOVES["thunder_wave"], [0.80, 0.25, 0.50, 0.25], []
        )

        misses = [
            e for e in eng.log.events
            if e.get("e") == "move_miss" and e.get("d") == d.id
        ]
        self.assertTrue(misses, "expected the status move to miss due to Sand Veil")
        self.assertEqual(misses[-1].get("reason"), "accuracy")
        self.assertIsNone(d.status, "a missed status move must not apply its status")

    def test_sand_veil_prevents_actual_residual_sandstorm_damage(self):
        eng, sand_veil_user, ordinary_user = self._make_integration_engine()
        sand_veil_user.ability = "sand_veil"
        ordinary_user.ability = None
        eng.weather = "sand"
        tick = eng.cfg.residual_interval
        self.assertGreater(tick, 0, "residual_interval must be positive for this test")

        eng._phase_environment(tick)
        weather_events = [
            e for e in eng.log.events
            if e.get("e") == "env_damage" and e.get("src") == "sandstorm"
        ]
        damaged_ids = {e.get("id") for e in weather_events}
        self.assertNotIn(
            sand_veil_user.id, damaged_ids,
            "Sand Veil holder took actual residual sandstorm damage",
        )
        self.assertIn(
            ordinary_user.id, damaged_ids,
            "control combatant should still take residual sandstorm damage",
        )


if __name__ == "__main__":
    unittest.main()
