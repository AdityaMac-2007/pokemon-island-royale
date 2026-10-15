"""Stage 2 ability tests: Water Absorb, Volt Absorb, Flash Fire, Thick Fat, Filter, Solid Rock.

Verifies Gen 9 mechanics through the real Engine and calc_damage paths:
- Reactive absorption/boost triggers only on hits (never on misses, Protect, or AI lookahead).
- Passive Levitate immunity remains pre-accuracy.
- Filter and Solid Rock reduce incoming super-effective damage only (never outgoing).
- Thick Fat halves incoming Fire and Ice attack stats only.
- Per-combatant Flash Fire state is isolated and does not stack on repeated hits.
"""
import sys
import unittest

sys.path.insert(0, ".")
from pkmn_br.capabilities import engine_capabilities
from pkmn_br.config import Config
from pkmn_br.data import Build, MOVES
from pkmn_br.effects import FX_DRAWS
from pkmn_br.engine import Engine
from pkmn_br.rng import stream
from pkmn_br.rules import BattleRules, calc_damage, expected_damage
from pkmn_br.state import Combatant, Intent

STATS = {"hp": 300, "atk": 200, "def": 200, "spa": 200, "spd": 200, "spe": 200}
FX_PASS = tuple(0.0 for _ in range(FX_DRAWS))


def bld(name, types=("normal",), moves=("hyper_voice",), ability="", stats=None):
    return Build(name, tuple(types), dict(stats or STATS), list(moves), ability, "", False)


class DuelProvider:
    def __init__(self, builds):
        self.builds = list(builds)

    def load_roster(self, cfg):
        return list(self.builds)


def make_duel(build_a, build_b, seed=1):
    cfg = Config(seed=seed, n_combatants=2, allow_duplicates=False)
    eng = Engine(cfg, DuelProvider([build_a, build_b]))
    a = next(c for c in eng.by_id if c.name == build_a.species)
    b = next(c for c in eng.by_id if c.name == build_b.species)
    for c, dx in ((a, 0.0), (b, 1.0)):
        c.x, c.y, c.biome, c.alt = eng.island.cx + dx, eng.island.cy, "plains", 0
        c.energy = 0.0
    return eng, a, b


def execute_attack_with_rolls(eng, att, tgt, move_name, rolls, fx=FX_PASS, t=1):
    idx = [m.name for m in att.moves].index(move_name)
    att.intent = Intent(attack=(tgt.id, idx))
    att.energy = eng.cfg.action_threshold
    eng.tick = t
    eng._do_attack(t, att, rolls, fx, deferred=None)


def evs(eng, kind):
    return [e for e in eng.log.events if e.get("e") == kind]


class Stage2AbilitiesTest(unittest.TestCase):
    def test_capabilities_report_all_twelve_active_abilities(self):
        caps = engine_capabilities()["abilities_with_effect"]
        expected = sorted([
            "blaze", "overgrow", "torrent", "swarm", "levitate", "sturdy",
            "water_absorb", "volt_absorb", "flash_fire", "thick_fat", "filter", "solid_rock",
        ])
        for ability in expected:
            self.assertIn(ability, caps)

    def test_water_absorb_prevents_damage_and_heals_quarter_max_hp(self):
        b_dfn = bld("Vaporeon", ("water",), ("hyper_voice",), ability="water_absorb")
        b_att = bld("Blastoise", ("water",), ("surf", "hydro_pump"))
        eng, dfn, att = make_duel(b_dfn, b_att)
        dfn.hp = 200  # 300 max_hp -> 25% heal is 75 HP

        execute_attack_with_rolls(eng, att, dfn, "surf", rolls=(0.10, 0.99, 0.50, 0.99), t=1)
        self.assertEqual(dfn.hp, 275)
        self.assertEqual(evs(eng, "damage"), [])
        heals = evs(eng, "heal")
        self.assertEqual(len(heals), 1)
        self.assertEqual((heals[0]["id"], heals[0]["amount"], heals[0]["src"], heals[0]["mv"]),
                         (dfn.id, 75, "ability", "water_absorb"))
        self.assertEqual(evs(eng, "move_miss")[-1]["reason"], "immune")

    def test_water_absorb_caps_healing_at_max_hp_and_does_not_heal_at_full_hp(self):
        b_dfn = bld("Vaporeon", ("water",), ("hyper_voice",), ability="water_absorb")
        b_att = bld("Blastoise", ("water",), ("surf",))
        eng, dfn, att = make_duel(b_dfn, b_att)
        dfn.hp = 290
        execute_attack_with_rolls(eng, att, dfn, "surf", rolls=(0.10, 0.99, 0.50, 0.99), t=1)
        self.assertEqual(dfn.hp, 300)
        self.assertEqual(evs(eng, "heal")[0]["amount"], 10)

        # Attack again at full HP: still immune, no extra heal event emitted
        execute_attack_with_rolls(eng, att, dfn, "surf", rolls=(0.10, 0.99, 0.50, 0.99), t=2)
        self.assertEqual(dfn.hp, 300)
        self.assertEqual(len(evs(eng, "heal")), 1)

    def test_water_absorb_does_not_trigger_on_missed_attack_or_protect(self):
        b_dfn = bld("Vaporeon", ("water",), ("protect",), ability="water_absorb")
        b_att = bld("Blastoise", ("water",), ("hydro_pump",))
        eng, dfn, att = make_duel(b_dfn, b_att)
        dfn.hp = 200

        # 1. Accuracy roll 0.95 misses Hydro Pump (80% accuracy) -> no heal
        execute_attack_with_rolls(eng, att, dfn, "hydro_pump", rolls=(0.95, 0.99, 0.50, 0.99), t=1)
        self.assertEqual(dfn.hp, 200)
        self.assertEqual(evs(eng, "heal"), [])
        self.assertEqual(evs(eng, "move_miss")[-1]["reason"], "accuracy")

        # 2. Protected target blocks move before absorption -> no heal
        dfn.protected_until = 5
        execute_attack_with_rolls(eng, att, dfn, "hydro_pump", rolls=(0.10, 0.99, 0.50, 0.99), t=2)
        self.assertEqual(dfn.hp, 200)
        self.assertEqual(evs(eng, "heal"), [])
        self.assertEqual(evs(eng, "move_blocked")[-1]["by"], "protect")

    def test_volt_absorb_blocks_damaging_and_status_electric_moves_and_heals(self):
        b_dfn = bld("Jolteon", ("normal",), ("hyper_voice",), ability="volt_absorb")
        b_att = bld("Pikachu", ("electric",), ("thunderbolt", "thunder_wave"))
        eng, dfn, att = make_duel(b_dfn, b_att)

        # Damaging Electric move
        dfn.hp = 150
        execute_attack_with_rolls(eng, att, dfn, "thunderbolt", rolls=(0.10, 0.99, 0.50, 0.0), t=1)
        self.assertEqual(dfn.hp, 225)
        self.assertIsNone(dfn.status)
        self.assertEqual(evs(eng, "damage"), [])

        # Status Electric move (Thunder Wave): absorbed when it hits, no paralysis
        execute_attack_with_rolls(eng, att, dfn, "thunder_wave", rolls=(0.10, 0.99, 0.50, 0.0), t=2)
        self.assertEqual(dfn.hp, 300)
        self.assertIsNone(dfn.status)

        # Missed Thunder Wave (roll 0.95 >= 0.90): does not heal
        dfn.hp = 200
        execute_attack_with_rolls(eng, att, dfn, "thunder_wave", rolls=(0.95, 0.99, 0.50, 0.0), t=3)
        self.assertEqual(dfn.hp, 200)

    def test_flash_fire_lifecycle_non_stacking_and_ai_purity(self):
        b_ff = bld("Flareon", ("fire",), ("flamethrower", "hyper_voice"), ability="flash_fire")
        b_att = bld("Charizard", ("fire",), ("flamethrower",))
        b_dummy = bld("Snorlax", ("normal",), ("hyper_voice",))
        eng, ff, att = make_duel(b_ff, b_att)

        self.assertFalse(ff.flash_fire_active)

        # AI expected_damage query must NEVER activate flash_fire_active
        dmg_est = expected_damage(att, ff, MOVES["flamethrower"], "clear", 1.0, 0.0, eng.rules)
        self.assertEqual(dmg_est, 0.0)
        self.assertFalse(ff.flash_fire_active)

        # Missed Fire attack -> does not activate Flash Fire
        _, miss_info = calc_damage(att, ff, MOVES["flamethrower"], (1.0, 0.99, 0.5, 0.99), "clear", 1.0, 0.0, eng.rules)
        self.assertTrue(miss_info.get("miss"))
        self.assertFalse(ff.flash_fire_active)

        # Protected target does not activate Flash Fire
        ff.protected_until = 3
        execute_attack_with_rolls(eng, att, ff, "flamethrower", rolls=(0.10, 0.99, 0.50, 0.99), t=1)
        self.assertFalse(ff.flash_fire_active)

        # Real hit after Protect expires -> activates Flash Fire, 0 damage
        execute_attack_with_rolls(eng, att, ff, "flamethrower", rolls=(0.10, 0.99, 0.50, 0.99), t=4)
        self.assertTrue(ff.flash_fire_active)
        self.assertEqual(ff.hp, ff.max_hp)
        self.assertEqual(len(evs(eng, "ability_trigger")), 1)

        # Measure unboosted vs boosted Fire damage and verify repeated triggers do not stack
        eng_test, ff_att, snorlax = make_duel(b_ff, b_dummy)
        rolls_fixed = (0.0, 0.99, 1.0, 0.99)
        ff_att.flash_fire_active = False
        base_fire, _ = calc_damage(ff_att, snorlax, MOVES["flamethrower"], rolls_fixed, "clear", 1.0, 0.0, eng_test.rules)
        base_norm, _ = calc_damage(ff_att, snorlax, MOVES["hyper_voice"], rolls_fixed, "clear", 1.0, 0.0, eng_test.rules)

        # Activate once
        execute_attack_with_rolls(eng, att, ff, "flamethrower", rolls=(0.10, 0.99, 0.50, 0.99), t=5)
        self.assertEqual(len(evs(eng, "ability_trigger")), 1)  # no duplicate trigger event

        ff_att.flash_fire_active = True
        boosted_fire, _ = calc_damage(ff_att, snorlax, MOVES["flamethrower"], rolls_fixed, "clear", 1.0, 0.0, eng_test.rules)
        boosted_norm, _ = calc_damage(ff_att, snorlax, MOVES["hyper_voice"], rolls_fixed, "clear", 1.0, 0.0, eng_test.rules)

        # Exact formula check: A = int(200 * 1.5) = 300
        b_300 = bld("Flareon300", ("fire",), ("flamethrower",), ability="", stats=dict(STATS, spa=300))
        c_300 = Combatant.from_build(9, b_300)
        want_fire, _ = calc_damage(c_300, snorlax, MOVES["flamethrower"], rolls_fixed, "clear", 1.0, 0.0, eng_test.rules)
        self.assertEqual(boosted_fire, want_fire)
        self.assertGreater(boosted_fire, base_fire)
        self.assertEqual(boosted_norm, base_norm)  # non-Fire move unaffected

    def test_flash_fire_state_isolated_between_combatants_and_matches(self):
        b1 = bld("FlareonA", ("fire",), ("flamethrower",), ability="flash_fire")
        b2 = bld("FlareonB", ("fire",), ("flamethrower",), ability="flash_fire")
        eng, a, b = make_duel(b1, b2)
        execute_attack_with_rolls(eng, a, b, "flamethrower", rolls=(0.10, 0.99, 0.50, 0.99), t=1)
        self.assertTrue(b.flash_fire_active)
        self.assertFalse(a.flash_fire_active)

        # New match resets state cleanly
        _, a_new, b_new = make_duel(b1, b2)
        self.assertFalse(a_new.flash_fire_active)
        self.assertFalse(b_new.flash_fire_active)

    def test_thick_fat_halves_incoming_fire_and_ice_only(self):
        b_tf = bld("SnorlaxTF", ("normal",), ("hyper_voice",), ability="thick_fat")
        b_norm = bld("SnorlaxNorm", ("normal",), ("hyper_voice",), ability="")
        b_att = bld("Attacker", ("normal",), ("flamethrower", "ice_beam", "thunderbolt"))
        eng, dfn_tf, att = make_duel(b_tf, b_att)
        dfn_norm = Combatant.from_build(2, b_norm)
        rolls = (0.0, 0.99, 1.0, 0.99)

        att_half = Combatant.from_build(3, bld("HalfAtt", ("normal",), ("flamethrower", "ice_beam"), stats=dict(STATS, spa=100)))

        for mv_name in ("flamethrower", "ice_beam"):
            mv = MOVES[mv_name]
            got, _ = calc_damage(att, dfn_tf, mv, rolls, "clear", 1.0, 0.0, eng.rules)
            want, _ = calc_damage(att_half, dfn_norm, mv, rolls, "clear", 1.0, 0.0, eng.rules)
            full, _ = calc_damage(att, dfn_norm, mv, rolls, "clear", 1.0, 0.0, eng.rules)
            self.assertEqual(got, want, mv_name)
            self.assertLess(got, full, mv_name)

        got_elec, _ = calc_damage(att, dfn_tf, MOVES["thunderbolt"], rolls, "clear", 1.0, 0.0, eng.rules)
        full_elec, _ = calc_damage(att, dfn_norm, MOVES["thunderbolt"], rolls, "clear", 1.0, 0.0, eng.rules)
        self.assertEqual(got_elec, full_elec)

    def test_filter_and_solid_rock_reduce_incoming_super_effective_only(self):
        rolls = (0.0, 0.99, 1.0, 0.99)
        rules = BattleRules()
        for ability in ("filter", "solid_rock"):
            dfn_ab = Combatant.from_build(0, bld("Rhyperior", ("rock", "ground"), ("earthquake", "stone_edge"), ability=ability))
            dfn_no = Combatant.from_build(1, bld("RhyperiorNo", ("rock", "ground"), ("earthquake", "stone_edge"), ability=""))
            att = Combatant.from_build(2, bld("Attacker", ("flying",), ("surf", "psychic", "flamethrower", "thunderbolt")))

            dmg_ab, _ = calc_damage(att, dfn_ab, MOVES["surf"], rolls, "clear", 1.0, 0.0, rules)
            dmg_no, _ = calc_damage(att, dfn_no, MOVES["surf"], rolls, "clear", 1.0, 0.0, rules)
            self.assertEqual(dmg_ab, int(dmg_no * 0.75), ability)

            neu_ab, _ = calc_damage(att, dfn_ab, MOVES["psychic"], rolls, "clear", 1.0, 0.0, rules)
            neu_no, _ = calc_damage(att, dfn_no, MOVES["psychic"], rolls, "clear", 1.0, 0.0, rules)
            self.assertEqual(neu_ab, neu_no, ability)

            res_ab, _ = calc_damage(att, dfn_ab, MOVES["flamethrower"], rolls, "clear", 1.0, 0.0, rules)
            res_no, _ = calc_damage(att, dfn_no, MOVES["flamethrower"], rolls, "clear", 1.0, 0.0, rules)
            self.assertEqual(res_ab, res_no, ability)

            imm_ab, info = calc_damage(att, dfn_ab, MOVES["thunderbolt"], rolls, "clear", 1.0, 0.0, rules)
            self.assertEqual((imm_ab, info), (0, {"immune": True}))

            out_ab, _ = calc_damage(dfn_ab, att, MOVES["stone_edge"], rolls, "clear", 1.0, 0.0, rules)
            out_no, _ = calc_damage(dfn_no, att, MOVES["stone_edge"], rolls, "clear", 1.0, 0.0, rules)
            self.assertEqual(out_ab, out_no, f"{ability} must not reduce outgoing super-effective damage")


if __name__ == "__main__":
    unittest.main()
