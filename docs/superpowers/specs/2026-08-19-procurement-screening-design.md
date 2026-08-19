# Design: procurement red-flag screening agent

Date: 2026-08-19
Status: approved in outline; upgrades from the review applied
Related: [`docs/upgrade-review.md`](../../upgrade-review.md), [`docs/plan-review.md`](../../plan-review.md)

## 1. Problem

A procurement analyst watches a small set of public buyers. For each buyer they want to
know which recent tenders deserve human attention, why, and on what evidence - and they
want the answer to accumulate across runs rather than restart each time.

This is not a lookup. A verdict on one tender depends on that buyer's award history, on
which statutory rules were in force when the tender was published, and on what the analyst
already decided in previous runs.

## 2. Architecture

Three processes, started independently:

```
Obsidian desktop                  agent process                custom MCP server
+ Local REST API plugin           (LangGraph)                  (separate process)
        |                              |                               |
        |<--- MCP (Obsidian server) ---|--- MCP (stdio) -------------->|
        |                              |                               |
   demo vault                    OpenRouter model            data/ (prepared dataset)
   watchlist + findings                                      fixtures/ (recorded API)
                                                             config/ (rules, thresholds)
                                                                      |
                                                             Prozorro open API
                                                             (live mode only)
```

- **Existing MCP server:** Obsidian Local REST API MCP. Reads the watchlist and prior
  findings; writes the findings note back. Read *and* write, and prior findings change the
  current run, so its result demonstrably affects later steps.
- **Custom MCP server:** own process, stdio transport, four tools, over the prepared
  dataset with an optional live path to the Prozorro open API.
- **Agent:** LangGraph. The graph shape is the deliverable's evidence that a tool result
  changes a later step, since the branch is a visible edge rather than a prompt hint.

## 3. Data

**Source.** Prozorro / OpenProcurement public API. No authentication, no key, no account.
Verified live on 2026-08-19; evidence table in `docs/upgrade-review.md`.

**Why a prepared dataset, not live queries.** `tenderID` cannot be resolved to a document
UUID through any public endpoint, and the change feed carries roughly 48k rows per day, so
buyer-targeted retrieval is only possible by sweeping the feed with a buyer allow-list.
The sweep therefore runs once, offline, from `scripts/harvest.py`:

1. read the watchlist EDRPOUs from `config/watchlist.yaml`;
2. page the change feed backwards over a configurable window (default 90 days) at
   `limit=1000`, requesting `opt_fields=tenderID,status,procuringEntity`, one request per
   second, honouring backoff on `503` and `429`;
3. keep only rows whose `procuringEntity.identifier.id` is in the allow-list, dedupe by
   document UUID;
4. fetch the full document for each kept UUID;
5. write normalised records to `data/tenders/` plus a manifest recording the window, the
   sweep timestamp, the record count and the API host.

The resulting slice - a few hundred tenders across three to five buyers - is committed, so
the demonstration needs no network at all. The same normalisation code path serves live
responses, replayed fixtures and dataset records; there is no branch anywhere that returns
a prewritten answer.

**Rate limiting.** One request per second, exponential backoff on `429` and `503`, a
descriptive User-Agent, and a hard cap on requests per sweep. The `503` seen during
probing is the reason this is a designed feature rather than an afterthought.

## 4. Custom MCP server

### 4.1 `screen_tender_red_flags`

Screens **one** tender against the rule set in force at its publication date.

Input: the tender identifier, optionally a pre-computed streak flag from tool 2, and an
optional rules-profile name. Output: `blocking_violations`, `advisories`, `risk_score`
(0-100), `evidence_chain`, and `rules_not_applicable`.

Rules, each with an id, a weight, an applicability set of procedure types, and a source
reference:

| Rule | Class | Notes |
|---|---|---|
| Bid window shorter than the statutory minimum in force at publication | blocking | Threshold looked up by `noticePublicationDate` |
| Procurement procedure inconsistent with value thresholds | blocking | Delegated to tool 3, surfaced here |
| Effective single participation where the procedure requires competition | blocking | Procedure-aware; never fires on `reporting` |
| Award/estimate ratio above the no-discount bound | advisory | Ratio always returned raw |
| Award/estimate ratio below the lowballing bound | advisory | Separate rule id from the above |
| Bid submissions compressed near the deadline | advisory | From `bids[].date` / `submissionDate` |
| Losing bidder named in the winner's subcontracting details | advisory | Free-text match; strings quoted as evidence |
| Supplier first seen in the dataset shortly before the notice | advisory | Dataset-horizon proxy, not a registration date |
| Brand-like token without equivalence wording | advisory | Deterministic lexical scan of title and item descriptions |
| Cancelled after a winner was selected | advisory | From `cancellations[]` |
| Cancelled and reissued with a similar scope | advisory | Similarity computed inside the dataset; match listed |

Scoring is a linear weighted sum clamped to 0-100. Weights are static, live in
`config/rules.yaml`, and are reported per firing rule in the evidence chain, so any score
can be recomputed by hand from the response.

Applicability matters more than scoring. `reporting` - a direct contract - has no bids by
construction, so every competition rule is skipped and listed in `rules_not_applicable`
with its reason, rather than silently passing or falsely firing.

### 4.2 `compute_buyer_supplier_concentration`

One buyer, one period. Returns HHI, top-1 and top-3 shares, each computed both by awarded
value and by award count, the distinct-supplier count, a monthly trend with direction and
magnitude, and `supplier_win_streak` - the longest run of consecutive awards to one
supplier for that buyer, with the CPV groups it spans. The response states the data window
and bucket count, so a short history is visible rather than misleading.

### 4.3 `check_procedure_threshold_compliance`

Validates the chosen procedure against the value thresholds and category rules in force at
publication. Handles framework agreements as their own branch. Asserts that item
classification uses CPV-DK 021:2015 and returns `DATA_INTEGRITY` if not. Returns
`compliant`, `failed_conditions` with an explanation and a source reference each, and the
`applicable_thresholds` block naming the config version and effective date used.

### 4.4 `find_tenders`

The single retrieval tool. Explicit filter allow-list: date range, buyer EDRPOU, supplier
EDRPOU, CPV prefix, procedure type, value range, region. No free-form query language, no
SQL, no joins. Returns `total_matched` separately from a bounded `sample`, and reports
`result_count: 0` as an ordinary success.

### 4.5 Deferred

`compare_to_peer_cohort` - how unusual a tender is against similar tenders - is designed
but not implemented unless the core four are finished and rehearsed.

### 4.6 Shared response envelope

Every tool returns `status`, `data_window`, and either a payload or an `error` object with
one of the codes in `docs/tool-contracts.md`. A successful empty result and a failure are
never expressible by the same response.

## 5. Configuration files

| File | Contents | Committed |
|---|---|---|
| `config/watchlist.yaml` | Buyer EDRPOUs for the sweep and the demo | Yes |
| `config/statutory_thresholds.yaml` | Value thresholds and minimum periods, each with `effective_from`, `effective_to` and a source reference | Yes |
| `config/rules.yaml` | Rule ids, weights, bounds, applicability sets | Yes |
| `config/mcp.json` | MCP connection definitions, no secrets | Yes |
| `config/mcp.local.json` | Machine-specific overrides | No, ignored |

**Open item carried into implementation:** every entry in `statutory_thresholds.yaml` must
be checked against the official text of the governing act before it is committed, and the
citation stored alongside the value. No threshold is written from memory.

## 6. Vault contract

```
demo-vault/
  procurement/
    watchlist.md                       # buyers to screen, frontmatter-driven
    findings/
      findings_<edrpou>_<YYYY-MM-DD>.md
```

Findings frontmatter: `finding_id`, `buyer_edrpou`, `tender_id`, `severity_score`,
`review_status` (`pending` / `approved` / `rejected`), `evidence_chain_ref`,
`created`, `run_id`. Watchlist frontmatter carries `last_reviewed_date` and
`reviewed_tender_ids`, which is how the next run skips what it already judged. Notes are
append-only; a re-screen adds a revision block rather than overwriting history.

## 7. Agent flow

```
read watchlist (Obsidian)
  -> read prior findings, build the already-reviewed set (Obsidian)
  -> for each buyer:
       compute_buyer_supplier_concentration        # once per buyer
       find_tenders                                 # constrained, minus already-reviewed
       for each tender:
         check_procedure_threshold_compliance
         screen_tender_red_flags                    # takes the streak flag as input
         -> branch on blocking_violations and risk_score
  -> if any finding exceeds the review threshold: interrupt, wait for human approval
  -> write findings notes and update watchlist state (Obsidian)
```

Branch points that are real, not cosmetic: a tender already in `reviewed_tender_ids` is
never re-screened; the concentration result changes the streak input and therefore the
score; a blocking violation changes which notes are written; and a high severity stops the
graph until a human approves.

## 8. Failure handling

| Scenario | Behaviour |
|---|---|
| Obsidian stopped or wrong API key | Connection error surfaced with the failing tool named; the run aborts before any write, and says so |
| Vault note missing | Reported as a missing resource, not as an empty watchlist |
| Invalid EDRPOU to a custom tool | `INVALID_INPUT`, clearly distinct from `result_count: 0` |
| Upstream `503` or `429` in live mode | Retry with exponential backoff, then `UPSTREAM_UNAVAILABLE` |
| Fixture absent in replay mode | `FIXTURE_MISSING`, never a silent fallback to live |
| Two tools succeed, one fails | Partial-result branch: partial findings written, the gap recorded in the note |
| Unexpected exception inside a rule | Caught per rule; that rule is reported as errored in the evidence chain and excluded from the score, rather than failing the whole screen |

## 9. Testing

Unit tests per rule against fixed tender fixtures including the awkward cases -
`reporting` with zero bids, a cancelled-then-reissued pair, a tender published under a
different threshold regime. Contract tests asserting that each tool's advertised schema
matches what it returns, and that the model-facing description in the code is identical to
the one in `docs/tool-contracts.md`. One end-to-end test over the committed dataset with
the network disabled.

## 10. Known limitations

1. Supplier "newness" is a dataset-horizon proxy, not a company registration date.
2. Shell-bidding detection matches free text, so it is suggestive and advisory only.
3. Spec-tailoring detection is lexical; attached documents are not parsed.
4. History is bounded by the sweep window, so trends and streaks are short-horizon.
5. The threshold table covers only the periods the dataset spans.
6. Rule weights are judgement, not calibrated against confirmed cases; the evidence chain
   exists so a reviewer can disagree with them precisely.
7. A high risk score is a prompt for human review, never an accusation.

## 11. Mapping to the assignment

| Requirement | Where satisfied |
|---|---|
| Existing server called and used in a flow | Obsidian read at the start, write at the end, prior findings change the run |
| Existing-server failure demonstrated | Section 8, rows 1-2 |
| Custom server, separate process, independently startable | Section 2 |
| Three substantive tools, at most one retrieval | 4.1, 4.2, 4.3 are non-retrieval; 4.4 is the single retrieval tool |
| Primary data-source tool | 4.4 over the prepared dataset, live path to the public API |
| Errors distinguishable from empty results | 4.6 |
| Fixtures and replay | Section 3 |
| No secrets | `.env.example` only; Obsidian key in the environment |
