"""Every rule: when it fires, when it does not, and when it must not apply."""

from datetime import timedelta

import pytest

from procurement_mcp.tools import screen as screen_tool

from factories import BASE, award, bid, cancellation, tender_doc
from support import make_config, make_store


def run_screen(tmp_path, document, others=(), **kwargs):
    store = make_store(tmp_path, [document, *others])
    config = make_config(tmp_path)
    return screen_tool.run(config, store, {"tender_identifier": document["id"], **kwargs})


def fired(result) -> set[str]:
    return {f["rule_id"] for f in [*result["blocking_violations"], *result["advisories"]]}


def skipped(result) -> dict[str, str]:
    return {s["rule_id"]: s["reason"] for s in result["rules_not_applicable"]}


# --- bid window ------------------------------------------------------------

def test_short_bid_window_is_a_blocking_violation(tmp_path):
    document = tender_doc(tender_end=BASE + timedelta(days=3))
    result = run_screen(tmp_path, document)

    assert "bid_window_below_statutory_minimum" in {f["rule_id"] for f in result["blocking_violations"]}
    assert result["has_blocking"] is True


def test_bid_window_uses_the_minimum_in_force_at_publication(tmp_path):
    """Works had a 7-day minimum before April 2024 and 14 days after."""
    old = tender_doc(
        uuid="old", tender_id="UA-old", category="works",
        published=BASE.replace(year=2023), tender_end=BASE.replace(year=2023) + timedelta(days=9),
    )
    new = tender_doc(
        uuid="new", tender_id="UA-new", category="works",
        published=BASE, tender_end=BASE + timedelta(days=9),
    )

    assert "bid_window_below_statutory_minimum" not in fired(run_screen(tmp_path / "a", old))
    assert "bid_window_below_statutory_minimum" in fired(run_screen(tmp_path / "b", new))


def test_bid_window_rule_does_not_apply_to_direct_awards(tmp_path):
    document = tender_doc(procedure="reporting", tender_end=BASE + timedelta(hours=1))
    result = run_screen(tmp_path, document)

    reason = skipped(result)["bid_window_below_statutory_minimum"]
    assert "does not invite competing bids" in reason


# --- procedure threshold ---------------------------------------------------

def test_direct_award_above_the_threshold_is_blocking(tmp_path):
    document = tender_doc(procedure="reporting", amount=250_000.0, awards=[award()])
    result = run_screen(tmp_path, document)

    assert "procedure_value_threshold_mismatch" in {f["rule_id"] for f in result["blocking_violations"]}


def test_direct_award_below_the_threshold_is_clean(tmp_path):
    document = tender_doc(procedure="reporting", amount=40_000.0, awards=[award(amount=40_000.0)])

    assert "procedure_value_threshold_mismatch" not in fired(run_screen(tmp_path, document))


def test_framework_selection_is_exempt_from_the_value_threshold(tmp_path):
    document = tender_doc(procedure="closeFrameworkAgreementSelectionUA", amount=5_000_000.0)
    result = run_screen(tmp_path, document)

    assert "governed by the agreement" in skipped(result)["procedure_value_threshold_mismatch"]


# --- participation ---------------------------------------------------------

def test_single_participation_is_an_advisory_never_a_blocking_violation(tmp_path):
    document = tender_doc(bids=[bid()], awards=[award()])
    result = run_screen(tmp_path, document)

    advisory_ids = {f["rule_id"] for f in result["advisories"]}
    blocking_ids = {f["rule_id"] for f in result["blocking_violations"]}
    assert "effective_single_participation" in advisory_ids
    assert "effective_single_participation" not in blocking_ids, "single bidding is lawful in Ukraine"


def test_two_bidders_do_not_trip_the_participation_rule(tmp_path):
    document = tender_doc(
        bids=[bid(identifier="b-1", edrpou="40000001"), bid(identifier="b-2", edrpou="40000002")],
        awards=[award()],
    )

    assert "effective_single_participation" not in fired(run_screen(tmp_path, document))


def test_withdrawn_bids_do_not_count_as_participation(tmp_path):
    document = tender_doc(
        bids=[bid(identifier="b-1"), bid(identifier="b-2", edrpou="40000002", status="invalid")],
        awards=[award()],
    )

    assert "effective_single_participation" in fired(run_screen(tmp_path, document))


def test_reporting_procedure_never_fires_the_participation_rule(tmp_path):
    document = tender_doc(procedure="reporting", amount=10_000.0, awards=[award(amount=10_000.0)])
    result = run_screen(tmp_path, document)

    assert "awards directly" in skipped(result)["effective_single_participation"]


# --- award ratio -----------------------------------------------------------

def test_no_discount_ratio_fires(tmp_path):
    document = tender_doc(amount=100_000.0, bids=[bid()], awards=[award(amount=99_500.0)])

    assert "award_ratio_no_discount" in fired(run_screen(tmp_path, document))


def test_lowball_ratio_fires_separately(tmp_path):
    document = tender_doc(amount=100_000.0, bids=[bid()], awards=[award(amount=50_000.0)])
    result = run_screen(tmp_path, document)

    assert "award_ratio_lowball" in fired(result)
    assert "award_ratio_no_discount" not in fired(result)


def test_ordinary_discount_fires_neither_ratio_rule(tmp_path):
    document = tender_doc(amount=100_000.0, bids=[bid()], awards=[award(amount=85_000.0)])
    result = fired(run_screen(tmp_path, document))

    assert "award_ratio_lowball" not in result
    assert "award_ratio_no_discount" not in result


def test_mismatched_currencies_skip_the_ratio_rather_than_compare_them(tmp_path):
    document = tender_doc(amount=100_000.0, currency="UAH", awards=[award(amount=2_000.0, currency="EUR")])
    result = run_screen(tmp_path, document)

    assert "currency" in skipped(result)["award_ratio_no_discount"]


# --- bid timing ------------------------------------------------------------

def test_bids_bunched_at_the_deadline_fire_the_timing_rule(tmp_path):
    deadline = BASE + timedelta(days=20)
    document = tender_doc(
        tender_end=deadline,
        bids=[
            bid(identifier="b-1", edrpou="40000001", submitted=deadline - timedelta(minutes=5)),
            bid(identifier="b-2", edrpou="40000002", submitted=deadline - timedelta(minutes=2)),
        ],
        awards=[award()],
    )

    assert "bid_timing_compressed" in fired(run_screen(tmp_path, document))


def test_spread_out_bids_do_not_fire(tmp_path):
    deadline = BASE + timedelta(days=20)
    document = tender_doc(
        tender_end=deadline,
        bids=[
            bid(identifier="b-1", edrpou="40000001", submitted=deadline - timedelta(days=4)),
            bid(identifier="b-2", edrpou="40000002", submitted=deadline - timedelta(minutes=2)),
        ],
        awards=[award()],
    )

    assert "bid_timing_compressed" not in fired(run_screen(tmp_path, document))


def test_timing_rule_needs_at_least_two_timed_bids(tmp_path):
    deadline = BASE + timedelta(days=20)
    document = tender_doc(
        tender_end=deadline,
        bids=[bid(submitted=deadline - timedelta(minutes=1))],
        awards=[award()],
    )
    result = run_screen(tmp_path, document)

    assert "fewer than 2 bids" in skipped(result)["bid_timing_compressed"]


# --- subcontracting --------------------------------------------------------

def test_losing_bidder_named_in_subcontracting_fires(tmp_path):
    document = tender_doc(
        bids=[
            bid(identifier="b-1", edrpou="40000001", name="ТОВ Альфаспецбуд",
                subcontracting="Субпідрядник: ТОВ Бетаінжиніринг, 30% робіт"),
            bid(identifier="b-2", edrpou="40000002", name="ТОВ Бетаінжиніринг"),
        ],
        awards=[award(bid_id="b-1", edrpou="40000001", name="ТОВ Альфаспецбуд")],
    )
    result = run_screen(tmp_path, document)

    assert "losing_bidder_in_subcontracting" in fired(result)
    finding = next(f for f in result["advisories"] if f["rule_id"] == "losing_bidder_in_subcontracting")
    assert finding["evidence"]["matched_losing_bidders"][0]["edrpou"] == "40000002"
    assert "string match" in finding["evidence"]["note"], "must not present a text match as proof"


def test_unrelated_subcontractor_does_not_fire(tmp_path):
    document = tender_doc(
        bids=[
            bid(identifier="b-1", edrpou="40000001", name="ТОВ Альфаспецбуд",
                subcontracting="Субпідрядник: ТОВ Гаммасервіс"),
            bid(identifier="b-2", edrpou="40000002", name="ТОВ Бетаінжиніринг"),
        ],
        awards=[award(bid_id="b-1")],
    )

    assert "losing_bidder_in_subcontracting" not in fired(run_screen(tmp_path, document))


# --- supplier horizon ------------------------------------------------------

def test_supplier_new_to_the_dataset_is_flagged_as_a_proxy(tmp_path):
    document = tender_doc(bids=[bid()], awards=[award(edrpou="49999999")])
    result = run_screen(tmp_path, document)

    finding = next(f for f in result["advisories"] if f["rule_id"] == "supplier_new_to_dataset")
    assert "NOT a company registration date" in finding["evidence"]["note"]


def test_long_standing_supplier_is_not_flagged(tmp_path):
    old = tender_doc(
        uuid="old", tender_id="UA-old", published=BASE - timedelta(days=300),
        awards=[award(edrpou="40000001", date=BASE - timedelta(days=290))],
    )
    current = tender_doc(uuid="cur", tender_id="UA-cur", awards=[award(edrpou="40000001")])

    assert "supplier_new_to_dataset" not in fired(run_screen(tmp_path, current, others=[old]))


# --- specification tailoring ----------------------------------------------

def test_brand_without_equivalence_wording_fires(tmp_path):
    document = tender_doc(
        title="Закупівля насосів Grundfos CR-15",
        items=[{"description": "Насос Grundfos CR-15", "classification": {"scheme": "ДК021", "id": "42122000-0"}}],
    )

    assert "brand_without_equivalence" in fired(run_screen(tmp_path, document))


def test_equivalence_wording_clears_the_rule(tmp_path):
    document = tender_doc(
        title="Закупівля насосів Grundfos CR-15 або еквівалент",
        items=[{"description": "Насос Grundfos CR-15 або еквівалент", "classification": {"scheme": "ДК021", "id": "42122000-0"}}],
    )

    assert "brand_without_equivalence" not in fired(run_screen(tmp_path, document))


def test_standards_are_not_mistaken_for_brands(tmp_path):
    document = tender_doc(
        title="Труби ПВХ згідно ISO 1452 та DSTU EN 1401",
        items=[{"description": "Труби за ISO 1452", "classification": {"scheme": "ДК021", "id": "44160000-9"}}],
    )

    assert "brand_without_equivalence" not in fired(run_screen(tmp_path, document))


# --- cancellations ---------------------------------------------------------

def test_cancellation_after_award_fires(tmp_path):
    document = tender_doc(
        status="cancelled",
        awards=[award(date=BASE + timedelta(days=25))],
        cancellations=[cancellation(date=BASE + timedelta(days=27))],
    )

    assert "cancelled_after_award" in fired(run_screen(tmp_path, document))


def test_cancellation_before_any_award_does_not_fire(tmp_path):
    document = tender_doc(
        status="cancelled",
        awards=[award(date=BASE + timedelta(days=25))],
        cancellations=[cancellation(date=BASE + timedelta(days=10))],
    )

    assert "cancelled_after_award" not in fired(run_screen(tmp_path, document))


def test_draft_cancellation_is_not_treated_as_a_cancellation(tmp_path):
    document = tender_doc(cancellations=[cancellation(status="pending")], awards=[award()])
    result = run_screen(tmp_path, document)

    assert "no effective cancellation" in skipped(result)["cancelled_after_award"]


def test_cancel_and_reissue_finds_the_sibling_tender(tmp_path):
    cancelled = tender_doc(
        uuid="c1", tender_id="UA-c1", amount=500_000.0, status="cancelled",
        cancellations=[cancellation(date=BASE + timedelta(days=5))],
    )
    reissued = tender_doc(
        uuid="c2", tender_id="UA-c2", amount=520_000.0, published=BASE + timedelta(days=20),
    )
    result = run_screen(tmp_path, cancelled, others=[reissued])

    finding = next(f for f in result["advisories"] if f["rule_id"] == "cancel_and_reissue")
    assert finding["evidence"]["reissue_candidates"][0]["tender_id"] == "UA-c2"


def test_reissue_with_an_unrelated_scope_is_not_matched(tmp_path):
    cancelled = tender_doc(
        uuid="c1", tender_id="UA-c1", amount=500_000.0, status="cancelled",
        cancellations=[cancellation(date=BASE + timedelta(days=5))],
    )
    unrelated = tender_doc(
        uuid="c2", tender_id="UA-c2", amount=505_000.0, published=BASE + timedelta(days=20),
        items=[{"description": "Медикаменти", "classification": {"scheme": "ДК021", "id": "33600000-6"}}],
    )

    assert "cancel_and_reissue" not in fired(run_screen(tmp_path, cancelled, others=[unrelated]))


# --- streak ----------------------------------------------------------------

def test_win_streak_is_resolved_for_this_tenders_winner(tmp_path):
    history = [
        tender_doc(
            uuid=f"h{i}", tender_id=f"UA-h{i}", published=BASE - timedelta(days=40 - i * 10),
            awards=[award(edrpou="40000001", date=BASE - timedelta(days=38 - i * 10))],
        )
        for i in range(3)
    ]
    current = tender_doc(uuid="cur", tender_id="UA-cur", awards=[award(edrpou="40000001")])
    result = run_screen(tmp_path, current, others=history)

    finding = next(f for f in result["advisories"] if f["rule_id"] == "supplier_win_streak")
    assert finding["evidence"]["supplier_edrpou"] == "40000001"
    assert finding["observed_value"] >= 3


def test_streak_is_not_inherited_from_another_supplier(tmp_path):
    history = [
        tender_doc(
            uuid=f"h{i}", tender_id=f"UA-h{i}", published=BASE - timedelta(days=40 - i * 10),
            awards=[award(edrpou="40000001", date=BASE - timedelta(days=38 - i * 10))],
        )
        for i in range(3)
    ]
    current = tender_doc(uuid="cur", tender_id="UA-cur", awards=[award(edrpou="47777777")])

    assert "supplier_win_streak" not in fired(run_screen(tmp_path, current, others=history))


def test_streak_cannot_be_supplied_by_the_caller(tmp_path):
    document = tender_doc(awards=[award()])
    store = make_store(tmp_path, [document])
    config = make_config(tmp_path)

    with pytest.raises(Exception) as excinfo:
        screen_tool.run(config, store, {"tender_identifier": "UA-2026-06-01-000001-a", "streak_flag": 9})

    assert "streak_flag" in str(excinfo.value) or "additionalProperties" in str(excinfo.value)
