"""The red-flag rules themselves.

Naming discipline: `blocking` is a breach of a written rule that can be cited;
everything else is an advisory no matter how strong. A single bidder is lawful
in Ukraine, so it is a heavily weighted advisory - not a violation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from ..models import Tender
from .base import Finding, RuleContext

# A make or model looks like a Latin-script token or an alphanumeric model code
# inside otherwise Cyrillic procurement text.
BRAND_TOKEN = re.compile(r"\b(?:[A-Z][A-Za-z]{2,}(?:[- ]?\d{1,4}[A-Za-z]?)?|[A-Z]{2,}\d{2,})\b")
# Latin words that are units, standards or common qualifiers rather than brands.
BRAND_STOPWORDS = frozenset(
    {
        "ISO", "DIN", "EN", "GOST", "DSTU", "USB", "LED", "PVC", "HDPE", "PPR", "IP", "PN",
        "SM", "ML", "KG", "MM", "CM", "GB", "TB", "RAL", "IEC", "ANSI", "UA", "EU", "CPV",
        "ДК", "PDF", "XML", "HD", "WI", "FI",
    }
)


def _text_blobs(tender: Tender) -> list[str]:
    blobs = [tender.title or "", tender.description or ""]
    blobs.extend(item.description or "" for item in tender.items)
    return [b for b in blobs if b.strip()]


@dataclass
class BidWindowBelowMinimum:
    """Blocking: the bid submission window was shorter than the law allows."""

    id: str = "bid_window_below_statutory_minimum"

    def applies(self, ctx: RuleContext) -> str | None:
        tender = ctx.tender
        if not tender.is_competitive_procedure:
            return f"procedure {tender.procedure_type!r} does not invite competing bids"
        if tender.published_at is None:
            return "no publication date, so the applicable regime cannot be selected"
        if tender.tender_period.end is None:
            return "tender period has no end date"
        if not ctx.book.period_rule_applies_to(tender.procedure_type, tender.published_at):
            return (
                f"no sourced minimum bid period is configured for procedure "
                f"{tender.procedure_type!r}; it runs under a separate order with its own timetable"
            )
        return None

    def check(self, ctx: RuleContext) -> Finding | None:
        tender = ctx.tender
        subject = ctx.book.subject_for(tender.main_category, tender.cpv_groups)
        hit = ctx.book.minimum_tender_period_days(subject, tender.published_at)
        tolerance = ctx.book.tolerance_days(tender.published_at)

        # Counted from publication, which is the legally meaningful start, not
        # from whenever the period object happens to open.
        actual_days = (tender.tender_period.end - tender.published_at).total_seconds() / 86400.0
        if actual_days >= hit.value - tolerance:
            return None

        return ctx.finding(
            self.id,
            observed=round(actual_days, 2),
            threshold=hit.value,
            evidence={
                "published_at": tender.published_at.isoformat(),
                "bids_due": tender.tender_period.end.isoformat(),
                "subject": subject,
                "tolerance_days": tolerance,
            },
            source=hit.to_payload(),
        )


@dataclass
class ProcedureThresholdMismatch:
    """Blocking: a direct award at or above the value that requires open tender."""

    id: str = "procedure_value_threshold_mismatch"

    def applies(self, ctx: RuleContext) -> str | None:
        tender = ctx.tender
        if tender.is_framework:
            return "framework selection is governed by the agreement, not by the value thresholds"
        if not tender.is_direct_award:
            return f"procedure {tender.procedure_type!r} is already a competitive procedure"
        if tender.amount is None:
            return "tender has no expected value to compare against the threshold"
        if tender.published_at is None:
            return "no publication date, so the applicable regime cannot be selected"
        return None

    def check(self, ctx: RuleContext) -> Finding | None:
        tender = ctx.tender
        subject = ctx.book.subject_for(tender.main_category, tender.cpv_groups)
        hit = ctx.book.mandatory_open_tender_from(subject, tender.published_at)
        if tender.amount < hit.value:
            return None

        # negotiation procedures have their own lawful grounds, which this data
        # does not carry; the finding says so instead of overstating.
        return ctx.finding(
            self.id,
            observed=tender.amount,
            threshold=hit.value,
            evidence={
                "procedure_type": tender.procedure_type,
                "subject": subject,
                "currency": tender.currency,
                "note": (
                    "negotiation procedures may have lawful grounds that this dataset "
                    "does not record; verify the stated grounds before acting"
                    if tender.procedure_type != "reporting"
                    else "a direct contract at or above the open-tender threshold"
                ),
            },
            source=hit.to_payload(),
        )


@dataclass
class EffectiveSingleParticipation:
    """Advisory, top weight: lawful, but the strongest single risk indicator."""

    id: str = "effective_single_participation"

    def applies(self, ctx: RuleContext) -> str | None:
        tender = ctx.tender
        if not tender.is_competitive_procedure:
            return f"procedure {tender.procedure_type!r} awards directly, so a single participant is by design"
        if tender.status in {"active.tendering", "active.enquiries", "draft"}:
            return f"status {tender.status!r} is before bid opening, so participation is not yet observable"
        if not tender.awards:
            return "no award yet, so participation cannot be judged"
        return None

    def check(self, ctx: RuleContext) -> Finding | None:
        tender = ctx.tender
        count = tender.effective_bid_count
        if count > 1:
            return None
        return ctx.finding(
            self.id,
            observed=count,
            threshold=2,
            evidence={
                "procedure_type": tender.procedure_type,
                "status": tender.status,
                "note": "lawful in Ukraine; weighted as a risk indicator, not as a violation",
            },
        )


@dataclass
class AwardRatioNoDiscount:
    """Advisory: the winner conceded nothing against the expected value."""

    id: str = "award_ratio_no_discount"

    def applies(self, ctx: RuleContext) -> str | None:
        return _award_ratio_applies(ctx)

    def check(self, ctx: RuleContext) -> Finding | None:
        ratio, award = _award_ratio(ctx)
        bound = float(ctx.config(self.id).get("ratio_at_or_above", 0.98))
        if ratio < bound:
            return None
        return ctx.finding(
            self.id,
            observed=round(ratio, 4),
            threshold=bound,
            evidence={
                "expected_value": ctx.tender.amount,
                "award_value": award.amount,
                "note": (
                    "a ratio near 1.0 is ambiguous: it can mean a thin market or "
                    "coordinated bidding, so it is advisory and never blocking"
                ),
            },
        )


@dataclass
class AwardRatioLowball:
    """Advisory: an implausible discount, often followed by amendments."""

    id: str = "award_ratio_lowball"

    def applies(self, ctx: RuleContext) -> str | None:
        return _award_ratio_applies(ctx)

    def check(self, ctx: RuleContext) -> Finding | None:
        ratio, award = _award_ratio(ctx)
        bound = float(ctx.config(self.id).get("ratio_below", 0.70))
        if ratio >= bound:
            return None
        return ctx.finding(
            self.id,
            observed=round(ratio, 4),
            threshold=bound,
            evidence={"expected_value": ctx.tender.amount, "award_value": award.amount},
        )


def _award_ratio_applies(ctx: RuleContext) -> str | None:
    tender = ctx.tender
    award = tender.active_award
    if tender.is_direct_award:
        # A direct contract is signed at its own stated value, so the award
        # always equals the expectation. Measuring "no discount" there would
        # flag every direct contract and mean nothing.
        return f"procedure {tender.procedure_type!r} awards directly, so there is no competitive discount to measure"
    if award is None:
        return "no active award, so there is no award price to compare"
    if not tender.amount:
        return "tender has no expected value to compare against"
    if award.amount is None:
        return "award carries no value"
    if award.currency and tender.currency and award.currency != tender.currency:
        return f"award currency {award.currency} differs from tender currency {tender.currency}"
    return None


def _award_ratio(ctx: RuleContext):
    award = ctx.tender.active_award
    return award.amount / ctx.tender.amount, award


@dataclass
class BidTimingCompressed:
    """Advisory: every bid landed in the final minutes, a coordination signal."""

    id: str = "bid_timing_compressed"

    def applies(self, ctx: RuleContext) -> str | None:
        tender = ctx.tender
        cfg = ctx.config(self.id)
        minimum = int(cfg.get("minimum_bids", 2))
        timed = [b for b in tender.bids if b.submitted_at is not None]
        if not tender.is_competitive_procedure:
            return f"procedure {tender.procedure_type!r} does not collect competing bids"
        if len(timed) < minimum:
            return f"fewer than {minimum} bids carry a submission time"
        if tender.tender_period.end is None:
            return "tender period has no deadline to measure against"
        return None

    def check(self, ctx: RuleContext) -> Finding | None:
        tender = ctx.tender
        window = timedelta(minutes=float(ctx.config(self.id).get("window_minutes", 15)))
        deadline = tender.tender_period.end
        times = sorted(b.submitted_at for b in tender.bids if b.submitted_at is not None)

        if any(deadline - t > window for t in times):
            return None

        spread = (times[-1] - times[0]).total_seconds() / 60.0
        return ctx.finding(
            self.id,
            observed=round(spread, 2),
            threshold=window.total_seconds() / 60.0,
            evidence={
                "deadline": deadline.isoformat(),
                "submissions": [t.isoformat() for t in times],
                "bids_considered": len(times),
            },
        )


@dataclass
class LosingBidderInSubcontracting:
    """Advisory: a losing bidder appears in the winner's subcontracting text."""

    id: str = "losing_bidder_in_subcontracting"

    def applies(self, ctx: RuleContext) -> str | None:
        tender = ctx.tender
        award = tender.active_award
        if award is None:
            return "no active award, so there is no winner whose subcontracting could be read"
        if not any(b.subcontracting_details for b in tender.bids):
            return "no bid declares subcontracting details"
        if len(tender.bids) < 2:
            return "fewer than two bids, so there is no losing bidder to match"
        return None

    def check(self, ctx: RuleContext) -> Finding | None:
        tender = ctx.tender
        award = tender.active_award
        winner_bid_ids = {award.bid_id} if award.bid_id else set()
        winner_edrpous = award.supplier_edrpous

        winning_text: list[str] = []
        losers: list[tuple[str, str]] = []
        for bid in tender.bids:
            is_winner = bid.identifier in winner_bid_ids or bool(bid.tenderer_edrpous & winner_edrpous)
            if is_winner:
                winning_text.extend(bid.subcontracting_details)
            else:
                for party in bid.tenderers:
                    if party.name:
                        losers.append((party.edrpou or "", party.name))

        if not winning_text or not losers:
            return None

        haystack = " \n".join(winning_text).casefold()
        matches = [
            {"edrpou": edrpou, "name": name}
            for edrpou, name in losers
            if _name_appears(name, haystack)
        ]
        if not matches:
            return None

        return ctx.finding(
            self.id,
            observed=len(matches),
            threshold=1,
            evidence={
                "matched_losing_bidders": matches,
                "subcontracting_text": winning_text,
                "note": (
                    "subcontracting details are free text in this API, so this is a "
                    "string match and is suggestive rather than proof"
                ),
            },
        )


def _name_appears(name: str, haystack: str) -> bool:
    """Match on the distinctive part of a company name, not on legal-form noise."""
    cleaned = re.sub(r"[«»\"'`]", " ", name).casefold()
    tokens = [t for t in re.split(r"[\s,\.]+", cleaned) if len(t) > 4]
    tokens = [t for t in tokens if t not in {"товариство", "приватне", "обмеженою", "відповідальністю", "підприємство"}]
    return any(token in haystack for token in tokens)


@dataclass
class SupplierNewToDataset:
    """Advisory: winner has almost no history here before this notice.

    Explicitly a dataset-horizon proxy. The API does not publish company
    registration dates, so this cannot and does not claim the company is new.
    """

    id: str = "supplier_new_to_dataset"

    def applies(self, ctx: RuleContext) -> str | None:
        if ctx.store is None:
            return "no dataset available to establish a supplier horizon"
        award = ctx.tender.active_award
        if award is None or not award.supplier_edrpous:
            return "no identified winning supplier"
        if ctx.tender.published_at is None:
            return "no publication date to measure against"

        # The proxy is only meaningful when the dataset reaches far enough back
        # to tell "new supplier" apart from "supplier that predates the window".
        limit = int(ctx.config(self.id).get("days_before_notice", 90))
        earliest = getattr(ctx.store.data_window(), "earliest_publication", None)
        if earliest:
            from datetime import datetime as _dt

            horizon_days = (ctx.tender.published_at - _dt.fromisoformat(earliest)).total_seconds() / 86400.0
            if horizon_days < limit:
                return (
                    f"the dataset reaches back only {horizon_days:.0f} days before this notice, "
                    f"which is less than the {limit}-day novelty window, so absence of earlier "
                    f"awards would say nothing about the supplier"
                )
        return None

    def check(self, ctx: RuleContext) -> Finding | None:
        tender = ctx.tender
        award = tender.active_award
        limit = int(ctx.config(self.id).get("days_before_notice", 90))

        newest: list[dict] = []
        for edrpou in sorted(award.supplier_edrpous):
            first_seen = ctx.store.supplier_first_seen(edrpou)
            if first_seen is None:
                continue
            gap_days = (tender.published_at - first_seen).total_seconds() / 86400.0
            if gap_days <= limit:
                newest.append(
                    {
                        "edrpou": edrpou,
                        "first_seen_in_dataset": first_seen.isoformat(),
                        "days_before_notice": round(gap_days, 1),
                    }
                )

        if not newest:
            return None

        return ctx.finding(
            self.id,
            observed=min(s["days_before_notice"] for s in newest),
            threshold=limit,
            evidence={
                "suppliers": newest,
                "note": (
                    "dataset-horizon proxy: the earliest appearance of this supplier in "
                    "the prepared dataset, NOT a company registration date, which this "
                    "API does not publish"
                ),
            },
        )


@dataclass
class BrandWithoutEquivalence:
    """Advisory: a make or model named without the required equivalence wording."""

    id: str = "brand_without_equivalence"

    def applies(self, ctx: RuleContext) -> str | None:
        if not _text_blobs(ctx.tender):
            return "tender carries no title, description or item text to scan"
        return None

    def check(self, ctx: RuleContext) -> Finding | None:
        markers = [m.casefold() for m in ctx.config(self.id).get("equivalence_markers", [])]
        blobs = _text_blobs(ctx.tender)
        joined = " \n".join(blobs).casefold()

        if any(marker in joined for marker in markers):
            return None

        hits: list[str] = []
        for blob in blobs:
            for token in BRAND_TOKEN.findall(blob):
                # "ISO 1452" is a standard, not a make: judge the alphabetic
                # head of the token, not the whole string with its number.
                head = re.match(r"[A-Za-z]+", token)
                if head and head.group(0).upper() in BRAND_STOPWORDS:
                    continue
                if len(token) < 3:
                    continue
                hits.append(token)
        hits = list(dict.fromkeys(hits))
        if not hits:
            return None

        return ctx.finding(
            self.id,
            observed=hits[:10],
            threshold="equivalence wording present",
            evidence={
                "matched_tokens": hits[:10],
                "note": (
                    "lexical scan of title, description and item text; attached "
                    "specification documents are not parsed"
                ),
            },
        )


@dataclass
class CancelledAfterAward:
    """Advisory: cancelling once a winner is known is the suspicious variant."""

    id: str = "cancelled_after_award"

    def applies(self, ctx: RuleContext) -> str | None:
        if not ctx.tender.cancellations:
            return "tender was not cancelled"
        if not any(c.is_effective for c in ctx.tender.cancellations):
            return "no effective cancellation; only drafts or withdrawn cancellations"
        return None

    def check(self, ctx: RuleContext) -> Finding | None:
        tender = ctx.tender
        cancellations = [c for c in tender.cancellations if c.is_effective]
        award_dates = [a.date for a in tender.awards if a.date is not None]
        if not award_dates:
            return None

        earliest_award = min(award_dates)
        after = [c for c in cancellations if c.date and c.date >= earliest_award]
        if not after:
            return None

        chosen = after[0]
        return ctx.finding(
            self.id,
            observed=chosen.date.isoformat(),
            threshold=earliest_award.isoformat(),
            evidence={
                "cancellation_reason": chosen.reason,
                "cancellation_reason_type": chosen.reason_type,
                "note": (
                    "cancellation for lack of participants is far less remarkable than "
                    "cancellation once a winner is known; the reason is quoted so the "
                    "reviewer can tell them apart"
                ),
            },
        )


@dataclass
class CancelAndReissue:
    """Advisory: a cancelled tender reappears with a similar scope and value."""

    id: str = "cancel_and_reissue"

    def applies(self, ctx: RuleContext) -> str | None:
        if ctx.store is None:
            return "no dataset available to look for a reissue"
        if not any(c.is_effective for c in ctx.tender.cancellations):
            return "tender was not cancelled"
        if not ctx.tender.buyer.edrpou:
            return "buyer is unidentified, so sibling tenders cannot be found"
        if ctx.tender.published_at is None:
            return "no publication date to measure the reissue interval from"
        return None

    def check(self, ctx: RuleContext) -> Finding | None:
        tender = ctx.tender
        cfg = ctx.config(self.id)
        horizon = timedelta(days=float(cfg.get("reissue_within_days", 60)))
        band = float(cfg.get("value_band", 0.25))
        groups = tender.cpv_groups

        candidates = []
        for other in ctx.store.for_buyer(tender.buyer.edrpou):
            if other.uuid == tender.uuid or other.published_at is None:
                continue
            if not (0 < (other.published_at - tender.published_at).total_seconds() <= horizon.total_seconds()):
                continue
            if groups and not (groups & other.cpv_groups):
                continue
            if tender.amount and other.amount:
                spread = abs(other.amount - tender.amount) / tender.amount
                if spread > band:
                    continue
            else:
                spread = None
            candidates.append(
                {
                    "tender_id": other.tender_id,
                    "published_at": other.published_at.isoformat(),
                    "value": other.amount,
                    "value_difference_ratio": round(spread, 4) if spread is not None else None,
                    "shared_cpv_groups": sorted(groups & other.cpv_groups),
                }
            )

        if not candidates:
            return None

        return ctx.finding(
            self.id,
            observed=len(candidates),
            threshold=1,
            evidence={
                "reissue_candidates": candidates[:5],
                "window_days": horizon.days,
                "value_band": band,
            },
        )


@dataclass
class SupplierWinStreak:
    """Advisory: the winner holds a run of consecutive awards from this buyer.

    Resolved server-side for *this tender's* winner. It is never accepted as an
    argument: a value the model carried between two tool calls could not be
    verified, and this one contributes to the score.
    """

    id: str = "supplier_win_streak"

    def applies(self, ctx: RuleContext) -> str | None:
        if ctx.store is None:
            return "no dataset available to compute award history"
        award = ctx.tender.active_award
        if award is None or not award.supplier_edrpous:
            return "no identified winning supplier"
        if not ctx.tender.buyer.edrpou:
            return "buyer is unidentified, so its award history cannot be assembled"
        return None

    def check(self, ctx: RuleContext) -> Finding | None:
        from ..analysis import award_sequence, longest_streak

        tender = ctx.tender
        minimum = int(ctx.config(self.id).get("minimum_streak", 3))
        winners = tender.active_award.supplier_edrpous

        sequence = award_sequence(ctx.store.for_buyer(tender.buyer.edrpou))
        best = None
        for edrpou in sorted(winners):
            streak = longest_streak(sequence, edrpou, until=tender.published_at)
            if best is None or streak["length"] > best["length"]:
                best = {**streak, "edrpou": edrpou}

        if best is None or best["length"] < minimum:
            return None

        return ctx.finding(
            self.id,
            observed=best["length"],
            threshold=minimum,
            evidence={
                "supplier_edrpou": best["edrpou"],
                "streak_tender_ids": best["tender_ids"],
                "cpv_groups_spanned": best["cpv_groups"],
                "buyer_edrpou": tender.buyer.edrpou,
            },
        )


ALL_RULES = [
    BidWindowBelowMinimum(),
    ProcedureThresholdMismatch(),
    EffectiveSingleParticipation(),
    AwardRatioNoDiscount(),
    AwardRatioLowball(),
    BidTimingCompressed(),
    LosingBidderInSubcontracting(),
    SupplierNewToDataset(),
    BrandWithoutEquivalence(),
    CancelledAfterAward(),
    CancelAndReissue(),
    SupplierWinStreak(),
]
