# Required engine integration change

Do not replace your entire local `engine.py` with a copy from an older archive. In `Claude engine/pkmn_br/engine.py`, inside `Engine._phase_environment`, replace the inline flight-stamina drain calculation with the shared helper:

```python
if c.alt == 1:
    c.flight_ticks += 1
    drain = ai.stamina_drain_rate(c)
    c.stamina -= drain
    if c.stamina <= 0:
        c.alt = 0
        c.stamina = 0.0
        self.log.emit(t, "land", id=c.id, why="exhausted")
```

The engine already imports `from . import ai` in the supplied source revision. If the local file no longer has that import, add it at module scope without creating a second import cycle.

The new helper uses the explicit `updraft` field when it exists and falls back to the legacy `note.startswith("updrafts")` schema when it does not. The parity test checks that entries containing both fields agree. The historical golden digest test remains the final zero-diff check.
