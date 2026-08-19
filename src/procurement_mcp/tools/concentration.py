"""compute_buyer_supplier_concentration: how concentrated one buyer's awards are."""

from __future__ import annotations

from typing import Any

from ..analysis import award_sequence, best_streak, concentration, monthly_trend
from ..config import Configuration
from ..store import DatasetStore, coerce_date, require_edrpou
from .validation import check_arguments

DESCRIPTION = (
    "Measure how concentrated one buyer's awarded contracts are among its "
    "suppliers, over the tenders held in the prepared dataset. Returns the "
    "Herfindahl-Hirschman index together with the top-1 and top-3 supplier "
    "shares - each computed BOTH by awarded value and by award count, because a "
    "buyer can look concentrated on one and dispersed on the other - the number "
    "of distinct suppliers, a monthly concentration trend with its direction, "
    "and the longest run of consecutive awards to a single supplier. Use it once "
    "per buyer to get context for reading that buyer's individual tenders. A "
    "buyer with no awards in the dataset is a successful empty result, not an "
    "error."
)

PURPOSE = (
    "Give the analyst the buyer-level context that a single tender cannot show: "
    "whether this buyer's money keeps landing with the same suppliers. Called "
    "once per buyer, before its tenders are read."
)

SIDE_EFFECTS = "None. Reads the prepared dataset only."

ERROR_CONDITIONS = [
    ("INVALID_INPUT", "buyer_edrpou is not 8 or 10 digits, dates are not ISO, or the period is reversed"),
    ("DATA_INTEGRITY", "the prepared dataset is missing or empty"),
]

EXAMPLE_ARGUMENTS = {"buyer_edrpou": "31557119", "include_trend": True}

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "buyer_edrpou": {
            "type": "string",
            "minLength": 8,
            "maxLength": 10,
            "description": "Ukrainian EDRPOU code of the buyer: 8 digits, or 10 for some registrations.",
        },
        "published_from": {
            "type": "string",
            "description": "Optional ISO date. Counts only tenders published on or after it.",
        },
        "published_to": {
            "type": "string",
            "description": "Optional ISO date. Counts only tenders published on or before it.",
        },
        "include_trend": {
            "type": "boolean",
            "default": True,
            "description": "Include the monthly concentration trend.",
        },
    },
    "required": ["buyer_edrpou"],
    "additionalProperties": False,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok"]},
        "buyer": {"type": "object"},
        "result_count": {"type": "integer", "minimum": 0},
        "tenders_considered": {"type": "integer"},
        "awards_counted": {"type": "integer"},
        "distinct_suppliers": {"type": "integer"},
        "total_awarded_value": {"type": "number"},
        "by_value": {"type": "object"},
        "by_count": {"type": "object"},
        "trend": {"type": "object"},
        "supplier_win_streak": {"type": "object"},
        "data_window": {"type": "object"},
        "provenance": {"type": "object"},
    },
    "required": ["status", "result_count", "buyer"],
}


def run(config: Configuration, store: DatasetStore, arguments: dict[str, Any]) -> dict[str, Any]:
    parsed = check_arguments(arguments, INPUT_SCHEMA, tool="compute_buyer_supplier_concentration")
    edrpou = require_edrpou(parsed["buyer_edrpou"])
    published_from = coerce_date(parsed.get("published_from"), field="published_from")
    published_to = coerce_date(parsed.get("published_to"), field="published_to")
    include_trend = parsed.get("include_trend", True)

    if published_from and published_to and published_from > published_to:
        from ..errors import invalid_input

        raise invalid_input(
            "published_from is later than published_to",
            published_from=published_from.isoformat(),
            published_to=published_to.isoformat(),
        )

    tenders = store.for_buyer(edrpou)
    if published_from or published_to:
        tenders = [
            t
            for t in tenders
            if t.published_at is not None
            and (published_from is None or t.published_at >= published_from)
            and (published_to is None or t.published_at <= published_to)
        ]

    events = award_sequence(tenders)
    metrics = concentration(events)
    buyer_name = next((t.buyer.name for t in tenders if t.buyer.name), None)

    payload: dict[str, Any] = {
        "status": "ok",
        "buyer": {"edrpou": edrpou, "name": buyer_name},
        # A well-formed code with no awards is an empty success. Callers tell it
        # apart from a failure by this field, never by an empty object.
        "result_count": len(events),
        "tenders_considered": len(tenders),
        "period": {
            "published_from": published_from.isoformat() if published_from else None,
            "published_to": published_to.isoformat() if published_to else None,
        },
        **metrics,
        "supplier_win_streak": best_streak(events),
        "data_window": store.data_window().to_payload(),
        "provenance": config.provenance(),
    }
    if include_trend:
        payload["trend"] = monthly_trend(events)
    if not events:
        payload["note"] = (
            "no active awards for this buyer within the dataset window; this is an "
            "empty result, not a failure"
        )
    return payload
