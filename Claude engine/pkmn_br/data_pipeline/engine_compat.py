"""Engine-compatible records. The engine's move registry is hard-coded, so a move is engine-supported only if
its snake_case name is already in pkmn_br.data.MOVES. This module never adds moves to the engine and never
invents reach/hits_air tags: those come from the engine registry itself."""
from __future__ import annotations
import importlib
import json
import sys
from pathlib import Path

from .dataset import Dataset
from .learnsets import move_legality
from .legality import check_build
from .ruleset import Ruleset, species_verdict

PLAYABLE_FORM_CLASSES = {"base", "regional", "permanent_form"}
SPREAD_TARGETS = {"allAdjacent", "allAdjacentFoes", "allySide", "foeSide", "all", "allies", "allyTeam"}
# 'sturdy' is hard-coded in engine.py (_apply_hit) rather than in a rules table; a test pins this against the adapter.
HARDCODED_ABILITIES = {"sturdy"}
DEFAULT_ITEM = "life_orb"


class EngineError(RuntimeError):
    pass


class EngineView:
    def __init__(self, data, rules):
        self.data, self.rules = data, rules
        self.moves = dict(data.MOVES)
        self.functional_abilities = set(rules.PINCH_ABILITY) | set(rules.GROUND_IMMUNE_ABILITIES) | HARDCODED_ABILITIES
        self.functional_items = set(rules.ITEMS)


def load_engine(engine_dir) -> EngineView:
    p = str(Path(engine_dir).resolve())
    if p not in sys.path:
        sys.path.insert(0, p)
    try:
        return EngineView(importlib.import_module("pkmn_br.data"), importlib.import_module("pkmn_br.rules"))
    except ImportError as exc:
        raise EngineError(f"cannot import Claude's engine package pkmn_br from {p!r}: {exc}") from exc


def load_flight_overrides(overrides_dir) -> dict:
    path = Path(overrides_dir) / "flight.json"
    return json.loads(path.read_text())["species"] if path.exists() else {}


def approximation_notes(rec: dict) -> list:
    """Canonical effects the engine does NOT model for this move (informational)."""
    notes = []
    if rec["target"] in SPREAD_TARGETS:
        notes.append(f"target:{rec['target']}")
    for k, v in rec["effects"].items():
        if v not in (None, False):
            notes.append(k)
    for s in rec["secondaries"]:
        if s["other"]:
            notes.append("secondary:" + ",".join(s["other"]))
        if s["status"] and s["status"] not in ("brn", "par", "psn"):
            notes.append(f"secondary_status:{s['status']}")
    for f in ("charge", "recharge"):
        if f in rec["flags"]:
            notes.append(f"flag:{f}")
    if rec["handlers"]:
        notes.append("scripted:" + ",".join(rec["handlers"]))
    return sorted(set(notes))


def registry_audit(ds: Dataset, eng: EngineView) -> dict:
    by_engine = {m["engine_id"]: m for m in ds.moves.values()}
    resolved, unresolved, mismatches, approx = [], [], [], {}
    for eid, mv in sorted(eng.moves.items()):
        rec = by_engine.get(eid)
        if rec is None:
            unresolved.append(eid)
            continue
        resolved.append(eid)
        acc = 100 if rec["always_hits"] else rec["accuracy"]
        diffs = {}
        for label, ours, theirs in (("type", mv.type, rec["type"]), ("category", mv.category, rec["category"]),
                                    ("power", mv.power, rec["base_power"]), ("accuracy", mv.accuracy, acc),
                                    ("priority", mv.priority, rec["priority"])):
            if ours != theirs:
                diffs[label] = {"engine": ours, "showdown": theirs}
        if diffs:
            mismatches.append({"move": eid, "diffs": diffs})
        approx[eid] = {"reach": mv.reach, "hits_air": mv.hits_air, "not_modelled": approximation_notes(rec)}
    return {"engine_moves": len(eng.moves), "resolved": resolved, "unresolved": unresolved,
            "mismatches": mismatches, "move_notes": approx}


def supported_moves(sid: str, ds: Dataset, eng: EngineView, rs: Ruleset) -> list:
    out = []
    for mid, entry in ds.learnsets.get(sid, {}).items():
        mv = ds.moves.get(mid)
        if not mv or mv["is_nonstandard"] or mv["is_z"] or mv["is_max"]:
            continue
        legal, _ = move_legality(entry, rs.regular_sources, rs.allow_event_only_moves)
        if legal and mv["engine_id"] in eng.moves:
            out.append(mv["engine_id"])
    return sorted(set(out))


def flight_status(sid: str, overrides: dict) -> tuple:
    return (bool(overrides[sid]), "reviewed") if sid in overrides else (False, "unreviewed")


def _default_build(sid, sp, sup, ds, rs, eng, overrides):
    base = sp["base_stats"]
    phys = base["atk"] >= base["spa"]
    nature, up, down = ("adamant", "atk", "spa") if phys else ("modest", "spa", "atk")

    def score(eid):
        mv = eng.moves[eid]
        s = mv.power * (mv.accuracy / 100.0) * (1.5 if mv.type in sp["types"] else 1.0)
        return s * (1.0 if (mv.category == "physical") == phys else 0.6)
    moves = sorted(sup, key=lambda e: (-score(e), e))[:4]
    legal_ab = [(slot, ds.abilities[a]["engine_id"]) for slot, a in sorted(sp["ability_ids"].items(), key=lambda kv: "01HS".index(kv[0]) if kv[0] in "01HS" else 9) if a]
    functional = [x for x in legal_ab if x[1] in eng.functional_abilities]
    slot, ability = (functional or legal_ab or [(None, "")])[0]
    item = ds.items[sp["required_items"][0]]["engine_id"] if sp["required_items"] and sp["required_items"][0] in ds.items else DEFAULT_ITEM
    evs = {s: 0 for s in ("hp", "atk", "def", "spa", "spd", "spe")}
    evs.update({up: 252, "spe": 252, "hp": 4})
    stats = eng.rules.calc_stats(dict(base), evs, up, down, ivs=31, level=rs.level)
    can_fly, fstatus = flight_status(sid, overrides)
    genome = {"species_id": sid, "nature": nature, "ability": ability, "item": item, "moves": moves, "evs": evs, "ivs": 31}
    row = {"species": sp["name"], "types": list(sp["types"]), "stats": stats, "moves": moves, "ability": ability,
           "item": item, "can_fly": can_fly, "form": sp["forme"] or "base"}
    audit = {"species_id": sid, "nature": nature, "evs": evs, "ability_slot": slot,
             "ability_functional": ability in eng.functional_abilities, "item_functional": item in eng.functional_items,
             "flight_status": fstatus, "supported_move_count": len(sup)}
    return genome, row, audit


def build_engine_artifacts(ds: Dataset, rs: Ruleset, eng: EngineView, overrides: dict) -> dict:
    roster, audit, incompat = [], {}, {}
    for sid in sorted(ds.species):
        sp = ds.species[sid]
        v = species_verdict(sp, rs)
        if not v["eligible"]:
            continue
        if sp["form_class"] not in PLAYABLE_FORM_CLASSES:
            incompat[sid] = [f"form_class_unsupported:{sp['form_class']}"]
            continue
        sup = supported_moves(sid, ds, eng, rs)
        if not sup:
            incompat[sid] = ["no_supported_moves"]
            continue
        genome, row, aud = _default_build(sid, sp, sup, ds, rs, eng, overrides)
        res = check_build(genome, ds, rs)
        if res["violations"]:
            incompat[sid] = ["default_build_illegal:" + ",".join(sorted({x["code"] for x in res["violations"]}))]
            continue
        aud["warnings"] = [w["code"] for w in res["warnings"]]
        roster.append(row)
        audit[sid] = aud
    return {"roster": roster, "audit": audit, "incompatible": incompat}
