"""Loader/lookup over the generated data/gen9 artifacts."""
from __future__ import annotations
import json
from pathlib import Path

from .normalize import Resolver


def _load(d: Path, name: str, key: str | None = None):
    obj = json.loads((d / name).read_text())
    return obj[key] if key else obj


class Dataset:
    def __init__(self, species, moves, abilities, items, natures, learnsets, typechart=None):
        self.species, self.moves, self.abilities = species, moves, abilities
        self.items, self.natures, self.learnsets, self.typechart = items, natures, learnsets, typechart
        self._res = {"species": Resolver(species), "moves": Resolver(moves), "abilities": Resolver(abilities),
                     "items": Resolver(items), "natures": Resolver(natures)}

    @classmethod
    def load(cls, gen9_dir) -> "Dataset":
        d = Path(gen9_dir)
        return cls(_load(d, "species.json", "species"), _load(d, "moves.json", "moves"),
                   _load(d, "abilities.json", "abilities"), _load(d, "items.json", "items"),
                   _load(d, "natures.json", "natures"), _load(d, "learnsets.json", "learnsets"),
                   _load(d, "typechart.json"))

    def species_id(self, ref): return self._res["species"](ref)
    def move_id(self, ref): return self._res["moves"](ref)
    def ability_id(self, ref): return self._res["abilities"](ref)
    def item_id(self, ref): return self._res["items"](ref)
    def nature_id(self, ref): return self._res["natures"](ref)
