# Procurement red-flag screening agent — MCP integration lab

Coursework for **MCP Integration: Student Assignment**
([assignment text](docs/assignment-original.md)).

An agent that screens Ukrainian public procurement records for red flags, using two MCP
connections:

1. **Obsidian Local REST API MCP** (the approved existing server) — reads the analyst's
   buyer watchlist and prior findings from a demonstration vault, and writes the new
   findings back. What the analyst wrote in the vault changes which tenders get screened,
   and what a previous run concluded stops this run re-judging the same tenders.
2. **A custom procurement MCP server** in this repository, running as its own process,
   exposing four tools over a prepared dataset of real Prozorro records:
   `find_tenders`, `compute_buyer_supplier_concentration`,
   `check_procedure_threshold_compliance` and `screen_tender_red_flags`.

Orchestration is a LangGraph graph, so the points where a tool result changes the next
step are edges in a graph rather than hints in a prompt.

## What it actually does

```
watchlist.md ──► plan (LLM reads the analyst's prose)
                  │
                  ├─► compute_buyer_supplier_concentration   (once per buyer)
                  ├─► find_tenders                           (minus already-reviewed)
                  └─► per tender: check_procedure_threshold_compliance
                                  screen_tender_red_flags
                                        │
                        blocking violation or score ≥ threshold?
                                 yes ──► human approval interrupt ──► write findings
                                 no  ─────────────────────────────► write findings
```

Screening separates two things that are usually blurred. A **blocking violation** is a
breach of a rule that can be cited — a bid window shorter than the statutory minimum, a
direct contract above the value at which an open tender is required. An **advisory** is a
pattern that is lawful but worth a look — a single bidder, an award at exactly the
expected value, a supplier on a winning streak. A single bidder is lawful in Ukraine, so
it is a heavily weighted advisory and never a violation. Every score comes with an
evidence chain that reconstructs it, rule by rule, with the weight each contributed.

## Prerequisites

- **Python 3.11+** and [`uv`](https://docs.astral.sh/uv/) (or `pip` and `venv`)
- **Obsidian desktop** with the
  [Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api) community
  plugin enabled, opened on the `demo-vault/` directory in this repository
- An **OpenRouter API key** for model inference
- Network access is **not** required to run the demonstration: the dataset is committed
  and the server defaults to offline replay

## Installation

```bash
uv venv --python 3.12
uv pip install -e ".[agent,dev]"
```

Then copy the environment template and fill it in:

```bash
cp .env.example .env
```

`.env` is git-ignored. Set at minimum:

| Variable | What to put there |
|---|---|
| `OPENROUTER_API_KEY` | your OpenRouter key |
| `OBSIDIAN_API_KEY` | Obsidian → Settings → Local REST API → API Key |
| `PROCUREMENT_MCP_PYTHON` | absolute path to `.venv/bin/python` |

## Obsidian setup

1. Open Obsidian and choose **Open folder as vault** → the `demo-vault/` directory here.
2. Settings → Community plugins → install and enable **Local REST API**.
3. Copy its API key into `.env`. The default endpoint is `127.0.0.1:27124`.

The vault contains only the demonstration watchlist and one prior findings note. Do not
point this at a personal vault.

> **Two things about the bridge, found by running it.** First, `mcp-obsidian` is written
> against MCP SDK 1.x; installed unpinned it pulls SDK 2.x and dies on import before listing
> a single tool, so `config/mcp.json` launches it as `uvx --with "mcp<2" mcp-obsidian`. The
> custom server runs on SDK 2.x in its own process — one practical benefit of process
> separation. Second, its `obsidian_patch_content` tool fails against the current plugin for
> every target type (error 40084: the bridge omits the `Markdown-Patch-Version` header), and
> there is no whole-file write. Append is the only usable write, so the run-to-run state is
> an append-only log and the agent structurally cannot rewrite the analyst's notes.

## Running

The two servers start independently. The custom server can be launched by hand:

```bash
PYTHONPATH=src CUSTOM_MCP_OFFLINE=true .venv/bin/python -m procurement_mcp.server
```

It speaks MCP over stdio and logs to stderr. To confirm both connections and see every
tool the agent discovers:

```bash
.venv/bin/python -m agent.main --check
```

To run the full flow:

```bash
.venv/bin/python -m agent.main
```

Useful flags: `--dry-run` stops at the approval gate and writes nothing, `--no-approval`
skips the gate, `--only procurement` connects to one server, `--watchlist PATH` points at
a different note.

## Repository layout

| Path | Contents |
|---|---|
| `src/procurement_mcp/` | The custom MCP server: client, harvest, normalisation, store, rules, tools |
| `src/agent/` | The agent: MCP client, vault access, LangGraph flow, CLI |
| `config/` | Watchlist, red-flag rules, statutory thresholds, MCP connections |
| `data/` | Prepared dataset: 533 raw tender documents plus the harvest manifest |
| `demo-vault/` | The committed Obsidian vault: watchlist, one seeded finding, and the run log the agent appends |
| `fixtures/` | Recorded API responses for offline replay |
| `scripts/` | Dataset harvest, fixture recording, tool-contract generation |
| `tests/` | 118 tests |
| `docs/` | Tool contracts, design rationale, defence checklist, reviews |

## The dataset

533 real tender documents from four ordinary civilian buyers — a municipal water utility,
a municipal hospital, a district heating company and a university — harvested from the
[Prozorro / OpenProcurement public API](https://public.api.openprocurement.org), which
needs no key and no account.

The harvest is committed, so nothing has to be downloaded to reproduce the demonstration.
To rebuild it:

```bash
.venv/bin/python scripts/harvest.py measure --hours 24    # size the window from observation
.venv/bin/python scripts/harvest.py sweep --days 30       # ~25 minutes at 1 request/second
```

The sweep walks the change feed **ascending** from an offset, keeps rows whose buyer is on
the allow-list in `config/watchlist.yaml`, then fetches those documents in full. It stores
them **raw**: normalisation happens on read, so a live response, a replayed fixture and a
stored document all go through the same parsing code at demonstration time.
`data/manifest.json` records the window, the request counts, the elapsed time, the
per-buyer yield and the fact that the fetch was capped at 150 tenders per buyer.

Why a sweep rather than a search: the human-facing `tenderID` cannot be resolved to a
document UUID through any public endpoint, and the search service returns neither the UUID
nor award data. The evidence for that is in [docs/upgrade-review.md](docs/upgrade-review.md).

## Offline replay

`CUSTOM_MCP_OFFLINE=true` (the default) makes the server serve any tender outside the
prepared dataset from `fixtures/` instead of the network. The recording is the response
the API returned, keyed by request path and parameters, and it is parsed by the same code
that parses a live call. If a recording is missing the server returns `FIXTURE_MISSING` —
it never falls back to the network, and no branch anywhere returns a prepared answer.

Record more fixtures with:

```bash
.venv/bin/python scripts/record_fixtures.py --recent 2
```

## Errors

Every tool distinguishes a failure from an empty result. A failure is an MCP error result
carrying `{"status": "error", "error": {"code", "message", "retryable", "details"}}`; an
empty result is an ordinary payload with `result_count: 0`. A malformed EDRPOU is
`INVALID_INPUT`; a well-formed EDRPOU with no tenders is a success with zero rows. Codes:
`INVALID_INPUT`, `NOT_FOUND`, `UPSTREAM_UNAVAILABLE`, `RATE_LIMITED`, `FIXTURE_MISSING`,
`DATA_INTEGRITY`.

The API returned `503` under rapid calls during design probing, so the client holds one
request per second and backs off exponentially with jitter, ending in
`UPSTREAM_UNAVAILABLE` rather than an exception. Both paths have tests with a fake clock.

## Tests

```bash
.venv/bin/python -m pytest -q
```

118 tests cover the backoff and replay paths, the harvest sweep, the concentration and
streak arithmetic, every red-flag rule, the tool contracts, the agent's branch logic, and
the documentation itself — `tests/test_documentation.py` fails if the tool contracts drift
from the code or if anything that looks like a key is committed.

## Documentation

| Document | Purpose |
|---|---|
| [Tool contracts](docs/tool-contracts.md) | Part C — every custom tool, generated from the running code, plus the Obsidian contract |
| [Design rationale](docs/design-rationale.md) | Why each piece exists, the trade-offs, the limitations |
| [Defence checklist](docs/defence-checklist.md) | The demonstration script |
| [Design spec](docs/superpowers/specs/2026-08-19-procurement-screening-design.md) | Architecture and data strategy |
| [Upgrade review](docs/upgrade-review.md) | API feasibility evidence and the reasoning behind each rule |
| [Plan review](docs/plan-review.md) | Critical review of the supplied draft plan |

## Limitations

Stated plainly, because a screening tool that oversells itself is worse than none:

- Supplier "newness" is a **dataset-horizon proxy**, not a registration date — the API does
  not publish registration dates. Over the shipped 30-day sweep it fires for roughly 58% of
  awarded tenders, so it carries little signal at this horizon and is weighted accordingly.
- Shell-bidding detection matches **free text**: `subcontractingDetails` is not a structured
  field, so a match is suggestive and can never block.
- Specification-tailoring detection is **lexical**. Attached specification documents are
  not downloaded or parsed.
- History is bounded by the sweep window, so streaks and trends are short-horizon. Every
  response states the window it used.
- The statutory table covers the periods the dataset spans. Value thresholds were read from
  the official text and are marked `primary`; the minimum bid periods come from procurement
  practice publications and are marked `secondary` pending confirmation against the
  official text.
- No minimum bid period is configured for `priceQuotation`, which runs under a separate
  order. The rule reports itself as not applicable rather than guessing — an earlier draft
  that guessed flagged all 68 price quotations in the dataset as statutory breaches.
- Rule weights are judgement, not calibration against confirmed cases. The evidence chain
  exists so a reviewer can disagree with a number rather than with a verdict.
- **A high score is a prompt for human review, never an accusation.**

## Licence

Coursework. No licence granted for reuse.
