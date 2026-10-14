"""Regenerates showdown_ts/data/*.ts. SYNTHETIC test fixtures shaped like Showdown's data files.
They are NOT real Pokemon data (except the engine-registry move numbers and a few stub-table base stats).
Usage (from project root): python data_pipeline/tests/fixtures/_generate_ts_fixtures.py
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "Claude engine"))
from pkmn_br.data import MOVES            # noqa: E402
from pkmn_br.rules import CHART, TYPES    # noqa: E402

OUT = Path(__file__).resolve().parent / "showdown_ts" / "data"
OUT.mkdir(parents=True, exist_ok=True)
HDR = "// SYNTHETIC TEST FIXTURE - not real Pokemon Showdown data.\n"
J = lambda o: json.dumps(o, indent=1)
ST = lambda hp, atk, d, spa, spd, spe: {"hp": hp, "atk": atk, "def": d, "spa": spa, "spd": spd, "spe": spe}
CODE = {"burn": "brn", "para": "par", "poison": "psn"}


def table(header, name, entries):
    body = ",\n".join(f"{json.dumps(k)}: {v if isinstance(v, str) else J(v)}" for k, v in entries.items())
    return f"{header}export const {name}: import('../sim/dex').DataTable = {{\n{body}\n}};\n"


# ---------- pokedex ----------
def sp(num, name, types, stats, abilities=None, **kw):
    d = {"num": num, "name": name, "types": types, "baseStats": ST(*stats),
         "abilities": abilities or {"0": "Pressure"}, "heightm": 1.0, "weightkg": 10.0}
    d.update(kw)
    return d

dex = {
 "gible": sp(443, "Gible", ["Dragon", "Ground"], (58, 70, 45, 40, 45, 42), {"0": "Sand Veil", "H": "Rough Skin"}, evos=["Gabite"]),
 "gabite": sp(444, "Gabite", ["Dragon", "Ground"], (68, 90, 65, 50, 55, 82), {"0": "Sand Veil", "H": "Rough Skin"}, prevo="Gible", evos=["Garchomp"]),
 "garchomp": sp(445, "Garchomp", ["Dragon", "Ground"], (108, 130, 95, 80, 85, 102), {"0": "Sand Veil", "H": "Rough Skin"}, prevo="Gabite"),
 "charizard": sp(6, "Charizard", ["Fire", "Flying"], (78, 84, 78, 109, 85, 100), {"0": "Blaze", "H": "Solar Power"}, otherFormes=["Charizard-Mega-X"]),
 "charizardmegax": sp(6, "Charizard-Mega-X", ["Fire", "Dragon"], (78, 130, 111, 130, 85, 100), {"0": "Tough Claws"}, baseSpecies="Charizard", forme="Mega-X", requiredItem="Charizardite X"),
 "fxtail": sp(9001, "Fxtail", ["Electric"], (60, 90, 55, 90, 80, 110), otherFormes=["Fxtail-Alola", "Fxtail-Galar"]),
 "fxtailalola": sp(9001, "Fxtail-Alola", ["Electric", "Psychic"], (60, 85, 50, 95, 85, 110), baseSpecies="Fxtail", forme="Alola"),
 "fxtailgalar": sp(9001, "Fxtail-Galar", ["Electric", "Steel"], (60, 85, 50, 95, 85, 110), baseSpecies="Fxtail", forme="Galar"),
 "fxrotom": sp(9002, "Fxrotom", ["Electric", "Ghost"], (50, 50, 77, 95, 77, 91), {"0": "Levitate"}, otherFormes=["Fxrotom-Wash"]),
 "fxrotomwash": sp(9002, "Fxrotom-Wash", ["Electric", "Water"], (50, 65, 107, 105, 107, 86), {"0": "Levitate"}, baseSpecies="Fxrotom", forme="Wash"),
 "fxvivi": sp(9003, "Fxvivi", ["Bug", "Flying"], (80, 52, 50, 90, 50, 89), cosmeticFormes=["Fxvivi-Polar"]),
 "fxvivipolar": sp(9003, "Fxvivi-Polar", ["Bug", "Flying"], (80, 52, 50, 90, 50, 89), baseSpecies="Fxvivi", forme="Polar"),
 "fxogre": sp(9004, "Fxogre", ["Grass"], (80, 120, 84, 60, 96, 110), otherFormes=["Fxogre-Wellspring"]),
 "fxogrewellspring": sp(9004, "Fxogre-Wellspring", ["Grass", "Water"], (80, 120, 84, 60, 96, 110), baseSpecies="Fxogre", forme="Wellspring", requiredItem="Wellspring Mask"),
 "fxdarm": sp(9005, "Fxdarm", ["Fire"], (105, 140, 55, 30, 55, 95), otherFormes=["Fxdarm-Zen"]),
 "fxdarmzen": sp(9005, "Fxdarm-Zen", ["Fire", "Psychic"], (105, 30, 105, 140, 105, 55), baseSpecies="Fxdarm", forme="Zen", battleOnly="Fxdarm"),
 "fxcap": sp(-1, "Fxcap", ["Normal"], (50, 50, 50, 50, 50, 50), isNonstandard="CAP"),
 "fxpast": sp(9006, "Fxpast", ["Normal"], (60, 60, 60, 60, 60, 60), isNonstandard="Past"),
 "fxpast2": sp(9007, "Fxpast2", ["Normal"], (60, 60, 60, 60, 60, 60)),
 "fxbebe": sp(9008, "Fxb\u00e9b\u00e9", ["Fairy"], (44, 38, 39, 61, 79, 42), evos=["Fxfloette"]),
 "fxfloette": sp(9009, "Fxfloette", ["Fairy"], (54, 45, 47, 75, 98, 52), prevo="Fxb\u00e9b\u00e9"),
 "fxnoteng": sp(9010, "Fxnoteng", ["Normal"], (70, 70, 70, 70, 70, 70)),
 "fxlev": sp(9011, "Fxlev", ["Ghost"], (60, 65, 60, 130, 75, 110), {"0": "Levitate"}),
}
(OUT / "pokedex.ts").write_text(HDR + "import type {SpeciesData} from '../sim/dex-species';\n" + table("", "Pokedex", dex))
fmt = {"charizardmegax": {"isNonstandard": "Past", "tier": "Illegal"}, "fxpast2": {"isNonstandard": "Past", "tier": "Illegal"},
       "garchomp": {"tier": "OU"}}
(OUT / "formats-data.ts").write_text(table(HDR, "FormatsData", fmt))

# ---------- moves ----------
moves = {}
def title(eid): return " ".join(w.capitalize() for w in eid.split("_"))
for eid, m in sorted(MOVES.items()):
    name = title(eid)
    e = {"num": 1, "name": name, "type": m.type.capitalize(), "category": m.category.capitalize(), "basePower": m.power,
         "accuracy": m.accuracy, "pp": 10, "priority": m.priority, "target": "normal", "flags": {"protect": 1}}
    if eid in ("earthquake", "surf", "hyper_voice"):
        e["target"] = "allAdjacent" if eid == "earthquake" else "allAdjacentFoes"
    if m.status:
        e["secondary"] = {"chance": int(round(m.status_chance * 100)), "status": CODE[m.status]}
    if eid == "flare_blitz": e["recoil"] = [33, 100]
    if eid == "close_combat": e["self"] = {"boosts": {"def": -1, "spd": -1}}
    if eid == "air_slash": e["secondary"] = {"chance": 30, "volatileStatus": "flinch"}
    moves["".join(c for c in name.lower() if c.isalnum())] = e
moves["protect"] = {"num": 182, "name": "Protect", "type": "Normal", "category": "Status", "basePower": 0, "accuracy": True,
                    "pp": 10, "priority": 4, "target": "self", "flags": {}}
moves["scald"] = {"num": 503, "name": "Scald", "type": "Water", "category": "Special", "basePower": 80, "accuracy": 100,
                  "pp": 15, "priority": 0, "target": "normal", "flags": {"protect": 1}, "secondary": {"chance": 30, "status": "brn"}}
moves["fxpastmove"] = {"num": 9001, "name": "Fxpastmove", "type": "Normal", "category": "Physical", "basePower": 50, "accuracy": 100,
                       "pp": 10, "priority": 0, "target": "normal", "flags": {}, "isNonstandard": "Past"}
moves["fxzmove"] = {"num": 9002, "name": "Fxzmove", "type": "Normal", "category": "Physical", "basePower": 150, "accuracy": True,
                    "pp": 1, "priority": 0, "target": "normal", "flags": {}, "isZ": "fxcrystal"}
scripted = """{
 num: 9003, name: "Fxscripted", type: "Normal", category: "Physical", basePower: 0, accuracy: 100, pp: 10, priority: 0, target: "normal", flags: {},
 basePowerCallback(pokemon: any, target: any) { return 60; },
 onHit(target: any, source: any) { return true; },
}"""
moves["fxscripted"] = scripted
(OUT / "moves.ts").write_text(table(HDR, "Moves", moves))

# ---------- abilities / items / natures ----------
ab = {k: {"num": i + 1, "name": n} for i, (k, n) in enumerate([("blaze", "Blaze"), ("levitate", "Levitate"), ("pressure", "Pressure"),
      ("roughskin", "Rough Skin"), ("sandveil", "Sand Veil"), ("solarpower", "Solar Power"), ("sturdy", "Sturdy"), ("toughclaws", "Tough Claws")])}
ab["pressure"]["onStart"] = "__fn__"
abt = table(HDR, "Abilities", ab).replace('"__fn__"', "function(pokemon: any) { return; }")
(OUT / "abilities.ts").write_text(abt)
items = {"lifeorb": {"num": 270, "name": "Life Orb"}, "leftovers": {"num": 234, "name": "Leftovers"},
         "choiceband": {"num": 220, "name": "Choice Band"}, "choicespecs": {"num": 297, "name": "Choice Specs"},
         "wellspringmask": {"num": 2407, "name": "Wellspring Mask", "itemUser": ["Fxogre-Wellspring"], "forcedForme": "Fxogre-Wellspring"},
         "fxpastitem": {"num": 9100, "name": "Fxpastitem", "isNonstandard": "Past"},
         "fxmegastone": {"num": 9101, "name": "Fxmegastone", "megaStone": "Fxmon-Mega"},
         "charizarditex": {"num": 660, "name": "Charizardite X", "megaStone": "Charizard-Mega-X", "isNonstandard": "Past"}}
(OUT / "items.ts").write_text(table(HDR, "Items", items))
nat = {"adamant": {"name": "Adamant", "plus": "atk", "minus": "spa"}, "modest": {"name": "Modest", "plus": "spa", "minus": "atk"},
       "bold": {"name": "Bold", "plus": "def", "minus": "atk"}, "hardy": {"name": "Hardy"}}
(OUT / "natures.ts").write_text(table(HDR, "Natures", nat))

# ---------- typechart (derived from the engine's CHART by inverting to Showdown's damageTaken codes) ----------
MULT_TO_CODE = {1.0: 0, 2.0: 1, 0.5: 2, 0.0: 3}
tc = {}
for d in TYPES:
    taken = {a.capitalize(): MULT_TO_CODE[CHART[a][d]] for a in TYPES}
    taken.update({"par": 3} if d == "ground" else {})
    tc[d.capitalize()] = {"damageTaken": taken}
(OUT / "typechart.ts").write_text(table(HDR, "TypeChart", tc))

# ---------- learnsets ----------
def L(**kw): return {"learnset": {k: v for k, v in kw.items()}}
ls = {
 "gible": L(dragonclaw=["9L5", "8L5"], earthquake=["9M"], dracometeor=["9E"], protect=["9M"]),
 "gabite": L(dragonclaw=["9L1"]),
 "garchomp": L(earthquake=["9M", "8M"], closecombat=["9M"], surf=["8M"], scald=["8M"], fxpastmove=["9M"]),
 "charizard": L(flamethrower=["9M"], airslash=["9M"], dragonclaw=["9M"], earthquake=["9M"], flareblitz=["9L50"], protect=["9M"], fxscripted=["9M"]),
 "fxtail": L(thunderbolt=["9M"], airslash=["9L1"]),
 "fxtailalola": L(thunderbolt=["9M"], scald=["9M"]),
 "fxrotom": L(thunderbolt=["9M"]),
 "fxrotomwash": L(thunderbolt=["9M"], surf=["9M"], scald=["9M"]),
 "fxvivi": L(airslash=["9L1"]),
 "fxogre": L(earthquake=["9M"]),
 "fxogrewellspring": L(earthquake=["9M"], surf=["9S0"]),
 "fxdarm": L(earthquake=["9M"]),
 "fxbebe": L(flamethrower=["9L1"]),
 "fxnoteng": L(protect=["9M"], scald=["9M"]),
 "fxlev": L(earthquake=["8M"], thunderbolt=["9M"]),
 "fxpast": L(earthquake=["9M"]),
 "fxpast2": L(earthquake=["9M"]),
}
(OUT / "learnsets.ts").write_text(table(HDR, "Learnsets", ls))
print("wrote", sorted(p.name for p in OUT.glob("*.ts")))
