# Tool-contract documentation (Part C)

Domain: public procurement red-flag screening over the Prozorro open API.

The custom-tool sections below are **generated from the running code** by
`scripts/generate_tool_docs.py`: the model-facing description, both schemas and the
worked example come from the server itself, and the example response is produced by
actually calling the tool against the committed dataset. `tests/test_documentation.py`
fails if this file drifts from the code, so a description here cannot quietly stop
matching the one the model sees.

Regenerate with:

```bash
python scripts/generate_tool_docs.py
```

---

<!-- BEGIN GENERATED TOOL CONTRACTS -->

## `find_tenders`

| Contract element | Content |
|---|---|
| **Name** | `find_tenders` |
| **Purpose** | Narrow hundreds of tenders down to the handful worth screening, under filters the caller cannot exceed. The single retrieval tool in the set; everything else in this server computes or judges. |
| **Side effects** | None. Reads the prepared dataset only. |

**Model-facing description** (the exact string exposed over MCP):

> Find tenders in the prepared Prozorro dataset using a fixed set of filters: publication date range, buyer EDRPOU, awarded supplier EDRPOU, CPV prefix, procedure type, value range and region. There is no free-text query and no arbitrary expression language - anything outside this filter list is rejected. Dates filter on the tender's PUBLICATION date, not on when the record was last modified. Returns total_matched separately from a bounded sample, so a large result set is visible rather than silently truncated. Zero matches is a successful result with result_count 0. Use it to decide which tenders to screen; use screen_tender_red_flags to judge one.

**Input schema**

| Field | Type | Required | Constraints | Default | Meaning |
|---|---|---|---|---|---|
| `buyer_edrpou` | string | no | minLength 8; maxLength 10 | — | Buyer's EDRPOU code. |
| `supplier_edrpou` | string | no | minLength 8; maxLength 10 | — | EDRPOU of a supplier that won an award. |
| `published_from` | string | no | — | — | ISO date; publication date lower bound. |
| `published_to` | string | no | — | — | ISO date; publication date upper bound. |
| `cpv_prefix` | string | no | minLength 2; maxLength 10 | — | CPV-DK 021:2015 code prefix, e.g. '336' for medical supplies. |
| `procedure_types` | array | no | maxItems 8; items enum: `aboveThreshold`, `aboveThresholdEU`, `aboveThresholdUA`, `aboveThresholdUA.defense`, `belowThreshold`… | — | Restrict to these procedure types. |
| `min_value` | number | no | minimum 0 | — | Expected value lower bound, UAH. |
| `max_value` | number | no | minimum 0 | — | Expected value upper bound, UAH. |
| `region` | string | no | minLength 3; maxLength 80 | — | Buyer region, matched case-insensitively as a substring. |
| `statuses` | array | no | maxItems 8 | — | Restrict to these tender statuses, e.g. 'complete', 'cancelled'. |
| `exclude_tender_ids` | array | no | maxItems 500 | — | Tender ids to leave out, for skipping records already reviewed. |
| `limit` | integer | no | minimum 1; maximum 100 | `20` | Maximum tenders to return in the sample. |
| _any other field_ | — | — | rejected with `INVALID_INPUT` | — | Unknown arguments are refused, not ignored. |

**Output schema**

| Field | Type | Meaning |
|---|---|---|
| `status` | string |  |
| `result_count` | integer |  |
| `total_matched` | integer |  |
| `truncated` | boolean |  |
| `filters_applied` | object |  |
| `tenders` | array |  |
| `data_window` | object |  |
| `provenance` | object |  |

Always present on success: `status`, `result_count`, `total_matched`, `tenders`.

**Error conditions**

| Code | Raised when |
|---|---|
| `INVALID_INPUT` | malformed EDRPOU, non-ISO date, reversed range, non-numeric CPV prefix, unknown procedure type, or an unknown argument |
| `DATA_INTEGRITY` | the prepared dataset is missing or empty |

Failures come back as an MCP error result carrying
`{"status": "error", "error": {"code", "message", "retryable", "details"}}`.
A successful empty result is an ordinary payload with `result_count: 0`,
so the two can never be read for one another.

**Example** — arguments:

```json
{
  "buyer_edrpou": "01999218",
  "cpv_prefix": "336",
  "limit": 2
}
```

Response, produced by running the tool against the committed dataset:

```json
{
  "status": "ok",
  "result_count": 2,
  "total_matched": 12,
  "truncated": true,
  "filters_applied": {
    "buyer_edrpou": "01999218",
    "cpv_prefix": "336",
    "limit": 2
  },
  "tenders": [
    {
      "tender_id": "UA-2026-08-06-010871-a",
      "uuid": "7fbe91dc47ed4b4596718f3ac7119836",
      "title": "Фармацевтична продукція (Йогексол, розчин для ін'єкцій, 300 мг/мл, по 100 мл;МНН: Iohexol), код ДК 021:2015-33600000-6 Фармацевтична продукція",
      "status": "complete",
      "procedure_type": "priceQuotation",
      "published_at": "2026-08-06T16:56:29.338012+03:00",
      "expected_value": 21067.48,
      "currency": "UAH",
      "buyer": {
        "edrpou": "01999218",
        "name": "Комунальне некомерційне підприємство \"Гадяцька міська центральна лікарня\" Гадяцької міської ради",
        "region": "Полтавська область"
      },
      "cpv_groups": [
        "3369"
      ],
      "bid_count": 1,
      "awarded_to": [
        {
          "edrpou": "45871275",
          "name": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ \"СМ-ФАРМ\""
        }
      ],
      "award_value": 19411.96
    },
    {
      "tender_id": "UA-2026-08-06-010617-a",
      "uuid": "a8d1417f449444e49aee9b6d94f48873",
      "title": "Фармацевтична продукція (Йогексол, розчин для ін'єкцій, 350 мг/мл, по 200 мл; Йогексол, розчин для ін'єкцій, 350 мг/мл, по 20 мл), код ДК 021:2015-33600000-6 Фармацевтична продукція/МНН: Iohexol",
      "status": "complete",
      "procedure_type": "priceQuotation",
      "published_at": "2026-08-06T16:40:47.381548+03:00",
      "expected_value": 1060485.98,
      "currency": "UAH",
      "buyer": {
        "edrpou": "01999218",
        "name": "Комунальне некомерційне підприємство \"Гадяцька міська центральна лікарня\" Гадяцької міської ради",
        "region": "Полтавська область"
      },
      "cpv_groups": [
        "3369"
      ],
      "bid_count": 1,
      "awarded_to": [
        {
          "edrpou": "45871275",
          "name": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ \"СМ-ФАРМ\""
        }
      ],
      "award_value": 837156.07
    }
  ],
  "data_window": {
    "source": "https://public.api.openprocurement.org/api/2.5",
    "swept_at": "2026-08-19T10:59:55+00:00",
    "window_days": 30,
    "window_basis": "dateModified",
    "tenders_in_dataset": 533,
    "earliest_publication": "2026-04-21T12:23:16.635720+03:00",
    "latest_publication": "2026-08-19T13:36:42.399465+03:00",
    "documents_truncated_to_per_buyer": 150
  },
  "provenance": {
    "rules_version": "2026-08-19",
    "thresholds_version": "2026-08-19b",
  … truncated for the document; the tool returns the full object
```

## `compute_buyer_supplier_concentration`

| Contract element | Content |
|---|---|
| **Name** | `compute_buyer_supplier_concentration` |
| **Purpose** | Give the analyst the buyer-level context that a single tender cannot show: whether this buyer's money keeps landing with the same suppliers. Called once per buyer, before its tenders are read. |
| **Side effects** | None. Reads the prepared dataset only. |

**Model-facing description** (the exact string exposed over MCP):

> Measure how concentrated one buyer's awarded contracts are among its suppliers, over the tenders held in the prepared dataset. Returns the Herfindahl-Hirschman index together with the top-1 and top-3 supplier shares - each computed BOTH by awarded value and by award count, because a buyer can look concentrated on one and dispersed on the other - the number of distinct suppliers, a monthly concentration trend with its direction, and the longest run of consecutive awards to a single supplier. Use it once per buyer to get context for reading that buyer's individual tenders. A buyer with no awards in the dataset is a successful empty result, not an error.

**Input schema**

| Field | Type | Required | Constraints | Default | Meaning |
|---|---|---|---|---|---|
| `buyer_edrpou` | string | yes | minLength 8; maxLength 10 | — | Ukrainian EDRPOU code of the buyer: 8 digits, or 10 for some registrations. |
| `published_from` | string | no | — | — | Optional ISO date. Counts only tenders published on or after it. |
| `published_to` | string | no | — | — | Optional ISO date. Counts only tenders published on or before it. |
| `include_trend` | boolean | no | — | `True` | Include the monthly concentration trend. |
| _any other field_ | — | — | rejected with `INVALID_INPUT` | — | Unknown arguments are refused, not ignored. |

**Output schema**

| Field | Type | Meaning |
|---|---|---|
| `status` | string |  |
| `buyer` | object |  |
| `result_count` | integer |  |
| `tenders_considered` | integer |  |
| `awards_counted` | integer |  |
| `distinct_suppliers` | integer |  |
| `total_awarded_value` | number |  |
| `by_value` | object |  |
| `by_count` | object |  |
| `trend` | object |  |
| `supplier_win_streak` | object |  |
| `data_window` | object |  |
| `provenance` | object |  |

Always present on success: `status`, `result_count`, `buyer`.

**Error conditions**

| Code | Raised when |
|---|---|
| `INVALID_INPUT` | buyer_edrpou is not 8 or 10 digits, dates are not ISO, or the period is reversed |
| `DATA_INTEGRITY` | the prepared dataset is missing or empty |

Failures come back as an MCP error result carrying
`{"status": "error", "error": {"code", "message", "retryable", "details"}}`.
A successful empty result is an ordinary payload with `result_count: 0`,
so the two can never be read for one another.

**Example** — arguments:

```json
{
  "buyer_edrpou": "31557119",
  "include_trend": true
}
```

Response, produced by running the tool against the committed dataset:

```json
{
  "status": "ok",
  "buyer": {
    "edrpou": "31557119",
    "name": "КП \"ХАРКІВСЬКІ ТЕПЛОВІ МЕРЕЖІ\""
  },
  "result_count": 124,
  "tenders_considered": 150,
  "period": {
    "published_from": null,
    "published_to": null
  },
  "awards_counted": 124,
  "distinct_suppliers": 71,
  "total_awarded_value": 261464583.75,
  "by_value": {
    "hhi": 0.0669,
    "top_1_share": 0.1736,
    "top_3_share": 0.3546,
    "top_suppliers": [
      {
        "edrpou": "32597770",
        "name": "ТОВ \"КТС ІНЖИНІРИНГ\"",
        "share": 0.1736
      },
      {
        "edrpou": "38158090",
        "name": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ \"СТАЛЬКОНСТРУКЦІЯ ЛТД\"",
        "share": 0.122
      },
      {
        "edrpou": "44513531",
        "name": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ «ЗАВОД РЕМГІДРОМАШ»",
        "share": 0.0591
      }
    ]
  },
  "by_count": {
    "hhi": 0.0232,
    "top_1_share": 0.0806,
    "top_3_share": 0.1452,
    "top_suppliers": [
      {
        "edrpou": "2475600712",
        "name": "ФОП СТЕПАНЕНКО ВІТАЛІЙ КОСТЯНТИНОВИЧ",
        "share": 0.0806
      },
      {
        "edrpou": "44215219",
        "name": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ \"ГІДРА БУД МОНТАЖ\"",
        "share": 0.0323
      },
      {
        "edrpou": "39524274",
        "name": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ \"ЗОЛОТИЙ СТАНДАРТ К\"",
        "share": 0.0323
      }
    ]
  },
  "supplier_win_streak": {
    "length": 4,
    "edrpou": "39747316",
    "supplier_name": "ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ \"ГЕОДЕЗИЧНА КОМПАНІЯ \"ГЕОПРОМ\"",
    "tender_ids": [
      "UA-2026-08-07-000431-a",
      "UA-2026-08-07-000467-a",
      "UA-2026-08-07-000515-a",
      "UA-2026-08-07-000549-a"
    ],
    "cpv_groups": [
      "7125"
    ]
  },
  "data_window": {
    "source": "https://public.api.openprocurement.org/api/2.5",
    "swept_at": "2026-08-19T10:59:55+00:00",
    "window_days": 30,
    "window_basis": "dateModified",
    "tenders_in_dataset": 533,
    "earliest_publication": "2026-04-21T12:23:16.635720+03:00",
    "latest_publication": "2026-08-19T13:36:42.399465+03:00",
    "documents_truncated_to_per_buyer": 150
  },
  "provenance": {
    "rules_version": "2026-08-19",
    "thresholds_version": "2026-08-19b",
    "classifier": "ДК 021:2015 (CPV)",
    "mode": "offline-replay",
    "human_review_threshold": 60.0
  },
  "trend": {
    "buckets": [
      {
        "month": "2026-06",
        "awards": 4,
        "hhi_by_value": 0.3208
      },
      {
        "month": "2026-07",
        "awards": 37,
        "hhi_by_value": 0.1819
  … truncated for the document; the tool returns the full object
```

## `check_procedure_threshold_compliance`

| Contract element | Content |
|---|---|
| **Name** | `check_procedure_threshold_compliance` |
| **Purpose** | Answer one legal question about a tender: was the procedure it used allowed at the value it carried, under the rules in force when it was published. Separated from screening because it is the only check whose answer is a citable yes or no rather than a judgement. |
| **Side effects** | None. Reads the prepared dataset and the versioned threshold configuration. |

**Model-facing description** (the exact string exposed over MCP):

> Check whether the procurement procedure a tender used is consistent with the Ukrainian value thresholds and category rules that were in force on its publication date - not today's rules. Handles framework agreements as their own case, and verifies that the tender classifies its items with CPV-DK 021:2015, which is what every threshold is expressed in terms of. Returns compliant - true when every check passed, false when one failed, and null when a check could not be performed at all - a list of failed conditions each with an explanation and a citation, any inconclusive checks, and the exact threshold values and configuration version used to decide. Use it for any tender before judging it, because a threshold breach is itself one of the strongest signals.

**Input schema**

| Field | Type | Required | Constraints | Default | Meaning |
|---|---|---|---|---|---|
| `tender_identifier` | string | yes | minLength 4; maxLength 64 | — | tenderID (for example 'UA-2026-06-01-000123-a') or document UUID. |
| _any other field_ | — | — | rejected with `INVALID_INPUT` | — | Unknown arguments are refused, not ignored. |

**Output schema**

| Field | Type | Meaning |
|---|---|---|
| `status` | string |  |
| `compliant` | boolean or null | true if every check passed, false if one failed, null if a check could not be performed. |
| `inconclusive_checks` | array |  |
| `tender` | object |  |
| `subject` | string |  |
| `failed_conditions` | array |  |
| `checks_performed` | array |  |
| `applicable_thresholds` | object |  |
| `provenance` | object |  |

Always present on success: `status`, `compliant`, `failed_conditions`, `applicable_thresholds`.

**Error conditions**

| Code | Raised when |
|---|---|
| `INVALID_INPUT` | tender_identifier missing or an unknown argument supplied |
| `NOT_FOUND` | no such tender in the dataset |
| `DATA_INTEGRITY` | items are not classified with CPV-DK 021:2015, or the tender has no publication date |

Failures come back as an MCP error result carrying
`{"status": "error", "error": {"code", "message", "retryable", "details"}}`.
A successful empty result is an ordinary payload with `result_count: 0`,
so the two can never be read for one another.

**Example** — arguments:

```json
{
  "tender_identifier": "UA-2026-08-18-004904-a"
}
```

Response, produced by running the tool against the committed dataset:

```json
{
  "status": "ok",
  "compliant": false,
  "inconclusive_checks": [],
  "tender": {
    "tender_id": "UA-2026-08-18-004904-a",
    "uuid": "db0404bbe451471bb4126b576ffaa39c",
    "procedure_type": "reporting",
    "expected_value": 7480649.61,
    "currency": "UAH",
    "main_category": "works",
    "published_at": "2026-08-18T12:04:51.395354+03:00"
  },
  "subject": "works",
  "failed_conditions": [
    {
      "condition": "procedure_matches_value_threshold",
      "explanation": "procedure 'reporting' awards directly, but the expected value 7,480,649.61 UAH is at or above the 1,500,000 UAH threshold at which an open tender is required for works",
      "statute_reference": {
        "value": 1500000,
        "subject": "works",
        "regime": "osoblyvosti-1178",
        "source": "https://zakon.rada.gov.ua/laws/show/1178-2022-%D0%BF",
        "source_point": "пункт 10",
        "verification": "primary"
      }
    }
  ],
  "checks_performed": [
    {
      "condition": "procedure_matches_value_threshold",
      "result": "failed",
      "expected_value": 7480649.61,
      "threshold": 1500000,
      "procedure_type": "reporting"
    }
  ],
  "applicable_thresholds": {
    "regime": "osoblyvosti-1178",
    "regime_name": "Особливості здійснення публічних закупівель (КМУ № 1178 від 12.10.2022)",
    "effective_from": "2022-10-19",
    "effective_to": null,
    "configuration_version": "2026-08-19b",
    "classifier": "ДК 021:2015 (CPV)",
    "mandatory_open_tender_from": {
      "value": 1500000,
      "subject": "works",
      "regime": "osoblyvosti-1178",
      "source": "https://zakon.rada.gov.ua/laws/show/1178-2022-%D0%BF",
      "source_point": "пункт 10",
      "verification": "primary"
    }
  },
  "provenance": {
    "rules_version": "2026-08-19",
    "thresholds_version": "2026-08-19b",
    "classifier": "ДК 021:2015 (CPV)",
    "mode": "offline-replay",
    "human_review_threshold": 60.0
  }
}
```

## `screen_tender_red_flags`

| Contract element | Content |
|---|---|
| **Name** | `screen_tender_red_flags` |
| **Purpose** | Decide whether one tender deserves human attention, and show the working. The model calls it once per tender after find_tenders has narrowed the set. It is the only tool that produces a risk score, and the only one that distinguishes a citable breach from a merely suspicious pattern. |
| **Side effects** | None on the dataset. Reads the prepared documents and, when the requested tender is absent and the identifier is a document UUID, one upstream document through the configured client - the live API, or a recorded fixture in offline mode. Nothing is written. |

**Model-facing description** (the exact string exposed over MCP):

> Screen ONE Ukrainian public tender for procurement red flags and return a structured, auditable result. Applies only the rules that fit the tender's procedure type and the statutory thresholds in force on its publication date. Returns blocking_violations (breaches of a citable written rule), advisories (lawful but suspicious patterns, including single participation and supplier win streaks), a 0-100 risk_score, an evidence_chain showing every signal with its weight and contribution, and rules_not_applicable explaining each rule that was skipped and why. Use it once per tender, after find_tenders has identified which tenders to look at. Do not use it to compare buyers or suppliers over time - that is compute_buyer_supplier_concentration.

**Input schema**

| Field | Type | Required | Constraints | Default | Meaning |
|---|---|---|---|---|---|
| `tender_identifier` | string | yes | minLength 4; maxLength 64 | — | Either the human-facing tenderID (for example 'UA-2026-06-01-000123-a') or the document UUID, as returned by find_tenders. |
| `include_evidence` | boolean | no | — | `True` | Return the full evidence chain. Set false only for a compact overview. |
| _any other field_ | — | — | rejected with `INVALID_INPUT` | — | Unknown arguments are refused, not ignored. |

**Output schema**

| Field | Type | Meaning |
|---|---|---|
| `status` | string |  |
| `tender` | object |  |
| `has_blocking` | boolean |  |
| `risk_score` | number |  |
| `raw_weighted_sum` | number |  |
| `blocking_floor_applied` | boolean |  |
| `requires_human_review` | boolean |  |
| `blocking_violations` | array |  |
| `advisories` | array |  |
| `evidence_chain` | array |  |
| `rules_not_applicable` | array |  |
| `rules_errored` | array |  |
| `tender_source` | string |  |
| `data_window` | object |  |
| `provenance` | object |  |

Always present on success: `status`, `risk_score`, `has_blocking`, `blocking_violations`, `advisories`.

**Error conditions**

| Code | Raised when |
|---|---|
| `INVALID_INPUT` | tender_identifier missing, too short, or an unknown argument supplied |
| `NOT_FOUND` | no such tender in the dataset, and a tenderID cannot be resolved upstream |
| `FIXTURE_MISSING` | offline mode, and no recording exists for that document |
| `UPSTREAM_UNAVAILABLE` | live mode, and the API failed after the configured retries |
| `DATA_INTEGRITY` | the document has no id or no procurementMethodType, so no rule set applies |

Failures come back as an MCP error result carrying
`{"status": "error", "error": {"code", "message", "retryable", "details"}}`.
A successful empty result is an ordinary payload with `result_count: 0`,
so the two can never be read for one another.

**Example** — arguments:

```json
{
  "tender_identifier": "UA-2026-07-06-008728-a",
  "include_evidence": true
}
```

Response, produced by running the tool against the committed dataset:

```json
{
  "status": "ok",
  "tender": {
    "tender_id": "UA-2026-07-06-008728-a",
    "uuid": "4d4701c80ffe4c54bfca0a263ebc3dba",
    "title": "Поточний ремонт внутрішньобудинкових систем теплопостачання та постачання гарячої води в багатоквартирних житлових будинках м. Харкова по проспекту  Перемоги, 65-Б.",
    "procedure_type": "aboveThreshold",
    "status": "active.awarded",
    "expected_value": 653420.15,
    "currency": "UAH",
    "published_at": "2026-07-06T16:02:26.526552+03:00",
    "buyer": {
      "edrpou": "31557119",
      "name": "КП \"Харківські теплові мережі\"",
      "region": "Харківська область"
    }
  },
  "has_blocking": true,
  "risk_score": 80.0,
  "raw_weighted_sum": 65.0,
  "blocking_floor_applied": true,
  "requires_human_review": true,
  "blocking_violations": [
    {
      "rule_id": "bid_window_below_statutory_minimum",
      "title": "Bid submission period shorter than the statutory minimum",
      "class": "blocking",
      "weight": 40.0,
      "observed_value": 7.33,
      "threshold_value": 14,
      "evidence": {
        "published_at": "2026-07-06T16:02:26.526552+03:00",
        "bids_due": "2026-07-14T00:00:00+03:00",
        "subject": "works",
        "tolerance_days": 0.5
      },
      "statute_reference": {
        "value": 14,
        "subject": "works",
        "regime": "osoblyvosti-1178",
        "source": "https://zakon.rada.gov.ua/laws/show/1178-2022-%D0%BF",
        "source_point": "пункт 34, у редакції Постанови КМУ № 382 від 02.04.2024",
        "verification": "primary"
      }
    }
  ],
  "advisories": [
    {
      "rule_id": "effective_single_participation",
      "title": "Competitive procedure that drew a single effective bid",
      "class": "advisory",
      "weight": 25.0,
      "observed_value": 1,
      "threshold_value": 2,
      "evidence": {
        "procedure_type": "aboveThreshold",
        "status": "active.awarded",
        "outcome": "awarded",
        "note": "lawful in Ukraine; weighted as a risk indicator, not as a violation"
      }
    }
  ],
  "rules_not_applicable": [
    {
      "rule_id": "procedure_value_threshold_mismatch",
      "reason": "procedure 'aboveThreshold' is already a competitive procedure"
    },
    {
      "rule_id": "bid_timing_compressed",
      "reason": "fewer than 2 bids carry a submission time"
    },
    {
      "rule_id": "losing_bidder_in_subcontracting",
      "reason": "no bid declares subcontracting details"
    },
    {
      "rule_id": "supplier_new_to_dataset",
  … truncated for the document; the tool returns the full object
```

<!-- END GENERATED TOOL CONTRACTS -->

---

## Error taxonomy (custom server)

Shared across all tools so the agent can branch on failure type rather than on prose.
Empty-but-successful results are never reported as errors; they return a normal payload
with an empty collection and an explicit `result_count: 0`.

| Code | Meaning | Retryable | Typical cause |
|---|---|---|---|
| `INVALID_INPUT` | Request failed schema or domain constraint validation | No | Model supplied an out-of-range or malformed argument |
| `NOT_FOUND` | Identified entity does not exist | No | Bad identifier |
| `UPSTREAM_UNAVAILABLE` | Public API unreachable, timed out, or returned 5xx | Yes | Network or provider outage |
| `RATE_LIMITED` | Local or upstream rate limit reached | Yes, after backoff | Too many calls |
| `FIXTURE_MISSING` | Offline/replay mode requested a recording that does not exist | No | Demo data gap |
| `DATA_INTEGRITY` | Source data present but violates an invariant the tool relies on | No | Corrupt or unexpected upstream payload |

`INVALID_INPUT` and a successful `result_count: 0` are the pair the assignment cares about
most: a bad EDRPOU is an error, a valid EDRPOU with no matching tenders is a success with
an empty collection. No response can be read both ways.

---

## Existing server (Part A): Obsidian Local REST API

| Element | Content |
|---|---|
| Server | Obsidian Local REST API plugin, reached through the `mcp-obsidian` bridge |
| Plugin | <https://github.com/coddingtonbear/obsidian-local-rest-api>, running inside the Obsidian desktop app |
| Bridge command | `uvx --with "mcp<2" mcp-obsidian` |
| Transport | stdio, launched by the agent from `config/mcp.json` |
| Authentication | `OBSIDIAN_API_KEY`, read from the environment; never committed |
| Tools discovered | `obsidian_list_files_in_dir`, `obsidian_list_files_in_vault`, `obsidian_get_file_contents`, `obsidian_simple_search`, `obsidian_patch_content`, `obsidian_append_content`, `obsidian_delete_file`, `obsidian_complex_search`, `obsidian_batch_get_file_contents`, `obsidian_get_periodic_note`, `obsidian_get_recent_periodic_notes`, `obsidian_get_recent_changes` |

**Version pin, and why it matters.** The published `mcp-obsidian` package is written
against the 1.x MCP SDK. Installed with an unpinned resolver it pulls SDK 2.x and dies on
import with `AttributeError: 'Server' object has no attribute 'list_tools'` before any
tool is listed. The connection therefore pins `mcp<2` for that process only; the custom
server in this repository runs on SDK 2.x in its own process, which is one practical
benefit of process separation.

**Role in this project.** The vault is the agent's memory and its output surface. It
supplies which buyers to screen, what the analyst wants looked at this cycle, and which
tenders were already judged; it receives the findings. Remove it and the agent loses both
its instructions and its run-to-run continuity - it would re-screen the same tenders every
run and have nowhere to put a conclusion.

### Documented tool: `obsidian_get_file_contents`

| Contract element | Content |
|---|---|
| Name | `obsidian_get_file_contents` |
| Purpose | Read one note from the vault. The agent uses it for `procurement/watchlist.md` and for each note under `procurement/findings/`. |
| Model-facing description | "Return the content of a single file in your vault." |
| Input schema | `filepath` (string, required, `format: path`) - path relative to the vault root. No other field is accepted. |
| Output | The note's raw text, returned as MCP text content. The agent parses the YAML frontmatter itself; the server does no interpretation. |
| Error conditions | Missing note returns a 404-derived error; a wrong or absent `OBSIDIAN_API_KEY` returns 401; if Obsidian is not running, or the plugin is disabled, the connection itself fails before any tool call and the agent reports `MCPConnectionError`. |
| Side effects | None. Read-only. |
| Example | Input `{"filepath": "procurement/watchlist.md"}`; output is the watchlist note, whose frontmatter yields four buyer EDRPOUs and one already-reviewed tender id, and whose prose becomes the CPV filter passed to `find_tenders`. |

### Second tool used: `obsidian_append_content`

Used for both writes, because it is the only write that works against this plugin version.

| Contract element | Content |
|---|---|
| Name | `obsidian_append_content` |
| Model-facing description | "Append content to a new or existing file in the vault." |
| Input schema | `filepath` (string, required, `format: path`), `content` (string, required) |
| Output | `Successfully appended content to <path>` |
| Error conditions | Endpoint unreachable when Obsidian is not running (classified `ENDPOINT_UNREACHABLE`); 401 on a wrong API key; path errors on an invalid vault path |
| Side effects | **Writes to the vault.** Creates the file when it does not exist. |
| Example | `{"filepath": "procurement/findings/_run-log.md", "content": "\n## run-20260819-115232 — 2026-08-19\n\n- screened: UA-…\n"}` |

**`obsidian_patch_content` is advertised but unusable here, and that shaped the design.**
Every call fails against the installed plugin, for every `target_type`:

> Error 40084: Header-based PATCH targeting is ambiguous between the two patch formats, so
> it requires an explicit 'Markdown-Patch-Version' header

The bridge does not send that header. Combined with the absence of any whole-file write,
the vault's usable write surface is **append only**. The run-to-run state is therefore an
append-only log at `procurement/findings/_run-log.md` rather than an edited frontmatter
field, and a re-screen adds a revision block instead of a second frontmatter block. The
arrangement is better than the one it replaced: the agent structurally cannot rewrite what
the analyst wrote.

**Failure demonstrated at the defence.** Quit Obsidian, or set `OBSIDIAN_API_KEY` to a
wrong value, and start the agent. The connection fails during startup, the agent prints
`MCP CONNECTION FAILURE` naming the server and the cause, exits with status 3, and writes
nothing - the read phase precedes every write for exactly this reason.

---
