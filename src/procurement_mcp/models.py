"""Normalised domain objects.

Only the fields the rules actually reason about are lifted out of the raw
document. Everything else stays in the stored JSON, which is never discarded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Procedures that invite competing bids. Everything outside this set either
# awards directly (reporting, negotiation) or selects inside an existing
# framework, so competition rules must not be applied to it.
COMPETITIVE_PROCEDURES = frozenset(
    {
        "aboveThreshold",
        "aboveThresholdUA",
        "aboveThresholdEU",
        "aboveThresholdUA.defense",
        "belowThreshold",
        "simple.defense",
        "priceQuotation",
        "competitiveDialogueUA",
        "competitiveDialogueEU",
        "competitiveOrdering",
        "esco",
        "closeFrameworkAgreementUA",
    }
)

# Direct-award procedures: zero bids is the designed outcome, not a red flag.
DIRECT_AWARD_PROCEDURES = frozenset(
    {"reporting", "negotiation", "negotiation.quick", "closeFrameworkAgreementSelectionUA"}
)

FRAMEWORK_PROCEDURES = frozenset({"closeFrameworkAgreementUA", "closeFrameworkAgreementSelectionUA"})


@dataclass(frozen=True)
class Party:
    edrpou: str | None
    name: str | None
    region: str | None = None

    @property
    def label(self) -> str:
        return self.name or self.edrpou or "unknown"


@dataclass(frozen=True)
class Item:
    cpv: str | None
    cpv_scheme: str | None
    description: str | None

    @property
    def cpv_group(self) -> str | None:
        """First four digits: the CPV division/group used for comparability."""
        return self.cpv[:4] if self.cpv else None


@dataclass(frozen=True)
class Bid:
    identifier: str | None
    status: str | None
    submitted_at: datetime | None
    tenderers: tuple[Party, ...] = ()
    subcontracting_details: tuple[str, ...] = ()

    @property
    def tenderer_edrpous(self) -> set[str]:
        return {t.edrpou for t in self.tenderers if t.edrpou}


@dataclass(frozen=True)
class Award:
    identifier: str | None
    bid_id: str | None
    status: str | None
    date: datetime | None
    amount: float | None
    currency: str | None
    suppliers: tuple[Party, ...] = ()

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def supplier_edrpous(self) -> set[str]:
        return {s.edrpou for s in self.suppliers if s.edrpou}


@dataclass(frozen=True)
class Cancellation:
    identifier: str | None
    status: str | None
    reason: str | None
    reason_type: str | None
    date: datetime | None

    @property
    def is_effective(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True)
class Period:
    start: datetime | None
    end: datetime | None

    @property
    def duration_days(self) -> float | None:
        if self.start is None or self.end is None:
            return None
        return (self.end - self.start).total_seconds() / 86400.0


@dataclass(frozen=True)
class Tender:
    uuid: str
    tender_id: str | None
    title: str | None
    description: str | None
    status: str | None
    procurement_method: str | None
    procedure_type: str | None
    buyer: Party
    amount: float | None
    currency: str | None
    vat_included: bool | None
    published_at: datetime | None
    enquiry_period: Period
    tender_period: Period
    items: tuple[Item, ...] = ()
    bids: tuple[Bid, ...] = ()
    awards: tuple[Award, ...] = ()
    cancellations: tuple[Cancellation, ...] = ()
    lot_count: int = 0
    main_category: str | None = None
    warnings: tuple[str, ...] = field(default=())

    @property
    def is_competitive_procedure(self) -> bool:
        return self.procedure_type in COMPETITIVE_PROCEDURES

    @property
    def is_direct_award(self) -> bool:
        return self.procedure_type in DIRECT_AWARD_PROCEDURES

    @property
    def is_framework(self) -> bool:
        return self.procedure_type in FRAMEWORK_PROCEDURES

    @property
    def is_classified_procedure(self) -> bool:
        """Whether this procedure type is one the rule set actually knows.

        Prozorro publishes more types than are enumerated here. Saying "unknown"
        is honest; treating an unrecognised type as competitive would exempt a
        direct-award procedure from the value-threshold check while claiming a
        classification that was never made.
        """
        return self.is_competitive_procedure or self.is_direct_award

    @property
    def active_award(self) -> Award | None:
        for award in self.awards:
            if award.is_active:
                return award
        return None

    @property
    def cpv_groups(self) -> set[str]:
        return {item.cpv_group for item in self.items if item.cpv_group}

    @property
    def effective_bid_count(self) -> int:
        """Bids that were actually submitted, ignoring drafts and withdrawals."""
        return sum(1 for bid in self.bids if bid.status not in {"draft", "invalid", "deleted"})
