# Defence / demo checklist

10–15 minutes, operated and explained by the student alone. Every numbered item is a
required demonstration step from the assignment; the grouping follows the suggested timing.

Two terminals, one editor, Obsidian open on `demo-vault/`.

## Before starting

- [ ] `.env` filled: `OPENROUTER_API_KEY`, `OBSIDIAN_API_KEY`, `PROCUREMENT_MCP_PYTHON`
- [ ] Obsidian running, Local REST API plugin enabled, vault = `demo-vault/`
- [ ] `git status` clean; confirm `.env` is untracked
- [ ] `.venv/bin/python -m pytest -q` → 131 passed
- [ ] Vault is at its seed state: `procurement/findings/` holds only
      `findings_01999218_2026-08-18.md`, and no `_run-log.md`
- [ ] Terminal font enlarged; tool-call logging visible
- [ ] Know your three inputs in advance:
      **valid** `UA-2026-08-18-004904-a` (blocking: direct contract above the threshold),
      **second valid** `UA-2026-07-06-008728-a` (blocking: bid window below the minimum),
      **invalid** `not-an-edrpou`

---

## 1 — Independent startup and architecture (2 min)

**1. Start the custom server by hand, in its own terminal, before the agent.**

```bash
PYTHONPATH=src CUSTOM_MCP_OFFLINE=true .venv/bin/python -m procurement_mcp.server
```

It logs `starting procurement-screening 0.1.0` to stderr and waits on stdio. Say out loud:
this is a separate OS process; it shares no memory with the agent, and everything between
them crosses the protocol as JSON.

**2. Show the agent discovering both connections.**

```bash
.venv/bin/python -m agent.main --check
```

Point at the output: `obsidian: 12 tools`, `procurement: 4 tools`. Note the version pin —
`uvx --with "mcp<2" mcp-obsidian` — and why: the published bridge is written against SDK
1.x and dies on import under 2.x, while our own server runs on 2.x in its own process.

**3. Architecture in one sentence.** Agent process (LangGraph) → MCP client → two servers
→ Obsidian vault on one side, prepared Prozorro dataset on the other.

## 2 — The existing server inside an agent flow (2–3 min)

**4. Show the vault.** Open `demo-vault/procurement/watchlist.md`. Frontmatter: four
buyers, and `reviewed_tender_ids` containing `UA-2026-08-06-010871-a`. Prose: *"Focus on
medicines and medical supplies this cycle"* for the hospital, *"anything below 500 000 UAH
is routine"* for the heating company.

**5. Run the flow.**

```bash
.venv/bin/python -m agent.main --dry-run
```

**6. Trace the dependency, which is the point of this segment.** The sentence about
medicines becomes `cpv_prefix: "336"` in the `find_tenders` call. The already-reviewed
tender never reaches the screening tools, because it arrived in `exclude_tender_ids`. Both
values came out of the vault through `obsidian_get_file_contents`.

**7. Explain that tool's contract** from
[docs/tool-contracts.md](tool-contracts.md#existing-server-part-a-obsidian-local-rest-api):
name, exact model-facing description, one required `filepath` argument, raw note text
returned, 404 / 401 / connection-refused failures, read-only. Then name the write tool —
`obsidian_patch_content` — and the constraint that shaped the design: the bridge has **no
whole-file write**, so findings are appended and the watchlist is patched field by field,
which is why the agent can never overwrite the analyst's prose.

## 3 — Custom MCP end-to-end (3–4 min)

**8. Show the four tools are exposed**, from step 2's output, and that each declares both
an input and an output schema and refuses unknown arguments.

**9. Run the whole workflow** and let it reach the approval gate:

```bash
.venv/bin/python -m agent.main
```

**10. At the interrupt**, read out what it is asking: the flagged tenders, their scores and
their blocking rules. Explain that this is a LangGraph `interrupt` — the graph is suspended
and nothing has been written — and that it exists because the output can become an
allegation about a named company. Approve it.

**11. Show the written note** in Obsidian: frontmatter with `finding_id`, `buyer_edrpou`,
`severity_score`, `review_status`, `tender_ids`, `evidence_chain_ref`; body composed from
the structured evidence, citing КМУ № 1178 пункт 10 with the observed value against the
threshold. Then open `procurement/findings/_run-log.md` and show the entry this run
appended — that is what makes the next run different. Say why it is a log and not an edited
field: the bridge's patch tool fails against this plugin version (error 40084) and there is
no whole-file write, so append is the only write, which means the agent cannot rewrite your
notes even by mistake.

**12. Explain one contract and one design decision.** `screen_tender_red_flags`: the
evidence chain, and the fact that the score is a linear sum you can recompute by hand from
it. The decision: **the win streak is resolved server-side for this tender's winner and is
not an argument**, because a value the model carried between two tool calls could not be
verified — and the whole selling point of the score is that it can be.

**13. Name a side effect.** The custom server has none; it reads. The Obsidian writes are
the side effects, and they are ordered notes-first, state-last so a mid-write failure
leaves a re-screenable tender rather than a lost one.

## 4 — Failure and replay (2 min)

**14. The existing server fails.** Quit Obsidian, then:

```bash
.venv/bin/python -m agent.main
```

The bridge process still starts — it connects lazily — so the failure surfaces on the first
tool call, classified from the server's prose as `ENDPOINT_UNREACHABLE` with the underlying
`Connection refused` kept intact. It happens in `read_vault`, before any write, and the run
stops there. (Variant, if quitting is awkward: set `OBSIDIAN_API_KEY=wrong` and show the
401 path, classified `UNAUTHORIZED`.)

Worth saying out loud: third-party servers report failures as prose rather than a structured
error object, so the client classifies the text and keeps the original message. An error
reported as "UNKNOWN: no message" would be useless here.

**15. Invalid input to a custom tool**, showing that a failure and an empty result are
different things:

```bash
PYTHONPATH=src .venv/bin/python -c "
from procurement_mcp.config import Configuration; from procurement_mcp.store import DatasetStore
from procurement_mcp.tools import concentration
c=Configuration.load(); s=DatasetStore(c.settings.data_dir).load()
try: concentration.run(c, s, {'buyer_edrpou':'not-an-edrpou'})
except Exception as e: print('ERROR:', e.code, '-', e.message)
print('EMPTY :', concentration.run(c, s, {'buyer_edrpou':'99999999'})['result_count'])
"
```

`INVALID_INPUT` for the malformed code; `result_count: 0` for a well-formed code with no
tenders. One is an error result, the other an ordinary success.

**16. Replay mode.** Screen a tender that is *not* in the dataset, served from a recording
and parsed by the same code as a live call, then ask for one that was never recorded:

```bash
PYTHONPATH=src CUSTOM_MCP_OFFLINE=true .venv/bin/python -c "
from procurement_mcp.config import Configuration; from procurement_mcp.store import DatasetStore
from procurement_mcp.http import ReplayClient; from procurement_mcp.tools import screen
c=Configuration.load(); s=DatasetStore(c.settings.data_dir, client=ReplayClient(c.settings.fixture_dir)).load()
r=screen.run(c, s, {'tender_identifier':'b022cec3c4464496a50de586bb383d47'})
print('replayed:', r['tender']['tender_id'], '| score', r['risk_score'])
try: screen.run(c, s, {'tender_identifier':'0'*32})
except Exception as e: print('unrecorded:', e.code)
"
```

`FIXTURE_MISSING`, never a silent fall back to the network.

## 5 — Questions and one variation (3–4 min)

**17. A different valid input.** Edit the watchlist prose — change *"medicines"* to
*"construction"* — re-run, and show `cpv_prefix` becoming `45`, with a different set of
tenders screened. Nothing in the code changed.

Second variation worth having ready: run twice without editing anything. The second run
reads its own run-log entry, excludes those 40 tenders, and screens the next ones —
measured on the live vault, `find_tenders` for one buyer drops from 150 matches to 140.

**18. Trace one value end to end.** Take the blocking finding on
`UA-2026-07-06-008728-a`:

`noticePublicationDate` and `tenderPeriod.endDate` in the raw document under
`data/tenders/4d4701c80ffe4c54bfca0a263ebc3dba.json.gz`
→ parsed by `normalize_tender` (the same function that parses a live response)
→ `BidWindowBelowMinimum.check` computes the gap in days
→ compared against `minimum_tender_period_days` for `works`, looked up **by the tender's
publication date** in `config/statutory_thresholds.yaml`
→ emitted as `observed_value` / `threshold_value` plus the citation in the evidence chain
→ carried into the note frontmatter as `severity_score`.

Demonstrate the temporal lookup by pointing at the config: works published before
2024-04-19 get a 7-day minimum, after it 14 — the rule never applies today's number to an
older tender.

**19. A small configuration change, live.** In `config/rules.yaml`, change
`blocking_floor` from 80 to 95 and re-run the screen: the same tender, the same evidence,
a different score. Nothing recompiled.

## Questions to expect, and the honest answers

- *"Why is a single bidder not a violation?"* Because it is lawful in Ukraine. Calling it
  one would put a false accusation into a note a person may forward.
- *"Is 'new supplier' really a new company?"* No. It is the earliest appearance in this
  dataset. The API does not publish registration dates, the field name says so, and over
  this 30-day sweep the signal fires for about 58% of awarded tenders — which is why its
  weight is 6.
- *"Where do the thresholds come from?"* Value thresholds from the official text of CMU
  1178, marked `primary` in the config with the point number. Minimum bid periods from
  procurement-practice publications, marked `secondary`, and still to be confirmed against
  the official text.
- *"Did anything surprise you?"* Yes — the first version flagged all 68 price quotations as
  statutory breaches, because the open-tender minimum does not govern that procedure. It
  now reports itself as not applicable instead of guessing.
