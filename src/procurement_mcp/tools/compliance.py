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
    "compliant - true when every check passed, false when one failed, and null "
    "when a check could not be performed at all - a list of failed conditions "
    "each with an explanation and a citation, any inconclusive checks, and the "
    "exact threshold values and configuration version used to decide. Use it for any tender before judging it, because a "
    "threshold breach is itself one of the strongest signals."
)

PURPOSE = (
    "Answer one legal question about a tender: was the procedure it used allowed "
    "at the value it carried, under the rules in force when it was published. "
    "Separated from screening because it is the only check whose answer is a "
    "citable yes or no rather than a judgement."
)

SIDE_EFFECTS = "None. Reads the prepared dataset and the versioned threshold configuration."

ERROR_CONDITIONS = [
    ("INVALID_INPUT", "tender_identifier missing or an unknown argument supplied"),
    ("NOT_FOUND", "no such tender in the dataset"),
    ("DATA_INTEGRITY", "items are not classified with CPV-DK 021:2015, or the tender has no publication date"),
]

EXAMPLE_ARGUMENTS = {"tender_identifier": "UA-2026-08-18-004904-a"}

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
        "compliant": {
            "type": ["boolean", "null"],
            "description": "true if every check passed, false if one failed, null if a check could not be performed.",
        },
        "inconclusive_checks": {"type": "array", "items": {"type": "object"}},
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

    if not tender.is_classified_procedure and not tender.is_framework:
        checks.append(
            {
                "condition": "procedure_matches_value_threshold",
                "result": "unknown",
                "explanation": (
                    f"procedure {tender.procedure_type!r} is not one this rule set classifies; "
                    f"the value thresholds cannot be applied without knowing whether it awards "
                    f"directly or invites competition"
                ),
            }
        )
    elif tender.is_framework:
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

    # A check that could not be performed must not read as a pass. Reporting
    # compliant: true for a procedure the rule set cannot classify would assert
    # a verdict that was never established - the same error this tool now
    # refuses to make about the procedure type itself.
    inconclusive = [c for c in checks if c["result"] == "unknown"]
    if failed:
        compliant: bool | None = False
    elif inconclusive:
        compliant = None
    else:
        compliant = True

    return {
        "status": "ok",
        "compliant": compliant,
        "inconclusive_checks": inconclusive,
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
