# MCP Integration Lab - domain-specific data agent

Coursework submission for **MCP Integration: Student Assignment** (see
[`docs/assignment-original.md`](docs/assignment-original.md)).

An agent that solves a coherent multi-step domain problem using two MCP connections:

1. **an approved existing MCP server** - selection pending, one of Obsidian Local REST API
   MCP, Microsoft Playwright MCP, or OpenWeather MCP;
2. **a custom MCP server**, implemented in this repository, running as a separate process
   and exposing at least three substantive domain tools.

> **Status: scaffolding only.** The domain, the agent framework and the tool set are not
> yet fixed. Sections marked *pending* are placeholders; no implementation exists yet.
> See [`docs/domain-candidates.md`](docs/domain-candidates.md) for the shortlist.

## Repository layout

| Path | Contents |
|---|---|
| `src/agent/` | Agent process: orchestration, MCP client wiring, prompts |
| `src/mcp_server/` | Custom MCP server, started independently of the agent |
| `config/` | MCP connection configuration; `*.local.json` is git-ignored |
| `data/` | Prepared local dataset used as deterministic demo input |
| `fixtures/` | Recorded genuine API responses for offline replay |
| `tests/` | Tests (encouraged, not required by the assignment) |
| `scripts/` | Helper scripts: fixture recording, dataset preparation |
| `docs/` | Assignment copy, tool contracts, design rationale, defence checklist |

## Documentation

| Document | Purpose |
|---|---|
| [`docs/plan-review.md`](docs/plan-review.md) | Critical review of the supplied draft plan and what was kept from it |
| [`docs/domain-candidates.md`](docs/domain-candidates.md) | Shortlisted domains with rubric analysis and a recommendation |
| [`docs/tool-contracts.md`](docs/tool-contracts.md) | Part C - full contract per custom tool, plus the existing server's tool |
| [`docs/design-rationale.md`](docs/design-rationale.md) | Submission item 6 - why each piece exists, trade-offs, limitations |
| [`docs/defence-checklist.md`](docs/defence-checklist.md) | Submission item 7 - the 10-15 minute demo script |
| [`docs/assignment-original.md`](docs/assignment-original.md) | Unmodified copy of the assignment |
| [`docs/source-plan-original.txt`](docs/source-plan-original.txt) | Unmodified copy of the supplied draft plan |

## Prerequisites

*Pending - fixed once the stack is chosen.* Expected: a Python 3.11+ toolchain for the
custom MCP server, Node.js only if the selected existing server ships as an npm package,
and an OpenRouter key for model inference.

## Configuration

Copy [`.env.example`](.env.example) to `.env` and fill in the values. `.env` is
git-ignored. **No credential, token, or key is ever committed to this repository**, and
none is embedded in source.

```bash
cp .env.example .env
```

## Running

The custom server and the agent start **independently**, in separate terminals. Exact
commands are added with the implementation.

```bash
# terminal 1 - custom MCP server (separate process)
# pending

# terminal 2 - agent
# pending
```

## Offline / replay mode

When the custom server calls a public API, recorded genuine responses in `fixtures/` are
replayed through the **normal parsing and processing path**, selected by
`CUSTOM_MCP_OFFLINE=true`. No branch returns a prewritten answer. If the final design uses
a local dataset with no runtime network access, the prepared dataset in `data/` is the
deterministic demo input and API fixtures are not additionally required.

## Failure demonstration

One realistic failure of the existing MCP server is reproduced during the defence, and the
agent's handling of it is shown. The scenario is recorded in
[`docs/defence-checklist.md`](docs/defence-checklist.md).

## Licence

Coursework. No licence granted for reuse.
