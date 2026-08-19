"""screen_tender_red_flags: screen one tender against the rules in force."""

from __future__ import annotations

from typing import Any

from ..config import Configuration
from ..errors import invalid_input
from ..rules.base import RuleContext, screen
from ..rules.red_flags import ALL_RULES
from ..store import DatasetStore
from .validation import check_arguments

DESCRIPTION = (
    "Screen ONE Ukrainian public tender for procurement red flags and return a "
    "structured, auditable result. Applies only the rules that fit the tender's "
    "procedure type and the statutory thresholds in force on its publication "
    "date. Returns blocking_violations (breaches of a citable written rule), "
    "advisories (lawful but suspicious patterns, including single participation "
    "and supplier win streaks), a 0-100 risk_score, an evidence_chain showing "
    "every signal with its weight and contribution, and rules_not_applicable "
    "explaining each rule that was skipped and why. Use it once per tender, "
    "after find_tenders has identified which tenders to look at. Do not use it "
    "to compare buyers or suppliers over time - that is "
    "compute_buyer_supplier_concentration."
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tender_identifier": {
            "type": "string",
            "minLength": 4,
            "maxLength": 64,
            "description": (
                "Either the human-facing tenderID (for example "
                "'UA-2026-06-01-000123-a') or the document UUID, as returned by "
                "find_tenders."
            ),
        },
        "include_evidence": {
            "type": "boolean",
            "default": True,
            "description": "Return the full evidence chain. Set false only for a compact overview.",
        },
    },
    "required": ["tender_identifier"],
    "additionalProperties": False,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok"]},
        "tender": {
            "type": "object",
            "properties": {
                "tender_id": {"type": ["string", "null"]},
                "uuid": {"type": "string"},
                "title": {"type": ["string", "null"]},
                "procedure_type": {"type": "string"},
                "status": {"type": ["string", "null"]},
                "expected_value": {"type": ["number", "null"]},
                "currency": {"type": ["string", "null"]},
                "published_at": {"type": ["string", "null"]},
                "buyer": {"type": "object"},
            },
        },
        "has_blocking": {"type": "boolean"},
        "risk_score": {"type": "number", "minimum": 0, "maximum": 100},
        "raw_weighted_sum": {"type": "number"},
        "blocking_floor_applied": {"type": "boolean"},
        "requires_human_review": {"type": "boolean"},
        "blocking_violations": {"type": "array", "items": {"type": "object"}},
        "advisories": {"type": "array", "items": {"type": "object"}},
        "evidence_chain": {"type": "array", "items": {"type": "object"}},
        "rules_not_applicable": {"type": "array", "items": {"type": "object"}},
        "rules_errored": {"type": "array", "items": {"type": "object"}},
        "data_window": {"type": "object"},
        "provenance": {"type": "object"},
    },
    "required": ["status", "risk_score", "has_blocking", "blocking_violations", "advisories"],
}


def run(config: Configuration, store: DatasetStore, arguments: dict[str, Any]) -> dict[str, Any]:
    parsed = check_arguments(arguments, INPUT_SCHEMA, tool="screen_tender_red_flags")
    identifier = parsed["tender_identifier"].strip()
    include_evidence = parsed.get("include_evidence", True)
    if not identifier:
        raise invalid_input("tender_identifier is required", field="tender_identifier")

    tender = store.get(identifier)
    context = RuleContext(
        tender=tender,
        rules=config.rule_book.rules,
        book=config.statutes,
        store=store,
    )
    result = screen(context, ALL_RULES, config.rule_book.scoring)

    payload: dict[str, Any] = {
        "status": "ok",
        "tender": {
            "tender_id": tender.tender_id,
            "uuid": tender.uuid,
            "title": tender.title,
            "procedure_type": tender.procedure_type,
            "status": tender.status,
            "expected_value": tender.amount,
            "currency": tender.currency,
            "published_at": tender.published_at.isoformat() if tender.published_at else None,
            "buyer": {
                "edrpou": tender.buyer.edrpou,
                "name": tender.buyer.name,
                "region": tender.buyer.region,
            },
        },
        "has_blocking": result.has_blocking,
        "risk_score": result.risk_score,
        "raw_weighted_sum": result.raw_score,
        "blocking_floor_applied": result.floor_applied,
        "requires_human_review": (
            result.has_blocking or result.risk_score >= config.rule_book.human_review_threshold
        ),
        "blocking_violations": [f.to_payload() for f in result.blocking_violations],
        "advisories": [f.to_payload() for f in result.advisories],
        "rules_not_applicable": [s.to_payload() for s in result.skipped],
        "rules_errored": result.errored,
        "data_window": store.data_window().to_payload(),
        "provenance": config.provenance(),
    }
    if include_evidence:
        payload["evidence_chain"] = result.evidence_chain()
    if tender.warnings:
        payload["source_warnings"] = list(tender.warnings)
    return payload
