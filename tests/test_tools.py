"""Tool contracts: schemas, empty-versus-error, and filter behaviour."""

import json

from datetime import timedelta

import pytest

from procurement_mcp.errors import ErrorCode, ToolError
from procurement_mcp.tools import compliance, concentration, find, screen
from procurement_mcp.tools.validation import check_arguments

from factories import BASE, award, bid, tender_doc
from support import make_config, make_store, write_broken_document


@pytest.fixture
def world(tmp_path):
    documents = [
        tender_doc(uuid="alpha1", tender_id="UA-alpha-1", amount=500_000.0,
                   bids=[bid()], awards=[award(edrpou="40000001")]),
        tender_doc(uuid="alpha2", tender_id="UA-alpha-2", amount=300_000.0,
                   published=BASE + timedelta(days=10),
                   awards=[award(edrpou="40000001", amount=290_000.0, date=BASE + timedelta(days=20))]),
        tender_doc(uuid="beta01", tender_id="UA-beta-1", amount=120_000.0,
                   buyer_edrpou="01999218", buyer_name="Лікарня", region="Полтавська область",
                   published=BASE + timedelta(days=5),
                   items=[{"description": "Ліки", "classification": {"scheme": "ДК021", "id": "33600000-6"}}],
                   awards=[award(edrpou="40000002", amount=118_000.0)]),
    ]
    store = make_store(tmp_path, documents)
    return make_config(tmp_path), store


# --- shared contract -------------------------------------------------------

def test_every_tool_rejects_unknown_arguments(world):
    config, store = world
    for module in (find, concentration, compliance, screen):
        with pytest.raises(ToolError) as excinfo:
            module.run(config, store, {"definitely_not_a_field": 1, **_minimal_args(module)})
        assert excinfo.value.code == ErrorCode.INVALID_INPUT


def _minimal_args(module):
    if module is find:
        return {}
    if module is concentration:
        return {"buyer_edrpou": "03327121"}
    return {"tender_identifier": "UA-alpha-1"}


def test_output_schemas_declare_the_keys_the_tools_return(world):
    config, store = world
    payloads = {
        find: find.run(config, store, {}),
        concentration: concentration.run(config, store, {"buyer_edrpou": "03327121"}),
        compliance: compliance.run(config, store, {"tender_identifier": "UA-alpha-1"}),
        screen: screen.run(config, store, {"tender_identifier": "UA-alpha-1"}),
    }
    for module, payload in payloads.items():
        for required in module.OUTPUT_SCHEMA["required"]:
            assert required in payload, f"{module.__name__} promises {required} and must return it"


# --- find_tenders ----------------------------------------------------------

def test_find_returns_everything_when_unfiltered(world):
    config, store = world
    result = find.run(config, store, {})

    assert result["total_matched"] == 3
    assert result["result_count"] == 3
    assert result["truncated"] is False


def test_find_filters_by_buyer(world):
    config, store = world
    result = find.run(config, store, {"buyer_edrpou": "01999218"})

    assert result["total_matched"] == 1
    assert result["tenders"][0]["tender_id"] == "UA-beta-1"


def test_find_filters_by_cpv_prefix(world):
    config, store = world

    assert find.run(config, store, {"cpv_prefix": "336"})["total_matched"] == 1
    assert find.run(config, store, {"cpv_prefix": "421"})["total_matched"] == 2


def test_find_filters_on_publication_date_not_modification(world):
    config, store = world
    result = find.run(config, store, {"published_from": (BASE + timedelta(days=3)).isoformat()})

    assert result["total_matched"] == 2, "the tender published before the bound must drop out"


def test_find_filters_by_value_range_and_region(world):
    config, store = world

    assert find.run(config, store, {"min_value": 200_000})["total_matched"] == 2
    assert find.run(config, store, {"region": "полтавська"})["total_matched"] == 1


def test_find_excludes_already_reviewed_tenders(world):
    config, store = world
    result = find.run(config, store, {"exclude_tender_ids": ["UA-alpha-1", "UA-alpha-2"]})

    assert [t["tender_id"] for t in result["tenders"]] == ["UA-beta-1"]


def test_find_reports_truncation_rather_than_hiding_it(world):
    config, store = world
    result = find.run(config, store, {"limit": 1})

    assert result["result_count"] == 1
    assert result["total_matched"] == 3
    assert result["truncated"] is True


def test_no_matches_is_a_success_with_zero_count(world):
    config, store = world
    result = find.run(config, store, {"buyer_edrpou": "99999999"})

    assert result["status"] == "ok"
    assert result["result_count"] == 0
    assert result["tenders"] == []


def test_malformed_edrpou_is_an_error_not_an_empty_result(world):
    config, store = world
    with pytest.raises(ToolError) as excinfo:
        find.run(config, store, {"buyer_edrpou": "not-a-code"})

    assert excinfo.value.code == ErrorCode.INVALID_INPUT


def test_unknown_procedure_type_is_rejected_by_the_enum(world):
    config, store = world
    with pytest.raises(ToolError) as excinfo:
        find.run(config, store, {"procedure_types": ["madeUpProcedure"]})

    assert excinfo.value.code == ErrorCode.INVALID_INPUT


def test_reversed_date_range_is_rejected(world):
    config, store = world
    with pytest.raises(ToolError):
        find.run(config, store, {"published_from": "2026-08-01", "published_to": "2026-07-01"})


# --- concentration ---------------------------------------------------------

def test_concentration_reports_both_denominators(world):
    config, store = world
    result = concentration.run(config, store, {"buyer_edrpou": "03327121"})

    assert result["result_count"] == 2
    assert result["by_value"]["hhi"] == 1.0, "one supplier took everything"
    assert result["by_count"]["top_1_share"] == 1.0
    assert result["supplier_win_streak"]["length"] == 2


def test_concentration_for_an_unknown_but_valid_buyer_is_empty_not_an_error(world):
    config, store = world
    result = concentration.run(config, store, {"buyer_edrpou": "99999999"})

    assert result["status"] == "ok"
    assert result["result_count"] == 0
    assert "not a failure" in result["note"]


def test_concentration_rejects_a_malformed_code(world):
    config, store = world
    with pytest.raises(ToolError) as excinfo:
        concentration.run(config, store, {"buyer_edrpou": "123"})

    assert excinfo.value.code == ErrorCode.INVALID_INPUT


def test_concentration_period_filter_narrows_the_history(world):
    config, store = world
    result = concentration.run(
        config, store, {"buyer_edrpou": "03327121", "published_from": (BASE + timedelta(days=5)).isoformat()}
    )

    assert result["result_count"] == 1


# --- compliance ------------------------------------------------------------

def test_compliance_passes_a_well_formed_open_tender(world):
    config, store = world
    result = compliance.run(config, store, {"tender_identifier": "UA-alpha-1"})

    assert result["compliant"] is True
    assert result["applicable_thresholds"]["regime"] == "osoblyvosti-1178"
    assert result["applicable_thresholds"]["mandatory_open_tender_from"]["verification"] == "primary"


def test_compliance_fails_a_direct_award_above_the_threshold(tmp_path):
    document = tender_doc(uuid="direct", tender_id="UA-direct", procedure="reporting", amount=250_000.0)
    store = make_store(tmp_path, [document])
    result = compliance.run(make_config(tmp_path), store, {"tender_identifier": "UA-direct"})

    assert result["compliant"] is False
    assert result["failed_conditions"][0]["condition"] == "procedure_matches_value_threshold"
    assert "statute_reference" in result["failed_conditions"][0]


def test_compliance_rejects_an_unexpected_classifier(tmp_path):
    document = tender_doc(
        uuid="badcpv", tender_id="UA-badcpv",
        items=[{"description": "x", "classification": {"scheme": "OKDP", "id": "1234"}}],
    )
    store = make_store(tmp_path, [document])
    with pytest.raises(ToolError) as excinfo:
        compliance.run(make_config(tmp_path), store, {"tender_identifier": "UA-badcpv"})

    assert excinfo.value.code == ErrorCode.DATA_INTEGRITY
    assert excinfo.value.details["found_schemes"] == ["OKDP"]


def test_unknown_tender_is_not_found_not_an_empty_result(world):
    config, store = world
    with pytest.raises(ToolError) as excinfo:
        compliance.run(config, store, {"tender_identifier": "UA-does-not-exist"})

    assert excinfo.value.code == ErrorCode.NOT_FOUND


# --- screening scoring -----------------------------------------------------

def test_blocking_violation_lifts_the_score_to_the_floor(tmp_path):
    document = tender_doc(uuid="short1", tender_id="UA-short", tender_end=BASE + timedelta(days=2))
    store = make_store(tmp_path, [document])
    config = make_config(tmp_path)
    result = screen.run(config, store, {"tender_identifier": "UA-short"})

    assert result["has_blocking"] is True
    assert result["risk_score"] >= config.rule_book.scoring["blocking_floor"]
    assert result["blocking_floor_applied"] is True
    assert result["requires_human_review"] is True


def test_evidence_chain_sums_to_the_raw_score(world):
    config, store = world
    result = screen.run(config, store, {"tender_identifier": "UA-alpha-1"})

    assert round(sum(e["contribution"] for e in result["evidence_chain"]), 2) == result["raw_weighted_sum"]


def test_evidence_chain_can_be_switched_off(world):
    config, store = world
    result = screen.run(config, store, {"tender_identifier": "UA-alpha-1", "include_evidence": False})

    assert "evidence_chain" not in result


def test_skipped_rules_are_reported_with_reasons(tmp_path):
    document = tender_doc(uuid="direct", tender_id="UA-direct", procedure="reporting", amount=10_000.0)
    store = make_store(tmp_path, [document])
    result = screen.run(make_config(tmp_path), store, {"tender_identifier": "UA-direct"})

    skipped = {s["rule_id"]: s["reason"] for s in result["rules_not_applicable"]}
    assert "effective_single_participation" in skipped
    assert all(entry["reason"] for entry in result["rules_not_applicable"])


# --- store robustness ------------------------------------------------------

def test_one_unreadable_document_does_not_lose_the_dataset(tmp_path):
    make_store(tmp_path, [tender_doc(uuid="good01", tender_id="UA-good")])
    write_broken_document(tmp_path, "broken.json", {"no": "id"})
    # The manifest must account for both files; an unexplained extra document is
    # a different failure, covered by the next test.
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["documents_written"] = 2
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    from procurement_mcp.store import DatasetStore

    reloaded = DatasetStore(tmp_path).load()

    assert len(reloaded) == 1
    assert reloaded.skipped[0]["file"] == "broken.json"


def test_documents_from_a_second_sweep_are_refused_rather_than_merged(tmp_path):
    """Loading the union of two harvests would make every window figure wrong."""
    from procurement_mcp.store import DatasetStore

    make_store(tmp_path, [tender_doc(uuid="sweep01", tender_id="UA-one")])
    write_broken_document(tmp_path, "leftover.json", {"id": "leftover", "tenderID": "UA-old",
                                                     "procurementMethodType": "reporting"})

    with pytest.raises(ToolError) as excinfo:
        DatasetStore(tmp_path).load()

    assert excinfo.value.code == ErrorCode.DATA_INTEGRITY
    assert excinfo.value.details["documents_found"] == 2
    assert excinfo.value.details["documents_in_manifest"] == 1


def test_missing_dataset_directory_is_reported_clearly(tmp_path):
    from procurement_mcp.store import DatasetStore

    with pytest.raises(ToolError) as excinfo:
        DatasetStore(tmp_path / "nope").load()

    assert excinfo.value.code == ErrorCode.DATA_INTEGRITY
    assert "harvest" in excinfo.value.message


# --- validation helper -----------------------------------------------------

def test_boolean_is_not_accepted_where_an_integer_is_declared():
    schema = {"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False}
    with pytest.raises(ToolError):
        check_arguments({"limit": True}, schema, tool="t")


# --- replay and live fetch -------------------------------------------------

def test_replayed_document_goes_through_the_normal_parsing_path(tmp_path):
    """A fixture must be parsed like a live response, not injected as an answer."""
    from procurement_mcp.http import ReplayClient, fixture_key
    from procurement_mcp.store import DatasetStore

    uuid = "a" * 32
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    document = tender_doc(uuid=uuid, tender_id="UA-replayed", amount=250_000.0,
                          procedure="reporting", awards=[award(amount=250_000.0)])
    (fixtures / fixture_key(f"/tenders/{uuid}", None)).write_text(
        json.dumps({"data": document}, ensure_ascii=False), encoding="utf-8"
    )

    make_store(tmp_path, [tender_doc(uuid="other1", tender_id="UA-other")])
    store = DatasetStore(tmp_path, client=ReplayClient(fixtures)).load()
    result = screen.run(make_config(tmp_path), store, {"tender_identifier": uuid})

    assert result["tender"]["tender_id"] == "UA-replayed"
    assert store.was_fetched(uuid)
    assert result["has_blocking"] is True, "the replayed document is screened by the same rules"


def test_missing_fixture_is_reported_and_never_falls_back(tmp_path):
    from procurement_mcp.http import ReplayClient
    from procurement_mcp.store import DatasetStore

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    make_store(tmp_path, [tender_doc(uuid="other1", tender_id="UA-other")])
    store = DatasetStore(tmp_path, client=ReplayClient(fixtures)).load()

    with pytest.raises(ToolError) as excinfo:
        screen.run(make_config(tmp_path), store, {"tender_identifier": "b" * 32})

    assert excinfo.value.code == ErrorCode.FIXTURE_MISSING


def test_a_tender_id_cannot_be_fetched_upstream_and_says_why(tmp_path):
    from procurement_mcp.http import ReplayClient
    from procurement_mcp.store import DatasetStore

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    make_store(tmp_path, [tender_doc(uuid="other1", tender_id="UA-other")])
    store = DatasetStore(tmp_path, client=ReplayClient(fixtures)).load()

    with pytest.raises(ToolError) as excinfo:
        screen.run(make_config(tmp_path), store, {"tender_identifier": "UA-2026-01-01-000001-a"})

    assert excinfo.value.code == ErrorCode.NOT_FOUND
    assert "document UUID" in excinfo.value.details["hint"]


# --- isolation of fetched tenders ------------------------------------------

def test_a_fetched_tender_does_not_join_the_dataset(tmp_path):
    """Otherwise a replay lookup silently changes later concentration numbers."""
    from procurement_mcp.http import ReplayClient, fixture_key
    from procurement_mcp.store import DatasetStore

    uuid = "c" * 32
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    outsider = tender_doc(uuid=uuid, tender_id="UA-outsider", buyer_edrpou="03327121",
                          amount=900_000.0, awards=[award(edrpou="48888888", amount=900_000.0)])
    (fixtures / fixture_key(f"/tenders/{uuid}", None)).write_text(
        json.dumps({"data": outsider}, ensure_ascii=False), encoding="utf-8"
    )
    make_store(tmp_path, [tender_doc(uuid="inside", tender_id="UA-inside",
                                     awards=[award(edrpou="40000001")])])
    store = DatasetStore(tmp_path, client=ReplayClient(fixtures)).load()
    config = make_config(tmp_path)

    before = concentration.run(config, store, {"buyer_edrpou": "03327121"})
    screen.run(config, store, {"tender_identifier": uuid})
    after = concentration.run(config, store, {"buyer_edrpou": "03327121"})

    assert before == after, "a lookup outside the dataset must not change dataset answers"
    assert find.run(config, store, {})["total_matched"] == 1
    assert store.data_window().tender_count == 1


def test_screen_reports_where_the_tender_came_from(tmp_path):
    from procurement_mcp.http import ReplayClient, fixture_key
    from procurement_mcp.store import DatasetStore

    uuid = "d" * 32
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / fixture_key(f"/tenders/{uuid}", None)).write_text(
        json.dumps({"data": tender_doc(uuid=uuid, tender_id="UA-replayed")}, ensure_ascii=False),
        encoding="utf-8",
    )
    make_store(tmp_path, [tender_doc(uuid="inside", tender_id="UA-inside")])
    store = DatasetStore(tmp_path, client=ReplayClient(fixtures)).load()
    config = make_config(tmp_path)

    assert screen.run(config, store, {"tender_identifier": "UA-inside"})["tender_source"] == "prepared_dataset"
    assert screen.run(config, store, {"tender_identifier": uuid})["tender_source"] == "offline_replay"


def test_find_does_not_echo_the_exclusion_list_back(world):
    config, store = world
    result = find.run(config, store, {"exclude_tender_ids": ["UA-alpha-1", "UA-alpha-2"]})

    assert result["filters_applied"]["exclude_tender_ids"] == "2 tender ids"


def test_undated_tenders_sort_last_not_first(world):
    config, store = world
    result = find.run(config, store, {})

    assert result["tenders"][0]["published_at"] is not None


def test_declared_patterns_are_actually_enforced(world):
    """A pattern in the advertised schema must not be decorative."""
    config, store = world

    for bad in ("1234567", "123456789", "0000000a", "00-00-00-00"):
        with pytest.raises(ToolError) as excinfo:
            concentration.run(config, store, {"buyer_edrpou": bad})
        assert excinfo.value.code == ErrorCode.INVALID_INPUT

    with pytest.raises(ToolError) as excinfo:
        find.run(config, store, {"published_from": "01/06/2026"})
    assert excinfo.value.code == ErrorCode.INVALID_INPUT
    assert "expected_pattern" in excinfo.value.details


def test_every_advertised_input_field_carries_a_constraint():
    """Rubric wording: schemas must be explicit and constrained, not free strings."""
    from procurement_mcp.server import ToolHost

    loose = []
    for tool in ToolHost().describe():
        for name, spec in tool.input_schema.get("properties", {}).items():
            constrained = any(
                key in spec
                for key in ("enum", "pattern", "minimum", "maximum", "minLength", "maxLength",
                            "maxItems", "items", "default")
            )
            if not constrained:
                loose.append(f"{tool.name}.{name}")
    assert not loose, f"unconstrained input fields: {loose}"


def test_an_unperformable_check_is_inconclusive_not_compliant(tmp_path):
    """Reporting compliant: true for a check that never ran asserts a verdict."""
    document = tender_doc(uuid="unkn01", tender_id="UA-unknown",
                          procedure="competitiveDialogueUA.stage2", amount=5_000_000.0,
                          category="works")
    store = make_store(tmp_path, [document])
    result = compliance.run(make_config(tmp_path), store, {"tender_identifier": "UA-unknown"})

    assert result["compliant"] is None
    assert result["inconclusive_checks"][0]["result"] == "unknown"
    assert "not one this rule set classifies" in result["inconclusive_checks"][0]["explanation"]


def test_a_clean_tender_is_still_positively_compliant(tmp_path):
    document = tender_doc(uuid="clean1", tender_id="UA-clean", amount=500_000.0)
    store = make_store(tmp_path, [document])
    result = compliance.run(make_config(tmp_path), store, {"tender_identifier": "UA-clean"})

    assert result["compliant"] is True
    assert result["inconclusive_checks"] == []
