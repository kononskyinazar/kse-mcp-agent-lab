"""Turning a raw Prozorro document into the domain objects the rules use.

This is the single parsing path. A live API response, a replayed fixture and a
document from the committed dataset all arrive here, so "where did this value
come from?" is answerable by reading one function.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .errors import data_integrity
from .models import Award, Bid, Cancellation, Item, Party, Period, Tender

# CPV-DK 021:2015 is what Prozorro publishes. The tools assert it rather than
# assume it, because every threshold rule is expressed in terms of that
# classifier and a different one would silently change their meaning.
CPV_SCHEMES = frozenset({"ДК021", "CPV", "ДК021:2015"})


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _party(payload: dict[str, Any] | None) -> Party:
    payload = payload or {}
    identifier = payload.get("identifier") or {}
    address = payload.get("address") or {}
    return Party(
        edrpou=identifier.get("id"),
        name=identifier.get("legalName") or payload.get("name"),
        region=address.get("region"),
    )


def _period(payload: dict[str, Any] | None) -> Period:
    payload = payload or {}
    return Period(start=parse_datetime(payload.get("startDate")), end=parse_datetime(payload.get("endDate")))


def _items(payload: list[dict[str, Any]] | None) -> tuple[tuple[Item, ...], list[str]]:
    items: list[Item] = []
    warnings: list[str] = []
    for raw in payload or []:
        classification = raw.get("classification") or {}
        scheme = classification.get("scheme")
        if scheme and scheme not in CPV_SCHEMES:
            warnings.append(f"unexpected classification scheme {scheme!r}")
        items.append(
            Item(
                cpv=classification.get("id"),
                cpv_scheme=scheme,
                description=raw.get("description"),
            )
        )
    return tuple(items), warnings


def _subcontracting(raw: dict[str, Any]) -> tuple[str, ...]:
    """Subcontracting is free text in this API, so it is collected verbatim.

    Nothing downstream treats it as structured: rules that use it quote it as
    evidence and stay advisory.
    """
    values: list[str] = []
    direct = raw.get("subcontractingDetails")
    if isinstance(direct, str) and direct.strip():
        values.append(direct.strip())
    for lot_value in raw.get("lotValues") or []:
        nested = lot_value.get("subcontractingDetails")
        if isinstance(nested, str) and nested.strip():
            values.append(nested.strip())
    return tuple(dict.fromkeys(values))


def _bids(payload: list[dict[str, Any]] | None) -> tuple[Bid, ...]:
    bids = []
    for raw in payload or []:
        bids.append(
            Bid(
                identifier=raw.get("id"),
                status=raw.get("status"),
                # submissionDate is the moment the bid was filed; date is the
                # last touch. Timing rules need the former and fall back only
                # when it is absent.
                submitted_at=parse_datetime(raw.get("submissionDate")) or parse_datetime(raw.get("date")),
                tenderers=tuple(_party(t) for t in raw.get("tenderers") or []),
                subcontracting_details=_subcontracting(raw),
            )
        )
    return tuple(bids)


def _awards(payload: list[dict[str, Any]] | None) -> tuple[Award, ...]:
    awards = []
    for raw in payload or []:
        value = raw.get("value") or {}
        awards.append(
            Award(
                identifier=raw.get("id"),
                bid_id=raw.get("bid_id"),
                status=raw.get("status"),
                date=parse_datetime(raw.get("date")),
                amount=value.get("amount"),
                currency=value.get("currency"),
                suppliers=tuple(_party(s) for s in raw.get("suppliers") or []),
            )
        )
    return tuple(awards)


def _cancellations(payload: list[dict[str, Any]] | None) -> tuple[Cancellation, ...]:
    cancellations = []
    for raw in payload or []:
        cancellations.append(
            Cancellation(
                identifier=raw.get("id"),
                status=raw.get("status"),
                reason=raw.get("reason"),
                reason_type=raw.get("reasonType"),
                date=parse_datetime(raw.get("date")),
            )
        )
    return tuple(cancellations)


def normalize_tender(document: dict[str, Any]) -> Tender:
    """Build a Tender from a raw API document.

    Raises DATA_INTEGRITY only for the two things that would make every
    downstream answer meaningless: no identifier, or no procedure type.
    """
    if not isinstance(document, dict):
        raise data_integrity("tender document is not an object")

    uuid = document.get("id")
    if not uuid:
        raise data_integrity("tender document has no id", tender_id=document.get("tenderID"))

    procedure_type = document.get("procurementMethodType")
    if not procedure_type:
        raise data_integrity(
            "tender document has no procurementMethodType, so no rule set applies",
            tender_id=document.get("tenderID"),
        )

    value = document.get("value") or {}
    items, warnings = _items(document.get("items"))

    return Tender(
        uuid=uuid,
        tender_id=document.get("tenderID"),
        title=document.get("title"),
        description=document.get("description"),
        status=document.get("status"),
        procurement_method=document.get("procurementMethod"),
        procedure_type=procedure_type,
        buyer=_party(document.get("procuringEntity")),
        amount=value.get("amount"),
        currency=value.get("currency"),
        vat_included=value.get("valueAddedTaxIncluded"),
        # noticePublicationDate is the legally meaningful moment; dateCreated
        # and date are fallbacks for older records that predate the field.
        published_at=(
            parse_datetime(document.get("noticePublicationDate"))
            or parse_datetime(document.get("dateCreated"))
            or parse_datetime(document.get("date"))
        ),
        enquiry_period=_period(document.get("enquiryPeriod")),
        tender_period=_period(document.get("tenderPeriod")),
        items=items,
        bids=_bids(document.get("bids")),
        awards=_awards(document.get("awards")),
        cancellations=_cancellations(document.get("cancellations")),
        lot_count=len(document.get("lots") or []),
        main_category=document.get("mainProcurementCategory"),
        warnings=tuple(warnings),
    )
