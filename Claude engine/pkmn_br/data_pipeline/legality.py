"""Build legality under a Ruleset. Returns violations (hard errors) and warnings; never raises on bad builds.

Violation codes
  ERR_SPECIES_UNKNOWN ERR_SPECIES_NOT_ELIGIBLE ERR_LEVEL ERR_NATURE
  ERR_ABILITY_ILLEGAL ERR_REQUIRED_ABILITY
  ERR_ITEM_UNKNOWN ERR_ITEM_NOT_ELIGIBLE ERR_ITEM_USER ERR_ITEM_FORCES_FORM ERR_ITEM_REQUIRED
  ERR_MOVE_COUNT ERR_MOVE_UNKNOWN ERR_MOVE_DUPLICATE ERR_MOVE_NOT_ELIGIBLE
  ERR_MOVE_NOT_LEARNABLE ERR_MOVE_EVENT_ONLY ERR_REQUIRED_MOVE
  ERR_EV_RANGE ERR_EV_TOTAL ERR_IV_RANGE
"""
from __future__ import annotations
import json
from pathlib import Path

from .dataset import Dataset
from .learnsets import move_legality
from .ranges import STATS, norm_table, table_errors
from .ruleset import Ruleset, species_verdict


def check_build(build: dict, ds: Dataset, rs: Ruleset) -> dict:
    V, W = [], []

    def bad(code, detail):
        V.append({"code": code, "detail": detail})

    ref = build.get("species", build.get("species_id"))
    sid = ds.species_id(ref)
    if sid is None:
        bad("ERR_SPECIES_UNKNOWN", f"{ref!r}")
        return {"species_id": None, "violations": V, "warnings": W}
    sp = ds.species[sid]
    verdict = species_verdict(sp, rs)
    if not verdict["eligible"]:
        bad("ERR_SPECIES_NOT_ELIGIBLE", f"{sid}: {', '.join(verdict['reasons'])}")

    if build.get("level", rs.level) != rs.level:
        bad("ERR_LEVEL", f"level {build.get('level')} != {rs.level}")

    if ds.nature_id(build.get("nature")) is None:
        bad("ERR_NATURE", f"{build.get('nature')!r}")

    # ability
    legal_abilities = {a for a in sp["ability_ids"].values() if a}
    aid = ds.ability_id(build.get("ability")) if build.get("ability") else None
    if aid is None or aid not in legal_abilities:
        bad("ERR_ABILITY_ILLEGAL", f"{build.get('ability')!r} not in {sorted(legal_abilities)} for {sid}")
    if sp["required_ability"] and aid != sp["required_ability"]:
        bad("ERR_REQUIRED_ABILITY", f"{sid} requires {sp['required_ability']}")

    # item
    iid = None
    if build.get("item"):
        iid = ds.item_id(build["item"])
        if iid is None:
            bad("ERR_ITEM_UNKNOWN", f"{build['item']!r}")
        else:
            it = ds.items[iid]
            if it["is_nonstandard"] or it["is_mega_stone"] or it["is_z_crystal"]:
                bad("ERR_ITEM_NOT_ELIGIBLE", f"{iid}: nonstandard={it['is_nonstandard']} mega={it['is_mega_stone']} z={it['is_z_crystal']}")
            if it["item_user"] and sid not in it["item_user"]:
                bad("ERR_ITEM_USER", f"{iid} can only be held by {it['item_user']}")
            if it["forced_forme"] and it["forced_forme"] != sid:
                bad("ERR_ITEM_FORCES_FORM", f"{iid} forces {it['forced_forme']}, species is {sid}")
    if sp["required_items"] and iid not in sp["required_items"]:
        bad("ERR_ITEM_REQUIRED", f"{sid} requires one of {sp['required_items']}")

    # moves
    moves = build.get("moves") or []
    if not 1 <= len(moves) <= 4:
        bad("ERR_MOVE_COUNT", f"{len(moves)} moves")
    seen = []
    for m in moves:
        mid = ds.move_id(m)
        if mid is None:
            bad("ERR_MOVE_UNKNOWN", f"{m!r}")
            continue
        if mid in seen:
            bad("ERR_MOVE_DUPLICATE", mid)
            continue
        seen.append(mid)
        mv = ds.moves[mid]
        if mv["is_nonstandard"] or mv["is_z"] or mv["is_max"]:
            bad("ERR_MOVE_NOT_ELIGIBLE", f"{mid}: nonstandard={mv['is_nonstandard']} z={mv['is_z']} max={mv['is_max']}")
            continue
        legal, status = move_legality(ds.learnsets.get(sid, {}).get(mid), rs.regular_sources, rs.allow_event_only_moves)
        if not legal:
            bad("ERR_MOVE_EVENT_ONLY" if status == "event_only" else "ERR_MOVE_NOT_LEARNABLE",
                f"{mid} for {sid} (Gen {rs.learnset_generation} sources)")
    if sp["required_move"] and sp["required_move"] not in seen:
        bad("ERR_REQUIRED_MOVE", f"{sid} requires {sp['required_move']}")

    # EVs / IVs (distinct tables, distinct ranges; zeros are legal)
    evs = norm_table(build.get("evs") or {}, default=0)
    for e in table_errors(evs, 0, rs.ev_max_each, "evs"):
        bad("ERR_EV_RANGE", e)
    if all(isinstance(evs[s], int) for s in STATS) and sum(evs.values()) > rs.ev_max_total:
        bad("ERR_EV_TOTAL", f"total {sum(evs.values())} > {rs.ev_max_total}")
    ivs_in = build.get("ivs", rs.iv_max)
    ivs = {s: ivs_in for s in STATS} if isinstance(ivs_in, int) and not isinstance(ivs_in, bool) else norm_table(dict(ivs_in), default=rs.iv_max)
    for e in table_errors(ivs, rs.iv_min, rs.iv_max, "ivs"):
        bad("ERR_IV_RANGE", e)

    if build.get("tera_type"):
        W.append({"code": "WARN_TERA_NOT_MODELLED", "detail": f"tera_type={build['tera_type']!r} is ignored"})
    return {"species_id": sid, "violations": V, "warnings": W}


def check_file(path, gen9_dir, rs: Ruleset) -> list:
    rows = json.loads(Path(path).read_text())
    if isinstance(rows, dict):
        rows = [rows]
    ds = Dataset.load(gen9_dir)
    return [check_build(r, ds, rs) for r in rows]
