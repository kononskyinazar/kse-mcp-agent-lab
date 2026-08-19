"""Rule plumbing: context, findings, applicability and scoring.

Two ideas carry the design:

* a rule that does not apply is *reported* as not applicable, with the reason.
  A `reporting` tender has no bids by construction, so silently passing every
  competition rule would read as a clean record and firing them would be a
  libel;
* every firing rule contributes a named weight to the score and says so, so the
  number can be recomputed by hand from the response.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ..models import Tender
from ..thresholds import StatutoryBook

BLOCKING = "blocking"
ADVISORY = "advisory"


@dataclass(frozen=True)
class Finding:
    rule_id: str
    title: str
    rule_class: str
    weight: float
    observed: Any = None
    threshold: Any = None
    evidence: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "rule_id": self.rule_id,
            "title": self.title,
            "class": self.rule_class,
            "weight": self.weight,
            "observed_value": self.observed,
            "threshold_value": self.threshold,
            "evidence": self.evidence,
        }
        if self.source:
            payload["statute_reference"] = self.source
        return payload


@dataclass(frozen=True)
class Skip:
    rule_id: str
    reason: str

    def to_payload(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "reason": self.reason}


@dataclass
class RuleContext:
    tender: Tender
    rules: dict[str, Any]
    book: StatutoryBook
    store: Any = None
    now: datetime | None = None

    def config(self, rule_id: str) -> dict[str, Any]:
        return dict(self.rules.get(rule_id) or {})

    def weight(self, rule_id: str) -> float:
        return float(self.config(rule_id).get("weight", 0))

    def rule_class(self, rule_id: str) -> str:
        return str(self.config(rule_id).get("class", ADVISORY))

    def title(self, rule_id: str) -> str:
        return str(self.config(rule_id).get("title", rule_id))

    def finding(self, rule_id: str, **kwargs: Any) -> Finding:
        return Finding(
            rule_id=rule_id,
            title=self.title(rule_id),
            rule_class=self.rule_class(rule_id),
            weight=self.weight(rule_id),
            **kwargs,
        )


class Rule(Protocol):
    id: str

    def applies(self, ctx: RuleContext) -> str | None:
        """Return None when the rule applies, or the reason it does not."""

    def check(self, ctx: RuleContext) -> Finding | None: ...


@dataclass
class ScreenResult:
    blocking_violations: list[Finding] = field(default_factory=list)
    advisories: list[Finding] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)
    errored: list[dict[str, str]] = field(default_factory=list)
    risk_score: float = 0.0
    raw_score: float = 0.0
    floor_applied: bool = False

    @property
    def has_blocking(self) -> bool:
        return bool(self.blocking_violations)

    def evidence_chain(self) -> list[dict[str, Any]]:
        chain = []
        for finding in [*self.blocking_violations, *self.advisories]:
            chain.append(
                {
                    "signal": finding.rule_id,
                    "class": finding.rule_class,
                    "weight": finding.weight,
                    "contribution": finding.weight,
                    "observed_value": finding.observed,
                    "threshold_value": finding.threshold,
                    "evidence": finding.evidence,
                    "statute_reference": finding.source,
                }
            )
        return chain


def screen(ctx: RuleContext, rules: list[Rule], scoring: dict[str, Any]) -> ScreenResult:
    result = ScreenResult()

    for rule in rules:
        try:
            reason = rule.applies(ctx)
            if reason is not None:
                result.skipped.append(Skip(rule.id, reason))
                continue
            finding = rule.check(ctx)
        except Exception as exc:  # one broken rule must not lose the screen
            result.errored.append(
                {"rule_id": rule.id, "error": exc.__class__.__name__, "message": str(exc)}
            )
            continue
        if finding is None:
            continue
        if finding.rule_class == BLOCKING:
            result.blocking_violations.append(finding)
        else:
            result.advisories.append(finding)

    max_score = float(scoring.get("max_score", 100))
    raw = sum(f.weight for f in [*result.blocking_violations, *result.advisories])
    score = min(raw, max_score)

    if result.has_blocking:
        floor = float(scoring.get("blocking_floor", 0))
        if score < floor:
            score = min(floor, max_score)
            result.floor_applied = True

    result.raw_score = round(raw, 2)
    result.risk_score = round(score, 2)
    return result
