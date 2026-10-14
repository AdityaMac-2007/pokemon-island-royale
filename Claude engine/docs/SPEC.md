# Pokémon Island Battle Royale — Rules Spec & Architecture (v0.1, Milestone 1)

## 1. Layers (kept separate on purpose)
| Layer | Module | Contents |
|---|---|---|
| Generation mechanics | `rules.py` | Type chart (Gen 6+), L100 stat & damage formula (Gen 5+), STAB, crit, roll, abilities/items hook tables |
| Battle-royale mechanics | `island.py`, `ai.py`, `engine.py` | Real-time ticks, positions, altitude, biomes, zone, hazards, pickups |
| Chaos mode | `ChaosConfig` → `BattleRules.from_config` | Independent switch: higher crit, wider roll, STAB 2.0, 3x hazard rate. Canonical rules are untouched when off |
| Data | `data.py` | `Build`, `Move`, `DataProvider` (Stub / Synthetic / JSON from the external pipeline) |

## 2. Time, actions, energy
* 1 tick = 0.25 s of notional time. Every Pokémon accrues `energy += speed * 0.1` per tick (halved by paralysis, cap 130).
* An attack costs 100 energy. Speed therefore sets action rate (Spe 300 acts about every 3.3 ticks, Spe 100 about every 10).
* Movement is continuous in 2D. Altitude is a discrete layer: `0` ground, `1` air.

## 3. Tick resolution order (normative)
1. **World**: zone state, weather, telegraph new hazards, refresh cached biome.
2. **Index**: rebuild uniform-grid spatial hash of living combatants.
3. **Decide**: read-only against the frozen world. Results are committed only after all decisions are made, so evaluation order cannot matter. Re-decided every `ai_interval` ticks (staggered by id) or when the target dies.
4. **Move**: takeoff/landing, movement toward goal (biome/altitude speed), island clamp, one deterministic separation pass per layer.
5. **Attacks**: all combatants with energy ≥ 100 and a planned attack, ordered by `(−priority, −effective speed, seeded tiebreak, id)`. Range and altitude legality are re-validated at resolution using post-move positions. If invalid, the action is not spent.
6. **Environment**: (a) zone damage, (b) hazard impacts, (c) residual step every 8 ticks (burn/poison, weather chip, biome chip, Leftovers), (d) berry pickup, (e) energy and stamina update.
7. **Finalize**: assign placements, check end, write replay frame.

### Simultaneity and knockouts
* **Default (`speed_tie_mode=random`)**: sequential resolution in the order above. A combatant KO'd earlier in the tick loses its queued action (`action_lost` is logged only if the target died first). Exact ties in priority and speed are broken by a seeded roll.
* **`simultaneous`**: attackers with identical `(priority, speed)` form a bracket. All of them act against pre-bracket HP, damage is applied afterwards in resolution order, so mutual KOs are possible.
* **Multiple attackers on one target**: processed in order; HP is updated after each hit, and the killer is whoever lands the final blow (kill credit). Assists are recoverable from `last_hits` on the elimination event.
* **Same-tick eliminations** (any source): placement slots are shared out by `(damage dealt desc, kills desc, id)`. This is logged as `placement.tiebreak=true`.
* **RNG**: one stream per tick, with exactly 4 attack draws plus 1 tiebreak draw per ready attacker, drawn in id order *before* ordering. Resolution order therefore never shifts the random stream.

## 4. Flight
* Takeoff needs stamina ≥ 40 (cost 8). Flight drains 1 stamina/tick (0.5 over `storm_peak` updrafts). At 0 the Pokémon is forced down. Ground regen is +2/tick.
* Air speed ×1.4 and +25% vision, but no biome buffs, no cover, and no berry pickup.
* **Ground → air**: a grounded attacker may target an airborne foe only with moves flagged `hits_air` (ranged special/projectile style moves; ground-only and melee moves cannot).
* **Air → ground**: always allowed. A *melee* strike from the air forces the attacker to land (dive trade-off).
* Airborne units are immune to eruptions but not to lightning, zone, or weather chip.
* Counterplay: ranged anti-air moves, Electric/Ice/Rock type effectiveness, the stamina clock, the shrinking zone.

## 5. Biomes (benefit / trade-off)
| Biome | Favored types (+boost on those moves) | Trade-off or alternative benefit |
|---|---|---|
| plains | normal, fighting (+10%) | +15% move speed but no cover |
| forest | grass, bug (+20%) | cover 35% and rich food, −10% speed |
| volcanic | fire, ground, rock (+20%) | ember chip on grounded non-resistants, eruptions, scarce food |
| wetland | water, poison (+20%) | −20% speed for non-favored types |
| tundra | ice, dragon (+20%) | frostbite chip for non-ice/steel, −15% speed |
| storm_peak | electric, flying, steel (+20%) | lightning strikes, −15% speed, halved flight drain |
| ruins | ghost, psychic, dark, fairy (+15%) | 40% cover, mid food |

Cover reduces ranged accuracy against occupants (up to −50% × cover) and shrinks how far they can be seen from. All 18 types have at least one home biome.

## 6. Zone
Six nested circles chosen from the seed; each holds 250 ticks and then shrinks over 150. Damage is a percentage of max HP per tick outside the circle (0.2% → 6%). After the last circle the radius collapses to a point over 300 ticks with damage rising to 8%, which guarantees resolution. Hard cap `max_ticks`: survivors are ranked by HP fraction.

## 7. AI (no LLM)
Utility scoring per decision. Inputs: build-aware expected damage (type, STAB, ability, item, weather, biome boost, cover, legality), HP, altitude, vision/cover, zone, hazards, bravery in [−0.10, +0.15].
* `p_win = td² / (ta² + td²)`, where `ta` and `td` are the times-to-kill for attacker and defender. This is a closed-form logistic with no exp/log, which keeps it platform-stable.
* Modes: `evade` (telegraphed hazard) > `hunt` (p ≥ flee threshold) / `flee` > `zone` > `forage` > `wander`.
* Opportunistic attacks: any legal in-range target may be attacked, with the primary target preferred.

## 8. Logging / replay
`events.jsonl` (match_start, weather, hazard_*, ai_mode, takeoff/land, hit, status, berry, elim, placement, match_end), `replay.json` (biome grid, zone phases, frames every N ticks), `summary.json`, `rankings.csv`. An `elim` event carries cause, killer, move, the last 5 hits, the AI mode and the last estimated p_win. `match_end` carries the winner explanation.

## 9. Assumptions and documented defaults (unresolved decisions)
1. Real-time energy model instead of strict turns, because 1000+ simultaneous combatants need a continuous clock.
2. Move set, species, abilities and items in M1 are a *stub subset*. The real roster arrives via `builds.json` (schema in `data.py`). Unknown moves fail loudly.
3. Forms: `form_policy` is passed to the pipeline (`base_only | battle_forms | all`); the engine treats each Build as one combatant. In-battle transformations (Mega, Gigantamax and so on) are out of M1.
4. Status coverage: burn, poison, paralysis. Sleep, freeze, confusion and stat stages are M2.
5. Determinism guarantee: same config + seed + Python version + platform. Cross-platform float differences in `sqrt` or `hypot` are avoided where decisions depend on them, but are not yet CI-verified across OSes.
6. No teams or alliances. Dead combatants never act.
7. Duplicates allowed when N > roster size (`allow_duplicates`).

## 10. Roadmap
M2 full-roster adapter, ability/item registry expansion, status and stat stages, ridge line-of-sight, kiting AI. M3 balance harness (thousands of seeded matches, win-rate by type/biome/altitude). M4 viewer for `replay.json`.
