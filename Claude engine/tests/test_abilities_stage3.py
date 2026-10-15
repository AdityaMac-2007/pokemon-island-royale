"""Stage 3 ability tests: Status prevention, status-triggered modifiers,
stat-stage manipulation, and offensive multipliers.

Abilities tested:
- Status immunity: limber, immunity, water_veil, purifying_salt
- Status-triggered: guts, marvel_scale
- Stat-stage manipulation: clear_body, white_smoke, contrary, simple
- Offensive multipliers: huge_power, pure_power
"""
import sys
import unittest

sys.path.insert(0, ".")
from pkmn_br import ai
from pkmn_br.abilities import get_active_abilities
from pkmn_br.capabilities import engine_capabilities
from pkmn_br.config import Config
from pkmn_br.data import Build, MOVES
from pkmn_br.effects import FX_DRAWS
from pkmn_br.engine import Engine
from pkmn_br.rules import BattleRules, STAGE_MAX, STAGE_MIN, calc_damage, expected_damage
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


class Stage3AbilitiesTest(unittest.TestCase):
    def test_capabilities_report_all_registered_abilities_and_exclude_sleep(self):
        caps = engine_capabilities()["abilities_with_effect"]
        expected = sorted([
            "blaze", "overgrow", "torrent", "swarm", "levitate", "sturdy",
            "water_absorb", "volt_absorb", "flash_fire", "thick_fat", "filter", "solid_rock",
            "limber", "immunity", "water_veil", "purifying_salt", "sand_veil",
            "guts", "marvel_scale",
            "clear_body", "white_smoke", "contrary", "simple",
            "huge_power", "pure_power",
        ])
        self.assertEqual(caps, expected)
        self.assertEqual(caps, get_active_abilities())
        for unsupported in ("insomnia", "vital_spirit", "comatose", "drizzle", "drought", "intimidate"):
            self.assertNotIn(unsupported, caps)

    def test_limber_prevents_paralysis_but_allows_burn_and_poison(self):
        b_limber = bld("Persian", ("normal",), ("hyper_voice",), ability="limber")
        b_att = bld("Attacker", ("normal",), ("thunder_wave", "thunderbolt", "flamethrower", "sludge_bomb"))
        eng, dfn, att = make_duel(b_limber, b_att)

        # 1. Status move Thunder Wave is blocked by Limber
        execute_attack_with_rolls(eng, att, dfn, "thunder_wave", rolls=(0.10, 0.99, 0.50, 0.0), t=1)
        self.assertIsNone(dfn.status)
        self.assertEqual(evs(eng, "move_blocked")[-1]["by"], "immune_ability")

        # 2. Damaging move secondary paralysis roll (rolls[3] == 0.0 < 0.10) is blocked by Limber
        execute_attack_with_rolls(eng, att, dfn, "thunderbolt", rolls=(0.10, 0.99, 0.50, 0.0), t=2)
        self.assertIsNone(dfn.status)

        # 3. Unrelated status (burn from Flamethrower, rolls[3] == 0.0 < 0.10) is NOT blocked
        execute_attack_with_rolls(eng, att, dfn, "flamethrower", rolls=(0.10, 0.99, 0.50, 0.0), t=3)
        self.assertEqual(dfn.status, "burn")

        # 4. Poison is also allowed on a fresh Limber combatant
        dfn.status = None
        self.assertTrue(eng.try_status(4, att.id, dfn, "poison"))
        self.assertEqual(dfn.status, "poison")

    def test_immunity_prevents_poison_only(self):
        b_imm = bld("Snorlax", ("normal",), ("hyper_voice",), ability="immunity")
        b_att = bld("Attacker", ("poison",), ("sludge_bomb", "flamethrower", "thunder_wave"))
        eng, dfn, att = make_duel(b_imm, b_att)

        # Sludge Bomb 30% poison proc (rolls[3] = 0.0) is prevented
        execute_attack_with_rolls(eng, att, dfn, "sludge_bomb", rolls=(0.10, 0.99, 0.50, 0.0), t=1)
        self.assertIsNone(dfn.status)
        self.assertFalse(eng.try_status(2, att.id, dfn, "poison"))
        self.assertIsNone(dfn.status)

        # Paralysis and Burn are not prevented
        self.assertTrue(eng.try_status(3, att.id, dfn, "para"))
        self.assertEqual(dfn.status, "para")
        dfn.status = None
        self.assertTrue(eng.try_status(4, att.id, dfn, "burn"))
        self.assertEqual(dfn.status, "burn")

    def test_water_veil_prevents_burn_only(self):
        b_wv = bld("Mantine", ("water",), ("surf",), ability="water_veil")
        b_att = bld("Attacker", ("fire",), ("flamethrower", "sludge_bomb"))
        eng, dfn, att = make_duel(b_wv, b_att)

        execute_attack_with_rolls(eng, att, dfn, "flamethrower", rolls=(0.10, 0.99, 0.50, 0.0), t=1)
        self.assertIsNone(dfn.status)
        self.assertFalse(eng.try_status(2, att.id, dfn, "burn"))

        # Poison still applies
        self.assertTrue(eng.try_status(3, att.id, dfn, "poison"))
        self.assertEqual(dfn.status, "poison")

    def test_purifying_salt_prevents_all_statuses_and_halves_ghost_damage(self):
        b_ps = bld("Garganacl", ("rock",), ("shadow_ball", "stone_edge"), ability="purifying_salt")
        b_norm = bld("GarganaclNo", ("rock",), ("shadow_ball", "stone_edge"), ability="")
        b_att = bld("Gengar", ("ghost",), ("shadow_ball", "sludge_bomb", "thunder_wave"))
        eng, dfn, att = make_duel(b_ps, b_att)
        dfn_norm = Combatant.from_build(5, b_norm)

        for st in ("burn", "poison", "para"):
            self.assertFalse(eng.try_status(1, att.id, dfn, st))
            self.assertIsNone(dfn.status)

        # Incoming Ghost-type damage is reduced by 50% (0.5x multiplier via MODIFY_INCOMING_DAMAGE)
        rolls = (0.0, 0.99, 1.0, 0.99)
        ghost_ps, _ = calc_damage(att, dfn, MOVES["shadow_ball"], rolls, "clear", 1.0, 0.0, eng.rules)
        ghost_no, _ = calc_damage(att, dfn_norm, MOVES["shadow_ball"], rolls, "clear", 1.0, 0.0, eng.rules)
        self.assertEqual(ghost_ps, int(ghost_no * 0.5))
        self.assertLess(ghost_ps, ghost_no)

        # Non-Ghost move (Sludge Bomb) is unchanged
        psn_ps, _ = calc_damage(att, dfn, MOVES["sludge_bomb"], rolls, "clear", 1.0, 0.0, eng.rules)
        psn_no, _ = calc_damage(att, dfn_norm, MOVES["sludge_bomb"], rolls, "clear", 1.0, 0.0, eng.rules)
        self.assertEqual(psn_ps, psn_no)

        # Outgoing Ghost-type move used BY Purifying Salt holder is NOT reduced
        out_ps, _ = calc_damage(dfn, att, MOVES["shadow_ball"], rolls, "clear", 1.0, 0.0, eng.rules)
        out_no, _ = calc_damage(dfn_norm, att, MOVES["shadow_ball"], rolls, "clear", 1.0, 0.0, eng.rules)
        self.assertEqual(out_ps, out_no)

    def test_guts_boosts_physical_when_statused_and_ignores_burn_halving(self):
        b_guts = bld("ursaring", ("normal",), ("close_combat", "hyper_voice"), ability="guts")
        b_norm = bld("ursaring_no", ("normal",), ("close_combat", "hyper_voice"), ability="")
        b_dfn = bld("Target", ("normal",), ("hyper_voice",))
        rules = BattleRules()
        rolls = (0.0, 0.99, 1.0, 0.99)

        c_guts = Combatant.from_build(0, b_guts)
        c_norm = Combatant.from_build(1, b_norm)
        c_300 = Combatant.from_build(2, bld("Ursaring300", ("normal",), ("close_combat",), stats=dict(STATS, atk=300)))
        dfn = Combatant.from_build(3, b_dfn)

        # 1. Healthy: Guts does NOT activate
        dmg_healthy, _ = calc_damage(c_guts, dfn, MOVES["close_combat"], rolls, "clear", 1.0, 0.0, rules)
        dmg_base, _ = calc_damage(c_norm, dfn, MOVES["close_combat"], rolls, "clear", 1.0, 0.0, rules)
        self.assertEqual(dmg_healthy, dmg_base)

        # 2. Burned: Normal combatant has Attack halved (100), Guts combatant has Attack 1.5x (300) and NO burn halving!
        c_guts.status = "burn"
        c_norm.status = "burn"
        dmg_guts_burn, _ = calc_damage(c_guts, dfn, MOVES["close_combat"], rolls, "clear", 1.0, 0.0, rules)
        dmg_norm_burn, _ = calc_damage(c_norm, dfn, MOVES["close_combat"], rolls, "clear", 1.0, 0.0, rules)
        dmg_want_300, _ = calc_damage(c_300, dfn, MOVES["close_combat"], rolls, "clear", 1.0, 0.0, rules)
        self.assertEqual(dmg_guts_burn, dmg_want_300)
        self.assertGreater(dmg_guts_burn, dmg_base)
        self.assertLess(dmg_norm_burn, dmg_base)

        # 3. Poisoned or Paralyzed: also 1.5x physical Attack
        for st in ("poison", "para"):
            c_guts.status = st
            dmg_st, _ = calc_damage(c_guts, dfn, MOVES["close_combat"], rolls, "clear", 1.0, 0.0, rules)
            self.assertEqual(dmg_st, dmg_want_300)

        # 4. Special move (hyper_voice) is NOT boosted by Guts
        spec_guts, _ = calc_damage(c_guts, dfn, MOVES["hyper_voice"], rolls, "clear", 1.0, 0.0, rules)
        spec_norm, _ = calc_damage(c_norm, dfn, MOVES["hyper_voice"], rolls, "clear", 1.0, 0.0, rules)
        self.assertEqual(spec_guts, spec_norm)

    def test_marvel_scale_boosts_physical_defense_when_statused(self):
        b_ms = bld("Milotic", ("water",), ("surf",), ability="marvel_scale")
        b_no = bld("MiloticNo", ("water",), ("surf",), ability="")
        b_att = bld("Attacker", ("normal",), ("close_combat", "hyper_voice"))
        rules = BattleRules()
        rolls = (0.0, 0.99, 1.0, 0.99)

        dfn_ms = Combatant.from_build(0, b_ms)
        dfn_no = Combatant.from_build(1, b_no)
        dfn_300 = Combatant.from_build(2, bld("Milotic300", ("water",), ("surf",), stats=dict(STATS, **{"def": 300})))
        att = Combatant.from_build(3, b_att)

        # Healthy -> no boost
        phys_healthy, _ = calc_damage(att, dfn_ms, MOVES["close_combat"], rolls, "clear", 1.0, 0.0, rules)
        phys_base, _ = calc_damage(att, dfn_no, MOVES["close_combat"], rolls, "clear", 1.0, 0.0, rules)
        self.assertEqual(phys_healthy, phys_base)

        # Burned -> 1.5x physical Defense (D = 300), Special Defense untouched
        dfn_ms.status = "burn"
        phys_statused, _ = calc_damage(att, dfn_ms, MOVES["close_combat"], rolls, "clear", 1.0, 0.0, rules)
        phys_want, _ = calc_damage(att, dfn_300, MOVES["close_combat"], rolls, "clear", 1.0, 0.0, rules)
        self.assertEqual(phys_statused, phys_want)
        self.assertLess(phys_statused, phys_base)

        spec_statused, _ = calc_damage(att, dfn_ms, MOVES["hyper_voice"], rolls, "clear", 1.0, 0.0, rules)
        spec_base, _ = calc_damage(att, dfn_no, MOVES["hyper_voice"], rolls, "clear", 1.0, 0.0, rules)
        self.assertEqual(spec_statused, spec_base)

    def test_clear_body_and_white_smoke_block_opponent_drops_but_allow_self_drops_and_boosts(self):
        for ability in ("clear_body", "white_smoke"):
            b_holder = bld("Dragapult", ("dragon",), ("superpower", "swords_dance"), ability=ability)
            b_opp = bld("FlutterMane", ("normal",), ("shadow_ball", "icy_wind"))
            eng, holder, opp = make_duel(b_holder, b_opp)

            # 1. Opponent-caused stat drop (Icy Wind: -1 spe on target) is blocked
            execute_attack_with_rolls(eng, opp, holder, "icy_wind", rolls=(0.10, 0.99, 0.50, 0.99), fx=FX_PASS, t=1)
            self.assertEqual(holder.stages["spe"], 0)
            trig = [e for e in evs(eng, "ability_trigger") if e.get("ability") == ability]
            self.assertEqual(len(trig), 1)
            self.assertEqual(trig[0]["stat"], "spe")

            # 2. Self-caused stat boost (Swords Dance: +2 atk on self) works normally
            execute_attack_with_rolls(eng, holder, opp, "swords_dance", rolls=(0.10, 0.99, 0.50, 0.99), fx=FX_PASS, t=2)
            self.assertEqual(holder.stages["atk"], 2)

            # 3. Self-inflicted stat drop (Close Combat: -1 def, -1 spd on self) is NOT blocked
            execute_attack_with_rolls(eng, holder, opp, "superpower", rolls=(0.10, 0.99, 0.50, 0.99), fx=FX_PASS, t=3)
            self.assertEqual(holder.stages["atk"], 1)
            self.assertEqual(holder.stages["def"], -1)

    def test_contrary_inverts_self_and_opponent_stage_changes_and_respects_caps(self):
        b_cont = bld("Serperior", ("grass",), ("superpower", "swords_dance"), ability="contrary")
        b_opp = bld("Opponent", ("normal",), ("icy_wind",))
        eng, cont, opp = make_duel(b_cont, b_opp)

        # 1. Superpower's self-inflicted -1 atk and -1 def become +1 atk and +1 def under Contrary.
        execute_attack_with_rolls(eng, cont, opp, "superpower", rolls=(0.10, 0.99, 0.50, 0.99), fx=FX_PASS, t=1)
        self.assertEqual(cont.stages["atk"], 1)
        self.assertEqual(cont.stages["def"], 1)

        # 2. Opponent Icy Wind (-1 spe) becomes +1 spe!
        execute_attack_with_rolls(eng, opp, cont, "icy_wind", rolls=(0.10, 0.99, 0.50, 0.99), fx=FX_PASS, t=2)
        self.assertEqual(cont.stages["spe"], 1)

        # 3. Swords Dance (+2 atk) becomes -2 atk, and clamps at STAGE_MIN (-6)
        for t in range(3, 7):
            execute_attack_with_rolls(eng, cont, opp, "swords_dance", rolls=(0.10, 0.99, 0.50, 0.99), fx=FX_PASS, t=t)
        self.assertEqual(cont.stages["atk"], STAGE_MIN)

    def test_simple_doubles_stage_changes_and_clamps_to_limits(self):
        b_simp = bld("Bibarel", ("normal",), ("swords_dance", "superpower"), ability="simple")
        b_opp = bld("Opponent", ("normal",), ("icy_wind",))
        eng, simp, opp = make_duel(b_simp, b_opp)

        # 1. Swords Dance (+2 atk) becomes +4 atk
        execute_attack_with_rolls(eng, simp, opp, "swords_dance", rolls=(0.10, 0.99, 0.50, 0.99), fx=FX_PASS, t=1)
        self.assertEqual(simp.stages["atk"], 4)

        # 2. Second Swords Dance (+4 atk) clamps cleanly at STAGE_MAX (+6)
        execute_attack_with_rolls(eng, simp, opp, "swords_dance", rolls=(0.10, 0.99, 0.50, 0.99), fx=FX_PASS, t=2)
        self.assertEqual(simp.stages["atk"], STAGE_MAX)

        # 3. Opponent Icy Wind (-1 spe) becomes -2 spe
        execute_attack_with_rolls(eng, opp, simp, "icy_wind", rolls=(0.10, 0.99, 0.50, 0.99), fx=FX_PASS, t=3)
        self.assertEqual(simp.stages["spe"], -2)

        # 4. Close Combat (-1 def, -1 spd) becomes -2 def, -2 spd
        execute_attack_with_rolls(eng, simp, opp, "superpower", rolls=(0.10, 0.99, 0.50, 0.99), fx=FX_PASS, t=4)
        self.assertEqual(simp.stages["atk"], 4)
        self.assertEqual(simp.stages["def"], -2)

    def test_huge_power_and_pure_power_double_physical_attack_only(self):
        rolls = (0.0, 0.99, 1.0, 0.99)
        rules = BattleRules()
        dfn = Combatant.from_build(9, bld("Target", ("normal",), ("hyper_voice",)))
        att_400 = Combatant.from_build(8, bld("Ref400", ("normal",), ("close_combat",), stats=dict(STATS, atk=400)))
        att_no = Combatant.from_build(7, bld("Ref200", ("normal",), ("close_combat", "hyper_voice")))

        for ability in ("huge_power", "pure_power"):
            att = Combatant.from_build(0, bld("PowerUser", ("normal",), ("close_combat", "hyper_voice"), ability=ability))
            phys_got, _ = calc_damage(att, dfn, MOVES["close_combat"], rolls, "clear", 1.0, 0.0, rules)
            phys_want, _ = calc_damage(att_400, dfn, MOVES["close_combat"], rolls, "clear", 1.0, 0.0, rules)
            self.assertEqual(phys_got, phys_want, ability)

            spec_got, _ = calc_damage(att, dfn, MOVES["hyper_voice"], rolls, "clear", 1.0, 0.0, rules)
            spec_want, _ = calc_damage(att_no, dfn, MOVES["hyper_voice"], rolls, "clear", 1.0, 0.0, rules)
            self.assertEqual(spec_got, spec_want, ability)

    def test_ai_prediction_does_not_mutate_state_and_respects_status_immunity(self):
        b_att = bld("Pikachu", ("electric",), ("thunder_wave", "hyper_voice"))
        b_dfn = bld("Persian", ("normal",), ("hyper_voice",), ability="limber")
        eng, att, dfn = make_duel(b_att, b_dfn)
        att.ai_policy = "tactical_v1"

        stages_before = dict(dfn.stages)
        hp_before = dfn.hp
        intent = ai.decide(eng, att, t=0)

        # AI must not choose Thunder Wave against a Limber target, and must not mutate state
        self.assertIsNone(dfn.status)
        self.assertEqual(dfn.hp, hp_before)
        self.assertEqual(dfn.stages, stages_before)
        if intent.attack is not None:
            _, move_idx = intent.attack
            self.assertNotEqual(att.moves[move_idx].name, "thunder_wave")


if __name__ == "__main__":
    unittest.main()
