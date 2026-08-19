# Design rationale

Submission item 6. Sections are written after the domain decision and kept in sync with
the implementation; a claim here that contradicts behaviour costs points under rubric
criterion 2.

## 1. Domain and problem statement

Public procurement red-flag screening. The user is an analyst watching a small set of
buyers; the decision supported is which tenders deserve human review and on what evidence.
A single lookup cannot answer it: the verdict depends on the buyer's award history, on the
statutory rules in force at publication, and on what previous runs already concluded.
Full design: [`superpowers/specs/2026-08-19-procurement-screening-design.md`](superpowers/specs/2026-08-19-procurement-screening-design.md).

## 2. Why the selected existing MCP server is relevant

Must answer: what does this server supply that the custom server cannot, and which later
step consumes its result. A connection whose result does not change a downstream decision
fails Part A regardless of how well it is configured.

## 3. Why each custom tool belongs at the MCP boundary

Per tool: the domain rule or computation it owns, why the model should not do it in the
prompt (determinism, data access, verifiability, cost), and what makes it distinct from
its siblings. Tools that differ only in a fixed parameter, filter, or output format are
one tool, not two.

## 4. How the tool set supports the agent workflow

The flow, step by step, showing where each tool's output feeds a later step. At least one
branch point must be genuine: a different tool result must produce a different subsequent
action, not merely different prose in the final answer.

## 5. Data source

Primary source, licence, access mode (public API without authentication, or local /
downloadable dataset), refresh assumptions, and the size of the prepared demo slice.

## 6. Determinism and replay

How the defence is made reproducible: recorded genuine API responses used as fixtures,
served through the normal parsing and processing path. No branch anywhere returns a
prewritten answer.

## 7. Trade-offs

Options considered and rejected, with the reason. Includes the severity model carried
over from `docs/plan-review.md` (blocking vs. advisory) and why the outputs are structured
that way.

## 8. Known limitations

Honest list: coverage gaps, data staleness, rules that are heuristics rather than ground
truth, scale limits, and anything the demo does not exercise.

## 9. Assumptions log

Numbered, each with the reason it was assumed and what would change if it is wrong.
Carried over from the "Ambiguities & Clarifications Log" idea in the reviewed plan.
