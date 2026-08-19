# Tool-contract documentation (Part C)

Contract-first rule: **a tool is not implemented until its row set below is filled in.**
Every custom tool is documented with all eight elements required by the assignment. The
model-facing description recorded here must be byte-identical to the one the server
actually exposes over MCP; a mismatch is treated as a defect.

Domain: public procurement red-flag screening over the Prozorro open API.
Status: tool names fixed; schemas are filled in as each tool is implemented.

---

## Custom server - Tool 1: `screen_tender_red_flags`

| Contract element | Content |
|---|---|
| Name | `<exact MCP tool name>` |
| Purpose | What it does; when the model should reach for it, and when it should not |
| Model-facing description | Exact string exposed through MCP |
| Input schema | Field, type, required/optional, constraint, default - one row per field |
| Output schema | Field, type, meaning, on success |
| Error conditions | Each expected failure, its code, and how the caller distinguishes it from a successful empty result |
| Side effects | Files written, state mutated, network calls; "none" where applicable |
| Example | One representative request/response pair, copied from a real run |

## Custom server - Tool 2: `compute_buyer_supplier_concentration`

(same eight rows)

## Custom server - Tool 3: `check_procedure_threshold_compliance`

(same eight rows)

## Custom server - Tool 4: `find_tenders`

(same eight rows; the single retrieval tool)

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

---

## Existing server (Part A) - documented tool

| Contract element | Content |
|---|---|
| Server | Obsidian Local REST API MCP |
| Pinned version / commit | Instructor-announced version |
| Tool name | |
| Model-facing description | Exact string as discovered from the running server, not from the README |
| Arguments and constraints | |
| Returned content / structured result | |
| Likely error conditions | |
| Side effects | |
| Role in this project | Why this server, and what breaks in the workflow without it |
| Demonstrated failure | Which failure is triggered on stage, how it is triggered, and what the agent reports |
