"""Numeric constraints. Base stats, IVs and EVs are DIFFERENT tables with different ranges.

base stat : 1..255   (1 is legal: Shedinja HP; 255 is legal: Blissey HP)
IV        : 0..31    (0 is legal)
EV        : 0..252 each, total <= 510   (0 is legal; default is 0)
"""
STATS = ("hp", "atk", "def", "spa", "spd", "spe")
BASE_STAT_MIN, BASE_STAT_MAX = 1, 255
IV_MIN, IV_MAX = 0, 31
EV_MIN, EV_MAX_EACH, EV_MAX_TOTAL = 0, 252, 510


def norm_table(table, default=None) -> dict:
    """Accept 'def' or 'def_' keys; missing keys take `default` (None = missing)."""
    out = {}
    for k in STATS:
        key = "def_" if (k == "def" and "def" not in table and "def_" in table) else k
        out[k] = table.get(key, default)
    return out


def table_errors(table: dict, lo: int, hi: int, label: str) -> list:
    errs = []
    for k in STATS:
        v = table.get(k)
        if isinstance(v, bool) or not isinstance(v, int):
            errs.append(f"{label}.{k}={v!r} is not an integer")
        elif not lo <= v <= hi:
            errs.append(f"{label}.{k}={v} outside [{lo}, {hi}]")
    return errs
