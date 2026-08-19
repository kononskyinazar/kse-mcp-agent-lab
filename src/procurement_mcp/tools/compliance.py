"""check_procedure_threshold_compliance: procedure against the thresholds in force."""

from __future__ import annotations

from typing import Any

from ..config import Configuration
from ..errors import data_integrity
from ..models import Tender
from ..normalize import CPV_SCHEMES
from ..store import DatasetStore
from ..thresholds import StatutoryBook
from .validation import check_arguments

DESCRIPTION = (
    "Check whether the procurement procedure a tender used is consistent with "
    "the Ukrainian value thresholds and category rules that were in force on its "
    "publication date - not today's rules. Handles framework agreements as their "
    "own case, and verifies that the tender classifies its items with CPV-DK "
    "021:2015, which is what every threshold is expressed in terms of. Returns "
    "compliant true/false, a list of failed conditions each with an explanation "
    "and a citation, and the exact threshold values and configuration version "
    "used to decide. Use it for any tender before judging it, because a "
    "threshold breach is itself one of the strongest signals."
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tender_identifier": {
            "type": "string",
            "minLength": 4,
            "maxLength": 64,
            "description": "tenderID (for example 'UA-2026-06-01-000123-a') or document UUID.",
        }
    },
    "required": ["tender_identifier"],
    "additionalProperties": False,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok"]},
        "compliant": {"type": "boolean"},
        "tender": {"type": "object"},
        "subject": {"type": "string"},
        "failed_conditions": {"type": "array", "items": {"type": "object"}},
        "checks_performed": {"type": "array", "items": {"type": "object"}},
        "applicable_thresholds": {"type": "object"},
        "provenance": {"type": "object"},
    },
    "required": ["status", "compliant", "failed_conditions", "applicable_thresholds"],
}


def _assert_classifier(tender: Tender) -> None:
    unexpected = sorted({i.cpv_scheme for i in tender.items if i.cpv_scheme and i.cpv_scheme not in CPV_SCHEMES})
    if unexpected:
        raise data_integrity(
            "tender items are not classified with CPV-DK 021:2015, so the value "
            "thresholds cannot be applied to them",
            tender_id=tender.tender_id,
            found_schemes=unexpected,
            expected_schemes=sorted(CPV_SCHEMES),
        )


def run(config: Configuration, store: DatasetStore, arguments: dict[str, Any]) -> dict[str, Any]:
    parsed = check_arguments(arguments, INPUT_SCHEMA, tool="check_procedure_threshold_compliance")
    tender = store.get(parsed["tender_identifier"].strip())
    _assert_classifier(tender)

    book: StatutoryBook = config.statutes
    failed: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    thresholds: dict[str, Any] = {}

    if tender.published_at is None:
        raise data_integrity(
            "tender has no publication date, so no regime can be selected",
            tender_id=tender.tender_id,
        )

    regime = book.regime_at(tender.published_at)
    subject = book.subject_for(tender.main_category, tender.cpv_groups)
    open_from = book.mandatory_open_tender_from(subject, tender.published_at)
    thresholds["mandatory_open_tender_from"] = open_from.to_payload()

    if tender.is_framework:
        checks.append(
            {
                "condition": "framework_agreement",
                "result": "not_applicable",
                "explanation": (
                    "selection inside a concluded framework agreement is governed by that "
                    "agreement, so the open-tender value thresholds do not decide it"
                ),
            }
        )
    elif tender.amount is None:
        checks.append(
            {
                "condition": "expected_value_present",
                "result": "unknown",
                "explanation": "the tender carries no expected value, so the threshold cannot be applied",
            }
        )
    else:
        breach = tender.is_direct_award and tender.amount >= open_from.value
        checks.append(
            {
                "condition": "procedure_matches_value_threshold",
                "result": "failed" if breach else "passed",
                "expected_value": tender.amount,
                "threshold": open_from.value,
                "procedure_type": tender.procedure_type,
            }
        )
        if breach:
            failed.append(
                {
                    "condition": "procedure_matches_value_threshold",
                    "explanation": (
                        f"procedure {tender.procedure_type!r} awards directly, but the expected "
                        f"value {tender.amount:,.2f} {tender.currency or ''} is at or above the "
                        f"{open_from.value:,.0f} UAH threshold at which an open tender is required "
                        f"for {subject.replace('_', ' ')}"
                    ),
                    "statute_reference": open_from.to_payload(),
                }
            )

    if (
        tender.is_competitive_procedure
        and tender.tender_period.end is not None
        and book.period_rule_applies_to(tender.procedure_type, tender.published_at)
    ):
        minimum = book.minimum_tender_period_days(subject, tender.published_at)
        thresholds["minimum_tender_period_days"] = minimum.to_payload()
        actual = (tender.tender_period.end - tender.published_at).total_seconds() / 86400.0
        tolerance = book.tolerance_days(tender.published_at)
        short = actual < minimum.value - tolerance
        checks.append(
            {
                "condition": "bid_period_meets_minimum",
                "result": "failed" if short else "passed",
                "observed_days": round(actual, 2),
                "minimum_days": minimum.value,
            }
        )
        if short:
            failed.append(
                {
                    "condition": "bid_period_meets_minimum",
                    "explanation": (
                        f"bids were due {actual:.2f} days after publication, below the "
                        f"{minimum.value}-day minimum in force for {subject.replace('_', ' ')}"
                    ),
                    "statute_reference": minimum.to_payload(),
                }
            )

    if tender.is_competitive_procedure and not book.period_rule_applies_to(
        tender.procedure_type, tender.published_at
    ):
        checks.append(
            {
                "condition": "bid_period_meets_minimum",
                "result": "not_applicable",
                "explanation": (
                    f"no sourced minimum bid period is configured for procedure "
                    f"{tender.procedure_type!r}; it is governed by a separate order"
                ),
            }
        )

    return {
        "status": "ok",
        "compliant": not failed,
        "tender": {
            "tender_id": tender.tender_id,
            "uuid": tender.uuid,
            "procedure_type": tender.procedure_type,
            "expected_value": tender.amount,
            "currency": tender.currency,
            "main_category": tender.main_category,
            "published_at": tender.published_at.isoformat(),
        },
        "subject": subject,
        "failed_conditions": failed,
        "checks_performed": checks,
        "applicable_thresholds": {
            "regime": regime.identifier,
            "regime_name": regime.name,
            "effective_from": regime.effective_from.isoformat(),
            "effective_to": regime.effective_to.isoformat() if regime.effective_to else None,
            "configuration_version": book.version,
            "classifier": book.classifier,
            **thresholds,
        },
        "provenance": config.provenance(),
    }
