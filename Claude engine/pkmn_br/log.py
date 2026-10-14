"""Event log, replay frames, stats, rankings. Everything is plain JSON-serialisable."""
import hashlib
import json


LEGACY_KINDS = frozenset({"match_start", "weather", "hazard_warning", "hazard_impact", "ai_mode", "takeoff", "land",
                          "hit", "status", "berry", "elim", "placement", "match_end", "action_lost"})


def legacy_digest(events) -> str:
    """Digest over only the pre-Combat-v1 event kinds (and without match_start.config, which hashes the
    now-larger Config). Used to prove existing mechanics are unchanged."""
    h = hashlib.sha256()
    for e in events:
        if e["e"] not in LEGACY_KINDS:
            continue
        if e["e"] == "match_start":
            e = {k: v for k, v in e.items() if k != "config"}
        h.update(json.dumps(e, sort_keys=True, separators=(",", ":")).encode())
    return h.hexdigest()


class EventLog:
    def __init__(self, cfg):
        self.cfg = cfg
        self.events = []
        self.frames = []

    def emit(self, t, ev, **kw):
        e = {"t": t, "e": ev}
        e.update(kw)
        self.events.append(e)

    def frame(self, t, zone, weather, combatants):
        self.frames.append({
            "t": t, "zone": [round(zone.cx, 2), round(zone.cy, 2), round(zone.r, 2)], "weather": weather,
            "c": [[c.id, round(c.x, 2), round(c.y, 2), c.alt, c.hp] for c in combatants if c.alive]})

    def digest(self) -> str:
        h = hashlib.sha256()
        for e in self.events:
            h.update(json.dumps(e, sort_keys=True, separators=(",", ":")).encode())
        for f in self.frames:
            h.update(json.dumps(f, sort_keys=True, separators=(",", ":")).encode())
        return h.hexdigest()

    def write_jsonl(self, path):
        with open(path, "w") as f:
            for e in self.events:
                f.write(json.dumps(e, separators=(",", ":")) + "\n")
