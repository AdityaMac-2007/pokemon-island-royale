# Coverage report (generated)

Ruleset: `gen9-l100-ffa-v1`  
Source commit: `3a766f2cfb5e51c1e428cfe87d0dba91a8f01640`

All numbers are counts from the generated data. They do not claim full Pokedex coverage.

## Imported
- species/form entries: 1518 ({'base': 1121, 'battle_only': 163, 'cosmetic': 37, 'permanent_form': 137, 'regional': 60})
- distinct dex numbers: 1025 (max 1025)
- moves 954, abilities 321, items 583

## Ruleset-eligible
- entries: 860 ({'base': 733, 'permanent_form': 83, 'regional': 44})
- distinct dex numbers: 733
- exclusion reasons: {'form_policy:battle_only': 163, 'form_policy:cosmetic': 37, 'nonstandard:CAP': 83, 'nonstandard:Custom': 19, 'nonstandard:Future': 49, 'nonstandard:LGPE': 2, 'nonstandard:Past': 452, 'num<=0': 138}

## Engine-compatible (not engine-verified)
- 840/860 eligible entries
- supported-move histogram: {'1': 18, '2': 47, '3': 95, '4+': 680}
- incompatible reasons: {'default_build_illegal': 1, 'no_supported_moves': 19}
- with functional legal ability: 157; functional default item: 817

## Flight
- reviewed overrides: 27
- unreviewed aerial-looking candidates: 117

## Engine move registry vs Showdown
- engine moves 36, resolved 36, unresolved []
- type/category/power/accuracy/priority mismatches: 0

## Limitations
- A species being imported does not make it engine-playable: the engine registry is a small hard-coded subset.
- 'engine_compatible' means >=1 legal move that already exists in the engine's registry plus a legal default build; it says nothing about balance or about the engine modelling the move's full canonical effect (see move_notes).
- Flight capability is never derived from data. Only species listed in data/overrides/flight.json are 'reviewed'; all others default to can_fly=false and are listed as unreviewed candidates when they look aerial.
- Learnset legality follows the ruleset's not_enforced list; event-only moves are excluded unless the ruleset allows them.
- No engine run is performed by the importer; 'engine_compatible' is not 'engine_verified'.
