"""find_tenders: constrained retrieval over the prepared dataset."""

from __future__ import annotations

from typing import Any

from datetime import datetime, timezone

from ..config import Configuration
from ..errors import invalid_input
from ..models import Tender
from ..store import DatasetStore, coerce_date, require_edrpou
from .validation import check_arguments

DESCRIPTION = (
    "Find tenders in the prepared Prozorro dataset using a fixed set of filters: "
    "publication date range, buyer EDRPOU, awarded supplier EDRPOU, CPV prefix, "
    "procedure type, value range and region. There is no free-text query and no "
    "arbitrary expression language - anything outside this filter list is "
    "rejected. Dates filter on the tender's PUBLICATION date, not on when the "
    "record was last modified. Returns total_matched separately from a bounded "
    "sample, so a large result set is visible rather than silently truncated. "
    "Zero matches is a successful result with result_count 0. Use it to decide "
    "which tenders to screen; use screen_tender_red_flags to judge one."
)

MIN_MOMENT = datetime.min.replace(tzinfo=timezone.utc)

PROCEDURE_TYPES = [
    "aboveThreshold",
    "aboveThresholdUA",
    "aboveThresholdEU",
    "belowThreshold",
    "priceQuotation",
    "reporting",
    "negotiation",
    "negotiation.quick",
    "competitiveOrdering",
    "closeFrameworkAgreementUA",
    "closeFrameworkAgreementSelectionUA",
    "esco",
    "simple.defense",
]

PURPOSE = (
    "Narrow hundreds of tenders down to the handful worth screening, under "
    "filters the caller cannot exceed. The single retrieval tool in the set; "
    "everything else in this server computes or judges."
)

SIDE_EFFECTS = "None. Reads the prepared dataset only."

ERROR_CONDITIONS = [
    ("INVALID_INPUT", "malformed EDRPOU, non-ISO date, reversed range, non-numeric CPV prefix, unknown procedure type, or an unknown argument"),
    ("DATA_INTEGRITY", "the prepared dataset is missing or empty"),
]

EXAMPLE_ARGUMENTS = {"buyer_edrpou": "01999218", "cpv_prefix": "336", "limit": 2}

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "buyer_edrpou": {"type": "string", "minLength": 8, "maxLength": 10,
                          "description": "Buyer's EDRPOU code."},
        "supplier_edrpou": {"type": "string", "minLength": 8, "maxLength": 10,
                             "description": "EDRPOU of a supplier that won an award."},
        "published_from": {"type": "string", "description": "ISO date; publication date lower bound."},
        "published_to": {"type": "string", "description": "ISO date; publication date upper bound."},
        "cpv_prefix": {"type": "string", "minLength": 2, "maxLength": 10,
                        "description": "CPV-DK 021:2015 code prefix, e.g. '336' for medical supplies."},
        "procedure_types": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "enum": PROCEDURE_TYPES},
            "description": "Restrict to these procedure types.",
        },
        "min_value": {"type": "number", "minimum": 0, "description": "Expected value lower bound, UAH."},
        "max_value": {"type": "number", "minimum": 0, "description": "Expected value upper bound, UAH."},
        "region": {"type": "string", "minLength": 3, "maxLength": 80,
                    "description": "Buyer region, matched case-insensitively as a substring."},
        "statuses": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string"},
            "description": "Restrict to these tender statuses, e.g. 'complete', 'cancelled'.",
        },
        "exclude_tender_ids": {
            "type": "array",
            "maxItems": 500,
            "items": {"type": "string"},
            "description": "Tender ids to leave out, for skipping records already reviewed.",
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20,
                   "description": "Maximum tenders to return in the sample."},
    },
    "required": [],
    "additionalProperties": False,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok"]},
        "result_count": {"type": "integer", "minimum": 0},
        "total_matched": {"type": "integer", "minimum": 0},
        "truncated": {"type": "boolean"},
        "filters_applied": {"type": "object"},
        "tenders": {"type": "array", "items": {"type": "object"}},
        "data_window": {"type": "object"},
        "provenance": {"type": "object"},
    },
    "required": ["status", "result_count", "total_matched", "tenders"],
}


def _summary(tender: Tender) -> dict[str, Any]:
    award = tender.active_award
    return {
        "tender_id": tender.tender_id,
        "uuid": tender.uuid,
        "title": tender.title,
        "status": tender.status,
        "procedure_type": tender.procedure_type,
        "published_at": tender.published_at.isoformat() if tender.published_at else None,
        "expected_value": tender.amount,
        "currency": tender.currency,
        "buyer": {"edrpou": tender.buyer.edrpou, "name": tender.buyer.name, "region": tender.buyer.region},
        "cpv_groups": sorted(tender.cpv_groups),
        "bid_count": tender.effective_bid_count,
        "awarded_to": (
            [{"edrpou": s.edrpou, "name": s.name} for s in award.suppliers] if award else []
        ),
        "award_value": award.amount if award else None,
    }


def run(config: Configuration, store: DatasetStore, arguments: dict[str, Any]) -> dict[str, Any]:
    parsed = check_arguments(arguments, INPUT_SCHEMA, tool="find_tenders")

    buyer = require_edrpou(parsed["buyer_edrpou"]) if parsed.get("buyer_edrpou") else None
    supplier = require_edrpou(parsed["supplier_edrpou"], field="supplier_edrpou") if parsed.get("supplier_edrpou") else None
    published_from = coerce_date(parsed.get("published_from"), field="published_from")
    published_to = coerce_date(parsed.get("published_to"), field="published_to")
    min_value = parsed.get("min_value")
    max_value = parsed.get("max_value")
    limit = int(parsed.get("limit", 20))

    if published_from and published_to and published_from > published_to:
        raise invalid_input("published_from is later than published_to")
    if min_value is not None and max_value is not None and min_value > max_value:
        raise invalid_input("min_value is greater than max_value")

    cpv_prefix = parsed.get("cpv_prefix")
    if cpv_prefix is not None and not cpv_prefix.replace("-", "").isdigit():
        raise invalid_input("cpv_prefix must be digits, optionally with a hyphen", received=cpv_prefix)

    procedure_types = set(parsed.get("procedure_types") or [])
    statuses = set(parsed.get("statuses") or [])
    excluded = set(parsed.get("exclude_tender_ids") or [])
    region = (parsed.get("region") or "").casefold() or None

    if buyer:
        candidates = store.for_buyer(buyer)
    elif supplier:
        candidates = store.for_supplier(supplier)
    else:
        candidates = store.all_tenders()

    matched: list[Tender] = []
    for tender in candidates:
        if supplier and buyer:
            award = tender.active_award
            if award is None or supplier not in award.supplier_edrpous:
                continue
        if tender.tender_id in excluded or tender.uuid in excluded:
            continue
        if procedure_types and tender.procedure_type not in procedure_types:
            continue
        if statuses and tender.status not in statuses:
            continue
        if published_from or published_to:
            if tender.published_at is None:
                continue
            if published_from and tender.published_at < published_from:
                continue
            if published_to and tender.published_at > published_to:
                continue
        if cpv_prefix and not any((i.cpv or "").startswith(cpv_prefix) for i in tender.items):
            continue
        if min_value is not None and (tender.amount is None or tender.amount < min_value):
            continue
        if max_value is not None and (tender.amount is None or tender.amount > max_value):
            continue
        if region and region not in (tender.buyer.region or "").casefold():
            continue
        matched.append(tender)

    # Newest publication first, with undated records last rather than first:
    # reversing a (is_none, date) tuple would float them to the top.
    matched.sort(key=lambda t: (t.published_at is not None, t.published_at or MIN_MOMENT, t.uuid), reverse=True)
    sample = matched[:limit]

    return {
        "status": "ok",
        "result_count": len(sample),
        "total_matched": len(matched),
        # A capped result says so; a silent truncation reads as "this is all of it".
        "truncated": len(matched) > len(sample),
        # The exclusion list can hold hundreds of ids; echoing it back would put
        # kilobytes of pure repetition into the caller's context on every call.
        "filters_applied": {
            k: (f"{len(v)} tender ids" if k == "exclude_tender_ids" else v)
            for k, v in parsed.items()
            if v not in (None, [], "")
        },
        "tenders": [_summary(t) for t in sample],
        "data_window": store.data_window().to_payload(),
        "provenance": config.provenance(),
    }
