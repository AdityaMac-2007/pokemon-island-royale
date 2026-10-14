"""Target ruleset: a pure-data definition + species verdicts. See rulesets/*.json."""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, asdict, replace
from pathlib import Path

POLICY_CLASSES = {
    "base_only": {"base"},
    "battle_forms": {"base", "regional", "permanent_form"},
    "all": {"base", "regional", "permanent_form", "battle_only", "cosmetic"},
}
DEFAULT_PATH = Path(__file__).resolve().parent / "rulesets" / "gen9_l100_ffa_v1.json"


@dataclass(frozen=True)
class Ruleset:
    id: str
    description: str
    level: int
    form_policy: str
    learnset_generation: int
    regular_sources: tuple
    allow_event_only_moves: bool
    ev_max_each: int
    ev_max_total: int
    iv_min: int
    iv_max: int
    not_enforced: tuple

    @classmethod
    def load(cls, path=DEFAULT_PATH) -> "Ruleset":
        d = json.loads(Path(path).read_text())
        d["regular_sources"] = tuple(d["regular_sources"])
        d["not_enforced"] = tuple(d["not_enforced"])
        rs = cls(**d)
        if rs.form_policy not in POLICY_CLASSES:
            raise ValueError(f"unknown form_policy {rs.form_policy!r}")
        return rs

    def with_form_policy(self, policy: str) -> "Ruleset":
        if policy not in POLICY_CLASSES:
            raise ValueError(f"unknown form_policy {policy!r}")
        if policy == self.form_policy:
            return self
        return replace(self, id=f"{self.id}+form_policy={policy}", form_policy=policy)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["regular_sources"] = list(self.regular_sources)
        d["not_enforced"] = list(self.not_enforced)
        return d

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()


def species_verdict(sp: dict, rs: Ruleset) -> dict:
    """Ruleset verdict for one normalized species record."""
    reasons = []
    if sp["num"] is None or sp["num"] <= 0:
        reasons.append("num<=0")
    if sp["is_nonstandard"]:
        reasons.append(f"nonstandard:{sp['is_nonstandard']}")
    legal = not reasons
    admitted = sp["form_class"] in POLICY_CLASSES[rs.form_policy]
    if not admitted:
        reasons.append(f"form_policy:{sp['form_class']}")
    return {"eligible": legal and admitted, "ruleset_legal": legal, "admitted_by_form_policy": admitted,
            "form_class": sp["form_class"], "reasons": reasons}
