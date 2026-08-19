# Domain candidates (DECIDED: Candidate A)

> **Decision, 2026-08-19:** Candidate A - procurement red-flag screening, with Obsidian
> Local REST API MCP as the existing server and LangGraph as the agent framework.
> Design: [`superpowers/specs/2026-08-19-procurement-screening-design.md`](superpowers/specs/2026-08-19-procurement-screening-design.md).

Constraints every candidate must satisfy, taken from the assignment:

- exactly one approved existing server: Obsidian Local REST API MCP, Microsoft Playwright
  MCP, or OpenWeather MCP;
- custom server over a **public API without authentication** or a **local/downloadable
  dataset**;
- at least three substantive, distinct tools, of which **at most one** may be
  search/retrieval;
- a real branch point: a tool result must change what the agent does next;
- not the reserved "automatic research/experiment agent" topic, nor a renamed variant.

---

## Candidate A - Public procurement red-flag screening (recommended)

**Story.** An analyst keeps a watchlist of buyers and tenders. The agent reads the
watchlist, pulls the corresponding procurement records, applies red-flag rules, and writes
a structured findings note back. Screening results decide which tenders get escalated and
which get a follow-up check.

- **Existing server:** Obsidian MCP - reads the watchlist and the previous findings note,
  writes the new findings note. Genuine read *and* write role, and previous conclusions
  affect the current run. (Playwright MCP is a possible alternative, opening the public
  tender page, but it needs an instructor-approved page.)
- **Custom server data source:** Prozorro open procurement API (public, no authentication)
  with recorded fixtures for offline replay.
- **Candidate tools:**
  1. `screen_tender_red_flags` - applies weighted domain rules (single-bidder award, bid
     window shorter than the statutory minimum, cancel-and-rebid pattern, award price
     versus the expected value) and returns `blocking_violations` plus `advisories`,
     using the severity model carried over from the plan review.
  2. `compute_buyer_supplier_concentration` - concentration metric over a buyer's awards
     in a period, with controlled normalisation so buyers of different size compare fairly.
  3. `check_procedure_threshold_compliance` - validates that the chosen procurement
     procedure matches the value thresholds and category rules that apply to it, and
     explains each failed condition.
  4. *(retrieval, counts as at most one)* `find_tenders` with a constrained contract.
- **Why it scores:** three non-retrieval rule/computation tools, structured outputs, an
  obvious branch point, a large real public dataset, and strong domain specificity.
- **Main risk:** learning the API's response shape; mitigated by fixtures recorded early.

## Candidate B - Field-operation window planner (agronomy)

**Story.** Given a set of fields with crops and growth stages, the agent proposes when a
field operation (spraying, harvest) may legally and safely be carried out. The forecast
decides whether a proposed window survives, and a rejected window forces a replan.

- **Existing server:** OpenWeather MCP - the five-day forecast is the input that makes or
  breaks each candidate window. The strongest natural fit of the three servers, since the
  weather result *must* change the plan.
- **Custom server data source:** local prepared dataset of fields, crops, growth stages and
  product rules; no network needed at runtime, so fixtures are not additionally required.
- **Candidate tools:** validate an operation plan against agronomic and product-label rules
  (pre-harvest interval, wind and temperature limits, re-entry period); compute
  accumulated growing degree days to place a crop in its stage; propose feasible
  alternative windows under the constraints.
- **Why it scores:** the weather-to-decision loop is unambiguous and easy to demonstrate.
- **Main risk:** the dataset is authored rather than sourced, so the rules must be
  documented carefully to avoid looking arbitrary; also depends on an OpenWeather key
  being active on the day of the defence.

## Candidate C - Claim validation against public statistics

**Story.** The vault contains notes with quantitative claims. The agent extracts a claim,
checks it against an open statistics API, and marks it supported, contradicted, stale, or
unverifiable, then writes the verdicts back and flags claims that conflict with each other.

- **Existing server:** Obsidian MCP - reads the notes, writes verdicts.
- **Custom server data source:** an open indicator API without authentication (for example
  World Bank indicators), with fixtures.
- **Candidate tools:** test a stated claim against the indicator series and return
  structured evidence; compare entities with controlled normalisation (per capita,
  constant prices, common base year); detect stale or mutually inconsistent claims across
  the note set.
- **Why it scores:** maps cleanly onto the "public-data analysis agent" pattern.
- **Main risk:** the read-notes / evaluate / write-conclusion loop resembles the reserved
  research-agent topic in *shape*. It is a different problem, but it needs framing care.

---

## Recommendation

Candidate A. It has the richest set of genuine domain rules, the clearest separation
between the three custom tools, a real public dataset, and it reuses the one good idea in
the supplied plan (blocking versus advisory severity) where that idea actually belongs.
