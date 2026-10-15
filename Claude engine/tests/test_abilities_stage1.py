"""Stage 1 tests: ability registry validation, capability reporting, trigger dispatch,
and non-leaking test isolation."""
import contextlib
import sys
import unittest

sys.path.insert(0, ".")
from pkmn_br.abilities import (
    ABILITY_REGISTRY,
    AbilitySpec,
    AbilityTrigger,
    apply_atk_abilities,
    check_ability_immunity,
    get_ability_hook,
    get_active_abilities,
    register_ability,
)
from pkmn_br.capabilities import engine_capabilities


@contextlib.contextmanager
def isolated_ability_registry():
    """Snapshot and restore ABILITY_REGISTRY so tests never leak registrations."""
    backup = dict(ABILITY_REGISTRY)
    try:
        yield
    finally:
        ABILITY_REGISTRY.clear()
        ABILITY_REGISTRY.update(backup)


class DummyMove:
    def __init__(self, name: str, mtype: str, category: str = "physical", power: int = 80):
        self.name = name
        self.type = mtype
        self.category = category
        self.power = power
        self.reach = "melee"
        self.accuracy = 100
        self.priority = 0
        self.hits_air = False
        self.effects = ()


class DummyCombatant:
    def __init__(self, name: str, types: tuple, stats: dict, ability: str = "", hp: int = 100, max_hp: int = 100):
        self.id = 0
        self.name = name
        self.types = types
        self.stats = stats
        self.ability = ability
        self.hp = hp
        self.max_hp = max_hp
        self.stages = {s: 0 for s in ("atk", "def", "spa", "spd", "spe")}
        self.status = None
        self.item = ""
        self.sturdy_used = False


class Stage1AbilityRegistryTests(unittest.TestCase):
    def test_baseline_six_abilities_active(self):
        active = get_active_abilities()
        expected = ["blaze", "levitate", "overgrow", "sturdy", "swarm", "torrent"]
        missing = sorted(set(expected) - set(active))
        self.assertFalse(missing, f"Missing baseline abilities: {missing}")
        self.assertEqual(engine_capabilities()["abilities_with_effect"], active)

    def test_duplicate_registration_rejected(self):
        with isolated_ability_registry():
            duplicate = AbilitySpec(
                name="blaze",
                description="Duplicate blaze attempt.",
                hooks={AbilityTrigger.MODIFY_ATK: lambda *args: 999}
            )
            with self.assertRaises(ValueError) as ctx:
                register_ability(duplicate)
            self.assertIn("already registered", str(ctx.exception))
            att = DummyCombatant("Charmander", ("fire",), {"atk": 100}, ability="blaze", hp=33, max_hp=100)
            dfn = DummyCombatant("Bulbasaur", ("grass",), {"def": 100})
            mv = DummyMove("flamethrower", "fire")
            self.assertEqual(apply_atk_abilities(att, dfn, mv, "clear", 100), 150)

    def test_explicit_replacement_allowed(self):
        with isolated_ability_registry():
            replacement = AbilitySpec(
                name="blaze",
                description="Explicit replacement test.",
                hooks={AbilityTrigger.MODIFY_ATK: lambda att, dfn, mv, weather, A: 200}
            )
            register_ability(replacement, replace=True)
            att = DummyCombatant("Charmander", ("fire",), {"atk": 100}, ability="blaze")
            dfn = DummyCombatant("Bulbasaur", ("grass",), {"def": 100})
            mv = DummyMove("flamethrower", "fire")
            self.assertEqual(apply_atk_abilities(att, dfn, mv, "clear", 100), 200)

    def test_unconnected_trigger_rejected(self):
        with isolated_ability_registry():
            unsupported = AbilitySpec(
                name="fake_ability",
                description="Test ability declaring an unconnected trigger.",
                hooks={"unconnected_trigger_xyz": lambda *args: None}
            )
            with self.assertRaises(ValueError) as ctx:
                register_ability(unsupported)
            self.assertIn("unconnected or unsupported trigger", str(ctx.exception))
            self.assertNotIn("fake_ability", get_active_abilities())
            self.assertNotIn("fake_ability", engine_capabilities()["abilities_with_effect"])

    def test_empty_or_invalid_hooks_rejected(self):
        with isolated_ability_registry():
            with self.assertRaises(ValueError):
                register_ability(AbilitySpec("empty_hooks", "No hooks", {}))
            with self.assertRaises(ValueError):
                register_ability(AbilitySpec("non_callable", "Bad hook", {AbilityTrigger.MODIFY_ATK: "not_a_function"}))

    def test_inactive_ability_produces_noop(self):
        att = DummyCombatant("Poochyena", ("dark",), {"atk": 100}, ability="run_away")
        dfn = DummyCombatant("Mareep", ("electric",), {"def": 100})
        mv = DummyMove("crunch", "dark")
        self.assertIsNone(get_ability_hook("run_away", AbilityTrigger.MODIFY_ATK))
        self.assertEqual(apply_atk_abilities(att, dfn, mv, "clear", 100), 100)
        self.assertFalse(check_ability_immunity(dfn, mv))

    def test_pinch_ability_dispatch_and_type_matching(self):
        att = DummyCombatant("Charmander", ("fire",), {"atk": 100, "spa": 100}, ability="blaze", hp=33, max_hp=100)
        dfn = DummyCombatant("Bulbasaur", ("grass",), {"def": 100, "spd": 100})
        mv_fire = DummyMove("flamethrower", "fire", "special")
        mv_normal = DummyMove("hyper_voice", "normal", "special")

        self.assertEqual(apply_atk_abilities(att, dfn, mv_fire, "clear", 100), 150)
        self.assertEqual(apply_atk_abilities(att, dfn, mv_normal, "clear", 100), 100)

        att.hp = 34
        self.assertEqual(apply_atk_abilities(att, dfn, mv_fire, "clear", 100), 100)

    def test_levitate_immunity_dispatch(self):
        dfn = DummyCombatant("Gengar", ("ghost", "poison"), {"def": 100}, ability="levitate")
        mv_ground = DummyMove("earthquake", "ground")
        mv_ghost = DummyMove("shadow_ball", "ghost", "special")

        self.assertTrue(check_ability_immunity(dfn, mv_ground))
        self.assertFalse(check_ability_immunity(dfn, mv_ghost))

    def test_sturdy_defense_handler_direct(self):
        hook = get_ability_hook("sturdy", AbilityTrigger.ON_HIT_DEFENSE)
        self.assertIsNotNone(hook)
        dfn = DummyCombatant("Donphan", ("ground",), {"def": 100}, ability="sturdy", hp=100, max_hp=100)
        info = {}

        dmg, triggered = hook(None, 0, None, dfn, None, 150, info)
        self.assertEqual(dmg, 99)
        self.assertTrue(triggered)
        self.assertTrue(info.get("sturdy"))
        self.assertTrue(dfn.sturdy_used)

        dfn.hp -= dmg
        self.assertEqual(dfn.hp, 1)

        dmg2, triggered2 = hook(None, 1, None, dfn, None, 150, info)
        self.assertEqual(dmg2, 150)
        self.assertFalse(triggered2)


if __name__ == "__main__":
    unittest.main()
