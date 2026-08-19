"""Concentration, streaks and trend over a buyer's award history.

The numbers are deliberately plural. HHI compresses tail structure, so it is
reported next to the top-1 and top-3 shares, and each is computed both by
awarded value and by award count - a buyer can look concentrated on one and
dispersed on the other, and which is which is the analyst's judgement, not the
tool's.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from .models import Tender


@dataclass(frozen=True)
class AwardEvent:
    published_at: datetime | None
    awarded_at: datetime | None
    tender_id: str | None
    uuid: str
    supplier_edrpou: str
    supplier_name: str | None
    amount: float
    currency: str | None
    cpv_groups: tuple[str, ...]

    @property
    def moment(self) -> datetime | None:
        return self.published_at or self.awarded_at


def award_sequence(tenders: Iterable[Tender]) -> list[AwardEvent]:
    """Flatten a buyer's tenders into one chronological list of active awards."""
    events: list[AwardEvent] = []
    for tender in tenders:
        award = tender.active_award
        if award is None or award.amount is None:
            continue
        for party in award.suppliers:
            if not party.edrpou:
                continue
            events.append(
                AwardEvent(
                    published_at=tender.published_at,
                    awarded_at=award.date,
                    tender_id=tender.tender_id,
                    uuid=tender.uuid,
                    supplier_edrpou=party.edrpou,
                    supplier_name=party.name,
                    # A multi-supplier award splits the value; otherwise one
                    # supplier's share would be counted several times over.
                    amount=award.amount / max(len(award.suppliers), 1),
                    currency=award.currency,
                    cpv_groups=tuple(sorted(tender.cpv_groups)),
                )
            )
    events.sort(key=lambda e: (e.moment or datetime.min.replace(tzinfo=None).astimezone(), e.uuid))
    return events


def herfindahl(shares: Iterable[float]) -> float:
    """HHI on fractional shares: 1.0 is one supplier, near 0 is fragmented."""
    return round(sum(s * s for s in shares), 4)


def _shares(totals: dict[str, float]) -> dict[str, float]:
    grand = sum(totals.values())
    if grand <= 0:
        return {}
    return {k: v / grand for k, v in totals.items()}


def concentration(events: list[AwardEvent]) -> dict[str, Any]:
    """Concentration by value and by count, with the tail made visible."""
    by_value: dict[str, float] = defaultdict(float)
    by_count: dict[str, float] = defaultdict(float)
    names: dict[str, str] = {}

    for event in events:
        by_value[event.supplier_edrpou] += event.amount
        by_count[event.supplier_edrpou] += 1.0
        if event.supplier_name:
            names.setdefault(event.supplier_edrpou, event.supplier_name)

    result: dict[str, Any] = {
        "awards_counted": len(events),
        "distinct_suppliers": len(by_value),
        "total_awarded_value": round(sum(by_value.values()), 2),
    }

    for label, totals in (("by_value", by_value), ("by_count", by_count)):
        shares = _shares(totals)
        ranked = sorted(shares.items(), key=lambda kv: kv[1], reverse=True)
        result[label] = {
            "hhi": herfindahl(shares.values()),
            "top_1_share": round(ranked[0][1], 4) if ranked else None,
            "top_3_share": round(sum(s for _, s in ranked[:3]), 4) if ranked else None,
            "top_suppliers": [
                {
                    "edrpou": edrpou,
                    "name": names.get(edrpou),
                    "share": round(share, 4),
                }
                for edrpou, share in ranked[:3]
            ],
        }
    return result


def month_key(moment: datetime) -> str:
    return f"{moment.year:04d}-{moment.month:02d}"


def monthly_trend(events: list[AwardEvent]) -> dict[str, Any]:
    """Concentration per month plus the direction of travel.

    A single short window cannot show a multi-year trajectory, so the number of
    buckets is reported alongside the direction: two buckets is a hint, not a
    trend, and the caller can see that.
    """
    buckets: dict[str, list[AwardEvent]] = defaultdict(list)
    for event in events:
        if event.moment is None:
            continue
        buckets[month_key(event.moment)].append(event)

    ordered = sorted(buckets.items())
    series = []
    for label, bucket in ordered:
        totals: dict[str, float] = defaultdict(float)
        for event in bucket:
            totals[event.supplier_edrpou] += event.amount
        series.append(
            {
                "month": label,
                "awards": len(bucket),
                "hhi_by_value": herfindahl(_shares(totals).values()),
            }
        )

    direction, magnitude = "insufficient_data", None
    if len(series) >= 2:
        magnitude = round(series[-1]["hhi_by_value"] - series[0]["hhi_by_value"], 4)
        if abs(magnitude) < 0.05:
            direction = "stable"
        else:
            direction = "increasing" if magnitude > 0 else "decreasing"

    return {
        "buckets": series,
        "periods_analyzed": len(series),
        "direction": direction,
        "magnitude": magnitude,
        "note": (
            "fewer than three buckets is a hint, not a trend"
            if len(series) < 3
            else "monthly buckets over the dataset window"
        ),
    }


def longest_streak(
    events: list[AwardEvent], edrpou: str, *, until: datetime | None = None
) -> dict[str, Any]:
    """The run of consecutive awards this supplier holds, ending at ``until``.

    Trailing rather than best-ever: what matters when screening one tender is
    whether the same supplier has been winning right up to it.
    """
    relevant = [e for e in events if until is None or (e.moment is not None and e.moment <= until)]
    tender_ids: list[str] = []
    cpv_groups: set[str] = set()

    for event in reversed(relevant):
        if event.supplier_edrpou != edrpou:
            break
        tender_ids.append(event.tender_id or event.uuid)
        cpv_groups.update(event.cpv_groups)

    return {
        "length": len(tender_ids),
        "tender_ids": list(reversed(tender_ids)),
        "cpv_groups": sorted(cpv_groups),
    }


def best_streak(events: list[AwardEvent]) -> dict[str, Any]:
    """The longest run held by any supplier anywhere in the sequence."""
    best = {"length": 0, "edrpou": None, "tender_ids": [], "cpv_groups": []}
    run: list[AwardEvent] = []

    for event in events:
        if run and run[-1].supplier_edrpou == event.supplier_edrpou:
            run.append(event)
        else:
            run = [event]
        if len(run) > best["length"]:
            best = {
                "length": len(run),
                "edrpou": event.supplier_edrpou,
                "supplier_name": event.supplier_name,
                "tender_ids": [e.tender_id or e.uuid for e in run],
                "cpv_groups": sorted({g for e in run for g in e.cpv_groups}),
            }
    return best
