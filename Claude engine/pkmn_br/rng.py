"""Seeded RNG utilities.

Design: there is NO single shared RNG whose consumption order can drift.
Every subsystem derives an independent stream from (master_seed, labels...).
Combat uses one stream per tick, with a fixed number of draws per attacker,
so adding logging or changing AI cadence cannot change combat randomness.
"""
import hashlib
import random

MASK = (1 << 64) - 1


def derive_seed(master: int, *labels) -> int:
    s = "|".join([str(master)] + [str(x) for x in labels])
    return int.from_bytes(hashlib.sha256(s.encode()).digest()[:8], "big")


def stream(master: int, *labels) -> random.Random:
    return random.Random(derive_seed(master, *labels))


def hash01(*ints) -> float:
    """Cheap stateless hash -> [0,1). Used by the AI so decisions need no RNG object."""
    x = 0x9E3779B97F4A7C15
    for v in ints:
        x = ((x ^ (int(v) & MASK)) * 0xBF58476D1CE4E5B9) & MASK
        x ^= x >> 31
    return (x >> 11) / float(1 << 53)
