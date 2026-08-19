# Critical review of the supplied "plan"

**Reviewed file:** `docs/source-plan-original.txt` (DeepSeek output, 2026-08-15)
**Reviewed against:** `docs/assignment-original.md` ("MCP Integration: Student Assignment")

## Verdict

The supplied file is **not a plan for this assignment**. It is a prompt-engineering
artifact: a rewritten prompt that instructs a model to act as a Senior Data Analyst and
produce a data-quality audit of an unspecified CSV/XLSX/JSON file, plus a rationale
section explaining the rewrite.

Concretely, it contains **zero** coverage of the assignment's graded surface:

| Assignment requirement | Present in the supplied plan? |
|---|---|
| Part A - configure and call an approved existing MCP server | No |
| Part A - explain an existing tool contract | No |
| Part A - demonstrate a realistic failure | No |
| Part B - custom MCP server in a **separate process** | No |
| Part B - three substantive, distinct tools | No |
| Part B - explicit input/output schemas per tool | Partially, by analogy only (see below) |
| Part B - errors distinguishable from empty results | No |
| Part C - tool-contract documentation | No |
| Part D - no secrets, rate limits, fixtures/replay mode | No |
| Agent framework, orchestration, multi-step flow | No |
| Defence/demo script | No |

Its final instruction, `**No Code:** Perform the analysis conceptually`, is in direct
opposition to an assignment whose deliverable is two running processes.

Adopting it as the project plan would score at or below the minimum-condition ceiling of
59/100, because none of the four minimum conditions (three qualifying tools, a primary
data-source tool, two callable MCP connections, both servers used in agent flows) would
be met.

## What is genuinely worth taking from it

The file is worthless as a project plan and useful as a **specification discipline**. Six
ideas transfer, and they map onto rubric criteria 2 and 3 (43 of 100 points combined):

1. **Contract-first thinking.** The plan insists that every field be named with type,
   required/optional status, constraint and default before anything is produced. That is
   exactly the Part C table and the "explicit input and output schemas for every tool"
   requirement. Carry it over verbatim as a working rule: no tool gets implemented before
   its schema and its model-facing description are written down.

2. **Critical vs. Non-critical severity labels.** The plan's definition - critical means
   *blocking, the record is disqualified*; non-critical means *advisory, it lowers a
   quality score* - is a genuinely good domain-rule model. Applied to a custom MCP
   validation tool it produces a structured, non-trivial output (`blocking_violations` vs.
   `advisories`) instead of a boolean, and it gives the agent something to branch on. This
   is the single strongest idea in the file.

3. **Field mapping and normalisation as a first-class step.** Source field to target
   field with an explicit transformation rule is a defensible reason for a *second*,
   distinct custom tool (normalise/adapt), separate from validation. It also satisfies
   "meaningful processing" rather than "returning stored text".

4. **Ambiguities and clarifications log.** Renaming this to "design trade-offs and known
   limitations" satisfies submission item 6 and rubric criterion 2 directly. Keep the
   discipline of writing assumptions down rather than silently choosing.

5. **Grounding constraint ("do not hallucinate; if information is missing, record it as
   an assumption").** At the MCP boundary this becomes the error contract in Part B: a
   tool must never invent a plausible answer, and the caller must be able to tell
   *failure* from *successful empty result*. Same principle, enforced in a schema instead
   of a prompt.

6. **Deliverables list up front.** Useful as a repository checklist; mirrored in
   `docs/defence-checklist.md`.

## What must be rejected

- **"No code."** Inverted: the graded artefact is running software.
- **Single-response Markdown as the deliverable.** Replaced by: two independently
  startable processes plus a repository.
- **The persona framing ("act as a Senior Data Analyst").** Irrelevant here; the model's
  behaviour is shaped by tool contracts and orchestration, not by a role sentence.
- **The generic ETL/data-warehouse domain as-is.** A tool set that only profiles a table
  (count nulls, list duplicates, list all rows) collides head-on with the assignment's
  explicit non-qualifying list: "listing all rows, files, or records without domain
  processing" and "thin wrappers". Data quality is acceptable only as *domain* rules -
  rules that encode something specific about the subject matter, not about spreadsheets
  in general.
- **Its placeholder-driven structure.** `[Project Goal]`, `[Data Usage]`,
  `[Success Criteria]` were never filled in, so the file commits to nothing. Every design
  decision in this repository must be concrete and named.

## How this review is applied in this repository

- Severity model (idea 2) is the intended backbone of the custom server's validation tool.
- Contract-first rule (idea 1) is enforced by `docs/tool-contracts.md`, which is filled in
  **before** a tool is implemented.
- Assumption logging (ideas 4, 5) lives in `docs/design-rationale.md` and in the error
  taxonomy of the custom server.
- Everything else in the supplied file is not carried forward.
