"""Combat Systems v1 tests: stat stages, effect framework, Protect, healing, recoil, paralysis,
event/state consistency, determinism, regression against pre-change baselines."""
import json
import math
import os
import sys
import tempfile
import unittest
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pkmn_br import feed
from pkmn_br.capabilities import engine_capabilities
from pkmn_br.config import Config
from pkmn_br.data import MOVES, Build, JsonProvider, Move, SyntheticProvider, _make, register_move
from pkmn_br.effects import Effect, EffectError, FX_DRAWS, HANDLERS, protect_succeeds
from pkmn_br.engine import Engine
from pkmn_br.log import legacy_digest
from pkmn_br.rng import stream
from pkmn_br.rules import STAGE_STATS, BattleRules, _core, apply_stage, calc_damage
from pkmn_br.state import Combatant, Intent

STATS = {"hp": 300, "atk": 200, "def": 200, "spa": 200, "spd": 200, "spe": 200}


def bld(name, types=("normal",), moves=("hyper_voice",), stats=None, fly=False):
    return Build(name, tuple(types), dict(stats or STATS), list(moves), "", "", fly)


class ListProvider:
    def __init__(self, builds):
        self.builds = builds

    def load_roster(self, cfg):
        return list(self.builds)


def duel(a_moves, b_moves=("hyper_voice",), a_types=("normal",), b_types=("normal",), a_stats=None, b_stats=None,
         seed=1, **cfgkw):
    cfg = Config(seed=seed, n_combatants=2, allow_duplicates=False, **cfgkw)
    eng = Engine(cfg, ListProvider([bld("A", a_types, a_moves, a_stats), bld("B", b_types, b_moves, b_stats)]))
    a = next(c for c in eng.by_id if c.name == "A")
    b = next(c for c in eng.by_id if c.name == "B")
    for c, dx in ((a, 0.0), (b, 1.0)):
        c.x, c.y, c.biome, c.alt = eng.island.cx + dx, eng.island.cy, "plains", 0
    return eng, a, b


def fire(eng, att, tgt, mv_name, t=0):
    idx = [m.name for m in att.moves].index(mv_name)
    att.intent = Intent(attack=(tgt.id, idx))
    att.energy = eng.cfg.action_threshold
    eng.tick = t
    eng._phase_attacks(t, stream(eng.cfg.seed, "tick", t))


def evs(eng, kind):
    return [e for e in eng.log.events if e["e"] == kind]


def find_tick(seed, pred, limit=2000):
    for t in range(limit):
        r, f = stream(seed, "tick", t), stream(seed, "fx", t)
        rolls = [r.random() for _ in range(4)]
        fx = [f.random() for _ in range(FX_DRAWS)]
        if pred(rolls, fx):
            return t
    raise AssertionError("no suitable tick found")


def oracle(stat, stage):
    m = Fraction(2 + stage, 2) if stage >= 0 else Fraction(2, 2 - stage)
    return math.floor(stat * m)


# ============================================================ 1. stat stages
class StageMath(unittest.TestCase):
    HAND = {100: {-6: 25, -1: 66, 0: 100, 1: 150, 6: 400},
            297: {-6: 74, -1: 198, 0: 297, 1: 445, 6: 1188}}

    def test_hand_computed_values(self):
        for stat, table in self.HAND.items():
            for stage, want in table.items():
                self.assertEqual(apply_stage(stat, stage), want, (stat, stage))

    def test_matches_exact_fraction_oracle_everywhere(self):
        for stat in list(range(1, 60)) + list(range(60, 1500, 13)):
            for stage in range(-6, 7):
                self.assertEqual(apply_stage(stat, stage), oracle(stat, stage), (stat, stage))

    def test_clamping(self):
        for big in (7, 9, 100):
            self.assertEqual(apply_stage(297, big), apply_stage(297, 6))
            self.assertEqual(apply_stage(297, -big), apply_stage(297, -6))

    def _pair(self):
        att = Combatant.from_build(0, bld("att", ("fire",)))
        dfn = Combatant.from_build(1, bld("dfn", ("normal",)))
        return att, dfn

    def test_all_five_stats_at_required_stages(self):
        """Each stat, through the code path that really consumes it, equals the oracle value."""
        rules = BattleRules()
        for stat in STAGE_STATS:
            for stage in (-6, -1, 0, 1, 6):
                att, dfn = self._pair()
                for c in (att, dfn):
                    c.stats[stat] = 297
                if stat == "spe":
                    att.stages["spe"] = stage
                    self.assertEqual(att.eff_speed(), oracle(297, stage), (stat, stage))
                    continue
                phys = stat in ("atk", "def")
                mv = MOVES["body_slam" if phys else "hyper_voice"]
                holder = att if stat in ("atk", "spa") else dfn
                holder.stages[stat] = stage
                got = _core(att, dfn, mv, "clear", 1.0, rules, 0.0, 1.0)
                att2, dfn2 = self._pair()
                for c in (att2, dfn2):
                    c.stats[stat] = 297
                ref_holder = att2 if stat in ("atk", "spa") else dfn2
                ref_holder.stats[stat] = oracle(297, stage)     # same effect, expressed as a raw stat at stage 0
                want = _core(att2, dfn2, mv, "clear", 1.0, rules, 0.0, 1.0)
                self.assertEqual(got, want, (stat, stage))

    def test_boosts_and_drops_move_damage_in_the_right_direction(self):
        rules, rolls = BattleRules(), (0.0, 0.99, 0.5, 0.9)
        for stat, holder_is_att, mvname in (("atk", True, "body_slam"), ("spa", True, "hyper_voice"),
                                            ("def", False, "body_slam"), ("spd", False, "hyper_voice")):
            seq = []
            for stage in range(-6, 7):
                att, dfn = self._pair()
                (att if holder_is_att else dfn).stages[stat] = stage
                d, _ = calc_damage(att, dfn, MOVES[mvname], rolls, "clear", 1.0, 0.0, rules)
                seq.append(d)
            if holder_is_att:
                self.assertTrue(all(b >= a for a, b in zip(seq, seq[1:])), (stat, seq))
            else:
                self.assertTrue(all(b <= a for a, b in zip(seq, seq[1:])), (stat, seq))
            self.assertNotEqual(seq[0], seq[-1])

    def test_crit_ignores_attacker_drops_and_defender_boosts_only(self):
        rules = BattleRules()
        mv = MOVES["body_slam"]

        def val(a_stage, d_stage, crit):
            att, dfn = self._pair()
            att.stages["atk"], dfn.stages["def"] = a_stage, d_stage
            return _core(att, dfn, mv, "clear", 1.0, rules, crit, 1.0)
        self.assertEqual(val(-3, 0, 1.0), val(0, 0, 1.0))      # attacker drop ignored on a crit
        self.assertEqual(val(0, 3, 1.0), val(0, 0, 1.0))       # defender boost ignored on a crit
        self.assertGreater(val(2, 0, 1.0), val(0, 0, 1.0))     # attacker boost still counts
        self.assertGreater(val(0, -2, 1.0), val(0, 0, 1.0))    # defender drop still counts
        self.assertLess(val(-3, 0, 0.0), val(0, 0, 0.0))       # without a crit the drop applies

    def test_speed_stage_then_paralysis_order(self):
        c = Combatant.from_build(0, bld("x"))
        c.stages["spe"] = 1
        c.status = "para"
        self.assertEqual(c.eff_speed(), apply_stage(200, 1) // 2)


class StageIntegration(unittest.TestCase):
    def test_boost_changes_real_damage_in_engine(self):
        t = find_tick(1, lambda r, f: r[2] > 0.3)
        eng, a, b = duel(["body_slam", "swords_dance"])
        fire(eng, a, b, "body_slam", t)
        base = evs(eng, "damage")[0]["amount"]
        eng2, a2, b2 = duel(["body_slam", "swords_dance"])
        a2.stages["atk"] = 2
        fire(eng2, a2, b2, "body_slam", t)
        boosted = evs(eng2, "damage")[0]["amount"]
        self.assertGreater(boosted, base * 1.7)

    def test_speed_stage_changes_energy_gain_and_action_order(self):
        eng, a, b = duel(["hyper_voice"], ["hyper_voice"], a_stats=dict(STATS, spe=150), b_stats=dict(STATS, spe=200))
        eng.tick = 1
        a.energy = b.energy = 0
        eng._phase_environment(1)
        slow = a.energy
        a.stages["spe"] = 2
        a.energy = 0
        eng._phase_environment(2)
        self.assertEqual(slow, 150 * eng.cfg.energy_per_speed)
        self.assertEqual(a.energy, 300 * eng.cfg.energy_per_speed)     # +2 doubles speed
        # order: A (150 base, +2 stage -> 300) must now act before B (200)
        for c, other in ((a, b), (b, a)):
            c.intent = Intent(attack=(other.id, 0))
            c.energy = 100
        eng._phase_attacks(3, stream(eng.cfg.seed, "tick", 3))
        order = [e["a"] for e in evs(eng, "move_use")]
        self.assertEqual(order, [a.id, b.id])

    def test_icy_wind_slows_target_and_reduces_its_energy_income(self):
        t = find_tick(1, lambda r, f: r[0] < 0.95)
        eng, a, b = duel(["icy_wind"], ["hyper_voice"])
        before = b.eff_speed()
        fire(eng, a, b, "icy_wind", t)
        self.assertEqual(b.stages["spe"], -1)
        self.assertEqual(b.eff_speed(), apply_stage(before, -1))
        self.assertLess(b.eff_speed(), before)


# ============================================================ 2. effect framework
class Framework(unittest.TestCase):
    def test_unimplemented_effect_cannot_be_registered(self):
        bad = Move("confuse_ray", "ghost", "status", 0, 100, effects=(Effect("confuse", "use", "target"),))
        with self.assertRaises(EffectError):
            register_move(bad)
        self.assertNotIn("confuse_ray", MOVES)

    def test_validation_rules(self):
        E = Effect
        cases = [
            Move("x1", "normal", "status", 40, 100, effects=(E("heal", "use", "self", num=1, den=2),)),     # power on status
            Move("x2", "normal", "status", 0, 100, effects=()),                                             # empty status move
            Move("x3", "normal", "physical", 50, 100, effects=(E("heal", "use", "self", num=1, den=2),)),   # use on damaging
            Move("x4", "normal", "status", 0, 100, effects=(E("stat_stage", "use", "self", stat="hp", delta=1),)),
            Move("x5", "normal", "status", 0, 100, effects=(E("stat_stage", "use", "self", stat="atk", delta=0),)),
            Move("x6", "normal", "status", 0, 100, effects=(E("status", "use", "target", status="burn"),)),  # unvalidated status
            Move("x7", "normal", "physical", 50, 100, effects=(E("recoil", "hit", "self", num=3, den=2),)),
            Move("x8", "normal", "physical", 50, 100, effects=(E("protect", "use", "self"),)),
            Move("x9", "normal", "special", 50, 100, effects=(E("stat_stage", "hit", "self", stat="spa", delta=1, chance=1.5),)),
        ]
        for mv in cases:
            with self.assertRaises(EffectError, msg=mv.name):
                register_move(mv)
            self.assertNotIn(mv.name, MOVES)

    def test_no_blanket_implemented_flag_and_capabilities_match_registry(self):
        self.assertFalse(any(hasattr(m, "implemented") for m in MOVES.values()))
        cap = engine_capabilities()
        self.assertEqual(cap["effect_kinds"], sorted(HANDLERS))
        for name in cap["moves_with_effects"]:
            for kind in cap["moves"][name]["effects"]:
                self.assertIn(kind, HANDLERS)
        for name, mv in MOVES.items():
            self.assertEqual(bool(mv.effects), name in cap["moves_with_effects"])
        self.assertNotIn("rough_skin", cap["abilities_with_effect"])      # imported-but-inert stays inert

    def test_legacy_moves_unchanged(self):
        legacy = [m for m in MOVES.values() if not m.effects and m.category != "status"]
        self.assertEqual(len(legacy), 36)
        self.assertEqual(MOVES["brave_bird"].effects, ())                 # deliberately NOT given recoil
        self.assertEqual(MOVES["close_combat"].effects, ())


# ============================================================ 3. individual mechanics
class StatMoves(unittest.TestCase):
    def test_swords_dance_clamps_at_plus_six(self):
        eng, a, b = duel(["swords_dance"])
        for k in range(4):
            fire(eng, a, b, "swords_dance", k)
        self.assertEqual(a.stages["atk"], 6)
        ch = evs(eng, "stat_change")
        self.assertEqual([(e["applied"], e["stage"]) for e in ch], [(2, 2), (2, 4), (2, 6), (0, 6)])

    def test_two_stat_moves_emit_two_events(self):
        eng, a, b = duel(["calm_mind", "dragon_dance"])
        fire(eng, a, b, "calm_mind", 0)
        fire(eng, a, b, "dragon_dance", 1)
        self.assertEqual((a.stages["spa"], a.stages["spd"], a.stages["atk"], a.stages["spe"]), (1, 1, 1, 1))
        self.assertEqual(len(evs(eng, "stat_change")), 4)

    def test_screech_hit_and_miss(self):
        hit_t = find_tick(1, lambda r, f: r[0] < 0.85)
        eng, a, b = duel(["screech"])
        fire(eng, a, b, "screech", hit_t)
        self.assertEqual(b.stages["def"], -2)
        miss_t = find_tick(1, lambda r, f: r[0] >= 0.85)
        eng2, a2, b2 = duel(["screech"])
        fire(eng2, a2, b2, "screech", miss_t)
        self.assertEqual(b2.stages["def"], 0)
        self.assertEqual(evs(eng2, "move_miss")[0]["reason"], "accuracy")

    def test_secondary_drop_skipped_when_target_faints_but_self_drop_applies(self):
        t = find_tick(1, lambda r, f: r[0] < 0.95)
        eng, a, b = duel(["icy_wind"])
        b.hp = 1
        fire(eng, a, b, "icy_wind", t)
        self.assertFalse(b.alive)
        self.assertEqual(b.stages["spe"], 0)                    # fainted targets are not modified
        eng2, a2, b2 = duel(["superpower"])
        b2.hp = 1
        fire(eng2, a2, b2, "superpower", 0)
        self.assertFalse(b2.alive)
        self.assertEqual((a2.stages["atk"], a2.stages["def"]), (-1, -1))   # user's drops still apply

    def test_mystical_fire_lowers_spa(self):
        eng, a, b = duel(["mystical_fire"])
        fire(eng, a, b, "mystical_fire", 0)
        self.assertEqual(b.stages["spa"], -1)


class Paralysis(unittest.TestCase):
    def test_thunder_wave_paralyses_and_halves_speed(self):
        t = find_tick(1, lambda r, f: r[0] < 0.9)
        eng, a, b = duel(["thunder_wave"])
        fire(eng, a, b, "thunder_wave", t)
        self.assertEqual(b.status, "para")
        self.assertEqual(b.eff_speed(), 100)
        self.assertEqual(evs(eng, "status")[0]["status"], "para")

    def test_immunities_and_repeat(self):
        for types, reason in ((("electric",), "immune_type"), (("ground",), "immune")):
            eng, a, b = duel(["thunder_wave"], b_types=types)
            t = find_tick(1, lambda r, f: r[0] < 0.9)
            fire(eng, a, b, "thunder_wave", t)
            self.assertIsNone(b.status, types)
            self.assertEqual(evs(eng, "move_failed")[0]["reason"], reason)
        eng, a, b = duel(["thunder_wave"])
        b.status = "para"
        fire(eng, a, b, "thunder_wave", find_tick(1, lambda r, f: r[0] < 0.9))
        self.assertEqual(evs(eng, "move_failed")[0]["reason"], "already_statused")

    def test_full_paralysis_is_off_by_default_and_works_when_enabled(self):
        eng, a, b = duel(["hyper_voice"])
        a.status = "para"
        fire(eng, a, b, "hyper_voice", 0)
        self.assertEqual(len(evs(eng, "damage")), 1)           # default 0.0: still acts (legacy behaviour)
        eng, a, b = duel(["hyper_voice"], para_skip_chance=1.0)
        a.status = "para"
        fire(eng, a, b, "hyper_voice", 0)
        self.assertEqual(evs(eng, "damage"), [])
        self.assertEqual(evs(eng, "move_failed")[0]["reason"], "fully_paralyzed")
        self.assertEqual(a.energy, 0)                           # the turn is consumed
        eng, a, b = duel(["hyper_voice"], para_skip_chance=0.25)
        a.status = "para"
        t_skip = find_tick(1, lambda r, f: f[4] < 0.25)
        t_go = find_tick(1, lambda r, f: f[4] >= 0.25)
        fire(eng, a, b, "hyper_voice", t_skip)
        self.assertEqual(len(evs(eng, "damage")), 0)
        fire(eng, a, b, "hyper_voice", t_go)
        self.assertEqual(len(evs(eng, "damage")), 1)


class ProtectTests(unittest.TestCase):
    def test_success_probability_function(self):
        self.assertTrue(protect_succeeds(0, 0.999999))
        self.assertTrue(protect_succeeds(1, 0.3333))
        self.assertFalse(protect_succeeds(1, 0.34))
        self.assertTrue(protect_succeeds(2, 0.11))
        self.assertFalse(protect_succeeds(2, 0.12))
        self.assertEqual([protect_succeeds(6, 0.0013), protect_succeeds(60, 0.0013)], [True, True])
        self.assertEqual([protect_succeeds(6, 0.002), protect_succeeds(60, 0.002)], [False, False])   # floor 1/729

    def test_protect_blocks_damage_and_status_then_expires(self):
        eng, a, b = duel(["double_edge", "thunder_wave"], ["protect"])
        fire(eng, b, a, "protect", 0)
        self.assertEqual(b.protected_until, 3)
        self.assertEqual(b.protect_streak, 1)
        hp = b.hp
        for t in (1, 2):
            fire(eng, a, b, "double_edge", t)
        fire(eng, a, b, "thunder_wave", 2)
        self.assertEqual(b.hp, hp)
        self.assertIsNone(b.status)
        self.assertEqual(len(evs(eng, "move_blocked")), 3)
        self.assertEqual(evs(eng, "recoil"), [])                # no hit -> no recoil
        fire(eng, a, b, "double_edge", 3)                       # protection ended
        self.assertLess(b.hp, hp)

    def test_protect_failure_resets_streak(self):
        eng, a, b = duel(["protect"])
        a.protect_streak = 1
        t = find_tick(1, lambda r, f: f[5] >= 1 / 3)
        fire(eng, a, b, "protect", t)
        self.assertEqual(a.protect_streak, 0)
        self.assertLess(a.protected_until, 0)
        self.assertFalse(evs(eng, "protect")[0]["ok"])
        self.assertEqual(evs(eng, "move_failed")[0]["reason"], "protect_failed")

    def test_other_move_resets_streak(self):
        eng, a, b = duel(["protect", "hyper_voice"])
        fire(eng, a, b, "protect", 0)
        self.assertEqual(a.protect_streak, 1)
        fire(eng, a, b, "hyper_voice", 10)
        self.assertEqual(a.protect_streak, 0)

    def test_protect_does_not_stop_zone_damage(self):
        eng, a, b = duel(["protect"])
        fire(eng, a, b, "protect", 0)
        a.x = eng.island.cx + eng.island.radius * 1.2
        hp = a.hp
        eng.zone = eng.island.zone.state(0)
        eng._phase_environment(1)
        self.assertLess(a.hp, hp)
        self.assertEqual(evs(eng, "env_damage")[0]["src"], "zone")


class HealingAndRecoil(unittest.TestCase):
    def test_recover_amounts_and_bounds(self):
        eng, a, b = duel(["recover"])
        a.hp = 1
        fire(eng, a, b, "recover", 0)
        self.assertEqual(a.hp, 151)
        a.hp = a.max_hp - 10
        fire(eng, a, b, "recover", 1)
        self.assertEqual(a.hp, a.max_hp)
        self.assertEqual([e["amount"] for e in evs(eng, "heal")], [150, 10])
        fire(eng, a, b, "recover", 2)                           # already full
        self.assertEqual(a.hp, a.max_hp)
        self.assertEqual(evs(eng, "move_failed")[-1]["reason"], "full_hp")
        self.assertEqual(len(evs(eng, "heal")), 2)

    def test_recoil_is_a_third_of_damage_dealt(self):
        eng, a, b = duel(["double_edge"])
        fire(eng, a, b, "double_edge", 0)
        dmg = evs(eng, "damage")[0]["amount"]
        rc = evs(eng, "recoil")[0]
        self.assertEqual(rc["amount"], max(1, dmg // 3))
        self.assertEqual(a.hp, a.max_hp - rc["amount"])
        self.assertEqual(rc["hp_after"], a.hp)
        self.assertEqual(rc["src"], "move")

    def test_recoil_uses_hp_actually_removed(self):
        eng, a, b = duel(["head_smash"])
        b.hp = 5
        t = find_tick(1, lambda r, f: r[0] < 0.8)
        fire(eng, a, b, "head_smash", t)
        e = evs(eng, "damage")[0]
        self.assertEqual((e["amount"], e["raw"] > 5), (5, True))
        self.assertEqual(evs(eng, "recoil")[0]["amount"], max(1, 5 // 2))

    def test_recoil_can_faint_the_user(self):
        eng, a, b = duel(["double_edge"])
        a.hp = 1
        fire(eng, a, b, "double_edge", 0)
        self.assertFalse(a.alive)
        self.assertTrue(b.alive)
        el = [e for e in evs(eng, "elim") if e["id"] == a.id][0]
        self.assertEqual((el["cause"], el["killer"]), ("recoil", None))
        self.assertEqual(evs(eng, "recoil")[0]["hp_after"], 0)

    def test_double_faint_credits_the_kill_and_ends_with_valid_state(self):
        eng, a, b = duel(["double_edge"])
        a.hp, b.hp = 1, 1
        fire(eng, a, b, "double_edge", 0)
        self.assertEqual((a.alive, b.alive, a.kills), (False, False, 1))
        eng._phase_finalize(0)
        self.assertEqual(sorted((a.placement, b.placement)), [1, 2])
        self.assertEqual(a.placement, 1)                        # tiebreak: more damage dealt

    def test_miss_and_immunity_produce_no_recoil(self):
        eng, a, b = duel(["head_smash"])
        fire(eng, a, b, "head_smash", find_tick(1, lambda r, f: r[0] >= 0.8))
        self.assertEqual(evs(eng, "recoil"), [])
        self.assertEqual(evs(eng, "move_miss")[0]["reason"], "accuracy")
        eng, a, b = duel(["double_edge"], b_types=("ghost",))
        fire(eng, a, b, "double_edge", 0)
        self.assertEqual(evs(eng, "recoil"), [])
        self.assertEqual(evs(eng, "move_miss")[0]["reason"], "immune")
        self.assertEqual(a.hp, a.max_hp)


class InvalidTargets(unittest.TestCase):
    def test_fainted_target(self):
        eng, a, b = duel(["hyper_voice"])
        eng._eliminate(0, b, None, "test", "environment")
        fire(eng, a, b, "hyper_voice", 1)
        self.assertEqual(evs(eng, "action_lost")[0]["why"], "target_down")
        self.assertEqual(evs(eng, "damage"), [])
        self.assertEqual(a.energy, 100)                         # not spent

    def test_out_of_range_keeps_energy(self):
        eng, a, b = duel(["double_edge"])
        b.x += 50
        fire(eng, a, b, "double_edge", 0)
        self.assertEqual(evs(eng, "move_use"), [])
        self.assertEqual(a.energy, 100)

    def test_ground_attacker_cannot_hit_airborne_with_melee_but_can_with_ranged(self):
        eng, a, b = duel(["double_edge", "icy_wind"])
        b.alt = 1
        fire(eng, a, b, "double_edge", 0)
        self.assertEqual(evs(eng, "move_use"), [])
        t = find_tick(1, lambda r, f: r[0] < 0.95)
        fire(eng, a, b, "icy_wind", t)
        self.assertEqual(len(evs(eng, "damage")), 1)

    def test_hp_never_leaves_bounds_in_matches(self):
        for seed in (1, 2, 3):
            eng = Engine(Config(seed=seed, n_combatants=40), SyntheticProvider(combat_moves=True))
            eng.run()
            for c in eng.by_id:
                self.assertTrue(0 <= c.hp <= c.max_hp)
                self.assertTrue(all(-6 <= v <= 6 for v in c.stages.values()))
            for f in eng.log.frames:
                for (_, _, _, _, hp) in f["c"]:
                    self.assertTrue(hp > 0)


# ============================================================ 4. event/state consistency + determinism
def combat_roster():
    return [
        _make("Snorlax", ("normal",), (160, 110, 65, 65, 110, 30), "", "leftovers", ["double_edge", "recover", "protect", "crunch"], False),
        _make("Garchomp", ("dragon", "ground"), (108, 130, 95, 80, 85, 102), "", "life_orb", ["swords_dance", "earthquake", "head_smash", "dragon_claw"], False),
        _make("Gengar", ("ghost", "poison"), (60, 65, 60, 130, 75, 110), "levitate", "", ["calm_mind", "shadow_ball", "thunder_wave", "protect"], False),
        _make("Lucario", ("fighting", "steel"), (70, 110, 70, 115, 70, 90), "", "life_orb", ["superpower", "flash_cannon", "screech", "extreme_speed"], False),
        _make("Dragonite", ("dragon", "flying"), (91, 134, 95, 100, 100, 80), "", "leftovers", ["dragon_dance", "extreme_speed", "earthquake", "recover"], True),
        _make("Lapras", ("water", "ice"), (130, 85, 80, 85, 95, 60), "", "leftovers", ["icy_wind", "surf", "recover", "thunder_wave"], False),
        _make("Magnezone", ("electric", "steel"), (70, 70, 115, 130, 90, 60), "", "choice_specs", ["thunderbolt", "mystical_fire", "protect", "flash_cannon"], False),
        _make("Tyranitar", ("rock", "dark"), (100, 134, 110, 95, 100, 61), "", "leftovers", ["head_smash", "crunch", "swords_dance", "protect"], False),
    ]


def run_combat(seed, **kw):
    e = Engine(Config(seed=seed, n_combatants=24, **kw), ListProvider(combat_roster()))
    e.run()
    return e


def check_hp_chain(tc, eng):
    hp = {c.id: c.max_hp for c in eng.by_id}
    for e in eng.log.events:
        k = e["e"]
        if k == "damage":
            cid = e["d"]; hp[cid] = max(0, hp[cid] - e["amount"])
        elif k in ("env_damage", "recoil"):
            cid = e["id"]; hp[cid] = max(0, hp[cid] - e["amount"])
        elif k == "heal":
            cid = e["id"]; hp[cid] = min(eng.by_id[cid].max_hp, hp[cid] + e["amount"])
        else:
            continue
        tc.assertEqual(e["hp_after"], hp[cid], e)
    for c in eng.by_id:
        tc.assertEqual(hp[c.id], c.hp, c.name)


class EventConsistency(unittest.TestCase):
    SEEDS = (1, 2, 3, 4)

    def test_hp_chain_matches_final_state_all_modes(self):
        for kw in ({}, {"speed_tie_mode": "simultaneous"}, {"ruleset": "chaos"}):
            for s in self.SEEDS:
                check_hp_chain(self, run_combat(s, **kw))

    def test_stage_status_damage_and_kill_accounting(self):
        seen = {k: 0 for k in ("stat_change", "heal", "protect", "recoil", "status", "move_blocked", "move_failed")}
        for s in self.SEEDS:
            eng = run_combat(s)
            for k in seen:
                seen[k] += len(evs(eng, k))
            stage = {c.id: {st: 0 for st in STAGE_STATS} for c in eng.by_id}
            for e in evs(eng, "stat_change"):
                stage[e["id"]][e["stat"]] += e["applied"]
                self.assertEqual(stage[e["id"]][e["stat"]], e["stage"])
            for c in eng.by_id:
                self.assertEqual(stage[c.id], c.stages)
            last_status = {}
            for e in evs(eng, "status"):
                last_status[e["id"]] = e["status"]
            for c in eng.by_id:
                self.assertEqual(last_status.get(c.id), c.status)
            taken, dealt = {c.id: 0 for c in eng.by_id}, {c.id: 0 for c in eng.by_id}
            for e in evs(eng, "damage"):
                taken[e["d"]] += e["raw"]; dealt[e["src"]] += e["raw"]
            for c in eng.by_id:
                self.assertEqual((taken[c.id], dealt[c.id]), (c.dmg_taken, c.dmg_dealt))
            elims = evs(eng, "elim")
            self.assertEqual(len(elims), len(eng.by_id) - len(eng.living()))
            self.assertEqual(sum(c.kills for c in eng.by_id), sum(1 for e in elims if e["killer"] is not None))
            self.assertEqual(sorted(c.placement for c in eng.by_id), list(range(1, 25)))
        for k in ("stat_change", "heal", "protect", "recoil"):     # the roster really exercises the features
            self.assertGreater(seen[k], 0, k)

    def test_every_hit_event_has_a_matching_damage_or_miss_event(self):
        eng = run_combat(2)
        hits = [e for e in evs(eng, "hit")]
        dmg = [e for e in evs(eng, "damage")]
        miss = [e for e in evs(eng, "move_miss")]
        self.assertEqual(len([h for h in hits if h["dmg"] > 0]), len(dmg))
        self.assertEqual(len([h for h in hits if h["dmg"] == 0]), len(miss))
        for h, d in zip([h for h in hits if h["dmg"] > 0], dmg):
            self.assertEqual((h["a"], h["d"], h["mv"], h["dmg"]), (d["src"], d["d"], d["mv"], d["raw"]))

    def test_feed_is_a_pure_projection(self):
        eng = run_combat(3)
        f = feed.combat_feed(eng.log.events, eng.cfg.replay_every)
        self.assertGreater(len(f), 50)
        src = [e for e in eng.log.events if e["e"] in feed.FEED_KINDS]
        self.assertEqual(len(f), len(src))
        for rec, e in zip(f, src):
            self.assertEqual(rec["frame_t"] % eng.cfg.replay_every, 0)
            self.assertEqual({k: v for k, v in rec.items() if k != "frame_t"}, e)
        self.assertFalse(hasattr(feed, "calc_damage"))             # the feed cannot compute damage


class Determinism(unittest.TestCase):
    def test_same_seed_same_digest_and_event_sequence(self):
        a, b = run_combat(7), run_combat(7)
        self.assertEqual(a.result()["digest"], b.result()["digest"])
        self.assertEqual(a.log.events, b.log.events)
        self.assertEqual(a.log.frames, b.log.frames)

    def test_different_seed_differs_and_logging_flags_keep_outcome(self):
        self.assertNotEqual(run_combat(7).result()["digest"], run_combat(8).result()["digest"])
        quiet = run_combat(7, log_hits=False, replay_every=50)
        full = run_combat(7)
        self.assertEqual([r["id"] for r in quiet.rankings()], [r["id"] for r in full.rankings()])
        self.assertEqual(quiet.tick, full.tick)
        self.assertEqual(evs(quiet, "stat_change"), [])            # gated detail events are off

    def test_synthetic_combat_roster_deterministic(self):
        d = [Engine(Config(seed=5, n_combatants=60), SyntheticProvider(combat_moves=True)).run()["digest"] for _ in range(2)]
        self.assertEqual(d[0], d[1])

    def test_outputs_include_capabilities_and_feed(self):
        eng = run_combat(1)
        with tempfile.TemporaryDirectory() as d:
            eng.write_outputs(d)
            for f in ("capabilities.json", "combat_feed.json", "replay.json", "events.jsonl", "summary.json"):
                self.assertTrue(os.path.getsize(os.path.join(d, f)) > 0, f)


# ============================================================ 5. regression
class Regression(unittest.TestCase):
    def test_golden_baselines_from_pre_change_engine(self):
        with open(os.path.join(HERE, "golden_legacy.json")) as f:
            golden = json.load(f)
        self.assertEqual(len(golden), 11)
        for g in golden:
            e = Engine(Config(**g["cfg"]))
            r = e.run()
            tag = str(g["cfg"])
            self.assertEqual(r["winner"], g["winner"], tag)
            self.assertEqual(r["ticks"], g["ticks"], tag)
            self.assertEqual([x["id"] for x in r["rankings"]], g["order"], tag)
            self.assertEqual([x["damage_dealt"] for x in r["rankings"]], g["dmg"], tag)
            self.assertEqual(legacy_digest(e.log.events), g["legacy_digest"], tag)

    def test_legacy_events_keep_their_exact_fields(self):
        e = Engine(Config(seed=1, n_combatants=24)); e.run()
        h = next(x for x in e.log.events if x["e"] == "hit")
        self.assertEqual(set(h) - {"crit", "eff", "miss", "immune", "sturdy"}, {"t", "e", "a", "d", "mv", "dmg", "hp"})

    def test_stub_roster_never_uses_new_moves_so_defaults_are_untouched(self):
        e = Engine(Config(seed=2, n_combatants=24)); e.run()
        for k in ("stat_change", "heal", "protect", "recoil", "move_blocked"):
            if k == "heal":                     # only existing sources: Leftovers and berries
                self.assertTrue(all(x["src"] in ("item", "berry") for x in evs(e, k)))
            elif k == "recoil":                 # only existing Life Orb recoil, never move recoil
                self.assertTrue(all(x["src"] == "item" and x["mv"] == "life_orb" for x in evs(e, k)))
            else:
                self.assertEqual(evs(e, k), [], k)

    def test_840_entry_roster_loader_standin(self):
        """STAND-IN: the real 840-entry importer output is not available in this workspace. This exercises
        the real JsonProvider at that scale with a generated 840-build file."""
        cfg = Config(seed=1, n_combatants=840, roster_source="synthetic")
        pool = SyntheticProvider(combat_moves=True).load_roster(cfg)
        self.assertEqual(len(pool), 840)
        rows = [{"species": b.species, "types": list(b.types), "stats": b.stats, "moves": b.moves,
                 "ability": b.ability, "item": b.item, "can_fly": b.can_fly} for b in pool]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "builds.json")
            with open(path, "w") as f:
                json.dump(rows, f)
            loaded = JsonProvider(path).load_roster(cfg)
            self.assertEqual(len(loaded), 840)
            self.assertEqual([(b.species, b.types, b.stats, b.moves) for b in loaded],
                             [(b.species, b.types, b.stats, b.moves) for b in pool])
            self.assertEqual(len({b.species for b in loaded}), 840)
            eng = Engine(Config(seed=3, n_combatants=24, roster_source=path)); eng.run()
            self.assertEqual(sorted(c.placement for c in eng.by_id), list(range(1, 25)))
            rows[0]["moves"] = ["totally_unknown_move"]
            with open(path, "w") as f:
                json.dump(rows, f)
            with self.assertRaises(ValueError):
                JsonProvider(path).load_roster(cfg)

    def test_synthetic_default_roster_unchanged_by_new_moves(self):
        cfg = Config(seed=4, n_combatants=60, roster_source="synthetic")
        legacy_names = {m.name for m in MOVES.values() if not m.effects and m.category != "status"}
        for b in SyntheticProvider().load_roster(cfg):
            self.assertTrue(set(b.moves) <= legacy_names)


if __name__ == "__main__":
    unittest.main()
