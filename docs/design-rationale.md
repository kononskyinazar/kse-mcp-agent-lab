# Design rationale

Submission item 6. Every claim here is checkable against the code; where a claim would be
convenient but untrue, the untrue version is not made.

## 1. Domain and problem statement

Public procurement red-flag screening. The user is an analyst who follows a handful of
public buyers and has to decide, every cycle, which of their tenders deserve a human hour.

A single lookup cannot answer that. The verdict on one tender depends on the buyer's award
history, on which statutory rules were in force when the tender was published, and on what
the analyst already concluded in previous cycles. Those three inputs live in three
different places — a public API, a versioned rule table, and the analyst's own notes — and
the agent's job is to bring them together and stop before it accuses anyone.

## 2. Why Obsidian is the right existing server here

The vault is not a display surface bolted onto the end of a pipeline. It is where the
analyst's intent and the run-to-run memory live, and it is read **before** anything else
happens:

- `procurement/watchlist.md` frontmatter lists the buyers. Without it there is nothing to
  screen.
- Its prose says what the analyst cares about this cycle ("focus on medicines", "only the
  larger contracts here"). The planning step turns that into the `cpv_prefix` and
  `min_value` arguments passed to `find_tenders`. Change the sentence, and a different set
  of tenders is screened — that is the clearest demonstration that the existing server's
  result affects a later step.
- `reviewed_tender_ids`, the `tender_ids` frontmatter of every note under
  `procurement/findings/`, and the append-only run log together become the exclusion list.
  Measured on the live vault: 40 ids from the log, 3 from findings notes, 1 seeded by hand,
  and `find_tenders` for one buyer drops from 150 matches to 140 with the previously
  screened tenders gone from the head of the list.
- The findings note is the output, and the run-log entry appended at the end is what makes
  the next run different from this one.

Remove Obsidian and the agent has no instructions, no memory and nowhere to put a
conclusion. That is a stronger role than either alternative on the approved list: a
browser would have re-fetched data the custom server already holds, and weather has no
bearing on procurement.

The write side is narrow because the server made it narrow. The bridge exposes no
whole-file write, and its `obsidian_patch_content` tool fails against the installed plugin
for every target type — the bridge omits the `Markdown-Patch-Version` header the plugin now
requires (error 40084). Append is the only write that works. So the run-to-run state is an
append-only log at `procurement/findings/_run-log.md`, and a re-screen appends a revision
block rather than rewriting a note. This is a better arrangement than the one originally
designed: the agent structurally cannot damage what the analyst wrote.

## 3. Why each custom tool belongs at the MCP boundary

The test applied to each was: *would a model doing this in its own context get it wrong,
or get it right unverifiably?*

**`screen_tender_red_flags`** — twelve rules, each with an applicability condition, a
statutory lookup by publication date, and a weight. A model asked to "look for red flags"
would produce plausible prose with no reproducible score and no way to check which rules
it considered. Here the score is a linear sum the reader can recompute from the evidence
chain, and the rules that did *not* apply are listed with their reasons, so silence is
never mistaken for a clean bill of health.

**`compute_buyer_supplier_concentration`** — arithmetic over a buyer's whole award
history: HHI, top-1 and top-3 shares by value and by count, a monthly trend, the longest
run of consecutive awards. A model cannot hold 124 awards in context and divide them
correctly, and if it tried, nobody could check it.

**`check_procedure_threshold_compliance`** — the only question in the set with a citable
yes or no. It is separate from screening precisely because its answer is legal rather than
judgemental, and because the agent runs it on *every* tender, not only on ones that
tripped something else: a threshold breach must not be reachable only through another rule
firing first.

**`find_tenders`** — the single retrieval tool. It exists to keep the other three from
growing search parameters, and its filter list is a closed allow-list: no free text, no
query language, no SQL.

Two design decisions are worth naming because the obvious alternative was worse:

- **The win streak is resolved inside the screening tool, not passed to it.** An earlier
  draft had the model carry a streak flag from the concentration tool into the screening
  tool. That would let a hallucinated number contribute to a score whose entire value is
  that it can be recomputed by hand. The server now looks the streak up itself, for the
  supplier that actually won *this* tender — a buyer-level streak belongs to the buyer's
  dominant supplier, and attaching it to a tender won by someone else would point the
  evidence at the wrong company.
- **Unknown arguments are refused, not ignored.** MCP clients are not obliged to validate
  against the advertised schema, so the server validates. Silently dropping an argument
  would let a caller believe it had influenced a result it never touched.

## 4. How the tool set supports the workflow

The graph has four branch points, and each is an edge rather than a prompt instruction:

1. tenders in the vault's reviewed set never reach the screening tools;
2. the analyst's prose changes the arguments `find_tenders` receives;
3. `has_blocking` is tested first and independently of the score, so a statutory breach
   with no other signals still routes to human review — a blocking violation also lifts the
   score to a configured floor of 80 for the same reason;
4. above the review threshold the graph **interrupts** and waits for a person before
   anything is written.

Concentration runs once per buyer rather than once per flagged tender: it is a buyer-level
metric, and recomputing it per tender would waste calls and risk inconsistent numbers
inside a single run.

## 5. Data source

Prozorro / OpenProcurement public API — no authentication, no key, no account. 533 tender
documents from four civilian buyers, harvested once over a 30-day window and committed, so
the demonstration needs no network at all.

The harvest sweeps the change feed rather than searching, because the human-facing
`tenderID` cannot be resolved to a document UUID through any public endpoint and the search
service returns neither the UUID nor award data. It pages **ascending** from an offset: the
feed is ordered by `dateModified` and mutates continuously, so a descending offset stops
being a valid cursor as soon as anything changes mid-sweep, and rows would be silently
skipped or repeated across a run of 300+ requests.

The window was sized from a measured 24-hour pass (22,559 feed rows per day, 24 requests)
rather than assumed. The fetch is capped at 150 tenders per buyer, and the manifest records
that it was capped along with how many matched — a silent cap reads as "this is
everything".

## 6. Determinism and replay

Documents are stored **raw** and normalised on read. That is the whole point: a live
response, a replayed fixture and a stored document traverse the same parsing function at
demonstration time, so "where did this value come from?" is answered by pointing at
running code rather than at a harvest that happened weeks ago.

Offline mode serves tenders outside the dataset from `fixtures/`, keyed by request path and
parameters. A missing recording returns `FIXTURE_MISSING`. There is no fallback to the
network and no branch that returns a prepared answer.

## 7. Trade-offs

**Blocking versus advisory.** The severity split is carried over from the reviewed draft
plan, where it was proposed for spreadsheet validation. It is the one idea in that document
worth keeping, and it belongs here far more than there: the cost of miscalling an advisory
a violation is a false accusation in a note a human may forward. `blocking` is therefore
reserved for breaches with a citation, and single participation — the classic red flag —
is a top-weight advisory because it is lawful.

**Rules over retrieval.** A vector store over tender documents would have been faster to
build and would have produced fluent, unfalsifiable answers. Explicit rules cost more and
are frequently wrong in ways you can *see*, which is the property that matters when the
output concerns named companies.

**Static linear weights.** Not logarithmic and not procedure-specific, because no
calibration data exists that would justify a curve. Procedure sensitivity is handled by an
applicability matrix instead, which is inspectable; a tuned curve would not be.

**Committed dataset over live queries.** Costs freshness, buys a demonstration that runs
with the network unplugged and produces the same answer twice.

## 8. What real data changed

Three rules were wrong in ways only real data showed, and each was corrected by *narrowing*
the rule rather than by tuning a number:

- The open-tender minimum bid period does not govern `priceQuotation`, which runs under a
  separate order. The first version applied it anyway and flagged **all 68** price
  quotations in the dataset as statutory breaches. There is now no configured minimum for
  that procedure and the rule reports itself as not applicable, naming the reason.
- A direct contract is signed at its own stated value, so the award always equals the
  expectation. The "no discount" rule fired on **220** direct contracts, meaning nothing.
  It no longer applies to them.
- A four-month dataset cannot tell a genuinely new supplier from one that simply predates
  the window. The novelty rule now refuses to fire when the dataset does not reach back
  far enough to support the claim, and its weight is low because even when it does apply it
  fires for most awarded tenders at this horizon.

After the corrections: 280 of 533 tenders score zero, 19 land at 80 or above, one genuine
short-bid-window breach and 18 procedure/threshold mismatches remain.

## 9. What the live integration changed

Three things only appeared once the agent talked to a real Obsidian:

- `obsidian_patch_content` fails for every target type against the installed plugin, so the
  state moved out of watchlist frontmatter into an append-only run log.
- A same-day re-run appended a second YAML frontmatter block to the same note, which is
  malformed — only the first is read. Notes now take a revision block instead, and the
  finding id is derived from what was found rather than from the run id, so an identical
  re-run changes nothing while a run that finds something new is recorded.
- Third-party servers report failures as prose, not as a structured error object. The
  client classifies that text (`ENDPOINT_UNREACHABLE`, `UNAUTHORIZED`, `NOT_FOUND`) and
  keeps the original message, because "UNKNOWN: no message" is useless at a defence.

## 10. Known limitations

1. Supplier newness is a dataset-horizon proxy, not a registration date.
2. Shell-bidding detection matches free text and can never block.
3. Specification tailoring is detected lexically; attached documents are not parsed.
4. Streaks and trends are bounded by the sweep window; every response states it.
5. The threshold table covers only the periods the dataset spans.
6. Bid-period minimums are sourced from practice publications, marked `secondary`, and need
   confirmation against the official text.
7. Rule weights are judgement, not calibration.
8. The agent screens what the watchlist names; it does not discover new buyers.
9. A high score is a prompt for human review, never a conclusion of wrongdoing.

## 11. Assumptions

1. The martial-law procurement regime (CMU 1178) is still in force on the tenders in the
   dataset. If it were superseded, `config/statutory_thresholds.yaml` gains a regime with a
   later `effective_from` and nothing else changes — that is why the table is keyed by date.
2. `procuringEntity.identifier.id` is a stable EDRPOU for a buyer across the window.
3. An award with `status: active` is the awarded outcome; superseded awards are ignored.
4. A joint award is split evenly between its suppliers for concentration purposes, because
   the API does not publish the split.
5. CPV division 45 on a services tender indicates current-repair works, which carry their
   own threshold. This approximates the legal definition and is flagged where it is used.
