# Gen 9 data pipeline

Stdlib-only Python plus one small Node step. Three stages, only the first two touch the network.

```
python -m data_pipeline pin      # [network] resolve a ref -> exact commit SHA -> data_pipeline/source.lock.json
python -m data_pipeline fetch    # [network] fetch that SHA, export data/*.ts -> JSON  (data/cache/showdown/<sha>/)
python -m data_pipeline build    # [offline] normalize -> data/gen9/*  (never uses the network; tested)
python -m data_pipeline check builds.json   # legality-check builds against the ruleset
```

## Source
Pokemon Showdown is a community-maintained battle simulator whose repository (`smogon/pokemon-showdown`) carries
species, move, ability, item, learnset, nature and type-chart data. It is used here as the primary data source.
PokeAPI is not used by this pipeline.

**Licensing and rights (no legal conclusion is drawn here).** The simulator's software license is whatever the
`LICENSE*` files in the pinned revision say; `fetch` records their SHA-256 in `fetch_record.json` and the manifest so
you can read exactly the files you built from. That software license is a separate matter from rights in Pokemon
names, designs and game data, which belong to their respective rights holders. Whether and how generated data may be
committed, published or redistributed has not been assessed; check before publishing `data/cache/` or `data/gen9/`.

## Extraction (TypeScript -> JSON)
`node/export_showdown.cjs` transpiles each `data/<file>.ts` with `sucrase` (pinned in `node/package.json`; type
syntax is stripped, nothing else is changed), evaluates it in an isolated `vm` whose `require` throws, and writes
key-sorted JSON. Function values are dropped but listed per entry under `_functions`. It fails loudly if a data file
imports anything or lacks its expected export. Recorded per file in `export_report.json` and the manifest: source
SHA-256, output SHA-256, entry count, functions dropped; plus Node and sucrase versions.

The mechanism is exercised end to end by `TestPinFetchExportEndToEnd` against a local git repo built from synthetic
fixtures. It has NOT been run against the real upstream repository by the author of this code; your first
`pin` + `fetch` is that verification. If a real data file imports something or exports a different name, the
exporter stops with a message naming the file.

## Target ruleset: `gen9-l100-ffa-v1` (`rulesets/gen9_l100_ffa_v1.json`)
* Level 100, free-for-all, each combatant validated alone.
* Species/form eligible iff: dex number > 0, **no** `isNonstandard` marker (from `pokedex` or `formats-data`), and the
  form policy admits its class. Classes: `base`, `regional` (Alola/Galar/Hisui/Paldea), `permanent_form`,
  `battle_only` (battleOnly/Mega/Primal/Gmax), `cosmetic` (listed in a base's `cosmeticFormes`).
  Policies: `base_only`; `battle_forms` (default: base+regional+permanent_form); `all`.
* Moves: must have a Gen 9 source (`9M`, `9T`, `9L*`, `9E`) in the species' own learnset, else its `changesFrom`/base
  species' (only when it has no own entry), plus every pre-evolution's. `9S` (event) moves are rejected unless
  `allow_event_only_moves` is true. Move must not be nonstandard / Z / Max. 1-4 distinct moves.
* Ability must be one of the species' slots (0/1/H/S) and match `requiredAbility`. Item must exist, not be
  nonstandard/mega stone/Z crystal, and satisfy `requiredItem(s)`, `itemUser`, `forcedForme`. Required move enforced.
* Nature must exist in the natures data. EV 0-252 each, total <= 510. IV 0-31 each (non-uniform IVs are legal here;
  the engine adapter separately requires uniform IVs).
* Not enforced (listed in the ruleset file): event combinations, egg-move combination limits, HOME/transfer rules,
  Terastallization (warned, ignored), team-level clauses.

## Artifacts (all distinct)
| Group | Files | Notes |
|---|---|---|
| raw snapshot | `data/cache/showdown/<sha>/repo`, `.../export/*.json`, `export_report.json`, `fetch_record.json` | not rewritten by `build` |
| normalized DB | `data/gen9/species.json moves.json abilities.json items.json natures.json typechart.json` | ruleset-independent |
| learnsets | `data/gen9/learnsets.json` | Gen 9 sources only, with source codes and `via` |
| ruleset verdicts | `data/gen9/ruleset_eligibility.json` | per species/form, with reasons |
| engine-compatible | `data/gen9/engine/engine_roster.json` (JsonProvider schema), `engine_roster_audit.json`, `not_engine_compatible.json` | default builds, not optimized |
| reports | `validation_report.json`, `coverage.json`, `COVERAGE.md`, `manifest.json` | manifest hashes every artifact |

`engine_compatible` = eligible, playable form class, >= 1 legal move already in the engine's hard-coded registry, and a
default build that passes the legality checker. It is not "engine verified" and says nothing about balance. Flight is
never inferred: only `data/overrides/flight.json` (seeded from the adapter's registry) sets `can_fly`.

## Review of docs/GEMINI_DATA_SOURCE_REVIEW.md (what changed and why)
* Learnset legality is derived from each species/form's data; no move blacklist. The "Scald" example is dropped
  (unverified); `TestLearnsets` shows availability varying per species.
* "Exactly 1025 species" is not a valid target under this ruleset: eligibility is whatever the data marks as
  standard; the report gives counts and exclusion reasons instead.
* Licence statements in that document were unverified and are replaced by the section above.
* `StatsTable` (ge=1) was reused for IVs and EVs, rejecting legal zeros; split into BaseStatsTable/IVTable/EVTable.
* "State 4 validated against the island engine" cannot be asserted by an importer; not claimed.
* `accuracy: true` is stored as `always_hits` (not coerced); the registry audit compares it as 100.
* Unhandled abilities/items stay as real, legal labels and are flagged in the audit (`ability_functional`,
  `item_functional`) rather than blanked.
