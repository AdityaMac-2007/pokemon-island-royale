"""Schema tests: base stats vs IVs vs EVs. Skipped when pydantic is not installed (pip install pydantic)."""
import os, sys, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
try:
    import pydantic  # noqa: F401
    from pydantic import ValidationError
    from gemini.Schema import BaseStatsTable, BattleRoyaleBuild, EVTable, IVTable, StatsTable
except ImportError:  # pragma: no cover
    pydantic = None


@unittest.skipIf(pydantic is None, "pydantic not installed")
class TestStatTables(unittest.TestCase):
    def test_base_stats_ranges(self):
        BaseStatsTable(hp=1, atk=255, **{"def": 1}, spa=1, spd=1, spe=1)       # 1 and 255 legal
        with self.assertRaises(ValidationError):
            BaseStatsTable(hp=0, atk=1, **{"def": 1}, spa=1, spd=1, spe=1)     # 0 not a legal base stat
        with self.assertRaises(ValidationError):
            BaseStatsTable(hp=256, atk=1, **{"def": 1}, spa=1, spd=1, spe=1)

    def test_zero_ivs_and_evs_are_legal(self):
        self.assertEqual(IVTable(**{"def": 0}, atk=0).def_, 0)
        self.assertEqual(EVTable().atk, 0)
        b = BattleRoyaleBuild(species_id="garchomp", nature="Jolly", ability="Rough Skin", item="Life Orb",
                              moves=["earthquake"], evs=EVTable(atk=252, spe=252), ivs=IVTable(atk=0))
        self.assertEqual(b.evs.hp, 0)
        self.assertEqual(b.ivs.atk, 0)
        self.assertEqual(b.ivs.spe, 31)

    def test_iv_and_ev_upper_bounds(self):
        with self.assertRaises(ValidationError):
            IVTable(atk=32)
        with self.assertRaises(ValidationError):
            EVTable(atk=253)
        with self.assertRaises(ValidationError):
            EVTable(atk=-1)

    def test_ev_total_limit(self):
        with self.assertRaises(ValidationError):
            BattleRoyaleBuild(species_id="garchomp", nature="Jolly", ability="a", item="b", moves=["earthquake"],
                              evs=EVTable(hp=252, atk=252, **{"def": 252}))

    def test_defaults_without_explicit_tables(self):
        b = BattleRoyaleBuild(species_id="garchomp", nature="Jolly", ability="a", item="b", moves=["earthquake"])
        self.assertEqual(b.ivs.hp, 31)
        self.assertEqual(b.evs.hp, 0)

    def test_legacy_alias_is_base_stats(self):
        self.assertIs(StatsTable, BaseStatsTable)


if __name__ == "__main__":
    unittest.main()
