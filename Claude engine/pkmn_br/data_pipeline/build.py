"""Offline build: raw export (from cache) -> data/gen9 artifacts. Never touches the network."""
from __future__ import annotations
import json
from pathlib import Path

from . import __version__, coverage, engine_compat, learnsets, normalize
from .dataset import Dataset
from .ruleset import Ruleset, species_verdict
from .source import cache_paths, read_lock, require_pinned, sha256_file

ARTIFACT_GROUPS = {
    "normalized": ["species.json", "moves.json", "abilities.json", "items.json", "natures.json", "typechart.json"],
    "learnsets": ["learnsets.json"],
    "ruleset": ["ruleset_eligibility.json"],
    "engine_compatible": ["engine/engine_roster.json", "engine/engine_roster_audit.json", "engine/not_engine_compatible.json"],
    "reports": ["validation_report.json", "coverage.json", "COVERAGE.md"],
}


class BuildError(RuntimeError):
    pass


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def verify_export(export_dir: Path) -> dict:
    report = json.loads((export_dir / "export_report.json").read_text())
    for name, meta in report["files"].items():
        got = sha256_file(export_dir / f"{name}.json")
        if got != meta["output_sha256"]:
            raise BuildError(f"raw export {name}.json does not match export_report.json (cache modified?)")
    return report


def run_build(root, out_dir, engine_dir=None, ruleset: Ruleset | None = None, overrides_dir=None,
              lock_path=None, allow_errors=False, no_engine=False) -> dict:
    root = Path(root)
    out = Path(out_dir)
    rs = ruleset or Ruleset.load()
    lock = read_lock(lock_path) if lock_path else read_lock()
    sha = require_pinned(lock)
    paths = cache_paths(root, sha)
    if not paths["export"].exists():
        raise BuildError(f"no raw export for {sha} under {paths['export']}; run `python -m data_pipeline fetch` first")
    export_report = verify_export(paths["export"])
    fetch_record = json.loads(paths["fetch_record"].read_text()) if paths["fetch_record"].exists() else {}

    raw = normalize.load_raw(paths["export"])
    issues: list = []
    species = normalize.normalize_species(raw, issues)
    moves = normalize.normalize_moves(raw, issues)
    abilities = normalize.normalize_abilities(raw)
    items = normalize.normalize_items(raw)
    natures = normalize.normalize_natures(raw)
    typechart = normalize.normalize_typechart(raw, issues)
    normalize.check_engine_id_collisions({"moves": moves, "abilities": abilities, "items": items}, issues)
    ls = learnsets.build_all(species, raw["learnsets"], rs.learnset_generation, issues)

    # learnset move ids that resolve to nothing, for species that matter
    verdicts = {sid: species_verdict(sp, rs) for sid, sp in species.items()}
    for sid, entry in ls.items():
        if verdicts.get(sid, {}).get("eligible"):
            for mid in entry:
                if mid not in moves:
                    issues.append({"severity": "error", "code": "LEARNSET_UNKNOWN_MOVE", "subject": sid, "detail": mid})
    for sid, v in verdicts.items():
        if v["eligible"]:
            for slot, a in species[sid]["ability_ids"].items():
                if a is None:
                    issues.append({"severity": "error", "code": "ELIGIBLE_SPECIES_UNRESOLVED_ABILITY", "subject": sid, "detail": slot})
    if len(natures) != 25:
        issues.append({"severity": "warning", "code": "NATURE_COUNT", "subject": "natures", "detail": str(len(natures))})

    write_json(out / "species.json", {"schema_version": 1, "species": species})
    write_json(out / "moves.json", {"schema_version": 1, "moves": moves})
    write_json(out / "abilities.json", {"schema_version": 1, "abilities": abilities})
    write_json(out / "items.json", {"schema_version": 1, "items": items})
    write_json(out / "natures.json", {"schema_version": 1, "natures": natures})
    write_json(out / "typechart.json", {"schema_version": 1, **typechart})
    write_json(out / "learnsets.json", {"schema_version": 1, "generation": rs.learnset_generation, "learnsets": ls})
    write_json(out / "ruleset_eligibility.json", {"schema_version": 1, "ruleset": rs.to_dict(), "ruleset_digest": rs.digest(),
                                                  "verdicts": verdicts})

    ds = Dataset.load(out)
    eng = eng_audit = eng_art = None
    overrides = engine_compat.load_flight_overrides(overrides_dir) if overrides_dir else {}
    if not no_engine:
        eng = engine_compat.load_engine(engine_dir)
        eng_audit = engine_compat.registry_audit(ds, eng)
        for eid in eng_audit["unresolved"]:
            issues.append({"severity": "error", "code": "ENGINE_MOVE_NOT_IN_DATA", "subject": eid, "detail": "registry move has no Showdown match"})
        eng_art = engine_compat.build_engine_artifacts(ds, rs, eng, overrides)
        write_json(out / "engine" / "engine_roster.json", eng_art["roster"])
        write_json(out / "engine" / "engine_roster_audit.json", eng_art["audit"])
        write_json(out / "engine" / "not_engine_compatible.json", eng_art["incompatible"])

    source = {"repo": lock["repo"], "commit": sha, "license_files_sha256": fetch_record.get("license_files_sha256", {}),
              "export": {"node_version": export_report["node_version"], "sucrase_version": export_report["sucrase_version"],
                         "files": {k: {"source_sha256": v["source_sha256"], "output_sha256": v["output_sha256"],
                                       "entries": v["entries"], "function_values_dropped": v["function_values_dropped"]}
                                   for k, v in sorted(export_report["files"].items())}}}
    cov = coverage.compute(ds, rs, {"commit": sha, "repo": lock["repo"]}, eng_audit, eng_art, overrides)
    errors = [i for i in issues if i["severity"] == "error"]
    write_json(out / "validation_report.json", {"errors": len(errors), "warnings": len(issues) - len(errors),
                                                "issues": sorted(issues, key=lambda i: (i["severity"], i["code"], i["subject"], i["detail"]))})
    write_json(out / "coverage.json", cov)
    (out / "COVERAGE.md").write_text(coverage.to_markdown(cov), encoding="utf-8")

    files = [f for g in ARTIFACT_GROUPS.values() for f in g if (out / f).exists()]
    manifest = {"schema_version": 1, "pipeline_version": __version__, "source": source,
                "ruleset": {"id": rs.id, "digest": rs.digest()},
                "engine_registry_moves": eng_audit["engine_moves"] if eng_audit else None,
                "counts": {"species": len(species), "moves": len(moves), "abilities": len(abilities), "items": len(items),
                           "eligible_species": cov["ruleset_eligible"]["species_entries"],
                           "engine_roster_rows": len(eng_art["roster"]) if eng_art else None},
                "artifact_groups": {g: [f for f in fs if f in files] for g, fs in ARTIFACT_GROUPS.items()},
                "artifact_sha256": {f: sha256_file(out / f) for f in sorted(files)},
                "raw_snapshot": f"data/cache/showdown/{sha}/export (not copied; hashes above are in source.export.files)",
                "notes": ["Showdown is a community-maintained simulator; see README for licensing and rights notes."]}
    write_json(out / "manifest.json", manifest)
    if errors and not allow_errors:
        raise BuildError(f"{len(errors)} validation error(s); see {out / 'validation_report.json'} (artifacts were written)")
    return manifest
