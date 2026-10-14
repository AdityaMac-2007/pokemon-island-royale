"""Viewer-facing combat feed: a pure projection of engine events. The viewer must render these
numbers as given; it must never recompute hit/miss or damage."""
FEED_KINDS = frozenset({"move_use", "move_miss", "move_blocked", "move_failed", "damage", "heal", "recoil",
                        "env_damage", "stat_change", "protect", "status", "elim"})


def combat_feed(events, replay_every: int) -> list:
    out = []
    for e in events:
        if e["e"] in FEED_KINDS:
            rec = dict(e)
            rec["frame_t"] = e["t"] - e["t"] % max(1, replay_every)   # replay frame to anchor the overlay to
            out.append(rec)
    return out
