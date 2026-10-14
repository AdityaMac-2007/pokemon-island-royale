"""Identifier helpers.

* to_id    : Showdown-style id (lowercase a-z0-9 only). Used for cross references.
* fold_id  : same, after stripping accents (so 'Flabebe' style names still resolve).
* snake_id : the engine's snake_case convention ('Close Combat' -> 'close_combat').
"""
import re
import unicodedata


def to_id(text) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def _ascii(text) -> str:
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")


def fold_id(text) -> str:
    return to_id(_ascii(text))


def snake_id(name) -> str:
    s = _ascii(name).lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")
