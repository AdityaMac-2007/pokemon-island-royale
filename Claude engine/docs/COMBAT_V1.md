# Combat Systems v1 (stat stages + move-effect framework)

Scope: the smallest complete vertical slice. The Python engine remains the only authority on combat.

## Implemented
| Mechanic | Rule as implemented |
|---|---|
| Stat stages | `atk def spa spd spe`, clamped to -6..+6. Positive: `floor(stat*(2+n)/2)`. Negative: `floor(stat*2/(2-n))`. Applied before burn/item/ability modifiers. Speed: stage first, then paralysis halving. HP has no stages. |
| Crits | Ignore the attacker's negative and the defender's positive stages (Gen 3+; confirmed on Bulbapedia's Stat modifier page). The AI's expected-damage estimate does not model this. |
| Stat-change moves | `swords_dance` (+2 atk), `calm_mind` (+1 spa/spd), `dragon_dance` (+1 atk/spe), `screech` (-2 def, 85%), plus secondary drops on `icy_wind` (-1 spe), `mystical_fire` (-1 spa) and self-drops on `superpower` (-1 atk/def). Drops are not applied to fainted targets. |
| Paralysis | `thunder_wave` (90%). Fails on Ground (type immunity), Electric (status immunity) and already-statused targets. Speed x0.5 (existing). Full-paralysis skip is implemented but **off by default** (`para_skip_chance=0.0`; Gen 9 value is 0.25). |
| Protect | Priority +4. Success chance is 1/3 per consecutive success (floor 1/729); a failure or any other move resets the streak. In this real-time engine a success blocks moves aimed at the user for `protect_duration_ticks` (default 3). Blocks damaging and targeted status moves. Does not stop zone/hazard/weather/status damage. |
| Healing | `recover`: floor(max HP / 2), bounded by max HP; fails at full HP. Existing Leftovers/berry healing now also emits events. |
| Recoil | `double_edge` (1/3), `head_smash` (1/2) of HP actually removed from the target, minimum 1, floor. None on miss, immunity, block or 0 damage. Can faint the user (cause `recoil`). Existing Life Orb recoil now emits a `recoil` event. |

New moves added to the registry: protect, swords_dance, calm_mind, dragon_dance, recover, screech, thunder_wave, double_edge, head_smash, superpower, icy_wind, mystical_fire.

## Capability gating (imported data vs engine capability)
`effects.py` holds `HANDLERS`. `data.register_move()` validates every move, and a move declaring an effect kind with no handler raises `EffectError` and never enters `MOVES`. There is no per-move "implemented" flag. `capabilities.engine_capabilities()` (also written to `capabilities.json` per match) lists what has real engine effect; anything absent has none, whatever the imported data says. Existing moves (e.g. `brave_bird`, `flare_blitz`, `close_combat`) were deliberately NOT given their real recoil/self-drop effects, to keep baseline behaviour unchanged. Opting in later is a data change plus a rebaseline.

## Randomness
New randomness comes from `stream(seed, "fx", tick)`: 6 draws per ready attacker, drawn in id order (indexes 0-3 per-effect chance, 4 paralysis skip, 5 Protect). Legacy streams are untouched, so existing outcomes did not change.

## Events (all deterministic; combat-detail events are gated by `log_hits`, `status`/`elim` always logged)
| Event | Fields |
|---|---|
| `move_use` | a, d, mv, kind (`damage`/`status`/`self`) |
| `move_miss` | a, d, mv, reason (`accuracy`/`immune`) |
| `move_blocked` | a, d, mv, by (`protect`) |
| `move_failed` | a, mv, reason (`full_hp`, `immune`, `immune_type`, `already_statused`, `protect_failed`, `fully_paralyzed`) |
| `damage` | src, d, mv, amount (HP removed), raw, hp_after, crit, eff, [sturdy] |
| `heal` | id, amount, hp_after, src (`move`/`item`/`berry`), mv |
| `recoil` | id, amount, hp_after, src (`move`/`item`), mv |
| `env_damage` | id, src (zone, hazard kind, status/weather/biome cause), amount, hp_after |
| `stat_change` | id, stat, requested, applied, stage, src, mv |
| `protect` | id, ok, streak, until |

Every HP-changing event carries `hp_after`; replaying them reproduces each combatant's final HP exactly (tested). Legacy events (`hit`, `status`, `elim`, ...) are byte-identical to before; the `legacy_digest()` in `log.py` hashes only those and matches the pre-change baselines.

## Viewer
`combat_feed.json` is written per match: a pure projection of the events above plus `frame_t` (nearest earlier replay frame). The viewer should render these values as given and never recompute hit/miss or damage. **The existing viewer's source was not available here, so it has not been modified.**

## Known gaps
- Accuracy/evasion stages, Simple, Contrary, Mist, Clear Body and similar abilities.
- Burn/poison as move effects (existing secondary burn/poison on legacy moves is unchanged); sleep, freeze, confusion.
- Protect variants, bypass moves, Endure; Protect's "fails if user moves last" rule does not map to this engine.
- Stages never reset (no switching); there is no Haze-style reset.
- In `speed_tie_mode=simultaneous`, status/self moves resolve immediately while damage in the same bracket is deferred; same-bracket boosts are therefore id-order dependent.
- The AI's status-move policy is a small heuristic, not tuned.
- Recoil rounding (floor, min 1) and the multiplier table are not cartridge-verified here; the multiplier table did not render in my source lookup, so the stage formula is the standard one, cross-checked against an exact-fraction oracle.

## Reproduce
```
cd "Claude engine"
python -W error -m unittest discover -s tests -v          # full suite
python -m unittest tests.test_combat_v1 -v                # Combat v1 only
python bench_combat.py --n 24 240 840 --repeats 7 --baseline <path to pre-change engine>
```
