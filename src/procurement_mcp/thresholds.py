"""Statutory thresholds, looked up by the date a tender was published.

Applying today's rule to a tender published under an earlier regime would
manufacture violations that never happened, so every lookup takes a date and
returns the source it used along with the number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from .errors import data_integrity, invalid_input

# Repairs sit under CPV division 45 (construction works and related services).
CURRENT_REPAIR_CPV_PREFIX = "45"


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise data_integrity(f"unparseable date in threshold config: {value!r}")


@dataclass(frozen=True)
class ThresholdHit:
    """A number plus where it came from. Both travel together, always."""

    value: float | int
    subject: str
    regime: str
    source: str
    source_point: str | None
    verification: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "subject": self.subject,
            "regime": self.regime,
            "source": self.source,
            "source_point": self.source_point,
            "verification": self.verification,
        }


@dataclass
class Regime:
    identifier: str
    name: str
    effective_from: date
    effective_to: date | None
    source: str
    payload: dict[str, Any]

    def covers(self, moment: date) -> bool:
        if moment < self.effective_from:
            return False
        return self.effective_to is None or moment <= self.effective_to


class StatutoryBook:
    def __init__(self, config: dict[str, Any]) -> None:
        self.version = str(config.get("version", "unversioned"))
        self.classifier = config.get("classifier")
        self.category_map: dict[str, str] = config.get("category_map") or {}
        self.regimes = [
            Regime(
                identifier=raw["id"],
                name=raw.get("name", raw["id"]),
                effective_from=_as_date(raw["effective_from"]),
                effective_to=_as_date(raw.get("effective_to")),
                source=raw.get("source", ""),
                payload=raw,
            )
            for raw in config.get("regimes") or []
        ]
        if not self.regimes:
            raise data_integrity("threshold configuration contains no regimes")

    @classmethod
    def from_file(cls, path: Path) -> "StatutoryBook":
        return cls(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})

    def regime_at(self, moment: datetime | date | None) -> Regime:
        if moment is None:
            raise invalid_input("a publication date is required to select the applicable rules")
        day = moment.date() if isinstance(moment, datetime) else moment
        for regime in self.regimes:
            if regime.covers(day):
                return regime
        raise invalid_input(
            "no statutory regime in the configuration covers this publication date",
            date=day.isoformat(),
            configured_regimes=[r.identifier for r in self.regimes],
        )

    def subject_for(self, main_category: str | None, cpvs: set[str] | None = None) -> str:
        """Map a tender to the subject the thresholds are expressed in.

        Current-repair services carry their own, higher threshold. They are
        identified by CPV division 45 on a services tender, which is a
        approximation of the legal definition and is documented as such.
        """
        mapped = self.category_map.get((main_category or "").lower(), "goods_and_services")
        if mapped == "goods_and_services" and (main_category or "").lower() == "services":
            if any(cpv.startswith(CURRENT_REPAIR_CPV_PREFIX) for cpv in cpvs or set()):
                return "current_repair_services"
        return mapped

    def _entries(self, regime: Regime, key: str) -> list[dict[str, Any]]:
        raw = regime.payload.get(key)
        if isinstance(raw, dict):
            return [raw]
        return list(raw or [])

    def _pick(self, entries: list[dict[str, Any]], subject: str, day: date) -> dict[str, Any] | None:
        candidates = [e for e in entries if e.get("subject", subject) == subject]
        for entry in candidates:
            starts = _as_date(entry.get("effective_from"))
            ends = _as_date(entry.get("effective_to"))
            if starts and day < starts:
                continue
            if ends and day > ends:
                continue
            return entry
        return None

    def mandatory_open_tender_from(self, subject: str, moment: datetime | date) -> ThresholdHit:
        regime = self.regime_at(moment)
        day = moment.date() if isinstance(moment, datetime) else moment
        entry = self._pick(self._entries(regime, "mandatory_open_tender_from"), subject, day)
        if entry is None:
            raise data_integrity(
                f"no open-tender threshold configured for subject {subject!r}",
                regime=regime.identifier,
            )
        return ThresholdHit(
            value=entry["amount_uah"],
            subject=subject,
            regime=regime.identifier,
            source=entry.get("source", regime.source),
            source_point=entry.get("source_point"),
            verification=entry.get("verification", "unstated"),
        )

    def minimum_tender_period_days(self, subject: str, moment: datetime | date) -> ThresholdHit:
        regime = self.regime_at(moment)
        day = moment.date() if isinstance(moment, datetime) else moment
        # Current repair is a services subject for period purposes.
        lookup = "goods_and_services" if subject == "current_repair_services" else subject
        entry = self._pick(self._entries(regime, "minimum_tender_period_days"), lookup, day)
        if entry is None:
            raise data_integrity(
                f"no minimum tender period configured for subject {lookup!r}",
                regime=regime.identifier,
            )
        return ThresholdHit(
            value=entry["days"],
            subject=lookup,
            regime=regime.identifier,
            source=entry.get("source", regime.source),
            source_point=entry.get("source_point"),
            verification=entry.get("verification", "unstated"),
        )

    def tolerance_days(self, moment: datetime | date) -> float:
        return float(self.regime_at(moment).payload.get("tender_period_tolerance_days", 0.0))
