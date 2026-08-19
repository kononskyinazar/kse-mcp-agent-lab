"""Builders for raw tender documents, shaped like the real API payloads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

KYIV = timezone(timedelta(hours=3))
BASE = datetime(2026, 6, 1, 10, 0, tzinfo=KYIV)


def iso(moment: datetime) -> str:
    return moment.isoformat()


def tender_doc(
    *,
    uuid: str = "t-1",
    tender_id: str = "UA-2026-06-01-000001-a",
    procedure: str = "aboveThreshold",
    status: str = "complete",
    amount: float | None = 500_000.0,
    currency: str = "UAH",
    buyer_edrpou: str = "03327121",
    buyer_name: str = "КП Водоканал",
    region: str = "Львівська область",
    published: datetime | None = BASE,
    tender_end: datetime | None = None,
    category: str = "goods",
    items: list[dict[str, Any]] | None = None,
    bids: list[dict[str, Any]] | None = None,
    awards: list[dict[str, Any]] | None = None,
    cancellations: list[dict[str, Any]] | None = None,
    title: str = "Закупівля насосного обладнання",
    description: str | None = None,
) -> dict[str, Any]:
    # Real document ids are 32-hex; the tools enforce a minimum length, so short
    # test ids are padded rather than weakening the contract for the tests.
    document: dict[str, Any] = {
        "id": uuid if len(uuid) >= 6 else f"doc-{uuid}",
        "tenderID": tender_id,
        "title": title,
        "status": status,
        "procurementMethodType": procedure,
        "procurementMethod": "open" if procedure != "reporting" else "limited",
        "mainProcurementCategory": category,
        "procuringEntity": {
            "name": buyer_name,
            "identifier": {"scheme": "UA-EDR", "id": buyer_edrpou, "legalName": buyer_name},
            "address": {"region": region},
        },
        "items": items if items is not None else [
            {"description": "Насос відцентровий", "classification": {"scheme": "ДК021", "id": "42122000-0"}}
        ],
    }
    if description is not None:
        document["description"] = description
    if amount is not None:
        document["value"] = {"amount": amount, "currency": currency, "valueAddedTaxIncluded": True}
    if published is not None:
        document["noticePublicationDate"] = iso(published)
        end = tender_end if tender_end is not None else published + timedelta(days=20)
        document["tenderPeriod"] = {"startDate": iso(published), "endDate": iso(end)}
    if bids is not None:
        document["bids"] = bids
    if awards is not None:
        document["awards"] = awards
    if cancellations is not None:
        document["cancellations"] = cancellations
    return document


def bid(
    *,
    identifier: str = "b-1",
    edrpou: str = "40000001",
    name: str = "ТОВ Постачальник",
    submitted: datetime | None = None,
    status: str = "active",
    subcontracting: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": identifier,
        "status": status,
        "tenderers": [{"name": name, "identifier": {"scheme": "UA-EDR", "id": edrpou, "legalName": name}}],
    }
    if submitted is not None:
        payload["submissionDate"] = iso(submitted)
        payload["date"] = iso(submitted)
    if subcontracting is not None:
        payload["subcontractingDetails"] = subcontracting
    return payload


def award(
    *,
    identifier: str = "a-1",
    bid_id: str = "b-1",
    edrpou: str = "40000001",
    name: str = "ТОВ Постачальник",
    amount: float = 490_000.0,
    currency: str = "UAH",
    status: str = "active",
    date: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "bid_id": bid_id,
        "status": status,
        "date": iso(date or BASE + timedelta(days=25)),
        "value": {"amount": amount, "currency": currency},
        "suppliers": [{"name": name, "identifier": {"scheme": "UA-EDR", "id": edrpou, "legalName": name}}],
    }


def cancellation(
    *,
    identifier: str = "c-1",
    status: str = "active",
    reason: str = "відсутність подальшої потреби",
    reason_type: str = "noDemand",
    date: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "status": status,
        "reason": reason,
        "reasonType": reason_type,
        "date": iso(date or BASE + timedelta(days=30)),
    }
