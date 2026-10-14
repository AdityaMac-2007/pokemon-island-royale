"""Raw Showdown JSON export -> normalized, ruleset-independent databases.

Nothing here knows about the battle engine or the ruleset's eligibility verdicts.
"""
from __future__ import annotations
import json
from pathlib import Path

from .ids import fold_id, snake_id, to_id
from .ranges import BASE_STAT_MAX, BASE_STAT_MIN, STATS

TYPES = ["normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison", "ground",
         "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy"]
REGIONAL_PREFIXES = ("Alola", "Galar", "Hisui", "Paldea")
TYPE_CODE_TO_MULT = {0: 1.0, 1: 2.0, 2: 0.5, 3: 0.0}   # Showdown damageTaken codes
STATUS_MAP = {"brn": "burn", "par": "para", "psn": "poison"}   # the only statuses the engine models
RAW_FILES = ("pokedex", "formats-data", "moves", "abilities", "items", "learnsets", "typechart", "natures")


def load_raw(export_dir) -> dict:
    d = Path(export_dir)
    out = {}
    for name in RAW_FILES:
        p = d / f"{name}.json"
        if not p.exists():
            raise FileNotFoundError(f"missing raw export {p}; run `python -m data_pipeline fetch`")
        out[name] = json.loads(p.read_text())
    return out


def _list(v):
    if v is None or v is False:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


class Resolver:
    """Resolve a name/id reference to a key in a table, tolerant of case, punctuation and accents."""
    def __init__(self, table: dict):
        self.keys = set(table)
        self.by_name = {}
        for k, e in table.items():
            for cand in (to_id(e.get("name", k)), fold_id(e.get("name", k))):
                self.by_name.setdefault(cand, k)

    def __call__(self, ref):
        if ref is None:
            return None
        for cand in (to_id(ref), fold_id(ref)):
            if cand in self.keys:
                return cand
            if cand in self.by_name:
                return self.by_name[cand]
        return None


def classify_form(key: str, e: dict, cosmetic_ids: set) -> str:
    forme = e.get("forme") or ""
    if key in cosmetic_ids:
        return "cosmetic"
    if (e.get("battleOnly") or e.get("isMega") or e.get("isPrimal")
            or forme.startswith("Mega") or forme in ("Primal", "Gmax")):
        return "battle_only"
    if forme:
        return "regional" if forme.startswith(REGIONAL_PREFIXES) else "permanent_form"
    return "base"


def normalize_species(raw: dict, issues: list) -> dict:
    dex, fmt = raw["pokedex"], raw["formats-data"]
    ab_res, mv_res, it_res = Resolver(raw["abilities"]), Resolver(raw["moves"]), Resolver(raw["items"])
    sp_res = Resolver(dex)
    cosmetic = set()
    for k, e in dex.items():
        for n in _list(e.get("cosmeticFormes")):
            cosmetic.add(sp_res(n) or to_id(n))

    def ref(owner, field, value, resolver):
        r = resolver(value)
        if r is None:
            issues.append({"severity": "warning", "code": "UNRESOLVED_REF", "subject": owner, "detail": f"{field}={value!r}"})
        return r

    out = {}
    for k in sorted(dex):
        e = dex[k]
        f = fmt.get(k, {})
        nonstd = e.get("isNonstandard") or f.get("isNonstandard")
        name = e.get("name", k)
        base_name = e.get("baseSpecies") or name
        base_id = sp_res(base_name) or k
        changes = sp_res(e.get("changesFrom")) if e.get("changesFrom") else None
        bs = e.get("baseStats", {})
        rec = {
            "id": k, "num": e.get("num"), "name": name, "base_species": base_name, "base_species_id": base_id,
            "forme": e.get("forme") or None, "form_class": classify_form(k, e, cosmetic),
            "collapse_to": base_id if k in cosmetic else None,
            "types": [t.lower() for t in _list(e.get("types"))],
            "base_stats": {s: bs.get(s) for s in STATS},
            "abilities": {slot: n for slot, n in sorted((e.get("abilities") or {}).items())},
            "ability_ids": {}, "height_m": e.get("heightm"), "weight_kg": e.get("weightkg"),
            "prevo": sp_res(e.get("prevo")) if e.get("prevo") else None,
            "evos": [sp_res(n) or to_id(n) for n in _list(e.get("evos"))],
            "changes_from": changes,
            "battle_only": [sp_res(n) or to_id(n) for n in _list(e.get("battleOnly"))],
            "required_items": [to_id(n) for n in _list(e.get("requiredItems")) + _list(e.get("requiredItem"))],
            "required_move": to_id(e["requiredMove"]) if e.get("requiredMove") else None,
            "required_ability": to_id(e["requiredAbility"]) if e.get("requiredAbility") else None,
            "is_nonstandard": nonstd, "tier": f.get("tier"),
        }
        for field, val in (("prevo", e.get("prevo")), ("changesFrom", e.get("changesFrom"))):
            if val and sp_res(val) is None:
                issues.append({"severity": "warning", "code": "UNRESOLVED_REF", "subject": k, "detail": f"{field}={val!r}"})
        for slot, an in rec["abilities"].items():
            rec["ability_ids"][slot] = ref(k, f"abilities.{slot}", an, ab_res)
        for ri in rec["required_items"]:
            if ri not in raw["items"]:
                issues.append({"severity": "warning", "code": "UNRESOLVED_REF", "subject": k, "detail": f"required item {ri!r}"})
        if rec["required_move"] and rec["required_move"] not in raw["moves"]:
            issues.append({"severity": "warning", "code": "UNRESOLVED_REF", "subject": k, "detail": f"required move {rec['required_move']!r}"})
        if (rec["num"] or 0) > 0:
            for s in STATS:
                v = rec["base_stats"][s]
                if isinstance(v, bool) or not isinstance(v, int) or not BASE_STAT_MIN <= v <= BASE_STAT_MAX:
                    issues.append({"severity": "error", "code": "BASE_STAT_RANGE", "subject": k, "detail": f"{s}={v!r}"})
            if not rec["types"] or any(t not in TYPES for t in rec["types"]):
                issues.append({"severity": "error", "code": "BAD_TYPE", "subject": k, "detail": f"types={rec['types']!r}"})
        out[k] = rec
    return out


def _handlers(e):
    return e.get("_functions", [])


def normalize_moves(raw: dict, issues: list) -> dict:
    out = {}
    for k in sorted(raw["moves"]):
        e = raw["moves"][k]
        secs = []
        for s in ([e["secondary"]] if e.get("secondary") else []) + list(e.get("secondaries") or []):
            secs.append({"chance": s.get("chance"), "status": s.get("status"),
                         "other": sorted(x for x in s if x not in ("chance", "status"))})
        out[k] = {
            "id": k, "engine_id": snake_id(e.get("name", k)), "num": e.get("num"), "name": e.get("name", k),
            "type": str(e.get("type", "")).lower(), "category": str(e.get("category", "")).lower(),
            "base_power": e.get("basePower", 0),
            "accuracy": None if e.get("accuracy") is True else e.get("accuracy"),
            "always_hits": e.get("accuracy") is True,
            "pp": e.get("pp"), "priority": e.get("priority", 0), "target": e.get("target"),
            "flags": sorted(f for f, v in (e.get("flags") or {}).items() if v),
            "is_nonstandard": e.get("isNonstandard"), "is_z": bool(e.get("isZ")), "is_max": bool(e.get("isMax")),
            "status": e.get("status"), "secondaries": secs,
            "effects": {"recoil": e.get("recoil"), "drain": e.get("drain"), "multihit": e.get("multihit"),
                        "self_boosts": (e.get("self") or {}).get("boosts"), "self_destruct": e.get("selfdestruct"),
                        "heal": e.get("heal"), "crit_ratio": e.get("critRatio"), "ohko": e.get("ohko"),
                        "fixed_damage": e.get("damage")},
            "handlers": _handlers(e),
        }
    return out


def normalize_simple(table: dict, extra) -> dict:
    out = {}
    for k in sorted(table):
        e = table[k]
        rec = {"id": k, "engine_id": snake_id(e.get("name", k)), "num": e.get("num"), "name": e.get("name", k),
               "is_nonstandard": e.get("isNonstandard"), "handlers": _handlers(e)}
        rec.update(extra(e))
        out[k] = rec
    return out


def normalize_items(raw: dict) -> dict:
    return normalize_simple(raw["items"], lambda e: {
        "item_user": [to_id(n) for n in _list(e.get("itemUser"))],
        "forced_forme": to_id(e["forcedForme"]) if e.get("forcedForme") else None,
        "is_mega_stone": bool(e.get("megaStone")), "is_z_crystal": bool(e.get("zMove"))})


def normalize_abilities(raw: dict) -> dict:
    return normalize_simple(raw["abilities"], lambda e: {})


def normalize_natures(raw: dict) -> dict:
    return {k: {"id": k, "name": e.get("name", k), "plus": e.get("plus"), "minus": e.get("minus")}
            for k, e in sorted(raw["natures"].items())}


def normalize_typechart(raw: dict, issues: list) -> dict:
    """matrix[attacker][defender] = multiplier, from Showdown's per-defender `damageTaken` codes."""
    by_def = {k.lower(): v for k, v in raw["typechart"].items()}
    extra = sorted(set(by_def) - set(TYPES))
    if extra:
        issues.append({"severity": "warning", "code": "EXTRA_TYPES", "subject": "typechart", "detail": ",".join(extra)})
    missing = [t for t in TYPES if t not in by_def]
    if missing:
        issues.append({"severity": "error", "code": "MISSING_TYPES", "subject": "typechart", "detail": ",".join(missing)})
        return {"types": TYPES, "matrix": {}}
    matrix = {a: {} for a in TYPES}
    for d in TYPES:
        taken = {k.lower(): v for k, v in by_def[d]["damageTaken"].items()}
        for a in TYPES:
            code = taken.get(a, 0)
            if code not in TYPE_CODE_TO_MULT:
                issues.append({"severity": "error", "code": "BAD_TYPE_CODE", "subject": f"{a}->{d}", "detail": repr(code)})
                code = 0
            matrix[a][d] = TYPE_CODE_TO_MULT[code]
    return {"types": TYPES, "matrix": matrix}


def check_engine_id_collisions(tables: dict, issues: list):
    for label, table in tables.items():
        seen = {}
        for k, rec in table.items():
            seen.setdefault(rec["engine_id"], []).append(k)
        for eid, ids in sorted(seen.items()):
            if len(ids) > 1:
                issues.append({"severity": "error", "code": "ENGINE_ID_COLLISION", "subject": label, "detail": f"{eid}: {ids}"})
