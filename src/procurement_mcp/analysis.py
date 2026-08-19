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
from datetime import datetime, timezone
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
    # Undated events sort first against a fixed aware sentinel. Converting
    # datetime.min to local time, as an earlier version did, raises OverflowError
    # on some platforms and would take the whole concentration call down.
    sentinel = datetime.min.replace(tzinfo=timezone.utc)
    events.sort(key=lambda e: (e.moment or sentinel, e.uuid))
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

    # Direction comes from a least-squares slope over every bucket, not from
    # first-versus-last: a buyer whose concentration spikes in the middle and
    # returns would otherwise be reported as "stable".
    direction, magnitude, slope = "insufficient_data", None, None
    if len(series) >= 2:
        values = [b["hhi_by_value"] for b in series]
        n = len(values)
        mean_x = (n - 1) / 2
        mean_y = sum(values) / n
        denominator = sum((i - mean_x) ** 2 for i in range(n))
        slope = round(sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values)) / denominator, 5)
        magnitude = round(values[-1] - values[0], 4)
        if abs(slope) < 0.02:
            direction = "stable"
        else:
            direction = "increasing" if slope > 0 else "decreasing"

    # Buckets exist only for months that had awards, so the series can be
    # non-contiguous. Saying so keeps "periods_analyzed: 2" from reading as two
    # consecutive months when it may span half a year.
    months_spanned = None
    gaps: list[str] = []
    if series:
        first_year, first_month = (int(p) for p in series[0]["month"].split("-"))
        last_year, last_month = (int(p) for p in series[-1]["month"].split("-"))
        months_spanned = (last_year - first_year) * 12 + (last_month - first_month) + 1
        present = {b["month"] for b in series}
        for offset in range(months_spanned):
            year, month = divmod((first_year * 12 + first_month - 1) + offset, 12)
            label = f"{year:04d}-{month + 1:02d}"
            if label not in present:
                gaps.append(label)

    return {
        "buckets": series,
        "periods_analyzed": len(series),
        "months_spanned": months_spanned,
        "months_without_awards": gaps,
        "direction": direction,
        "magnitude": magnitude,
        "slope_per_month": slope,
        "note": (
            "fewer than three buckets is a hint, not a trend"
            if len(series) < 3
            else "monthly buckets over the dataset window; direction is a least-squares slope"
        ),
    }


def _tender_sequence(events: list[AwardEvent]) -> list[dict[str, Any]]:
    """Collapse per-supplier events back into one entry per tender.

    A streak is a run of consecutive *tenders* won by a supplier. Walking the
    raw event list instead would let a joint award - which emits one event per
    supplier - break every run, so a buyer awarding everything to the same pair
    of firms would report no streak at all.
    """
    order: list[str] = []
    grouped: dict[str, dict[str, Any]] = {}
    for event in events:
        entry = grouped.get(event.uuid)
        if entry is None:
            order.append(event.uuid)
            entry = grouped[event.uuid] = {
                "uuid": event.uuid,
                "tender_id": event.tender_id or event.uuid,
                "moment": event.moment,
                "suppliers": set(),
                "names": {},
                "cpv_groups": set(event.cpv_groups),
            }
        entry["suppliers"].add(event.supplier_edrpou)
        if event.supplier_name:
            entry["names"].setdefault(event.supplier_edrpou, event.supplier_name)
        entry["cpv_groups"].update(event.cpv_groups)
    return [grouped[uuid] for uuid in order]


def longest_streak(
    events: list[AwardEvent], edrpou: str, *, until: datetime | None = None
) -> dict[str, Any]:
    """The run of consecutive tenders this supplier has won, ending at ``until``.

    Trailing rather than best-ever: what matters when screening one tender is
    whether the same supplier has been winning right up to it.
    """
    sequence = [
        t
        for t in _tender_sequence(events)
        if until is None or (t["moment"] is not None and t["moment"] <= until)
    ]
    tender_ids: list[str] = []
    cpv_groups: set[str] = set()

    for tender in reversed(sequence):
        if edrpou not in tender["suppliers"]:
            break
        tender_ids.append(tender["tender_id"])
        cpv_groups.update(tender["cpv_groups"])

    return {
        "length": len(tender_ids),
        "tender_ids": list(reversed(tender_ids)),
        "cpv_groups": sorted(cpv_groups),
    }


def best_streak(events: list[AwardEvent]) -> dict[str, Any]:
    """The longest run of consecutive tenders held by any one supplier."""
    sequence = _tender_sequence(events)
    best: dict[str, Any] = {"length": 0, "edrpou": None, "tender_ids": [], "cpv_groups": []}
    runs: dict[str, list[dict[str, Any]]] = {}

    for tender in sequence:
        for edrpou in list(runs):
            if edrpou not in tender["suppliers"]:
                runs.pop(edrpou)
        for edrpou in tender["suppliers"]:
            runs.setdefault(edrpou, []).append(tender)
            run = runs[edrpou]
            if len(run) > best["length"]:
                best = {
                    "length": len(run),
                    "edrpou": edrpou,
                    "supplier_name": tender["names"].get(edrpou),
                    "tender_ids": [t["tender_id"] for t in run],
                    "cpv_groups": sorted({g for t in run for g in t["cpv_groups"]}),
                }
    return best
