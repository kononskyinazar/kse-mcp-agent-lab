# Review of the proposed upgrades

Each item is judged on three axes: **is the data actually available** in the Prozorro open
API, **does it survive the assignment's grading rules** (distinct responsibility, no
generic plumbing, no hard-coded answers), and **does it fit a 10-15 minute defence**.

## Feasibility evidence gathered before judging

Probed against the live public API on 2026-08-19, no authentication used at any point:

| Question | Answer |
|---|---|
| `GET /api/2.5/tenders/{uuid}` full document | 200, ~229 KB, no key |
| Bid-level data on a competitive tender | `bids[]` exposes `date`, `submissionDate`, `status`, `lotValues`, `tenderers[].identifier.id` (EDRPOU), `subcontractingDetails` |
| Award-level data | `awards[]` exposes `bid_id`, `date`, `status`, `qualified`, `eligible`, `value`, `suppliers[]` |
| Cancellations | `cancellations[]` present on the tender document |
| Buyer identity in the change feed | `opt_fields=procuringEntity` returns the nested object, so a feed sweep can be filtered by EDRPOU |
| Feed page size | `limit=1000` accepted; 1000 rows covered ~30 minutes of activity, so roughly 48k feed rows per day |
| Buyer-targeted search | `POST https://prozorro.gov.ua/api/search/tenders` works without a key; `status` and `procurementMethodType` filter correctly, free-text buyer name matched 17 of 20 rows, so results still need a client-side EDRPOU filter |
| `tenderID` to UUID resolution | **Not available.** `/tenders/UA-2026-...` returns 404 on both hosts, and the search response carries only `tenderID`, `title`, `value`, `status`, `procuringEntity` |
| Rate limiting | Rapid sequential calls returned `503 Service Unavailable`; backoff cleared it |

Two consequences follow, and they shape the whole design:

1. Because `tenderID` cannot be resolved to a document UUID, the prepared dataset is built
   by **sweeping the change feed with a buyer allow-list** rather than by searching. The
   watchlist EDRPOUs are therefore fixed before the sweep, not after.
2. Because the feed carries roughly 48k rows per day, the sweep window is a documented,
   configurable parameter. Everything that needs history - streaks, trends - is bounded by
   that window, not by an arbitrary 12 months.

---

## 1. `screen_tender_red_flags`

| # | Item | Verdict | Reasoning |
|---|---|---|---|
| 1.1 | Temporal statutory minimums | **Adopt** | Correct and important. Rules are looked up by the tender's `noticePublicationDate` against entries carrying `effective_from` / `effective_to`. Caveat recorded as a limitation: the shipped table covers only the periods the prepared dataset spans, each entry citing its source; it is not a complete legal history of Ukrainian procurement law. |
| 1.2 | Directional award/estimate ratio | **Adopt** | Both `value.amount` and `awards[].value.amount` are available. Both tails are flagged with separate rule ids, and the raw ratio is always returned so a reviewer can disagree with the thresholds. Your own note - a ratio near 1.0 is ambiguous between healthy competition and bid rigging - is exactly why this stays an advisory, never a blocking violation. |
| 1.3 | Winner-takes-all streak | **Adopt, but relocated to tool 2** | The signal is real, the placement is wrong. Tool 1 screens one tender; a streak is a property of a buyer-supplier pair over time. Putting a cross-tender computation inside a per-tender tool blurs the "distinct responsibility" criterion the rubric grades, and forces tool 1 to load history it otherwise never needs. It becomes `supplier_win_streak` inside the concentration tool, and tool 1 accepts the resulting flag as an optional input so its risk score can still take it into account. |
| 1.4 | Bid timing compression | **Adopt** | Confirmed feasible: `bids[].date` and `submissionDate` are exposed. Implemented as the spread between first and last bid relative to `tenderPeriod.endDate`, with a configurable window. Applies only to procedures that actually have competing bids. |
| 1.5 | Bidder overlap / shell bidding | **Adopt, downgraded to advisory** | `subcontractingDetails` exists on bids, but it is **free text, not an EDRPOU field**. A losing bidder can therefore only be matched to a named subcontractor by normalised string matching, which is suggestive, not proof. It fires as an advisory with the matched strings quoted as evidence, and it can never block. |
| 1.6 | Winner registration timing | **Adopt as an explicitly labelled proxy** | Prozorro does not publish company registration dates; the real source is the state register, which needs paid or authenticated access the assignment forbids. Instead the tool reports `supplier_first_seen_days_before_notice`, the first appearance of that supplier EDRPOU anywhere in the prepared dataset. That is a *dataset-horizon* proxy, not a registration date, and the field name and documentation say so. Advisory only. |
| 1.7 | Spec tailoring | **Adopt a deterministic subset; reject the NLP version** | Full specification analysis means downloading and parsing attached documents - PDF, DOC, scans - which is a project of its own and would not be reproducible offline. The subset that *is* cheap and genuinely domain-specific: scan item descriptions and the title for brand and model tokens, and flag a brand-like token that appears without the equivalence wording Ukrainian procurement rules require. Deterministic, quotable as evidence, advisory only. |
| 1.8 | Cancellation refinement | **Adopt** | `cancellations[]` carries the reason and status, so the three cases you name are distinguishable. Reissue similarity is computed inside the prepared dataset: same buyer, overlapping CPV, value within a band, later publication date. Returned as a score with the matched tender listed, so the reviewer can check it. |
| 1.9 | Evidence chain | **Adopt, and treat as a headline feature** | This is the single best item in the list for the grade. It converts the tool from an opaque scorer into something a reviewer can audit, and it is what makes the defence question "where did this value come from?" answerable in one screen. Every entry carries the rule id, the observed value, the threshold, the weight and the contribution. |
| 1.10 | Weighting scheme documented | **Adopt, with the choice made explicit** | Static per-rule weights, linear sum, clamped to 0-100, loaded from a versioned config file. Not logarithmic - there is no calibration data that would justify a non-linear curve, and inventing one would be unfalsifiable. Procedure-type specificity is handled by an applicability matrix rather than by varying weights. |

**Added on top of your list, forced by the data:** the return value includes
`rules_not_applicable`, listing every rule skipped for this procedure type with the reason.
Without it, a `reporting` tender - a direct contract that has zero bids by design - would
look either suspiciously clean or falsely damning depending on which way the code fell.

## 2. `compute_buyer_supplier_concentration`

| # | Item | Verdict | Reasoning |
|---|---|---|---|
| 2.1 | Denominator clarification | **Adopt in full** | Returning several metrics rather than picking one is right, and it costs nothing: share by awarded value, share by award count, and the distinct-supplier count are all computed from the same pass. |
| 2.2 | HHI plus top-1 and top-3 shares | **Adopt** | Your worked example is correct - HHI compresses tail structure - and the three numbers together are cheap. |
| 2.3 | Trend over time | **Adopt, with the window corrected** | Sound, but "quarterly over three years" is not reachable: history is bounded by the feed sweep window, which is a documented parameter. Buckets are monthly by default and the response states the window and bucket count, so a short window is visible rather than silently misleading. |
| new | `supplier_win_streak` | **Added** | Item 1.3 relocated here. |

## 3. `check_procedure_threshold_compliance`

| # | Item | Verdict | Reasoning |
|---|---|---|---|
| 3.1 | CPV-DK 021:2015 | **Adopt** | This is what Prozorro publishes in `items[].classification`, so the tool asserts the scheme rather than assuming it, and returns `DATA_INTEGRITY` if a record carries something else. |
| 3.2 | Framework agreements | **Adopt** | `closeFrameworkAgreementUA` and its selection variant are distinct procedure types with their own logic; without a branch for them the tool would emit confident nonsense. |
| 3.3 | Config file for thresholds | **Adopt** | Same versioned file as item 1.1, each entry carrying its source reference and effective dates. Hard-coding was correctly rejected in your own note. |

## 4. `find_tenders`

All three items adopted. Filters are an explicit allow-list - date range, buyer EDRPOU,
supplier EDRPOU, CPV prefix, procedure type, value range, region (available from
`procuringEntity.address.region`) - with no free-form query language, no SQL and no joins.
The response separates `total_matched` from the returned `sample`, and reports
`result_count: 0` as an ordinary success, which is what makes a genuine empty result
distinguishable from a failure.

## 5. Architectural pieces

| # | Item | Verdict | Reasoning |
|---|---|---|---|
| 5.1 | State persistence in vault frontmatter | **Adopt** | Keeps the system self-contained, gives the Obsidian connection a second real job, and makes the run-to-run loop visible during the defence. |
| 5.2 | Human-in-the-loop interrupt | **Adopt** | Strong on two axes at once: it is the right call for output that could become a corruption allegation, and a LangGraph interrupt is an excellent thing to show live. |
| 5.3 | Vault schema | **Adopt** | Needed regardless; without a fixed frontmatter contract the state in 5.1 is unreadable. |
| 5.4 | Richer failure demos | **Adopt** | Already justified by evidence: the API returned `503` under rapid calls during these probes, so the retry-with-backoff path is real behaviour rather than a staged one. Partial failure - two tools of three succeed - becomes a named branch in the graph. |

## 6. Suggested extra tool

| # | Item | Verdict | Reasoning |
|---|---|---|---|
| 6.1 | `explain_finding` | **Deferred, and partly absorbed** | As specified - take a finding id, return its stored evidence chain - it would return stored text, which the assignment lists explicitly as not counting as a substantive tool. The parts worth keeping are absorbed into item 1.9: statute references travel inside the evidence chain, so no extra hop is needed to answer "why did this fire?". The one genuinely new capability in your sketch is the peer-cohort comparison - how unusual is this tender against similar tenders - and that is kept as a designed but unimplemented fifth tool, `compare_to_peer_cohort`, to be built only if the core four are finished and rehearsed. Four tools already clear the requirement; a fifth adds defence surface without adding points. |

## 7. Revised agent flow

Adopted essentially as written, with two changes: the concentration call is made **once
per buyer** rather than once per flagged tender, because it is a buyer-level metric and
repeating it per tender wastes calls and invites inconsistent numbers within one run; and
threshold compliance is checked for **every screened tender**, not only flagged ones,
since a threshold breach is itself one of the strongest blocking signals and must not be
reachable only through another rule firing first.

## 8. Return formats

Adopted as the basis, with four additions applied to every tool for the sake of the error
contract the assignment grades: a `status` discriminator, `result_count` where a
collection is returned, a `data_window` block stating which dataset slice produced the
answer, and `rules_not_applicable` on the screening tool.
