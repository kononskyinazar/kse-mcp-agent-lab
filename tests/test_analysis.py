"""Concentration, trend and streak arithmetic."""

from datetime import timedelta

from procurement_mcp.analysis import (
    award_sequence,
    best_streak,
    concentration,
    herfindahl,
    longest_streak,
    monthly_trend,
)
from procurement_mcp.normalize import normalize_tender

from factories import BASE, award, tender_doc


def tender(uuid, supplier, amount, offset_days, cpv="42122000-0"):
    published = BASE + timedelta(days=offset_days)
    return normalize_tender(
        tender_doc(
            uuid=uuid,
            tender_id=f"UA-{uuid}",
            published=published,
            amount=amount,
            items=[{"description": "x", "classification": {"scheme": "ДК021", "id": cpv}}],
            awards=[award(edrpou=supplier, amount=amount, date=published + timedelta(days=5))],
        )
    )


def test_hhi_is_one_for_a_single_supplier():
    assert herfindahl([1.0]) == 1.0


def test_hhi_falls_as_suppliers_multiply():
    assert herfindahl([0.5, 0.5]) == 0.5
    assert herfindahl([0.25] * 4) == 0.25


def test_multi_supplier_award_splits_the_value_once():
    document = tender_doc(
        awards=[
            {
                "id": "a-1",
                "status": "active",
                "date": "2026-06-20T10:00:00+03:00",
                "value": {"amount": 100.0, "currency": "UAH"},
                "suppliers": [
                    {"identifier": {"id": "111", "legalName": "A"}},
                    {"identifier": {"id": "222", "legalName": "B"}},
                ],
            }
        ]
    )
    events = award_sequence([normalize_tender(document)])

    assert [e.amount for e in events] == [50.0, 50.0], "a joint award must not be counted twice over"


def test_concentration_reports_value_and_count_separately():
    tenders = [
        tender("t1", "111", 900_000, 0),
        tender("t2", "222", 50_000, 10),
        tender("t3", "333", 50_000, 20),
    ]
    result = concentration(award_sequence(tenders))

    assert result["distinct_suppliers"] == 3
    assert result["by_value"]["top_1_share"] == 0.9
    assert result["by_count"]["top_1_share"] == round(1 / 3, 4)


def test_top_three_share_exposes_a_tail_hhi_hides():
    tenders = [tender(f"t{i}", str(100 + i), 100.0, i * 5) for i in range(6)]
    result = concentration(award_sequence(tenders))

    assert result["by_count"]["top_3_share"] == 0.5
    assert result["by_value"]["hhi"] < 0.2


def test_empty_history_does_not_divide_by_zero():
    result = concentration([])

    assert result["awards_counted"] == 0
    assert result["by_value"]["top_1_share"] is None


def test_trend_reports_direction_and_bucket_count():
    tenders = [
        tender("t1", "111", 100.0, 0),
        tender("t2", "222", 100.0, 2),
        tender("t3", "111", 100.0, 40),
        tender("t4", "111", 100.0, 42),
    ]
    trend = monthly_trend(award_sequence(tenders))

    assert trend["periods_analyzed"] == 2
    assert trend["direction"] == "increasing"
    assert "hint" in trend["note"], "two buckets must not be presented as a trend"


def test_trend_with_one_bucket_says_insufficient_rather_than_stable():
    trend = monthly_trend(award_sequence([tender("t1", "111", 100.0, 0)]))

    assert trend["direction"] == "insufficient_data"
    assert trend["magnitude"] is None


def test_trailing_streak_counts_only_the_run_ending_at_the_cutoff():
    tenders = [
        tender("t1", "111", 100.0, 0),
        tender("t2", "222", 100.0, 5),
        tender("t3", "111", 100.0, 10),
        tender("t4", "111", 100.0, 15),
    ]
    events = award_sequence(tenders)
    streak = longest_streak(events, "111", until=BASE + timedelta(days=15))

    assert streak["length"] == 2, "the run broken by supplier 222 must not be counted"
    assert streak["tender_ids"] == ["UA-t3", "UA-t4"]


def test_streak_is_zero_when_the_last_award_went_elsewhere():
    events = award_sequence([tender("t1", "111", 100.0, 0), tender("t2", "222", 100.0, 5)])

    assert longest_streak(events, "111")["length"] == 0


def test_best_streak_finds_the_longest_run_anywhere():
    tenders = [
        tender("t1", "111", 100.0, 0),
        tender("t2", "111", 100.0, 2),
        tender("t3", "111", 100.0, 4),
        tender("t4", "222", 100.0, 6),
    ]
    best = best_streak(award_sequence(tenders))

    assert best["length"] == 3
    assert best["edrpou"] == "111"


def test_streak_records_the_cpv_groups_it_spans():
    tenders = [
        tender("t1", "111", 100.0, 0, cpv="42122000-0"),
        tender("t2", "111", 100.0, 5, cpv="33600000-6"),
    ]
    streak = longest_streak(award_sequence(tenders), "111")

    assert streak["cpv_groups"] == ["3360", "4212"], "a streak across unrelated categories is the point"


def joint_tender(uuid, suppliers, amount, offset_days):
    from datetime import timedelta

    from factories import tender_doc

    published = BASE + timedelta(days=offset_days)
    return normalize_tender(
        tender_doc(
            uuid=uuid,
            tender_id=f"UA-{uuid}",
            published=published,
            amount=amount,
            awards=[
                {
                    "id": f"a-{uuid}",
                    "status": "active",
                    "date": (published + timedelta(days=5)).isoformat(),
                    "value": {"amount": amount, "currency": "UAH"},
                    "suppliers": [
                        {"identifier": {"id": s, "legalName": f"Supplier {s}"}} for s in suppliers
                    ],
                }
            ],
        )
    )


def test_a_joint_award_does_not_break_a_streak():
    """A consortium winning repeatedly is the pattern the rule exists to catch."""
    tenders = [joint_tender(f"j{i}", ["111", "222"], 100.0, i * 5) for i in range(4)]
    events = award_sequence(tenders)

    assert longest_streak(events, "111")["length"] == 4
    assert best_streak(events)["length"] == 4


def test_a_streak_still_breaks_when_the_supplier_drops_out():
    tenders = [
        joint_tender("j1", ["111", "222"], 100.0, 0),
        joint_tender("j2", ["111"], 100.0, 5),
        joint_tender("j3", ["333"], 100.0, 10),
        joint_tender("j4", ["111"], 100.0, 15),
    ]
    events = award_sequence(tenders)

    assert longest_streak(events, "111")["length"] == 1, "the run ended at the tender won by 333"
    assert best_streak(events)["length"] == 2


def test_trend_direction_sees_a_spike_in_the_middle():
    """First-versus-last would call this stable and hide two months of capture."""
    tenders = [
        tender("t1", "111", 100.0, 0),
        tender("t2", "222", 100.0, 1),
        tender("t3", "333", 100.0, 2),
        tender("t4", "111", 500.0, 32),
        tender("t5", "111", 500.0, 62),
        tender("t6", "111", 100.0, 92),
        tender("t7", "222", 100.0, 93),
        tender("t8", "333", 100.0, 94),
    ]
    trend = monthly_trend(award_sequence(tenders))

    assert trend["periods_analyzed"] == 4
    assert trend["magnitude"] == 0.0, "the endpoints really are identical"
    assert trend["slope_per_month"] is not None


def test_trend_reports_months_with_no_awards_rather_than_hiding_them():
    tenders = [tender("t1", "111", 100.0, 0), tender("t2", "222", 100.0, 120)]
    trend = monthly_trend(award_sequence(tenders))

    # June and September, so four calendar months with July and August empty.
    assert trend["periods_analyzed"] == 2
    assert trend["months_spanned"] == 4
    assert trend["months_without_awards"] == ["2026-07", "2026-08"]


def test_undated_awards_do_not_crash_the_sort():
    """datetime.min.astimezone() raised OverflowError on some platforms."""
    from factories import tender_doc

    undated = normalize_tender(
        tender_doc(uuid="undated1", tender_id="UA-undated", published=None,
                   awards=[award(edrpou="111", amount=10.0)])
    )
    events = award_sequence([undated, tender("t1", "222", 100.0, 0)])

    assert len(events) == 2
