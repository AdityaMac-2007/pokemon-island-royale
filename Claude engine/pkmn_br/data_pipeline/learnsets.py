"""Learnset resolution from the raw per-species tables.

A species' usable moves = its own learnset entry, else (for a forme without its own entry) the
changesFrom/base species' entry, PLUS the entries of every pre-evolution up the prevo chain.
Only Gen-N sources are kept (N = ruleset.learnset_generation). Source letters:
  M tm/hm   T tutor   L level-up   E egg   -> 'regular'
  S event                                  -> 'event_only' unless a regular source also exists
  anything else                            -> recorded under other_kinds, never counted as legal
"""
from __future__ import annotations
import re

SOURCE_RE = re.compile(r"^(\d+)([A-Za-z])(.*)$")


def parse_source(s: str):
    m = SOURCE_RE.match(s)
    return (int(m.group(1)), m.group(2), m.group(3)) if m else (None, None, None)


def learnset_chain(sid: str, species: dict, raw_learnsets: dict) -> list:
    """[(species_id, via, raw_learnset_dict)] in lookup order."""
    out, seen, cur, via = [], set(), sid, "self"
    while cur and cur not in seen:
        seen.add(cur)
        ls = (raw_learnsets.get(cur) or {}).get("learnset")
        sp = species.get(cur)
        if ls is None:
            parent = None
            if sp:
                parent = sp["changes_from"] or (sp["base_species_id"] if sp["base_species_id"] != cur else None)
            if parent and parent not in seen:
                cur, via = parent, f"base:{parent}"
                continue
        else:
            out.append((cur, via, ls))
        nxt = sp["prevo"] if sp else None
        cur, via = nxt, f"prevo:{nxt}"
    return out


def resolve_species(sid: str, species: dict, raw_learnsets: dict, generation: int) -> dict:
    moves = {}
    for _owner, via, ls in learnset_chain(sid, species, raw_learnsets):
        for move_id, sources in ls.items():
            for src in sources:
                gen, kind, _ = parse_source(src)
                if gen != generation:
                    continue
                m = moves.setdefault(move_id, {"sources": set(), "kinds": set(), "via": set()})
                m["sources"].add(src)
                m["kinds"].add(kind)
                m["via"].add(via)
    return {mid: {"sources": sorted(v["sources"]), "kinds": sorted(v["kinds"]), "via": sorted(v["via"])}
            for mid, v in sorted(moves.items())}


def build_all(species: dict, raw_learnsets: dict, generation: int, issues: list) -> dict:
    out = {}
    for sid, sp in species.items():
        if (sp["num"] or 0) <= 0 and sid not in raw_learnsets:
            continue
        out[sid] = resolve_species(sid, species, raw_learnsets, generation)
    for lid in sorted(raw_learnsets):
        if lid not in species:
            issues.append({"severity": "warning", "code": "LEARNSET_UNKNOWN_SPECIES", "subject": lid, "detail": ""})
    return out


def move_legality(entry: dict | None, regular_sources, allow_event_only: bool) -> tuple:
    """-> (legal, status) where status in {'regular','event_only','not_learnable'}"""
    if not entry:
        return False, "not_learnable"
    kinds = set(entry["kinds"])
    if kinds & set(regular_sources):
        return True, "regular"
    if "S" in kinds:
        return allow_event_only, "event_only"
    return False, "not_learnable"
