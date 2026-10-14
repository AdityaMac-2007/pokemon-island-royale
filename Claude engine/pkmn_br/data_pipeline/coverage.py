"""Coverage report. Every figure is a count with its denominator; nothing here asserts 'full coverage'."""
from __future__ import annotations
from collections import Counter

from .ruleset import Ruleset, species_verdict

LIMITATIONS = [
    "A species being imported does not make it engine-playable: the engine registry is a small hard-coded subset.",
    "'engine_compatible' means >=1 legal move that already exists in the engine's registry plus a legal default build; it says nothing about balance or about the engine modelling the move's full canonical effect (see move_notes).",
    "Flight capability is never derived from data. Only species listed in data/overrides/flight.json are 'reviewed'; all others default to can_fly=false and are listed as unreviewed candidates when they look aerial.",
    "Learnset legality follows the ruleset's not_enforced list; event-only moves are excluded unless the ruleset allows them.",
    "No engine run is performed by the importer; 'engine_compatible' is not 'engine_verified'.",
]


def compute(ds, rs: Ruleset, manifest_source: dict, eng_audit: dict | None, eng_art: dict | None, flight_overrides: dict) -> dict:
    verdicts = {sid: species_verdict(sp, rs) for sid, sp in ds.species.items()}
    imported = Counter(sp["form_class"] for sp in ds.species.values())
    eligible = {sid for sid, v in verdicts.items() if v["eligible"]}
    reasons = Counter(r for v in verdicts.values() if not v['eligible'] for r in v['reasons'])
    nums_imported = {sp["num"] for sp in ds.species.values() if (sp["num"] or 0) > 0}
    nums_eligible = {ds.species[s]["num"] for s in eligible}
    cov = {
        "ruleset": rs.id, "ruleset_digest": rs.digest(), "source": manifest_source,
        "imported": {"species_entries": len(ds.species), "by_form_class": dict(sorted(imported.items())),
                     "distinct_dex_numbers": len(nums_imported), "max_dex_number": max(nums_imported, default=None),
                     "moves": len(ds.moves), "abilities": len(ds.abilities), "items": len(ds.items)},
        "ruleset_eligible": {"species_entries": len(eligible),
                             "by_form_class": dict(sorted(Counter(ds.species[s]["form_class"] for s in eligible).items())),
                             "distinct_dex_numbers": len(nums_eligible),
                             "excluded_species_reasons": dict(sorted(reasons.items()))},
        "limitations": LIMITATIONS,
    }
    cands = sorted(s for s in eligible if "flying" in ds.species[s]["types"]
                   or "levitate" in ds.species[s]["ability_ids"].values())
    cov["flight"] = {"reviewed_overrides": len(flight_overrides),
                     "overrides_not_eligible_or_unknown": sorted(s for s in flight_overrides if s not in eligible),
                     "unreviewed_candidates": [s for s in cands if s not in flight_overrides]}
    if eng_art is not None:
        hist = Counter()
        for sid, a in eng_art["audit"].items():
            n = a["supported_move_count"]
            hist["4+" if n >= 4 else str(n)] += 1
        cov["engine_compatible"] = {
            "species_entries": len(eng_art["roster"]), "of_ruleset_eligible": f"{len(eng_art['roster'])}/{len(eligible)}",
            "supported_move_count_histogram": dict(sorted(hist.items())),
            "with_4_or_more_supported_moves": hist["4+"],
            "incompatible_reasons": dict(sorted(Counter(r.split(":")[0] for rs_ in eng_art["incompatible"].values() for r in rs_).items())),
            "with_functional_legal_ability": sum(1 for a in eng_art["audit"].values() if a["ability_functional"]),
            "with_functional_default_item": sum(1 for a in eng_art["audit"].values() if a["item_functional"]),
            "flight_reviewed": sum(1 for a in eng_art["audit"].values() if a["flight_status"] == "reviewed"),
        }
    if eng_audit is not None:
        cov["engine_registry"] = {"engine_moves": eng_audit["engine_moves"], "resolved": len(eng_audit["resolved"]),
                                  "unresolved": eng_audit["unresolved"], "stat_mismatches": eng_audit["mismatches"],
                                  "move_notes": eng_audit["move_notes"]}
    return cov


def to_markdown(cov: dict) -> str:
    L = ["# Coverage report (generated)", "", f"Ruleset: `{cov['ruleset']}`  ",
         f"Source commit: `{cov['source'].get('commit')}`", "",
         "All numbers are counts from the generated data. They do not claim full Pokedex coverage.", ""]
    i, e = cov["imported"], cov["ruleset_eligible"]
    L += ["## Imported", f"- species/form entries: {i['species_entries']} ({i['by_form_class']})",
          f"- distinct dex numbers: {i['distinct_dex_numbers']} (max {i['max_dex_number']})",
          f"- moves {i['moves']}, abilities {i['abilities']}, items {i['items']}", "",
          "## Ruleset-eligible", f"- entries: {e['species_entries']} ({e['by_form_class']})",
          f"- distinct dex numbers: {e['distinct_dex_numbers']}",
          f"- exclusion reasons: {e['excluded_species_reasons']}", ""]
    if "engine_compatible" in cov:
        c = cov["engine_compatible"]
        L += ["## Engine-compatible (not engine-verified)", f"- {c['of_ruleset_eligible']} eligible entries",
              f"- supported-move histogram: {c['supported_move_count_histogram']}",
              f"- incompatible reasons: {c['incompatible_reasons']}",
              f"- with functional legal ability: {c['with_functional_legal_ability']}; functional default item: {c['with_functional_default_item']}", ""]
    f = cov["flight"]
    L += ["## Flight", f"- reviewed overrides: {f['reviewed_overrides']}",
          f"- unreviewed aerial-looking candidates: {len(f['unreviewed_candidates'])}", ""]
    if "engine_registry" in cov:
        r = cov["engine_registry"]
        L += ["## Engine move registry vs Showdown", f"- engine moves {r['engine_moves']}, resolved {r['resolved']}, unresolved {r['unresolved']}",
              f"- type/category/power/accuracy/priority mismatches: {len(r['stat_mismatches'])}", ""]
    L += ["## Limitations"] + [f"- {x}" for x in cov["limitations"]]
    return "\n".join(L) + "\n"
