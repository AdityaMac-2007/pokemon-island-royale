"""Importer tests. All fixture data is SYNTHETIC (tests/fixtures); tests marked LIVE need a real build and skip otherwise."""
import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from data_pipeline.tests.helpers import (ENGINE_DIR, FAKE_SHA, FIXTURE_TS, ROOT, build_fixture, make_cache, make_lock)
from data_pipeline import ids, learnsets, normalize, source
from data_pipeline.dataset import Dataset
from data_pipeline.legality import check_build
from data_pipeline.ruleset import Ruleset, species_verdict

SUCRASE_FALLBACK = "/home/claude/.npm-global/lib/node_modules/@mermaid-js/mermaid-cli/node_modules"


class FixtureCase(unittest.TestCase):
    """Builds the fixture dataset once for the whole class."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.manifest = build_fixture(cls.root)
        cls.out = cls.root / "out"
        cls.ds = Dataset.load(cls.out)
        cls.rs = Ruleset.load()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def build(self, **over):
        b = {"species": "Garchomp", "nature": "adamant", "ability": "Rough Skin", "item": "Life Orb",
             "moves": ["earthquake", "close_combat"], "evs": {"atk": 252, "spe": 252, "hp": 4}, "ivs": 31}
        b.update(over)
        return b

    def codes(self, build, rs=None):
        return {v["code"] for v in check_build(build, self.ds, rs or self.rs)["violations"]}


class TestIds(unittest.TestCase):
    def test_showdown_and_snake_ids(self):
        self.assertEqual(ids.to_id("Close Combat"), "closecombat")
        self.assertEqual(ids.snake_id("Close Combat"), "close_combat")
        self.assertEqual(ids.snake_id("U-turn"), "u_turn")
        self.assertEqual(ids.snake_id("Will-O-Wisp"), "will_o_wisp")
        self.assertEqual(ids.snake_id("Farfetch\u2019d"), "farfetchd")
        self.assertEqual(ids.fold_id("Flab\u00e9b\u00e9"), "flabebe")


class TestEngineIdMapping(FixtureCase):
    def test_all_engine_registry_moves_resolve_with_no_stat_mismatch(self):
        from data_pipeline.engine_compat import load_engine, registry_audit
        audit = registry_audit(self.ds, load_engine(ENGINE_DIR))
        self.assertEqual(audit["unresolved"], [])
        self.assertEqual(audit["mismatches"], [])
        self.assertEqual(len(audit["resolved"]), audit["engine_moves"])

    def test_bodypress_style_lookalikes_are_not_conflated(self):
        # engine id derives from the NAME, so distinct moves can never alias to each other
        self.assertNotEqual(ids.snake_id("Body Press"), ids.snake_id("Body Slam"))

    def test_no_engine_id_collisions_in_fixture(self):
        report = json.loads((self.out / "validation_report.json").read_text())
        self.assertEqual([i for i in report["issues"] if i["code"] == "ENGINE_ID_COLLISION"], [])

    def test_move_notes_flag_unmodelled_effects(self):
        from data_pipeline.engine_compat import load_engine, registry_audit
        notes = registry_audit(self.ds, load_engine(ENGINE_DIR))["move_notes"]
        self.assertIn("recoil", notes["flare_blitz"]["not_modelled"])
        self.assertIn("target:allAdjacent", notes["earthquake"]["not_modelled"])
        self.assertIn("self_boosts", notes["close_combat"]["not_modelled"])
        self.assertIn("secondary:volatileStatus", notes["air_slash"]["not_modelled"])


class TestNormalizeAndForms(FixtureCase):
    def test_form_classes(self):
        fc = {k: v["form_class"] for k, v in self.ds.species.items()}
        self.assertEqual(fc["garchomp"], "base")
        self.assertEqual(fc["fxtailalola"], "regional")
        self.assertEqual(fc["fxrotomwash"], "permanent_form")
        self.assertEqual(fc["fxvivipolar"], "cosmetic")
        self.assertEqual(self.ds.species["fxvivipolar"]["collapse_to"], "fxvivi")
        self.assertEqual(fc["fxdarmzen"], "battle_only")
        self.assertEqual(fc["charizardmegax"], "battle_only")

    def test_nonstandard_merged_from_formats_data(self):
        self.assertEqual(self.ds.species["fxpast2"]["is_nonstandard"], "Past")   # only present in formats-data
        self.assertEqual(self.ds.species["fxpast"]["is_nonstandard"], "Past")    # present in pokedex

    def test_accent_tolerant_reference_resolution(self):
        self.assertEqual(self.ds.species["fxfloette"]["prevo"], "fxbebe")
        report = json.loads((self.out / "validation_report.json").read_text())
        self.assertEqual([i for i in report["issues"] if i["code"] == "UNRESOLVED_REF" and i["subject"] == "fxfloette"], [])

    def test_typechart_matrix_round_trips_engine_chart(self):
        from pkmn_br.rules import CHART
        m = self.ds.typechart["matrix"]
        for a in CHART:
            for d in CHART[a]:
                self.assertEqual(m[a][d], CHART[a][d], (a, d))
        self.assertEqual(set(m), set(CHART))   # status keys such as damageTaken.par are not treated as types

    def test_move_normalization(self):
        self.assertTrue(self.ds.moves["protect"]["always_hits"])
        self.assertIsNone(self.ds.moves["protect"]["accuracy"])
        self.assertEqual(self.ds.moves["protect"]["category"], "status")
        self.assertEqual(self.ds.moves["fxscripted"]["handlers"], ["basePowerCallback", "onHit"])

    def test_base_stats_accept_one_and_255_but_not_zero(self):
        issues = []
        raw = normalize.load_raw(Path(__file__).parent / "fixtures" / "export")
        raw["pokedex"]["fxlev"]["baseStats"]["hp"] = 1
        raw["pokedex"]["fxogre"]["baseStats"]["hp"] = 255
        normalize.normalize_species(raw, issues)
        self.assertEqual([i for i in issues if i["code"] == "BASE_STAT_RANGE"], [])
        raw["pokedex"]["fxlev"]["baseStats"]["hp"] = 0
        normalize.normalize_species(raw, issues)
        self.assertEqual([i["subject"] for i in issues if i["code"] == "BASE_STAT_RANGE"], ["fxlev"])


class TestLearnsets(FixtureCase):
    def test_prevo_chain_and_egg_move_inheritance(self):
        ls = self.ds.learnsets["garchomp"]
        self.assertIn("dracometeor", ls)             # only gible has it (9E)
        self.assertEqual(ls["dracometeor"]["via"], ["prevo:gible"])
        self.assertIn("dragonclaw", ls)              # from gabite/gible level-up

    def test_only_generation_9_sources_kept(self):
        self.assertNotIn("surf", self.ds.learnsets["garchomp"])        # 8M only
        self.assertEqual(self.ds.learnsets["gible"]["dragonclaw"]["sources"], ["9L5"])

    def test_forme_without_own_entry_falls_back_to_base(self):
        self.assertEqual(self.ds.learnsets["fxtailgalar"]["thunderbolt"]["via"], ["base:fxtail"])

    def test_forme_with_own_entry_does_not_inherit_base(self):
        self.assertNotIn("airslash", self.ds.learnsets["fxtailalola"])   # fxtail has it, alola's own list does not

    def test_move_availability_is_derived_per_species_not_blacklisted(self):
        # same move, different species: availability comes only from each species' learnset data
        self.assertIn("scald", self.ds.learnsets["fxrotomwash"])
        self.assertNotIn("scald", self.ds.learnsets["garchomp"])        # 8M only in the fixture
        self.assertIn("scald", self.ds.learnsets["fxtailalola"])

    def test_source_parser(self):
        self.assertEqual(learnsets.parse_source("9L50"), (9, "L", "50"))
        self.assertEqual(learnsets.parse_source("8M"), (8, "M", ""))
        self.assertEqual(learnsets.parse_source("junk"), (None, None, None))


class TestLegality(FixtureCase):
    def test_legal_build_passes(self):
        self.assertEqual(self.codes(self.build()), set())

    def test_zero_evs_and_zero_ivs_are_legal(self):
        self.assertEqual(self.codes(self.build(evs={}, ivs=0)), set())
        self.assertEqual(self.codes(self.build(evs={s: 0 for s in ("hp", "atk", "def", "spa", "spd", "spe")},
                                               ivs={"hp": 31, "atk": 0, "def": 31, "spa": 31, "spd": 31, "spe": 31})), set())

    def test_ev_and_iv_bounds(self):
        self.assertIn("ERR_EV_RANGE", self.codes(self.build(evs={"atk": 253})))
        self.assertIn("ERR_EV_RANGE", self.codes(self.build(evs={"atk": -1})))
        self.assertIn("ERR_EV_TOTAL", self.codes(self.build(evs={"hp": 252, "atk": 252, "def": 252})))
        self.assertIn("ERR_IV_RANGE", self.codes(self.build(ivs=32)))
        self.assertIn("ERR_IV_RANGE", self.codes(self.build(ivs={"hp": -1})))

    def test_learnset_rules(self):
        self.assertIn("ERR_MOVE_NOT_LEARNABLE", self.codes(self.build(moves=["surf"])))       # 8M only
        self.assertIn("ERR_MOVE_NOT_ELIGIBLE", self.codes(self.build(moves=["fxpastmove"])))  # isNonstandard move
        self.assertIn("ERR_MOVE_UNKNOWN", self.codes(self.build(moves=["nonexistent"])))
        self.assertIn("ERR_MOVE_DUPLICATE", self.codes(self.build(moves=["earthquake", "Earthquake"])))
        self.assertEqual(self.codes(self.build(moves=["dragon_claw", "draco_meteor"])), set())  # via prevo chain
        self.assertIn("ERR_MOVE_COUNT", self.codes(self.build(moves=[])))

    def test_event_only_move_policy(self):
        b = self.build(species="Fxogre-Wellspring", ability="Pressure", item="Wellspring Mask", moves=["surf"])
        self.assertIn("ERR_MOVE_EVENT_ONLY", self.codes(b))
        from dataclasses import replace
        self.assertEqual(self.codes(b, replace(self.rs, allow_event_only_moves=True)), set())

    def test_species_ability_item_rules(self):
        self.assertIn("ERR_SPECIES_UNKNOWN", self.codes(self.build(species="Nopemon")))
        self.assertIn("ERR_SPECIES_NOT_ELIGIBLE", self.codes(self.build(species="Fxpast", ability="Pressure", moves=["earthquake"])))
        self.assertIn("ERR_SPECIES_NOT_ELIGIBLE", self.codes(self.build(species="Fxpast2", ability="Pressure", moves=["earthquake"])))
        self.assertIn("ERR_SPECIES_NOT_ELIGIBLE", self.codes(self.build(species="Fxcap", ability="Pressure")))
        self.assertIn("ERR_SPECIES_NOT_ELIGIBLE", self.codes(self.build(species="Fxdarm-Zen", ability="Pressure", moves=["earthquake"])))
        self.assertIn("ERR_ABILITY_ILLEGAL", self.codes(self.build(ability="Blaze")))
        self.assertIn("ERR_ABILITY_ILLEGAL", self.codes(self.build(ability="")))
        self.assertIn("ERR_ITEM_UNKNOWN", self.codes(self.build(item="Fake Item")))
        self.assertIn("ERR_ITEM_NOT_ELIGIBLE", self.codes(self.build(item="Fxpastitem")))
        self.assertIn("ERR_ITEM_NOT_ELIGIBLE", self.codes(self.build(item="Fxmegastone")))
        self.assertIn("ERR_ITEM_USER", self.codes(self.build(item="Wellspring Mask")))
        self.assertIn("ERR_NATURE", self.codes(self.build(nature="Grumpy")))
        self.assertIn("ERR_LEVEL", self.codes(self.build(level=50)))

    def test_required_item_form(self):
        ok = self.build(species="Fxogre-Wellspring", ability="Pressure", item="Wellspring Mask", moves=["earthquake"])
        self.assertEqual(self.codes(ok), set())
        self.assertIn("ERR_ITEM_REQUIRED", self.codes(dict(ok, item="Life Orb")))

    def test_form_policy_changes_admission(self):
        b = self.build(species="Fxvivi-Polar", ability="Pressure", moves=["air_slash"])
        self.assertIn("ERR_SPECIES_NOT_ELIGIBLE", self.codes(b))
        self.assertEqual(self.codes(b, self.rs.with_form_policy("all")), set())
        self.assertIn("ERR_SPECIES_NOT_ELIGIBLE", self.codes(self.build(species="Fxtail-Alola", ability="Pressure", moves=["thunderbolt"]), self.rs.with_form_policy("base_only")))

    def test_tera_is_warned_not_rejected(self):
        r = check_build(self.build(tera_type="Fire"), self.ds, self.rs)
        self.assertEqual(r["violations"], [])
        self.assertEqual([w["code"] for w in r["warnings"]], ["WARN_TERA_NOT_MODELLED"])

    def test_accepts_gemini_genome_keys(self):
        genome = {"species_id": "garchomp", "nature": "Adamant", "ability": "rough_skin", "item": "life_orb",
                  "moves": ["earthquake", "closecombat"], "evs": {"hp": 4, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 252}}
        self.assertEqual(self.codes(genome), set())


class TestEngineCompat(FixtureCase):
    def test_roster_and_incompatibilities(self):
        roster = json.loads((self.out / "engine" / "engine_roster.json").read_text())
        incompat = json.loads((self.out / "engine" / "not_engine_compatible.json").read_text())
        names = {r["species"] for r in roster}
        self.assertIn("Garchomp", names)
        self.assertEqual(incompat["fxnoteng"], ["no_supported_moves"])   # protect/scald are not in the 36-move registry
        self.assertNotIn("Fxpast", names)
        self.assertNotIn("Fxvivi-Polar", names)                           # cosmetic: not admitted under default policy

    def test_exported_roster_loads_through_real_json_provider(self):
        from pkmn_br.config import Config
        from pkmn_br.data import JsonProvider, MOVES
        path = self.out / "engine" / "engine_roster.json"
        roster = JsonProvider(str(path)).load_roster(Config(roster_source=str(path), n_combatants=1))
        self.assertEqual(len(roster), len(json.loads(path.read_text())))
        for c in roster:
            self.assertTrue(1 <= len(c.moves) <= 4)
            self.assertTrue(all(m in MOVES for m in c.moves))

    def test_roster_can_actually_run_a_match(self):
        from pkmn_br.config import Config
        from pkmn_br.engine import Engine
        path = str(self.out / "engine" / "engine_roster.json")
        n = len(json.loads(Path(path).read_text()))
        self.assertGreaterEqual(n, 2)
        res = Engine(Config(seed=7, n_combatants=min(n, 6), roster_source=path)).run()
        self.assertIn("digest", res)

    def test_flight_is_override_only(self):
        audit = json.loads((self.out / "engine" / "engine_roster_audit.json").read_text())
        roster = {r["species"]: r for r in json.loads((self.out / "engine" / "engine_roster.json").read_text())}
        self.assertEqual(audit["charizard"]["flight_status"], "reviewed")
        self.assertTrue(roster["Charizard"]["can_fly"])
        self.assertEqual(audit["fxrotom"]["flight_status"], "unreviewed")
        self.assertFalse(roster["Fxrotom"]["can_fly"])   # Levitate ability alone never grants flight
        cov = json.loads((self.out / "coverage.json").read_text())
        self.assertIn("fxlev", cov["flight"]["unreviewed_candidates"])

    def test_default_builds_are_ruleset_legal(self):
        for sid in json.loads((self.out / "engine" / "engine_roster_audit.json").read_text()):
            self.assertTrue(species_verdict(self.ds.species[sid], self.rs)["eligible"])

    def test_flight_overrides_match_adapter_registry(self):
        try:
            from gemini.adapter import SPECIES_FLIGHT_REGISTRY
        except ImportError:
            self.skipTest("gemini.adapter not importable")
        data = json.loads((ROOT / "data" / "overrides" / "flight.json").read_text())["species"]
        self.assertEqual(data, SPECIES_FLIGHT_REGISTRY)

    def test_functional_ability_set_matches_adapter(self):
        try:
            from gemini.adapter import CLAUDE_IMPLEMENTED_ABILITIES, CLAUDE_IMPLEMENTED_ITEMS
        except ImportError:
            self.skipTest("gemini.adapter not importable")
        from data_pipeline.engine_compat import load_engine
        eng = load_engine(ENGINE_DIR)
        self.assertEqual(eng.functional_abilities, set(CLAUDE_IMPLEMENTED_ABILITIES))
        self.assertEqual(eng.functional_items, set(CLAUDE_IMPLEMENTED_ITEMS))


class TestArtifactsAndCoverage(FixtureCase):
    def test_artifact_groups_are_distinct_files(self):
        groups = self.manifest["artifact_groups"]
        allf = [f for fs in groups.values() for f in fs]
        self.assertEqual(len(allf), len(set(allf)))
        for g in ("normalized", "learnsets", "ruleset", "engine_compatible", "reports"):
            self.assertTrue(groups[g], g)
        for f in allf:
            self.assertTrue((self.out / f).exists(), f)

    def test_coverage_makes_no_blanket_claims(self):
        cov = json.loads((self.out / "coverage.json").read_text())
        e, c = cov["ruleset_eligible"]["species_entries"], cov["engine_compatible"]["species_entries"]
        self.assertLess(c, e)   # fixture has eligible-but-incompatible entries, and the report says so
        self.assertEqual(cov["engine_compatible"]["of_ruleset_eligible"], f"{c}/{e}")
        md = (self.out / "COVERAGE.md").read_text()
        self.assertIn("do not claim full Pokedex coverage", md)
        self.assertIn("not engine-verified", md)
        self.assertNotIn("100%", md)

    def test_manifest_records_source_revision_and_hashes(self):
        m = self.manifest
        self.assertEqual(m["source"]["commit"], FAKE_SHA)
        self.assertEqual(m["source"]["export"]["sucrase_version"], "0.0.0-test")
        self.assertEqual(set(m["source"]["export"]["files"]), set(normalize.RAW_FILES))
        for f, h in m["artifact_sha256"].items():
            self.assertEqual(len(h), 64, f)


class TestCacheOnlyAndDeterminism(unittest.TestCase):
    def test_build_is_offline_and_byte_identical(self):
        outs = []
        real_connect, real_run = socket.socket.connect, subprocess.run
        def no_net(*a, **k): raise AssertionError("network access attempted during build")
        def no_proc(*a, **k): raise AssertionError("subprocess spawned during build")
        socket.socket.connect, subprocess.run = no_net, no_proc
        try:
            for _ in range(2):
                with tempfile.TemporaryDirectory() as t:
                    root = Path(t)
                    build_fixture(root)
                    outs.append({str(p.relative_to(root / "out")): p.read_bytes() for p in sorted((root / "out").rglob("*")) if p.is_file()})
        finally:
            socket.socket.connect, subprocess.run = real_connect, real_run
        self.assertEqual(outs[0], outs[1])

    def test_build_refuses_without_pin_or_cache(self):
        from data_pipeline.build import BuildError, run_build
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            unpinned = root / "lock.json"
            unpinned.write_text(json.dumps({"repo": "x", "ref": "x", "commit": None}))
            with self.assertRaises(source.SourceError):
                run_build(root, root / "out", ENGINE_DIR, lock_path=unpinned)
            make_lock(unpinned)
            with self.assertRaises(BuildError):
                run_build(root, root / "out", ENGINE_DIR, lock_path=unpinned)   # pinned but nothing cached

    def test_tampered_cache_is_detected(self):
        from data_pipeline.build import BuildError, run_build
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            export = make_cache(root)
            (export / "moves.json").write_text("{}")
            with self.assertRaises(BuildError):
                run_build(root, root / "out", ENGINE_DIR, lock_path=make_lock(root / "lock.json"))

    def test_validation_errors_fail_the_build_but_still_write_report(self):
        from data_pipeline.build import BuildError, run_build
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            export = make_cache(root)
            moves = json.loads((export / "moves.json").read_text())
            moves["earthquake"]["name"] = "Earth Quake?"          # engine id earth_quake -> engine registry move unresolved
            data = json.dumps(moves).encode()
            (export / "moves.json").write_bytes(data)
            rep = json.loads((export / "export_report.json").read_text())
            import hashlib
            rep["files"]["moves"]["output_sha256"] = hashlib.sha256(data).hexdigest()
            (export / "export_report.json").write_text(json.dumps(rep))
            with self.assertRaises(BuildError):
                run_build(root, root / "out", ENGINE_DIR, lock_path=make_lock(root / "lock.json"))
            report = json.loads((root / "out" / "validation_report.json").read_text())
            self.assertTrue(any(i["code"] == "ENGINE_MOVE_NOT_IN_DATA" for i in report["issues"]))


def _node_env():
    node = shutil.which("node")
    if not node:
        return None
    if (source.NODE_DIR / "node_modules" / "sucrase").exists():
        return {}
    if Path(SUCRASE_FALLBACK, "sucrase").exists():
        return {"NODE_PATH": SUCRASE_FALLBACK}
    return None


@unittest.skipIf(shutil.which("git") is None or _node_env() is None, "git, node or sucrase unavailable")
class TestPinFetchExportEndToEnd(unittest.TestCase):
    """Exercises pin -> fetch -> TypeScript export against a throw-away LOCAL git repo built from the TS fixtures.
    This verifies the mechanism offline; it does not verify the real upstream repository."""

    def test_pin_fetch_export_and_build(self):
        env = _node_env()
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            up = t / "upstream"
            shutil.copytree(FIXTURE_TS, up)
            (up / "LICENSE").write_text("fixture license\n")
            git = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=up, check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q", "-b", "main"); git("add", "-A"); git("commit", "-q", "-m", "fixture")
            head = git("rev-parse", "HEAD")
            lock_path = t / "lock.json"
            lock = source.pin(repo=up.as_uri(), ref="main", lock_path=lock_path)
            self.assertEqual(lock["commit"], head)                                  # ref resolved to the exact SHA
            self.assertEqual(source.pin(repo=up.as_uri(), commit=head, lock_path=lock_path)["commit"], head)
            # later upstream changes must not affect the pinned fetch
            (up / "data" / "natures.ts").write_text((up / "data" / "natures.ts").read_text() + "\n// changed\n")
            git("add", "-A"); git("commit", "-q", "-m", "later")
            root = t / "proj"
            root.mkdir()
            record = source.fetch(root, source.read_lock(lock_path), node_modules=env.get("NODE_PATH"), run_npm=False)
            self.assertEqual(record["commit"], head)
            self.assertIn("LICENSE", record["license_files_sha256"])
            export = source.cache_paths(root, head)["export"]
            report = json.loads((export / "export_report.json").read_text())
            self.assertEqual(report["files"]["moves"]["function_values_dropped"], 2)
            for f in (Path(__file__).parent / "fixtures" / "export").glob("*.json"):
                self.assertEqual((export / f.name).read_bytes(), f.read_bytes(), f.name)   # extraction == golden JSON
            from data_pipeline.build import run_build
            m = run_build(root, root / "out", ENGINE_DIR, overrides_dir=ROOT / "data" / "overrides", lock_path=lock_path)
            self.assertEqual(m["source"]["commit"], head)
            self.assertTrue(m["source"]["export"]["sucrase_version"])
            self.assertEqual(m["source"]["license_files_sha256"].keys(), {"LICENSE"})

    def test_pin_rejects_bad_sha(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(source.SourceError):
                source.pin(commit="abc", lock_path=Path(t) / "l.json")

    def test_export_fails_loudly_on_unexpected_import(self):
        env = _node_env()
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            shutil.copytree(FIXTURE_TS, t / "src")
            (t / "src" / "data" / "items.ts").write_text("import {x} from './other';\nexport const Items = {a: {name: x}};\n")
            r = subprocess.run(["node", str(source.NODE_DIR / "export_showdown.cjs"), str(t / "src"), str(t / "out")],
                               capture_output=True, text=True, env={**os.environ, **env})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("unexpected require", r.stderr)


class TestLiveData(unittest.TestCase):
    """Run only when a real build exists at data/gen9 whose manifest is not the fixture. Verifies the real import."""
    @classmethod
    def setUpClass(cls):
        mp = ROOT / "data" / "gen9" / "manifest.json"
        if not mp.exists():
            raise unittest.SkipTest("LIVE: no data/gen9/manifest.json (run fetch + build first)")
        cls.m = json.loads(mp.read_text())
        if cls.m["source"]["commit"] == FAKE_SHA:
            raise unittest.SkipTest("LIVE: manifest is from the fixture")
        cls.ds = Dataset.load(ROOT / "data" / "gen9")

    def test_pinned_and_hashes(self):
        self.assertRegex(self.m["source"]["commit"], r"^[0-9a-f]{40}$")
        from data_pipeline.source import sha256_file
        for f, h in self.m["artifact_sha256"].items():
            self.assertEqual(sha256_file(ROOT / "data" / "gen9" / f), h, f)

    def test_validation_report_has_no_errors(self):
        rep = json.loads((ROOT / "data" / "gen9" / "validation_report.json").read_text())
        self.assertEqual([i for i in rep["issues"] if i["severity"] == "error"], [])

    def test_stub_species_match_imported_types_and_base_stats(self):
        from pkmn_br.data import _S
        problems = []
        for name, types, stats, *_ in _S:
            sid = self.ds.species_id(name)
            if sid is None:
                problems.append(f"{name}: not found"); continue
            sp = self.ds.species[sid]
            if tuple(sp["types"]) != tuple(types):
                problems.append(f"{name}: types {sp['types']} != {types}")
            if tuple(sp["base_stats"][s] for s in ("hp", "atk", "def", "spa", "spd", "spe")) != tuple(stats):
                problems.append(f"{name}: base stats differ")
        self.assertEqual(problems, [])

    def test_engine_chart_equals_imported_chart(self):
        from pkmn_br.rules import CHART
        for a in CHART:
            for d in CHART[a]:
                self.assertEqual(self.ds.typechart["matrix"][a][d], CHART[a][d], (a, d))

    def test_engine_registry_resolves(self):
        from data_pipeline.engine_compat import load_engine, registry_audit
        a = registry_audit(self.ds, load_engine(ENGINE_DIR))
        self.assertEqual(a["unresolved"], [])

    def test_forms_present_in_real_data_are_classified(self):
        for sid, cls_ in (("rotomwash", "permanent_form"), ("vivillonpolar", "cosmetic"), ("giratinaorigin", "permanent_form")):
            if sid in self.ds.species:
                self.assertEqual(self.ds.species[sid]["form_class"], cls_, sid)


if __name__ == "__main__":
    unittest.main()
